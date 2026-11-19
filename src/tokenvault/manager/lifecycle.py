"""Manager components and their lifecycle (no GUI here).

Start order: data dir -> settings (manager_id once) -> certificate (never
overwritten: unusable files raise ``StartupError("[cert_error] ...")``) ->
pricing -> ``Store(tz_offset_minutes=<Windows local offset now>)`` ->
``Enrollment`` -> ``IngestServer`` on the LAST bound port from settings (the
bound port, which may be a fallback, is saved back: installers and discovery
carry it) -> ``DiscoveryResponder`` -> firewall check (log + warning only, never
elevates) -> presence ticker.

Presence ticker: every ``presence_interval`` s, devices with a live connection get
``last_seen = now`` (an idle adapter sends no batch, yet is online) and the live
listener is notified when the connected set changes.

Thread safety: Store / Enrollment / IngestServer are thread-safe; the mutable
references here (``cert``, ``ingest``) only change in :meth:`regenerate_certificate`
under ``self.lock``.
"""

from __future__ import annotations

import contextlib
import logging
import re
import secrets
import socket
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tokenvault.common.discovery_wire import DISCOVERY_PORT
from tokenvault.common.models import UsageEvent
from tokenvault.common.pricing import Pricing
from tokenvault.manager import firewall, netinfo, tls
from tokenvault.manager.aggregates import Aggregates
from tokenvault.manager.discovery import DiscoveryResponder
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.ingest_server import IngestServer
from tokenvault.manager.paths import ManagerPaths
from tokenvault.manager.settings_store import SettingsStore
from tokenvault.manager.store import Store, TzOffsetChangedError

log = logging.getLogger(__name__)
ONLINE_WINDOW_S = 30.0


class StartupError(RuntimeError):
    """The Manager cannot start (message is user-presentable)."""


def local_tz_offset_minutes() -> int:
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off is not None else 0


def new_manager_id() -> str:
    host = re.sub(r"[^A-Za-z0-9-]+", "-", socket.gethostname()).strip("-")[:40] or "manager"
    return f"{host}-{secrets.token_hex(3)}"


def app_version() -> str:
    try:
        from importlib.metadata import version

        return version("tokenvault")
    except Exception:  # noqa: BLE001 - frozen app without metadata
        return "0.1.0"


@dataclass
class ManagerConfig:
    data_dir: Path
    template: Path
    pricing: Path
    ingest: bool = True
    ingest_host: str = "0.0.0.0"
    ingest_port: int | None = None  # None = last bound port from settings
    discovery: bool = True
    discovery_port: int = DISCOVERY_PORT
    tz_offset_minutes: int | None = None  # None = Windows local offset at start
    firewall_check: Callable[[int], firewall.FirewallStatus] | None = None
    presence_interval: float = 10.0
    hosts_provider: Callable[[], list[str]] = netinfo.default_hosts
    reveal: Callable[[Path], None] | None = None
    open_file: Callable[[Path], None] | None = None  # open a report in the PDF viewer
    pick_save_path: Callable[[str, Path], Path | None] | None = None  # save dialog
    reports_dir: Path | None = None  # None = Documents/TokenVault Reports
    autostart: Callable[[bool], None] | None = None
    version: str = field(default_factory=app_version)


class _StoreProxy:
    """What the ingest server writes through: records tz-offset conflicts for Diagnostics."""

    def __init__(self, store: Store, owner: Manager) -> None:
        self._store = store
        self._owner = owner

    def upsert_events(self, device_id: str, events: Iterable[UsageEvent]) -> int:
        try:
            return self._store.upsert_events(device_id, events)
        except TzOffsetChangedError:
            self._owner.tz_error = True
            log.error("store time-zone offset changed by another process; batch refused")
            raise

    def upsert_device(self, *args: Any, **kw: Any) -> None:
        try:
            self._store.upsert_device(*args, **kw)
        except TzOffsetChangedError:
            self._owner.tz_error = True
            log.error("store time-zone offset changed by another process; device not updated")
            raise


class CertRestartError(RuntimeError):
    """The certificate WAS replaced but the ingest server did not come back."""


