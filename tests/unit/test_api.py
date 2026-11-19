"""Manager Api contract: read methods, JSON shape, errors, installers, teams."""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import api_helpers as h
import pytest
from api_helpers import call_error, make_cfg

from tokenvault.common.config_payload import read_payload
from tokenvault.common.models import UsageEvent
from tokenvault.manager.api import EXPOSED, Api
from tokenvault.manager.demo_data import UNPRICED_MODEL
from tokenvault.manager.lifecycle import Manager

manager = h.manager  # fixtures shared with the other test_api* module
demo = h.demo


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


