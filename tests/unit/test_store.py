from __future__ import annotations

import json
import random
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from tokenvault.common.models import UsageEvent
from tokenvault.manager.store import Store

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
    return [tuple(r) for r in store.fetchall(sql, params)]


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
        s = Store(tmp_path / f"p{seed}.db")
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
        assert raw == _raw(s, f"SELECT {cols}, SUM(events) FROM rollup_day")
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
        assert raw_tools == dict(
            _raw(s, "SELECT tool, SUM(calls) FROM rollup_tool_hour GROUP BY tool")
        )
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
    assert _raw(store, "SELECT COUNT(*), SUM(events) FROM rollup_hour") != []
    assert _raw(store, "SELECT COUNT(*) FROM events") == [(200,)]
    assert _raw(store, "SELECT SUM(events) FROM rollup_hour") == [(200,)]
