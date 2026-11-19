"""Extra coverage for Aggregates.skills() / orchestration() (Task 16), real Store, no mocks."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tokenvault.common.models import UsageEvent
from tokenvault.common.pricing import Pricing
from tokenvault.manager.aggregates import Aggregates
from tokenvault.manager.store import Store

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC).timestamp()  # 19:00 in Vietnam (UTC+7)
PRICING = {"_comment": "t", "models": {}}


def ev(n: int, **kw: object) -> UsageEvent:
    base = UsageEvent(
        uuid=f"u{n}",
        session_id="s1",
        message_id=f"msg_{n}",
        timestamp="2026-11-19T10:00:00Z",
        model="claude-sonnet-4-6",
        reasoning_effort=None,
        is_sidechain=False,
        input_tokens=10,
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
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "m.db")
    yield s
    s.close()


@pytest.fixture
def agg(store: Store, tmp_path: Path) -> Aggregates:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return Aggregates(store, Pricing.load(p), tz_offset_minutes=7 * 60, now=lambda: NOW)


def put(store: Store, *events: UsageEvent) -> None:
    for e in events:
        store.upsert_events(e.device_id, [e])


def test_skills_counts_tool_use_named_Skill_only(store: Store, agg: Aggregates) -> None:
    put(
        store,
        ev(1, tool_names=("Skill",)),
        ev(2, tool_names=("Read", "Skill")),
        ev(3, tool_names=("skill",)),  # wrong case: a different tool
        ev(4, tool_names=("Skills", "SkillTool", "Task")),  # prefixes are not "Skill"
        ev(5, tool_names=()),
        ev(6, tool_names=("Bash",), timestamp="2026-11-18T10:00:00Z"),
        ev(
            7,
            tool_names=("Skill",),
            timestamp="2026-11-18T10:00:00Z",
            windows_user="bob",
            claude_account_email="b@x.com",
            account_uuid="accb",
            device_id="dev2",
        ),
    )
    view = agg.skills("all")
    assert view.total_invocations == 3
    assert {d.day: d.count for d in view.daily} == {"2026-11-19": 2, "2026-11-18": 1}
    assert [d.day for d in view.daily] == sorted(d.day for d in view.daily)
    assert {e.key: e.count for e in view.by_employee} == {"a@x.com": 2, "b@x.com": 1}
    assert view.by_employee[0].key == "a@x.com"  # ranked by count
    assert "Skill" in view.note
    # The window applies: only today within "7d" is still both days, a far-old event is not.
    put(store, ev(8, tool_names=("Skill",), timestamp="2026-06-01T10:00:00Z"))
    assert agg.skills("30d").total_invocations == 3
    assert agg.skills("all").total_invocations == 4
    assert agg.skills("7d").total_invocations == 3


def test_skills_without_any_skill_tool_is_empty(store: Store, agg: Aggregates) -> None:
    put(store, ev(1, tool_names=("Read",)))
    view = agg.skills("30d")
    assert (view.total_invocations, view.daily, view.by_employee) == (0, [], [])


def test_skills_rejects_unknown_range(agg: Aggregates) -> None:
    with pytest.raises(ValueError):
        agg.skills("12d")


def test_orchestration_splits_sidechain_tokens(store: Store, agg: Aggregates) -> None:
    put(
        store,
        ev(1, input_tokens=100, cache_read_tokens=50),  # main 150
        ev(
            2,
            input_tokens=20,
            output_tokens=5,
            is_sidechain=True,
            model="claude-haiku-4-5",
            cache_create_1h_tokens=5,
        ),  # side 30
        ev(3, input_tokens=70, is_sidechain=True, session_id="s2", session_title="Big"),
        ev(4, input_tokens=1, is_sidechain=True, session_id="s3", timestamp="2026-06-01T10:00:00Z"),
        ev(5, input_tokens=9, session_id="s3", timestamp="2026-06-01T10:00:00Z"),
    )
    o = agg.orchestration("30d")
    assert (o.main_tokens, o.sidechain_tokens) == (150, 100)
    assert {m.model: m.tokens for m in o.main_by_model} == {"claude-sonnet-4-6": 150}
    assert {m.model: m.tokens for m in o.sidechain_by_model} == {
        "claude-sonnet-4-6": 70,
        "claude-haiku-4-5": 30,
    }
    assert [m.tokens for m in o.sidechain_by_model] == [70, 30]  # biggest first
    ranked = [(a.session_id, a.sidechain_tokens) for a in o.agents_by_tokens]
    assert ranked == [("s2", 70), ("s1", 30)]
    assert o.agents_by_tokens[0].title == "Big"
    # "all" also sees the old session.
    a = agg.orchestration("all")
    assert (a.main_tokens, a.sidechain_tokens) == (159, 101)


def test_orchestration_without_sidechain_has_no_agents(store: Store, agg: Aggregates) -> None:
    put(store, ev(1, input_tokens=40), ev(2, input_tokens=2, session_id="s9"))
    o = agg.orchestration("7d")
    assert (o.main_tokens, o.sidechain_tokens) == (42, 0)
    assert o.sidechain_by_model == [] and o.agents_by_tokens == []
