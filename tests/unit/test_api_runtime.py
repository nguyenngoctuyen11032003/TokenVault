"""Manager Api: settings, window methods, diagnostics, live push."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any

import api_helpers as h
import pytest
from api_helpers import FW_MISSING, PREFIX, call_error, make_cfg

from tokenvault.manager import api_manage
from tokenvault.manager.api import Api, dispatch
from tokenvault.manager.lifecycle import Manager
from tokenvault.manager.live import LivePusher, live_script

manager = h.manager  # fixtures shared with the other test_api* module
demo = h.demo


def test_settings_roundtrip_and_autostart(tmp_path: Path) -> None:
    calls: list[bool] = []
    m = Manager(make_cfg(tmp_path, autostart=calls.append))
    m.start()
    try:
        api = Api(m)
        s = api.settings()
        assert set(s) >= {"transparent_window", "start_with_windows", "port", "manager_id",
                          "hosts", "fingerprint", "static_ip_hint", "tz_offset_minutes",
                          "version"}
        assert s["hosts"] == [] and s["port"] == m.bound_port()  # raw: [] = automatic
        assert s["hosts_auto"] == ["192.168.1.20", "pc-quanly"]
        assert s["installer_hosts"] == ["192.168.1.20", "pc-quanly"]
        assert s["tz_offset_minutes"] == 420 and len(s["fingerprint"]) == 64
        out = api.save_settings({"start_with_windows": True, "transparent_window": False})
        assert out["start_with_windows"] is True and calls == [True]
        assert call_error(api.save_settings, {"port": 1}).startswith("[invalid_settings]")
        assert call_error(api.save_settings, {"hosts": ["a b"]}).startswith("[invalid_settings]")
        assert api.save_settings({"hosts": ["10.1.1.1"]})["hosts"] == ["10.1.1.1"]
        assert api.save_settings({"hosts": []})["hosts"] == []
        # the UI echoing the automatic list unchanged must not freeze today's IPs
        out = api.save_settings({"hosts": ["192.168.1.20", "PC-QUANLY"]})
        assert out["hosts"] == [] and m.settings.get()["hosts"] == []
    finally:
        m.stop()


def test_reveal_only_installer_files(tmp_path: Path) -> None:
    shown: list[Path] = []
    m = Manager(make_cfg(tmp_path, reveal=shown.append))
    m.start()
    try:
        api = Api(m)
        res = api.create_installer("Dev", None)
        assert api.reveal_in_explorer(res["path"]) == {"ok": True}
        assert shown == [Path(res["path"]).resolve()]
        outside = tmp_path / "x.txt"
        outside.write_text("x")
        assert call_error(api.reveal_in_explorer, str(outside)).startswith("[invalid_args]")
        assert call_error(api.reveal_in_explorer, str(tmp_path / "missing.exe"))
    finally:
        m.stop()


def test_window_methods(manager: Manager) -> None:
    log: list[str] = []

    class Win:
        def minimize(self) -> None:
            log.append("min")

        def hide(self) -> None:
            log.append("hide")

        def ready(self) -> None:
            log.append("ready")

    api = Api(manager, Win())
    assert api.window_min() == api.window_close() == api.window_ready() == {"ok": True}
    assert log == ["min", "hide", "ready"]
    assert Api(manager).window_close() == {"ok": True}  # no window: no-op


def test_dispatch_only_contract_methods(manager: Manager) -> None:
    api = Api(manager)
    assert dispatch(api, "teams", []) == []
    for bad in ("_m", "_set_window", "__init__", "nope"):
        with pytest.raises(LookupError):
            dispatch(api, bad, [])


# --------------------------------------------------------------- diagnostics
def test_diagnostics_reports_port_in_use_fallback(tmp_path: Path) -> None:
    blocker = socket.socket()
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    taken = blocker.getsockname()[1]
    m = Manager(make_cfg(tmp_path, ingest_port=None, firewall_check=lambda p: FW_MISSING))
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"port": taken}))
    try:
        m.start()
        d = Api(m).diagnostics()
        assert d["port_requested"] == taken and d["port"] != taken and d["port"] > taken
        assert any(f"Port {taken} was in use" in w for w in d["warnings"])
        assert d["firewall_rule_present"] is False
        assert any("Firewall" in w for w in d["warnings"])
        assert Api(m).settings()["port"] == d["port"]
        bound = d["port"]
    finally:
        m.stop()
        blocker.close()
    # Restart: asks for the persisted (bound) port and gets it; no fallback warning.
    m2 = Manager(make_cfg(tmp_path, ingest_port=None))
    m2.start()
    try:
        d2 = Api(m2).diagnostics()
        assert d2["port_requested"] == bound and d2["port"] == bound
        assert not any("was in use" in w for w in d2["warnings"])
    finally:
        m2.stop()


def test_diagnostics_shape(demo: Manager) -> None:
    d = Api(demo).diagnostics()
    for key in ("port", "port_requested", "fingerprint", "connected", "connected_count",
                "last_event_at", "rejected_count", "firewall_rule_present", "cert_expires_at",
                "db_path", "queue", "warnings"):
        assert key in d
    assert d["queue"]["events"] > 1000 and d["queue"]["devices"] == 14
    assert d["cert_expires_at"].endswith("Z") and d["db_path"].endswith("manager.db")
    assert d["warnings"] == []


def test_connectivity_test_checks(demo: Manager) -> None:
    r = Api(demo).connectivity_test()
    names = [c["name"] for c in r["checks"]]
    assert names == ["bind", "firewall_rule", "lan_ips", "certificate"]
    bind = r["checks"][0]
    assert bind["ok"] is True, bind
    assert r["checks"][3]["ok"] is True


# ----------------------------------------------------------------- live push
class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_live_push_is_throttled() -> None:
    clock = FakeClock()
    sent: list[tuple[float, str]] = []
    counter = {"n": 0}

    def payload() -> dict[str, Any]:
        return {"online": counter["n"], "total": 14, "last_event_at": None,
                "devices": {"online": counter["n"], "total": 14}}

    p = LivePusher(lambda js: sent.append((clock.t, js)), payload, max_per_s=2, clock=clock)
    # 10 s of events every 10 ms, pumped every 10 ms -> at most 2 pushes per second.
    for _ in range(1000):
        counter["n"] += 1
        p.notify()
        p.pump()
        clock.t += 0.01
    assert 19 <= len(sent) <= 21
    gaps = [b[0] - a[0] for a, b in zip(sent, sent[1:], strict=False)]
    assert min(gaps) >= 0.5 - 1e-9
    # Trailing flush: the last event is delivered after the quiet period.
    last_n = counter["n"]
    for _ in range(100):
        p.pump()
        clock.t += 0.01
    assert json.loads(sent[-1][1][len(PREFIX):-1])["online"] == last_n
    n = len(sent)
    for _ in range(100):  # nothing pending -> nothing pushed
        p.pump()
        clock.t += 0.01
    assert len(sent) == n


def test_live_push_never_after_close() -> None:
    sent: list[str] = []
    p = LivePusher(sent.append, lambda: {"online": 1}, max_per_s=2, clock=FakeClock())
    p.close()
    p.notify()
    assert p.pump() is None and sent == []


def test_live_script_escapes_payload() -> None:
    evil = {"x": "</script><script>alert(1)</script> '\")//", "n": 1}
    js = live_script(evil)
    assert js.startswith(PREFIX) and js.endswith(")")
    literal = js[len(PREFIX):-1]
    assert json.loads(literal) == evil and literal.isascii() and " " not in literal


def test_live_pusher_thread_coalesces_and_flushes_last() -> None:
    sent: list[str] = []
    counter = {"n": 0}
    p = LivePusher(sent.append, lambda: {"n": counter["n"]}, max_per_s=10)
    p.start()
    try:
        t_end = time.monotonic() + 0.6
        while time.monotonic() < t_end:
            counter["n"] += 1
            p.notify()
            time.sleep(0.005)
        time.sleep(0.4)
        assert 3 <= len(sent) <= 9
        assert json.loads(sent[-1][len(PREFIX):-1])["n"] == counter["n"]
    finally:
        p.stop()


def test_live_pusher_survives_eval_errors() -> None:
    def bad_eval(js: str) -> None:
        raise RuntimeError("window gone")

    p = LivePusher(bad_eval, lambda: {}, clock=FakeClock())
    p.notify()
    p.pump()  # no exception
    assert p.pushed == 0


def test_live_payload_shape(demo: Manager) -> None:
    pl = api_manage.live_payload(demo)
    assert pl["total"] == 14 and pl["devices"] == {"online": pl["online"], "total": 14}
    assert "last_event_at" in pl


def test_ingest_event_triggers_change_listener(manager: Manager) -> None:
    hit = threading.Event()
    manager.on_change = hit.set
    manager._on_event("dev", 3)
    assert hit.is_set()


def test_window_geometry_methods_headless(manager: Manager) -> None:
    api = Api(manager)  # no real window: plausible virtual WindowState (dev server)
    st = api.window_state()
    assert set(st) >= {"maximized", "fullscreen", "x", "y", "width", "height", "min_width",
                       "min_height", "screen"}
    assert set(st["screen"]) == {"x", "y", "width", "height", "work_x", "work_y",
                                 "work_width", "work_height"}
    assert api.window_toggle_maximize()["maximized"] is True
    assert api.window_get_bounds()["height"] == st["screen"]["work_height"]
    assert api.window_restore()["maximized"] is False
    assert api.window_set_fullscreen(True)["fullscreen"] is True
    assert api.window_toggle_fullscreen()["fullscreen"] is False
    assert api.window_maximize()["maximized"] is True
    b = api.window_set_bounds(10, 20, 500.4, 300)
    assert (b["maximized"], b["width"], b["height"]) == (False, 1100, 720)
    assert call_error(api.window_set_bounds, "a", 0, 1, 1).startswith("[invalid_args]")
    assert call_error(api.window_set_fullscreen, 1).startswith("[invalid_args]")
    json.dumps(api.window_state(), allow_nan=False)



# ------------------------------------------------------------ fix round 1
def test_packager_errors_get_template_prefix_only_for_missing_template(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenvault.manager import packager

    def bad_hosts(self: Any, team: str, label: Any) -> Any:
        raise packager.PackagerError("Manager address 'template-host' is not valid.")

    monkeypatch.setattr(packager.Packager, "build_for_team", bad_hosts)
    msg = call_error(Api(manager).create_installer, "Dev", None)
    assert not msg.startswith("[template_missing]") and "template-host" in msg

    def vanished(self: Any, team: str, label: Any) -> Any:
        manager.cfg.template.unlink()  # removed between the check and the build
        raise packager.PackagerError("cannot read it")

    monkeypatch.setattr(packager.Packager, "build_for_team", vanished)
    assert call_error(Api(manager).create_installer, "Dev", None).startswith(
        "[template_missing]")


def test_regenerate_restart_failure_is_reported_precisely(
    manager: Manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_fp = manager.cert.fingerprint_sha256
    assert manager.ingest is not None

    def no_start(port: Any = None) -> int:
        raise RuntimeError("port busy")

    monkeypatch.setattr(manager.ingest, "start", no_start)
    msg = call_error(Api(manager).regenerate_certificate)
    assert msg.startswith("[cert_error] certificate regenerated but the server did not "
                          "restart: port busy")
    assert manager.cert.fingerprint_sha256 != old_fp
    warnings = Api(manager).diagnostics()["warnings"]
    assert any("regenerated" in w and "did not restart" in w for w in warnings)


def test_regenerate_waits_for_installer_builds(manager: Manager) -> None:
    done = threading.Event()
    with manager.installer_lock:
        t = threading.Thread(target=lambda: (Api(manager).regenerate_certificate(), done.set()))
        t.start()
        assert not done.wait(0.5)  # blocked while an installer build holds the lock
    assert done.wait(15)
    t.join()


def test_store_proxy_device_write_records_tz_conflict(manager: Manager) -> None:
    from tokenvault.manager.lifecycle import _StoreProxy
    from tokenvault.manager.store import TzOffsetChangedError

    class Boom:
        def upsert_device(self, *a: Any, **k: Any) -> None:
            raise TzOffsetChangedError("x")

    proxy = _StoreProxy(Boom(), manager)  # type: ignore[arg-type]
    with pytest.raises(TzOffsetChangedError):
        proxy.upsert_device("d", "Dev", None, 1.0)
    assert manager.tz_error is True


def test_settings_writes_are_best_effort(manager: Manager, monkeypatch: pytest.MonkeyPatch,
                                         caplog: pytest.LogCaptureFixture) -> None:
    from tokenvault.manager.settings_store import SettingsError

    def locked(**kw: Any) -> Any:
        raise SettingsError("cannot save settings: [WinError 32] in use")

    monkeypatch.setattr(manager.settings, "update_internal", locked)
    with caplog.at_level(logging.WARNING):
        assert manager.save_internal(port=47801) is False
    assert "could not save" in caplog.text
