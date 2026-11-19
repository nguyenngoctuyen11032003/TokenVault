"""Manager SQLite store: raw events, devices and pre-aggregated rollups.

Design
------
* ``events`` holds one row per assistant response keyed by
  ``(device_id, message_id or uuid)``. A duplicate keeps, per token field, the
  larger value; ``tool_names`` are unioned in first-seen order; the session
  title carried by the copy with the highest event timestamp wins (ties: the
  latest received). The dimensions of a row (time, project, model, e-mail,
  sidechain) are fixed by the first copy received.
* **Employee identity is frozen at ingest**: ``events.email`` = the event's
  account e-mail, else the device's e-mail at that moment, else ``''`` (no
  account; ``''`` rather than NULL because it is part of rollup primary keys).
  A later change of the device e-mail never re-attributes history. Only the
  device *team* is joined at query time (so team re-assignment is retroactive).
* Rollups (``rollup_hour``, ``rollup_day``, ``rollup_tool_hour``,
  ``rollup_session``) are updated in the same transaction by the *delta* a row
  gained, so their sums always equal the raw sums.
* Cost is never stored.

Concurrency
-----------
* Writes use one connection guarded by a writer ``RLock``. ``upsert_events``
  commits in chunks of ``chunk_rows`` (default 2000) distinct rows, each chunk
  one ``BEGIN IMMEDIATE`` transaction (raw rows + all rollups), so a big
  backfill never holds the write transaction for long.
* Reads use a second, read-only connection (``mode=ro`` + ``query_only``) with
  its own ``RLock``. Thanks to WAL, readers never wait for the writer.
  :meth:`read_snapshot` opens one read transaction so several queries see one
  consistent snapshot; it is re-entrant on the same thread.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from tokenvault.common.models import UsageEvent
from tokenvault.manager.store_core import (
    EVENT_COLS,
    MIGRATE_V1_BACKFILL,
    MIGRATE_V1_COLUMNS,
    N_EVENT_COLS,
    TOKEN_COLS,
    TOOL_TABLES,
    UPSERT_DEVICE_FROM_EVENTS,
    UPSERT_SESSION,
    UPSERT_TITLE,
    USAGE_TABLES,
    Row,
    local_day,
    month_of_day,
    parse_ts,
    rebuild_local_sql,
    upsert_tool_sql,
    upsert_usage_sql,
)

__all__ = ["TOKEN_COLS", "DeviceRow", "ReadSnapshot", "SchemaTooNewError", "Store", "parse_ts"]

SCHEMA_VERSION = 2
_IN_CHUNK = 500


class SchemaTooNewError(RuntimeError):
    """The database was written by a newer TokenVault."""


@dataclass(frozen=True)
class DeviceRow:
    device_id: str
    team: str
    email: str | None
    windows_user: str
    hostname: str
    last_seen: float


def _load_schema() -> str:
    return resources.files("tokenvault.manager").joinpath("schema.sql").read_text("utf-8")


def _txn(*scripts: str) -> str:
    return "\n".join(("BEGIN;", *scripts, "COMMIT;"))


class ReadSnapshot:
    """Query handle valid inside :meth:`Store.read_snapshot` (read-only connection)."""

    def __init__(self, con: sqlite3.Connection):
        self._con = con

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self._con.execute(sql, tuple(params)).fetchall()


class Store:
    def __init__(self, path: str | Path, chunk_rows: int = 2000, tz_offset_minutes: int = 0):
        """Open (create/migrate) the database.

        ``tz_offset_minutes`` is the Manager's fixed local offset used to key
        the local day/month rollups; opening with a different offset than
        last time rebuilds those rollups from the hourly ones (one-off).
        """
        self.path = Path(path)
        self.chunk_rows = max(1, chunk_rows)
        self.tz_offset_minutes = int(tz_offset_minutes)
        self._month_cache: dict[int, int] = {}
        self._wlock = threading.RLock()
        self._con = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None, timeout=10
        )
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.execute("PRAGMA busy_timeout=10000")
        try:
            self._migrate()
        except BaseException:
            self._con.close()
            raise
        self._rlock = threading.RLock()
        self._rdepth = 0
        self._rcon = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro",
            uri=True,
            check_same_thread=False,
            isolation_level=None,
            timeout=10,
        )
        self._rcon.row_factory = sqlite3.Row
        self._rcon.execute("PRAGMA query_only=1")
        self._rcon.execute("PRAGMA busy_timeout=10000")
        self._snap = ReadSnapshot(self._rcon)

    # ------------------------------------------------------------------ setup
    def _migrate(self) -> None:
        with self._wlock:
            con = self._con
            ver = int(con.execute("PRAGMA user_version").fetchone()[0])
            if ver > SCHEMA_VERSION:
                raise SchemaTooNewError(
                    f"{self.path}: schema version {ver} is newer than this TokenVault "
                    f"supports ({SCHEMA_VERSION}); upgrade the Manager"
                )
            if ver < SCHEMA_VERSION:
                parts = [_load_schema()]
                if ver == 1:
                    parts = [MIGRATE_V1_COLUMNS, *parts, MIGRATE_V1_BACKFILL]
                parts.append(f"PRAGMA user_version={SCHEMA_VERSION};")
                con.executescript(_txn(*parts))
            row = con.execute("SELECT value FROM meta WHERE key = 'tz_offset_minutes'").fetchone()
            if row is None or int(row[0]) != self.tz_offset_minutes:
                con.executescript(_txn(rebuild_local_sql(self.tz_offset_minutes)))

    def close(self) -> None:
        with self._rlock:
            self._rcon.close()
        with self._wlock:
            self._con.close()

    # ------------------------------------------------------------------ reads
    @contextmanager
    def read_snapshot(self) -> Iterator[ReadSnapshot]:
        """One read transaction on the read-only connection (consistent snapshot).

        Re-entrant on the same thread; other reader threads wait for the read
        lock, writers never block it (WAL).
        """
        with self._rlock:
            outer = self._rdepth == 0
            if outer:
                self._rcon.execute("BEGIN")
            self._rdepth += 1
            try:
                yield self._snap
            finally:
                self._rdepth -= 1
                if outer:
                    self._rcon.execute("COMMIT")

    def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self.read_snapshot() as snap:
            return snap.fetchall(sql, params)

    def list_devices(self) -> list[DeviceRow]:
        rows = self._fetchall(
            "SELECT device_id, team, email, windows_user, hostname, last_seen "
            "FROM devices ORDER BY device_id"
        )
        return [DeviceRow(*tuple(r)) for r in rows]

    def devices_online(self, now: float, window_s: float = 30) -> list[str]:
        rows = self._fetchall(
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
        The device e-mail only affects events ingested *later* (identity is
        frozen at ingest).
        """
        with self._wlock:
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
        with self._wlock:
            self._con.execute(
                "INSERT INTO devices (device_id, team) VALUES (?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET team = excluded.team",
                (device_id, team),
            )

    # ----------------------------------------------------------------- events
    def upsert_events(self, device_id: str, events: Iterable[UsageEvent]) -> int:
        """Upsert a batch for ``device_id``; return the number of new-or-grown rows.

        A row "grows" when any token field increases or it gains a tool name.
        Distinct rows are committed in chunks of ``chunk_rows``; each chunk is
        atomic (raw rows + all rollups). Raises ``ValueError`` on an unparsable
        timestamp before anything is written.
        """
        batch: dict[str, Row] = {}
        for e in events:
            row = Row.from_event(e)
            if row.key in batch:
                batch[row.key].absorb(row)
            else:
                batch[row.key] = row
        rows = list(batch.values())
        grown = 0
        for i in range(0, len(rows), self.chunk_rows):
            chunk = rows[i : i + self.chunk_rows]
            with self._wlock:
                con = self._con
                con.execute("BEGIN IMMEDIATE")
                try:
                    grown += self._apply(device_id, chunk)
                    con.execute("COMMIT")
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
        return grown

    def _existing(self, device_id: str, keys: list[str]) -> dict[str, Row]:
        out: dict[str, Row] = {}
        for i in range(0, len(keys), _IN_CHUNK):
            part = keys[i : i + _IN_CHUNK]
            marks = ",".join("?" * len(part))
            for r in self._con.execute(
                f"SELECT {EVENT_COLS} FROM events WHERE device_id = ? AND msg_key IN ({marks})",
                (device_id, *part),
            ):
                out[r["msg_key"]] = Row(
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
                    title_ts=r["title_ts"],
                    tools=json.loads(r["tool_names"]),
                    account_email=r["account_email"],
                    email=r["email"],
                    account_uuid=r["account_uuid"],
                    windows_user=r["windows_user"],
                    hostname=r["hostname"],
                    team=r["team_at_send"],
                )
        return out

    def _apply(self, device_id: str, chunk: list[Row]) -> int:
        con = self._con
        existing = self._existing(device_id, [r.key for r in chunk])
        dev = con.execute("SELECT email FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        device_email = (dev["email"] if dev else None) or ""
        inserts: list[tuple[Any, ...]] = []
        updates: list[tuple[Any, ...]] = []
        usage_acc: dict[tuple[Any, ...], list[int]] = {}  # (table, *key) -> sums
        tool_acc: dict[tuple[Any, ...], int] = {}  # (table, *key) -> calls
        sess_acc: dict[tuple[Any, ...], list[Any]] = {}
        titles: dict[str, tuple[float, str]] = {}
        grown = 0

        for inc in chunk:
            if inc.title is not None and inc.title_ts is not None:
                cur_t = titles.get(inc.session_id)
                if cur_t is None or inc.title_ts >= cur_t[0]:
                    titles[inc.session_id] = (inc.title_ts, inc.title)
            old = existing.get(inc.key)
            if old is None:
                row, n_ev = inc, 1
                row.email = row.account_email or device_email  # frozen identity
                delta, new_tools = list(row.tokens), list(row.tools)
                inserts.append(
                    (device_id, row.key, row.uuid, row.message_id, row.session_id,
                     row.timestamp, row.ts, row.hour, row.model, row.effort, row.sidechain,
                     *row.tokens, row.project, row.title, row.title_ts,
                     json.dumps(row.tools, ensure_ascii=False), row.account_email, row.email,
                     row.account_uuid, row.windows_user, row.hostname, row.team)
                )  # fmt: skip
            else:
                before = (old.tokens, old.tools, old.title, old.title_ts)
                old.absorb(inc)
                row, n_ev = old, 0
                delta = [a - b for a, b in zip(row.tokens, before[0], strict=True)]
                new_tools = row.tools[len(before[1]) :]
                if not any(delta) and not new_tools and (row.title, row.title_ts) == before[2:]:
                    continue
                updates.append(
                    (*row.tokens, json.dumps(row.tools, ensure_ascii=False), row.title,
                     row.title_ts, device_id, row.key)
                )  # fmt: skip
                if not any(delta) and not new_tools:
                    continue  # title-only change: rollups untouched
            grown += 1
            dims = (row.email, row.project, row.model)
            day = local_day(row.hour, self.tz_offset_minutes)
            times = (row.hour, day, self._month(day))
            for (table, _), t_val in zip(USAGE_TABLES, times, strict=True):
                cur = usage_acc.setdefault((table, *dims, row.sidechain, t_val), [0] * 6)
                for i, d in enumerate(delta):
                    cur[i] += d
                cur[5] += n_ev
            for tool in new_tools:
                for (table, _), t_val in zip(TOOL_TABLES, times, strict=True):
                    tk = (table, *dims, t_val, tool)
                    tool_acc[tk] = tool_acc.get(tk, 0) + 1
            s = sess_acc.setdefault((row.session_id, *dims), [0] * 7 + [row.ts, row.ts])
            for i, d in enumerate(delta):
                s[i] += d
            s[5] += sum(delta) if row.sidechain else 0
            s[6] += n_ev
            s[7], s[8] = min(s[7], row.ts), max(s[8], row.ts)

        self._write(device_id, inserts, updates, usage_acc, tool_acc, sess_acc, titles)
        last = chunk[-1]
        # Make sure the device exists (team/e-mail from the events until the
        # Manager records better data via upsert_device / set_device_team).
        con.execute(
            UPSERT_DEVICE_FROM_EVENTS,
            (device_id, last.team, last.account_email, last.windows_user, last.hostname),
        )
        return grown

    def _month(self, day: int) -> int:
        m = self._month_cache.get(day)
        if m is None:
            m = self._month_cache[day] = month_of_day(day)
        return m

    def _write(
        self,
        device_id: str,
        inserts: list[tuple[Any, ...]],
        updates: list[tuple[Any, ...]],
        usage_acc: dict[tuple[Any, ...], list[int]],
        tool_acc: dict[tuple[Any, ...], int],
        sess_acc: dict[tuple[Any, ...], list[Any]],
        titles: dict[str, tuple[float, str]],
    ) -> None:
        con = self._con
        if inserts:
            marks = ",".join("?" * N_EVENT_COLS)
            con.executemany(f"INSERT INTO events ({EVENT_COLS}) VALUES ({marks})", inserts)
        if updates:
            con.executemany(
                "UPDATE events SET input=?, output=?, cache_read=?, cache_5m=?, cache_1h=?, "
                "tool_names=?, session_title=?, title_ts=? WHERE device_id=? AND msg_key=?",
                updates,
            )
        for table, tcol in USAGE_TABLES:
            con.executemany(
                upsert_usage_sql(table, tcol),
                [(device_id, *k[1:], *v) for k, v in usage_acc.items() if k[0] == table],
            )
        for table, tcol in TOOL_TABLES:
            con.executemany(
                upsert_tool_sql(table, tcol),
                [(device_id, *k[1:], v) for k, v in tool_acc.items() if k[0] == table],
            )
        con.executemany(UPSERT_SESSION, [(device_id, *k, *v) for k, v in sess_acc.items()])
        con.executemany(UPSERT_TITLE, [(device_id, sid, t, ts) for sid, (ts, t) in titles.items()])
