"""Adapter side of LAN discovery: find the Manager by UDP broadcast.

Trust: an answer is accepted ONLY if its nonce matches our query, its manager_id
matches and its certificate fingerprint equals the PINNED one (constant-time
compare). The TLS pin is still checked by the Transport afterwards, so a spoofed
answer can at worst make the Adapter try (and refuse) an impostor's address.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import socket
import time
from collections.abc import Sequence

from tokenvault.adapter.transport import Endpoint
from tokenvault.adapter.transport_tls import validate_fp
from tokenvault.common.discovery_wire import (
    DISCOVERY_PORT,
    MAX_DATAGRAM,
    build_query,
    parse_answer,
)

log = logging.getLogger("tokenvault.adapter.discovery")


def local_broadcast_addrs() -> list[str]:
    """Limited broadcast plus ``a.b.c.255`` for each local IPv4 (/24 assumed:
    the real netmask is not available in the stdlib; non-/24 LANs still get the
    limited broadcast)."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(str(info[4][0]))
    except OSError:
        pass
    try:  # UDP "connect" sends nothing; it only selects the outgoing interface
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ips.add(str(s.getsockname()[0]))
    except OSError:
        pass
    out = ["255.255.255.255"]
    for ip in sorted(ips):
        parts = ip.split(".")
        if len(parts) == 4 and not ip.startswith(("127.", "169.254.", "0.")):
            out.append(".".join([*parts[:3], "255"]))
    return list(dict.fromkeys(out))


def _discover(
    manager_id: str,
    fingerprint: str,
    timeout_s: float,
    broadcast_addrs: Sequence[str] | None,
    port: int,
) -> tuple[str, int] | None:
    pin = validate_fp(fingerprint).encode("ascii")
    nonce = secrets.token_hex(8)
    query = build_query(manager_id, nonce)
    targets = list(broadcast_addrs) if broadcast_addrs is not None else local_broadcast_addrs()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 0))
        for target in targets:
            try:
                sock.sendto(query, (target, port))
            except OSError as e:
                log.debug("discovery send to %s failed: %s", target, e)
        deadline = time.monotonic() + timeout_s
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(MAX_DATAGRAM + 1)
            except TimeoutError:
                break
            except OSError:
                continue
            ans = parse_answer(data, manager_id, nonce)
            if ans is None or not hmac.compare_digest(ans.fingerprint.encode("ascii"), pin):
                continue
            return str(addr[0]), ans.port
    return None


def discover(
    manager_id: str,
    fingerprint: str,
    timeout_s: float = 2.0,
    broadcast_addrs: Sequence[str] | None = None,
    port: int = DISCOVERY_PORT,
) -> Endpoint | None:
    """Broadcast a query; return the Manager's Endpoint or None. Never raises."""
    try:
        found = _discover(manager_id, fingerprint, timeout_s, broadcast_addrs, port)
    except Exception as e:
        log.debug("discovery failed: %r", e)
        return None
    if found is None:
        return None
    ip, ingest_port = found
    return Endpoint(hosts=[ip], port=ingest_port, fingerprint=fingerprint)


class Resolver:
    """Callable for ``Transport(resolver=...)``; ``last_port`` is the ingest port
    announced by the last found Manager (None until found).

    LIMITATION: Transport connects to ``endpoint.port`` on every host, so if the
    announced port differs from the configured one the app must rebuild the
    Endpoint (or Transport) using ``last_port``.
    """

    def __init__(
        self,
        manager_id: str,
        fingerprint: str,
        port: int = DISCOVERY_PORT,
        timeout_s: float = 2.0,
        broadcast_addrs: Sequence[str] | None = None,
    ) -> None:
        self._args = (manager_id, fingerprint, timeout_s, broadcast_addrs, port)
        self.last_port: int | None = None

    def __call__(self) -> list[str]:
        m, f, t, b, p = self._args
        ep = discover(m, f, t, b, p)
        if ep is None:
            return []
        self.last_port = ep.port
        return list(ep.hosts)


def make_resolver(
    manager_id: str,
    fingerprint: str,
    port: int = DISCOVERY_PORT,
    timeout_s: float = 2.0,
    broadcast_addrs: Sequence[str] | None = None,
) -> Resolver:
    """Resolver for ``Transport``; ``broadcast_addrs`` is for tests."""
    return Resolver(manager_id, fingerprint, port, timeout_s, broadcast_addrs)
