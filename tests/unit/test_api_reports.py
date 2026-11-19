"""Api report export: options, spec validation, error prefixes, path safety, no prompt text."""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import api_helpers as h
import pytest
from api_helpers import call_error, make_cfg

from tokenvault.manager.api import EXPOSED, Api
from tokenvault.manager.demo_data import seed_now
from tokenvault.manager.lifecycle import Manager

manager = h.manager
demo = h.demo


def _start(tmp_path: Path, **kw: Any) -> tuple[Manager, Api, Path]:
    out = tmp_path / "reports"
    m = Manager(make_cfg(tmp_path, reports_dir=out, **kw))
    m.start()
    seed_now(m.store, m.enrollment, m.tz_offset_minutes)
    return m, Api(m), out


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Any]:
    opened: list[tuple[str, Path]] = []
    m, api, out = _start(
        tmp_path,
        open_file=lambda p: opened.append(("open", p)),
        reveal=lambda p: opened.append(("reveal", p)),
    )
    try:
        yield m, api, out, opened
    finally:
        m.stop()


def _text(path: Path) -> str:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(path.read_bytes()))
    return "\n".join(p.extract_text() for p in reader.pages)


def test_methods_exposed() -> None:
    assert {"report_options", "export_report", "report_progress", "open_path"} <= set(EXPOSED)


def test_report_options(rig: Any) -> None:
    _, api, out, _ = rig
    o = api.report_options()
    assert o["default_dir"] == str(out)
    assert o["can_pick_path"] is False and o["can_open"] is True
    assert "Dev" in o["teams"] and o["max_employees"] >= 1


def test_default_folder_is_documents_tokenvault_reports(demo: Manager) -> None:
    d = Path(Api(demo).report_options()["default_dir"])
    assert d.name == "TokenVault Reports" and d.parent.name == "Documents"


def test_export_all_creates_real_pdf(rig: Any) -> None:
    _, api, out, _ = rig
    res = api.export_report({"scope": "all", "range": "30d"})
    p = Path(res["path"])
    assert p.parent == out.resolve() and p.suffix == ".pdf"
    assert p.read_bytes().startswith(b"%PDF") and res["bytes"] == p.stat().st_size
    assert res["pages"] >= 1 and res["file_name"] == p.name
    prog = api.report_progress()
    assert prog["running"] is False and prog["fraction"] == pytest.approx(1.0)


def test_export_team_employee_custom_range(rig: Any) -> None:
    _, api, _, _ = rig
    emp = api.employees(None)[0]["key"]
    r1 = api.export_report({"scope": "team", "team": "Dev", "range": "7d",
                            "per_employee_pages": False})
    r2 = api.export_report({"scope": "employee", "employee_keys": [emp], "range": "today"})
    today = date.today()
    r3 = api.export_report({"scope": "all", "range": "custom",
                            "start": (today - timedelta(days=3)).isoformat(),
                            "end": today.isoformat()})
    assert len({r1["path"], r2["path"], r3["path"]}) == 3
    assert all(Path(r["path"]).exists() for r in (r1, r2, r3))


def test_same_minute_gets_unique_names(rig: Any) -> None:
    _, api, _, _ = rig
    a = api.export_report({"scope": "all"})
    b = api.export_report({"scope": "all"})
    assert a["path"] != b["path"]


def test_no_prompt_text_or_session_titles(rig: Any) -> None:
    _, api, _, _ = rig
    titles = {s["title"] for s in api.sessions(None) if s.get("title")}
    res = api.export_report({"scope": "all", "range": "30d"})
    text = _text(Path(res["path"]))
    assert text.strip()
    for t in titles:
        assert t not in text


