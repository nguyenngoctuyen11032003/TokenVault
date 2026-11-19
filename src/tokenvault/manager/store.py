"""Manager SQLite store: raw events, devices and pre-aggregated rollups.

Design
------
* ``events`` holds one row per assistant response keyed by
  ``(device_id, message_id or uuid)``. A duplicate keeps, per token field, the
  larger value; ``tool_names`` are unioned in first-seen order; the latest
  non-null ``session_title`` received wins. The dimensions of a row (time,
  project, model, e-mail, sidechain) are fixed by the first copy received.
* Rollups (``rollup_hour``, ``rollup_day``, ``rollup_tool_hour``,
  ``rollup_session``) are updated in the same transaction by the *delta* a row
  gained, so their sums always equal the raw sums.
* Team (and the fallback e-mail) live on ``devices`` and are joined at query
  time, so re-assigning a device's team applies to its old rows too.
* Cost is never stored.

Thread safety
-------------
One ``sqlite3`` connection (``check_same_thread=False``) guarded by a single
``threading.RLock``: every public method holds the lock for its whole
duration, so writes are serialised and a reader never sees a half-applied
upsert (each upsert is one ``BEGIN IMMEDIATE`` transaction). The database runs
in WAL mode with ``synchronous=NORMAL`` so outside readers do not block it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from tokenvault.common.models import UsageEvent

SCHEMA_VERSION = 1
TOKEN_COLS = ("input", "output", "cache_read", "cache_5m", "cache_1h")
_CHUNK = 500


@dataclass(frozen=True)
class DeviceRow:
    device_id: str
    team: str
    email: str | None
    windows_user: str
    hostname: str
    last_seen: float


def parse_ts(ts: str) -> float:
    """ISO-8601 -> UTC epoch seconds. A naive timestamp is taken as UTC."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


@dataclass
class _Row:
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
    tools: list[str]
    email: str
    account_uuid: str | None
    windows_user: str
    hostname: str
    team: str

    @classmethod
    def from_event(cls, e: UsageEvent) -> _Row:
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
            tools=tools,
            email=e.claude_account_email or "",
            account_uuid=e.account_uuid,
            windows_user=e.windows_user,
            hostname=e.hostname,
            team=e.team,
        )

    def absorb(self, other: _Row) -> None:
        """Merge a duplicate into this row (this row's dimensions are kept)."""
        self.tokens = [max(a, b) for a, b in zip(self.tokens, other.tokens, strict=True)]
        self.tools = self.tools + [t for t in other.tools if t not in self.tools]
        if other.title is not None:
            self.title = other.title


_EVENT_COLS = (
    "device_id, msg_key, uuid, message_id, session_id, timestamp, ts, hour, model, "
    "reasoning_effort, sidechain, input, output, cache_read, cache_5m, cache_1h, project, "
    "session_title, tool_names, email, account_uuid, windows_user, hostname, team_at_send"
)
_SUM_UPDATE = ", ".join(f"{c} = {c} + excluded.{c}" for c in (*TOKEN_COLS, "events"))


