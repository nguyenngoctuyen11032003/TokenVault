"""Management half of the Manager API (devices, packages, installers, settings,
diagnostics). Functions take the running :class:`Manager`; :mod:`api` wraps them."""

from __future__ import annotations

import hashlib
import socket
import ssl
import time
from pathlib import Path
from typing import Any

from tokenvault.common.discovery_wire import DISCOVERY_PORT
from tokenvault.manager import firewall, netinfo
from tokenvault.manager.api_models import (
    NO_TEAM,
    ApiError,
    TemplateMissingError,
    clean_label,
    clean_team,
    iso_utc,
    require_text,
)
from tokenvault.manager.lifecycle import CertRestartError, Manager
from tokenvault.manager.packager import Packager, PackagerError
from tokenvault.manager.settings_store import SettingsError

REGENERATE_WARNING = (
    "The Manager certificate was replaced. Every installed adapter pins the OLD fingerprint "
    "and can no longer connect: create new employee installers and reinstall them on all PCs."
)
EXPIRY_WARN_S = 30 * 86400


def _unknown_label(windows_user: str, hostname: str, device_id: str) -> str:
    if windows_user or hostname:
        return f"Unknown account ({windows_user}@{hostname})"
    return f"Unknown account ({device_id})"


def _live_devices(m: Manager) -> list[dict[str, Any]]:
    dead_pk = {p.package_id for p in m.enrollment.list_packages() if p.revoked}
    enrolled = {d.device_id: d for d in m.enrollment.list_devices()}
    revoked = {i for i, e in enrolled.items() if e.revoked or e.package_id in dead_pk}
    online = m.online_ids()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set(revoked)
    for d in m.store.list_devices():
        e = enrolled.get(d.device_id)
        if d.device_id in revoked:
            continue
        seen.add(d.device_id)
        rows.append({
            "device_id": d.device_id,
            "email": d.email or None,
            "display_name": d.email or _unknown_label(d.windows_user, d.hostname, d.device_id),
            "windows_user": d.windows_user,
            "hostname": d.hostname,
            "team": d.team,
            "online": d.device_id in online,
            "last_seen": iso_utc(d.last_seen),
            "package_id": e.package_id if e else None,
        })
    for e in enrolled.values():  # enrolled but never sent anything yet
        if e.device_id in seen:
            continue
        rows.append({
            "device_id": e.device_id, "email": None,
            "display_name": _unknown_label("", "", e.device_id), "windows_user": "",
            "hostname": "", "team": e.team, "online": e.device_id in online,
            "last_seen": None, "package_id": e.package_id,
        })
    rows.sort(key=lambda r: (not r["online"], r["display_name"].lower(), r["device_id"]))
    return rows


def devices(m: Manager) -> list[dict[str, Any]]:
    return _live_devices(m)


def teams(m: Manager) -> list[dict[str, Any]]:
    acc: dict[str, list[int]] = {}
    for d in _live_devices(m):
        name = d["team"] or NO_TEAM
        c = acc.setdefault(name, [0, 0])
        c[0] += 1
        c[1] += int(d["online"])
    return [{"name": k, "devices": v[0], "online": v[1]}
            for k, v in sorted(acc.items(), key=lambda kv: (kv[0] == NO_TEAM, kv[0].lower()))]


def set_device_team(m: Manager, device_id: Any, team: Any) -> dict[str, Any]:
    dev = require_text(device_id, "device_id")
    t = clean_team(team, allow_empty=True)
    known = {d.device_id for d in m.store.list_devices()}
    enrolled = {d.device_id: d for d in m.enrollment.list_devices()}
    if dev not in known and dev not in enrolled:
        raise ApiError(f"unknown device {dev!r}", "not_found")
    dead_pk = {p.package_id for p in m.enrollment.list_packages() if p.revoked}
    if dev in enrolled and (enrolled[dev].revoked or enrolled[dev].package_id in dead_pk):
        raise ApiError("this device has been revoked", "revoked")
    m.store.set_device_team(dev, t)
    m.notify_change()
    return {"ok": True}


def revoke_device(m: Manager, device_id: Any) -> dict[str, Any]:
    dev = require_text(device_id, "device_id")
    if m.ingest is not None:
        m.ingest.revoke_device(dev)  # also closes the live session
    else:
        m.enrollment.revoke_device(dev)
    m.notify_change()
    return {"ok": True}


def packages(m: Manager) -> list[dict[str, Any]]:
    return [
        {"package_id": p.package_id, "team": p.team, "label": p.label,
         "created_at": iso_utc(p.created_at), "revoked": p.revoked, "devices": p.device_count}
        for p in sorted(m.enrollment.list_packages(), key=lambda p: -p.created_at)
    ]


