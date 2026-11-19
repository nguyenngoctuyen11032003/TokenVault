from __future__ import annotations

import dataclasses
import json
import random
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tokenvault.common.models import UsageEvent
from tokenvault.common.pricing import Pricing
from tokenvault.manager.aggregates import Aggregates, Filter
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
    assert sorted(ov.unknown_cost_models) == ["", "claude-mystery-9"]
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
    (n,) = store.fetchall("SELECT COUNT(*) FROM events")[0]
    assert n >= 199_000
    assert insert_s < 120, f"bulk insert took {insert_s:.1f}s"
    a = agg(store, pricing)
    best = float("inf")
    for _ in range(3):
        t = time.perf_counter()
        ov = a.overview(Filter())
        best = min(best, time.perf_counter() - t)
    assert ov.totals.events == n
    assert best < 0.300, f"overview took {best * 1000:.0f} ms"
