"""Single-session header (``Aggregates.session``), same shape and rules as a
``sessions()`` row: tokens are not clipped to any time range, ``project`` is the
project with most tokens, cost follows the usual known/partial/UNKNOWN rules.

A session id is unique per device; if the same id exists on several devices the
most recently active one is returned.
"""

from __future__ import annotations

from collections import defaultdict

from tokenvault.manager.agg_models import SessionRow
from tokenvault.manager.agg_sql import KEY, Acc, QueryContext, model_label, tok


def session(ctx: QueryContext, session_id: str) -> SessionRow | None:
    if not session_id:
        return None
    hit = ctx.q(
        "SELECT device_id, MAX(last_ts) AS lt FROM rollup_session WHERE session_id = ? "
        "GROUP BY device_id ORDER BY lt DESC, device_id LIMIT 1",
        [session_id],
    )
    if not hit:
        return None
    device_id = str(hit[0][0])
    rows = ctx.q(
        f"SELECT {KEY}, r.project, r.model, r.input, r.output, r.cache_read, r.cache_5m, "
        "r.cache_1h, r.sidechain_tokens, r.events, r.first_ts, r.last_ts, m.title "
        "FROM rollup_session r LEFT JOIN session_meta m "
        "ON m.device_id = r.device_id AND m.session_id = r.session_id "
        "WHERE r.device_id = ? AND r.session_id = ?",
        [device_id, session_id],
    )
    if not rows:
        return None
    acc = Acc()
    proj_tok: dict[str, int] = defaultdict(int)
    side, events, first, last = 0, 0, float("inf"), 0.0
    models: set[str] = set()
    for r in rows:
        toks = list(r[3:8])
        acc.add(ctx.pricing, r[2], toks)
        proj_tok[r[1]] += sum(toks)
        models.add(model_label(r[2]))
        side += r[8]
        events += r[9]
        first, last = min(first, r[10]), max(last, r[11])
    key = str(rows[0][0])
    return SessionRow(
        **tok(acc.t),
        session_id=session_id,
        device_id=device_id,
        title=rows[0][12],
        project=max(proj_tok, key=lambda p: (proj_tok[p], p)),
        projects=sorted(proj_tok),
        models=sorted(models),
        employee_key=key,
        display_name=ctx.display(key, ctx.names()),
        events=events,
        sidechain_tokens=side,
        cost=acc.cost,
        cost_partial=acc.partial,
        started_at=ctx.iso(first) or "",
        last_at=ctx.iso(last) or "",
    )
