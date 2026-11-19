"""One adapter connection on the Manager: Hello/auth, then Batch -> store -> Receipt.

Driven by :class:`tokenvault.manager.ingest_server.IngestServer`; everything a
client sends is untrusted. Reject reasons starting with ``auth:`` mean "your
credentials are refused" (the adapter backs off for minutes); every other
reason is a transient/protocol problem.

* Before auth a connection may only send frames <= ``PREAUTH_MAX_BYTES``; the
  limit is raised to ``MAX_FRAME_BYTES`` right before ``Welcome`` is sent (the
  websockets parser reads the limit when the next frame's first bytes arrive,
  and a well-behaved client sends nothing before ``Welcome``).
* Revocation is re-checked with ``Enrollment.verify_device`` on EVERY batch
  (one indexed SQLite lookup, done in the same worker-thread hop as the store
  write) and once more right after the session is registered, so a revocation
  that raced the Hello, or one done behind the server's back, still stops the
  next batch before anything is written (close code 4003).
* Each event is validated (UTF-8 encodable text, timestamp parsed by the
  store's own ``parse_ts``; token ranges are already enforced by the protocol
  decoder). Invalid events are dropped (WARNING with a count only) but their
  ids ARE included in the Receipt so the adapter acks them and its queue can
  never be blocked by one poisoned row; valid neighbours are stored normally.
  An event whose id itself cannot be encoded cannot be receipted and is only
  dropped (a real adapter can never produce such a frame).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from websockets.asyncio.server import ServerConnection

from tokenvault.common.models import UsageEvent
from tokenvault.common.protocol import (
    MAX_FRAME_BYTES,
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
from tokenvault.manager.store_core import parse_ts

if TYPE_CHECKING:
    from tokenvault.manager.ingest_server import IngestServer

log = logging.getLogger("tokenvault.manager.ingest")

T = TypeVar("T")

PREAUTH_MAX_BYTES = 4096
REJECT_AUTH_ENROLL = "auth: enrollment refused"
REJECT_AUTH_KEY = "auth: unknown or revoked device"
REJECT_AUTH_REVOKED = "auth: device revoked"
REJECT_EXPECTED_HELLO = "protocol: expected hello"
REJECT_HELLO_TIMEOUT = "protocol: hello timeout"
REJECT_INVALID = "protocol: invalid frame"
REJECT_STORAGE = "server: storage error"
CLOSE_POLICY = 1008
CLOSE_REPLACED = 4001
CLOSE_REVOKED = 4003


def _encodable(v: str | None) -> bool:
    if v is None:
        return True
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def event_ok(e: UsageEvent) -> bool:
    """True if the store can take ``e``: encodable text and a parseable timestamp."""
    texts = (
        e.uuid, e.session_id, e.message_id, e.timestamp, e.model, e.reasoning_effort,
        e.project, e.session_title, e.claude_account_email, e.account_uuid,
        e.windows_user, e.hostname, e.team, e.device_id, *e.tool_names,
    )
    if not all(_encodable(v) for v in texts):
        return False
    try:
        return math.isfinite(parse_ts(e.timestamp))
    except (ValueError, TypeError, OverflowError):
        return False


def receipt_id(e: UsageEvent) -> str | None:
    key = e.message_id or e.uuid
    return key if _encodable(key) else None


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


class _Revoked(Exception):
    pass


class Session:
    def __init__(self, server: IngestServer, ws: ServerConnection) -> None:
        self.server = server
        self.ws = ws
        self.device_id: str | None = None  # set once authenticated
        self.package_id: str | None = None
        self.team = ""
        self._key = ""
        self.peer = str(ws.remote_address[0]) if ws.remote_address else "?"

    async def _run_blocking(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.server._executor, fn, *args)

    async def reject(self, reason: str, code: int = CLOSE_POLICY) -> None:
        self.server.rejected_count += 1
        log.info("rejecting connection from %s: %s", self.peer, reason)
        try:
            await self.ws.send(encode_msg(Reject(reason)))
            await self.ws.close(code, reason[:100])
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
        self._key = key
        team = await self._run_blocking(enr.device_team, did)
        self.team = team or ""
        self.package_id = await self._run_blocking(enr.device_package, did)
        self.device_id = did
        self.server._register(self)
        # Close the race with a revocation that happened between verify and register.
        if not await self._run_blocking(enr.verify_device, did, key):
            await self.reject(REJECT_AUTH_REVOKED, CLOSE_REVOKED)
            return False
        self.ws.protocol.max_message_size = MAX_FRAME_BYTES  # authenticated: full frames
        self.server._mark_authenticated(self.ws)
        # Every accepted Hello is answered with Welcome: a new key after enrollment,
        # the same key echoed back after key auth (over the same pinned channel).
        await self.ws.send(encode_msg(Welcome(key)))
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
            try:
                grown = await self._run_blocking(self._write, events)
            except _Revoked:
                await self.reject(REJECT_AUTH_REVOKED, CLOSE_REVOKED)
                return
            except Exception as e:  # incl. TzOffsetChangedError, sqlite3.Error
                log.error("store write failed for device %r: %r", self.device_id, e)
                await self.reject(REJECT_STORAGE)
                return
            # Receipt only after the store committed every chunk of this batch.
            ids = list(dict.fromkeys(i for i in map(receipt_id, events) if i is not None))
            await self.ws.send(encode_msg(Receipt(ids)))
            if grown and self.server.on_event is not None:
                try:
                    self.server.on_event(self.device_id, grown)
                except Exception:
                    log.exception("on_event callback failed")

    def _write(self, events: list[UsageEvent]) -> int:
        """Worker thread: re-check the device, drop invalid events, write the rest."""
        assert self.device_id is not None
        if not self.server.enrollment.verify_device(self.device_id, self._key):
            raise _Revoked
        valid = [e for e in events if event_ok(e)]
        if len(valid) < len(events):
            log.warning(
                "dropped %d invalid event(s) of %d from device %r",
                len(events) - len(valid),
                len(events),
                self.device_id,
            )
        if not valid:
            return 0
        store = self.server.store
        last = valid[-1]
        email = next(
            (e.claude_account_email for e in reversed(valid) if e.claude_account_email), None
        )
        store.upsert_device(
            self.device_id, self.team, email, time.time(), last.windows_user, last.hostname
        )
        grown = int(store.upsert_events(self.device_id, valid))
        self.server.last_event_at = time.time()
        return grown
