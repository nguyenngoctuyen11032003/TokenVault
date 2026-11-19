"""Manager Api (contract: docs/superpowers/specs/manager-api-contract.md) and live push."""

from __future__ import annotations

import inspect
import json
import logging
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tokenvault.common.config_payload import read_payload
from tokenvault.common.models import UsageEvent
from tokenvault.manager import api_manage
from tokenvault.manager.api import EXPOSED, Api, dispatch
from tokenvault.manager.demo_data import UNPRICED_MODEL, seed_now
from tokenvault.manager.firewall import FirewallStatus
from tokenvault.manager.lifecycle import Manager, ManagerConfig
from tokenvault.manager.live import LivePusher, live_script
from tokenvault.manager.paths import pricing_path

PREFIX = "window.tv && window.tv.onLive && window.tv.onLive("
FW_OK = FirewallStatus(True, True, True, "ok")
FW_MISSING = FirewallStatus(False, False, True, "TCP rule missing")


def make_cfg(root: Path, **kw: Any) -> ManagerConfig:
    tpl = root / "adapter-setup-template.exe"
    if not tpl.exists():
        tpl.write_bytes(b"MZ" + b"\0" * 4096)
    base: dict[str, Any] = dict(
        data_dir=root / "data", template=tpl, pricing=pricing_path(), ingest=True,
        ingest_host="127.0.0.1", ingest_port=0, discovery=False, tz_offset_minutes=420,
        firewall_check=lambda port: FW_OK, presence_interval=0.2,
        hosts_provider=lambda: ["192.168.1.20", "pc-quanly"], reveal=None, autostart=None,
    )
    base.update(kw)
    return ManagerConfig(**base)


@pytest.fixture
def manager(tmp_path: Path) -> Iterator[Manager]:
    m = Manager(make_cfg(tmp_path))
    m.start()
    yield m
    m.stop()


@pytest.fixture
def demo(manager: Manager) -> Manager:
    seed_now(manager.store, manager.enrollment, manager.tz_offset_minutes)
    return manager


def call_error(fn: Any, *args: Any) -> str:
    with pytest.raises(Exception) as ei:  # noqa: PT011 - pywebview forwards plain Exceptions
        fn(*args)
    assert type(ei.value) is Exception
    return str(ei.value)


# ------------------------------------------------------------------- contract
def test_exposed_methods_match_contract(manager: Manager) -> None:
    api = Api(manager)
    public = {n for n, v in inspect.getmembers(api) if not n.startswith("_")}
    assert public == set(EXPOSED)


def test_api_methods_return_json_serialisable(demo: Manager) -> None:
    api = Api(demo)
    today = datetime.now().date().isoformat()
    flt = {"team": "Dev", "email": None, "project": None, "model": None,
           "start": "2026-01-01", "end": today}
    sessions = api.sessions(None)
    assert sessions, "demo data has sessions"
    sid = sessions[0]["session_id"]
    emps = api.employees(flt)
    results = {
        "overview": api.overview(flt), "overview_all": api.overview(),
        "employees": emps, "employee": api.employee(emps[0]["key"], "30d"),
        "projects": api.projects(flt), "sessions": sessions, "session": api.session(sid),
        "session_turns": api.session_turns(sid), "skills": api.skills("30d"),
        "orchestration": api.orchestration("all"), "teams": api.teams(),
        "devices": api.devices(), "packages": api.packages(), "settings": api.settings(),
        "diagnostics": api.diagnostics(), "connectivity_test": api.connectivity_test(),
    }
    for name, value in results.items():
        text = json.dumps(value, allow_nan=False)
        assert json.loads(text) == value, name
    assert results["skills"]["total_invocations"] > 0
    assert results["session"]["session_id"] == sid
    assert {t["name"] for t in results["teams"]} == {"Dev", "QA", "BA-PM"}
    assert sum(t["devices"] for t in results["teams"]) == 14
    names = [d["display_name"] for d in results["devices"]]
    assert "Unknown account (ktv-lab@LAB-PC07)" in names
    assert all(d["package_id"] for d in results["devices"])
    pk = {p["team"]: p for p in results["packages"] if not p["revoked"]}
    assert pk["QA"]["devices"] == 4 and any(p["revoked"] for p in results["packages"])
    assert results["devices"][0]["last_seen"].endswith("Z")


def test_unknown_cost_serialised_as_null_with_flag(manager: Manager) -> None:
    def ev(n: int, **kw: Any) -> UsageEvent:
        e = UsageEvent(
            uuid=f"u{n}", session_id="s1", message_id=f"m{n}",
            timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            model="claude-sonnet-4-6", reasoning_effort=None, is_sidechain=False,
            input_tokens=1000, output_tokens=0, cache_read_tokens=0, cache_create_5m_tokens=0,
            cache_create_1h_tokens=0, project="p", session_title=None, tool_names=(),
            claude_account_email="a@x.vn", account_uuid=None, windows_user="a", hostname="H",
            team="Dev", device_id="d1")
        return replace(e, **kw)

    manager.store.upsert_events("d1", [ev(1), ev(2, model=UNPRICED_MODEL, session_id="s2")])
    api = Api(manager)
    ov = api.overview(None)
    assert ov["totals"]["cost_partial"] is True and ov["totals"]["cost"] == pytest.approx(0.003)
    assert ov["unknown_cost_models"] == [UNPRICED_MODEL]
    by_model = {r["model"]: r for r in ov["by_model"]}
    assert by_model[UNPRICED_MODEL]["cost"] is None
    s2 = api.session("s2")
    assert s2["cost"] is None and s2["cost_partial"] is True
    text = json.dumps(s2, allow_nan=False)
    assert '"cost": null' in text and '"cost_partial": true' in text
    turns = api.session_turns("s2")
    assert turns[0]["cost"] is None
    assert isinstance(api.session("s1")["cost"], float)


