"""The background agent: Watcher -> Collector -> Outbox -> Transport (+ tray).

Credentials (both DPAPI-protected files in the data dir):

* ``enroll.secret`` - from the installer payload, BOUND to its Manager
  (manager_id + fingerprint). Used in the Hello until the Manager answers with a
  Welcome carrying a device key, then deleted. A secret bound to another Manager
  is never sent (protects the config swap during a re-run of setup).
* ``device.key`` - used in every later Hello. An unreadable key falls back to
  the enroll secret. It is dropped only when the Manager rejected THAT key with
  an ``auth`` reason while a fresh enroll secret exists (setup re-run).
* Neither usable -> state "not_configured": the transport is stopped (no blind
  retries) until setup signals a reload.

Reload (setup re-run): ``singleton.ReloadListener`` waits on a named event; on
signal the agent stops cleanly and ``run`` returns, so the process exits and
setup starts the new agent with the new config.

Discovery port change: the Transport connects every host on
``transport.endpoint.port``. When the LAN resolver finds the Manager on a
different ingest port (``resolver.last_port``), ``_resolve`` replaces
``transport.endpoint`` with that port BEFORE returning the hosts (the Transport
reads the port when it connects, right after the resolver returns) and saves the
new port to config.json. The last good host is kept in state.json and tried
first after a restart.

Windows ("What is sent?", "About") are shown by a short-lived child process
(``<exe> show ...``): the agent itself never imports tkinter.
"""

from __future__ import annotations

