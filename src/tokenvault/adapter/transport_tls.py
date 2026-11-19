"""Certificate pinning for the adapter transport (no CA, no host-name check).

The pin is enforced in :meth:`connection_made` of the websockets connection,
which asyncio calls right after the TLS handshake completes and before the
WebSocket upgrade request is written. On mismatch the transport is aborted and
all further writes are suppressed, so nothing at all is sent to that peer.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ssl
from typing import Any, cast

from websockets.asyncio.client import ClientConnection


class PinMismatch(ConnectionError):
    """The server's certificate is not the pinned Manager certificate."""


def normalize_fp(fp: str) -> str:
    return fp.replace(":", "").replace(" ", "").strip().lower()


def short_fp(fp: str) -> str:
    return (fp[:12] + "...") if fp else "<none>"


def client_ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    # Trust comes from the pinned fingerprint, checked right after the handshake.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def peer_fingerprint(transport: Any) -> str | None:
    """Lowercase hex SHA-256 of the peer's DER certificate, or None."""
    try:
        sslobj = transport.get_extra_info("ssl_object")
        der = sslobj.getpeercert(binary_form=True) if sslobj is not None else None
    except Exception:
        return None
    return hashlib.sha256(der).hexdigest() if der else None


def pinned_connection_class(expected: str, holder: dict[str, str]) -> type[ClientConnection]:
    """A ClientConnection subclass that refuses peers whose cert isn't ``expected``.

    On mismatch, ``holder["mismatch"]`` gets a log-safe description.
    """

    class PinnedConnection(ClientConnection):
        _pin_failed = False

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            super().connection_made(transport)
            got = peer_fingerprint(transport)
            if got is None or not expected or not hmac.compare_digest(got, expected):
                self._pin_failed = True
                holder["mismatch"] = f"got {short_fp(got or '')}, pinned {short_fp(expected)}"
                cast(asyncio.Transport, transport).abort()

        def send_data(self) -> None:
            if self._pin_failed:
                return  # never write a byte to an unverified peer
            super().send_data()

    return PinnedConnection
