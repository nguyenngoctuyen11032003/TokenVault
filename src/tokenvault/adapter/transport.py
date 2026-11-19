"""Adapter -> Manager transport: one persistent pinned-TLS WebSocket.

* TLS >= 1.2, no CA and no host-name check: trust is the pinned SHA-256 of the
  Manager's certificate (``Endpoint.fingerprint``). The pin is checked inside
  ``connection_made`` - right after the TLS handshake and BEFORE the WebSocket
  upgrade request - and on mismatch the TCP connection is aborted, so not one
  application byte (let alone the Hello carrying the enrollment secret) reaches
  an impostor (Review Focus 3). It is re-checked before the Hello is sent.
* Session: Hello -> Welcome (new device key -> ``on_device_key``) | Reject.
  Then loop: ``Batch`` of ``outbox.pending(batch_size)`` -> wait ``Receipt`` ->
  ``outbox.ack([(id, revision)])`` (a row that changed meanwhile stays queued).
  Idle: wait for ``notify()`` or 30 s, then re-check the outbox and ping.
* Failures -> state "offline", reconnect with exponential backoff + jitter
  (1 s -> 60 s). ``auth:`` rejects -> state "rejected", retry every 5 min.
* Host order: last good host, ``endpoint.hosts``, then ``resolver()`` results.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from websockets.asyncio.client import ClientConnection, connect

from tokenvault.adapter.outbox import PendingEvent
from tokenvault.adapter.transport_tls import (
    PinMismatch,
    client_ssl_context,
    peer_fingerprint,
    pin_matches,
    pinned_connection_class,
    short_fp,
    validate_fp,
)
from tokenvault.common.protocol import (
    MAX_BATCH,
    MAX_FRAME_BYTES,
    Batch,
    Hello,
    ProtocolError,
    Receipt,
    Reject,
    Welcome,
    decode_msg,
    encode_msg,
)

log = logging.getLogger("tokenvault.adapter.transport")

STATES = ("connecting", "connected", "offline", "rejected")


class OutboxLike(Protocol):
    def pending(self, limit: int) -> list[PendingEvent]: ...

    def ack(self, sent: Iterable[tuple[str, int]]) -> int: ...


@dataclass(frozen=True)
class Endpoint:
    hosts: list[str] = field(default_factory=list)
    port: int = 47800
    fingerprint: str = ""


class Rejected(Exception):
    """The Manager refused our credentials."""


class Offline(Exception):
    """Could not reach / lost the Manager."""


class Transport:
    def __init__(
        self,
        outbox: OutboxLike,
        endpoint: Endpoint,
        hello_factory: Callable[[], Hello],
        on_state: Callable[[str], None] | None = None,
        resolver: Callable[[], list[str]] | None = None,
        batch_size: int = MAX_BATCH,
        on_device_key: Callable[[str], None] | None = None,
        *,
        idle_s: float = 30.0,
        backoff_min: float = 1.0,
        backoff_max: float = 60.0,
        rejected_retry_s: float = 300.0,
        open_timeout: float = 10.0,
        reply_timeout: float = 60.0,
    ) -> None:
        self._outbox = outbox
        self.endpoint = endpoint
        self._hello_factory = hello_factory
        self._on_state = on_state
        self._resolver = resolver
        self.batch_size = max(1, min(batch_size, MAX_BATCH))
        self._on_device_key = on_device_key
        self.idle_s = idle_s
        self.backoff_min = backoff_min
        self.backoff_max = backoff_max
        self.rejected_retry_s = rejected_retry_s
        self.open_timeout = open_timeout
        self.reply_timeout = reply_timeout
        self._pin = validate_fp(endpoint.fingerprint)  # ValueError if malformed
        self.state = "offline"
        self.last_error: str | None = None  # human-readable reason of the last failure
        self.last_good_host: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._connected_once = False

    # ------------------------------------------------------------ public API
    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def start(self) -> None:
        """Start the background thread. Raises RuntimeError if it is still running."""
        if self.running:
            raise RuntimeError("transport already running (or an old thread has not exited)")
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._wake = asyncio.Event()
        self._task = loop.create_task(self._main())
        self._thread = threading.Thread(
            target=self._run, args=(loop, self._task), name="tv-transport", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the transport. Safe from any thread, including its own callbacks."""
        loop, thread, task = self._loop, self._thread, self._task
        if loop is None or thread is None or task is None:
            return
        if threading.current_thread() is thread:
            task.cancel()  # we are on the loop (e.g. inside on_state): just cancel
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(task.cancel)
        thread.join(timeout)
        if thread.is_alive():
            log.warning("transport thread did not stop within %.0f s", timeout)
        elif self._thread is thread:
            self._thread = None

    def notify(self) -> None:
        """Thread-safe: new rows are in the outbox, send them now."""
        loop, wake = self._loop, self._wake
        if loop is None or wake is None:
            return
        with contextlib.suppress(RuntimeError):  # loop already closed
            loop.call_soon_threadsafe(wake.set)

    # ------------------------------------------------------------- internals
    def _run(self, loop: asyncio.AbstractEventLoop, task: asyncio.Task[None]) -> None:
        asyncio.set_event_loop(loop)
        try:
            with contextlib.suppress(asyncio.CancelledError):
                loop.run_until_complete(task)
        except Exception:
            log.exception("transport loop crashed")
        finally:
            with contextlib.suppress(Exception):
                # Wait for in-flight outbox calls so none outlives stop().
                loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
            if self._loop is loop:  # a newer start() may already own a new loop
                self._loop = None

    def _set_state(self, state: str, reason: str | None = None) -> None:
        if reason is not None:
            self.last_error = reason
        if state == self.state:
            return
        self.state = state
        if state in ("offline", "rejected"):
            log.info("transport %s: %s", state, reason)
        else:
            log.info("transport %s", state)
        if self._on_state is not None:
            try:
                self._on_state(state)
            except Exception:
                log.exception("on_state callback failed")

    def _backoff(self, failures: int) -> float:
        base = min(self.backoff_max, self.backoff_min * (2 ** min(failures, 16)))
        return random.uniform(base / 2, base)

    async def _main(self) -> None:
        failures = 0
        while True:
            self._set_state("connecting")
            self._connected_once = False
            try:
                await self._connect_and_run()
                delay = self._backoff(0)
            except Rejected as e:
                self._set_state("rejected", str(e))
                failures = 0
                delay = self.rejected_retry_s
            except Exception as e:  # Offline, ConnectionClosed, OSError, ProtocolError, bugs
                if self._connected_once:
                    failures = 0
                reason = str(e) if isinstance(e, Offline) else f"{type(e).__name__}: {e}"
                self._set_state("offline", reason)
                delay = self._backoff(failures)
                failures += 1
            await asyncio.sleep(delay)

    async def _connect_and_run(self) -> None:
        ws = await self._open()
        try:
            await self._handshake(ws)
            self._connected_once = True
            self._set_state("connected")
            await self._pump(ws)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(), 5)

    def _candidates(self) -> list[str]:
        hosts = [self.last_good_host] if self.last_good_host else []
        return list(dict.fromkeys([*hosts, *self.endpoint.hosts]))

    async def _open(self) -> ClientConnection:
        tried: list[str] = []
        errors: list[str] = []
        mismatch: str | None = None
        for phase in (0, 1):
            if phase == 0:
                hosts = self._candidates()
            elif self._resolver is not None:
                try:
                    found = await asyncio.to_thread(self._resolver)
                except Exception as e:
                    log.warning("resolver failed: %r", e)
                    found = []
                hosts = [h for h in dict.fromkeys(found) if h not in tried]
            else:
                hosts = []
            for host in hosts:
                tried.append(host)
                try:
                    ws = await self._connect_host(host)
                except PinMismatch as e:
                    mismatch = f"fingerprint mismatch at {host}: {e}"
                    log.warning("refusing %s: %s", host, e)
                    continue
                except Exception as e:
                    errors.append(f"{host}: {type(e).__name__}: {e}")
                    continue
                self.last_good_host = host
                return ws
        raise Offline(mismatch or ("; ".join(errors) or "no Manager address known"))

    async def _connect_host(self, host: str) -> ClientConnection:
        holder: dict[str, str] = {}
        uri_host = f"[{host}]" if ":" in host else host
        try:
            ws = await connect(
                f"wss://{uri_host}:{self.endpoint.port}/",
                ssl=client_ssl_context(),
                proxy=None,
                compression=None,
                max_size=MAX_FRAME_BYTES,
                open_timeout=self.open_timeout,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                user_agent_header=None,
                create_connection=pinned_connection_class(self._pin, holder),
            )
        except Exception:
            if "mismatch" in holder:
                raise PinMismatch(holder["mismatch"]) from None
            raise
        # Belt and braces: verify again before anything of ours is sent.
        got = peer_fingerprint(ws.transport)
        if not pin_matches(got, self._pin):
            ws.transport.abort()
            raise PinMismatch(f"got {short_fp(got or '')}, pinned {short_fp(self._pin)}")
        return ws

    async def _recv_msg(self, ws: ClientConnection) -> Any:
        raw = await asyncio.wait_for(ws.recv(), self.reply_timeout)
        if not isinstance(raw, str):
            raise ProtocolError("binary frame from Manager")
        msg = decode_msg(raw)
        if isinstance(msg, Reject):
            if msg.reason.startswith("auth"):
                raise Rejected(msg.reason)
            raise Offline(f"Manager rejected: {msg.reason}")
        return msg

    async def _handshake(self, ws: ClientConnection) -> None:
        hello = self._hello_factory()
        await ws.send(encode_msg(hello))
        msg = await self._recv_msg(ws)
        if not isinstance(msg, Welcome):
            raise ProtocolError(f"expected welcome, got {type(msg).__name__}")
        if msg.device_key and msg.device_key != hello.device_key:
            log.info("enrolled with the Manager; received a device key")
            if self._on_device_key is not None:
                try:
                    self._on_device_key(msg.device_key)
                except Exception:
                    log.exception("on_device_key callback failed")

    async def _pump(self, ws: ClientConnection) -> None:
        wake = self._wake
        assert wake is not None
        closed = asyncio.ensure_future(ws.wait_closed())
        try:
            while True:
                wake.clear()
                rows = await asyncio.to_thread(self._outbox.pending, self.batch_size)
                if rows:
                    await self._send_batch(ws, rows)
                    continue
                waiter = asyncio.ensure_future(wake.wait())
                try:
                    done, _ = await asyncio.wait(
                        {waiter, closed}, timeout=self.idle_s, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    waiter.cancel()
                if closed in done:
                    raise Offline("connection closed by Manager")
                if not done:  # idle: keepalive round trip
                    pong = await ws.ping()
                    await asyncio.wait_for(pong, self.reply_timeout)
        finally:
            closed.cancel()

    async def _drop(self, rows: list[PendingEvent], why: str) -> None:
        """Ack rows that can never be sent, so they cannot block the queue forever."""
        for r in rows:
            log.error("dropping outbox row %a: %s", r.message_id, why)
        await asyncio.to_thread(self._outbox.ack, [(r.message_id, r.revision) for r in rows])

    async def _send_batch(self, ws: ClientConnection, rows: list[PendingEvent]) -> None:
        good: list[PendingEvent] = []
        bad: list[PendingEvent] = []
        for r in rows:  # a row that cannot be framed on its own is poison
            try:
                encode_msg(Batch([r.event]))
                good.append(r)
            except (ProtocolError, ValueError):  # incl. UnicodeEncodeError, oversize
                bad.append(r)
        if bad:
            await self._drop(bad, "cannot be encoded into a frame")
        if not good:
            return
        rows = good
        while True:
            try:
                frame = encode_msg(Batch([r.event for r in rows]))
                break
            except (ProtocolError, ValueError):
                rows = rows[: max(1, len(rows) // 2)]  # too big together: send fewer
        await ws.send(frame)
        msg = await self._recv_msg(ws)
        if not isinstance(msg, Receipt):
            raise ProtocolError(f"expected receipt, got {type(msg).__name__}")
        sent = {r.message_id: r.revision for r in rows}
        pairs = [(m, sent[m]) for m in dict.fromkeys(msg.message_ids) if m in sent]
        acked = await asyncio.to_thread(self._outbox.ack, pairs)
        if acked < len(rows):
            log.debug("%d of %d rows stay queued (changed meanwhile)", len(rows) - acked, len(rows))
