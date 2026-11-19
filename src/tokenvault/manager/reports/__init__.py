"""PDF usage reports (Task 21): ``build_report(aggregates, ReportSpec(...), path, now=...)``."""

from tokenvault.manager.reports.errors import (
    ReportEmptyScopeError,
    ReportError,
    ReportFontError,
    ReportSpecError,
)
from tokenvault.manager.reports.pdf_builder import PAGE_CAP, ReportResult, build_report
from tokenvault.manager.reports.spec import ReportSpec, unique_report_path

__all__ = [
    "PAGE_CAP",
    "ReportEmptyScopeError",
    "ReportError",
    "ReportFontError",
    "ReportResult",
    "ReportSpec",
    "ReportSpecError",
    "build_report",
    "unique_report_path",
]
