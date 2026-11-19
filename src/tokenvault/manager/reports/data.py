"""Everything a report shows, gathered read-only through :class:`Aggregates`.

The scope is a list of *parts* (one :class:`Filter` for "all"/"team", one per
employee key for "employee") whose overviews are merged. Days come from the
server's local calendar (the Aggregates' fixed offset), never from the machine clock.
``Overview.daily`` covers at most 90 days, so longer custom ranges read the earlier
days with the same shared series helper (``agg_series.daily``) on the Aggregates' own
query context, inside one read snapshot.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from tokenvault.manager import agg_series
from tokenvault.manager.agg_models import (
    DayPoint,
    DeviceInfo,
    EmployeeRow,
    EmployeeView,
    Filter,
    HourPoint,
    ModelRow,
    Overview,
    ProjectRow,
    SessionRow,
    ToolCount,
    Totals,
)
from tokenvault.manager.aggregates import Aggregates
from tokenvault.manager.reports import merge
from tokenvault.manager.reports import strings_vi as S
from tokenvault.manager.reports.errors import ReportEmptyScopeError
from tokenvault.manager.reports.spec import ReportSpec, local_now, range_label, resolve_range

Progress = Callable[[float, str], None]
SESSION_POOL = 3000  # most recent sessions scanned for the "top sessions by tokens" tables
MIN_POOL = 200  # per selected employee in multi-employee reports
MAX_RANKED = 60  # employees listed in the ranking table / given a detail page
STACK_SERIES = 8  # employees in the stacked daily chart before "Khác"
CHUNK_DAYS = agg_series.DAILY_DAYS


@dataclass
class EmployeeSection:
    row: EmployeeRow
    view: EmployeeView  # range data; ``view.daily`` zero-filled over the whole range
    sessions: list[SessionRow]  # top sessions by tokens

    @property
    def devices(self) -> list[DeviceInfo]:
        return self.view.devices


@dataclass
class ReportData:
    spec: ReportSpec
    now: datetime  # local (Aggregates' offset)
    first_day: str
    last_day: str
    scope_label: str
    range_label: str
    totals: Totals
    unknown_cost_models: list[str]
    daily: list[DayPoint]  # zero-filled, every day of the range
    busiest_day: DayPoint | None
    hourly_busiest: list[HourPoint]  # 24 local hours of the busiest day ([] when empty)
    by_project: list[ProjectRow]
    by_model: list[ModelRow]
    top_tools: list[ToolCount]
    ranking_all: list[EmployeeRow]  # every employee of the scope, tokens desc, shares sum to 1
    employees: list[EmployeeRow]  # first MAX_RANKED of ranking_all
    employees_more: int
    active_employees: int
    online_employees: int
    top_sessions: list[SessionRow]
    sections: list[EmployeeSection] = field(default_factory=list)
    stacked: list[tuple[str, list[int]]] = field(default_factory=list)
    parts: int = 1  # >1: several employees merged (tool counts are approximate)
    session_window: int | None = None  # sessions scanned when the recent-sessions pool was cut
    single_devices: list[DeviceInfo] = field(default_factory=list)

    @property
    def single(self) -> bool:
        """One selected employee: the summary *is* that employee (no detail page)."""
        return self.spec.scope == "employee" and len(self.spec.employee_keys) == 1

    @property
    def is_empty(self) -> bool:
        return self.totals.total_tokens == 0

    @property
    def top_project(self) -> ProjectRow | None:
        return self.by_project[0] if self.by_project and self.by_project[0].total_tokens else None


def _days(first: date, last: date) -> list[str]:
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def _daily(a: Aggregates, flt: Filter, days: list[str], got: list[DayPoint]) -> list[DayPoint]:
    """Zero-filled points for ``days``; days the overview did not cover are read in chunks."""
    by_day = {d.day: d for d in got}
    covered = set(by_day)
    todo = [d for d in days if d not in covered]
    if todo and len(days) > CHUNK_DAYS:
        with a.ctx.snapshot():
            for i in range(0, len(todo), CHUNK_DAYS):
                chunk = todo[i : i + CHUNK_DAYS]
                pts, _ = agg_series.daily(a.ctx, replace(flt, start=chunk[0], end=chunk[-1]))
                by_day.update({p.day: p for p in pts if p.day not in covered})
    return [by_day.get(d) or DayPoint(day=d) for d in days]


def _hourly(a: Aggregates, flts: list[Filter], day: str) -> list[HourPoint]:
    with a.ctx.snapshot():
        parts = [agg_series.hourly(a.ctx, replace(f, start=day, end=day)) for f in flts]
    return merge.hours(parts)


def _check_scope(a: Aggregates, spec: ReportSpec) -> dict[str, EmployeeRow]:
    """All-time employee rows by key; raises when a requested key or team does not exist."""
    known = {r.key: r for r in a.employees(Filter())}
    if spec.scope == "employee":
        missing = [k for k in spec.employee_keys if k not in known]
        if missing:
            raise ReportEmptyScopeError(
                f"Không tìm thấy nhân viên: {', '.join(missing)} / unknown employee key"
            )
    if spec.scope == "team" and not any(spec.team in r.teams for r in known.values()):
        raise ReportEmptyScopeError(f"Không tìm thấy nhóm: {spec.team} / unknown team")
    return known


def _scope_label(spec: ReportSpec, known: dict[str, EmployeeRow]) -> str:
    if spec.scope == "team":
        return f"{S.SCOPE_TEAM} {spec.team}"
    if spec.scope == "employee":
        if len(spec.employee_keys) == 1:
            return known[spec.employee_keys[0]].display_name
        return S.SCOPE_EMPLOYEES.format(n=len(spec.employee_keys))
    return S.SCOPE_ALL


def _ranking(
    a: Aggregates, spec: ReportSpec, base: Filter, known: dict[str, EmployeeRow]
) -> list[EmployeeRow]:
    if spec.scope != "employee":
        rows = a.employees(base)
    else:
        got = {r.key: r for r in a.employees(base)}
        rows = []
        for k in spec.employee_keys:
            r = got.get(k)
            if r is None:  # known key without usage in range and no device under it now
                k0 = known[k]
                r = EmployeeRow(
                    key=k,
                    email=k0.email,
                    display_name=k0.display_name,
                    devices=k0.devices,
                    teams=k0.teams,
                    online=k0.online,
                    last_seen=k0.last_seen,
                )
            rows.append(r)
        grand = sum(r.total_tokens for r in rows)
        rows = [replace(r, share=r.total_tokens / grand if grand else 0.0) for r in rows]
    return sorted(rows, key=lambda r: (-r.total_tokens, r.display_name, r.key))


def _top_sessions(pool: list[SessionRow]) -> list[SessionRow]:
    return sorted(pool, key=lambda s: (-s.total_tokens, s.started_at, s.session_id))


def _view(
    row: EmployeeRow,
    rng: str,
    ov: Overview,
    daily: list[DayPoint],
    sessions: list[SessionRow],
    devices: list[DeviceInfo],
) -> EmployeeView:
    return EmployeeView(
        key=row.key,
        email=row.email,
        display_name=row.display_name,
        range=rng,
        totals=ov.totals,
        hourly=ov.hourly,
        daily=daily,
        monthly=ov.monthly,
        by_project=ov.by_project,
        by_model=ov.by_model,
        sessions=sessions,
        devices=devices,
        unknown_cost_models=ov.unknown_cost_models,
    )


def gather(
    a: Aggregates, spec: ReportSpec, now: datetime, progress: Progress | None = None
) -> ReportData:
    """Collect the report data for ``spec`` at server-local ``now`` (naive = local)."""
    spec.validate()
    tick = progress or (lambda _f, _m: None)
    tick(0.0, "Đang đọc dữ liệu")
    known = _check_scope(a, spec)
    now_l = local_now(now, a.tz_offset_minutes)
    first, last = resolve_range(spec, now_l.date())
    days = _days(first, last)
    base = Filter(
        team=spec.team if spec.scope == "team" else None,
        start=first.isoformat(),
        end=last.isoformat(),
    )
    flts = (
        [replace(base, email=k) for k in spec.employee_keys] if spec.scope == "employee" else [base]
    )
    pool_size = max(MIN_POOL, SESSION_POOL // len(flts))
    overviews, dailies, pools = [], [], []
    for i, f in enumerate(flts):
        ov = a.overview(f)
        overviews.append(ov)
        dailies.append(_daily(a, f, days, ov.daily))
        pools.append(a.sessions(f, pool_size) if spec.include_sessions else [])
        tick(0.25 * (i + 1) / len(flts), "Đang đọc dữ liệu")
    totals = merge.totals([o.totals for o in overviews])
    daily = merge.days(dailies)
    busiest = max(daily, key=lambda d: (d.total_tokens, d.day))
    busiest_day = busiest if busiest.total_tokens > 0 else None
    ranking = _ranking(a, spec, base, known)
    all_sessions = _top_sessions([s for p in pools for s in p])
    tick(0.3, "Đang tính xếp hạng")

    data = ReportData(
        spec=spec,
        now=now_l,
        first_day=days[0],
        last_day=days[-1],
        scope_label=_scope_label(spec, known),
        range_label=range_label(spec, first, last),
        totals=totals,
        unknown_cost_models=merge.union(o.unknown_cost_models for o in overviews),
        daily=daily,
        busiest_day=busiest_day,
        hourly_busiest=_hourly(a, flts, busiest_day.day) if busiest_day else [],
        by_project=merge.projects([o.by_project for o in overviews]),
        by_model=merge.models([o.by_model for o in overviews]),
        top_tools=merge.tools([o.top_tools for o in overviews]),
        ranking_all=ranking,
        employees=ranking[:MAX_RANKED],
        employees_more=max(0, len(ranking) - MAX_RANKED),
        active_employees=sum(1 for r in ranking if r.total_tokens > 0),
        online_employees=sum(1 for r in ranking if r.online),
        top_sessions=all_sessions[: spec.top_n],
        parts=len(flts),
        session_window=(
            sum(len(p) for p in pools) if any(len(p) >= pool_size for p in pools) else None
        ),
    )
    if data.single:
        data.single_devices = a.employee(spec.employee_keys[0], "today").devices

    by_key = {r.key: r for r in ranking}
    part = {k: i for i, k in enumerate(spec.employee_keys)} if spec.scope == "employee" else {}
    # Same rules for every scope: active employees only, tokens desc, capped, opt-out honoured.
    keys = [r.key for r in ranking if r.total_tokens > 0][:MAX_RANKED]
    if not spec.per_employee_pages or data.single:
        keys = []
    per_section = spec.top_n if spec.scope == "employee" else min(spec.top_n, 8)
    for n, key in enumerate(keys):
        if key in part:
            i = part[key]
            ov, dly, pool = overviews[i], dailies[i], pools[i]
        else:
            f = replace(base, email=key)
            ov = a.overview(f)
            dly = _daily(a, f, days, ov.daily)
            pool = [s for s in all_sessions if s.employee_key == key]
        devices = a.employee(key, "today").devices
        sess = _top_sessions(pool)[:per_section] if spec.include_sessions else []
        view = _view(by_key[key], spec.range, ov, dly, sess, devices)
        data.sections.append(EmployeeSection(by_key[key], view, sess))
        tick(0.3 + 0.3 * (n + 1) / len(keys), "Đang đọc dữ liệu từng nhân viên")

    if spec.include_charts and (spec.scope != "employee" or len(flts) > 1):
        data.stacked = _stacked(a, base, data)
    tick(0.6, "Đã đọc xong dữ liệu")
    return data


def _stacked(a: Aggregates, base: Filter, data: ReportData) -> list[tuple[str, list[int]]]:
    """Daily tokens of the top employees + 'Khác' (the rest), summing to the scope's daily."""
    top = [r for r in data.ranking_all if r.total_tokens > 0][:STACK_SERIES]
    if not top:
        return []
    sections = {s.row.key: s for s in data.sections}
    days = [d.day for d in data.daily]
    out: list[tuple[str, list[int]]] = []
    for r in top:
        sec = sections.get(r.key)
        if sec is not None:
            pts = sec.view.daily
        else:
            f = replace(base, email=r.key)
            pts = _daily(a, f, days, a.overview(f).daily)
        out.append((r.display_name, [p.total_tokens for p in pts]))
    rest = [d.total_tokens - sum(v[i] for _, v in out) for i, d in enumerate(data.daily)]
    if any(rest):
        out.append((S.OTHER, rest))
    return out
