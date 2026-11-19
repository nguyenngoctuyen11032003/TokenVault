"""Read-side aggregation for the Manager UI. Queries rollups, never stores cost.

* All time-series and totals come from ``rollup_hour`` (UTC hour buckets);
  local days are derived as ``floor((hour * 60 + tz_offset_minutes) / 1440)``,
  so "today" matches the Manager's local day (e.g. UTC+7 for Vietnam).
  Offsets that are not whole hours are rounded to the containing UTC hour.
* Team and the fallback account e-mail come from ``devices`` at query time.
* Employee identity = Claude account e-mail (the event's, else the device's).
  Without any e-mail the key is ``"device:<id>"`` and the display name is
  ``"Unknown account (windows_user@hostname)"``; such devices are never dropped.
* Cost = per-model token sums x :class:`Pricing`; unknown models give ``None``
  and are listed in ``unknown_cost_models``. Mixed groups report the known part
  with ``cost_partial=True``.
* Session figures come from ``rollup_session``: a session matches a time range
  when its [first, last] activity overlaps it; its tokens honour the team /
  employee / project / model filter but are not clipped to the time range.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from tokenvault.common.pricing import Pricing
from tokenvault.manager import agg_people
from tokenvault.manager.agg_models import (
    AgentRow,
    CacheDayPoint,
    DayCount,
    DayPoint,
    EmployeeCount,
    EmployeeRow,
    EmployeeView,
    Filter,
    HourPoint,
    ModelRow,
    ModelTokens,
    OrchestrationView,
    Overview,
    ProjectRow,
    SessionRow,
    SkillsView,
    ToolCount,
    Totals,
    Turn,
)
from tokenvault.manager.store import TOKEN_COLS, Store

__all__ = ["Aggregates", "Filter", "RANGES"]

RANGES = {"today": 1, "7d": 7, "30d": 30, "90d": 90, "all": 0}
_EPOCH = date(1970, 1, 1)
_SUMS = ", ".join(f"SUM(r.{c})" for c in TOKEN_COLS)
# Employee key of a rollup row (alias r) joined with devices (alias d).
_KEY = (
    "CASE WHEN r.email <> '' THEN r.email WHEN COALESCE(d.email, '') <> '' THEN d.email "
    "ELSE 'device:' || r.device_id END"
)
_JOIN = "LEFT JOIN devices d ON d.device_id = r.device_id"


def _tok(vals: Sequence[int]) -> dict[str, int]:
    i, o, cr, c5, c1 = (int(v or 0) for v in vals)
    return {
        "input_tokens": i,
        "output_tokens": o,
        "cache_read_tokens": cr,
        "cache_create_5m_tokens": c5,
        "cache_create_1h_tokens": c1,
        "total_tokens": i + o + cr + c5 + c1,
    }


class _Acc:
    """Token + cost accumulator for one group of rows."""

    def __init__(self) -> None:
        self.t = [0, 0, 0, 0, 0]
        self.events = 0
        self.known = Decimal(0)
        self.unknown: set[str] = set()
        self.has_known = False

    def add(self, pricing: Pricing, model: str, toks: Sequence[int], events: int = 0) -> None:
        toks = [int(v or 0) for v in toks]
        for i, v in enumerate(toks):
            self.t[i] += v
        self.events += int(events or 0)
        c = pricing.cost(model, *toks)
        if c is None:
            if any(toks):
                self.unknown.add(model)
        else:
            self.known += c
            self.has_known = True

    @property
    def cost(self) -> float | None:
        if self.unknown and not self.has_known:
            return None
        return round(float(self.known), 6)

    @property
    def partial(self) -> bool:
        return bool(self.unknown)

    @property
    def total(self) -> int:
        return sum(self.t)


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
        self._tz = timezone(timedelta(minutes=tz_offset_minutes))
        self._now = now

    # ------------------------------------------------------------- time utils
    def _ts(self, v: str | datetime, is_end: bool) -> float:
        if isinstance(v, str):
            if len(v) == 10:  # date only = local day
                d = date.fromisoformat(v) + timedelta(days=1 if is_end else 0)
                return datetime(d.year, d.month, d.day, tzinfo=self._tz).timestamp()
            v = datetime.fromisoformat(v)
        if v.tzinfo is None:
            v = v.replace(tzinfo=self._tz)
        return v.timestamp()

    def _hours(self, flt: Filter) -> tuple[int | None, int | None]:
        s = None if flt.start is None else int(self._ts(flt.start, False) // 3600)
        e = None if flt.end is None else -int(-self._ts(flt.end, True) // 3600)
        return s, e

    def _local_day(self, hour: int) -> str:
        return (_EPOCH + timedelta(days=(hour * 60 + self.tz_offset_minutes) // 1440)).isoformat()

    def _iso(self, ts: float | None) -> str | None:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, self._tz).isoformat(timespec="seconds")

    def _range_filter(self, rng: str, base: Filter | None = None) -> Filter:
        if rng not in RANGES:
            raise ValueError(f"range must be one of {sorted(RANGES)}")
        b = base or Filter()
        if rng == "all":
            return Filter(b.team, b.email, b.project, b.model, None, None)
        today = datetime.fromtimestamp(self._now(), self._tz).date()
        first = today - timedelta(days=RANGES[rng] - 1)
        return Filter(b.team, b.email, b.project, b.model, first.isoformat(), today.isoformat())

    # ------------------------------------------------------------ SQL helpers
    def _where(
        self, flt: Filter, time_col: str = "hour", session: bool = False
    ) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if flt.team is not None:
            conds.append("COALESCE(d.team, '') = ?")
            params.append(flt.team)
        if flt.email is not None:
            if flt.email.startswith("device:"):
                conds.append("r.device_id = ? AND r.email = '' AND COALESCE(d.email, '') = ''")
                params.append(flt.email[len("device:") :])
            else:
                conds.append("(r.email = ? OR (r.email = '' AND d.email = ?))")
                params += [flt.email, flt.email]
        if flt.project is not None:
            conds.append("r.project = ?")
            params.append(flt.project)
        if flt.model is not None:
            conds.append("r.model = ?")
            params.append(flt.model)
        sh, eh = self._hours(flt)
        if session:
            if sh is not None:
                conds.append("r.last_ts >= ?")
                params.append(sh * 3600)
            if eh is not None:
                conds.append("r.first_ts < ?")
                params.append(eh * 3600)
        else:
            if sh is not None:
                conds.append(f"r.{time_col} >= ?")
                params.append(sh)
            if eh is not None:
                conds.append(f"r.{time_col} < ?")
                params.append(eh)
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def _q(self, sql: str, params: Iterable[Any] = ()) -> list[Any]:
        return self.store.fetchall(sql, list(params))

    def _names(self) -> dict[str, str]:
        """device_id -> 'Unknown account (user@host)' label."""
        return {
            d.device_id: f"Unknown account ({d.windows_user}@{d.hostname})"
            if d.windows_user or d.hostname
            else f"Unknown account ({d.device_id})"
            for d in self.store.list_devices()
        }

    def _display(self, key: str, names: dict[str, str]) -> str:
        if key.startswith("device:"):
            dev = key[len("device:") :]
            return names.get(dev, f"Unknown account ({dev})")
        return key

    # ------------------------------------------------------------- building
    def _summary(self, flt: Filter) -> dict[str, Any]:
        """Totals, daily, cache_daily, by_project, by_model from one rollup scan."""
        where, params = self._where(flt)
        rows = self._q(
            f"SELECT (r.hour * 60 + ?) / 1440 AS lday, r.project, r.model, {_SUMS}, "
            f"SUM(r.events) FROM rollup_hour r {_JOIN}{where} GROUP BY lday, r.project, r.model",
            [self.tz_offset_minutes, *params],
        )
        total = _Acc()
        by_day: dict[int, _Acc] = defaultdict(_Acc)
        by_proj: dict[str, _Acc] = defaultdict(_Acc)
        by_model: dict[str, _Acc] = {}
        for lday, project, model, *rest in rows:
            toks, ev = rest[:5], rest[5]
            for acc in (total, by_day[lday], by_proj[project]):
                acc.add(self.pricing, model, toks, ev)
            by_model.setdefault(model, _Acc()).add(self.pricing, model, toks, ev)
        sh, eh = self._hours(flt)
        days = sorted(by_day)
        if sh is not None and eh is not None:
            first = (sh * 60 + self.tz_offset_minutes) // 1440
            last = ((eh - 1) * 60 + self.tz_offset_minutes) // 1440
            days = list(range(first, last + 1)) if last - first < 3660 else days
        elif days:
            days = list(range(days[0], days[-1] + 1))
        daily, cache_daily = [], []
        for dn in days:
            a = by_day.get(dn) or _Acc()
            day = (_EPOCH + timedelta(days=dn)).isoformat()
            daily.append(DayPoint(**_tok(a.t), day=day, cost=a.cost))
            denom = a.t[0] + a.t[2] + a.t[3] + a.t[4]
            cache_daily.append(
                CacheDayPoint(day, a.t[2], a.t[3], a.t[4], round(a.t[2] / denom, 4) if denom else 0)
            )
        return {
            "total": total,
            "daily": daily,
            "cache_daily": cache_daily,
            "by_project": by_proj,
            "by_model": [
                ModelRow(**_tok(a.t), model=m, events=a.events, cost=a.cost)
                for m, a in sorted(by_model.items(), key=lambda kv: (-kv[1].total, kv[0]))
            ],
        }

    def _hourly(self, flt: Filter) -> list[HourPoint]:
        sh, eh = self._hours(flt)
        end = eh if eh is not None else int(self._now() // 3600) + 1
        start = end - 24 if sh is None else max(sh, end - 24)
        sub = Filter(flt.team, flt.email, flt.project, flt.model)
        where, params = self._where(sub)
        where += (" AND " if where else " WHERE ") + "r.hour >= ? AND r.hour < ?"
        rows = self._q(
            f"SELECT r.hour, {_SUMS} FROM rollup_hour r {_JOIN}{where} GROUP BY r.hour",
            [*params, start, end],
        )
        got = {h: list(rest) for h, *rest in rows}
        out = []
        for h in range(start, end):
            label = datetime.fromtimestamp(h * 3600, self._tz).strftime("%Y-%m-%dT%H:00")
            out.append(HourPoint(**_tok(got.get(h, [0] * 5)), hour=label))
        return out

    def _session_counts(self, flt: Filter, group: str) -> dict[Any, int]:
        where, params = self._where(flt, session=True)
        rows = self._q(
            f"SELECT {group} AS g, COUNT(DISTINCT r.device_id || char(31) || r.session_id) "
            f"FROM rollup_session r {_JOIN}{where} GROUP BY g",
            params,
        )
        return {g: int(n) for g, n in rows}

    def _top_tools(self, flt: Filter, limit: int = 15) -> list[ToolCount]:
        where, params = self._where(flt)
        rows = self._q(
            f"SELECT r.tool, SUM(r.calls) AS n FROM rollup_tool_hour r {_JOIN}{where} "
            "GROUP BY r.tool ORDER BY n DESC, r.tool LIMIT ?",
            [*params, limit],
        )
        return [ToolCount(t, int(n)) for t, n in rows]

    def _project_rows(self, flt: Filter, by_proj: dict[str, _Acc]) -> list[ProjectRow]:
        sess = self._session_counts(flt, "r.project")
        where, params = self._where(flt)
        extra = {
            p: (int(n), int(h))
            for p, n, h in self._q(
                f"SELECT r.project, COUNT(DISTINCT {_KEY}), MAX(r.hour) FROM rollup_hour r "
                f"{_JOIN}{where} GROUP BY r.project",
                params,
            )
        }
        out = []
        for p, a in by_proj.items():
            emps, last_h = extra.get(p, (0, None))
            out.append(
                ProjectRow(
                    **_tok(a.t),
                    project=p,
                    events=a.events,
                    sessions=sess.get(p, 0),
                    employees=emps,
                    cost=a.cost,
                    cost_partial=a.partial,
                    unknown_cost_models=sorted(a.unknown),
                    last_active=self._iso(last_h * 3600 + 3599) if last_h is not None else None,
                )
            )
        return sorted(out, key=lambda r: (-r.total_tokens, r.project))

    def _sessions(self, flt: Filter, limit: int) -> list[SessionRow]:
        where, params = self._where(flt, session=True)
        rows = self._q(
            f"WITH top AS (SELECT r.device_id, r.session_id, MAX(r.last_ts) AS lt "
            f"FROM rollup_session r {_JOIN}{where} GROUP BY r.device_id, r.session_id "
            f"ORDER BY lt DESC LIMIT ?) "
            f"SELECT r.device_id, r.session_id, top.lt, {_KEY}, r.project, r.model, "
            f"r.input, r.output, r.cache_read, r.cache_5m, r.cache_1h, r.sidechain_tokens, "
            f"r.events, r.first_ts, r.last_ts, m.title FROM rollup_session r "
            f"JOIN top ON top.device_id = r.device_id AND top.session_id = r.session_id "
            f"{_JOIN} LEFT JOIN session_meta m "
            f"ON m.device_id = r.device_id AND m.session_id = r.session_id{where}",
            [*params, limit, *params],
        )
        names = self._names()
        grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
        order: dict[tuple[str, str], float] = {}
        for r in rows:
            grouped[(r[0], r[1])].append(r)
            order[(r[0], r[1])] = r[2]
        out = []
        for k in sorted(grouped, key=lambda k: (-order[k], k)):
            acc = _Acc()
            proj_tok: dict[str, int] = defaultdict(int)
            side, ev, first, last = 0, 0, float("inf"), 0.0
            models: list[str] = []
            for r in grouped[k]:
                toks = list(r[6:11])
                acc.add(self.pricing, r[5], toks)
                proj_tok[r[4]] += sum(toks)
                if r[5] not in models:
                    models.append(r[5])
                side += r[11]
                ev += r[12]
                first, last = min(first, r[13]), max(last, r[14])
            key0 = grouped[k][0][3]
            out.append(
                SessionRow(
                    **_tok(acc.t),
                    session_id=k[1],
                    device_id=k[0],
                    title=grouped[k][0][15],
                    project=max(proj_tok, key=lambda p: (proj_tok[p], p)),
                    projects=sorted(proj_tok),
                    models=sorted(models),
                    employee_key=key0,
                    display_name=self._display(key0, names),
                    events=ev,
                    sidechain_tokens=side,
                    cost=acc.cost,
                    cost_partial=acc.partial,
                    started_at=self._iso(first) or "",
                    last_at=self._iso(last) or "",
                )
            )
        return out

    def _totals(self, a: _Acc, sessions: int) -> Totals:
        return Totals(
            **_tok(a.t), events=a.events, sessions=sessions, cost=a.cost, cost_partial=a.partial
        )

    # --------------------------------------------------------------- public
    def overview(self, flt: Filter) -> Overview:
        s = self._summary(flt)
        total: _Acc = s["total"]
        n_sess = sum(self._session_counts(flt, "1").values())
        return Overview(
            totals=self._totals(total, n_sess),
            hourly=self._hourly(flt),
            daily=s["daily"],
            cache_daily=s["cache_daily"],
            by_project=self._project_rows(flt, s["by_project"]),
            by_model=s["by_model"],
            top_tools=self._top_tools(flt),
            recent_sessions=self._sessions(flt, 10),
            unknown_cost_models=sorted(total.unknown),
        )

    def projects(self, flt: Filter) -> list[ProjectRow]:
        return self._project_rows(flt, self._summary(flt)["by_project"])

    def sessions(self, flt: Filter, limit: int = 200) -> list[SessionRow]:
        """Sessions overlapping the filter, most recently active first."""
        return self._sessions(flt, limit)

    def employees(self, flt: Filter) -> list[EmployeeRow]:
        """One row per employee key; the same e-mail on several devices merges.

        Devices with no events are listed too (zero tokens) unless the filter
        names a project or model.
        """
        return agg_people.employees(self, flt)

    def employee(self, key: str, rng: str) -> EmployeeView:
        """Detail for one employee key (e-mail or ``device:<id>``) over a range."""
        return agg_people.employee(self, key, rng)

    def session_turns(self, session_id: str) -> list[Turn]:
        rows = self._q(
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
                    **_tok(toks),
                    timestamp=r[0],
                    device_id=r[1],
                    model=r[2],
                    reasoning_effort=r[3],
                    is_sidechain=bool(r[4]),
                    tool_names=json.loads(r[10]),
                    cost=None if c is None else round(float(c), 6),
                )
            )
        return out

    def skills(self, rng: str, flt: Filter | None = None) -> SkillsView:
        """Skill invocations per local day and per employee.

        The wire protocol carries tool NAMES only, so this counts responses that
        invoked the tool named ``"Skill"``; which skill was used is unknown.
        Per-skill names need a future protocol field (e.g. ``skill_names``).
        """
        f = self._range_filter(rng, flt)
        where, params = self._where(f)
        where += (" AND " if where else " WHERE ") + "r.tool = 'Skill'"
        rows = self._q(
            f"SELECT (r.hour * 60 + ?) / 1440 AS lday, {_KEY} AS k, SUM(r.calls) "
            f"FROM rollup_tool_hour r {_JOIN}{where} GROUP BY lday, k",
            [self.tz_offset_minutes, *params],
        )
        per_day: dict[int, int] = defaultdict(int)
        per_emp: dict[str, int] = defaultdict(int)
        for lday, k, n in rows:
            per_day[lday] += int(n)
            per_emp[k] += int(n)
        names = self._names()
        return SkillsView(
            range=rng,
            total_invocations=sum(per_day.values()),
            daily=[
                DayCount((_EPOCH + timedelta(days=d)).isoformat(), per_day[d])
                for d in sorted(per_day)
            ],
            by_employee=sorted(
                (EmployeeCount(k, self._display(k, names), n) for k, n in per_emp.items()),
                key=lambda e: (-e.count, e.display_name),
            ),
            note="Counts calls of the 'Skill' tool; per-skill names need a protocol field.",
        )

    def orchestration(self, rng: str, flt: Filter | None = None) -> OrchestrationView:
        """Main-thread vs sub-agent (``is_sidechain``) tokens.

        ``agents_by_tokens`` ranks sessions by their sidechain tokens: a stand-in
        for per-agent ranking, since agent names are not on the wire.
        """
        f = self._range_filter(rng, flt)
        where, params = self._where(f)
        side_sum = "+".join(f"SUM(r.{c})" for c in TOKEN_COLS)
        rows = self._q(
            f"SELECT r.sidechain, r.model, {side_sum} FROM rollup_hour r {_JOIN}{where} "
            "GROUP BY r.sidechain, r.model",
            params,
        )
        split: dict[int, dict[str, int]] = {0: {}, 1: {}}
        for sc, model, n in rows:
            split[int(sc)][model] = int(n)
        swhere, sparams = self._where(f, session=True)
        agents = self._q(
            f"SELECT r.device_id, r.session_id, {_KEY} AS k, SUM(r.sidechain_tokens) AS st, "
            f"MAX(m.title) FROM rollup_session r {_JOIN} LEFT JOIN session_meta m "
            f"ON m.device_id = r.device_id AND m.session_id = r.session_id{swhere} "
            "GROUP BY r.device_id, r.session_id HAVING st > 0 ORDER BY st DESC, r.session_id "
            "LIMIT 20",
            sparams,
        )
        names = self._names()

        def models(d: dict[str, int]) -> list[ModelTokens]:
            return [ModelTokens(m, n) for m, n in sorted(d.items(), key=lambda x: (-x[1], x[0]))]

        return OrchestrationView(
            range=rng,
            main_tokens=sum(split[0].values()),
            sidechain_tokens=sum(split[1].values()),
            main_by_model=models(split[0]),
            sidechain_by_model=models(split[1]),
            agents_by_tokens=[
                AgentRow(sid, dev, title, self._display(k, names), int(st))
                for dev, sid, k, st, title in agents
            ],
        )
