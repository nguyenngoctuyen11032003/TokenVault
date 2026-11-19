from __future__ import annotations

import dataclasses
import json
import random
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tokenvault.common.models import UsageEvent
from tokenvault.common.pricing import Pricing
from tokenvault.manager.aggregates import NO_MODEL, Aggregates, Filter
from tokenvault.manager.store import Store

PRICING = {
    "_comment": "test",
    "models": {
        "claude-sonnet-4-6": {
            "input": 3.0,
            "output": 15.0,
            "cache_read": 0.3,
            "cache_create_5m": 3.75,
            "cache_create_1h": 6.0,
        }
    },
}
# 2026-11-19 12:00 UTC == 19:00 in Vietnam (UTC+7)
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC).timestamp()
VN = 7 * 60


def ev(n: int = 1, **kw: object) -> UsageEvent:
    base = UsageEvent(
        uuid=f"u{n}",
        session_id="s1",
        message_id=f"msg_{n}",
        timestamp="2026-11-19T10:00:00Z",
        model="claude-sonnet-4-6",
        reasoning_effort=None,
        is_sidechain=False,
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_create_5m_tokens=0,
        cache_create_1h_tokens=0,
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
def pricing(tmp_path: Path) -> Pricing:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return Pricing.load(p)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "m.db")
    yield s
    s.close()


def agg(store: Store, pricing: Pricing, tz: int = 0) -> Aggregates:
    return Aggregates(store, pricing, tz_offset_minutes=tz, now=lambda: NOW)


def put(store: Store, *events: UsageEvent) -> None:
    for e in events:
        store.upsert_events(e.device_id, [e])


def test_overview_totals_cost_and_shape(store: Store, pricing: Pricing) -> None:
    put(
        store,
        ev(1, output_tokens=1_000_000, tool_names=("Read", "Skill"), session_title="T"),
        ev(2, session_id="s2", project="other", tool_names=("Read",)),
    )
    ov = agg(store, pricing).overview(Filter())
    assert ov.totals.input_tokens == 2_000_000
    assert ov.totals.total_tokens == 3_000_000
    assert ov.totals.sessions == 2 and ov.totals.events == 2
    assert ov.totals.cost == pytest.approx(3 + 15 + 3)
    assert ov.unknown_cost_models == []
    assert [p.project for p in ov.by_project] == ["proj", "other"]
    assert ov.top_tools[0].tool == "Read" and ov.top_tools[0].calls == 2
    assert ov.recent_sessions[0].session_id in {"s1", "s2"}
    assert {d.day for d in ov.daily} == {"2026-11-19"}
    assert ov.cache_daily and ov.hourly
    json.dumps(dataclasses.asdict(ov))  # must be JSON-serialisable


def test_unknown_model_surfaces_in_overview(store: Store, pricing: Pricing) -> None:
    put(store, ev(1), ev(2, model="claude-mystery-9"), ev(3, model=None))
    ov = agg(store, pricing).overview(Filter())
    assert sorted(ov.unknown_cost_models) == ["(no model)", "claude-mystery-9"]
    by_model = {m.model: m for m in ov.by_model}
    assert by_model["claude-mystery-9"].cost is None
    assert by_model["claude-sonnet-4-6"].cost == pytest.approx(3)
    assert ov.totals.cost == pytest.approx(3)  # known part only
    assert ov.totals.cost_partial is True


def test_team_filter_uses_current_device_team_retroactively(store: Store, pricing: Pricing) -> None:
    store.upsert_device("dev1", "Dev", "a@x.com", NOW, "alice", "PC1")
    store.upsert_device("dev2", "QA", "b@x.com", NOW, "bob", "PC2")
    put(store, ev(1), ev(2, device_id="dev2", claude_account_email="b@x.com", team="QA"))
    a = agg(store, pricing)
    assert a.overview(Filter(team="QA")).totals.input_tokens == 1_000_000
    store.set_device_team("dev1", "QA")  # old rows follow the device
    assert a.overview(Filter(team="QA")).totals.input_tokens == 2_000_000
    assert a.overview(Filter(team="Dev")).totals.input_tokens == 0
    assert [e.email for e in a.employees(Filter(team="QA"))] == ["a@x.com", "b@x.com"]


