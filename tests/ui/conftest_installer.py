"""Stub Manager management data + page fixture for the Task 17 pages.

Loaded by ``pytest_plugins = ["conftest_installer"]`` in the installer / settings / diagnostics
test modules. Shapes follow ``docs/superpowers/specs/manager-api-contract.md`` (management
methods). State lives in ``window.__tvMgr``; see ``fixtures/api_stub_installer.js``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

STUB_JS = Path(__file__).resolve().parent / "fixtures" / "api_stub_installer.js"

XSS = '<img src=x onerror="window.__xss=1">'
FINGERPRINT = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
OUT_DIR = "C:\\Users\\Owner\\Documents\\TokenVault\\Installers"
LONG_NAME = "Nguyễn Thị Ánh Tuyết - Phòng Kiểm thử Chất lượng Phần mềm Hà Nội"

DEVICES: list[dict[str, Any]] = [
    {"device_id": "dev-01", "email": "tuyen.nguyen@ics.vn", "display_name": "tuyen.nguyen@ics.vn",
     "windows_user": "TUYEN", "hostname": "DESKTOP-TUYEN", "team": "Dev", "online": True,
     "last_seen": "2026-11-19T10:02:00+07:00", "package_id": "pkg-1"},
    {"device_id": "dev-02", "email": "an.tran@ics.vn", "display_name": "an.tran@ics.vn",
     "windows_user": "an.tran", "hostname": "LAPTOP-AN", "team": "Dev", "online": False,
     "last_seen": "2026-11-18T17:45:00+07:00", "package_id": "pkg-1"},
    {"device_id": "qa-01", "email": "hoa.pham@ics.vn", "display_name": "hoa.pham@ics.vn",
     "windows_user": "hoa", "hostname": "QA-STATION-1", "team": "QA", "online": True,
     "last_seen": "2026-11-19T10:01:00+07:00", "package_id": "pkg-2"},
    {"device_id": "qa-02", "email": None, "display_name": LONG_NAME,
     "windows_user": "tuyet", "hostname": "PC Kế toán 01", "team": "QA", "online": False,
     "last_seen": None, "package_id": "pkg-2"},
    {"device_id": "x-01", "email": "x@evil.test", "display_name": XSS,
     "windows_user": "x", "hostname": "EVIL", "team": XSS, "online": False,
     "last_seen": "2026-11-17T08:00:00+07:00", "package_id": "pkg-3"},
]

PACKAGES: list[dict[str, Any]] = [
    {"package_id": "pkg-3", "team": XSS, "label": XSS, "created_at": "2026-11-12T09:00:00+07:00",
     "revoked": False, "devices": 1},
    {"package_id": "pkg-2", "team": "QA", "label": "Tầng 3", "created_at":
     "2026-11-10T14:20:00+07:00", "revoked": False, "devices": 2},
    {"package_id": "pkg-1", "team": "Dev", "label": None, "created_at":
     "2026-11-03T08:15:00+07:00", "revoked": True, "devices": 2},
]


def mgr_data(*, firewall: bool = True, port: int = 47800, empty: bool = False) -> dict[str, Any]:
    """Seeded fake Manager state (healthy unless ``firewall=False`` / ``port`` != 47800)."""
    warnings = []
    if not firewall:
        warnings.append("Firewall rule 'TokenVault Manager' (TCP 47800) not found.")
    if port != 47800:
        warnings.append("Port 47800 busy; listening on fallback port.")
    devices = [] if empty else copy.deepcopy(DEVICES)
    return {
        "outDir": OUT_DIR,
        "fail": {},
        "failOnce": {},
        "delay": {},
        "devices": devices,
        "packages": [] if empty else copy.deepcopy(PACKAGES),
        "settings": {
            "transparent_window": True, "start_with_windows": True, "port": 47800,
            "manager_id": "mgr-7f3a2c", "hosts": ["192.168.1.10", "MANAGER-PC"],
            # Task 12: hosts = raw configured list ([] = automatic), hosts_auto = detected,
            # installer_hosts = what new installers carry (computed by the stub when absent).
            "hosts_auto": ["192.168.1.10", "10.0.5.23"],
            "fingerprint": FINGERPRINT, "static_ip_hint": "", "tz_offset_minutes": 420,
            "version": "0.1.0",
        },
        "diagnostics": {
            "port": port, "port_requested": 47800, "fingerprint": FINGERPRINT,
            "connected": [] if empty else ["dev-01", "qa-01"],
            "connected_count": 0 if empty else 2,
            "last_event_at": None if empty else "2026-11-19T10:02:11+07:00",
            "rejected_count": 3, "firewall_rule_present": firewall,
            "cert_expires_at": "2036-11-17T00:00:00+07:00",
            "db_path": "C:\\Users\\Owner\\AppData\\Local\\TokenVault\\manager.db",
            "queue": {"events": 15234, "devices": 5}, "warnings": warnings,
        },
        "connectivity": {
            "ok": firewall,
            "checks": [
                {"name": "bind", "ok": True, "detail": f"0.0.0.0:{port}"},
                {"name": "firewall_rule", "ok": firewall,
                 "detail": "TokenVault Manager (TCP 47800)" if firewall else "rule not found"},
                {"name": "lan_ips", "ok": True, "detail": "192.168.1.10 (Ethernet), "
                 "10.0.5.23 (Wi-Fi)"},
                {"name": "certificate", "ok": True, "detail": "valid until 2036-11-17"},
            ],
        },
    }


def stub_init_script(data: dict[str, Any], refresh_ms: int | None = None) -> str:
    head = f"window.__tvMgr = {json.dumps(data)};\n"
    if refresh_ms is not None:
        head += f"window.TV_DIAG_REFRESH_MS = {int(refresh_ms)};\n"
    return head + STUB_JS.read_text(encoding="utf-8")


OpenMgr = Callable[..., Any]


@pytest.fixture
def open_mgr(open_shell: Any) -> OpenMgr:
    """Open a Task 17 route with stub data; waits until the page finished its first draw."""

    def _open(
        route: str = "#/installer",
        *,
        data: dict[str, Any] | None = None,
        width: int = 1440,
        height: int = 900,
        refresh_ms: int | None = None,
        extra_script: str = "",
        grant_clipboard: bool = False,
    ) -> Any:
        script = stub_init_script(data if data is not None else mgr_data(), refresh_ms)

        def setup(ctx: Any) -> None:
            if grant_clipboard:
                ctx.grant_permissions(["clipboard-read", "clipboard-write"])

        page = open_shell(
            route, width=width, height=height, init_script=f"{script};{extra_script}",
            setup=setup,
        )
        page.wait_for_selector("#tv-view[data-route-state]")
        page.wait_for_selector("#tv-view [data-tv-rev]:not([data-tv-rev='0'])")
        return page

    return _open


def calls(page: Any, name: str) -> list[list[Any]]:
    return [c["args"] for c in page.evaluate("window.__tvCalls") if c["name"] == name]


def rev(page: Any) -> int:
    return int(page.evaluate("document.querySelector('#tv-view [data-tv-rev]').dataset.tvRev"))


def wait_rev_after(page: Any, before: int) -> None:
    page.wait_for_function(
        "n => Number(document.querySelector('#tv-view [data-tv-rev]')?.dataset.tvRev) > n",
        arg=before,
    )


def console_problems(page: Any) -> list[str]:
    bad = [m for m in page.tv_console if m.startswith("error") or "missing key" in m]
    return [*page.tv_errors, *bad]