# -------------------------------------------------------------------- errors
def test_filter_validation(manager: Manager) -> None:
    api = Api(manager)
    assert call_error(api.overview, {"range": "today"}).startswith("[invalid_filter]")
    assert call_error(api.overview, {"start": "2026-13-01"}).startswith("[invalid_filter]")
    assert call_error(api.overview, {"start": "2026-11-19T00:00"}).startswith("[invalid_filter]")
    assert call_error(api.overview, {"team": 5}).startswith("[invalid_filter]")
    assert call_error(api.overview, ["x"]).startswith("[invalid_filter]")
    assert call_error(api.overview, {"start": "2026-11-20", "end": "2026-11-19"})
    assert call_error(api.skills, "1y").startswith("[invalid_range]")
    assert call_error(api.employee, "", "7d").startswith("[invalid_args]")
    assert api.overview({"project": "", "start": "2026-11-19", "end": "2026-11-19"})


def test_bad_arg_count_and_internal_errors_are_clean(
    manager: Manager, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    api = Api(manager)
    assert call_error(api.session_turns).startswith("[invalid_args]")
    assert call_error(api.teams, 1, 2).startswith("[invalid_args]")

    def boom(*a: Any) -> Any:
        raise KeyError("secret-detail")

    monkeypatch.setattr(manager.aggregates, "overview", boom)
    with caplog.at_level(logging.ERROR):
        msg = call_error(api.overview, None)
    assert "secret-detail" not in msg and "internal error" in msg
    assert "Traceback" in caplog.text or any(r.exc_info for r in caplog.records)


def test_create_installer_returns_existing_exe(manager: Manager) -> None:
    api = Api(manager)
    api.save_settings({"hosts": ["10.0.0.5", "pc-quanly"]})
    res = api.create_installer("Kế toán", "Đợt 2")
    p = Path(res["path"])
    assert p.is_file() and p.stat().st_size == res["bytes"]
    assert res["file_name"] == p.name and p.parent == manager.paths.installers
    assert res["team"] == "Kế toán"
    cfg = read_payload(p)
    assert cfg is not None
    assert cfg.port == manager.bound_port() and cfg.hosts == ["10.0.0.5", "pc-quanly"]
    assert cfg.fingerprint == manager.cert.fingerprint_sha256 and cfg.team == "Kế toán"
    assert cfg.manager_id == manager.manager_id
    pk = api.packages()
    assert pk[0]["team"] == "Kế toán" and pk[0]["label"] == "Đợt 2"


def test_create_installer_auto_hosts(manager: Manager) -> None:
    res = Api(manager).create_installer("QA", None)
    cfg = read_payload(Path(res["path"]))
    assert cfg is not None and cfg.hosts == ["192.168.1.20", "pc-quanly"]


def test_create_installer_template_missing_prefix(tmp_path: Path) -> None:
    m = Manager(make_cfg(tmp_path, template=tmp_path / "nope.exe"))
    m.start()
    try:
        msg = call_error(Api(m).create_installer, "Dev", None)
        assert msg.startswith("[template_missing]") and "template" in msg
        assert Api(m).packages() == []  # no package created
        assert any("template" in w for w in Api(m).diagnostics()["warnings"])
    finally:
        m.stop()


@pytest.mark.parametrize("team", ["", "   ", "a/b", "x\\y", "a<b", "CON", "Team.", "x" * 41,
                                  "tab\tname", 5, None])
def test_create_installer_invalid_team(manager: Manager, team: Any) -> None:
    msg = call_error(Api(manager).create_installer, team, None)
    assert msg.startswith("[invalid_team]")


def test_set_device_team_and_revoked(demo: Manager) -> None:
    api = Api(demo)
    dev = api.devices()[0]["device_id"]
    assert api.set_device_team(dev, "Nhóm Mới") == {"ok": True}
    assert any(t["name"] == "Nhóm Mới" for t in api.teams())
    assert api.set_device_team(dev, "") == {"ok": True}
    assert any(t["name"] == "(no team)" for t in api.teams())
    assert call_error(api.set_device_team, dev, "a|b").startswith("[invalid_team]")
    assert call_error(api.set_device_team, "ghost", "Dev").startswith("[not_found]")
    assert api.revoke_device(dev) == {"ok": True}
    assert dev not in {d["device_id"] for d in api.devices()}
    assert call_error(api.set_device_team, dev, "Dev").startswith("[revoked]")


def test_revoke_package(demo: Manager) -> None:
    api = Api(demo)
    qa = next(p for p in api.packages() if p["team"] == "QA")
    assert api.revoke_package(qa["package_id"]) == {"ok": True}
    assert next(p for p in api.packages() if p["package_id"] == qa["package_id"])["revoked"]
    assert all(t["name"] != "QA" for t in api.teams())  # its devices are revoked too
    assert call_error(api.revoke_package, "nope").startswith("[not_found]")


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
        assert s["hosts"] == ["192.168.1.20", "pc-quanly"] and s["port"] == m.bound_port()
        assert s["tz_offset_minutes"] == 420 and len(s["fingerprint"]) == 64
        out = api.save_settings({"start_with_windows": True, "transparent_window": False})
        assert out["start_with_windows"] is True and calls == [True]
        assert call_error(api.save_settings, {"port": 1}).startswith("[invalid_settings]")
        assert call_error(api.save_settings, {"hosts": ["a b"]}).startswith("[invalid_settings]")
        assert api.save_settings({"hosts": ["10.1.1.1"]})["hosts"] == ["10.1.1.1"]
        assert api.save_settings({"hosts": []})["hosts"] == ["192.168.1.20", "pc-quanly"]
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
