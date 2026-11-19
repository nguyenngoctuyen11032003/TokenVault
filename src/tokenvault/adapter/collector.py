"""Incremental transcript collector: tails *.jsonl files into the Outbox.

Each source keeps (file_id, offset, hash of the 4 KiB before offset, size). Only
complete lines are consumed. If the file shrank, changed identity, or the bytes
before the offset differ, the file is re-read from 0 ("re-baseline"); the
Outbox's idempotent enqueue keeps that from double counting.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from tokenvault.adapter.identity import Identity
from tokenvault.adapter.outbox import Outbox, SourceOffset
from tokenvault.adapter.parser import (
    AssistantUsage,
    MessageDeduper,
    SessionContext,
    TitleUpdate,
    parse_line,
)
from tokenvault.common.models import UsageEvent
from tokenvault.common.protocol import MAX_TOKENS

log = logging.getLogger(__name__)

MAX_LINE_BYTES = 1_048_576
MAX_BATCH_BYTES = 16 * 1024 * 1024
HASH_WINDOW = 4096


@dataclass(frozen=True)
class CollectResult:
    new_events: int = 0
    skipped_oversize: int = 0
    rebaselined: bool = False
    skipped_invalid: int = 0
    more: bool = False  # batch cap hit; call collect_file again

    def __add__(self, other: CollectResult) -> CollectResult:
        return CollectResult(
            self.new_events + other.new_events,
            self.skipped_oversize + other.skipped_oversize,
            self.rebaselined or other.rebaselined,
            self.skipped_invalid + other.skipped_invalid,
            self.more or other.more,
        )


def source_id(path: Path) -> str:
    norm = os.path.normcase(os.path.abspath(path))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _file_id(st: os.stat_result) -> str:
    if st.st_ino:
        return f"{st.st_dev}:{st.st_ino}"
    birth = getattr(st, "st_birthtime_ns", None) or st.st_ctime_ns
    return f"ctime:{birth}"


def _window_hash(fh: BinaryIO, end: int) -> str:
    start = max(0, end - HASH_WINDOW)
    fh.seek(start)
    return hashlib.sha256(fh.read(end - start)).hexdigest()


def _valid(u: AssistantUsage) -> bool:
    if not u.timestamp:
        return False
    try:
        datetime.fromisoformat(u.timestamp)
    except ValueError:
        return False
    return all(
        n <= MAX_TOKENS
        for n in (
            u.input_tokens,
            u.output_tokens,
            u.cache_read_tokens,
            u.cache_create_5m_tokens,
            u.cache_create_1h_tokens,
        )
    )


class Collector:
    def __init__(
        self,
        outbox: Outbox,
        identity: Identity,
        team: str,
        ctx: SessionContext | None = None,
    ) -> None:
        self._outbox = outbox
        self._identity = identity
        self._team = team
        self._ctx = ctx if ctx is not None else SessionContext()
        self._deduper = MessageDeduper()

    def _to_event(self, u: AssistantUsage) -> UsageEvent:
        i = self._identity
        return UsageEvent(
            uuid=u.uuid,
            session_id=u.session_id,
            message_id=u.message_id,
            timestamp=u.timestamp,
            model=u.model,
            reasoning_effort=u.reasoning_effort,
            is_sidechain=u.is_sidechain,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_tokens,
            cache_create_5m_tokens=u.cache_create_5m_tokens,
            cache_create_1h_tokens=u.cache_create_1h_tokens,
            project=u.project,
            session_title=self._ctx.titles.get(u.session_id),
            tool_names=u.tool_names,
            claude_account_email=i.email,
            account_uuid=i.account_uuid,
            windows_user=i.windows_user,
            hostname=i.hostname,
            team=self._team,
            device_id=i.device_id,
        )

    def _start_offset(self, fh: BinaryIO, sid: str, fid: str, size: int) -> tuple[int, bool]:
        """Return (offset to resume from, whether we re-baselined)."""
        saved = self._outbox.get_offset(sid)
        if saved is None:
            return 0, False
        if (
            saved.file_id != fid
            or saved.offset < 0
            or size < saved.offset
            or size < saved.file_size
            or _window_hash(fh, saved.offset) != saved.prefix_hash
        ):
            log.info("re-baselining source %s", sid[:12])
            return 0, True
        return saved.offset, False

    def collect_file(self, path: Path) -> CollectResult:
        sid = source_id(path)
        with open(path, "rb") as fh:
            st = os.fstat(fh.fileno())
            fid = _file_id(st)
            start, rebaselined = self._start_offset(fh, sid, fid, st.st_size)
            fh.seek(start)
            consumed, oversize, invalid, more = 0, 0, 0, False
            titles: dict[str, str] = {}
            while True:
                if consumed >= MAX_BATCH_BYTES:
                    more = True
                    break
                line = fh.readline(MAX_LINE_BYTES + 1)
                if not line.endswith(b"\n"):
                    if len(line) <= MAX_LINE_BYTES:
                        break  # EOF or a partial line still being written
                    rest = self._walk_past(fh)
                    if rest is None:
                        break  # oversize line not finished yet; retry later
                    consumed += len(line) + rest
                    oversize += 1
                    continue
                consumed += len(line)
                parsed = parse_line(line, self._ctx)
                if isinstance(parsed, TitleUpdate):
                    titles[parsed.session_id] = parsed.title
                elif isinstance(parsed, AssistantUsage):
                    if _valid(parsed):
                        self._deduper.add(parsed)
                    else:
                        invalid += 1
            end = start + consumed
            events = [self._to_event(u) for u in self._deduper.flush()]
            new = self._outbox.enqueue(events)
            for session, title in titles.items():
                self._outbox.set_session_title(session, title)
            size_now = os.fstat(fh.fileno()).st_size
            self._outbox.set_offset(
                sid, SourceOffset(fid, end, _window_hash(fh, end), max(size_now, end))
            )
        if oversize:
            log.warning("skipped %d oversize line(s) in source %s", oversize, sid[:12])
        return CollectResult(new, oversize, rebaselined, invalid, more)

    @staticmethod
    def _walk_past(fh: BinaryIO) -> int | None:
        """Skip the remainder of an oversize line. None if it has no newline yet."""
        skipped = 0
        while True:
            chunk = fh.readline(MAX_LINE_BYTES + 1)
            if not chunk:
                return None
            skipped += len(chunk)
            if chunk.endswith(b"\n"):
                return skipped

    def collect_all(self, root: Path) -> CollectResult:
        total = CollectResult()
        for path in sorted(root.rglob("*.jsonl")):
            if not path.is_file():
                continue
            while True:
                try:
                    r = self.collect_file(path)
                except OSError as e:
                    log.warning("cannot read %s: %s", path.name, e)
                    break
                total = total + CollectResult(
                    r.new_events, r.skipped_oversize, r.rebaselined, r.skipped_invalid
                )
                if not r.more:
                    break
        return total
