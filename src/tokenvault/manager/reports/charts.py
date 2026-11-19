"""Print-friendly vector charts (reportlab.graphics) - a calm relative of the app's charts.

Every function returns a :class:`Drawing` of exactly ``w`` x ``h`` points, never raises
on odd data (empty, one point, huge, negative/NaN -> 0) and shows
"Chưa có dữ liệu trong khoảng này" instead of an empty plot. Labels are cleaned for the
embedded font and cut with an ellipsis; numbers use 1.2M / 340K.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, PolyLine, Rect, Wedge

from tokenvault.manager.reports import palette as P
from tokenvault.manager.reports import strings_vi as S
from tokenvault.manager.reports.chart_kit import (
    LABEL_SIZE,
    TITLE_H,
    col,
    fit,
    legend,
    new,
    nice_ticks,
    num,
    placeholder,
    text,
    tint,
    top_with_other,
    width,
)

Fmt = Callable[[float], str]
HEADROOM = 1.14  # scale headroom so the peak's direct label stays inside the plot
LINE = P.ACCENT


def _y_axis(d: Drawing, x0: float, y0: float, pw: float, ph: float, top: float) -> float:
    """Grid + compact y labels; returns the scale's maximum."""
    ticks = nice_ticks(top)
    vmax = ticks[-1]
    for t in ticks:
        y = y0 + ph * t / vmax
        d.add(Line(x0, y, x0 + pw, y, strokeColor=col(P.GRID if t else P.RULE), strokeWidth=0.5))
        d.add(text(x0 - 4, y - 2.4, S.fmt_compact(t), LABEL_SIZE - 0.4, P.MUTED, "end"))
    return vmax


def _xlabel(d: Drawing, x: float, y: float, s: str, x_max: float) -> None:
    """Centred axis label, shifted to stay inside [0, x_max]."""
    half = width(s, LABEL_SIZE - 0.4) / 2
    x = min(max(x, half), x_max - half)
    d.add(text(x, y, s, LABEL_SIZE - 0.4, P.MUTED, "middle"))


def _x_day_labels(d: Drawing, days: Sequence[str], xs: Sequence[float], y: float) -> None:
    n = len(days)
    step = max(1, math.ceil(n / 8))
    idx = list(range(0, n, step))
    if n - 1 not in idx and (n - 1 - idx[-1]) >= step / 2:
        idx.append(n - 1)
    elif n - 1 not in idx:
        idx[-1] = n - 1
    for i in idx:
        try:
            label = S.fmt_day_short(days[i])
        except ValueError:
            label = fit(days[i], LABEL_SIZE, 30)
        _xlabel(d, xs[i], y, label, d.width)


def _plot_box(w: float, h: float, ymax_label: str, bottom: float = 14) -> tuple[float, ...]:
    left = max(24.0, width(ymax_label, LABEL_SIZE) + 8)
    return left, bottom, w - left - 6, h - TITLE_H - bottom - 6


def daily_area(
    title: str,
    days: Sequence[str],
    values: Sequence[object],
    w: float,
    h: float,
    color: str = LINE,
    note: str = "",
) -> Drawing:
    """Tokens per day: light area + 1.6pt line, peak labelled directly."""
    vals = [num(v) for v in values][: len(days)]
    d = new(w, h, title, note)
    if not vals or max(vals) <= 0:
        return placeholder(d, 0, 0, w, h - TITLE_H)
    top = max(vals) * HEADROOM
    x0, y0, pw, ph = _plot_box(w, h, S.fmt_compact(nice_ticks(top)[-1]))
    vmax = _y_axis(d, x0, y0, pw, ph, top)
    n = len(vals)
    pad = pw / (2 * n) if n < 3 else 0
    xs = [x0 + pad + (pw - 2 * pad) * (i / (n - 1) if n > 1 else 0.5) for i in range(n)]
    ys = [y0 + ph * v / vmax for v in vals]
    if n > 1:
        pts = [c for x, y in zip(xs, ys, strict=True) for c in (x, y)]
        d.add(Polygon([xs[0], y0, *pts, xs[-1], y0], fillColor=tint(color, 0.86), strokeColor=None))
        d.add(PolyLine(pts, strokeColor=col(color), strokeWidth=1.6, strokeLineJoin=1))
    peak = max(range(n), key=lambda i: (vals[i], i))
    d.add(
        Circle(
            xs[peak], ys[peak], 2.6, fillColor=col(color), strokeColor=col(P.WHITE), strokeWidth=0.8
        )
    )
    lab = S.fmt_compact(vals[peak])
    anchor = "end" if xs[peak] > x0 + pw - 30 else "start" if xs[peak] < x0 + 30 else "middle"
    d.add(text(xs[peak], ys[peak] + 5, lab, LABEL_SIZE, P.INK, anchor, bold=True))
    _x_day_labels(d, days, xs, y0 - 10)
    return d


