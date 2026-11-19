"""Story builders: summary KPIs, chart blocks, tables, per-employee pages, privacy note."""

from __future__ import annotations

from collections.abc import Sequence

from reportlab.graphics.shapes import Drawing
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    KeepInFrame,
    KeepTogether,
    PageBreak,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from tokenvault.manager.agg_models import DayPoint, DeviceInfo, ModelRow, ProjectRow, Totals
from tokenvault.manager.reports import charts, fonts
from tokenvault.manager.reports import palette as P
from tokenvault.manager.reports import strings_vi as S
from tokenvault.manager.reports.data import EmployeeSection, ReportData
from tokenvault.manager.reports.layout import (
    FRAME_W,
    PAGE_H,
    Card,
    KpiGrid,
    SectionHeading,
    esc,
    styles,
)
from tokenvault.manager.reports.tables import employees_table, projects_table, sessions_table

GAP = 14.0
HALF = (FRAME_W - GAP) / 2
SECTION_H = PAGE_H - 2 * 42.52 - 8  # usable frame height for a one-page employee section


def _pair(left: Drawing, right: Drawing) -> Table:
    t = Table([[left, right]], colWidths=[HALF + GAP, HALF])
    t.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return t


def _cost_card(cost: float | None, partial: bool, unknown: Sequence[str]) -> Card:
    lines = [S.COST_DISCLAIMER]
    models = ", ".join(unknown)
    if cost is None and unknown:
        lines.append(S.COST_UNKNOWN_NOTE.format(models=models))
    elif partial:
        lines.append(f"{S.COST_PARTIAL} · " + S.COST_PARTIAL_NOTE.format(models=models))
    color = P.ACCENT if cost is None else P.INK
    return Card(S.KPI_COST, S.fmt_cost(cost, partial), lines, color)


def _token_card(t: Totals) -> Card:
    cw = t.cache_create_5m_tokens + t.cache_create_1h_tokens
    return Card(
        S.KPI_TOKENS,
        S.fmt_compact(t.total_tokens),
        [
            f"{S.fmt_int(t.total_tokens)} token",
            S.BREAKDOWN.format(
                i=S.fmt_compact(t.input_tokens),
                o=S.fmt_compact(t.output_tokens),
                cr=S.fmt_compact(t.cache_read_tokens),
                cw=S.fmt_compact(cw),
            ),
        ],
    )


def _busiest_card(day: DayPoint | None) -> Card:
    if day is None:
        return Card(S.KPI_BUSIEST, S.NONE, [S.EMPTY])
    return Card(S.KPI_BUSIEST, S.fmt_day(day.day), [f"{S.fmt_compact(day.total_tokens)} token"])


def _device_lines(devices: Sequence[DeviceInfo]) -> list[str]:
    out = []
    for d in devices:
        state = (
            S.DEV_ONLINE
            if d.online
            else (
                S.DEV_LAST_SEEN.format(when=S.fmt_dt(d.last_seen)) if d.last_seen else S.DEV_NEVER
            )
        )
        who = f"{d.windows_user}@{d.hostname}" if d.windows_user or d.hostname else S.NONE
        out.append(f"{who} · {d.team or S.NONE} · {state}")
    return out or [S.DEVICES_NONE]


def summary(data: ReportData) -> list[Flowable]:
    t = data.totals
    top = data.top_project
    share = top.total_tokens / t.total_tokens if top and t.total_tokens else 0.0
    cards = [
        _token_card(t),
        _cost_card(t.cost, t.cost_partial, data.unknown_cost_models),
        Card(
            S.KPI_SESSIONS,
            S.fmt_int(t.sessions),
            [S.SESSIONS_SUB.format(events=S.fmt_int(t.events))],
        ),
    ]
    if data.single:
        devs = data.single_devices
        cards.append(Card(S.KPI_DEVICES, str(len(devs)), _device_lines(devs)))
    else:
        cards.append(
            Card(
                S.KPI_EMPLOYEES,
                S.fmt_int(data.active_employees),
                [
                    S.EMPLOYEES_SUB.format(n=len(data.ranking_all)),
                    S.ONLINE_SUB.format(n=data.online_employees),
                ],
            )
        )
    cards.append(_busiest_card(data.busiest_day))
    cards.append(
        Card(
            S.KPI_TOP_PROJECT,
            top.project if top else S.NONE,
            [f"{S.fmt_compact(top.total_tokens)} token · {S.fmt_pct(share)}"] if top else [S.EMPTY],
        )
    )
    out: list[Flowable] = [
        SectionHeading(S.SEC_OVERVIEW, space_before=6),
        Spacer(1, 6),
        KpiGrid(cards),
    ]
    if data.is_empty:
        st = styles(fonts.ensure_fonts())
        out += [Spacer(1, 8), Paragraph(S.NO_DATA_SCOPE, st["body"])]
    return out


