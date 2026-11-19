"""Durable SQLite outbox: one row per message_id, plus per-source read offsets."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from tokenvault.common.models import UsageEvent
from tokenvault.common.protocol import ProtocolError, event_from_dict, event_to_dict

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_create_5m_tokens",
    "cache_create_1h_tokens",
)
_MIGRATIONS = {
    1: (
        """CREATE TABLE IF NOT EXISTS events (
            message_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            delivered INTEGER NOT NULL DEFAULT 0,
            enqueued_at REAL NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS events_pending ON events(delivered, enqueued_at)",
        "CREATE INDEX IF NOT EXISTS events_session ON events(session_id, delivered)",
        """CREATE TABLE IF NOT EXISTS source_offsets (
            source_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            offset INTEGER NOT NULL,
            prefix_hash TEXT NOT NULL,
            file_size INTEGER NOT NULL)""",
    ),
}


@dataclass(frozen=True)
class SourceOffset:
    file_id: str
    offset: int
    prefix_hash: str
    file_size: int = 0  # size seen when the offset was saved; shrinking => re-baseline


@dataclass(frozen=True)
class OutboxStats:
    queued: int  # all rows ever enqueued (one per message_id)
    delivered: int
    waiting: int  # not yet acknowledged by the Manager


def _key(ev: UsageEvent) -> str:
    return ev.message_id or ev.uuid


def _dump(ev: UsageEvent) -> str:
    return json.dumps(event_to_dict(ev), ensure_ascii=False, separators=(",", ":"))


def _load(payload: str) -> UsageEvent:
    return event_from_dict(json.loads(payload))


def _merge(old: UsageEvent, new: UsageEvent) -> UsageEvent | None:
    """Return the merged event if `new` adds anything, else None."""
    larger = any(getattr(new, f) > getattr(old, f) for f in _TOKEN_FIELDS)
    extra_tools = tuple(t for t in new.tool_names if t not in old.tool_names)
    if not larger and not extra_tools:
        return None
    tokens = {f: max(getattr(old, f), getattr(new, f)) for f in _TOKEN_FIELDS}
    return replace(
        new,
        session_title=new.session_title or old.session_title,
        tool_names=old.tool_names + extra_tools,
        **tokens,
    )


class Outbox:
    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> Outbox:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _migrate(self) -> None:
        with self._tx() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"outbox schema {version} is newer than {SCHEMA_VERSION}")
            for v in range(version + 1, SCHEMA_VERSION + 1):
                for stmt in _MIGRATIONS[v]:
                    db.execute(stmt)
                db.execute(f"PRAGMA user_version={v}")

    def enqueue(self, events: Iterable[UsageEvent]) -> int:
        """Insert new rows; replace rows whose usage grew. Returns rows changed."""
        changed = 0
        now = time.time()
        with self._tx() as db:
            for ev in events:
                key = _key(ev)
                row = db.execute("SELECT payload FROM events WHERE message_id=?", (key,)).fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO events VALUES (?, ?, ?, 0, ?)",
                        (key, ev.session_id, _dump(ev), now),
                    )
                    changed += 1
                    continue
                try:
                    merged = _merge(_load(row[0]), ev)
                except (ValueError, ProtocolError):
                    log.warning("replacing unreadable outbox row %s", key)
                    merged = ev
                if merged is not None:
                    db.execute(
                        "UPDATE events SET session_id=?, payload=?, delivered=0, enqueued_at=? "
                        "WHERE message_id=?",
                        (merged.session_id, _dump(merged), now, key),
                    )
                    changed += 1
        return changed

    def pending(self, limit: int) -> list[UsageEvent]:
        with self._lock:
            rows = self._db.execute(
                "SELECT message_id, payload FROM events WHERE delivered=0 "
                "ORDER BY enqueued_at, rowid LIMIT ?",
                (limit,),
            ).fetchall()
        out: list[UsageEvent] = []
        for key, payload in rows:
            try:
                out.append(_load(payload))
            except (ValueError, ProtocolError):
                log.warning("skipping unreadable outbox row %s", key)
        return out

    def ack(self, message_ids: Sequence[str]) -> int:
        with self._tx() as db:
            cur = db.executemany(
                "UPDATE events SET delivered=1 WHERE message_id=? AND delivered=0",
                [(m,) for m in message_ids],
            )
            return max(cur.rowcount, 0)

    def stats(self) -> OutboxStats:
        with self._lock:
            total, delivered = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(delivered), 0) FROM events"
            ).fetchone()
        return OutboxStats(queued=total, delivered=delivered, waiting=total - delivered)

    def set_session_title(self, session_id: str, title: str) -> int:
        """Set session_title on undelivered rows of a session. Returns rows changed."""
        changed = 0
        with self._tx() as db:
            rows = db.execute(
                "SELECT message_id, payload FROM events WHERE session_id=? AND delivered=0",
                (session_id,),
            ).fetchall()
            for key, payload in rows:
                try:
                    ev = _load(payload)
                except (ValueError, ProtocolError):
                    continue
                if ev.session_title == title:
                    continue
                db.execute(
                    "UPDATE events SET payload=? WHERE message_id=?",
                    (_dump(replace(ev, session_title=title)), key),
                )
                changed += 1
        return changed

    def get_offset(self, source: str) -> SourceOffset | None:
        with self._lock:
            row = self._db.execute(
                "SELECT file_id, offset, prefix_hash, file_size FROM source_offsets "
                "WHERE source_id=?",
                (source,),
            ).fetchone()
        return None if row is None else SourceOffset(row[0], row[1], row[2], row[3])

    def set_offset(self, source: str, off: SourceOffset) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO source_offsets VALUES (?, ?, ?, ?, ?) ON CONFLICT(source_id) DO "
                "UPDATE SET file_id=excluded.file_id, offset=excluded.offset, "
                "prefix_hash=excluded.prefix_hash, file_size=excluded.file_size",
                (source, off.file_id, off.offset, off.prefix_hash, off.file_size),
            )