def hbars(
    title: str,
    labels: Sequence[object],
    values: Sequence[object],
    w: float,
    h: float,
    *,
    keep: int = 8,
    fmt: Fmt = S.fmt_compact,
    colors: Sequence[str] | None = None,
    single: str | None = None,
    note: str = "",
) -> Drawing:
    """Horizontal bars, largest first (top ``keep`` + 'Khác'), value at each bar's end."""
    labs, vals = top_with_other(labels, values, keep)
    d = new(w, h, title, note)
    if not vals:
        return placeholder(d, 0, 0, w, h - TITLE_H)
    cols = list(colors) if colors else ([single] * len(labs) if single else P.assign(labs))
    if single:
        cols = [P.OTHER if lb == S.OTHER else single for lb in labs]
    avail = h - TITLE_H - 2
    row = min(17.0, avail / len(labs))
    bar = max(2.0, min(10.0, row * 0.62))
    label_w = min(
        w * 0.38, max(width(fit(lb, LABEL_SIZE, w * 0.38), LABEL_SIZE) for lb in labs) + 6
    )
    val_w = max(width(fmt(v), LABEL_SIZE) for v in vals) + 6
    x0, pw = label_w, max(10.0, w - label_w - val_w)
    vmax = max(vals)
    top = h - TITLE_H - 2
    d.add(Line(x0, top - row * len(labs), x0, top, strokeColor=col(P.RULE), strokeWidth=0.6))
    for i, (lb, v) in enumerate(zip(labs, vals, strict=True)):
        cy = top - row * i - row / 2
        bw = max(1.5, pw * v / vmax)
        d.add(
            Rect(
                x0, cy - bar / 2, bw, bar, rx=1.6, ry=1.6, fillColor=col(cols[i]), strokeColor=None
            )
        )
        d.add(text(x0 - 5, cy - 2.5, fit(lb, LABEL_SIZE, label_w - 6), LABEL_SIZE, P.INK_2, "end"))
        d.add(text(x0 + bw + 4, cy - 2.5, fmt(v), LABEL_SIZE, P.INK, bold=False))
    return d


def donut(
    title: str,
    labels: Sequence[object],
    values: Sequence[object],
    w: float,
    h: float,
    *,
    keep: int = 6,
    center_caption: str = "token",
    note: str = "",
) -> Drawing:
    """Share donut (top ``keep`` + 'Khác') with a legend carrying each slice's %."""
    labs, vals = top_with_other(labels, values, keep)
    d = new(w, h, title, note)
    if not vals:
        return placeholder(d, 0, 0, w, h - TITLE_H)
    total = sum(vals)
    cols = P.assign(labs)
    avail_h = h - TITLE_H - 4
    r = max(12.0, min(avail_h / 2, w * 0.2))
    cx, cy = r + 2, avail_h / 2
    angle = 90.0
    for v, c in zip(vals, cols, strict=True):
        sweep = 360.0 * v / total
        if sweep >= 359.99:
            d.add(Circle(cx, cy, r, fillColor=col(c), strokeColor=None))
        elif sweep > 0.05:
            d.add(
                Wedge(
                    cx,
                    cy,
                    r,
                    angle - sweep,
                    angle,
                    fillColor=col(c),
                    strokeColor=col(P.WHITE),
                    strokeWidth=1.2,
                )
            )
        angle -= sweep
    d.add(Circle(cx, cy, r * 0.6, fillColor=col(P.WHITE), strokeColor=None))
    d.add(text(cx, cy, S.fmt_compact(total), 10.5 if r > 30 else 8, P.INK, "middle", True))
    d.add(text(cx, cy - 10, center_caption, LABEL_SIZE - 0.6, P.MUTED, "middle"))
    lx = cx + r + 12
    lw = w - lx
    row = min(14.0, avail_h / max(1, len(labs)))
    y = cy + row * len(labs) / 2 - row / 2
    for lb, v, c in zip(labs, vals, cols, strict=True):
        pct = S.fmt_pct(v / total)
        pw_ = width(pct, LABEL_SIZE, True)
        d.add(Rect(lx, y - 1, 7, 7, rx=1.5, ry=1.5, fillColor=col(c), strokeColor=None))
        d.add(text(lx + 11, y, fit(lb, LABEL_SIZE, lw - pw_ - 18), LABEL_SIZE, P.INK_2))
        d.add(text(w, y, pct, LABEL_SIZE, P.INK, "end", True))
        y -= row
    return d


