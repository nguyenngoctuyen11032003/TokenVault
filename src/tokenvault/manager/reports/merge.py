"""Merge Aggregates results of several scope parts (one per selected employee).

Cost semantics follow ``agg_models``: ``cost`` sums priced tokens, ``None`` when there
are tokens but none priced, ``cost_partial`` when some tokens are unpriced.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from tokenvault.manager.agg_models import (
    DayPoint,
    HourPoint,
    ModelRow,
    ProjectRow,
    Tokens,
    ToolCount,
    Totals,
)

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_create_5m_tokens",
    "cache_create_1h_tokens",
    "total_tokens",
)


def tokens(items: Iterable[Tokens]) -> dict[str, int]:
    out = dict.fromkeys(TOKEN_FIELDS, 0)
    for it in items:
        for f in TOKEN_FIELDS:
            out[f] += getattr(it, f)
    return out


def cost(items: Sequence[Any]) -> tuple[float | None, bool]:
    """(cost, partial) of items having ``cost``, ``total_tokens`` and maybe ``cost_partial``."""
    known = [i.cost for i in items if i.cost is not None]
    unpriced = any(i.cost is None and i.total_tokens > 0 for i in items)
    partial = unpriced or any(getattr(i, "cost_partial", False) for i in items)
    if not known:
        return (None if unpriced else 0.0), partial
    return round(sum(known), 6), partial


def union(lists: Iterable[Iterable[str]]) -> list[str]:
    return sorted({m for lst in lists for m in lst})


def totals(parts: Sequence[Totals]) -> Totals:
    if len(parts) == 1:
        return parts[0]
    c, partial = cost(parts)
    return Totals(
        **tokens(parts),
        events=sum(p.events for p in parts),
        sessions=sum(p.sessions for p in parts),
        cost=c,
        cost_partial=partial,
    )


def days(parts: Sequence[list[DayPoint]]) -> list[DayPoint]:
    """Same-length zero-filled day lists -> one list (element-wise sum)."""
    if len(parts) == 1:
        return parts[0]
    out = []
    for group in zip(*parts, strict=True):
        c, partial = cost(group)
        out.append(
            DayPoint(
                **tokens(group),
                day=group[0].day,
                cost=c,
                cost_partial=partial,
                unknown_cost_models=union(g.unknown_cost_models for g in group),
            )
        )
    return out


def hours(parts: Sequence[list[HourPoint]]) -> list[HourPoint]:
    if len(parts) == 1:
        return parts[0]
    by: dict[str, list[HourPoint]] = {}
    for p in parts:
        for h in p:
            by.setdefault(h.hour, []).append(h)
    out = []
    for hour in sorted(by):
        group = by[hour]
        c, partial = cost(group)
        out.append(
            HourPoint(
                **tokens(group),
                hour=hour,
                cost=c,
                cost_partial=partial,
                unknown_cost_models=union(g.unknown_cost_models for g in group),
            )
        )
    return out


def projects(parts: Sequence[list[ProjectRow]]) -> list[ProjectRow]:
    if len(parts) == 1:
        return parts[0]
    by: dict[str, list[ProjectRow]] = {}
    for p in parts:
        for r in p:
            by.setdefault(r.project, []).append(r)
    out = []
    for name, group in by.items():
        c, partial = cost(group)
        last = [g.last_active for g in group if g.last_active]
        out.append(
            ProjectRow(
                **tokens(group),
                project=name,
                events=sum(g.events for g in group),
                sessions=sum(g.sessions for g in group),
                employees=sum(g.employees for g in group),
                cost=c,
                cost_partial=partial,
                unknown_cost_models=union(g.unknown_cost_models for g in group),
                last_active=max(last) if last else None,
            )
        )
    return sorted(out, key=lambda r: (-r.total_tokens, r.project))


def models(parts: Sequence[list[ModelRow]]) -> list[ModelRow]:
    if len(parts) == 1:
        return parts[0]
    by: dict[str, list[ModelRow]] = {}
    for p in parts:
        for r in p:
            by.setdefault(r.model, []).append(r)
    out = []
    for name, group in by.items():
        c, _ = cost(group)
        out.append(
            ModelRow(**tokens(group), model=name, events=sum(g.events for g in group), cost=c)
        )
    return sorted(out, key=lambda r: (-r.total_tokens, r.model))


def tools(parts: Sequence[list[ToolCount]]) -> list[ToolCount]:
    if len(parts) == 1:
        return parts[0]
    by: dict[str, int] = {}
    for p in parts:
        for t in p:
            by[t.tool] = by.get(t.tool, 0) + t.calls
    ranked = sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))
    return [ToolCount(t, n) for t, n in ranked]
