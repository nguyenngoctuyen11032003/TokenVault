"""UDP discovery responder: lets Adapters find the Manager when its IP changed.

Discovery is optional: if the UDP port is taken or the bind fails, the responder
logs a warning, disables itself and ``start()`` returns 0 (never raises).

Abuse resistance: only well-formed queries for OUR ``manager_id`` are answered
(everything else is dropped silently), at most ``max_per_s`` answers per second
per source IP, and the answer (~195 bytes) is under 2.5x the size of a minimal
query (~85 bytes), so the Manager is a poor reflector. The answer carries only
public data (ingest port + certificate fingerprint).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections import deque
from collections.abc import Callable

from tokenvault.common.discovery_wire import (
    DISCOVERY_PORT,
    MAX_DATAGRAM,
    build_answer,
    parse_query,
)

log = logging.getLogger("tokenvault.manager.discovery")

_MAX_TRACKED_SOURCES = 1024


class DiscoveryResponder:
    def __init__(
        self,
        manager_id: str,
        get_endpoint: Callable[[], tuple[int, str]],
        bind_host: str = "0.0.0.0",
        port: int = DISCOVERY_PORT,
        *,
        max_per_s: int = 5,
    ) -> None:
        self.manager_id = manager_id
        self._get_endpoint = get_endpoint
        self._bind_host = bind_host
        self._port = port
        self._max_per_s = max_per_s
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._hits: dict[str, deque[float]] = {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def bound_port(self) -> int:
        """The UDP port actually bound (0 when not running)."""
        return int(self._sock.getsockname()[1]) if self._sock is not None else 0

    def start(self) -> int:
        """Bind and serve in a daemon thread. Returns the bound port, 0 if disabled."""
        if self._sock is not None:
            return self.bound_port
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((self._bind_host, self._port))
            sock.settimeout(0.2)
        except OSError as e:
            log.warning("LAN discovery disabled (cannot bind UDP %d): %s", self._port, e)
            sock.close()
            return 0
        self._sock = sock
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._serve, args=(sock,), name="tv-discovery", daemon=True
        )
        self._thread.start()
        return int(sock.getsockname()[1])

    def stop(self) -> None:
        """Idempotent; returns within about a second."""
        sock, thread = self._sock, self._thread
        self._sock = None
        self._thread = None
        self._stopping.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(1.0)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _allowed(self, ip: str, now: float) -> bool:
        hits = self._hits.get(ip)
        if hits is None:
            if len(self._hits) >= _MAX_TRACKED_SOURCES:
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 1.0}
                if len(self._hits) >= _MAX_TRACKED_SOURCES:
                    return False
            hits = self._hits[ip] = deque()
        while hits and now - hits[0] >= 1.0:
            hits.popleft()
        if len(hits) >= self._max_per_s:
            return False
        hits.append(now)
        return True

    def _serve(self, sock: socket.socket) -> None:
        while not self._stopping.is_set():
            try:
                data, addr = sock.recvfrom(MAX_DATAGRAM + 1)
            except TimeoutError:
                continue
            except OSError:
                if self._stopping.is_set():
                    return
                continue  # e.g. Windows WSAECONNRESET after an ICMP unreachable
            try:
                self._handle(sock, data, addr)
            except Exception:  # the receive loop must never die
                log.debug("discovery datagram handling failed", exc_info=True)

    def _handle(self, sock: socket.socket, data: bytes, addr: tuple[str, int]) -> None:
        query = parse_query(data)  # oversized (513 bytes) data is rejected here
        if query is None or query.manager_id != self.manager_id:
            return
        if not self._allowed(addr[0], time.monotonic()):
            return
        port, fingerprint = self._get_endpoint()
        sock.sendto(build_answer(self.manager_id, port, fingerprint, query.nonce), addr)