def hour_bars(
    title: str,
    labels: Sequence[str],
    values: Sequence[object],
    w: float,
    h: float,
    color: str = LINE,
    note: str = "",
) -> Drawing:
    """Vertical bars per hour of day (labels like '0h'); the peak hour is labelled."""
    vals = [num(v) for v in values]
    d = new(w, h, title, note)
    if not vals or max(vals) <= 0:
        return placeholder(d, 0, 0, w, h - TITLE_H)
    top = max(vals) * HEADROOM
    x0, y0, pw, ph = _plot_box(w, h, S.fmt_compact(nice_ticks(top)[-1]))
    vmax = _y_axis(d, x0, y0, pw, ph, top)
    n = len(vals)
    slot = pw / n
    bw = max(0.6, slot * 0.7)
    peak = max(range(n), key=lambda i: (vals[i], -i))
    labs = [str(x) for x in labels]
    every = 3 if n <= 24 else max(1, n // 8)
    for i, v in enumerate(vals):
        bh = ph * v / vmax
        x = x0 + slot * i + (slot - bw) / 2
        if bh > 0:
            c = col(color) if i == peak else tint(color, 0.35)
            d.add(
                Rect(
                    x,
                    y0,
                    bw,
                    max(bh, 0.8),
                    rx=min(1.5, bw / 3),
                    ry=min(1.5, bw / 3),
                    fillColor=c,
                    strokeColor=None,
                )
            )
        if i % every == 0:
            lab = labs[i] if i < len(labs) else str(i)
            _xlabel(d, x + bw / 2, y0 - 10, fit(lab, LABEL_SIZE - 0.4, slot * 2.6), w)
    px = x0 + slot * peak + slot / 2
    py = y0 + ph * vals[peak] / vmax + 3
    d.add(text(px, py, S.fmt_compact(vals[peak]), LABEL_SIZE, P.INK, "middle", True))
    return d


def stacked_days(
    title: str,
    days: Sequence[str],
    series: Sequence[tuple[str, Sequence[object]]],
    w: float,
    h: float,
    note: str = "",
) -> Drawing:
    """Stacked columns per day (series = top employees + 'Khác'), legend underneath."""
    clean = [(name, [num(v) for v in vals][: len(days)]) for name, vals in series][:9]
    clean = [(nm, v + [0.0] * (len(days) - len(v))) for nm, v in clean]
    totals = [sum(s[1][i] for s in clean) for i in range(len(days))] if clean else []
    d = new(w, h, title, note)
    if not totals or max(totals) <= 0:
        return placeholder(d, 0, 0, w, h - TITLE_H)
    cols = P.assign([nm for nm, _ in clean])
    probe = Drawing(w, h)
    leg_h = legend(probe, [(nm, c) for (nm, _), c in zip(clean, cols, strict=True)], 0, 0, w)
    x0, y0, pw, ph = _plot_box(w, h - leg_h - 4, S.fmt_compact(nice_ticks(max(totals))[-1]))
    y0 += leg_h + 4
    vmax = _y_axis(d, x0, y0, pw, ph, max(totals))
    n = len(days)
    slot = pw / n
    bw = max(0.5, slot * (0.72 if n <= 60 else 0.85))
    gap = 0.6 if bw > 3 else 0.0
    xs = []
    for i in range(n):
        x = x0 + slot * i + (slot - bw) / 2
        xs.append(x + bw / 2)
        y = y0
        for (_, vals), c in zip(clean, cols, strict=True):
            bh = ph * vals[i] / vmax
            if bh <= 0:
                continue
            d.add(Rect(x, y, bw, max(0.4, bh - gap), fillColor=col(c), strokeColor=None))
            y += bh
    _x_day_labels(d, days, xs, y0 - 10)
    legend(d, [(nm, c) for (nm, _), c in zip(clean, cols, strict=True)], 0, leg_h, w)
    return d