def test_same_email_two_devices_merge_in_employee_view(store: Store, pricing: Pricing) -> None:
    store.upsert_device("dev1", "Dev", "a@x.com", NOW, "alice", "PC1")
    store.upsert_device("dev2", "Dev", "a@x.com", NOW - 3600, "alice", "LAPTOP")
    put(store, ev(1), ev(2, device_id="dev2", session_id="s9"))
    a = agg(store, pricing)
    rows = a.employees(Filter())
    assert len(rows) == 1
    (r,) = rows
    assert r.email == "a@x.com" and r.total_tokens == 2_000_000
    assert sorted(r.devices) == ["dev1", "dev2"] and r.online is True
    assert r.sessions == 2
    view = a.employee("a@x.com", "today")
    assert view.totals.input_tokens == 2_000_000
    assert sorted(d.device_id for d in view.devices) == ["dev1", "dev2"]
    assert {s.session_id for s in view.sessions} == {"s1", "s9"}
    json.dumps(dataclasses.asdict(view))


def test_unknown_account_row_listed(store: Store, pricing: Pricing) -> None:
    store.upsert_device("dev3", "Dev", None, NOW, "carol", "PC3")
    put(store, ev(1, device_id="dev3", claude_account_email=None))
    put(store, ev(2))
    a = agg(store, pricing)
    rows = {r.display_name: r for r in a.employees(Filter())}
    assert "Unknown account (carol@PC3)" in rows
    unk = rows["Unknown account (carol@PC3)"]
    assert unk.email is None and unk.total_tokens == 1_000_000
    # the key works for drill-down and filtering
    assert a.employee(unk.key, "today").totals.input_tokens == 1_000_000
    assert a.overview(Filter(email=unk.key)).totals.input_tokens == 1_000_000
    assert a.overview(Filter(email="a@x.com")).totals.input_tokens == 1_000_000


def test_employee_listed_even_without_events(store: Store, pricing: Pricing) -> None:
    store.upsert_device("dev4", "Dev", None, NOW, "dan", "PC4")
    names = [r.display_name for r in agg(store, pricing).employees(Filter())]
    assert names == ["Unknown account (dan@PC4)"]


def test_today_uses_local_day_utc_plus_7(store: Store, pricing: Pricing) -> None:
    # 16:59 UTC on the 18th == 23:59 on the 18th in VN -> yesterday
    # 17:00 UTC on the 18th == 00:00 on the 19th in VN -> today
    put(
        store,
        ev(1, timestamp="2026-11-18T16:59:59Z", input_tokens=1),
        ev(2, timestamp="2026-11-18T17:00:00Z", input_tokens=10),
        ev(3, timestamp="2026-11-19T11:59:00Z", input_tokens=100),
    )
    vn = agg(store, pricing, tz=VN)
    assert vn.employee("a@x.com", "today").totals.input_tokens == 110
    days = {d.day: d.input_tokens for d in vn.overview(Filter()).daily}
    assert days == {"2026-11-18": 1, "2026-11-19": 110}
    utc = agg(store, pricing, tz=0)
    days_utc = {d.day: d.input_tokens for d in utc.overview(Filter()).daily}
    assert days_utc == {"2026-11-18": 11, "2026-11-19": 100}
    # date-only filter bounds are local days
    f = Filter(start="2026-11-19", end="2026-11-19")
    assert vn.overview(f).totals.input_tokens == 110


def test_filters_project_model_and_range(store: Store, pricing: Pricing) -> None:
    put(
        store,
        ev(1, project="A", timestamp="2026-11-10T10:00:00Z"),
        ev(2, project="B", model="claude-opus-4-7", timestamp="2026-11-19T10:00:00Z"),
    )
    a = agg(store, pricing)
    assert a.overview(Filter(project="A")).totals.events == 1
    assert a.overview(Filter(model="claude-opus-4-7")).totals.events == 1
    assert a.overview(Filter(start="2026-11-15")).totals.events == 1
    assert a.overview(Filter(end="2026-11-15")).totals.events == 1
    # equal tokens -> ordered by name
    assert [p.project for p in a.projects(Filter())] == ["A", "B"]
    assert a.projects(Filter(project="B"))[0].unknown_cost_models == ["claude-opus-4-7"]


