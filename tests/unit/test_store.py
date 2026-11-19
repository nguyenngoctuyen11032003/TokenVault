from __future__ import annotations

import json
import random
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from tokenvault.common.models import UsageEvent
from tokenvault.manager.store import SchemaTooNewError, Store, TzOffsetChangedError
from tokenvault.manager.store_core import parse_ts

TOKEN_FIELDS = ("input", "output", "cache_read", "cache_5m", "cache_1h")


def ev(n: int = 1, **kw: object) -> UsageEvent:
    base = UsageEvent(
        uuid=f"u{n}",
        session_id="s1",
        message_id=f"msg_{n}",
        timestamp="2026-11-19T10:00:00.000Z",
        model="claude-sonnet-4-6",
        reasoning_effort=None,
        is_sidechain=False,
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=30,
        cache_create_5m_tokens=40,
        cache_create_1h_tokens=50,
        project="proj",
        session_title=None,
        tool_names=(),
        claude_account_email="a@x.com",
        account_uuid="acc",
        windows_user="alice",
        hostname="PC1",
        team="Dev",
        device_id="dev1",
    )
    return replace(base, **kw)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "m.db")
    yield s  # type: ignore[misc]
    s.close()


def _raw(store: Store, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    return [tuple(r) for r in store._fetchall(sql, params)]


def test_schema_uses_wal_and_user_version(tmp_path: Path) -> None:
    s = Store(tmp_path / "m.db")
    s.close()
    con = sqlite3.connect(tmp_path / "m.db")
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("PRAGMA user_version").fetchone()[0] >= 1
    con.close()
    # reopening an existing db must not re-run migrations / fail
    Store(tmp_path / "m.db").close()


def test_upsert_same_message_twice_counts_once(store: Store) -> None:
    assert store.upsert_events("dev1", [ev(1)]) == 1
    assert store.upsert_events("dev1", [ev(1)]) == 0
    assert store.upsert_events("dev1", [ev(1, uuid="other-line")]) == 0
    assert _raw(store, "SELECT COUNT(*), SUM(input) FROM events") == [(1, 10)]
    assert _raw(store, "SELECT SUM(input), SUM(events) FROM rollup_hour") == [(10, 1)]
    # same message id on another device is a different row
    assert store.upsert_events("dev2", [ev(1, device_id="dev2")]) == 1
    # duplicates inside a single batch also count once
    assert store.upsert_events("dev1", [ev(7), ev(7), ev(7)]) == 1
    assert _raw(store, "SELECT COUNT(*) FROM events") == [(3,)]


def test_message_id_none_falls_back_to_uuid(store: Store) -> None:
    assert store.upsert_events("dev1", [ev(1, message_id=None, uuid="uu")]) == 1
    assert store.upsert_events("dev1", [ev(1, message_id=None, uuid="uu")]) == 0
    assert store.upsert_events("dev1", [ev(1, message_id=None, uuid="vv")]) == 1


def test_larger_usage_wins(store: Store) -> None:
    store.upsert_events("dev1", [ev(1, input_tokens=100, output_tokens=5)])
    # grows output only; smaller input must not shrink the stored value
    assert store.upsert_events("dev1", [ev(1, input_tokens=50, output_tokens=80)]) == 1
    row = _raw(store, "SELECT input, output, cache_read, cache_5m, cache_1h FROM events")
    assert row == [(100, 80, 30, 40, 50)]
    roll = _raw(store, "SELECT SUM(input), SUM(output), SUM(events) FROM rollup_hour")
    assert roll == [(100, 80, 1)]
    roll_d = _raw(store, "SELECT SUM(input), SUM(output), SUM(events) FROM rollup_day")
    assert roll_d == [(100, 80, 1)]
    # all-smaller duplicate changes nothing
    assert store.upsert_events("dev1", [ev(1, input_tokens=1, output_tokens=1)]) == 0
    assert _raw(store, "SELECT SUM(input), SUM(output) FROM rollup_hour") == [(100, 80)]


def test_tool_names_union_first_seen_order(store: Store) -> None:
    store.upsert_events("dev1", [ev(1, tool_names=("Read", "Bash"))])
    assert store.upsert_events("dev1", [ev(1, tool_names=("Skill", "Read"))]) == 1
    (tools,) = _raw(store, "SELECT tool_names FROM events")[0]
    assert json.loads(str(tools)) == ["Read", "Bash", "Skill"]
    counts = dict(_raw(store, "SELECT tool, SUM(calls) FROM rollup_tool_hour GROUP BY tool"))
    assert counts == {"Read": 1, "Bash": 1, "Skill": 1}


def test_session_title_latest_non_null_wins(store: Store) -> None:
    store.upsert_events("dev1", [ev(1, session_title="Old")])
    store.upsert_events("dev1", [ev(2, session_title="New")])
    store.upsert_events("dev1", [ev(3, session_title=None)])
    store.upsert_events("dev1", [ev(1, session_title=None)])
    assert _raw(store, "SELECT session_title FROM events WHERE msg_key='msg_1'") == [("Old",)]
    assert _raw(store, "SELECT title FROM session_meta") == [("New",)]
    store.upsert_events("dev1", [ev(1, session_title="Newest")])
    assert _raw(store, "SELECT session_title FROM events WHERE msg_key='msg_1'") == [("Newest",)]
    assert _raw(store, "SELECT title FROM session_meta") == [("Newest",)]


def test_unicode_titles_roundtrip(store: Store) -> None:
    title = "Sửa lỗi đăng nhập — tiếng Việt 🚀"
    project = "dự-án-thử"
    store.upsert_events("dev1", [ev(1, session_title=title, project=project)])
    assert _raw(store, "SELECT session_title, project FROM events") == [(title, project)]
    assert _raw(store, "SELECT DISTINCT project FROM rollup_hour") == [(project,)]
    assert _raw(store, "SELECT title FROM session_meta") == [(title,)]


def test_devices_upsert_list_and_team(store: Store) -> None:
    store.upsert_device("dev1", "Dev", "a@x.com", 1000.0, "alice", "PC1")
    store.upsert_device("dev2", "QA", None, 990.0, "bob", "PC2")
    store.set_device_team("dev2", "Dev")
    devs = {d.device_id: d for d in store.list_devices()}
    assert devs["dev2"].team == "Dev" and devs["dev2"].email is None
    assert devs["dev1"].windows_user == "alice" and devs["dev1"].hostname == "PC1"
    online = store.devices_online(1020.0)
    assert online == ["dev1", "dev2"]
    assert store.devices_online(1025.0, window_s=30) == ["dev1"]


def test_events_create_device_row_if_missing(store: Store) -> None:
    store.upsert_events("devX", [ev(1, device_id="devX", team="Ops", windows_user="u")])
    (d,) = store.list_devices()
    assert (d.device_id, d.team, d.windows_user, d.email) == ("devX", "Ops", "u", "a@x.com")


def test_rollup_totals_equal_raw_sum(tmp_path: Path) -> None:
    for seed in range(5):
        rnd = random.Random(seed)
        s = Store(tmp_path / f"p{seed}.db", tz_offset_minutes=[0, 420, -300, 330, 345][seed])
        pool: list[UsageEvent] = []
        for _ in range(40):
            batch: list[UsageEvent] = []
            for _ in range(rnd.randint(0, 25)):
                if pool and rnd.random() < 0.4:
                    base = rnd.choice(pool)
                    e = replace(
                        base,
                        input_tokens=max(0, base.input_tokens + rnd.randint(-50, 50)),
                        output_tokens=max(0, base.output_tokens + rnd.randint(-50, 50)),
                        cache_read_tokens=max(0, base.cache_read_tokens + rnd.randint(-9, 9)),
                        cache_create_5m_tokens=rnd.randint(0, 200),
                        cache_create_1h_tokens=rnd.randint(0, 200),
                        tool_names=tuple(rnd.sample(["Read", "Bash", "Skill", "Task"], 2)),
                        session_title=rnd.choice([None, "t1", "t2"]),
                        # dimensions of a duplicate never move the row
                        project=rnd.choice(["p1", "p2"]),
                        timestamp=f"2026-11-{rnd.randint(1, 28):02d}T10:00:00Z",
                    )
                else:
                    n = rnd.randint(0, 10**9)
                    e = ev(
                        n,
                        device_id=rnd.choice(["d1", "d2", "d3"]),
                        session_id=rnd.choice(["s1", "s2", "s3", "s4"]),
                        timestamp=(
                            f"2026-11-{rnd.randint(1, 28):02d}T{rnd.randint(0, 23):02d}:"
                            f"{rnd.randint(0, 59):02d}:00+07:00"
                        ),
                        model=rnd.choice(["claude-sonnet-4-6", "claude-opus-4-7", None]),
                        is_sidechain=rnd.random() < 0.3,
                        input_tokens=rnd.randint(0, 1000),
                        output_tokens=rnd.randint(0, 1000),
                        cache_read_tokens=rnd.randint(0, 5000),
                        cache_create_5m_tokens=rnd.randint(0, 500),
                        cache_create_1h_tokens=rnd.randint(0, 500),
                        project=rnd.choice(["p1", "p2", "p3"]),
                        tool_names=tuple(rnd.sample(["Read", "Bash", "Skill", "Task"], 2)),
                        claude_account_email=rnd.choice(["a@x", "b@x", None]),
                    )
                pool.append(e)
                batch.append(e)
            by_dev: dict[str, list[UsageEvent]] = {}
            for e in batch:
                by_dev.setdefault(e.device_id, []).append(e)
            for dev, evs in by_dev.items():
                s.upsert_events(dev, evs)
        cols = ", ".join(f"SUM({c})" for c in TOKEN_FIELDS)
        raw = _raw(s, f"SELECT {cols}, COUNT(*) FROM events")
        assert raw == _raw(s, f"SELECT {cols}, SUM(events) FROM rollup_hour")
        for table in ("rollup_day", "rollup_month"):
            assert raw == _raw(s, f"SELECT {cols}, SUM(events) FROM {table}")
        assert raw == _raw(s, f"SELECT {cols}, SUM(events) FROM rollup_session")
        # grouped equality, not just grand totals
        g = "device_id, email, project, model, sidechain, hour"
        assert _raw(s, f"SELECT {g}, {cols} FROM events GROUP BY {g} ORDER BY {g}") == _raw(
            s, f"SELECT {g}, {', '.join(TOKEN_FIELDS)} FROM rollup_hour ORDER BY {g}"
        )
        # tool calls rollup equals raw tool occurrences
        raw_tools: dict[str, int] = {}
        for (tn,) in _raw(s, "SELECT tool_names FROM events"):
            for t in json.loads(str(tn)):
                raw_tools[t] = raw_tools.get(t, 0) + 1
        for table in ("rollup_tool_hour", "rollup_tool_day", "rollup_tool_month"):
            assert raw_tools == dict(_raw(s, f"SELECT tool, SUM(calls) FROM {table} GROUP BY tool"))
        s.close()


def test_concurrent_writers_are_serialised(store: Store) -> None:
    def work(dev: str) -> None:
        for i in range(50):
            store.upsert_events(dev, [ev(i, device_id=dev)])

    threads = [threading.Thread(target=work, args=(f"d{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    per_dev = _raw(store, "SELECT device_id, SUM(events), SUM(input) FROM rollup_hour GROUP BY 1")
    assert per_dev == [(f"d{i}", 50, 500) for i in range(4)]
    assert _raw(store, "SELECT COUNT(*) FROM events") == [(200,)]
    assert _raw(store, "SELECT SUM(events) FROM rollup_hour") == [(200,)]


def test_title_does_not_regress_on_resend_of_older_event(store: Store) -> None:
    store.upsert_events("dev1", [ev(1, timestamp="2026-11-19T10:00:00Z", session_title="Old")])
    store.upsert_events("dev1", [ev(2, timestamp="2026-11-19T11:00:00Z", session_title="New")])
    # resend of the older event, still carrying the older title
    store.upsert_events("dev1", [ev(1, timestamp="2026-11-19T10:00:00Z", session_title="Old")])
    assert _raw(store, "SELECT title FROM session_meta") == [("New",)]
    # same inside one batch, older event last
    store.upsert_events(
        "dev1",
        [
            ev(4, session_id="s2", timestamp="2026-11-19T12:00:00Z", session_title="B"),
            ev(3, session_id="s2", timestamp="2026-11-19T09:00:00Z", session_title="A"),
        ],
    )
    assert _raw(store, "SELECT title FROM session_meta WHERE session_id='s2'") == [("B",)]


def test_identity_frozen_at_ingest(store: Store) -> None:
    store.upsert_device("dev1", "Dev", None, 1.0, "alice", "PC1")
    store.upsert_events("dev1", [ev(1, claude_account_email=None)])
    store.upsert_device("dev1", "Dev", "later@x.com", 2.0)  # device learns an e-mail
    store.upsert_events("dev1", [ev(1, claude_account_email=None, output_tokens=99)])
    store.upsert_events("dev1", [ev(2, claude_account_email=None)])
    rows = _raw(store, "SELECT msg_key, email, account_email FROM events ORDER BY msg_key")
    # old row stays unattributed even after growing; new row gets device e-mail
    assert rows == [("msg_1", "", None), ("msg_2", "later@x.com", None)]
    assert _raw(store, "SELECT email, SUM(output) FROM rollup_hour GROUP BY email ORDER BY 1") == [
        ("", 99),
        ("later@x.com", 20),
    ]


def test_upsert_commits_in_chunks(tmp_path: Path) -> None:
    s = Store(tmp_path / "c.db", chunk_rows=10)
    commits = []
    s._con.set_trace_callback(lambda sql: commits.append(sql) if sql == "COMMIT" else None)
    assert s.upsert_events("dev1", [ev(i) for i in range(35)]) == 35
    assert len(commits) == 4
    assert _raw(s, "SELECT COUNT(*), SUM(events) FROM rollup_hour") == [(1, 35)]
    s.close()


def test_schema_newer_than_supported_is_refused(tmp_path: Path) -> None:
    Store(tmp_path / "n.db").close()
    con = sqlite3.connect(tmp_path / "n.db")
    con.execute("PRAGMA user_version=99")
    con.close()
    with pytest.raises(SchemaTooNewError, match="99"):
        Store(tmp_path / "n.db")


def test_migration_v1_backfills_identity_once(tmp_path: Path) -> None:
    db = tmp_path / "v1.db"
    s = Store(db)
    s.upsert_events(
        "dev1", [ev(1, claude_account_email=None, session_title="T", tool_names=("Read", "Skill"))]
    )
    s.upsert_events("dev2", [ev(2, device_id="dev2", claude_account_email="b@x")])
    s.close()
    # downgrade to the v1 layout, then give dev1 an e-mail as v1 would have
    con = sqlite3.connect(db)
    con.executescript(
        "ALTER TABLE events DROP COLUMN account_email; ALTER TABLE events DROP COLUMN title_ts;"
        "ALTER TABLE session_meta DROP COLUMN title_ts;"
        "DROP TABLE meta; DROP TABLE rollup_month; DROP TABLE rollup_tool_day;"
        "DROP TABLE rollup_tool_month; DROP INDEX ix_rs_project;"
        "UPDATE rollup_hour SET email = '';"  # v1 rollups keyed the empty e-mail
        "UPDATE devices SET email='a@x' WHERE device_id='dev1'; PRAGMA user_version=1;"
    )
    con.close()
    s = Store(db)
    assert _raw(s, "SELECT device_id, email, account_email FROM events ORDER BY 1") == [
        ("dev1", "a@x", None),
        ("dev2", "b@x", "b@x"),
    ]
    assert _raw(s, "SELECT device_id, email, events FROM rollup_hour ORDER BY 1") == [
        ("dev1", "a@x", 1),
        ("dev2", "b@x", 1),
    ]
    assert _raw(s, "SELECT COUNT(*) FROM rollup_session") == [(2,)]
    assert _raw(s, "SELECT email, SUM(calls) FROM rollup_tool_hour GROUP BY 1") == [("a@x", 2)]
    assert _raw(s, "PRAGMA user_version") == [(2,)]
    for table in ("rollup_day", "rollup_month"):
        assert _raw(s, f"SELECT email, SUM(events) FROM {table} GROUP BY 1") == [
            ("a@x", 1),
            ("b@x", 1),
        ]
    assert _raw(s, "SELECT SUM(calls) FROM rollup_tool_month") == [(2,)]
    # session_meta.title_ts backfilled from the event that carried the title
    assert _raw(s, "SELECT title, title_ts FROM session_meta") == [("T", parse_ts(ev(1).timestamp))]
    s.close()


LOCAL_TABLES = ("rollup_day", "rollup_month", "rollup_tool_day", "rollup_tool_month")


def _local_rows(s: Store) -> dict[str, list[tuple[object, ...]]]:
    return {t: _raw(s, f"SELECT * FROM {t} ORDER BY 1, 2, 3, 4, 5, 6") for t in LOCAL_TABLES}


def _spread(n: int, start: int = 0) -> list[UsageEvent]:
    # hours 0..23 UTC on two days: UTC and UTC+7 put many of them on different days
    return [
        ev(i, timestamp=f"2026-11-{18 + i % 2:02d}T{i % 24:02d}:30:00Z", tool_names=("Read",))
        for i in range(start, start + n)
    ]


def test_secondary_opener_with_default_offset_does_not_rebuild(tmp_path: Path) -> None:
    a = Store(tmp_path / "m.db", tz_offset_minutes=420)
    a.upsert_events("dev1", _spread(30))
    b = Store(tmp_path / "m.db")  # e.g. CLI / export: adopts the recorded offset
    assert b.tz_offset_minutes == 420
    assert b._con.total_changes == 0  # nothing rebuilt or rewritten
    assert _local_rows(b) == _local_rows(a)
    b.close()
    a.close()


def test_two_stores_with_different_offsets_never_write_miskeyed_rows(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    a = Store(db, tz_offset_minutes=420)
    first, second = _spread(30), _spread(30, start=100)
    a.upsert_events("dev1", first)
    c = Store(db, tz_offset_minutes=0)  # explicit, different: rebuilds for UTC
    with pytest.raises(TzOffsetChangedError, match="another Store"):
        a.upsert_events("dev1", second)  # A fails loudly, writes nothing
    assert _raw(c, "SELECT COUNT(*) FROM events") == [(30,)]
    assert not a._con.in_transaction
    # rebuilt local tables equal incrementally built ones
    fresh0 = Store(tmp_path / "f0.db", tz_offset_minutes=0)
    fresh0.upsert_events("dev1", first)
    assert _local_rows(c) == _local_rows(fresh0)
    # the UTC store keeps writing correctly keyed rows
    c.upsert_events("dev1", second)
    fresh0.upsert_events("dev1", second)
    assert _local_rows(c) == _local_rows(fresh0)
    # reopening as UTC+7 rebuilds again and equals an incremental UTC+7 store
    a.close()
    a2 = Store(db, tz_offset_minutes=420)
    fresh7 = Store(tmp_path / "f7.db", tz_offset_minutes=420)
    fresh7.upsert_events("dev1", first + second)
    assert _local_rows(a2) == _local_rows(fresh7)
    for st in (a2, c, fresh0, fresh7):
        st.close()


@pytest.mark.parametrize("chunk_rows", [3, 1])
def test_identity_does_not_depend_on_chunk_size(tmp_path: Path, chunk_rows: int) -> None:
    s = Store(tmp_path / "c.db", chunk_rows=chunk_rows)
    s.upsert_events(
        "dev1",
        [
            ev(1, claude_account_email="a@x"),
            ev(2, claude_account_email=None),
            ev(3, claude_account_email=None),
        ],
    )
    assert _raw(s, "SELECT msg_key, email FROM events ORDER BY 1") == [
        ("msg_1", "a@x"),
        ("msg_2", "a@x"),
        ("msg_3", "a@x"),
    ]
    assert [d.email for d in s.list_devices()] == ["a@x"]
    s.close()


def test_failed_begin_is_not_masked_by_rollback(tmp_path: Path) -> None:
    s = Store(tmp_path / "b.db", busy_timeout_ms=50)
    other = sqlite3.connect(tmp_path / "b.db", isolation_level=None)
    other.execute("BEGIN IMMEDIATE")  # holds the write lock
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        s.upsert_events("dev1", [ev(1)])
    assert not s._con.in_transaction
    other.execute("ROLLBACK")
    other.close()
    assert s.upsert_events("dev1", [ev(1)]) == 1
    s.close()
