"""UDP discovery responder: lets Adapters find the Manager when its IP changed.

Discovery is optional: if the UDP port is taken or the bind fails, the responder
logs a warning, disables itself and ``start()`` returns 0 (never raises).

Abuse resistance: only well-formed queries for OUR ``manager_id`` are answered
(everything else is dropped silently); at most ``max_per_s`` answers per second
per source IP (bounded LRU table, oldest source evicted in O(1), so a flood of
distinct sources never locks legitimate Adapters out); and a GLOBAL token bucket
(``global_rate`` answers/s, ``global_burst`` burst) so rotating/spoofed sources
cannot turn the Manager into a reflector at scale. Amplification is small: the
answer is < 2.5x the query (measured: 75 B query -> 162 B answer for a 1-char
manager_id = 2.16x; 110 B -> 197 B for a 36-char id = 1.8x). The answer carries
only public data (ingest port + certificate fingerprint).

Network notes: IPv4 only. Adapters broadcast to the limited broadcast address
(which leaves only through the interface the OS picks: multi-NIC/VPN hosts may
need the subnet broadcast) and to each local ``a.b.c.255`` (/24 assumed). The
Manager machine must allow UDP 47801 inbound (firewall rule at install).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from tokenvault.common.discovery_wire import (
    DISCOVERY_PORT,
    MAX_DATAGRAM,
    build_answer,
    parse_query,
)

log = logging.getLogger("tokenvault.manager.discovery")

MAX_TRACKED_SOURCES = 1024


class DiscoveryResponder:
    def __init__(
        self,
        manager_id: str,
        get_endpoint: Callable[[], tuple[int, str]],
        bind_host: str = "0.0.0.0",
        port: int = DISCOVERY_PORT,
        *,
        max_per_s: int = 5,
        global_rate: float = 50.0,
        global_burst: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.manager_id = manager_id
        self._get_endpoint = get_endpoint
        self._bind_host = bind_host
        self._port = port
        self._max_per_s = max_per_s
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._global_rate = global_rate
        self._global_burst = global_burst
        self._tokens = global_burst
        self._tokens_at: float | None = None

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

    def _admit(self, ip: str, now: float) -> bool:
        """Per-source (LRU-bounded) and global token-bucket admission."""
        hits = self._hits.get(ip)
        if hits is None:
            while len(self._hits) >= MAX_TRACKED_SOURCES:
                self._hits.popitem(last=False)  # evict the oldest source, O(1)
            hits = self._hits[ip] = deque()
        else:
            self._hits.move_to_end(ip)
        while hits and now - hits[0] >= 1.0:
            hits.popleft()
        if len(hits) >= self._max_per_s:
            return False
        last = self._tokens_at if self._tokens_at is not None else now
        gained = max(0.0, now - last) * self._global_rate
        self._tokens = min(self._global_burst, self._tokens + gained)
        self._tokens_at = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        hits.append(now)
        return True

    def _serve(self, sock: socket.socket) -> None:
        while not self._stopping.is_set():
            try:
                data, addr = sock.recvfrom(MAX_DATAGRAM + 1)
            except TimeoutError:
                continue
            except OSError:
                if self._stopping.is_set() or sock.fileno() == -1:
                    return
                # e.g. Windows WSAECONNRESET after an ICMP unreachable; never spin
                self._stopping.wait(0.1)
                continue
            try:
                self._handle(sock, data, addr)
            except Exception:  # the receive loop must never die
                log.debug("discovery datagram handling failed", exc_info=True)

    def _handle(self, sock: socket.socket, data: bytes, addr: tuple[str, int]) -> None:
        query = parse_query(data)  # oversized (513 bytes) data is rejected here
        if query is None or query.manager_id != self.manager_id:
            return
        if not self._admit(addr[0], self._clock()):
            return
        port, fingerprint = self._get_endpoint()
        sock.sendto(build_answer(self.manager_id, port, fingerprint, query.nonce), addr)
