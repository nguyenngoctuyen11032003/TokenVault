"""One adapter connection on the Manager: Hello/auth, then Batch -> store -> Receipt.

Driven by :class:`tokenvault.manager.ingest_server.IngestServer`; everything a
client sends is untrusted. Reject reasons starting with ``auth:`` mean "your
credentials are refused" (the adapter backs off for minutes); every other
reason is a transient/protocol problem.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from websockets.asyncio.server import ServerConnection

from tokenvault.common.models import UsageEvent
from tokenvault.common.protocol import (
    Batch,
    Hello,
    Msg,
    ProtocolError,
    Receipt,
    Reject,
    Welcome,
    decode_msg,
    encode_msg,
)

if TYPE_CHECKING:
    from tokenvault.manager.ingest_server import IngestServer

log = logging.getLogger("tokenvault.manager.ingest")

T = TypeVar("T")

REJECT_AUTH_ENROLL = "auth: enrollment refused"
REJECT_AUTH_KEY = "auth: unknown or revoked device"
REJECT_EXPECTED_HELLO = "protocol: expected hello"
REJECT_HELLO_TIMEOUT = "protocol: hello timeout"
REJECT_INVALID = "protocol: invalid frame"
REJECT_BAD_TEXT = "protocol: invalid text"
REJECT_STORAGE = "server: storage error"
CLOSE_POLICY = 1008
CLOSE_REPLACED = 4001


def text_ok(events: Sequence[UsageEvent]) -> bool:
    """False if any string can't be encoded as UTF-8 (lone surrogates from JSON escapes)."""
    try:
        for e in events:
            for v in (
                e.uuid, e.session_id, e.message_id, e.timestamp, e.model, e.reasoning_effort,
                e.project, e.session_title, e.claude_account_email, e.account_uuid,
                e.windows_user, e.hostname, e.team, e.device_id, *e.tool_names,
            ):
                if v is not None:
                    v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class RateLimiter:
    """Token bucket: ``rate`` batches per second (burst ``rate``); over limit -> sleep."""

    def __init__(self, rate: float) -> None:
        self.rate = max(rate, 0.001)
        self.tokens = self.rate
        self.t = time.monotonic()

    async def take(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.rate, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= 1:
            self.tokens -= 1
            return
        await asyncio.sleep((1 - self.tokens) / self.rate)
        self.tokens = 0
        self.t = time.monotonic()


class Session:
    def __init__(self, server: IngestServer, ws: ServerConnection) -> None:
        self.server = server
        self.ws = ws
        self.device_id: str | None = None  # set once authenticated
        self.team = ""
        self.peer = str(ws.remote_address[0]) if ws.remote_address else "?"

    async def _run_blocking(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.server._executor, fn, *args)

    async def reject(self, reason: str) -> None:
        self.server.rejected_count += 1
        log.info("rejecting connection from %s: %s", self.peer, reason)
        try:
            await self.ws.send(encode_msg(Reject(reason)))
            await self.ws.close(CLOSE_POLICY, reason[:100])
        except Exception:  # peer already gone - nothing else to do
            pass

    def _decode(self, raw: str | bytes) -> Msg | None:
        if not isinstance(raw, str):
            return None
        try:
            return decode_msg(raw)
        except (ProtocolError, ValueError, RecursionError):
            return None

    async def run(self) -> None:
        try:
            raw = await asyncio.wait_for(self.ws.recv(), self.server.hello_timeout)
        except TimeoutError:
            await self.reject(REJECT_HELLO_TIMEOUT)
            return
        hello = self._decode(raw)
        if not isinstance(hello, Hello):
            await self.reject(REJECT_EXPECTED_HELLO if hello is not None else REJECT_INVALID)
            return
        self.server.hellos_received += 1
        if not await self._authenticate(hello):
            return
        await self._serve_batches()

    async def _authenticate(self, hello: Hello) -> bool:
        enr = self.server.enrollment
        did = hello.device_id
        if hello.enroll_secret is not None:
            secret = hello.enroll_secret
            key = await self._run_blocking(enr.register_device, secret, did, hello.team)
            if key is None:
                await self.reject(REJECT_AUTH_ENROLL)
                return False
            log.info("device %r enrolled from %s", did, self.peer)
        else:
            key = hello.device_key or ""
            if not await self._run_blocking(enr.verify_device, did, key):
                await self.reject(REJECT_AUTH_KEY)
                return False
        team = await self._run_blocking(enr.device_team, did)
        self.team = team or ""
        # Every accepted Hello is answered with Welcome: a new key after enrollment,
        # the same key echoed back after key auth (over the same pinned channel).
        await self.ws.send(encode_msg(Welcome(key)))
        self.device_id = did
        self.server._register(did, self.ws)
        await self._run_blocking(self.server.store.upsert_device, did, self.team, None, time.time())
        log.info("device %r connected from %s (team %r)", did, self.peer, self.team)
        return True

    async def _serve_batches(self) -> None:
        assert self.device_id is not None
        limiter = RateLimiter(self.server.max_batches_per_s)
        while True:
            raw = await self.ws.recv()
            await limiter.take()
            msg = self._decode(raw)
            if not isinstance(msg, Batch):
                await self.reject(REJECT_INVALID)
                return
            events = list(msg.events)
            if not text_ok(events):
                await self.reject(REJECT_BAD_TEXT)
                return
            if events:
                try:
                    grown = await self._run_blocking(self._write, events)
                except Exception as e:  # incl. TzOffsetChangedError, sqlite3.Error
                    log.error("store write failed for device %r: %r", self.device_id, e)
                    await self.reject(REJECT_STORAGE)
                    return
                self.server.last_event_at = time.time()
            else:
                grown = 0
            # Receipt only after the store committed every chunk of this batch.
            ids = list(dict.fromkeys(e.message_id or e.uuid for e in events))
            await self.ws.send(encode_msg(Receipt(ids)))
            if grown and self.server.on_event is not None:
                try:
                    self.server.on_event(self.device_id, grown)
                except Exception:
                    log.exception("on_event callback failed")

    def _write(self, events: list[UsageEvent]) -> int:
        assert self.device_id is not None
        store = self.server.store
        last = events[-1]
        email = next(
            (e.claude_account_email for e in reversed(events) if e.claude_account_email), None
        )
        store.upsert_device(
            self.device_id, self.team, email, time.time(), last.windows_user, last.hostname
        )
        return int(store.upsert_events(self.device_id, events))