def revoke_package(m: Manager, package_id: Any) -> dict[str, Any]:
    pid = require_text(package_id, "package_id")
    if pid not in {p.package_id for p in m.enrollment.list_packages()}:
        raise ApiError(f"unknown package {pid!r}", "not_found")
    if m.ingest is not None:
        m.ingest.revoke_package(pid)
    else:
        m.enrollment.revoke_package(pid)
    m.notify_change()
    return {"ok": True}


def template_ok(m: Manager) -> bool:
    t = m.cfg.template
    try:
        return t.is_file() and t.stat().st_size > 0
    except OSError:
        return False


def create_installer(m: Manager, team: Any, label: Any = None,
                     target: Any = "windows-exe") -> dict[str, Any]:
    """Copy the template + payload. Runs on pywebview's per-call worker thread, so the
    UI thread is never blocked; builds are serialised (one at a time)."""
    t = clean_team(team, allow_empty=False)
    lbl = clean_label(label)
    if target == "windows-portable":  # Task 25: signed-runtime ZIP, no unsigned exe
        from tokenvault.manager.api_portable import create_portable_installer

        with m.installer_lock:
            res = create_portable_installer(m, t, lbl)
        m.notify_change()
        return res
    if target not in (None, "windows-exe"):
        raise ApiError(f"Unknown installer target: {target!r}")
    if not template_ok(m):
        raise TemplateMissingError(m.cfg.template)
    with m.installer_lock:
        packager = Packager(m.cfg.template, m.enrollment, m.cert, m.paths.installers,
                            m.endpoint, m.manager_id)
        try:
            built = packager.build_for_team(t, lbl)
        except PackagerError as e:
            if not template_ok(m):  # removed/emptied between the check and the build
                raise TemplateMissingError(m.cfg.template) from e
            raise ApiError(str(e)) from e
    m.notify_change()
    return {"path": str(built.path), "file_name": built.file_name, "team": built.team,
            "bytes": built.bytes, "package_id": built.package_id}


def reveal_in_explorer(m: Manager, path: Any) -> dict[str, Any]:
    p = Path(require_text(path, "path", 1024))
    try:
        target = p.resolve(strict=True)
    except OSError:
        raise ApiError("file not found", "not_found") from None
    root = m.paths.installers.resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ApiError("only files in the installers folder can be shown", "invalid_args")
    if m.cfg.reveal is not None:
        m.cfg.reveal(target)
    return {"ok": True}


def settings(m: Manager) -> dict[str, Any]:
    s = m.settings.get()
    return {
        "transparent_window": s["transparent_window"],
        "start_with_windows": s["start_with_windows"],
        "port": m.bound_port(),
        "manager_id": m.manager_id,
        "hosts": list(s["hosts"]),  # RAW configured list; [] = automatic
        "hosts_auto": m.cfg.hosts_provider(),  # what "automatic" detects right now
        "installer_hosts": m.hosts(),  # what new installers carry
        "fingerprint": m.cert.fingerprint_sha256,
        "static_ip_hint": s["static_ip_hint"],
        "tz_offset_minutes": m.tz_offset_minutes,
        "version": m.cfg.version,
        "installers_dir": str(m.paths.installers),
    }


def save_settings(m: Manager, patch: Any) -> dict[str, Any]:
    before = m.settings.get()
    if isinstance(patch, dict) and isinstance(patch.get("hosts"), list) and not before["hosts"]:
        echoed = [str(h).strip().lower() for h in patch["hosts"]]
        if echoed == [h.lower() for h in m.cfg.hosts_provider()]:
            patch = {**patch, "hosts": []}  # UI echoed the automatic list: stay automatic
    try:
        after = m.settings.save_patch(patch)
    except SettingsError as e:
        raise ApiError(str(e), "invalid_settings") from e
    if after["start_with_windows"] != before["start_with_windows"] and m.cfg.autostart:
        try:
            m.cfg.autostart(after["start_with_windows"])
        except OSError as e:
            m.settings.save_patch({"start_with_windows": before["start_with_windows"]})
            raise ApiError(f"cannot change start with Windows: {e}") from e
    return settings(m)


