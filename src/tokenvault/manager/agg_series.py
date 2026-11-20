"""Series and table builders shared by the aggregate views (rollups only)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tokenvault.manager.agg_models import (
    CacheDayPoint,
    DayPoint,
    Filter,
    HourPoint,
    ModelRow,
    MonthPoint,
    ProjectRow,
    SessionRow,
    ToolCount,
    Totals,
)
from tokenvault.manager.agg_sql import KEY, SUMS, Acc, QueryContext, model_label, tok
from tokenvault.manager.store_core import month_label

DAILY_DAYS = 90  # daily series covers at most the last 90 local days of a range


@dataclass
class Summary:
    total: Acc
    monthly: list[MonthPoint]
    by_project: dict[str, Acc]
    by_model: list[ModelRow]


def totals(a: Acc, sessions: int) -> Totals:
    return Totals(
        **tok(a.t), events=a.events, sessions=sessions, cost=a.cost, cost_partial=a.partial
    )


def summary(ctx: QueryContext, flt: Filter) -> Summary:
    """Totals, monthly series, by_project and by_model over the whole range."""
    rows = ctx.seg_query(
        flt,
        "usage",
        f"{{t}}, r.project, r.model, {SUMS}, SUM(r.events)",
        "{t}, r.project, r.model",
    )
    total = Acc()
    by_month: dict[int, Acc] = defaultdict(Acc)
    by_proj: dict[str, Acc] = defaultdict(Acc)
    by_model: dict[str, Acc] = defaultdict(Acc)
    for grain, (t, project, model, *rest) in rows:
        toks, ev = rest[:5], rest[5]
        month = ctx.month_of(grain, t)
        for acc in (total, by_month[month], by_proj[project], by_model[model]):
            acc.add(ctx.pricing, model, toks, ev)
    monthly = []
    if by_month:
        sh, eh = ctx.hours(flt)
        lo = min(by_month) if sh is None else ctx.month_of("hour", sh)
        hi = max(by_month) if eh is None else ctx.month_of("hour", eh - 1)
        for m in range(lo, hi + 1):
            a = by_month.get(m) or Acc()
            monthly.append(
                MonthPoint(
                    **tok(a.t),
                    month=month_label(m),
                    cost=a.cost,
                    cost_partial=a.partial,
                    unknown_cost_models=a.unknown_models,
                )
            )
    models = [
        ModelRow(**tok(a.t), model=model_label(m), events=a.events, cost=a.cost)
        for m, a in by_model.items()
    ]
    models.sort(key=lambda r: (-r.total_tokens, r.model))
    return Summary(total, monthly, dict(by_proj), models)


def daily(ctx: QueryContext, flt: Filter) -> tuple[list[DayPoint], list[CacheDayPoint]]:
    """Zero-filled local days: the range (or the data span when open) capped to
    its last ``DAILY_DAYS`` days. Older history is in ``Summary.monthly``."""
    sh, eh = ctx.hours(flt)
    first, last = ctx.data_hours()
    if first is None or last is None:
        return [], []
    end_day = ctx.local_day(eh - 1 if eh is not None else last) + 1
    start_day = ctx.local_day(sh if sh is not None else first)
    start_day = max(start_day, end_day - DAILY_DAYS)
    if start_day >= end_day:
        return [], []
    lo = ctx.day_first_hour(start_day)
    if sh is not None:
        lo = max(lo, sh)
    rows = ctx.seg_query(
        flt, "usage", f"{{t}}, r.model, {SUMS}", "{t}, r.model", months=False, bounds=(lo, eh)
    )
    by_day: dict[int, Acc] = defaultdict(Acc)
    for grain, (t, model, *toks) in rows:
        by_day[ctx.day_of(grain, t)].add(ctx.pricing, model, toks)
    days, cache = [], []
    for dn in range(start_day, end_day):
        a = by_day.get(dn) or Acc()
        day = ctx.day_str(dn)
        days.append(
            DayPoint(
                **tok(a.t),
                day=day,
                cost=a.cost,
                cost_partial=a.partial,
                unknown_cost_models=a.unknown_models,
            )
        )
        denom = a.t[0] + a.t[2] + a.t[3] + a.t[4]
        ratio = round(a.t[2] / denom, 4) if denom else 0.0
        cache.append(CacheDayPoint(day, a.t[2], a.t[3], a.t[4], ratio))
    return days, cache


def hourly(ctx: QueryContext, flt: Filter) -> list[HourPoint]:
    """Zero-filled last 24 hours ending at the filter end (or now)."""
    sh, eh = ctx.hours(flt)
    end = eh if eh is not None else int(ctx.now() // 3600) + 1
    start = end - 24 if sh is None else max(sh, end - 24)
    sub = Filter(flt.team, flt.email, flt.project, flt.model)
    src, params = ctx.scope(sub, "rollup_hour", tcol="hour", lo=start, hi=end)
    rows = ctx.q(f"SELECT r.hour, r.model, {SUMS} FROM {src} GROUP BY r.hour, r.model", params)
    got: dict[int, Acc] = defaultdict(Acc)
    for h, model, *toks in rows:
        got[h].add(ctx.pricing, model, toks)
    out = []
    for h in range(start, end):
        a = got.get(h) or Acc()
        label = datetime.fromtimestamp(h * 3600, ctx.tz).strftime("%Y-%m-%dT%H:00")
        out.append(
            HourPoint(
                **tok(a.t),
                hour=label,
                cost=a.cost,
                cost_partial=a.partial,
                unknown_cost_models=a.unknown_models,
            )
        )
    return out


def session_counts(ctx: QueryContext, flt: Filter, group: str) -> dict[Any, int]:
    """Distinct (device, session) per ``group`` expression over rollup_session."""
    src, params = ctx.scope(flt, "rollup_session", session=True)
    rows = ctx.q(
        f"SELECT g, COUNT(*) FROM (SELECT {group} AS g FROM {src} "
        "GROUP BY r.device_id, r.session_id, g) GROUP BY g",
        params,
    )
    return {g: int(n) for g, n in rows}


def session_total(ctx: QueryContext, flt: Filter) -> int:
    """Distinct (device, session) pairs matching the filter (PK-order scan)."""
    src, params = ctx.scope(flt, "rollup_session", session=True)
    rows = ctx.q(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {src} GROUP BY r.device_id, r.session_id)", params
    )
    return int(rows[0][0])


def top_tools(ctx: QueryContext, flt: Filter, limit: int = 15) -> list[ToolCount]:
    counts: dict[str, int] = defaultdict(int)
    for _, (tool, n) in ctx.seg_query(flt, "tool", "r.tool, SUM(r.calls)", "r.tool"):
        counts[tool] += int(n)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [ToolCount(t, n) for t, n in ranked]


def project_rows(ctx: QueryContext, flt: Filter, by_proj: dict[str, Acc]) -> list[ProjectRow]:
    src, params = ctx.scope(flt, "rollup_session", session=True)
    # Group to (project, device, session, key) first - a streaming GROUP BY in the order
    # of ix_rs_project_span - so the DISTINCT counts see sessions, not model rows.
    stats = {
        p: (int(n), int(e), float(last))
        for p, n, e, last in ctx.q(
            "SELECT p, COUNT(DISTINCT dev || char(31) || sid), COUNT(DISTINCT k), MAX(lt) "
            f"FROM (SELECT r.project AS p, r.device_id AS dev, r.session_id AS sid, {KEY} AS k, "
            f"MAX(r.last_ts) AS lt FROM {src} GROUP BY r.project, r.device_id, r.session_id, k) "
            "GROUP BY p",
            params,
        )
    }
    out = []
    for p, a in by_proj.items():
        n_sess, emps, last = stats.get(p, (0, 0, None))
        out.append(
            ProjectRow(
                **tok(a.t),
                project=p,
                events=a.events,
                sessions=n_sess,
                employees=emps,
                cost=a.cost,
                cost_partial=a.partial,
                unknown_cost_models=a.unknown_models,
                last_active=ctx.iso(last),
            )
        )
    return sorted(out, key=lambda r: (-r.total_tokens, r.project))


def _recent_keys(ctx: QueryContext, flt: Filter, limit: int) -> list[tuple[str, str, float]]:
    """Most recently active (device, session) pairs, newest first (index walk)."""
    src, params = ctx.scope(flt, "rollup_session", session=True)
    probe = max(limit * 8, 64)
    rows = ctx.q(
        f"SELECT r.device_id, r.session_id, r.last_ts FROM {src} ORDER BY r.last_ts DESC LIMIT ?",
        [*params, probe],
    )
    picked: dict[tuple[str, str], float] = {}
    for dev, sid, lt in rows:
        picked.setdefault((dev, sid), lt)
    if len(picked) < limit and len(rows) == probe:  # many rows per session: exact path
        rows = ctx.q(
            f"SELECT r.device_id, r.session_id, MAX(r.last_ts) AS lt FROM {src} "
            "GROUP BY r.device_id, r.session_id ORDER BY lt DESC LIMIT ?",
            [*params, limit],
        )
        picked = {(dev, sid): lt for dev, sid, lt in rows}
    return [(d, s, lt) for (d, s), lt in list(picked.items())[:limit]]


def sessions(ctx: QueryContext, flt: Filter, limit: int) -> list[SessionRow]:
    """Sessions overlapping the filter, most recently active first."""
    keys = _recent_keys(ctx, flt, limit)
    if not keys:
        return []
    order = {(d, s): lt for d, s, lt in keys}
    pairs = ",".join("(?,?)" for _ in keys)
    meta = "LEFT JOIN session_meta m ON m.device_id = r.device_id AND m.session_id = r.session_id"
    src, params = ctx.scope(
        flt,
        "rollup_session",
        session=True,
        joins=meta,
        extra=[f"(r.device_id, r.session_id) IN (VALUES {pairs})"],
        extra_params=[v for d, s, _ in keys for v in (d, s)],
    )
    rows = ctx.q(
        f"SELECT r.device_id, r.session_id, {KEY}, r.project, r.model, r.input, r.output, "
        "r.cache_read, r.cache_5m, r.cache_1h, r.sidechain_tokens, r.events, r.first_ts, "
        f"r.last_ts, m.title FROM {src}",
        params,
    )
    names = ctx.names()
    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for r in rows:
        grouped[(r[0], r[1])].append(r)
    out = []
    for k in sorted(grouped, key=lambda k: (-order[k], k)):
        acc = Acc()
        proj_tok: dict[str, int] = defaultdict(int)
        side, ev, first, last = 0, 0, float("inf"), 0.0
        models: set[str] = set()
        for r in grouped[k]:
            toks = list(r[5:10])
            acc.add(ctx.pricing, r[4], toks)
            proj_tok[r[3]] += sum(toks)
            models.add(model_label(r[4]))
            side += r[10]
            ev += r[11]
            first, last = min(first, r[12]), max(last, r[13])
        key0 = grouped[k][0][2]
        out.append(
            SessionRow(
                **tok(acc.t),
                session_id=k[1],
                device_id=k[0],
                title=grouped[k][0][14],
                project=max(proj_tok, key=lambda p: (proj_tok[p], p)),
                projects=sorted(proj_tok),
                models=sorted(models),
                employee_key=key0,
                display_name=ctx.display(key0, names),
                events=ev,
                sidechain_tokens=side,
                cost=acc.cost,
                cost_partial=acc.partial,
                started_at=ctx.iso(first) or "",
                last_at=ctx.iso(last) or "",
            )
        )
    return out