def _load_schema() -> str:
    return resources.files("tokenvault.manager").joinpath("schema.sql").read_text("utf-8")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None, timeout=10
        )
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.execute("PRAGMA busy_timeout=10000")
        self._migrate()

    # ------------------------------------------------------------------ setup
    def _migrate(self) -> None:
        migrations: dict[int, str] = {1: _load_schema()}
        with self._lock:
            ver = int(self._con.execute("PRAGMA user_version").fetchone()[0])
            for target in range(ver + 1, SCHEMA_VERSION + 1):
                self._con.executescript(
                    f"BEGIN;\n{migrations[target]}\nPRAGMA user_version={target};\nCOMMIT;"
                )

    def close(self) -> None:
        with self._lock:
            self._con.close()

    # ------------------------------------------------------------------ reads
    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Run a read-only query under the store lock (used by Aggregates)."""
        with self._lock:
            return self._con.execute(sql, tuple(params)).fetchall()

    def list_devices(self) -> list[DeviceRow]:
        rows = self.fetchall(
            "SELECT device_id, team, email, windows_user, hostname, last_seen "
            "FROM devices ORDER BY device_id"
        )
        return [DeviceRow(*tuple(r)) for r in rows]

    def devices_online(self, now: float, window_s: float = 30) -> list[str]:
        rows = self.fetchall(
            "SELECT device_id FROM devices WHERE last_seen >= ? ORDER BY device_id",
            (now - window_s,),
        )
        return [str(r[0]) for r in rows]

    # ---------------------------------------------------------------- devices
    def upsert_device(
        self,
        device_id: str,
        team: str | None,
        email: str | None,
        last_seen: float,
        windows_user: str | None = None,
        hostname: str | None = None,
    ) -> None:
        """Insert or refresh a device.

        ``team`` is only applied when the device is new or has no team yet, so
        an adapter reconnecting with its installer team never overrides a
        Manager-side :meth:`set_device_team`. ``email``/``windows_user``/
        ``hostname`` overwrite when given (non-empty); ``last_seen`` only grows.
        """
        with self._lock:
            self._con.execute(
                "INSERT INTO devices (device_id, team, email, windows_user, hostname, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET "
                "team = CASE WHEN devices.team = '' THEN excluded.team ELSE devices.team END, "
                "email = COALESCE(NULLIF(excluded.email, ''), devices.email), "
                "windows_user = COALESCE(NULLIF(excluded.windows_user, ''), "
                "devices.windows_user), "
                "hostname = COALESCE(NULLIF(excluded.hostname, ''), devices.hostname), "
                "last_seen = MAX(devices.last_seen, excluded.last_seen)",
                (device_id, team or "", email, windows_user or "", hostname or "", last_seen),
            )

    def set_device_team(self, device_id: str, team: str) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO devices (device_id, team) VALUES (?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET team = excluded.team",
                (device_id, team),
            )

    # ----------------------------------------------------------------- events
    def upsert_events(self, device_id: str, events: Iterable[UsageEvent]) -> int:
        """Upsert a batch for ``device_id``; return the number of new-or-grown rows.

        A row "grows" when any token field increases or it gains a tool name.
        The whole batch is one transaction (raw rows + all rollups).
        Raises ``ValueError`` on an unparsable timestamp (nothing is written).
        """
        batch: dict[str, _Row] = {}
        titles: dict[str, str] = {}
        last: _Row | None = None
        for e in events:
            row = _Row.from_event(e)
            last = row
            if row.title is not None:
                titles[row.session_id] = row.title
            if row.key in batch:
                batch[row.key].absorb(row)
            else:
                batch[row.key] = row
        if last is None:
            return 0

        with self._lock:
            con = self._con
            con.execute("BEGIN IMMEDIATE")
            try:
                n = self._apply(device_id, batch, titles, last)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return n

    def _existing(self, device_id: str, keys: list[str]) -> dict[str, _Row]:
        out: dict[str, _Row] = {}
        for i in range(0, len(keys), _CHUNK):
            part = keys[i : i + _CHUNK]
            marks = ",".join("?" * len(part))
            for r in self._con.execute(
                f"SELECT {_EVENT_COLS} FROM events WHERE device_id = ? AND msg_key IN ({marks})",
                (device_id, *part),
            ):
                out[r["msg_key"]] = _Row(
                    key=r["msg_key"],
                    uuid=r["uuid"],
                    message_id=r["message_id"],
                    session_id=r["session_id"],
                    timestamp=r["timestamp"],
                    ts=r["ts"],
                    hour=r["hour"],
                    model=r["model"],
                    effort=r["reasoning_effort"],
                    sidechain=r["sidechain"],
                    tokens=[r[c] for c in TOKEN_COLS],
                    project=r["project"],
                    title=r["session_title"],
                    tools=json.loads(r["tool_names"]),
                    email=r["email"],
                    account_uuid=r["account_uuid"],
                    windows_user=r["windows_user"],
                    hostname=r["hostname"],
                    team=r["team_at_send"],
                )
        return out

    def _apply(
        self, device_id: str, batch: dict[str, _Row], titles: dict[str, str], last: _Row
    ) -> int:
        con = self._con
        existing = self._existing(device_id, list(batch))
        inserts: list[tuple[Any, ...]] = []
        updates: list[tuple[Any, ...]] = []
        hour_acc: dict[tuple[Any, ...], list[int]] = {}
        day_acc: dict[tuple[Any, ...], list[int]] = {}
        tool_acc: dict[tuple[Any, ...], int] = {}
        sess_acc: dict[tuple[Any, ...], list[Any]] = {}
        grown = 0

        for key, inc in batch.items():
            old = existing.get(key)
            if old is None:
                row, delta, new_tools, n_ev = inc, list(inc.tokens), list(inc.tools), 1
                inserts.append(
                    (device_id, key, row.uuid, row.message_id, row.session_id, row.timestamp,
                     row.ts, row.hour, row.model, row.effort, row.sidechain, *row.tokens,
                     row.project, row.title, json.dumps(row.tools, ensure_ascii=False),
                     row.email, row.account_uuid, row.windows_user, row.hostname, row.team)
                )  # fmt: skip
            else:
                before_tokens, before_tools, before_title = old.tokens, old.tools, old.title
                old.absorb(inc)
                row = old
                delta = [a - b for a, b in zip(row.tokens, before_tokens, strict=True)]
                new_tools = row.tools[len(before_tools) :]
                n_ev = 0
                if not any(delta) and not new_tools and row.title == before_title:
                    continue
                updates.append(
                    (*row.tokens, json.dumps(row.tools, ensure_ascii=False), row.title,
                     device_id, key)
                )  # fmt: skip
            if n_ev or any(delta) or new_tools:
                grown += 1
            if not n_ev and not any(delta) and not new_tools:
                continue
            dims = (row.email, row.project, row.model)
            for acc, k in (
                (hour_acc, (*dims, row.sidechain, row.hour)),
                (day_acc, (*dims, row.sidechain, row.hour // 24)),
            ):
                cur = acc.setdefault(k, [0] * 6)
                for i, d in enumerate(delta):
                    cur[i] += d
                cur[5] += n_ev
            for t in new_tools:
                tk = (*dims, row.hour, t)
                tool_acc[tk] = tool_acc.get(tk, 0) + 1
            sk = (row.session_id, *dims)
            s = sess_acc.setdefault(sk, [0, 0, 0, 0, 0, 0, 0, row.ts, row.ts])
            for i, d in enumerate(delta):
                s[i] += d
            s[5] += sum(delta) if row.sidechain else 0
            s[6] += n_ev
            s[7] = min(s[7], row.ts)
            s[8] = max(s[8], row.ts)

        if inserts:
            con.executemany(
                f"INSERT INTO events ({_EVENT_COLS}) VALUES ({','.join('?' * 24)})", inserts
            )
        if updates:
            con.executemany(
                "UPDATE events SET input=?, output=?, cache_read=?, cache_5m=?, cache_1h=?, "
                "tool_names=?, session_title=? WHERE device_id=? AND msg_key=?",
                updates,
            )
        cols = "input, output, cache_read, cache_5m, cache_1h, events"
        con.executemany(
            "INSERT INTO rollup_hour (device_id, email, project, model, sidechain, hour, "
            f"{cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(device_id, email, project, model, sidechain, hour) "
            f"DO UPDATE SET {_SUM_UPDATE}",
            [(device_id, *k, *v) for k, v in hour_acc.items()],
        )
        con.executemany(
            "INSERT INTO rollup_day (device_id, email, project, model, sidechain, day, "
            f"{cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(device_id, email, project, model, sidechain, day) "
            f"DO UPDATE SET {_SUM_UPDATE}",
            [(device_id, *k, *v) for k, v in day_acc.items()],
        )
        con.executemany(
            "INSERT INTO rollup_tool_hour (device_id, email, project, model, hour, tool, calls) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(device_id, email, project, model, hour, tool) "
            "DO UPDATE SET calls = calls + excluded.calls",
            [(device_id, *k, v) for k, v in tool_acc.items()],
        )
        con.executemany(
            "INSERT INTO rollup_session (device_id, session_id, email, project, model, "
            "input, output, cache_read, cache_5m, cache_1h, sidechain_tokens, events, "
            "first_ts, last_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(device_id, session_id, email, project, model) DO UPDATE SET "
            f"{_SUM_UPDATE}, sidechain_tokens = sidechain_tokens + excluded.sidechain_tokens, "
            "first_ts = MIN(first_ts, excluded.first_ts), "
            "last_ts = MAX(last_ts, excluded.last_ts)",
            [(device_id, *k, *v) for k, v in sess_acc.items()],
        )
        con.executemany(
            "INSERT INTO session_meta (device_id, session_id, title) VALUES (?,?,?) "
            "ON CONFLICT(device_id, session_id) DO UPDATE SET title = excluded.title",
            [(device_id, sid, t) for sid, t in titles.items()],
        )
        # Make sure the device exists (team/e-mail from the events until the
        # Manager records better data via upsert_device / set_device_team).
        con.execute(
            "INSERT INTO devices (device_id, team, email, windows_user, hostname, last_seen) "
            "VALUES (?, ?, ?, ?, ?, 0) ON CONFLICT(device_id) DO UPDATE SET "
            "email = COALESCE(NULLIF(devices.email, ''), excluded.email), "
            "team = CASE WHEN devices.team = '' THEN excluded.team ELSE devices.team END, "
            "windows_user = CASE WHEN devices.windows_user = '' THEN excluded.windows_user "
            "ELSE devices.windows_user END, "
            "hostname = CASE WHEN devices.hostname = '' THEN excluded.hostname "
            "ELSE devices.hostname END",
            (device_id, last.team, last.email or None, last.windows_user, last.hostname),
        )
        return grown
