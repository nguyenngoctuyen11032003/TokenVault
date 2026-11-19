from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from report_testkit import NOW_DT, VN, demo_dataset, email_of, ev, make_agg, make_store, put
from report_testkit import write_pricing as pricing_at

from tokenvault.common.pricing import Pricing
from tokenvault.manager.aggregates import Aggregates, Filter
from tokenvault.manager.reports.data import gather
from tokenvault.manager.reports.errors import ReportEmptyScopeError
from tokenvault.manager.reports.spec import ReportSpec
from tokenvault.manager.store import Store

LOCAL_NOW = datetime(2026, 11, 19, 19, 0)  # naive = Manager local time (UTC+7)


@pytest.fixture
def pricing(tmp_path: Path) -> Pricing:
    return pricing_at(tmp_path)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = make_store(tmp_path)
    yield s
    s.close()


def test_totals_equal_aggregates(store: Store, pricing: Pricing) -> None:
    demo_dataset(store, employees=5, days=20)
    a = make_agg(store, pricing)
    data = gather(a, ReportSpec(range="7d").validate(), LOCAL_NOW)
    ov = a.overview(Filter(start="2026-11-13", end="2026-11-19"))
    assert data.totals.total_tokens == ov.totals.total_tokens > 0
    assert data.totals.input_tokens == ov.totals.input_tokens
    assert data.totals.cache_read_tokens == ov.totals.cache_read_tokens
    assert data.totals.cost == pytest.approx(ov.totals.cost)
    assert data.totals.sessions == ov.totals.sessions
    assert sum(d.total_tokens for d in data.daily) == ov.totals.total_tokens
    assert [p.project for p in data.by_project] == [p.project for p in ov.by_project]
    assert data.active_employees == 5 and not data.is_empty
    assert data.scope_label == "Toàn bộ nhân viên"


def test_daily_zero_filled_from_server_days_under_offset(store: Store, pricing: Pricing) -> None:
    store.upsert_device("dev1", "Dev", "a@x.com", 0, "alice", "PC1")
    put(
        store,
        ev(1, timestamp="2026-11-18T18:30:00Z"),  # 01:30 on the 19th in UTC+7
        ev(2, timestamp="2026-11-16T03:00:00Z"),
        ev(3, timestamp="2026-11-03T10:00:00Z"),
    )
    a = make_agg(store, pricing)
    today = gather(a, ReportSpec(range="today"), LOCAL_NOW)
    assert [d.day for d in today.daily] == ["2026-11-19"]
    assert today.daily[0].input_tokens == 1_000_000
    week = gather(a, ReportSpec(range="7d"), LOCAL_NOW)
    assert [d.day for d in week.daily] == [f"2026-11-{d}" for d in range(13, 20)]
    assert [d.total_tokens > 0 for d in week.daily] == [0, 0, 0, 1, 0, 0, 1]
    # aware 'now' is converted to the Aggregates' offset (00:30 UTC on the 19th = 07:30 local)
    utc_now = datetime(2026, 11, 19, 0, 30, tzinfo=NOW_DT.tzinfo)
    aware = gather(a, ReportSpec(range="today"), utc_now)
    assert aware.first_day == "2026-11-19"
    custom = gather(a, ReportSpec(range="custom", start="2026-11-01", end="2026-11-05"), LOCAL_NOW)
    assert [d.day for d in custom.daily] == [f"2026-11-0{d}" for d in range(1, 6)]
    assert custom.daily[2].total_tokens == 1_000_000 and custom.busiest_day is not None
    assert custom.busiest_day.day == "2026-11-03"
    assert len(custom.hourly_busiest) == 24
    assert sum(h.total_tokens for h in custom.hourly_busiest) == 1_000_000
    assert custom.range_label == "01/11/2026 – 05/11/2026"


def test_long_custom_range_has_every_day(store: Store, pricing: Pricing) -> None:
    put(store, ev(1, timestamp="2026-01-02T10:00:00Z"), ev(2, timestamp="2026-11-10T10:00:00Z"))
    a = make_agg(store, pricing)
    spec = ReportSpec(range="custom", start="2026-01-01", end="2026-12-31")
    data = gather(a, spec, LOCAL_NOW)
    assert len(data.daily) == 365 and data.daily[0].day == "2026-01-01"
    assert data.daily[1].total_tokens == 1_000_000 and data.daily[313].total_tokens == 1_000_000


