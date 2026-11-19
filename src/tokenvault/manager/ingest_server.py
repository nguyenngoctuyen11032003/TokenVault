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

Resource limits (pre-auth)
--------------------------
* Frames: 4 KiB until authenticated, ``MAX_FRAME_BYTES`` after; ``max_queue=2``.
* Connections are counted from ``connection_made`` (right after the TLS
  handshake, before the HTTP upgrade): at most ``max_connections`` in total and
  ``max_unauth_per_ip`` not-yet-authenticated ones per remote IP; extra ones
  are aborted at once. NOT covered by these counters: sockets still in the TLS
  handshake - those are bounded by ``ssl_handshake_timeout`` (10 s) and the
  listen backlog. The Hello must arrive within ``hello_timeout`` (10 s).

Revocation
----------
Use :meth:`IngestServer.revoke_device` / :meth:`IngestServer.revoke_package`:
they update ``enrollment.db`` AND close the live sessions (code 4003). A
revocation made directly on the Enrollment is still enforced on the session's
next batch (see :mod:`tokenvault.manager.ingest_session`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import threading
from collections import defaultdict
from collections.abc import Callable, Coroutine, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.frames import CloseCode

from tokenvault.common.models import UsageEvent
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.ingest_session import (
    CLOSE_REPLACED,
    CLOSE_REVOKED,
    PREAUTH_MAX_BYTES,
    Session,
)
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


def _guarded_connection(server: IngestServer) -> type[ServerConnection]:
    class GuardedConnection(ServerConnection):
        tv_ip = "?"
        tv_counted = False
        tv_unauth = False

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            peer = transport.get_extra_info("peername")
            self.tv_ip = str(peer[0]) if peer else "?"
            admitted = server._admit(self)
            super().connection_made(transport)
            if not admitted:
                transport.abort()  # type: ignore[attr-defined]

        def connection_lost(self, exc: Exception | None) -> None:
            server._release(self)
            super().connection_lost(exc)

    return GuardedConnection


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
        max_unauth_per_ip: int = 20,
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
        self.max_unauth_per_ip = max_unauth_per_ip
        self.hello_timeout = hello_timeout
        self.max_batches_per_s = max_batches_per_s
        self.ping_interval = ping_interval
        self.port: int | None = None
        self.last_event_at: float | None = None
        self.rejected_count = 0
        self.refused_count = 0  # connections aborted at accept (caps)
        self.hellos_received = 0
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._total = 0
        self._unauth: dict[str, int] = defaultdict(int)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Server | None = None
        self._executor: ThreadPoolExecutor | None = None

    # ------------------------------------------------------------ lifecycle
    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def start(self, port: int | None = None) -> int:
        """Start serving; return the bound port. Raises RuntimeError if it cannot start."""
        if self._thread is not None:
            raise RuntimeError("ingest server already running")
        if port is not None:
            self.requested_port = port
        try:
            ctx = load_server_context(self.cert)
            sock = bind_with_fallback(self.host, self.requested_port)
        except Exception as e:
            raise RuntimeError(f"ingest server cannot start: {e}") from e
        executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tv-ingest")
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        errors: list[BaseException] = []

        async def open_server() -> Server:
            return await serve(
                self._handler,
                sock=sock,
                ssl=ctx,
                ssl_handshake_timeout=10,
                max_size=PREAUTH_MAX_BYTES,
                max_queue=2,
                compression=None,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_interval,
                open_timeout=self.hello_timeout,
                server_header=None,
                create_connection=_guarded_connection(self),
            )

        def run() -> None:
            asyncio.set_event_loop(loop)
            try:
                try:
                    self._server = loop.run_until_complete(open_server())
                except BaseException as e:
                    errors.append(e)
                    return
                finally:
                    ready.set()
                loop.run_forever()
            finally:
                sock.close()
                with contextlib.suppress(Exception):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
                executor.shutdown(wait=True, cancel_futures=True)
                if self._thread is threading.current_thread():
                    self._reset()

        self._executor = executor
        self._loop = loop
        self.port = int(sock.getsockname()[1])
        thread = threading.Thread(target=run, name="tv-ingest-loop", daemon=True)
        self._thread = thread
        thread.start()
        started = ready.wait(30)
        if errors or not started:
            if not started:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(loop.stop)
            thread.join(10)
            self._reset()
            cause = errors[0] if errors else TimeoutError("server did not start within 30 s")
            raise RuntimeError(f"ingest server cannot start: {cause}") from cause
        log.info(
            "ingest server listening on %s:%d (cert %s)",
            self.host,
            self.port,
            short_fp(self.cert.fingerprint_sha256),
        )
        return self.port

    async def _shutdown(self, timeout: float) -> None:
        if self._server is not None:
            self._server.close(close_connections=True)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout)
        asyncio.get_running_loop().stop()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop serving. Safe from any thread, including the server's own callbacks."""
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        if threading.current_thread() is thread:
            # Called from on_event or another callback on the loop: just schedule it.
            self._spawn(self._shutdown(timeout))
            return
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(timeout), loop)
        except RuntimeError:
            pass  # loop already closed
        thread.join(timeout + 5)
        if thread.is_alive():
            log.warning("ingest server thread did not stop within %.0f s", timeout + 5)
            return
        log.info("ingest server stopped")

    def _reset(self) -> None:
        self._loop = None
        self._thread = None
        self._server = None
        self._executor = None
        self.port = None
        with self._lock:
            self._sessions.clear()
            self._unauth.clear()
        self._total = 0

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_loop(self, make: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        """Run ``make()`` on the server loop from any thread (no-op if stopped)."""
        loop = self._loop
        if loop is None:
            return
        if threading.current_thread() is self._thread:
            self._spawn(make())
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(lambda: self._spawn(make()))

    # --------------------------------------------------------------- status
    def connected_devices(self) -> set[str]:
        with self._lock:
            return set(self._sessions)

    def unauthenticated_count(self) -> int:
        with self._lock:
            return sum(self._unauth.values())

    # ------------------------------------------------------------ revocation
    def _close_sessions(self, sessions: list[Session], code: int, reason: str) -> None:
        for s in sessions:
            ws = s.ws
            self._on_loop(lambda ws=ws: ws.close(code, reason))  # type: ignore[misc]

    def disconnect(self, device_id: str) -> None:
        """Close a device's live connection. Thread-safe."""
        with self._lock:
            s = self._sessions.get(device_id)
        if s is not None:
            self._close_sessions([s], CLOSE_REVOKED, "disconnected")

    def revoke_device(self, device_id: str) -> None:
        """Revoke in enrollment.db and close the device's live session. Thread-safe."""
        self.enrollment.revoke_device(device_id)
        with self._lock:
            s = self._sessions.get(device_id)
        if s is not None:
            self._close_sessions([s], CLOSE_REVOKED, "revoked")
        log.info("revoked device %r", device_id)

    def revoke_package(self, package_id: str) -> None:
        """Revoke a package and close the live sessions of all its devices. Thread-safe."""
        self.enrollment.revoke_package(package_id)
        with self._lock:
            hit = [s for s in self._sessions.values() if s.package_id == package_id]
        self._close_sessions(hit, CLOSE_REVOKED, "revoked")
        log.info("revoked package %s (%d live session(s) closed)", package_id, len(hit))

    # ---------------------------------------------------------- connections
    def _admit(self, conn: Any) -> bool:
        """Accept-time accounting (loop thread). False => the connection is aborted."""
        with self._lock:
            if self._total >= self.max_connections:
                why = "too many connections"
            elif self._unauth[conn.tv_ip] >= self.max_unauth_per_ip:
                why = "too many unauthenticated connections from this address"
            else:
                self._total += 1
                self._unauth[conn.tv_ip] += 1
                conn.tv_counted = conn.tv_unauth = True
                return True
        self.refused_count += 1
        log.warning("refusing connection from %s: %s", conn.tv_ip, why)
        return False

    def _mark_authenticated(self, conn: Any) -> None:
        with self._lock:
            if getattr(conn, "tv_unauth", False):
                conn.tv_unauth = False
                self._dec_unauth(conn.tv_ip)

    def _release(self, conn: Any) -> None:
        with self._lock:
            if not getattr(conn, "tv_counted", False):
                return
            conn.tv_counted = False
            self._total -= 1
            if conn.tv_unauth:
                conn.tv_unauth = False
                self._dec_unauth(conn.tv_ip)

    def _dec_unauth(self, ip: str) -> None:
        self._unauth[ip] -= 1
        if self._unauth[ip] <= 0:
            del self._unauth[ip]

    def _register(self, session: Session) -> None:
        """Make ``session`` the device's live one; close an older one (loop thread)."""
        assert session.device_id is not None
        with self._lock:
            old = self._sessions.get(session.device_id)
            self._sessions[session.device_id] = session
        if old is not None and old is not session:
            log.info("device %r reconnected; closing its older connection", session.device_id)
            self._spawn(old.ws.close(CLOSE_REPLACED, "replaced by a newer connection"))

    async def _handler(self, ws: ServerConnection) -> None:
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
            did = session.device_id
            if did is not None:
                with self._lock:
                    if self._sessions.get(did) is session:
                        del self._sessions[did]