class Manager:
    def __init__(self, cfg: ManagerConfig) -> None:
        self.cfg = cfg
        self.paths = ManagerPaths(Path(cfg.data_dir))
        self.lock = threading.RLock()
        self.installer_lock = threading.Lock()
        self.settings: SettingsStore
        self.store: Store
        self.enrollment: Enrollment
        self.aggregates: Aggregates
        self.cert: tls.CertInfo
        self.ingest: IngestServer | None = None
        self.ingest_error: str | None = None
        self.discovery: DiscoveryResponder | None = None
        self.firewall: firewall.FirewallStatus | None = None
        self.port_requested = 0
        self.tz_offset_minutes = 0
        self.tz_error = False
        self.pricing_error: str | None = None
        self.cert_restart_error: str | None = None
        self.manager_id = ""
        self.on_change: Callable[[], None] | None = None
        self.presence_source: Callable[[], set[str]] | None = None
        self._last_connected: set[str] = set()
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None
        self._started = False

    # ------------------------------------------------------------ startup
    def start(self) -> None:
        try:
            self.paths.create()
        except OSError as e:
            raise StartupError(f"cannot create the data folder {self.paths.root}: {e}") from e
        self.settings = SettingsStore(self.paths.settings)
        s = self.settings.get()
        if not s["manager_id"]:
            mid = new_manager_id()
            self.save_internal(manager_id=mid)
            s = {**s, "manager_id": mid}
        self.manager_id = s["manager_id"]
        try:
            self.cert = tls.ensure_certificate(self.paths.certs)
        except (tls.CertificateError, OSError) as e:
            raise StartupError(f"[cert_error] {e}") from e
        pricing = self._load_pricing()
        self.tz_offset_minutes = (
            self.cfg.tz_offset_minutes
            if self.cfg.tz_offset_minutes is not None
            else local_tz_offset_minutes()
        )
        try:
            self.store = Store(self.paths.db, tz_offset_minutes=self.tz_offset_minutes)
            self.enrollment = Enrollment(self.paths.enrollment)
        except Exception as e:
            raise StartupError(f"cannot open the Manager database: {e}") from e
        self.aggregates = Aggregates(self.store, pricing, self.tz_offset_minutes)
        self._started = True
        self.port_requested = (
            self.cfg.ingest_port if self.cfg.ingest_port is not None else int(s["port"])
        )
        if self.cfg.ingest:
            self._start_ingest()
        if self.cfg.discovery:
            self.discovery = DiscoveryResponder(
                self.manager_id, self._discovery_endpoint, port=self.cfg.discovery_port
            )
            self.discovery.start()
        self.check_firewall()
        self._stop.clear()
        self._ticker = threading.Thread(target=self._tick, name="tv-presence", daemon=True)
        self._ticker.start()
        log.info("manager %s started (tz offset %+d min, port %s)", self.manager_id,
                 self.tz_offset_minutes, self.bound_port())

    def _load_pricing(self) -> Pricing:
        try:
            return Pricing.load(self.cfg.pricing)
        except (OSError, ValueError) as e:
            log.error("pricing file %s unusable (%s): every cost shows UNKNOWN",
                      self.cfg.pricing, e)
            self.pricing_error = str(e)
            return Pricing({})

    def _start_ingest(self) -> None:
        srv = IngestServer(
            _StoreProxy(self.store, self),
            self.enrollment,
            self.cert,
            host=self.cfg.ingest_host,
            port=self.port_requested,
            on_event=self._on_event,
        )
        try:
            bound = srv.start()
        except RuntimeError as e:
            self.ingest_error = str(e)
            log.error("ingest server failed to start: %s", e)
            return
        self.ingest = srv
        self.ingest_error = None
        if self.port_requested and bound != self.port_requested:
            log.warning("port %d in use: listening on %d", self.port_requested, bound)
        self.save_internal(port=bound)

    def save_internal(self, **values: Any) -> bool:
        """Best effort: antivirus/backup tools may hold settings.json for a moment."""
        for attempt in range(3):
            try:
                self.settings.update_internal(**values)
                return True
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    log.warning("could not save %s to settings.json: %s (continuing)",
                                sorted(values), e)
                    return False
                time.sleep(0.2)
        return False

    def _discovery_endpoint(self) -> tuple[int, str]:
        return self.bound_port(), self.cert.fingerprint_sha256

    def stop(self) -> None:
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join(2)
            self._ticker = None
        if self.discovery is not None:
            self.discovery.stop()
            self.discovery = None
        if self.ingest is not None:
            self.ingest.stop()
            self.ingest = None
        if self._started:
            self._started = False
            with contextlib.suppress(Exception):
                self.enrollment.close()
            with contextlib.suppress(Exception):
                self.store.close()
        log.info("manager stopped")

    # ------------------------------------------------------------- status
    def bound_port(self) -> int:
        ing = self.ingest
        if ing is not None and ing.port:
            return int(ing.port)
        return int(self.settings.get()["port"])

    def connected(self) -> set[str]:
        ing = self.ingest
        return ing.connected_devices() if ing is not None else set()

    def online_ids(self) -> set[str]:
        """Live connections (+ the demo presence source); without an ingest server
        (dev server) devices seen in the last 30 s."""
        if self.presence_source is not None:
            return self.connected() | self.presence_source()
        if self.ingest is not None:
            return self.connected()
        now = time.time()
        return {d.device_id for d in self.store.list_devices()
                if d.last_seen >= now - ONLINE_WINDOW_S}

    def hosts(self) -> list[str]:
        override = self.settings.get()["hosts"]
        return list(override) if override else self.cfg.hosts_provider()

    def endpoint(self) -> tuple[list[str], int]:
        return self.hosts(), self.bound_port()

    def check_firewall(self) -> firewall.FirewallStatus | None:
        if self.cfg.firewall_check is None:
            return None
        try:
            self.firewall = self.cfg.firewall_check(self.bound_port())
        except Exception as e:  # noqa: BLE001 - diagnostics only
            self.firewall = firewall.FirewallStatus(False, False, False, f"check failed: {e}")
        if not self.firewall.present:
            log.warning("firewall rule missing: %s", self.firewall.detail)
        return self.firewall

    def cert_expires_at(self) -> float | None:
        try:
            from cryptography import x509

            c = x509.load_pem_x509_certificate(self.cert.cert_path.read_bytes())
            return c.not_valid_after_utc.timestamp()
        except Exception:  # noqa: BLE001
            return None

    def notify_change(self) -> None:
        cb = self.on_change
        if cb is not None:
            with contextlib.suppress(Exception):
                cb()

    # ------------------------------------------------------------- events
    def _on_event(self, device_id: str, n: int) -> None:
        self.notify_change()

    def _tick(self) -> None:
        while not self._stop.wait(self.cfg.presence_interval):
            self.touch_presence()

    def touch_presence(self) -> None:
        src = self.presence_source
        ids = src() if src is not None else self.connected()
        now = time.time()
        for dev in ids:
            try:
                self.store.upsert_device(dev, None, None, now)
            except TzOffsetChangedError:
                self.tz_error = True
            except Exception as e:  # noqa: BLE001 - presence is best effort
                log.warning("presence update failed for %s: %s", dev, e)
        if ids != self._last_connected:
            self._last_connected = set(ids)
            self.notify_change()

    # -------------------------------------------------------- certificate
    def regenerate_certificate(self) -> tls.CertInfo:
        """New certificate; the ingest server restarts on the SAME port with it."""
        # installer_lock: no installer may embed the old fingerprint mid-regeneration
        with self.installer_lock, self.lock:
            new = tls.regenerate_certificate(self.paths.certs)
            self.cert = new
            old = self.ingest
            port = self.bound_port()
            if old is not None:
                old.stop()
                old.cert = new
                try:
                    bound = old.start(port)
                except RuntimeError as e:
                    self.ingest = None
                    self.ingest_error = f"after the certificate was regenerated: {e}"
                    self.cert_restart_error = str(e)
                    raise CertRestartError(
                        "certificate regenerated but the server did not restart: "
                        f"{e}") from e
                if bound != port:
                    log.warning("after certificate change the port moved %d -> %d", port, bound)
                    self.save_internal(port=bound)
            elif self.cfg.ingest:
                self._start_ingest()
            self.cert_restart_error = None  # a successful regenerate clears an old failure
            log.warning("certificate regenerated: new fingerprint %s",
                        tls.short_fp(new.fingerprint_sha256))
            return new
