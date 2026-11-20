"""Task 30: read-side indexes, reader pragmas, presence batching and the read cache."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import api_helpers as h
import pytest

from tokenvault.manager.read_cache import ReadCache
from tokenvault.manager.store import Store
from tokenvault.manager.store_core import PERF_INDEXES

manager = h.manager  # fixtures from api_helpers
demo = h.demo

NAMES = ("ix_rs_span", "ix_rs_project_span", "ix_rd_day_cover", "ix_rtd_day_cover",
         "ix_rt_hour_cover")


def _indexes(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        con.close()


def test_perf_indexes_exist_on_new_and_existing_databases(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    Store(db, tz_offset_minutes=420).close()
    assert set(NAMES) <= _indexes(db)
    con = sqlite3.connect(db)
    for n in NAMES:
        con.execute(f"DROP INDEX {n}")
    con.commit()
    con.close()
    Store(db).close()  # an older database gains them on the next open
    assert set(NAMES) <= _indexes(db)
    assert all(n in PERF_INDEXES for n in NAMES)


@pytest.mark.parametrize(("sql", "index"), [
    # session counts / totals (overview, employees): index-only scan
    ("SELECT COUNT(*) FROM (SELECT 1 FROM rollup_session r WHERE r.last_ts >= 0 AND "
     "r.first_ts < 9e9 GROUP BY r.device_id, r.session_id)", "COVERING INDEX ix_rs_span"),
    ("SELECT r.project, COUNT(*) FROM rollup_session r WHERE r.last_ts >= 0 AND r.first_ts < 9e9 "
     "GROUP BY r.project, r.device_id, r.session_id", "COVERING INDEX ix_rs_project_span"),
    ("SELECT r.day, r.model, SUM(r.input), SUM(r.output), SUM(r.cache_read), SUM(r.cache_5m), "
     "SUM(r.cache_1h) FROM rollup_day r WHERE r.day >= 1 AND r.day < 90 GROUP BY r.day, r.model",
     "COVERING INDEX ix_rd_day_cover"),
    ("SELECT r.tool, SUM(r.calls) FROM rollup_tool_day r WHERE r.day >= 1 AND r.day < 9 "
     "GROUP BY r.tool", "COVERING INDEX ix_rtd_day_cover"),
    ("SELECT r.tool, SUM(r.calls) FROM rollup_tool_hour r WHERE r.hour >= 1 AND r.hour < 9 "
     "GROUP BY r.tool", "COVERING INDEX ix_rt_hour_cover"),
])
def test_hot_queries_use_covering_indexes(tmp_path: Path, sql: str, index: str) -> None:
    s = Store(tmp_path / "m.db", tz_offset_minutes=420)
    try:
        plan = " | ".join(r[3] for r in s._rcon.execute("EXPLAIN QUERY PLAN " + sql))
    finally:
        s.close()
    assert index in plan, plan


def test_reader_connection_is_tuned(tmp_path: Path) -> None:
    s = Store(tmp_path / "m.db")
    try:
        r = s._rcon
        assert r.execute("PRAGMA query_only").fetchone()[0] == 1
        assert r.execute("PRAGMA cache_size").fetchone()[0] == -16384
        assert r.execute("PRAGMA temp_store").fetchone()[0] == 2  # MEMORY
        assert r.execute("PRAGMA mmap_size").fetchone()[0] > 0
        assert s._con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert s._con.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    finally:
        s.close()


def test_presence_touch_is_one_commit_and_keeps_device_data(tmp_path: Path) -> None:
    s = Store(tmp_path / "m.db")
    try:
        s.upsert_device("a", "Dev", "a@x", 10.0, "ua", "ha")
        stmts: list[str] = []
        s._con.set_trace_callback(stmts.append)
        s.touch_devices(["a", "b", "c"], 50.0)
        s._con.set_trace_callback(None)
        assert sum(1 for x in stmts if x.strip().upper() == "COMMIT") == 1
        devs = {d.device_id: d for d in s.list_devices()}
        assert (devs["a"].team, devs["a"].email, devs["a"].windows_user) == ("Dev", "a@x", "ua")
        assert devs["a"].last_seen == 50.0 and devs["c"].last_seen == 50.0
        s.touch_devices(["a"], 20.0)  # last_seen only grows
        assert {d.device_id: d for d in s.list_devices()}["a"].last_seen == 50.0
        s.touch_devices([], 60.0)
    finally:
        s.close()


def test_data_version_moves_on_writer_commits(tmp_path: Path) -> None:
    s = Store(tmp_path / "m.db")
    try:
        v0 = s.data_version()
        assert s.data_version() == v0
        s.upsert_device("a", "Dev", None, 1.0)
        assert s.data_version() != v0
    finally:
        s.close()


def test_read_cache_hits_until_revision_or_ttl_changes() -> None:
    rev = {"v": 1}
    t = {"now": 0.0}
    c = ReadCache(lambda: rev["v"], ttl_s=2.0, clock=lambda: t["now"])
    calls: list[int] = []

    def compute() -> Any:
        calls.append(1)
        return {"n": len(calls)}

    assert c.get("k", compute) == {"n": 1}
    assert c.get("k", compute) == {"n": 1}  # same revision, fresh
    rev["v"] = 2
    assert c.get("k", compute) == {"n": 2}  # the store committed
    t["now"] = 5.0
    assert c.get("k", compute) == {"n": 3}  # older than the TTL
    assert c.get("other", compute) == {"n": 4}
    assert c.hits == 1 and c.misses == 4


def test_api_reuses_an_unchanged_overview_and_recomputes_after_a_write(
        demo: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from tokenvault.manager.api import Api

    api = Api(demo)
    calls: list[Any] = []
    real = demo.aggregates.overview
    monkeypatch.setattr(demo.aggregates, "overview", lambda f: calls.append(f) or real(f))
    first = api.overview(None)
    assert api.overview(None) == first and len(calls) == 1
    demo.store.upsert_device("new-device", "Dev", None, 1.0)  # any commit invalidates
    api.overview(None)
    assert len(calls) == 2


def test_read_cache_is_bounded() -> None:
    c = ReadCache(lambda: 1, size=3)
    for i in range(10):
        c.get(i, lambda i=i: i)
    assert len(c._items) == 3