def _hour_labels(hours: Sequence[str]) -> list[str]:
    return [f"{int(h[11:13])}h" if len(h) >= 13 and h[11:13].isdigit() else "" for h in hours]


def _daily_chart(days: Sequence[DayPoint], w: float, h: float) -> Drawing:
    total = sum(d.total_tokens for d in days)
    note = f"Tổng {S.fmt_compact(total)} token" if total else ""
    return charts.daily_area(
        S.CH_DAILY, [d.day for d in days], [d.total_tokens for d in days], w, h, note=note
    )


def _project_chart(rows: Sequence[ProjectRow], w: float, h: float) -> Drawing:
    return charts.hbars(
        S.CH_PROJECT, [r.project for r in rows], [r.total_tokens for r in rows], w, h
    )


def _model_chart(rows: Sequence[ModelRow], w: float, h: float) -> Drawing:
    return charts.donut(
        S.CH_MODEL,
        [r.model.removeprefix("claude-") for r in rows],
        [r.total_tokens for r in rows],
        w,
        h,
    )


def chart_block(data: ReportData) -> list[Flowable]:
    """Charts for the scope; nothing when the scope has no tokens (the KPI note says so)."""
    if data.is_empty:
        return []
    out: list[Flowable] = [CondPageBreak(200), SectionHeading(S.SEC_CHARTS), Spacer(1, 6)]
    busy = data.busiest_day
    hours_title = S.CH_HOURS.format(day=S.fmt_day(busy.day)) if busy else S.CH_HOURS_EMPTY
    hour_labels = _hour_labels([p.hour for p in data.hourly_busiest])
    hour_vals = [p.total_tokens for p in data.hourly_busiest]
    multi_day = len(data.daily) > 1
    if multi_day:
        out += [_daily_chart(data.daily, FRAME_W, 150), Spacer(1, 10)]
    else:  # one day: the hour profile is the time chart
        out += [charts.hour_bars(hours_title, hour_labels, hour_vals, FRAME_W, 150), Spacer(1, 10)]
    out.append(
        _pair(_project_chart(data.by_project, HALF, 165), _model_chart(data.by_model, HALF, 165))
    )
    tools_w = HALF if multi_day else FRAME_W
    tools = charts.hbars(
        S.CH_TOOLS,
        [t.tool for t in data.top_tools],
        [t.calls for t in data.top_tools],
        tools_w,
        165,
        single=P.SERIES[3],
        fmt=S.fmt_int,
    )
    if multi_day:
        out.append(_pair(tools, charts.hour_bars(hours_title, hour_labels, hour_vals, HALF, 165)))
    else:
        out += [tools, Spacer(1, 10)]
    if data.parts > 1:
        st = styles(fonts.ensure_fonts())
        out += [Paragraph(S.TOOLS_APPROX, st["small"]), Spacer(1, 8)]
    if data.spec.scope != "employee" or len(data.ranking_all) > 1:
        top = [r for r in data.ranking_all if r.total_tokens > 0]
        h = 18 + 17 * min(len(top), 11) + 8
        out.append(
            charts.hbars(
                S.CH_EMPLOYEES,
                [r.display_name for r in top],
                [r.total_tokens for r in top],
                FRAME_W,
                h,
                keep=10,
                single=P.SERIES[0],
            )
        )
        out.append(Spacer(1, 10))
    if data.stacked and multi_day:
        out.append(
            charts.stacked_days(
                S.CH_STACKED, [d.day for d in data.daily], data.stacked, FRAME_W, 210
            )
        )
    return out


