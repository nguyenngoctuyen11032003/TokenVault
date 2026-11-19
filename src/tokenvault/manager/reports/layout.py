"""Page furniture and custom flowables: cover band, section headings, KPI cards,
paragraph styles and the two-pass numbered canvas (footer "Trang x/y").

All untrusted text is drawn with ``drawString`` after :func:`fonts.clean` (never parsed
as markup); Paragraphs only receive text passed through :func:`esc`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable

from tokenvault.manager.reports import fonts
from tokenvault.manager.reports import palette as P
from tokenvault.manager.reports import strings_vi as S
from tokenvault.manager.reports.chart_kit import fit

PAGE_W, PAGE_H = A4
MARGIN = 15 * mm
FRAME_W = PAGE_W - 2 * MARGIN
FOOTER_Y = 8 * mm


def esc(text: object, font: str) -> str:
    """Untrusted text -> safe Paragraph markup (cleaned for the font, ``<&>`` escaped)."""
    return escape(fonts.clean(text, font))


def styles(f: fonts.FontFamily) -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "body", fontName=f.regular, fontSize=8.4, leading=11.6, textColor=HexColor(P.INK_2)
    )
    return {
        "body": base,
        "small": ParagraphStyle(
            "small", parent=base, fontSize=7.4, leading=9.8, textColor=HexColor(P.MUTED)
        ),
        "note": ParagraphStyle(
            "note",
            parent=base,
            fontSize=7.8,
            leading=10.4,
            textColor=HexColor(P.INK_2),
            leftIndent=8,
            borderPadding=(4, 6, 4, 6),
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base,
            leftIndent=10,
            bulletIndent=0,
            bulletFontName=f.regular,
            spaceAfter=3,
        ),
    }


class SectionHeading(Flowable):  # type: ignore[misc]
    """Accent tick + bold title + hairline rule; kept with the next flowable."""

    def __init__(self, title: str, note: str = "", size: float = 12.0, space_before: float = 10):
        super().__init__()
        self.title, self.note, self.size, self.space = title, note, size, space_before
        self.keepWithNext = True

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        self.width = aw
        return aw, self.size + 10 + self.space

    def draw(self) -> None:
        f = fonts.ensure_fonts()
        c = self.canv
        y = 6
        c.setFillColor(HexColor(P.ACCENT))
        c.rect(0, y, 3, self.size, stroke=0, fill=1)
        c.setFillColor(HexColor(P.INK))
        c.setFont(f.bold, self.size)
        c.drawString(9, y + 1.5, fit(self.title, self.size, self.width * 0.7, True))
        if self.note:
            c.setFont(f.regular, 7.4)
            c.setFillColor(HexColor(P.MUTED))
            c.drawRightString(self.width, y + 2, fit(self.note, 7.4, self.width * 0.3))
        c.setStrokeColor(HexColor(P.RULE))
        c.setLineWidth(0.5)
        c.line(0, 0, self.width, 0)


@dataclass
class Card:
    label: str
    value: str
    lines: Sequence[str] = field(default_factory=list)
    value_color: str = P.INK


class KpiGrid(Flowable):  # type: ignore[misc]
    """Rows of equal-width KPI cards (label, big value, up to 3 wrapped sub-lines)."""

    def __init__(self, cards: Sequence[Card], per_row: int = 3, value_size: float = 16.0):
        super().__init__()
        self.cards, self.per_row, self.vs = list(cards), per_row, value_size
        self.gap = 6.0

    def _layout(self, aw: float) -> tuple[float, list[list[list[str]]], float]:
        f = fonts.ensure_fonts()
        cw = (aw - self.gap * (self.per_row - 1)) / self.per_row
        wrapped = []
        for card in self.cards:
            lines: list[str] = []
            for ln in card.lines:
                lines += simpleSplit(fonts.clean(ln, f.regular), f.regular, 7.2, cw - 16)
            wrapped.append(lines[:4])
        rows = [wrapped[i : i + self.per_row] for i in range(0, len(wrapped), self.per_row)]
        row_h = 14 + 10 + self.vs + 4 + 9.2 * max((len(w) for w in wrapped), default=0)
        return cw, rows, row_h

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        self.width = aw
        _, rows, row_h = self._layout(aw)
        self.height = len(rows) * row_h + (len(rows) - 1) * self.gap
        return aw, self.height

    def draw(self) -> None:
        f = fonts.ensure_fonts()
        c = self.canv
        cw, rows, row_h = self._layout(self.width)
        for r, row in enumerate(rows):
            y0 = self.height - (r + 1) * row_h - r * self.gap
            for i, lines in enumerate(row):
                card = self.cards[r * self.per_row + i]
                x0 = i * (cw + self.gap)
                c.setFillColor(HexColor(P.CARD_BG))
                c.setStrokeColor(HexColor(P.RULE))
                c.setLineWidth(0.6)
                c.roundRect(x0, y0, cw, row_h, 3, stroke=1, fill=1)
                c.setFillColor(HexColor(P.MUTED))
                c.setFont(f.regular, 7.4)
                c.drawString(x0 + 8, y0 + row_h - 13, fit(card.label.upper(), 7.4, cw - 16))
                c.setFillColor(HexColor(card.value_color))
                c.setFont(f.bold, self.vs)
                c.drawString(
                    x0 + 8, y0 + row_h - 16 - self.vs, fit(card.value, self.vs, cw - 16, True)
                )
                c.setFont(f.regular, 7.2)
                c.setFillColor(HexColor(P.INK_2))
                y = y0 + row_h - 24 - self.vs - 7
                for ln in lines:
                    c.drawString(x0 + 8, y, ln)
                    y -= 9.2


class CoverBand(Flowable):  # type: ignore[misc]
    """Page-1 header: letter-spaced TOKENVAULT wordmark, title, scope, meta, accent rule."""

    def __init__(self, scope: str, meta: Sequence[tuple[str, str]], subtitle: str = ""):
        super().__init__()
        self.scope, self.meta, self.subtitle = scope, list(meta), subtitle

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        self.width = aw
        self.height = 96.0
        return aw, self.height

    def draw(self) -> None:
        f = fonts.ensure_fonts()
        c, w, h = self.canv, self.width, self.height
        left_w = w * 0.6
        c.setFillColor(HexColor(P.ACCENT))
        c.setFont(f.bold, 10)
        c.drawString(0, h - 12, S.WORDMARK, charSpace=3.2)
        c.setFillColor(HexColor(P.INK))
        c.setFont(f.bold, 19)
        c.drawString(0, h - 40, fit(S.TITLE, 19, left_w, True))
        c.setFont(f.regular, 11)
        c.setFillColor(HexColor(P.INK_2))
        c.drawString(0, h - 58, fit(self.scope, 11, left_w))
        if self.subtitle:
            c.setFont(f.regular, 8)
            c.setFillColor(HexColor(P.MUTED))
            c.drawString(0, h - 72, fit(self.subtitle, 8, left_w))
        y = h - 14
        for label, value in self.meta:
            c.setFont(f.regular, 7.2)
            c.setFillColor(HexColor(P.MUTED))
            c.drawRightString(w, y, fit(label.upper(), 7.2, w - left_w - 10))
            c.setFont(f.bold, 9)
            c.setFillColor(HexColor(P.INK))
            c.drawRightString(w, y - 11, fit(value, 9, w - left_w - 10, True))
            y -= 24
        c.setStrokeColor(HexColor(P.ACCENT))
        c.setLineWidth(1.6)
        c.line(0, 4, w, 4)


def numbered_canvas(
    footer_when: str, header: str, pages_out: list[int], pdf_date: str
) -> type[Canvas]:
    """Canvas class that defers pages so the footer can print "Trang x/y"."""

    class NumberedCanvas(Canvas):  # type: ignore[misc]
        def __init__(self, *args: Any, **kw: Any) -> None:
            super().__init__(*args, **kw)
            self._saved: list[dict[str, Any]] = []
            try:  # private reportlab hook; without it the date is reportlab's own
                self._doc.info._dateFormatter = lambda *_a: pdf_date
            except AttributeError:
                pass

        def showPage(self) -> None:  # noqa: N802 - reportlab API
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._saved)
            pages_out.append(total)
            for state in self._saved:
                self.__dict__.update(state)
                self._furniture(total)
                Canvas.showPage(self)
            Canvas.save(self)

        def _furniture(self, total: int) -> None:
            f = fonts.ensure_fonts()
            page = self.getPageNumber()
            self.saveState()
            self.setStrokeColor(HexColor(P.RULE))
            self.setLineWidth(0.5)
            self.line(MARGIN, FOOTER_Y + 10, PAGE_W - MARGIN, FOOTER_Y + 10)
            self.setFont(f.regular, 7)
            self.setFillColor(HexColor(P.MUTED))
            self.drawString(
                MARGIN, FOOTER_Y, S.FOOTER.format(when=footer_when, page=page, total=total)
            )
            if page > 1:
                self.setFillColor(HexColor(P.ACCENT))
                self.setFont(f.bold, 7)
                self.drawString(MARGIN, PAGE_H - MARGIN + 14, S.WORDMARK, charSpace=1.6)
                self.setFillColor(HexColor(P.MUTED))
                self.setFont(f.regular, 7)
                self.drawRightString(
                    PAGE_W - MARGIN, PAGE_H - MARGIN + 14, fit(header, 7, FRAME_W * 0.7)
                )
            self.restoreState()

    return NumberedCanvas