def test_sessions_and_turns(store: Store, pricing: Pricing) -> None:
    put(
        store,
        ev(2, timestamp="2026-11-19T10:05:00Z", is_sidechain=True, tool_names=("Task",)),
        ev(1, timestamp="2026-11-19T10:00:00Z", session_title="Tiêu đề"),
        ev(3, session_id="s2", timestamp="2026-11-19T11:00:00Z"),
    )
    a = agg(store, pricing)
    ss = a.sessions(Filter())
    assert [s.session_id for s in ss] == ["s2", "s1"]
    s1 = ss[1]
    assert s1.title == "Tiêu đề" and s1.events == 2 and s1.sidechain_tokens == 1_000_000
    turns = a.session_turns("s1")
    assert [t.timestamp for t in turns] == ["2026-11-19T10:00:00Z", "2026-11-19T10:05:00Z"]
    assert turns[1].tool_names == ["Task"] and turns[1].is_sidechain is True
    assert turns[0].cost == pytest.approx(3)


def test_skills_counts_skill_tool_per_day_and_employee(store: Store, pricing: Pricing) -> None:
    put(
        store,
        ev(1, tool_names=("Skill", "Read")),
        ev(2, tool_names=("Skill",), timestamp="2026-11-18T10:00:00Z"),
        ev(3, tool_names=("Read",)),
    )
    sk = agg(store, pricing).skills("7d")
    assert sk.total_invocations == 2
    assert {d.day: d.count for d in sk.daily} == {"2026-11-18": 1, "2026-11-19": 1}
    assert [(e.display_name, e.count) for e in sk.by_employee] == [("a@x.com", 2)]
    assert agg(store, pricing).skills("today").total_invocations == 1


def test_orchestration_main_vs_sidechain(store: Store, pricing: Pricing) -> None:
    put(
        store,
        ev(1, input_tokens=100),
        ev(2, input_tokens=40, is_sidechain=True, model="claude-haiku-4-5"),
        ev(3, input_tokens=5, is_sidechain=True, session_id="s2"),
    )
    o = agg(store, pricing).orchestration("30d")
    assert (o.main_tokens, o.sidechain_tokens) == (100, 45)
    assert {m.model: m.tokens for m in o.sidechain_by_model} == {
        "claude-haiku-4-5": 40,
        "claude-sonnet-4-6": 5,
    }
    assert [(x.session_id, x.sidechain_tokens) for x in o.agents_by_tokens] == [
        ("s1", 40),
        ("s2", 5),
    ]


def test_unicode_titles_roundtrip(store: Store, pricing: Pricing) -> None:
    title = "Phân tích dữ liệu khách hàng ✓"
    put(store, ev(1, session_title=title, project="Dự án Ổn định"))
    a = agg(store, pricing)
    ov = a.overview(Filter(project="Dự án Ổn định"))
    assert ov.recent_sessions[0].title == title
    assert ov.by_project[0].project == "Dự án Ổn định"
    assert json.loads(json.dumps(dataclasses.asdict(ov), ensure_ascii=False))


