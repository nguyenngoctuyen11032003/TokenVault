"""What to put in a report (:class:`ReportSpec`), range resolution and output file naming."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from tokenvault.manager.reports import strings_vi as S
from tokenvault.manager.reports.errors import ReportSpecError

Scope = Literal["all", "employee", "team"]
RangeName = Literal["today", "7d", "30d", "custom"]
RANGE_DAYS = {"today": 1, "7d": 7, "30d": 30}
MAX_SPAN_DAYS = 366
MAX_EMPLOYEE_KEYS = 200
MAX_NAME = 40
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10))}
_RESERVED |= {f"LPT{i}" for i in range(1, 10)}


@dataclass(frozen=True)
class ReportSpec:
    """Options chosen in the export dialog. Call :meth:`validate` before use.

    ``employee_keys`` are :attr:`EmployeeRow.key` values (e-mail or ``device:<id>``);
    ``start``/``end`` are local days ``YYYY-MM-DD`` (inclusive), only for ``custom``.
    """

    scope: Scope = "all"
    employee_keys: list[str] = field(default_factory=list)
    team: str | None = None
    range: RangeName = "7d"
    start: str | None = None
    end: str | None = None
    per_employee_pages: bool = True
    include_charts: bool = True
    include_sessions: bool = True
    include_session_titles: bool = True
    top_n: int = 15
    language: Literal["vi"] = "vi"

    def validate(self) -> ReportSpec:
        """Return ``self`` or raise :class:`ReportSpecError` naming the bad field."""
        if self.scope not in ("all", "employee", "team"):
            raise ReportSpecError(f"Phạm vi không hợp lệ / invalid scope: {self.scope!r}")
        keys = self.employee_keys
        if not isinstance(keys, (list, tuple)) or not all(
            isinstance(k, str) and k.strip() for k in keys
        ):
            raise ReportSpecError("employee_keys phải là danh sách chuỗi không rỗng")
        if len(set(keys)) != len(keys):
            raise ReportSpecError("employee_keys bị trùng / duplicate employee keys")
        if self.scope == "employee" and not keys:
            raise ReportSpecError("Chọn ít nhất một nhân viên / pick at least one employee")
        if len(keys) > MAX_EMPLOYEE_KEYS:
            raise ReportSpecError(f"Tối đa {MAX_EMPLOYEE_KEYS} nhân viên mỗi báo cáo")
        if self.scope == "team" and (not isinstance(self.team, str) or not self.team.strip()):
            raise ReportSpecError("Chọn một nhóm / scope 'team' needs a team name")
        if self.range not in ("today", "7d", "30d", "custom"):
            raise ReportSpecError(f"Khoảng thời gian không hợp lệ / invalid range: {self.range!r}")
        if self.range == "custom":
            s, e = _parse_day(self.start, "start"), _parse_day(self.end, "end")
            if s > e:
                raise ReportSpecError("Ngày bắt đầu phải trước ngày kết thúc / start > end")
            if (e - s).days + 1 > MAX_SPAN_DAYS:
                raise ReportSpecError(f"Khoảng tối đa {MAX_SPAN_DAYS} ngày / span too long")
        elif self.start is not None or self.end is not None:
            raise ReportSpecError("start/end chỉ dùng với range 'custom'")
        for name in (
            "per_employee_pages",
            "include_charts",
            "include_sessions",
            "include_session_titles",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ReportSpecError(f"{name} phải là true/false")
        top = self.top_n
        if isinstance(top, bool) or not isinstance(top, int) or not 1 <= top <= 100:
            raise ReportSpecError("top_n phải trong khoảng 1..100")
        if self.language != "vi":
            raise ReportSpecError(f"Ngôn ngữ chưa hỗ trợ / unsupported language: {self.language!r}")
        return self


def _parse_day(value: str | None, name: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ReportSpecError(f"{name} phải có dạng YYYY-MM-DD (range 'custom')")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ReportSpecError(f"{name} không phải ngày hợp lệ: {value!r}") from None


def local_now(now: datetime, tz_offset_minutes: int) -> datetime:
    """``now`` in the Manager's fixed offset (naive values are taken as already local)."""
    tz = timezone(timedelta(minutes=tz_offset_minutes))
    return now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)


def resolve_range(spec: ReportSpec, today: date) -> tuple[date, date]:
    """Inclusive local [first, last] days of the report; ``today`` is the server's local day."""
    if spec.range == "custom":
        return _parse_day(spec.start, "start"), _parse_day(spec.end, "end")
    return today - timedelta(days=RANGE_DAYS[spec.range] - 1), today


def range_label(spec: ReportSpec, first: date, last: date) -> str:
    """'01/11/2026 – 19/11/2026 (7 ngày gần nhất)'."""
    if spec.range == "today":
        return f"{S.fmt_day(last)} ({S.RANGE_TODAY})"
    span = f"{S.fmt_day(first)} – {S.fmt_day(last)}"
    if spec.range == "custom":
        return span
    return f"{span} ({S.RANGE_LAST.format(n=RANGE_DAYS[spec.range])})"


def safe_file_part(text: str, fallback: str = "BaoCao") -> str:
    """Windows-filename-safe fragment (same rules as ``packager.safe_team_name``)."""
    text = unicodedata.normalize("NFC", text).strip()
    out: list[str] = []
    for c in text:
        if c in " -_" or unicodedata.category(c)[0] in "LNM":
            out.append(c)
        elif not out or out[-1] != "_":
            out.append("_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    s = s[:MAX_NAME].rstrip(". _")
    if not s:
        return fallback
    if s.upper() in _RESERVED:
        s += "_"
    return s


def _scope_part(spec: ReportSpec) -> str:
    base = S.SCOPE_FILE[spec.scope]
    if spec.scope == "team" and spec.team:
        return f"{base}-{safe_file_part(spec.team, 'Team')}"
    if spec.scope == "employee" and len(spec.employee_keys) == 1:
        return f"{base}-{safe_file_part(spec.employee_keys[0], 'NV')}"
    if spec.scope == "employee":
        return f"{base}-{len(spec.employee_keys)}"
    return base


def unique_report_path(directory: Path, spec: ReportSpec, now: datetime) -> Path:
    """``<dir>/TokenVault-BaoCao-<Scope>-<YYYYMMDD-HHMM>.pdf``, with `` (2)``... if taken."""
    stem = f"TokenVault-BaoCao-{_scope_part(spec)}-{now:%Y%m%d-%H%M}"
    candidate = Path(directory) / f"{stem}.pdf"
    n = 2
    while candidate.exists():
        candidate = Path(directory) / f"{stem} ({n}).pdf"
        n += 1
    return candidate
