"""Building blocks of :mod:`tokenvault.manager.store`: the event-row merge
model, SQL text and local time-bucket helpers. ``store.py`` keeps the
connection, locking, migration and write/read orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from tokenvault.common.models import UsageEvent

TOKEN_COLS = ("input", "output", "cache_read", "cache_5m", "cache_1h")
_EPOCH = date(1970, 1, 1)

# (table, time column) per grain; hour is UTC, day/month are local (store offset).
USAGE_TABLES = (("rollup_hour", "hour"), ("rollup_day", "day"), ("rollup_month", "month"))
TOOL_TABLES = (
    ("rollup_tool_hour", "hour"),
    ("rollup_tool_day", "day"),
    ("rollup_tool_month", "month"),
)

EVENT_COLS = (
    "device_id, msg_key, uuid, message_id, session_id, timestamp, ts, hour, model, "
    "reasoning_effort, sidechain, input, output, cache_read, cache_5m, cache_1h, project, "
    "session_title, title_ts, tool_names, account_email, email, account_uuid, windows_user, "
    "hostname, team_at_send"
)
N_EVENT_COLS = EVENT_COLS.count(",") + 1

_TOK_SUMS = ", ".join(f"SUM({c})" for c in TOKEN_COLS)
_SUM_UPDATE = ", ".join(f"{c} = {c} + excluded.{c}" for c in (*TOKEN_COLS, "events"))
_USAGE_COLS = "input, output, cache_read, cache_5m, cache_1h, events"


# --------------------------------------------------------------------- time
def local_day(hour: int, offset_min: int) -> int:
    """Local day number of a UTC hour (the day its start falls in)."""
    return (hour * 60 + offset_min) // 1440


def day_first_hour(day: int, offset_min: int) -> int:
    """First UTC hour whose start falls in local ``day``."""
    return -((offset_min - day * 1440) // 60)


def month_of_day(day: int) -> int:
    d = _EPOCH + timedelta(days=day)
    return d.year * 12 + d.month - 1


def month_first_day(month: int) -> int:
    return (date(month // 12, month % 12 + 1, 1) - _EPOCH).days


def month_label(month: int) -> str:
    return f"{month // 12:04d}-{month % 12 + 1:02d}"


# ---------------------------------------------------------------------- SQL
def upsert_usage_sql(table: str, tcol: str) -> str:
    return (
        f"INSERT INTO {table} (device_id, email, project, model, sidechain, {tcol}, "
        f"{_USAGE_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        f"ON CONFLICT(device_id, email, project, model, sidechain, {tcol}) "
        f"DO UPDATE SET {_SUM_UPDATE}"
    )


def upsert_tool_sql(table: str, tcol: str) -> str:
    return (
        f"INSERT INTO {table} (device_id, email, project, model, {tcol}, tool, calls) "
        f"VALUES (?,?,?,?,?,?,?) ON CONFLICT(device_id, email, project, model, {tcol}, tool) "
        "DO UPDATE SET calls = calls + excluded.calls"
    )


UPSERT_SESSION = (
    "INSERT INTO rollup_session (device_id, session_id, email, project, model, "
    "input, output, cache_read, cache_5m, cache_1h, sidechain_tokens, events, "
    "first_ts, last_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(device_id, session_id, email, project, model) DO UPDATE SET "
    f"{_SUM_UPDATE}, sidechain_tokens = sidechain_tokens + excluded.sidechain_tokens, "
    "first_ts = MIN(first_ts, excluded.first_ts), last_ts = MAX(last_ts, excluded.last_ts)"
)

UPSERT_TITLE = (
    "INSERT INTO session_meta (device_id, session_id, title, title_ts) VALUES (?,?,?,?) "
    "ON CONFLICT(device_id, session_id) DO UPDATE SET "
    "title = excluded.title, title_ts = excluded.title_ts "
    "WHERE excluded.title_ts >= session_meta.title_ts"
)

UPSERT_DEVICE_FROM_EVENTS = (
    "INSERT INTO devices (device_id, team, email, windows_user, hostname, last_seen) "
    "VALUES (?, ?, ?, ?, ?, 0) ON CONFLICT(device_id) DO UPDATE SET "
    "email = COALESCE(NULLIF(devices.email, ''), excluded.email), "
    "team = CASE WHEN devices.team = '' THEN excluded.team ELSE devices.team END, "
    "windows_user = CASE WHEN devices.windows_user = '' THEN excluded.windows_user "
    "ELSE devices.windows_user END, "
    "hostname = CASE WHEN devices.hostname = '' THEN excluded.hostname "
    "ELSE devices.hostname END"
)

# Rebuild the UTC-hour and session rollups from `events`.
REBUILD_FROM_EVENTS = f"""
DELETE FROM rollup_hour;
INSERT INTO rollup_hour (device_id, email, project, model, sidechain, hour, {_USAGE_COLS})
SELECT device_id, email, project, model, sidechain, hour, {_TOK_SUMS}, COUNT(*)
FROM events GROUP BY device_id, email, project, model, sidechain, hour;
DELETE FROM rollup_tool_hour;
INSERT INTO rollup_tool_hour (device_id, email, project, model, hour, tool, calls)
SELECT e.device_id, e.email, e.project, e.model, e.hour, j.value, COUNT(*)
FROM events e, json_each(e.tool_names) j
GROUP BY e.device_id, e.email, e.project, e.model, e.hour, j.value;
DELETE FROM rollup_session;
INSERT INTO rollup_session (device_id, session_id, email, project, model,
    input, output, cache_read, cache_5m, cache_1h, sidechain_tokens, events, first_ts, last_ts)
