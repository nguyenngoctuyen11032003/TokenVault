"""Exceptions raised by the PDF report generator (clean, user-facing messages)."""

from __future__ import annotations


class ReportError(Exception):
    """Base class: the report could not be produced; ``str(exc)`` is user-facing."""


class ReportSpecError(ReportError, ValueError):
    """The :class:`ReportSpec` is invalid (bad field, range, or option)."""


class ReportEmptyScopeError(ReportError):
    """The requested employee key or team does not exist at all."""


class ReportFontError(ReportError):
    """No embeddable font able to render Vietnamese was found."""
