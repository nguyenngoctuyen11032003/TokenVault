"""Read-side aggregation facade for the Manager UI. Reads rollups, never stores cost.

* **Time zone:** a fixed UTC offset (``tz_offset_minutes``), no DST. Rollups are
  UTC hours; local days are ``floor((hour * 60 + offset) / 1440)``, so "today"
  matches the Manager's local day (e.g. UTC+7 for Vietnam). Offsets that are
  not a whole number of hours are applied to whole UTC hours (each hour is
  counted in the local day its start falls in).
* **Speed:** when the :class:`Store` was opened with the same offset, ranges
  are read from local month / day rollups plus leftover hours (identical
  results to an all-hourly scan); otherwise from hourly rollups only.
  ``daily`` covers at most the last 90 local days of the range (the data span
  when open); ``monthly`` covers the whole range.
* **Team** comes from ``devices`` at query time (re-assignment is retroactive).
* **Employee identity** is the e-mail frozen on each event at ingest (event
  e-mail, else the device e-mail at that moment). Without one, the key is
  ``"device:<id>"`` and the label ``"Unknown account (windows_user@hostname)"``.
* **Cost** = per-model token sums x :class:`Pricing`; unknown models are listed
  in ``unknown_cost_models`` and flagged with ``cost_partial`` (``cost`` is
  ``None`` when no tokens are priced). A missing model shows as ``"(no model)"``.
* **Sessions** come from ``rollup_session``: a session matches a time range when
  its [first, last] activity overlaps it; its tokens honour the team / employee
  / project / model filter but are not clipped to the time range.
* **Consistency:** every public method runs all its queries inside one read
  transaction on the store's read-only connection (one snapshot, never torn,
  never blocked by the writer). Only :meth:`session_turns` reads raw ``events``.

Implementation lives in ``agg_sql`` (query core), ``agg_series`` (shared
series), ``agg_people`` (employees) and ``agg_activity`` (skills,
orchestration), ``agg_session`` (one session header); this module only
wires them together.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from tokenvault.common.pricing import Pricing
from tokenvault.manager import agg_activity, agg_people, agg_series, agg_session
from tokenvault.manager.agg_models import (
    EmployeeRow,
    EmployeeView,
    Filter,
    OrchestrationView,
    Overview,
    ProjectRow,
    SessionRow,
    SkillsView,
    Turn,
)
from tokenvault.manager.agg_sql import NO_MODEL, RANGES, QueryContext, model_label, tok
from tokenvault.manager.store import Store

__all__ = ["NO_MODEL", "RANGES", "Aggregates", "Filter"]


class Aggregates:
    def __init__(
        self,
        store: Store,
        pricing: Pricing,
        tz_offset_minutes: int = 0,
        *,
        now: Callable[[], float] = time.time,
    ):
        self.store = store
        self.pricing = pricing
        self.tz_offset_minutes = tz_offset_minutes
        self.ctx = QueryContext(store, pricing, tz_offset_minutes, now)

    def overview(self, flt: Filter) -> Overview:
        ctx = self.ctx
        with ctx.snapshot():
            s = agg_series.summary(ctx, flt)
            days, cache = agg_series.daily(ctx, flt)
            n_sess = agg_series.session_total(ctx, flt)
            return Overview(
                totals=agg_series.totals(s.total, n_sess),
                hourly=agg_series.hourly(ctx, flt),
                daily=days,
                monthly=s.monthly,
                cache_daily=cache,
                by_project=agg_series.project_rows(ctx, flt, s.by_project),
                by_model=s.by_model,
                top_tools=agg_series.top_tools(ctx, flt),
                recent_sessions=agg_series.sessions(ctx, flt, 10),
                unknown_cost_models=s.total.unknown_models,
            )

    def employees(self, flt: Filter) -> list[EmployeeRow]:
        """One row per employee key; the same e-mail on several devices merges."""
        with self.ctx.snapshot():
            return agg_people.employees(self.ctx, flt)

    def employee(self, key: str, rng: str) -> EmployeeView:
        """Detail for one employee key (e-mail or ``device:<id>``); ``rng`` in RANGES."""
        with self.ctx.snapshot():
            return agg_people.employee(self.ctx, key, rng)

    def projects(self, flt: Filter) -> list[ProjectRow]:
        with self.ctx.snapshot():
            s = agg_series.summary(self.ctx, flt)
            return agg_series.project_rows(self.ctx, flt, s.by_project)

    def sessions(self, flt: Filter, limit: int = 200) -> list[SessionRow]:
        """Sessions overlapping the filter, most recently active first."""
        with self.ctx.snapshot():
            return agg_series.sessions(self.ctx, flt, limit)

    def session(self, session_id: str) -> SessionRow | None:
        """One session header (as in :meth:`sessions`), or None when unknown."""
        with self.ctx.snapshot():
            return agg_session.session(self.ctx, session_id)

    def session_turns(self, session_id: str) -> list[Turn]:
        """Raw per-response rows of one session, ordered by timestamp."""
        rows = self.ctx.q(
            "SELECT timestamp, device_id, model, reasoning_effort, sidechain, input, output, "
            "cache_read, cache_5m, cache_1h, tool_names FROM events WHERE session_id = ? "
            "ORDER BY ts, msg_key",
            [session_id],
        )
        out = []
        for r in rows:
            toks = list(r[5:10])
            c = self.pricing.cost(r[2], *toks)
            out.append(
                Turn(
                    **tok(toks),
                    timestamp=r[0],
                    device_id=r[1],
                    model=model_label(r[2]),
                    reasoning_effort=r[3],
                    is_sidechain=bool(r[4]),
                    tool_names=json.loads(r[10]),
                    cost=None if c is None else round(float(c), 6),
                )
            )
        return out

    def skills(self, rng: str, flt: Filter | None = None) -> SkillsView:
        """Calls of the tool named "Skill" per day/employee.

        Only tool NAMES are on the wire, so per-skill names need a future
        protocol field.
        """
        with self.ctx.snapshot():
            return agg_activity.skills(self.ctx, rng, flt)

    def orchestration(self, rng: str, flt: Filter | None = None) -> OrchestrationView:
        """Main vs sidechain tokens, model split per side, sessions by sidechain tokens."""
        with self.ctx.snapshot():
            return agg_activity.orchestration(self.ctx, rng, flt)
