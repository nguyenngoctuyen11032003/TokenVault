"""Plain result types returned by :class:`tokenvault.manager.aggregates.Aggregates`.

Everything here is JSON-serialisable via ``dataclasses.asdict``: costs are
``float`` USD (rounded to 6 places) or ``None`` when UNKNOWN, timestamps are
ISO-8601 strings in the Manager's local offset, days are ``"YYYY-MM-DD"``.
"Total tokens" always means input + output + cache_read + cache_5m + cache_1h.
Cost semantics everywhere: ``cost`` sums the priced models; ``cost_partial`` is
True when some tokens are from unpriced models (listed in
``unknown_cost_models``); ``cost`` is ``None`` when there are tokens but none
from a priced model, and ``0.0`` for an empty group. A missing model is shown
as ``"(no model)"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Filter:
    """Global UI filter; every field optional.

    ``model`` uses the displayed label (``"(no model)"`` for events without one).
    ``email`` is an employee key: an account e-mail, or ``"device:<id>"`` for
    a device with no known account (see ``EmployeeRow.key``).
    ``start``/``end`` accept ``datetime`` or ISO strings. A date-only string is
    a local day: ``start`` is that day's local midnight, ``end`` is inclusive
    (the whole day). Datetimes are ``start`` inclusive / ``end`` exclusive,
    naive values are local time. Bounds are applied at hour granularity.
    """

    team: str | None = None
    email: str | None = None
    project: str | None = None
    model: str | None = None
    start: str | datetime | None = None
    end: str | datetime | None = None


@dataclass
class Tokens:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_5m_tokens: int = 0
    cache_create_1h_tokens: int = 0
    total_tokens: int = 0


@dataclass
class Totals(Tokens):
    events: int = 0
    sessions: int = 0
    cost: float | None = 0.0
    cost_partial: bool = False  # True when some tokens are from UNKNOWN-priced models


@dataclass
class HourPoint(Tokens):
    hour: str = ""  # local "YYYY-MM-DDTHH:00"
    cost: float | None = 0.0
    cost_partial: bool = False
    unknown_cost_models: list[str] = field(default_factory=list)


@dataclass
class DayPoint(Tokens):
    day: str = ""
    cost: float | None = 0.0
    cost_partial: bool = False
    unknown_cost_models: list[str] = field(default_factory=list)


@dataclass
class MonthPoint(Tokens):
    month: str = ""  # local "YYYY-MM"
    cost: float | None = 0.0
    cost_partial: bool = False
    unknown_cost_models: list[str] = field(default_factory=list)


@dataclass
class CacheDayPoint:
    day: str
    cache_read_tokens: int
    cache_create_5m_tokens: int
    cache_create_1h_tokens: int
    hit_ratio: float  # cache_read / (input + cache_read + cache_create), 0 when empty


@dataclass
class ProjectRow(Tokens):
    project: str = ""
    events: int = 0
    sessions: int = 0
    employees: int = 0
    cost: float | None = 0.0
    cost_partial: bool = False
    unknown_cost_models: list[str] = field(default_factory=list)
    last_active: str | None = None


@dataclass
class ModelRow(Tokens):
    model: str = ""  # "(no model)" when the event had none
    events: int = 0
    cost: float | None = 0.0  # None = UNKNOWN price


@dataclass
class ToolCount:
    tool: str
    calls: int  # number of responses that used the tool


@dataclass
class SessionRow(Tokens):
    session_id: str = ""
    device_id: str = ""
    title: str | None = None
    project: str = ""  # project with most tokens in the session
    projects: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    employee_key: str = ""
    display_name: str = ""
    events: int = 0
    sidechain_tokens: int = 0
    cost: float | None = 0.0
    cost_partial: bool = False
    started_at: str = ""
    last_at: str = ""


@dataclass
class Overview:
    totals: Totals
    hourly: list[HourPoint]
    daily: list[DayPoint]  # at most the last 90 local days of the range
    monthly: list[MonthPoint]  # whole range, local months
    cache_daily: list[CacheDayPoint]
    by_project: list[ProjectRow]
    by_model: list[ModelRow]
    top_tools: list[ToolCount]
    recent_sessions: list[SessionRow]
    unknown_cost_models: list[str]


@dataclass
class EmployeeRow(Tokens):
    key: str = ""  # e-mail, or "device:<id>" for an unknown account
    email: str | None = None
    display_name: str = ""
    devices: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)
    online: bool = False
    last_seen: str | None = None
    events: int = 0
    sessions: int = 0
    cost: float | None = 0.0
    cost_partial: bool = False
    share: float = 0.0  # fraction of total tokens among the listed employees


@dataclass
class DeviceInfo:
    device_id: str
    team: str
    windows_user: str
    hostname: str
    online: bool
    last_seen: str | None


@dataclass
class EmployeeView:
    key: str
    email: str | None
    display_name: str
    range: str
    totals: Totals
    hourly: list[HourPoint]
    daily: list[DayPoint]  # at most the last 90 local days of the range
    monthly: list[MonthPoint]  # whole range, local months
    by_project: list[ProjectRow]
    by_model: list[ModelRow]
    sessions: list[SessionRow]
    devices: list[DeviceInfo]
    unknown_cost_models: list[str]


@dataclass
class Turn(Tokens):
    timestamp: str = ""
    device_id: str = ""
    model: str = ""
    reasoning_effort: str | None = None
    is_sidechain: bool = False
    tool_names: list[str] = field(default_factory=list)
    cost: float | None = 0.0


@dataclass
class DayCount:
    day: str
    count: int


@dataclass
class EmployeeCount:
    key: str
    display_name: str
    count: int


@dataclass
class SkillsView:
    range: str
    total_invocations: int
    daily: list[DayCount]
    by_employee: list[EmployeeCount]
    note: str


@dataclass
class ModelTokens:
    model: str
    tokens: int


@dataclass
class AgentRow:
    session_id: str
    device_id: str
    title: str | None
    display_name: str
    sidechain_tokens: int


@dataclass
class OrchestrationView:
    range: str
    main_tokens: int
    sidechain_tokens: int
    main_by_model: list[ModelTokens]
    sidechain_by_model: list[ModelTokens]
    agents_by_tokens: list[AgentRow]
