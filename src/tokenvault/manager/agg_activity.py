"""Skills and orchestration views (derived from tool names and is_sidechain)."""

from __future__ import annotations

from collections import defaultdict

from tokenvault.manager.agg_models import (
    AgentRow,
    DayCount,
    EmployeeCount,
    Filter,
    ModelTokens,
    OrchestrationView,
    SkillsView,
)
from tokenvault.manager.agg_sql import KEY, QueryContext, model_label
from tokenvault.manager.store import TOKEN_COLS

SKILL_NOTE = "Counts calls of the 'Skill' tool; per-skill names need a protocol field."


def skills(ctx: QueryContext, rng: str, flt: Filter | None = None) -> SkillsView:
    """Skill invocations per local day and per employee.

    The wire protocol carries tool NAMES only, so this counts responses that
    invoked the tool named ``"Skill"``; which skill was used is unknown.
    Per-skill names need a future protocol field (e.g. ``skill_names``).
    """
    f = ctx.range_filter(rng, flt)
    rows = ctx.seg_query(
        f,
        "tool",
        f"{{t}}, {KEY}, SUM(r.calls)",
        f"{{t}}, {KEY}",
        months=False,
        extra=["r.tool = 'Skill'"],
    )
    per_day: dict[int, int] = defaultdict(int)
    per_emp: dict[str, int] = defaultdict(int)
    for grain, (t, k, n) in rows:
        per_day[ctx.day_of(grain, t)] += int(n)
        per_emp[k] += int(n)
    names = ctx.names()
    return SkillsView(
        range=rng,
        total_invocations=sum(per_day.values()),
        daily=[DayCount(ctx.day_str(d), per_day[d]) for d in sorted(per_day)],
        by_employee=sorted(
            (EmployeeCount(k, ctx.display(k, names), n) for k, n in per_emp.items()),
            key=lambda e: (-e.count, e.display_name, e.key),
        ),
        note=SKILL_NOTE,
    )


def orchestration(ctx: QueryContext, rng: str, flt: Filter | None = None) -> OrchestrationView:
    """Main-thread vs sub-agent (``is_sidechain``) tokens.

    ``agents_by_tokens`` ranks sessions by their sidechain tokens: a stand-in
    for per-agent ranking, since agent names are not on the wire.
    """
    f = ctx.range_filter(rng, flt)
    total = "+".join(f"SUM(r.{c})" for c in TOKEN_COLS)
    rows = ctx.seg_query(f, "usage", f"r.sidechain, r.model, {total}", "r.sidechain, r.model")
    split: dict[int, dict[str, int]] = {0: {}, 1: {}}
    for _, (sc, model, n) in rows:
        side = split[int(sc)]
        side[model_label(model)] = side.get(model_label(model), 0) + int(n)
    meta = "LEFT JOIN session_meta m ON m.device_id = r.device_id AND m.session_id = r.session_id"
    ssrc, sparams = ctx.scope(f, "rollup_session", session=True, joins=meta)
    agents = ctx.q(
        f"SELECT r.device_id, r.session_id, {KEY} AS k, SUM(r.sidechain_tokens) AS st, "
        f"MAX(m.title) FROM {ssrc} GROUP BY r.device_id, r.session_id HAVING st > 0 "
        "ORDER BY st DESC, r.session_id LIMIT 20",
        sparams,
    )
    names = ctx.names()

    def by_model(d: dict[str, int]) -> list[ModelTokens]:
        return [ModelTokens(m, n) for m, n in sorted(d.items(), key=lambda x: (-x[1], x[0]))]

    return OrchestrationView(
        range=rng,
        main_tokens=sum(split[0].values()),
        sidechain_tokens=sum(split[1].values()),
        main_by_model=by_model(split[0]),
        sidechain_by_model=by_model(split[1]),
        agents_by_tokens=[
            AgentRow(sid, dev, title, ctx.display(k, names), int(st))
            for dev, sid, k, st, title in agents
        ],
    )