def test_unknown_cost_models_propagate(store: Store, pricing: Pricing) -> None:
    put(
        store,
        ev(1),
        ev(2, model="claude-mystery-9"),
        ev(3, model=None, device_id="dev2", claude_account_email="b@x.com"),
    )
    a = make_agg(store, pricing)
    data = gather(a, ReportSpec(range="today", per_employee_pages=True), LOCAL_NOW)
    assert data.totals.cost_partial is True and data.totals.cost == pytest.approx(3)
    assert data.unknown_cost_models == ["(no model)", "claude-mystery-9"]
    only_unknown = gather(
        a, ReportSpec(scope="employee", employee_keys=["b@x.com"], range="today"), LOCAL_NOW
    )
    assert only_unknown.totals.cost is None and only_unknown.unknown_cost_models == ["(no model)"]
    sec = {s.row.key: s for s in data.sections}
    assert sec["a@x.com"].view.unknown_cost_models == ["claude-mystery-9"]


def test_employee_ranking_shares_and_cap(store: Store, pricing: Pricing) -> None:
    demo_dataset(store, employees=65, days=3, events_per_day=1)
    a = make_agg(store, pricing)
    data = gather(a, ReportSpec(range="7d", per_employee_pages=True), LOCAL_NOW)
    assert len(data.employees) == 60 and data.employees_more == 5
    assert sum(r.share for r in data.ranking_all) == pytest.approx(1.0, abs=1e-3)
    toks = [r.total_tokens for r in data.employees]
    assert toks == sorted(toks, reverse=True)
    assert len(data.sections) == 60
    assert [s.row.key for s in data.sections] == [r.key for r in data.employees]
    assert len(data.stacked) == 9 and data.stacked[-1][0] == "Khác"
    for i, day in enumerate(data.daily):
        assert sum(vals[i] for _, vals in data.stacked) == day.total_tokens


def test_multi_employee_scope_sums_parts(store: Store, pricing: Pricing) -> None:
    demo_dataset(store, employees=4, days=10)
    a = make_agg(store, pricing)
    keys = [email_of(0), email_of(2)]
    data = gather(a, ReportSpec(scope="employee", employee_keys=keys, range="7d"), LOCAL_NOW)
    parts = [a.overview(Filter(email=k, start="2026-11-13", end="2026-11-19")) for k in keys]
    assert data.totals.total_tokens == sum(p.totals.total_tokens for p in parts)
    assert data.totals.sessions == sum(p.totals.sessions for p in parts)
    assert data.totals.cost == pytest.approx(sum(p.totals.cost or 0 for p in parts))
    tokens_of = {k: p.totals.total_tokens for k, p in zip(keys, parts, strict=True)}
    assert [s.row.key for s in data.sections] == sorted(keys, key=lambda k: -tokens_of[k])
    assert {r.key for r in data.employees} == set(keys)
    assert sum(r.share for r in data.employees) == pytest.approx(1.0)
    assert data.top_sessions and all(s.employee_key in keys for s in data.top_sessions)
    assert data.sections[0].devices and data.sections[0].devices[0].hostname.startswith("PC-")


def test_team_scope(store: Store, pricing: Pricing) -> None:
    demo_dataset(store, employees=8, days=5)
    a = make_agg(store, pricing)
    data = gather(a, ReportSpec(scope="team", team="QA", range="7d"), LOCAL_NOW)
    ov = a.overview(Filter(team="QA", start="2026-11-13", end="2026-11-19"))
    assert data.totals.total_tokens == ov.totals.total_tokens > 0
    assert all("QA" in r.teams for r in data.employees)
    assert data.scope_label == "Nhóm QA"


