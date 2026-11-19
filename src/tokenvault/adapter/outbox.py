"""Durable SQLite outbox: one row per message_id, plus per-source read offsets."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from pathlib import Path

from tokenvault.common.models import UsageEvent
from tokenvault.common.protocol import ProtocolError, event_from_dict, event_to_dict

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
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
    2: (
        # Bumped on every payload change so a stale receipt cannot ack newer data.
        "ALTER TABLE events ADD COLUMN revision INTEGER NOT NULL DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS session_titles (
            session_id TEXT PRIMARY KEY,
            title TEXT NOT NULL)""",
    ),
}


@dataclass(frozen=True)
class SourceOffset:
    file_id: str
    offset: int
    prefix_hash: str
    file_size: int = 0  # size seen when the offset was saved; shrinking => re-baseline


@dataclass(frozen=True)
class PendingEvent:
    """An undelivered event and the row revision it was read at.

    Ack with `(message_id, revision)`; the ack is ignored if the row changed since.
    """

    message_id: str
    revision: int
    event: UsageEvent


@dataclass(frozen=True)
class OutboxStats:
    queued: int  # all rows ever enqueued (one per message_id)
    delivered: int
    waiting: int  # not yet acknowledged by the Manager


def _key(ev: UsageEvent) -> str:
    return ev.message_id or ev.uuid


def _dump(ev: UsageEvent) -> str:
    return json.dumps(event_to_dict(ev), ensure_ascii=False, separators=(",", ":"))


def _field_defaults() -> dict[str, object]:
    """Neutral value per UsageEvent field, used when an old row lacks the field."""
    out: dict[str, object] = {}
    for f in fields(UsageEvent):
        t = str(f.type)
        if f.name == "tool_names":
            out[f.name] = []
        elif t == "bool":
            out[f.name] = False
        elif t == "int":
            out[f.name] = 0
        elif "None" in t:
            out[f.name] = None
        else:
            out[f.name] = ""
    return out


_DEFAULTS = _field_defaults()
PRUNE_AFTER_DAYS = 30


def _load(payload: str) -> UsageEvent:
    """Decode leniently: missing fields default, unknown ones are dropped.

    Adding a field to ``UsageEvent`` therefore never bricks an existing queue.
    """
    d = json.loads(payload)
    if not isinstance(d, dict):
        raise ProtocolError("event must be an object")
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in d.items() if k in _DEFAULTS})
    return event_from_dict(merged)


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
        # stats() must not scan the whole table on every refresh: keep a counter.
        self._delivered = int(
            self._db.execute("SELECT COUNT(*) FROM events WHERE delivered=1").fetchone()[0])

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
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

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
                row = db.execute(
                    "SELECT payload, delivered FROM events WHERE message_id=?", (key,)).fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO events (message_id, session_id, payload, delivered, "
                        "enqueued_at, revision) VALUES (?, ?, ?, 0, ?, 0)",
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
                    self._delivered -= int(row[1] == 1)
                    db.execute(
                        "UPDATE events SET session_id=?, payload=?, delivered=0, enqueued_at=?, "
                        "revision=revision+1 WHERE message_id=?",
                        (merged.session_id, _dump(merged), now, key),
                    )
                    changed += 1
        return changed

    def pending(self, limit: int) -> list[PendingEvent]:
        """Oldest undelivered events. Undecodable rows are marked delivered (poison) so
        they can never block the head of the queue."""
        out: list[PendingEvent] = []
        bad: list[tuple[str, int]] = []
        cursor = (-1.0, -1)
        while len(out) < limit:
            with self._lock:
                rows = self._db.execute(
                    "SELECT message_id, revision, payload, enqueued_at, rowid FROM events "
                    "WHERE delivered=0 AND (enqueued_at, rowid) > (?, ?) "
                    "ORDER BY enqueued_at, rowid LIMIT ?",
                    (*cursor, limit - len(out)),
                ).fetchall()
            if not rows:
                break
            for key, rev, payload, enq, rid in rows:
                cursor = (enq, rid)
                try:
                    out.append(PendingEvent(key, rev, _load(payload)))
                except (ValueError, ProtocolError, TypeError):
                    bad.append((key, rev))
        if bad:
            log.warning("dropping %d unreadable outbox row(s) (marked delivered)", len(bad))
            self.ack(bad)
        return out

    def ack(self, sent: Iterable[tuple[str, int]]) -> int:
        """Mark `(message_id, revision)` pairs delivered. Stale revisions are ignored."""
        with self._tx() as db:
            cur = db.executemany(
                "UPDATE events SET delivered=1 WHERE message_id=? AND revision=? AND delivered=0",
                [(m, r) for m, r in sent],
            )
            n = max(cur.rowcount, 0)
            self._delivered += n
            return n

    def stats(self) -> OutboxStats:
        # Pending count walks only the undelivered part of the (delivered, enqueued_at)
        # index; the delivered count is a maintained counter.
        with self._lock:
            waiting = self._db.execute(
                "SELECT COUNT(*) FROM events WHERE delivered=0").fetchone()[0]
            delivered = self._delivered
        return OutboxStats(queued=waiting + delivered, delivered=delivered, waiting=waiting)

    def prune(self, max_age_days: float = PRUNE_AFTER_DAYS) -> int:
        """Delete delivered rows older than ``max_age_days`` (and titles of sessions that
        no longer have any row). Source offsets are untouched: they track file read
        positions, which stay valid. Returns rows deleted."""
        cutoff = time.time() - max_age_days * 86400
        with self._tx() as db:
            n = db.execute(
                "DELETE FROM events WHERE delivered=1 AND enqueued_at < ?", (cutoff,)
            ).rowcount
            if n > 0:
                db.execute("DELETE FROM session_titles WHERE session_id NOT IN "
                           "(SELECT session_id FROM events)")
            n = max(n, 0)
            self._delivered -= n
        if n:
            log.info("pruned %d delivered outbox row(s) older than %g days", n, max_age_days)
        return n

    def set_session_title(self, session_id: str, title: str) -> int:
        """Persist a session's title and set it on its undelivered rows. Returns rows changed."""
        changed = 0
        with self._tx() as db:
            db.execute(
                "INSERT INTO session_titles VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET title=excluded.title",
                (session_id, title),
            )
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
                    "UPDATE events SET payload=?, revision=revision+1 WHERE message_id=?",
                    (_dump(replace(ev, session_title=title)), key),
                )
                changed += 1
        return changed

    def session_titles(self) -> dict[str, str]:
        with self._lock:
            rows = self._db.execute("SELECT session_id, title FROM session_titles").fetchall()
        return dict(rows)

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