SELECT device_id, session_id, email, project, model, {_TOK_SUMS},
    SUM(CASE WHEN sidechain THEN input + output + cache_read + cache_5m + cache_1h ELSE 0 END),
    COUNT(*), MIN(ts), MAX(ts)
FROM events GROUP BY device_id, session_id, email, project, model;
"""


def rebuild_local_sql(offset_min: int) -> str:
    """Rebuild the local day/month rollups from the hourly ones for ``offset_min``."""
    off = int(offset_min)
    day = f"((hour * 60 + {off}) / 1440)"  # hours are positive: '/' == floor
    month = (
        f"(CAST(strftime('%Y', {day} * 86400, 'unixepoch') AS INTEGER) * 12 "
        f"+ CAST(strftime('%m', {day} * 86400, 'unixepoch') AS INTEGER) - 1)"
    )
    sums = f"{_TOK_SUMS}, SUM(events)"
    dims = "device_id, email, project, model, sidechain"
    tdims = "device_id, email, project, model"
    parts = []
    for table, expr in (("rollup_day", day), ("rollup_month", month)):
        tcol = table.split("_")[1]
        parts.append(
            f"DELETE FROM {table}; INSERT INTO {table} ({dims}, {tcol}, {_USAGE_COLS}) "
            f"SELECT {dims}, {expr} AS t, {sums} FROM rollup_hour GROUP BY {dims}, t;"
        )
    for table, expr in (("rollup_tool_day", day), ("rollup_tool_month", month)):
        tcol = table.split("_")[2]
        parts.append(
            f"DELETE FROM {table}; INSERT INTO {table} ({tdims}, {tcol}, tool, calls) "
            f"SELECT {tdims}, {expr} AS t, tool, SUM(calls) FROM rollup_tool_hour "
            f"GROUP BY {tdims}, t, tool;"
        )
    parts.append(
        "INSERT INTO meta (key, value) VALUES ('tz_offset_minutes', "
        f"'{off}') ON CONFLICT(key) DO UPDATE SET value = excluded.value;"
    )
    return "\n".join(parts)


# v1 -> v2: frozen identity (backfilled once from the device's current e-mail),
# title timestamps, raw account e-mail, local day/month and tool rollups.
MIGRATE_V1_COLUMNS = """
ALTER TABLE events ADD COLUMN account_email TEXT;
ALTER TABLE events ADD COLUMN title_ts REAL;
ALTER TABLE session_meta ADD COLUMN title_ts REAL NOT NULL DEFAULT 0;
"""
MIGRATE_V1_BACKFILL = f"""
UPDATE events SET account_email = NULLIF(email, '');
UPDATE events SET title_ts = ts WHERE session_title IS NOT NULL;
UPDATE session_meta SET title_ts = COALESCE(
    (SELECT MAX(e.title_ts) FROM events e WHERE e.device_id = session_meta.device_id
        AND e.session_id = session_meta.session_id AND e.session_title = session_meta.title),
    (SELECT MAX(e.ts) FROM events e WHERE e.device_id = session_meta.device_id
        AND e.session_id = session_meta.session_id), 0);
UPDATE events SET email = COALESCE((SELECT d.email FROM devices d
    WHERE d.device_id = events.device_id AND d.email <> ''), '') WHERE email = '';
{REBUILD_FROM_EVENTS}
"""


def parse_ts(ts: str) -> float:
    """ISO-8601 -> UTC epoch seconds. A naive timestamp is taken as UTC."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


@dataclass
class Row:
    """One event row; ``absorb`` merges a duplicate (larger usage, tool union,
    title of the highest-timestamp copy)."""

    key: str
    uuid: str
    message_id: str | None
    session_id: str
    timestamp: str
    ts: float
    hour: int
    model: str
    effort: str | None
    sidechain: int
    tokens: list[int]
    project: str
    title: str | None
    title_ts: float | None
    tools: list[str]
    account_email: str | None
    email: str
    account_uuid: str | None
    windows_user: str
    hostname: str
    team: str

    @classmethod
    def from_event(cls, e: UsageEvent) -> Row:
        ts = parse_ts(e.timestamp)
        tools: list[str] = []
        for t in e.tool_names:
            if t not in tools:
                tools.append(t)
        return cls(
            key=e.message_id or e.uuid,
            uuid=e.uuid,
            message_id=e.message_id,
            session_id=e.session_id,
            timestamp=e.timestamp,
            ts=ts,
            hour=int(ts // 3600),
            model=e.model or "",
            effort=e.reasoning_effort,
            sidechain=1 if e.is_sidechain else 0,
            tokens=[
                e.input_tokens,
                e.output_tokens,
                e.cache_read_tokens,
                e.cache_create_5m_tokens,
                e.cache_create_1h_tokens,
            ],
            project=e.project,
            title=e.session_title,
            title_ts=ts if e.session_title is not None else None,
            tools=tools,
            account_email=e.claude_account_email or None,
            email=e.claude_account_email or "",
            account_uuid=e.account_uuid,
            windows_user=e.windows_user,
            hostname=e.hostname,
            team=e.team,
        )

    def absorb(self, other: Row) -> None:
        """Merge a duplicate into this row (this row's dimensions are kept)."""
        self.tokens = [max(a, b) for a, b in zip(self.tokens, other.tokens, strict=True)]
        self.tools = self.tools + [t for t in other.tools if t not in self.tools]
        if other.title is not None and other.title_ts is not None:
            if self.title_ts is None or other.title_ts >= self.title_ts:
                self.title, self.title_ts = other.title, other.title_ts