def _sessions_note(data: ReportData) -> str:
    note = f"top {len(data.top_sessions)}"
    if data.session_window:
        note += " · " + S.SESSIONS_WINDOW.format(n=S.fmt_int(data.session_window))
    return note


def tables(data: ReportData) -> list[Flowable]:
    spec = data.spec
    out: list[Flowable] = []
    if spec.scope != "employee" or len(data.ranking_all) > 1:
        out += [
            SectionHeading(S.SEC_EMPLOYEES, f"{len(data.ranking_all)} tài khoản"),
            Spacer(1, 6),
            employees_table(data.employees, data.employees_more, FRAME_W),
        ]
    if data.by_project:
        out += [
            SectionHeading(S.SEC_PROJECTS, f"top {min(spec.top_n, len(data.by_project))}"),
            Spacer(1, 6),
            projects_table(data.by_project, spec.top_n, FRAME_W),
        ]
    if spec.include_sessions and data.top_sessions:
        out += [
            SectionHeading(S.SEC_SESSIONS, _sessions_note(data)),
            Spacer(1, 6),
            sessions_table(
                data.top_sessions,
                FRAME_W,
                titles=spec.include_session_titles,
                with_employee=len(data.ranking_all) > 1,
            ),
        ]
    return out


def employee_section(sec: EmployeeSection, data: ReportData) -> list[Flowable]:
    """One page per employee (shrunk to fit), header + KPIs + charts + top sessions."""
    st = styles(fonts.ensure_fonts())
    f = fonts.ensure_fonts()
    v, row = sec.view, sec.row
    sub = " · ".join(x for x in (", ".join(row.teams), S.SEC_EMPLOYEE_DETAIL) if x)
    body: list[Flowable] = [SectionHeading(row.display_name, sub, size=14, space_before=0)]
    dev = " | ".join(_device_lines(sec.devices)[:4])
    body += [
        Spacer(1, 3),
        Paragraph(f"{S.KPI_DEVICES}: {esc(dev, f.regular)}", st["small"]),
        Spacer(1, 6),
    ]
    t = v.totals
    busiest = max(v.daily, key=lambda d: (d.total_tokens, d.day), default=None)
    body.append(
        KpiGrid(
            [
                _token_card(t),
                _cost_card(t.cost, t.cost_partial, v.unknown_cost_models),
                Card(
                    S.KPI_SESSIONS,
                    S.fmt_int(t.sessions),
                    [S.SESSIONS_SUB.format(events=S.fmt_int(t.events))],
                ),
                _busiest_card(busiest if busiest and busiest.total_tokens else None),
            ],
            per_row=4,
            value_size=14,
        )
    )
    if t.total_tokens == 0:
        body += [Spacer(1, 10), Paragraph(S.NO_DATA_SCOPE, st["body"])]
    elif data.spec.include_charts:
        if len(v.daily) > 1:
            body += [Spacer(1, 10), _daily_chart(v.daily, FRAME_W, 125)]
        body += [
            Spacer(1, 8),
            _pair(_project_chart(v.by_project, HALF, 140), _model_chart(v.by_model, HALF, 140)),
        ]
    if sec.sessions:
        body += [
            Spacer(1, 4),
            SectionHeading(S.SEC_SESSIONS, f"top {len(sec.sessions)}", size=10.5, space_before=2),
            Spacer(1, 5),
            sessions_table(
                sec.sessions, FRAME_W, titles=data.spec.include_session_titles, with_employee=False
            ),
        ]
    return [PageBreak(), KeepInFrame(FRAME_W, SECTION_H, body, mode="shrink")]


def about(data: ReportData) -> list[Flowable]:
    st = styles(fonts.ensure_fonts())
    titles = S.TITLES_INCLUDED if data.spec.include_session_titles else S.TITLES_EXCLUDED
    paras: list[Flowable] = [SectionHeading(S.SEC_ABOUT), Spacer(1, 4)]
    for line in S.ABOUT_LINES:
        paras.append(
            Paragraph(
                esc(line.format(titles=titles), fonts.ensure_fonts().regular),
                st["bullet"],
                bulletText="•",
            )
        )
    return [Spacer(1, 8), KeepTogether(paras)]