def _warnings(m: Manager) -> list[str]:
    w: list[str] = []
    bound = m.bound_port()
    if m.cfg.ingest and m.ingest is None:
        w.append(f"The ingest server is not running: {m.ingest_error or 'unknown error'}.")
    elif m.ingest is not None and m.port_requested and bound != m.port_requested:
        w.append(
            f"Port {m.port_requested} was in use, so the Manager listens on port {bound}. "
            f"New installers carry port {bound}; adapters installed for port "
            f"{m.port_requested} find the Manager through LAN discovery or must be reinstalled."
        )
    fw = m.firewall
    if fw is not None and not fw.present:
        w.append(
            f"The Windows Firewall rule is missing (TCP {firewall.tcp_range(bound)}, UDP "
            f"{DISCOVERY_PORT}): other PCs cannot connect. Reinstall the Manager or run "
            "'TokenVault-Manager.exe firewall --ensure' as administrator."
        )
    if not template_ok(m):
        w.append(f"No adapter template found at {m.cfg.template}: installers cannot be "
                 "created. Reinstall the Manager.")
    exp = m.cert_expires_at()
    if exp is not None and exp - time.time() < EXPIRY_WARN_S:
        w.append("The Manager certificate expires on "
                 f"{iso_utc(exp)}: regenerate it and re-issue installers.")
    if m.cfg.discovery and (m.discovery is None or not m.discovery.bound_port):
        w.append(f"LAN discovery is off (UDP {DISCOVERY_PORT} unavailable).")
    if m.tz_error:
        w.append("Another program changed the database time zone; restart the Manager.")
    if m.cert_restart_error:
        w.append("The certificate was regenerated (new fingerprint "
                 f"{m.cert.fingerprint_sha256[:16]}...) but the ingest server did not restart: "
                 f"{m.cert_restart_error}. Restart the Manager.")
    if m.pricing_error:
        w.append(f"Price list unusable ({m.pricing_error}): costs show UNKNOWN.")
    return w


def _queue_counts(m: Manager) -> dict[str, int]:
    with m.store.read_snapshot() as snap:
        ev = snap.fetchall("SELECT COALESCE(SUM(events), 0) FROM rollup_month")[0][0]
        dv = snap.fetchall("SELECT COUNT(*) FROM devices")[0][0]
    return {"events": int(ev), "devices": int(dv)}


def diagnostics(m: Manager) -> dict[str, Any]:
    ing = m.ingest
    conn = sorted(m.connected())
    fw = m.firewall
    return {
        "port": m.bound_port(),
        "port_requested": m.port_requested,
        "fingerprint": m.cert.fingerprint_sha256,
        "connected": conn,
        "connected_count": len(conn),
        "last_event_at": iso_utc(ing.last_event_at) if ing else None,
        "rejected_count": ing.rejected_count if ing else 0,
        "firewall_rule_present": bool(fw and fw.present),
        "cert_expires_at": iso_utc(m.cert_expires_at()),
        "db_path": str(m.paths.db),
        "queue": _queue_counts(m),
        "warnings": _warnings(m),
        "ingest_running": ing is not None,
        "discovery_port": m.discovery.bound_port if m.discovery else 0,
        "tz_offset_minutes": m.tz_offset_minutes,
    }


def _probe_tls(port: int) -> tuple[bool, str]:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as raw, \
                ctx.wrap_socket(raw) as s:
            der = s.getpeercert(binary_form=True) or b""
    except OSError as e:
        return False, f"cannot connect to 127.0.0.1:{port}: {e}"
    return True, hashlib.sha256(der).hexdigest()


def connectivity_test(m: Manager) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    port = m.bound_port()
    if m.ingest is None:
        checks.append({"name": "bind", "ok": False,
                       "detail": f"ingest server not running: {m.ingest_error or 'disabled'}"})
        served = None
    else:
        ok, fp = _probe_tls(port)
        served = fp if ok else None
        checks.append({"name": "bind", "ok": ok,
                       "detail": f"listening on {m.cfg.ingest_host}:{port}" if ok else fp})
    fw = m.check_firewall()
    checks.append({"name": "firewall_rule", "ok": bool(fw and fw.present),
                   "detail": fw.detail if fw else "not checked"})
    ips = netinfo.lan_ipv4s()
    checks.append({"name": "lan_ips", "ok": bool(ips),
                   "detail": ", ".join(ips) if ips else "no LAN IPv4 address found"})
    fp_now = m.cert.fingerprint_sha256
    exp = m.cert_expires_at()
    cert_ok = exp is not None and exp > time.time() and served in (None, fp_now)
    detail = f"fingerprint {fp_now[:16]}..., expires {iso_utc(exp)}"
    if served not in (None, fp_now):
        detail += "; the port serves a DIFFERENT certificate"
    checks.append({"name": "certificate", "ok": cert_ok, "detail": detail})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def regenerate_certificate(m: Manager) -> dict[str, Any]:
    try:
        new = m.regenerate_certificate()
    except CertRestartError as e:
        m.notify_change()
        raise ApiError(str(e), "cert_error") from e
    except (OSError, RuntimeError, ValueError) as e:
        raise ApiError(f"certificate regeneration failed: {e}", "cert_error") from e
    m.notify_change()
    return {"fingerprint": new.fingerprint_sha256, "warning": REGENERATE_WARNING}


def live_payload(m: Manager) -> dict[str, Any]:
    devs = _live_devices(m)
    online = sum(1 for d in devs if d["online"])
    ing = m.ingest
    return {"online": online, "total": len(devs),
            "last_event_at": iso_utc(ing.last_event_at) if ing else None,
            "devices": {"online": online, "total": len(devs)}}