def test_unknown_key_or_team_raises(store: Store, pricing: Pricing) -> None:
    put(store, ev(1))
    a = make_agg(store, pricing)
    with pytest.raises(ReportEmptyScopeError):
        gather(a, ReportSpec(scope="employee", employee_keys=["nobody@x.com"]), LOCAL_NOW)
    with pytest.raises(ReportEmptyScopeError):
        gather(a, ReportSpec(scope="team", team="Không tồn tại"), LOCAL_NOW)


def test_existing_but_empty_scope(store: Store, pricing: Pricing) -> None:
    store.upsert_device("dev9", "Ops", "idle@x.com", 0, "idle", "PC9")
    put(store, ev(1, timestamp="2026-10-01T10:00:00Z"))  # someone else, long ago
    a: Aggregates = make_agg(store, pricing)
    data = gather(a, ReportSpec(scope="employee", employee_keys=["idle@x.com"]), LOCAL_NOW)
    assert data.is_empty and data.totals.total_tokens == 0 and data.totals.cost == 0
    assert len(data.daily) == 7 and data.busiest_day is None
    assert data.single and data.sections == []  # the summary is the employee; no detail page
    assert [d.hostname for d in data.single_devices] == ["PC9"]
    assert [r.key for r in data.ranking_all] == ["idle@x.com"]
    team = gather(a, ReportSpec(scope="team", team="Ops"), LOCAL_NOW)
    assert team.is_empty and team.scope_label == "Nhóm Ops"
    nothing = gather(a, ReportSpec(range="today"), LOCAL_NOW)
    assert nothing.is_empty and nothing.top_sessions == []


def test_progress_is_monotonic(store: Store, pricing: Pricing) -> None:
    demo_dataset(store, employees=3, days=3)
    seen: list[float] = []
    gather(
        make_agg(store, pricing), ReportSpec(), LOCAL_NOW, progress=lambda f, _msg: seen.append(f)
    )
    assert seen and seen == sorted(seen) and 0 <= seen[0] and seen[-1] <= 1


def test_tz_offset_is_taken_from_aggregates(tmp_path: Path, pricing: Pricing) -> None:
    s = make_store(tmp_path, tz=0)
    put(s, ev(1, timestamp="2026-11-18T18:30:00Z"))
    utc = gather(
        make_agg(s, pricing, tz=0),
        ReportSpec(range="today"),
        datetime(2026, 11, 19, 1, 0, tzinfo=NOW_DT.tzinfo),
    )
    assert utc.first_day == "2026-11-19" and utc.totals.total_tokens == 0
    vn = gather(
        make_agg(s, pricing, tz=VN),
        ReportSpec(range="today"),
        datetime(2026, 11, 19, 1, 0, tzinfo=NOW_DT.tzinfo),
    )
    assert vn.totals.total_tokens == 1_000_000
    s.close()


def test_employee_scope_honours_caps_and_page_opt_out(store: Store, pricing: Pricing) -> None:
    demo_dataset(store, employees=72, days=2, events_per_day=1)
    a = make_agg(store, pricing)
    keys = [email_of(i) for i in range(70)]
    spec = ReportSpec(scope="employee", employee_keys=keys, range="7d")
    with_pages = gather(a, spec, LOCAL_NOW)
    assert len(with_pages.sections) == 60 and len(with_pages.employees) == 60
    assert with_pages.employees_more == 10 and with_pages.parts == 70
    toks = [s.row.total_tokens for s in with_pages.sections]
    assert toks == sorted(toks, reverse=True) and all(toks)
    no_pages = gather(a, replace(spec, per_employee_pages=False), LOCAL_NOW)
    assert no_pages.sections == [] and no_pages.totals == with_pages.totals


def test_session_window_reported_only_when_pool_is_cut(
    store: Store, pricing: Pricing, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenvault.manager.reports import data as data_mod

    demo_dataset(store, employees=2, days=10, session_every=1)
    a = make_agg(store, pricing)
    assert gather(a, ReportSpec(range="7d"), LOCAL_NOW).session_window is None
    monkeypatch.setattr(data_mod, "SESSION_POOL", 5)
    monkeypatch.setattr(data_mod, "MIN_POOL", 5)
    cut = gather(a, ReportSpec(range="7d"), LOCAL_NOW)
    assert cut.session_window == 5 and len(cut.top_sessions) == 5
