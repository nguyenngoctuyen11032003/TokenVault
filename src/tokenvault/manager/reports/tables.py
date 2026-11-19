"""Report tables: repeating header, zebra rows, right-aligned numbers, ellipsis-cut text.

Cells are plain strings (drawn as text, never parsed as markup) already fitted to their
column width, so long or hostile values cannot break the layout.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeAlias

from reportlab.graphics.shapes import Drawing, Rect
from reportlab.lib.colors import HexColor
from reportlab.platypus import LongTable, TableStyle

from tokenvault.manager.agg_models import EmployeeRow, ProjectRow, SessionRow
from tokenvault.manager.reports import fonts
from tokenvault.manager.reports import palette as P
from tokenvault.manager.reports import strings_vi as S
from tokenvault.manager.reports.chart_kit import fit, text

FONT_SIZE = 7.6
PAD = 4.0
Cell: TypeAlias = Any  # str or a reportlab Drawing (untyped library)


def _table(
    header: Sequence[str],
    rows: Sequence[Sequence[Cell]],
    widths: Sequence[float],
    right: Sequence[int],
    footer: str | None = None,
) -> LongTable:
    f = fonts.ensure_fonts()
    head = [fit(h, FONT_SIZE, w - 2 * PAD, True) for h, w in zip(header, widths, strict=True)]
    body: list[list[Cell]] = []
    for r in rows:
        body.append(
            [
                fit(v, FONT_SIZE, w - 2 * PAD) if isinstance(v, str) else v
                for v, w in zip(r, widths, strict=True)
            ]
        )
    data: list[list[Cell]] = [head, *body]
    if footer:
        data.append([fit(footer, FONT_SIZE, sum(widths) - 2 * PAD)] + [""] * (len(widths) - 1))
    t = LongTable(data, colWidths=list(widths), repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), f.regular),
        ("FONTNAME", (0, 0), (-1, 0), f.bold),
        ("FONTSIZE", (0, 0), (-1, -1), FONT_SIZE),
        ("LEADING", (0, 0), (-1, -1), FONT_SIZE + 2),
        ("TEXTCOLOR", (0, 0), (-1, -1), HexColor(P.INK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor(P.INK)),
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(P.HEAD_BG)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, HexColor(P.ACCENT)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor(P.WHITE), HexColor(P.ZEBRA)]),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, HexColor(P.RULE)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), PAD),
        ("RIGHTPADDING", (0, 0), (-1, -1), PAD),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
    ]
    for c in right:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    if footer:
        style += [
            ("SPAN", (0, -1), (-1, -1)),
            ("TEXTCOLOR", (0, -1), (-1, -1), HexColor(P.MUTED)),
            ("ALIGN", (0, -1), (-1, -1), "LEFT"),
        ]
    t.setStyle(TableStyle(style))
    return t


def share_bar(share: float, max_share: float, w: float) -> Drawing:
    """Tiny in-cell bar + percentage (the bar is relative to the largest share)."""
    d = Drawing(w, 9)
    pct = S.fmt_pct(share)
    bar_w = max(0.0, w - 30)
    d.add(Rect(0, 1.5, bar_w, 5, rx=1.5, ry=1.5, fillColor=HexColor(P.GRID), strokeColor=None))
    if share > 0 and max_share > 0:
        d.add(
            Rect(
                0,
                1.5,
                max(1.2, bar_w * share / max_share),
                5,
                rx=1.5,
                ry=1.5,
                fillColor=HexColor(P.ACCENT),
                strokeColor=None,
            )
        )
    d.add(text(w, 1.2, pct, FONT_SIZE - 0.4, P.INK, "end"))
    return d


def employees_table(rows: Sequence[EmployeeRow], more: int, width: float) -> LongTable:
    widths = [18, 168, 72, 64, 66, 38, 0.0]
    widths[-1] = width - sum(widths)
    max_share = max((r.share for r in rows), default=0.0)
    body: list[list[Cell]] = []
    for i, r in enumerate(rows, 1):
        body.append(
            [
                str(i),
                r.display_name,
                ", ".join(r.teams) or S.NONE,
                S.fmt_int(r.total_tokens),
                S.fmt_cost(r.cost, r.cost_partial),
                S.fmt_int(r.sessions),
                share_bar(r.share, max_share, widths[-1] - 2 * PAD),
            ]
        )
    footer = S.MORE_PEOPLE.format(n=more) if more else None
    return _table(
        ["#", S.COL_ACCOUNT, S.COL_TEAM, S.COL_TOKENS, S.COL_COST, S.COL_SESSIONS, S.COL_SHARE],
        body,
        widths,
        right=[0, 3, 4, 5],
        footer=footer,
    )


def projects_table(rows: Sequence[ProjectRow], top_n: int, width: float) -> LongTable:
    widths = [0.0, 84, 76, 56, 60]
    widths[0] = width - sum(widths)
    body: list[list[Cell]] = [
        [
            r.project or S.NONE,
            S.fmt_int(r.total_tokens),
            S.fmt_cost(r.cost, r.cost_partial),
            S.fmt_int(r.sessions),
            S.fmt_int(r.employees),
        ]
        for r in rows[:top_n]
    ]
    more = len(rows) - top_n
    return _table(
        [S.COL_PROJECT, S.COL_TOKENS, S.COL_COST_SHORT, S.COL_SESSIONS, S.COL_EMPLOYEES],
        body,
        widths,
        right=[1, 2, 3, 4],
        footer=S.MORE_ROWS.format(n=more) if more > 0 else None,
    )


def _models(models: Sequence[str]) -> str:
    short = [m.removeprefix("claude-") for m in models]
    if not short:
        return S.NONE
    return short[0] if len(short) == 1 else f"{short[0]} +{len(short) - 1}"


def _started(iso: str) -> str:
    return S.fmt_dt(iso, short=True) if iso else S.NONE


def sessions_table(
    rows: Sequence[SessionRow], width: float, *, titles: bool, with_employee: bool = True
) -> LongTable:
    if with_employee:
        widths = [0.0, 74, 92, 66, 46, 44, 62]
        header = [
            S.COL_TITLE,
            S.COL_PROJECT,
            S.COL_EMPLOYEE,
            S.COL_MODEL,
            S.COL_TOKENS,
            S.COL_COST_SHORT,
            S.COL_STARTED,
        ]
    else:
        widths = [0.0, 84, 76, 50, 46, 62]
        header = [
            S.COL_TITLE,
            S.COL_PROJECT,
            S.COL_MODEL,
            S.COL_TOKENS,
            S.COL_COST_SHORT,
            S.COL_STARTED,
        ]
    widths[0] = width - sum(widths)
    body: list[list[Cell]] = []
    for s in rows:
        title = (s.title or S.NONE) if titles else S.NONE
        cells: list[Cell] = [title, s.project or S.NONE]
        if with_employee:
            cells.append(s.display_name)
        cells += [
            _models(s.models),
            S.fmt_compact(s.total_tokens),
            S.fmt_cost(s.cost, s.cost_partial),
            _started(s.started_at),
        ]
        body.append(cells)
    n = len(widths)
    return _table(header, body, widths, right=[n - 3, n - 2])
