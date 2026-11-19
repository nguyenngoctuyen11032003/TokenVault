"""Manager ingest server: WebSocket over TLS (>= 1.2) on the LAN, default port 47800.

Runs its own asyncio loop in a background thread so the (sync) Manager app can
``start()``/``stop()`` it. Store and enrollment calls run in a small thread
pool. Every exception inside a connection is caught there, so one bad client
can never stop the server. The listening socket is bound with
``SO_EXCLUSIVEADDRUSE`` on Windows, so no other program can share our port
and a port already held by another program is detected; then the next free
port (up to +20) is used (Review Focus 5). The bound port is returned by
:meth:`IngestServer.start` - the caller must show it and persist it for the
installers it builds.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.frames import CloseCode

from tokenvault.common.models import UsageEvent
from tokenvault.common.protocol import MAX_FRAME_BYTES
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.ingest_session import CLOSE_REPLACED, Session
from tokenvault.manager.tls import CertInfo, load_server_context, short_fp

log = logging.getLogger("tokenvault.manager.ingest")

DEFAULT_PORT = 47800
PORT_FALLBACKS = 20


class IngestStore(Protocol):
    def upsert_events(self, device_id: str, events: Iterable[UsageEvent]) -> int: ...

    def upsert_device(
        self,
        device_id: str,
        team: str | None,
        email: str | None,
        last_seen: float,
        windows_user: str | None = None,
        hostname: str | None = None,
    ) -> None: ...


def _bind(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host, port))
        sock.listen(128)
    except BaseException:
        sock.close()
        raise
    return sock


def bind_with_fallback(host: str, port: int, tries: int = PORT_FALLBACKS) -> socket.socket:
    """Bind ``port``, else ``port+1`` ... ``port+tries``; port 0 picks an ephemeral one."""
    if port == 0:
        return _bind(host, 0)
    last: OSError | None = None
    for p in range(port, min(port + tries, 65535) + 1):
        try:
            sock = _bind(host, p)
        except OSError as e:
            last = e
            continue
        if p != port:
            log.warning("port %d is taken; ingest server uses port %d instead", port, p)
        return sock
    raise OSError(f"no free port in {port}..{port + tries}: {last}")


class IngestServer:
    def __init__(
        self,
        store: IngestStore,
        enrollment: Enrollment,
        cert: CertInfo,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        on_event: Callable[[str, int], None] | None = None,
        max_connections: int = 500,
        *,
        hello_timeout: float = 10.0,
        max_batches_per_s: float = 20.0,
        ping_interval: float = 20.0,
    ) -> None:
        self.store = store
        self.enrollment = enrollment
        self.cert = cert
        self.host = host
        self.requested_port = port
        self.on_event = on_event  # (device_id, new-or-grown rows), called on the server thread
        self.max_connections = max_connections
        self.hello_timeout = hello_timeout
        self.max_batches_per_s = max_batches_per_s
        self.ping_interval = ping_interval
        self.port: int | None = None
        self.last_event_at: float | None = None
        self.rejected_count = 0
        self.hellos_received = 0
        self._lock = threading.Lock()
        self._by_device: dict[str, ServerConnection] = {}
        self._active = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Server | None = None
        self._executor: ThreadPoolExecutor | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self, port: int | None = None) -> int:
        """Start serving; return the bound port. ``port`` overrides the constructor's."""
        if self._thread is not None:
            raise RuntimeError("ingest server already running")
        if port is not None:
            self.requested_port = port
        ctx = load_server_context(self.cert)
        sock = bind_with_fallback(self.host, self.requested_port)
        self.port = int(sock.getsockname()[1])
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tv-ingest")
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        errors: list[BaseException] = []

        async def open_server() -> Server:
            return await serve(
                self._handler,
                sock=sock,
                ssl=ctx,
                max_size=MAX_FRAME_BYTES,
                compression=None,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_interval,
                open_timeout=self.hello_timeout,
                server_header=None,
            )

        def run() -> None:
            asyncio.set_event_loop(loop)
            try:
                self._server = loop.run_until_complete(open_server())
            except BaseException as e:
                errors.append(e)
                sock.close()
                loop.close()
                ready.set()
                return
            ready.set()
            try:
                loop.run_forever()
            finally:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        self._loop = loop
        self._thread = threading.Thread(target=run, name="tv-ingest-loop", daemon=True)
        self._thread.start()
        ready.wait(30)
        if errors:
            self._reset()
            raise errors[0]
        log.info(
            "ingest server listening on %s:%d (cert %s)",
            self.host,
            self.port,
            short_fp(self.cert.fingerprint_sha256),
        )
        return self.port

    def stop(self, timeout: float = 10.0) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return

        async def shutdown() -> None:
            if self._server is not None:
                self._server.close(close_connections=True)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self._server.wait_closed(), timeout)

        try:
            asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout + 2)
        except Exception as e:
            log.warning("ingest server shutdown incomplete: %r", e)
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout)
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
        self._reset()
        log.info("ingest server stopped")

    def _reset(self) -> None:
        self._loop = None
        self._thread = None
        self._server = None
        self._executor = None
        with self._lock:
            self._by_device.clear()
        self._active = 0

    # --------------------------------------------------------------- status
    def connected_devices(self) -> set[str]:
        with self._lock:
            return set(self._by_device)

    def disconnect(self, device_id: str) -> None:
        """Close a device's live connection (e.g. right after revoking it). Thread-safe."""
        loop = self._loop
        with self._lock:
            ws = self._by_device.get(device_id)
        if loop is None or ws is None:
            return
        with contextlib.suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(ws.close(1008, "revoked"), loop)

    # ---------------------------------------------------------- connections
    def _register(self, device_id: str, ws: ServerConnection) -> None:
        """Make ``ws`` the device's connection; close an older one (runs on the loop)."""
        with self._lock:
            old = self._by_device.get(device_id)
            self._by_device[device_id] = ws
        if old is not None and old is not ws:
            log.info("device %r reconnected; closing its older connection", device_id)
            task = asyncio.get_running_loop().create_task(
                old.close(CLOSE_REPLACED, "replaced by a newer connection")
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _handler(self, ws: ServerConnection) -> None:
        if self._active >= self.max_connections:
            log.warning("too many connections (%d); refusing one", self._active)
            with contextlib.suppress(Exception):
                await ws.close(CloseCode.TRY_AGAIN_LATER, "server busy")
            return
        self._active += 1
        session = Session(self, ws)
        try:
            await session.run()
        except ConnectionClosed as e:
            if e.sent is not None and e.sent.code == CloseCode.MESSAGE_TOO_BIG:
                self.rejected_count += 1
                log.info("closed connection from %s: frame too large", session.peer)
        except Exception:
            # Anything at all (bad unicode, sqlite, bugs): log, close this one connection.
            log.exception("error on connection from %s", session.peer)
            with contextlib.suppress(Exception):
                await ws.close(CloseCode.INTERNAL_ERROR, "internal error")
        finally:
            self._active -= 1
            did = session.device_id
            if did is not None:
                with self._lock:
                    if self._by_device.get(did) is ws:
                        del self._by_device[did]