@pytest.mark.parametrize("spec,code", [
    ("nope", "invalid_args"),
    ({"scope": "all", "path": "C:/x.pdf"}, "invalid_args"),
    ({"scope": "all", "destination": "somewhere"}, "invalid_args"),
    ({"scope": "all", "employee_keys": "a"}, "invalid_args"),
    ({"scope": "bogus"}, "invalid_report_spec"),
    ({"scope": "employee"}, "invalid_report_spec"),
    ({"scope": "team"}, "invalid_report_spec"),
    ({"scope": "all", "range": "custom", "start": "2026-02-01", "end": "2026-01-01"},
     "invalid_report_spec"),
    ({"scope": "all", "range": "custom"}, "invalid_report_spec"),
    ({"scope": "all", "per_employee_pages": "yes"}, "invalid_report_spec"),
    ({"scope": "team", "team": "NoSuchTeam"}, "empty_scope"),
    ({"scope": "employee", "employee_keys": ["ghost@nowhere"]}, "empty_scope"),
])
def test_error_prefixes(rig: Any, spec: Any, code: str) -> None:
    _, api, out, _ = rig
    msg = call_error(api.export_report, spec)
    assert msg.startswith(f"[{code}]"), msg
    assert not out.exists() or not list(out.glob("*.pdf"))


def test_traversal_in_team_or_key_is_harmless(rig: Any) -> None:
    _, api, out, _ = rig
    for spec in ({"scope": "team", "team": "..\\..\\evil"},
                 {"scope": "employee", "employee_keys": ["../../evil"]}):
        assert call_error(api.export_report, spec).startswith("[empty_scope]")
    assert not list(out.parent.glob("**/evil*"))


def test_dialog_destination(tmp_path: Path) -> None:
    chosen = tmp_path / "picked" / "my:report?.PDF"
    chosen.parent.mkdir()
    seen: list[str] = []

    def pick(name: str, folder: Path) -> Path | None:
        seen.append(name)
        return chosen

    m, api, _ = _start(tmp_path, pick_save_path=pick)
    try:
        assert api.report_options()["can_pick_path"] is True
        res = api.export_report({"scope": "all", "destination": "dialog"})
        p = Path(res["path"])
        assert p.parent == chosen.parent.resolve() and p.suffix == ".pdf"
        assert ":" not in p.name and "?" not in p.name
        assert seen and seen[0].endswith(".pdf")
        m.cfg.pick_save_path = lambda n, f: None
        msg = call_error(api.export_report, {"scope": "all", "destination": "dialog"})
        assert msg.startswith("[cancelled]")
    finally:
        m.stop()


def test_dialog_without_hook_refused(rig: Any) -> None:
    _, api, _, _ = rig
    msg = call_error(api.export_report, {"scope": "all", "destination": "dialog"})
    assert msg.startswith("[invalid_args]")


def test_open_path_rules(rig: Any, tmp_path: Path) -> None:
    _, api, out, opened = rig
    res = api.export_report({"scope": "all"})
    assert api.open_path(res["path"]) == {"ok": True}
    assert api.open_path(res["path"], True) == {"ok": True}
    assert [k for k, _ in opened] == ["open", "reveal"]
    stray = tmp_path / "stray.pdf"
    stray.write_bytes(b"%PDF-1.4")
    assert call_error(api.open_path, str(stray)).startswith("[invalid_args]")
    sneaky = str(out / ".." / "stray.pdf")
    assert call_error(api.open_path, sneaky).startswith("[invalid_args]")
    (out / "note.txt").write_text("x")
    assert call_error(api.open_path, str(out / "note.txt")).startswith("[invalid_args]")
    (out / "other.pdf").write_bytes(b"%PDF-1.4")  # inside the folder: allowed
    assert api.open_path(str(out / "other.pdf")) == {"ok": True}
    assert call_error(api.open_path, str(out / "missing.pdf")).startswith("[not_found]")
    assert call_error(api.open_path, str(out)).startswith("[invalid_args]")
    assert call_error(api.open_path, 5).startswith("[invalid_args]")
    assert call_error(api.open_path, res["path"], "yes").startswith("[invalid_args]")
    assert len(opened) == 3


def test_busy_when_export_running(rig: Any) -> None:
    _, api, _, _ = rig
    assert api._reports.lock.acquire(blocking=False)
    try:
        assert call_error(api.export_report, {"scope": "all"}).startswith("[busy]")
    finally:
        api._reports.lock.release()