def _bulk(store: Store, n_events: int, n_devices: int, days: int) -> None:
    """Realistic shape: each device works a few sessions per day, one project
    per session, mostly one model, sub-agents occasionally."""
    rnd = random.Random(42)
    start = datetime(2026, 10, 20, tzinfo=UTC)
    per_dev = n_events // n_devices
    for d in range(n_devices):
        dev = f"dev{d}"
        store.upsert_device(dev, rnd.choice(["Dev", "QA"]), f"user{d}@x.com", NOW, "u", "h")
        projects = [f"proj{d % 7}", f"proj{(d + 3) % 7}", "shared"]
        events: list[UsageEvent] = []
        per_day = per_dev // days
        for day in range(days):
            sess = f"{dev}-s{day}-"
            for i in range(per_day):
                k = i * 3 // per_day  # 3 sessions/day
                ts = start + timedelta(days=day, hours=1 + (i * 9) // per_day, seconds=i)
                side = rnd.random() < 0.2
                events.append(
                    ev(
                        len(events),
                        uuid=f"{dev}-{day}-{i}",
                        message_id=f"{dev}-{day}-{i}",
                        device_id=dev,
                        session_id=sess + str(k),
                        timestamp=ts.isoformat().replace("+00:00", "Z"),
                        model="claude-haiku-4-5" if side else "claude-sonnet-4-6",
                        is_sidechain=side,
                        input_tokens=rnd.randint(1, 5000),
                        output_tokens=rnd.randint(1, 5000),
                        cache_read_tokens=rnd.randint(0, 50000),
                        project=projects[k],
                        tool_names=("Read", "Bash") if i % 2 else ("Edit",),
                        claude_account_email=f"user{d}@x.com",
                    )
                )
        store.upsert_events(dev, events)


def test_overview_query_fast_on_200k_rows(store: Store, pricing: Pricing) -> None:
    t0 = time.perf_counter()
    _bulk(store, 200_000, 20, 30)
    insert_s = time.perf_counter() - t0
    (n,) = store._fetchall("SELECT COUNT(*) FROM events")[0]
    assert n >= 199_000
    assert insert_s < 120, f"bulk insert took {insert_s:.1f}s"
    a = agg(store, pricing)
    best = float("inf")
    for _ in range(3):
        t = time.perf_counter()
        ov = a.overview(Filter())
        best = min(best, time.perf_counter() - t)
    assert ov.totals.events == n
    print(f"[perf] 200k overview best-of-3: {best * 1000:.0f} ms")
    assert best < 0.300, f"overview took {best * 1000:.0f} ms"


def test_day_mixing_priced_and_unpriced_models_is_flagged(store: Store, pricing: Pricing) -> None:
    put(
        store,
        ev(1, timestamp="2026-11-18T10:00:00Z"),
        ev(2, timestamp="2026-11-19T10:00:00Z"),
        ev(3, timestamp="2026-11-19T10:30:00Z", model="claude-mystery-9"),
    )
    ov = agg(store, pricing).overview(Filter())
    days = {d.day: d for d in ov.daily}
    assert days["2026-11-18"].cost_partial is False
    assert days["2026-11-18"].unknown_cost_models == []
    assert days["2026-11-19"].cost == pytest.approx(3)
    assert days["2026-11-19"].cost_partial is True
    assert days["2026-11-19"].unknown_cost_models == ["claude-mystery-9"]
    hour = next(h for h in ov.hourly if h.total_tokens)
    assert hour.cost_partial is True and hour.unknown_cost_models == ["claude-mystery-9"]


def test_cost_none_when_no_tokens_are_priced(store: Store, pricing: Pricing) -> None:
    # a priced model with zero tokens must not turn UNKNOWN into 0.0
    put(store, ev(1, input_tokens=0), ev(2, model="claude-mystery-9"))
    ov = agg(store, pricing).overview(Filter())
    assert ov.totals.cost is None and ov.totals.cost_partial is True
    assert ov.daily[0].cost is None
    empty = agg(store, pricing).overview(Filter(project="nothing"))
    assert empty.totals.cost == 0.0 and empty.totals.cost_partial is False


def test_missing_model_uses_no_model_label(store: Store, pricing: Pricing) -> None:
    put(store, ev(1, model=None, is_sidechain=True), ev(2, model=""))
    a = agg(store, pricing)
    ov = a.overview(Filter())
    assert [m.model for m in ov.by_model] == [NO_MODEL]
    assert ov.unknown_cost_models == [NO_MODEL]
    assert a.sessions(Filter())[0].models == [NO_MODEL]
    assert a.session_turns("s1")[0].model == NO_MODEL
    assert a.orchestration("all").sidechain_by_model[0].model == NO_MODEL
    assert a.overview(Filter(model=NO_MODEL)).totals.events == 2


def test_unknown_account_stays_unknown_after_device_reports_email(
    store: Store, pricing: Pricing
) -> None:
    store.upsert_device("dev3", "Dev", None, NOW, "carol", "PC3")
    put(store, ev(1, device_id="dev3", claude_account_email=None))
    store.upsert_device("dev3", "Dev", "carol@x.com", NOW)
    put(store, ev(2, device_id="dev3", claude_account_email=None))
    a = agg(store, pricing)
    rows = {r.key: r.total_tokens for r in a.employees(Filter())}
    assert rows == {"device:dev3": 1_000_000, "carol@x.com": 1_000_000}
    assert a.employee("device:dev3", "all").display_name == "Unknown account (carol@PC3)"
    assert a.employee("device:dev3", "all").totals.input_tokens == 1_000_000


def test_second_person_on_same_machine_does_not_take_over_history(
    store: Store, pricing: Pricing
) -> None:
    store.upsert_device("pc", "Dev", "first@x.com", NOW, "shared", "PC")
    put(store, ev(1, device_id="pc", claude_account_email="first@x.com"))
    put(store, ev(2, device_id="pc", claude_account_email=None))  # resolved to first@
    store.upsert_device("pc", "Dev", "second@x.com", NOW)
    put(store, ev(3, device_id="pc", claude_account_email="second@x.com", input_tokens=7))
    a = agg(store, pricing)
    rows = {r.key: r.input_tokens for r in a.employees(Filter())}
    assert rows == {"first@x.com": 2_000_000, "second@x.com": 7}
    assert a.employee("first@x.com", "all").totals.input_tokens == 2_000_000
    assert a.employee("second@x.com", "all").totals.input_tokens == 7


def test_only_session_turns_reads_raw_events(store: Store, pricing: Pricing) -> None:
    put(store, ev(1, tool_names=("Skill",), is_sidechain=True), ev(2, session_title="t"))
    store.upsert_device("dev1", "Dev", "a@x.com", NOW, "alice", "PC1")
    read: set[str] = set()

    def auth(action: int, arg1: str | None, *_: object) -> int:
        if action == sqlite3.SQLITE_READ and arg1:
            read.add(arg1)
        return sqlite3.SQLITE_OK

    store._rcon.set_authorizer(auth)
    a = agg(store, pricing)
    calls = {
        "overview": lambda: a.overview(Filter(team="Dev", start="2026-11-01")),
        "employees": lambda: a.employees(Filter()),
        "employee": lambda: a.employee("a@x.com", "7d"),
        "projects": lambda: a.projects(Filter()),
        "sessions": lambda: a.sessions(Filter()),
        "skills": lambda: a.skills("all"),
        "orchestration": lambda: a.orchestration("30d"),
    }
    for name, call in calls.items():
        read.clear()
        call()
        assert read and "events" not in read, (name, read)
    read.clear()
    a.session_turns("s1")
    assert "events" in read


def test_reader_during_chunked_ingest_is_fast_and_consistent(
    tmp_path: Path, pricing: Pricing
) -> None:
    store = Store(tmp_path / "c.db", chunk_rows=2000)
    base = datetime(2026, 11, 19, 0, 0, tzinfo=UTC)  # inside the 24 h hourly window
    events = [
        ev(
            i,
            timestamp=(base + timedelta(seconds=i % 40_000)).isoformat(),
            project=f"p{i % 5}",
            session_id=f"s{i % 50}",
            input_tokens=1 + i % 7,
        )
        for i in range(60_000)
    ]
    done = threading.Event()

    def ingest() -> None:
        store.upsert_events("dev1", events)
        done.set()

    a = agg(store, pricing)
    t = threading.Thread(target=ingest)
    t.start()
    worst, seen_partial, reads = 0.0, False, 0
    while not done.is_set():
        t0 = time.perf_counter()
        ov = a.overview(Filter())
        worst = max(worst, time.perf_counter() - t0)
        reads += 1
        total = ov.totals.total_tokens
        # by_project/totals and hourly come from different queries:
        # they agree only if all queries ran in one snapshot
        assert total == sum(p.total_tokens for p in ov.by_project)
        assert total == sum(h.total_tokens for h in ov.hourly)
        assert ov.totals.events == sum(p.events for p in ov.by_project)
        seen_partial |= 0 < ov.totals.events < len(events)
    t.join()
    assert reads >= 2 and seen_partial, (reads, seen_partial)
    assert worst < 0.300, f"reader blocked {worst * 1000:.0f} ms"
    assert a.overview(Filter()).totals.events == len(events)
    store.close()


def _bulk_year(store: Store, n_devices: int, days: int) -> None:
    """Long-range data shape (not tidy per hour): every device is active
    9 hours/day and runs 4 sessions/day, each session on one of 12 rotating
    projects; per active hour 2 main-thread responses with a random model
    (sonnet or opus) plus 1 sidechain (haiku) response, each with 1-3 random
    tools out of 8. That is 27 events per device-day: for 50 devices x 365
    days ~493k events, ~380k rollup_hour rows, ~1M rollup_tool_hour rows and
    ~220k rollup_session rows."""
    rnd = random.Random(7)
    start = datetime(2025, 11, 19, tzinfo=UTC)
    tools = ["Read", "Edit", "Bash", "Grep", "Glob", "Task", "Skill", "Write"]
    for d in range(n_devices):
        dev = f"dev{d}"
        store.upsert_device(dev, ["Dev", "QA", "Ops"][d % 3], f"u{d}@x.com", NOW, "u", "h")
        events: list[UsageEvent] = []
        for day in range(days):
            for h in range(9):
                k = h * 4 // 9
                ts0 = start + timedelta(days=day, hours=1 + h)
                for j in range(3):
                    side = j == 2
                    model = (
                        "claude-haiku-4-5"
                        if side
                        else rnd.choice(["claude-sonnet-4-6", "claude-opus-4-7"])
                    )
                    mid = f"{dev}-{day}-{h}-{j}"
                    events.append(
                        ev(
                            0,
                            uuid=mid,
                            message_id=mid,
                            device_id=dev,
                            session_id=f"{dev}-{day}-{k}",
                            timestamp=(ts0 + timedelta(minutes=j * 13)).isoformat(),
                            model=model,
                            is_sidechain=side,
                            input_tokens=rnd.randint(1, 5000),
                            output_tokens=rnd.randint(1, 5000),
                            cache_read_tokens=rnd.randint(0, 50000),
                            project=f"proj{(d + day + k) % 12}",
                            tool_names=tuple(rnd.sample(tools, rnd.randint(1, 3))),
                            claude_account_email=f"u{d}@x.com",
                        )
                    )
        store.upsert_events(dev, events)


def test_overview_fast_on_50_devices_365_days(tmp_path: Path, pricing: Pricing) -> None:
    """A year of 50 devices; the Manager runs at UTC+7 so the store keeps its
    local day/month rollups for that offset and long ranges read months/days."""
    store = Store(tmp_path / "year.db", tz_offset_minutes=VN)
    _bulk_year(store, 50, 365)
    (n_roll,) = store._fetchall("SELECT COUNT(*) FROM rollup_hour")[0]
    a = agg(store, pricing, tz=VN)
    best = float("inf")
    for _ in range(3):
        t = time.perf_counter()
        ov = a.overview(Filter())
        best = min(best, time.perf_counter() - t)
    assert ov.totals.events == 50 * 365 * 27
    assert len(ov.daily) == 90 and len(ov.monthly) == 13
    assert sum(m.total_tokens for m in ov.monthly) == ov.totals.total_tokens
    store.close()
    print(f"[perf] 50x365 overview best-of-3: {best * 1000:.0f} ms over {n_roll} rollup_hour rows")
    assert best < 0.500, f"overview took {best * 1000:.0f} ms over {n_roll} rollup rows"


def _random_events(seed: int, n: int) -> list[UsageEvent]:
    rnd = random.Random(seed)
    start = datetime(2026, 8, 25, tzinfo=UTC)
    out = []
    for i in range(n):
        ts = start + timedelta(minutes=rnd.randint(0, 90 * 24 * 60))
        out.append(
            ev(
                i,
                device_id=rnd.choice(["d1", "d2", "d3"]),
                session_id=f"s{rnd.randint(0, 40)}",
                timestamp=ts.isoformat(),
                model=rnd.choice(["claude-sonnet-4-6", "claude-mystery", None]),
                is_sidechain=rnd.random() < 0.3,
                input_tokens=rnd.randint(0, 900),
                output_tokens=rnd.randint(0, 900),
                cache_read_tokens=rnd.randint(0, 900),
                project=rnd.choice(["p1", "p2", "p3"]),
                tool_names=tuple(rnd.sample(["Read", "Skill", "Bash", "Task"], 2)),
                claude_account_email=rnd.choice(["a@x", "b@x", None]),
            )
        )
    return out


@pytest.mark.parametrize("tz", [VN, 0, -330, 345])
def test_month_day_hour_segments_equal_hourly_scan(
    tmp_path: Path, pricing: Pricing, tz: int
) -> None:
    """The coarse (month/day/hour) path must give exactly the all-hourly answer."""
    fast = Store(tmp_path / "fast.db", tz_offset_minutes=tz)
    slow = Store(tmp_path / "slow.db", tz_offset_minutes=tz + 60)  # offsets differ: hourly only
    events = _random_events(tz, 3000)
    for st in (fast, slow):
        for dev in ("d1", "d2", "d3"):
            st.set_device_team(dev, "QA" if dev == "d2" else "Dev")
            st.upsert_events(dev, [e for e in events if e.device_id == dev])
    fa, sa = agg(fast, pricing, tz=tz), agg(slow, pricing, tz=tz)
    assert fa.ctx.local_tables and not sa.ctx.local_tables
    rnd = random.Random(tz)
    filters = [Filter(), Filter(team="QA"), Filter(email="a@x", project="p2")]
    for _ in range(12):
        a_day = date(2026, 8, 20) + timedelta(days=rnd.randint(0, 100))
        b_day = a_day + timedelta(days=rnd.randint(0, 70))
        filters.append(Filter(start=a_day.isoformat(), end=b_day.isoformat()))
        filters.append(
            Filter(
                model=rnd.choice([None, "claude-mystery", NO_MODEL]),
                start=f"{a_day.isoformat()}T{rnd.randint(0, 23):02d}:00",
                end=f"{b_day.isoformat()}T{rnd.randint(0, 23):02d}:00",
            )
        )
    for f in filters:
        assert dataclasses.asdict(fa.overview(f)) == dataclasses.asdict(sa.overview(f)), f
        assert fa.employees(f) == sa.employees(f), f
    for rng in ("today", "7d", "30d", "90d", "all"):
        assert fa.skills(rng) == sa.skills(rng)
        assert fa.orchestration(rng) == sa.orchestration(rng)
        assert fa.employee("a@x", rng) == sa.employee("a@x", rng)
    fast.close()
    slow.close()


def test_long_range_daily_capped_monthly_covers_all(store: Store, pricing: Pricing) -> None:
    put(
        store,
        ev(1, timestamp="2026-01-15T10:00:00Z"),
        ev(2, timestamp="2026-11-19T10:00:00Z", model="claude-mystery-9"),
    )
    ov = agg(store, pricing).overview(Filter())
    assert len(ov.daily) == 90 and ov.daily[-1].day == "2026-11-19"
    assert [m.month for m in ov.monthly][:1] == ["2026-01"] and len(ov.monthly) == 11
    jan, nov = ov.monthly[0], ov.monthly[-1]
    assert jan.cost == pytest.approx(3) and not jan.cost_partial
    assert nov.cost is None and nov.unknown_cost_models == ["claude-mystery-9"]
    assert sum(m.input_tokens for m in ov.monthly) == ov.totals.input_tokens


def test_store_offset_change_rebuilds_local_rollups(tmp_path: Path, pricing: Pricing) -> None:
    events = _random_events(1, 500)
    s = Store(tmp_path / "a.db", tz_offset_minutes=0)
    for dev in ("d1", "d2", "d3"):
        s.upsert_events(dev, [e for e in events if e.device_id == dev])
    s.close()
    moved = Store(tmp_path / "a.db", tz_offset_minutes=VN)  # Manager moved to UTC+7
    fresh = Store(tmp_path / "b.db", tz_offset_minutes=VN)
    for dev in ("d1", "d2", "d3"):
        fresh.upsert_events(dev, [e for e in events if e.device_id == dev])
    for table in ("rollup_day", "rollup_month", "rollup_tool_day", "rollup_tool_month"):
        q = f"SELECT * FROM {table} ORDER BY 1, 2, 3, 4, 5, 6"
        assert [tuple(r) for r in moved._fetchall(q)] == [tuple(r) for r in fresh._fetchall(q)]
    moved.close()
    fresh.close()