import ctypes
import dataclasses
import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tokenvault.adapter import singleton, status_view
from tokenvault.adapter.collector import Collector
from tokenvault.adapter.config import (
    AdapterConfig,
    delete_file,
    load_config,
    read_enroll_secret,
    read_secret,
    save_config,
    write_secret,
)
from tokenvault.adapter.discovery import make_resolver
from tokenvault.adapter.identity import Identity, default_identity
from tokenvault.adapter.outbox import Outbox
from tokenvault.adapter.paths import DataFiles, self_argv
from tokenvault.adapter.strings import t
from tokenvault.adapter.transport import Endpoint, Transport
from tokenvault.adapter.tray import Throttle, Tray
from tokenvault.adapter.watcher import Watcher
from tokenvault.common.config_payload import valid_host
from tokenvault.common.dpapi import DpapiError
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
        child_args: list[str] | None = None,
        reload_event: str | None = None,
    ) -> None:
        cfg = load_config(files.config)
        if cfg is None:
            raise NotConfigured(t("run.not_configured", path=files.root))
        self.files = files
        self.config: AdapterConfig = cfg
        self.projects_dir = Path(projects_dir)
        self.identity = identity if identity is not None else default_identity()
        self.state = "offline"
        self._tray_enabled = tray
        self._resolver = resolver
        self._transport_options = dict(transport_options or {})
        self._backfill_days = backfill_days
        self._child_args = child_args if child_args is not None else [
            "--data-dir", str(files.root)]
        self._reload_event = reload_event or singleton.reload_event_name()
        self._key_lock = threading.Lock()
        self._life_lock = threading.Lock()
        self._stopped = threading.Event()
        self._outbox: Outbox | None = None
        self._transport: Transport | None = None
        self._watcher: Watcher | None = None
        self._tray: Tray | None = None
        self._reload: singleton.ReloadListener | None = None
        self._child: subprocess.Popen[bytes] | None = None
        self._console_cb: Any = None
        self._used_key = False  # did the last Hello carry the device key?
        self._halted = False  # "not_configured": no usable credential
        self._state_writer = Throttle(self._write_state)

    # ----------------------------------------------------------- credentials
    def hello_factory(self) -> Hello:
        did, team = self.identity.device_id, self.config.team
        with self._key_lock:
            key = read_secret(self.files.device_key)
            secret = None if key else read_enroll_secret(self.files.enroll_secret, self.config)
        self._used_key = bool(key)
        if key:
            return Hello(did, None, key, team, PROTOCOL_VERSION)
        if secret:
            return Hello(did, secret, None, team, PROTOCOL_VERSION)
        self._halt()
        raise RuntimeError("no usable device key or enrollment secret; re-run the installer")

    def _halt(self) -> None:
        """No credential: stop retrying until setup signals a reload."""
        log.error("no usable device key or enrollment secret; waiting for setup")
        self._halted = True
        self.state = "not_configured"
        self._state_writer()
        if self._tray is not None:
            self._tray.refresh()
        t_ = self._transport
        if t_ is not None:
            t_.stop()  # safe from the transport's own thread (it just cancels)

    def on_device_key(self, key: str) -> None:
        with self._key_lock:
            try:
                write_secret(self.files.device_key, key)
            except (DpapiError, OSError) as e:
                log.error("cannot store the device key (%s); will re-enroll next time", e)
                return
            delete_file(self.files.enroll_secret)
        log.info("enrolled: device key saved, enrollment secret deleted")

    def on_state(self, state: str) -> None:
        if self._halted:
            return  # "not_configured" is sticky until reload
        self.state = state
        t_ = self._transport
        reason = (t_.last_error or "") if t_ is not None else ""
        with self._key_lock:
            has_key = self.files.device_key.exists()
            has_secret = self.files.enroll_secret.exists()
            if state == "connected" and has_key and has_secret and self._used_key:
                delete_file(self.files.enroll_secret)  # key accepted: secret not needed
            elif (state == "rejected" and self._used_key and has_key and has_secret
                  and reason.startswith("auth")):
                log.warning("device key rejected (%s); will re-enroll with the installer's "
                            "secret", reason)
                delete_file(self.files.device_key)
        self._state_writer()
        if self._tray is not None:
            self._tray.refresh()

    def _write_state(self, state: str | None = None) -> None:
        t_ = self._transport
        extra: dict[str, Any] = {}
        if t_ is not None and t_.last_good_host:
            extra["last_good_host"] = t_.last_good_host  # kept across restarts
        status_view.write_state(self.files, state or self.state,
                                t_.last_error if t_ else None, **extra)

    # ------------------------------------------------------------- discovery
    def _resolve(self) -> list[str]:
        resolver = self._resolver
        if resolver is None:
            return []
        hosts = resolver()
        port = getattr(resolver, "last_port", None)
        t_ = self._transport
        if hosts and isinstance(port, int) and t_ is not None and port != t_.endpoint.port:
            log.info("Manager found on port %d (configured %d); switching", port, t_.endpoint.port)
            t_.endpoint = dataclasses.replace(t_.endpoint, port=port)
            self.config = dataclasses.replace(self.config, port=port)
            try:
                save_config(self.files.config, self.config)
            except (OSError, ValueError) as e:
                log.warning("could not save the new port: %s", e)
        return hosts

    def _saved_good_host(self) -> str | None:
        st = status_view.read_state(self.files) or {}
        host = st.get("last_good_host")
        return host if valid_host(host) else None

    # ------------------------------------------------------------- lifecycle
    def _on_new(self) -> None:
        t_ = self._transport
        if t_ is not None:
            t_.notify()
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
            self._transport.last_good_host = self._saved_good_host()
            self._watcher = Watcher(self.projects_dir, collector, on_new=self._on_new)
            if self._tray_enabled:
                self._tray = Tray(lambda: self.state, self.waiting, self.show_what_is_sent,
                                  self.open_log_folder, self.show_about)
                try:
                    self._tray.start()
                except Exception:
                    log.exception("tray icon unavailable; continuing without it")
                    self._tray = None
            self._reload = singleton.ReloadListener(self._reload_event, self.stop)
            self._reload.start()
            self._transport.start()
            self._watcher.start()
            self._watcher.backfill(max_age_days=self._backfill_days)
        log.info("adapter started (device %s, team %r)", self.identity.device_id, cfg.team)

    def stop(self) -> None:
        """Clean shutdown: reload listener, watcher, transport, tray, timers, outbox."""
        with self._life_lock:
            if self._stopped.is_set():
                return
            if self._reload is not None:
                self._reload.stop()
            if self._watcher is not None:
                self._watcher.stop()
            if self._transport is not None:
                self._transport.stop()
            if self._tray is not None:
                self._tray.stop()
            self._state_writer.close()
            if self._outbox is not None:
                self._write_state("stopped")
                self._outbox.close()
            self._stopped.set()
        log.info("adapter stopped")

    def run(self) -> int:
        """Start and block until :meth:`stop` (reload signal, Ctrl+C, console close)."""
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
        th = threading.Thread(target=self._stopped.wait, name="tv-adapter", daemon=True)
        th.start()
        return th

    @property
    def transport(self) -> Transport | None:
        return self._transport

    # ------------------------------------------------------------ tray verbs
    def _spawn_show(self, which: str) -> None:
        child = self._child
        if child is not None and child.poll() is None:
            return  # one window at a time
        argv = [*self_argv(windowed=True), "show", which, *self._child_args]
        try:
            self._child = subprocess.Popen(argv, close_fds=True)
        except OSError as e:
            log.warning("cannot open the %s window: %s", which, e)

    def show_what_is_sent(self) -> None:
        self._spawn_show("what-is-sent")

    def show_about(self) -> None:
        self._spawn_show("about")

    def open_log_folder(self) -> None:
        status_view.open_folder(self.files.root)

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
