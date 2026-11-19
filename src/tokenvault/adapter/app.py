"""The background agent: Watcher -> Collector -> Outbox -> Transport (+ tray).

Credentials (both DPAPI-protected files in the data dir):

* ``enroll.secret`` - from the installer payload; used in the Hello until the
  Manager answers with a Welcome carrying a device key. Then it is deleted.
* ``device.key`` - used in every later Hello. If the Manager rejects it
  (``auth:`` reject) while a fresh ``enroll.secret`` exists (setup was re-run
  with a new installer), the key is dropped so the next attempt re-enrolls.

Discovery port change: the Transport connects every host on
``transport.endpoint.port``. When the LAN resolver finds the Manager on a
different ingest port (``resolver.last_port``), ``_resolve`` replaces
``transport.endpoint`` with that port BEFORE returning the hosts (the Transport
reads the port when it connects, right after the resolver returns) and saves the
new port to config.json.
"""

from __future__ import annotations

import ctypes
import dataclasses
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tokenvault.adapter import status_view
from tokenvault.adapter.collector import Collector
from tokenvault.adapter.config import (
    AdapterConfig,
    delete_file,
    load_config,
    read_secret,
    save_config,
    write_secret,
)
from tokenvault.adapter.discovery import make_resolver
from tokenvault.adapter.identity import Identity, default_identity
from tokenvault.adapter.outbox import Outbox
from tokenvault.adapter.paths import DataFiles
from tokenvault.adapter.transport import Endpoint, Transport
from tokenvault.adapter.tray import Throttle, Tray
from tokenvault.adapter.watcher import Watcher
from tokenvault.common.protocol import PROTOCOL_VERSION, Hello

log = logging.getLogger("tokenvault.adapter.app")


class NotConfigured(RuntimeError):
    """config.json is missing or invalid: the installer's setup step has not run."""


