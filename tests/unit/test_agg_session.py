"""Aggregates.session(): one session header, same shape as a sessions() row."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tokenvault.common.models import UsageEvent
from tokenvault.common.pricing import Pricing
from tokenvault.manager.aggregates import Aggregates, Filter
from tokenvault.manager.store import Store

PRICING = {
    "models": {
        "claude-sonnet-4-6": {
            "input": 3.0,
            "output": 15.0,
            "cache_read": 0.3,
            "cache_create_5m": 3.75,
            "cache_create_1h": 6.0,
        }
    }
}
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC).timestamp()


def ev(n: int, **kw: object) -> UsageEvent:
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
def agg(tmp_path: Path) -> Iterator[Aggregates]:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    store = Store(tmp_path / "m.db", tz_offset_minutes=420)
    events = [
        ev(1, session_title="Tiêu đề ✓"),
        ev(2, timestamp="2026-11-19T10:05:00Z", is_sidechain=True, project="other"),
        ev(3, timestamp="2026-11-19T10:06:00Z", model="mystery-model", input_tokens=5),
        ev(4, session_id="s2", timestamp="2026-11-19T11:00:00Z"),
        ev(5, session_id="s3", device_id="dev9", claude_account_email=None,
           windows_user="bob", hostname="PC9", timestamp="2026-11-18T09:00:00Z"),
    ]
    for e in events:
        store.upsert_events(e.device_id, [e])
    store.upsert_device("dev9", "QA", None, NOW, "bob", "PC9")
    yield Aggregates(store, Pricing.load(p), tz_offset_minutes=420, now=lambda: NOW)
    store.close()


def test_session_matches_sessions_row(agg: Aggregates) -> None:
    rows = {r.session_id: r for r in agg.sessions(Filter())}
    for sid in ("s1", "s2", "s3"):
        one = agg.session(sid)
        assert one is not None
        assert dataclasses.asdict(one) == dataclasses.asdict(rows[sid])


def test_session_fields(agg: Aggregates) -> None:
    s1 = agg.session("s1")
    assert s1 is not None
    assert s1.title == "Tiêu đề ✓" and s1.events == 3
    assert s1.sidechain_tokens == 1_000_000
    assert s1.project in ("proj", "other") and s1.projects == ["other", "proj"]
    assert s1.cost_partial is True and s1.cost == pytest.approx(6.0)
    assert s1.started_at.startswith("2026-11-19T17:00:00")  # UTC+7
    s3 = agg.session("s3")
    assert s3 is not None and s3.employee_key == "device:dev9"
    assert s3.display_name == "Unknown account (bob@PC9)"


def test_session_unknown_is_none(agg: Aggregates) -> None:
    assert agg.session("nope") is None
    assert agg.session("") is None


def test_session_repeated_id_picks_most_recent_device(agg: Aggregates) -> None:
    agg.store.upsert_events("dev2", [ev(9, device_id="dev2", timestamp="2026-11-19T11:30:00Z")])
    s1 = agg.session("s1")
    assert s1 is not None and s1.device_id == "dev2"
