"""Building blocks for the report's vector charts: text fitting, ticks, titles, legends."""

from __future__ import annotations

import math
from collections.abc import Sequence

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib.colors import Color, HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth

from tokenvault.manager.reports import fonts
from tokenvault.manager.reports import palette as P
from tokenvault.manager.reports import strings_vi as S

TITLE_SIZE = 9.5
LABEL_SIZE = 7.2
TITLE_H = 18.0
ELLIPSIS = "…"


def col(hex_: str) -> Color:
    return HexColor(hex_)


def tint(hex_: str, t: float) -> Color:
    """``hex_`` mixed with white (t=0 -> colour, t=1 -> white); print-safe instead of alpha."""
    c = HexColor(hex_)
    return Color(c.red + (1 - c.red) * t, c.green + (1 - c.green) * t, c.blue + (1 - c.blue) * t)


def fam() -> fonts.FontFamily:
    return fonts.ensure_fonts()


def num(v: object) -> float:
    """Untrusted number -> finite, non-negative float (negatives / NaN / None become 0)."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) and f > 0 else 0.0


def fit(text: object, size: float, max_w: float, bold: bool = False) -> str:
    """Clean ``text`` for the font and cut it with an ellipsis to ``max_w`` points."""
    f = fam()
    name = f.bold if bold else f.regular
    s = fonts.clean(text, name).strip()
    if max_w <= 0:
        return ""
    if stringWidth(s, name, size) <= max_w:
        return s
    ell_w = stringWidth(ELLIPSIS, name, size)
    lo, hi = 0, len(s)
    while lo < hi:  # longest prefix that fits with the ellipsis
        mid = (lo + hi + 1) // 2
        if stringWidth(s[:mid], name, size) + ell_w <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (s[:lo].rstrip() + ELLIPSIS) if lo else (ELLIPSIS if ell_w <= max_w else "")


def width(text: str, size: float, bold: bool = False) -> float:
    f = fam()
    return float(stringWidth(text, f.bold if bold else f.regular, size))


def text(
    x: float,
    y: float,
    s: str,
    size: float = LABEL_SIZE,
    color: str = P.MUTED,
    anchor: str = "start",
    bold: bool = False,
) -> String:
    f = fam()
    return String(
        x,
        y,
        s,
        fontName=f.bold if bold else f.regular,
        fontSize=size,
        fillColor=col(color),
        textAnchor=anchor,
    )


def nice_ticks(max_v: float, count: int = 4) -> list[float]:
    """0 and ``count`` evenly spaced 1/2/2.5/5 x 10^k ticks covering ``max_v`` (> 0)."""
    if max_v <= 0:
        return [0.0, 1.0]
    raw = max_v / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    n = max(1, math.ceil(max_v / step - 1e-9))
    return [i * step for i in range(n + 1)]


def new(width_: float, height: float, title: str, note: str = "") -> Drawing:
    """Drawing with the title (bold, left) and an optional muted note (right)."""
    d = Drawing(width_, height)
    note_s = fit(note, LABEL_SIZE, width_ * 0.4) if note else ""
    note_w = width(note_s, LABEL_SIZE) + 8 if note_s else 0
    d.add(
        text(
            0,
            height - 12,
            fit(title, TITLE_SIZE, width_ - note_w, True),
            TITLE_SIZE,
            P.INK,
            bold=True,
        )
    )
    if note_s:
        d.add(text(width_, height - 12, note_s, LABEL_SIZE, P.MUTED, "end"))
    return d


def placeholder(d: Drawing, x: float, y: float, w: float, h: float) -> Drawing:
    """Empty-data box: dashed outline with the 'no data in this range' message."""
    d.add(
        Rect(
            x,
            y,
            w,
            h,
            rx=4,
            ry=4,
            fillColor=col(P.CARD_BG),
            strokeColor=col(P.RULE),
            strokeWidth=0.6,
            strokeDashArray=[3, 2],
        )
    )
    d.add(text(x + w / 2, y + h / 2 - 3, fit(S.EMPTY, 8.5, w - 12), 8.5, P.MUTED, "middle"))
    return d


def legend(
    d: Drawing,
    items: Sequence[tuple[str, str]],
    x: float,
    y_top: float,
    max_w: float,
    size: float = LABEL_SIZE,
    max_rows: int = 3,
) -> float:
    """Wrapping legend (swatch + label) from ``y_top`` down; returns the height used."""
    row_h = size + 5
    cx, row = x, 0
    item_max = max(40.0, max_w / 2)
    for label, color in items:
        s = fit(label, size, item_max - 14)
        w = 10 + width(s, size) + 12
        if cx > x and cx + w > x + max_w:
            row += 1
            cx = x
        if row >= max_rows:
            break
        y = y_top - row * row_h - size
        d.add(Rect(cx, y, 7, 7, rx=1.5, ry=1.5, fillColor=col(color), strokeColor=None))
        d.add(text(cx + 10, y + 0.5, s, size, P.INK_2))
        cx += w
    return (row + 1) * row_h if items else 0.0


def top_with_other(
    labels: Sequence[object], values: Sequence[object], keep: int
) -> tuple[list[str], list[float]]:
    """Largest ``keep`` items, the rest summed into 'Khác' (labels as text, values >= 0)."""
    pairs = sorted(
        ((str(lb), num(v)) for lb, v in zip(labels, values, strict=False)),
        key=lambda p: -p[1],
    )
    pairs = [p for p in pairs if p[1] > 0]
    head, rest = pairs[:keep], pairs[keep:]
    if rest:
        head.append((S.OTHER, sum(v for _, v in rest)))
    return [p[0] for p in head], [p[1] for p in head]