class AdapterApp:
    def __init__(
        self,
        files: DataFiles,
        projects_dir: Path,
        *,
        identity: Identity | None = None,
        tray: bool = True,
        resolver: Callable[[], list[str]] | None = None,
        transport_options: dict[str, Any] | None = None,
        backfill_days: int = 90,
    ) -> None:
        cfg = load_config(files.config)
        if cfg is None:
            raise NotConfigured(f"no valid config in {files.root}; run the installer (setup)")
        self.files = files
        self.config: AdapterConfig = cfg
        self.projects_dir = Path(projects_dir)
        self.identity = identity if identity is not None else default_identity()
        self.state = "offline"
        self._tray_enabled = tray
        self._resolver = resolver
        self._transport_options = dict(transport_options or {})
        self._backfill_days = backfill_days
        self._key_lock = threading.Lock()
        self._life_lock = threading.Lock()
        self._stopped = threading.Event()
        self._outbox: Outbox | None = None
        self._transport: Transport | None = None
        self._watcher: Watcher | None = None
        self._tray: Tray | None = None
        self._window_open = threading.Lock()
        self._console_cb: Any = None
        self._state_writer = Throttle(self._write_state)

    # ----------------------------------------------------------- credentials
    def hello_factory(self) -> Hello:
        did, team = self.identity.device_id, self.config.team
        with self._key_lock:
            key = read_secret(self.files.device_key)
            if key:
                return Hello(did, None, key, team, PROTOCOL_VERSION)
            secret = read_secret(self.files.enroll_secret)
        if secret:
            return Hello(did, secret, None, team, PROTOCOL_VERSION)
        raise RuntimeError("not enrolled and no enrollment secret; re-run the installer")

    def on_device_key(self, key: str) -> None:
        with self._key_lock:
            write_secret(self.files.device_key, key)
            delete_file(self.files.enroll_secret)
        log.info("enrolled: device key saved, enrollment secret deleted")

    def on_state(self, state: str) -> None:
        self.state = state
        with self._key_lock:
            has_key = self.files.device_key.exists()
            has_secret = self.files.enroll_secret.exists()
            if state == "connected" and has_key and has_secret:
                delete_file(self.files.enroll_secret)  # key accepted: secret not needed
            elif state == "rejected" and has_key and has_secret:
                log.warning("device key rejected; will re-enroll with the installer's secret")
                delete_file(self.files.device_key)
        self._state_writer()
        if self._tray is not None:
            self._tray.refresh()

    def _write_state(self) -> None:
        t = self._transport
        status_view.write_state(self.files, self.state, t.last_error if t else None)

    # ------------------------------------------------------------- discovery
    def _resolve(self) -> list[str]:
        resolver = self._resolver
        if resolver is None:
            return []
        hosts = resolver()
        port = getattr(resolver, "last_port", None)
        t = self._transport
        if hosts and isinstance(port, int) and t is not None and port != t.endpoint.port:
            log.info("Manager found on port %d (configured %d); switching", port, t.endpoint.port)
            t.endpoint = dataclasses.replace(t.endpoint, port=port)
            self.config = dataclasses.replace(self.config, port=port)
            try:
                save_config(self.files.config, self.config)
            except (OSError, ValueError) as e:
                log.warning("could not save the new port: %s", e)
        return hosts

    # ------------------------------------------------------------- lifecycle
    def _on_new(self) -> None:
        t = self._transport
        if t is not None:
            t.notify()
        if self._tray is not None:
            self._tray.refresh()

    def waiting(self) -> int:
        ob = self._outbox
        return ob.stats().waiting if ob is not None else 0

    def start(self) -> None:
        with self._life_lock:
            if self._outbox is not None:
                return
            self.files.root.mkdir(parents=True, exist_ok=True)
            cfg = self.config
            if self._resolver is None:
                self._resolver = make_resolver(cfg.manager_id, cfg.fingerprint)
            ob = Outbox(self.files.outbox)
            self._outbox = ob
            collector = Collector(ob, self.identity, cfg.team)
            self._transport = Transport(
                ob,
                Endpoint(list(cfg.hosts), cfg.port, cfg.fingerprint),
                self.hello_factory,
                on_state=self.on_state,
                resolver=self._resolve,
                on_device_key=self.on_device_key,
                **self._transport_options,
            )
            self._watcher = Watcher(self.projects_dir, collector, on_new=self._on_new)
            if self._tray_enabled:
                self._tray = Tray(lambda: self.state, self.waiting, self.show_what_is_sent,
                                  self.open_log_folder, self.show_about)
                try:
                    self._tray.start()
                except Exception:
                    log.exception("tray icon unavailable; continuing without it")
                    self._tray = None
            self._transport.start()
            self._watcher.start()
            self._watcher.backfill(max_age_days=self._backfill_days)
        log.info("adapter started (device %s, team %r)", self.identity.device_id, cfg.team)

    def stop(self) -> None:
        """Clean shutdown: watcher, transport, tray, timers, outbox. Idempotent."""
        with self._life_lock:
            if self._stopped.is_set():
                return
            if self._watcher is not None:
                self._watcher.stop()
            if self._transport is not None:
                self._transport.stop()
            if self._tray is not None:
                self._tray.stop()
            self._state_writer.close()
            if self._outbox is not None:
                status_view.write_state(self.files, "stopped")
                self._outbox.close()
            self._stopped.set()
        log.info("adapter stopped")

    def run(self) -> int:
        """Start and block until :meth:`stop` (or Ctrl+C / console close)."""
        try:
            self.start()
        except BaseException:
            self.stop()
            raise
        if threading.current_thread() is threading.main_thread():
            self._install_console_handler()
        self._stopped.wait()
        return 0

    def run_in_thread(self) -> threading.Thread:
        """Start now (errors raise here); the returned thread ends after :meth:`stop`."""
        self.start()
        t = threading.Thread(target=self._stopped.wait, name="tv-adapter", daemon=True)
        t.start()
        return t

    @property
    def transport(self) -> Transport | None:
        return self._transport

    # ------------------------------------------------------------ tray verbs
    def _show_window(self, title: str, make_text: Callable[[], str]) -> None:
        if not self._window_open.acquire(blocking=False):
            return  # one window at a time

        def run() -> None:
            try:
                status_view.show_text_window(title, make_text())
            finally:
                self._window_open.release()

        threading.Thread(target=run, name="tv-window", daemon=True).start()

    def show_what_is_sent(self) -> None:
        ob = self._outbox
        if ob is not None:
            hosts = list(self.config.hosts)
            self._show_window("TokenVault - What is sent?",
                              lambda: status_view.what_is_sent_text(ob, hosts))

    def open_log_folder(self) -> None:
        status_view.open_folder(self.files.root)

    def show_about(self) -> None:
        self._show_window("About TokenVault Adapter", lambda: status_view.about_text(
            self.identity, self.files))

    # -------------------------------------------------------------- console
    def _install_console_handler(self) -> None:
        """Ctrl+C / Ctrl+Break / console close -> clean stop (Windows dev runs).

        ``Event.wait`` is not interruptible on Windows, so a console control
        handler (called on a system thread) performs the stop instead.
        """
        if sys.platform != "win32":
            return
        try:
            from ctypes import wintypes

            handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

            def handler(event: int) -> bool:
                self.stop()
                return True

            self._console_cb = handler_type(handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(self._console_cb, True)
        except Exception:
            log.debug("no console control handler", exc_info=True)
