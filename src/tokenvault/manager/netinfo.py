"""LAN addresses of the Manager PC (what new installers carry when no override is set)."""

from __future__ import annotations

import contextlib
import ipaddress
import socket

from tokenvault.common.config_payload import MAX_HOSTS, valid_host


def _usable(ip: str) -> bool:
    try:
        a = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return not (a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast)


def primary_ipv4() -> str | None:
    """IP of the interface holding the default route (UDP connect: no packet is sent)."""
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed anywhere real
        ip = str(s.getsockname()[0])
        return ip if _usable(ip) else None
    return None


def lan_ipv4s() -> list[str]:
    """Primary-route IPv4 first, then the other non-loopback, non-link-local ones."""
    out: list[str] = []
    first = primary_ipv4()
    if first:
        out.append(first)
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if _usable(ip) and ip not in out:
                out.append(ip)
    return out


def default_hosts() -> list[str]:
    """Up to 8 entries: LAN IPv4s (primary first) then the host name (if valid)."""
    ips = lan_ipv4s()
    name = socket.gethostname()
    hosts = ips[: MAX_HOSTS - 1] if valid_host(name) else ips[:MAX_HOSTS]
    if valid_host(name) and name.lower() not in (h.lower() for h in hosts):
        hosts.append(name)
    return hosts
