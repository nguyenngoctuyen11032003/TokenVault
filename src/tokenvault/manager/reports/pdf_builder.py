"""Build the PDF report: gather data, lay out the story, write atomically.

Public entry point: :func:`build_report`. The file is written to a temporary file in
the destination folder and moved into place with ``os.replace``; on any failure the
temporary file is removed and nothing appears at ``out_path``.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate

from tokenvault.manager.aggregates import Aggregates
from tokenvault.manager.reports import fonts, sections
from tokenvault.manager.reports import strings_vi as S
from tokenvault.manager.reports.data import ReportData, gather
from tokenvault.manager.reports.errors import ReportError, ReportSpecError
from tokenvault.manager.reports.layout import MARGIN, PAGE_H, PAGE_W, CoverBand, numbered_canvas
from tokenvault.manager.reports.spec import ReportSpec, local_now

Progress = Callable[[float, str], None]
PAGE_CAP = 120  # 60 employee pages + summary/tables; enforced by data limits, checked in tests


@dataclass(frozen=True)
class ReportResult:
    path: Path
    pages: int
    bytes: int
    scope_label: str
    range_label: str


class _Doc(BaseDocTemplate):  # type: ignore[misc]
    def __init__(self, path: str, on_flowable: Callable[[], None], **kw: Any) -> None:
        super().__init__(path, **kw)
        self._on_flowable = on_flowable

    def afterFlowable(self, flowable: Flowable) -> None:  # noqa: N802 - reportlab API
        self._on_flowable()


def _pdf_date(now: datetime) -> str:
    off = now.utcoffset()
    minutes = int(off.total_seconds() // 60) if off is not None else 0
    sign = "+" if minutes >= 0 else "-"
    return f"D:{now:%Y%m%d%H%M%S}{sign}{abs(minutes) // 60:02d}'{abs(minutes) % 60:02d}'"


def _story(data: ReportData, manager_name: str) -> list[Flowable]:
    spec = data.spec
    meta = [
        (S.LBL_RANGE, data.range_label),
        (S.LBL_GENERATED, S.fmt_dt(data.now)),
        (S.LBL_MANAGER, manager_name or S.NONE),
    ]
    single = data.single
    subtitle = ""
    if single and data.ranking_all:
        row = data.ranking_all[0]
        email = row.email if row.email and row.email != data.scope_label else ""
        subtitle = " · ".join(x for x in (email, ", ".join(row.teams)) if x)
    story: list[Flowable] = [CoverBand(data.scope_label, meta, subtitle)]
    story += sections.summary(data)
    if spec.include_charts:
        story += sections.chart_block(data)
    story += sections.tables(data)
    if not single:
        for sec in data.sections:
            story += sections.employee_section(sec, data)
    story += sections.about(data)
    return story


def build_report(
    aggregates: Aggregates,
    spec: ReportSpec,
    out_path: Path,
    *,
    now: datetime,
    manager_name: str,
    tz_offset_minutes: int | None = None,
    progress: Progress | None = None,
) -> ReportResult:
    """Write the PDF for ``spec`` to ``out_path`` (parent folders created, replaced atomically).

    ``now`` is the generation time (naive = Manager local time); days and "today" use the
    Aggregates' fixed UTC offset. ``tz_offset_minutes``, when given, must equal it.
    ``progress(fraction, message)`` receives non-decreasing fractions from 0 to 1.
    Raises :class:`ReportError` subclasses only.
    """
    last = [0.0]

    def tick(frac: float, msg: str) -> None:
        frac = min(1.0, max(last[0], frac))
        last[0] = frac
        if progress is not None:
            progress(frac, msg)

    if tz_offset_minutes is not None and tz_offset_minutes != aggregates.tz_offset_minutes:
        raise ReportSpecError("tz_offset_minutes khác với Aggregates / offset mismatch")
    try:
        spec.validate()
    except ReportSpecError:
        raise
    except Exception as exc:  # malformed field types
        raise ReportSpecError(f"Tuỳ chọn báo cáo không hợp lệ / invalid spec: {exc}") from None
    fonts.ensure_fonts()
    out_path = Path(out_path)
    tmp: Path | None = None
    try:
        data = gather(aggregates, spec, now, tick)
        story = _story(data, manager_name)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_name(f".{out_path.stem}.{secrets.token_hex(4)}.tmp")
        pages: list[int] = []
        n_flow = max(1, len(story))
        done = [0]

        def on_flowable() -> None:
            done[0] += 1
            tick(0.6 + 0.35 * min(1.0, done[0] / n_flow), "Đang dựng trang")

        doc = _Doc(
            str(tmp),
            on_flowable,
            pagesize=(PAGE_W, PAGE_H),
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            topMargin=MARGIN,
            bottomMargin=MARGIN,
            title=f"{S.TITLE} – {data.range_label}",
            author="TokenVault",
            subject=S.PDF_SUBJECT,
            creator="TokenVault",
            producer="TokenVault",
            keywords="",
            invariant=1,
            lang="vi-VN",
        )
        frame = Frame(
            MARGIN,
            MARGIN,
            PAGE_W - 2 * MARGIN,
            PAGE_H - 2 * MARGIN,
            id="body",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        doc.addPageTemplates([PageTemplate(id="page", frames=[frame])])
        local = local_now(now, aggregates.tz_offset_minutes)
        canvas_cls = numbered_canvas(
            S.fmt_dt(local), f"{S.TITLE} · {data.scope_label}", pages, _pdf_date(local)
        )
        doc.build(story, canvasmaker=canvas_cls)
        size = tmp.stat().st_size
        os.replace(tmp, out_path)
        tmp = None
        tick(1.0, "Hoàn tất")
        return ReportResult(
            out_path, pages[-1] if pages else 0, size, data.scope_label, data.range_label
        )
    except ReportError:
        raise
    except OSError as exc:
        detail = exc.strerror or exc
        raise ReportError(
            f"Không ghi được tệp báo cáo / cannot write the report: {detail}"
        ) from None
    except Exception as exc:
        raise ReportError(
            f"Không tạo được báo cáo / report failed: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
