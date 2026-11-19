"""Helpers for the Adapter -> Manager integration tests (real loopback sockets)."""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from websockets.sync.client import ClientConnection, connect

from tokenvault.adapter.outbox import Outbox, PendingEvent
from tokenvault.adapter.transport import Endpoint, Transport
from tokenvault.common.models import UsageEvent
from tokenvault.common.protocol import Hello, decode_msg, encode_msg
from tokenvault.manager.enrollment import Enrollment, Package
from tokenvault.manager.ingest_server import IngestServer
from tokenvault.manager.store import Store
from tokenvault.manager.tls import CertInfo, ensure_certificate, load_server_context

DEVICE = "dev-1"


def make_event(n: int, **kw: Any) -> UsageEvent:
    base = UsageEvent(
        uuid=f"u{n}",
        session_id="s1",
        message_id=f"msg_{n}",
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        model="claude-sonnet-4-6",
        reasoning_effort=None,
        is_sidechain=False,
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=0,
        cache_create_5m_tokens=0,
        cache_create_1h_tokens=0,
        project="proj",
        session_title="Phiên làm việc ✓",
        tool_names=("Read",),
        claude_account_email="alice@example.com",
        account_uuid="acc",
        windows_user="alice",
        hostname="PC1",
        team="Dev",
        device_id=DEVICE,
    )
    return replace(base, **kw)


def wait_until(pred: Callable[[], bool], timeout: float = 20.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def store_rows(store: Store) -> list[tuple[str, int]]:
    with store.read_snapshot() as snap:
        rows = snap.fetchall("SELECT msg_key, output FROM events ORDER BY msg_key")
    return [(str(r[0]), int(r[1])) for r in rows]


def store_count(store: Store) -> int:
    with store.read_snapshot() as snap:
        return int(snap.fetchall("SELECT COUNT(*) FROM events")[0][0])


def client_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # The websockets SYNC client over TLS 1.3 intermittently stalls on Windows (its
    # upgrade request is never seen by the asyncio server; ~2-7% of connections,
    # with plain websockets too). The asyncio client the Transport uses does not
    # (0 of 450 in the same probe), and nor does TLS 1.2, so the raw test client
    # uses TLS 1.2.
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def raw_client(port: int) -> ClientConnection:
    """A plain websocket client that does NOT pin (used to send hand-made frames)."""
    return connect(
        f"wss://127.0.0.1:{port}/",
        ssl=client_ctx(),
        proxy=None,
        compression=None,
        max_size=None,
        open_timeout=10,
        legacy=True,  # plain connection object (also usable as a context manager)
    )


def hello_frame(
    device_id: str = DEVICE, *, secret: str | None = None, key: str | None = None
) -> str:
    return encode_msg(Hello(device_id, secret, key, "Dev", 1))


class FlakyStore:
    """Store proxy whose upsert_events can be made to fail (before or after committing)."""

    def __init__(self, inner: Store) -> None:
        self.inner = inner
        self.calls = 0
        self.fail_before_commit: set[int] = set()
        self.fail_after_commit: set[int] = set()
        self.failed = threading.Event()

    def upsert_events(self, device_id: str, events: Iterable[UsageEvent]) -> int:
        self.calls += 1
        n = self.calls
        if n in self.fail_before_commit:
            self.failed.set()
            raise OSError(f"injected store failure on call {n}")
        grown = self.inner.upsert_events(device_id, events)
        if n in self.fail_after_commit:
            self.failed.set()
            raise RuntimeError(f"injected crash after commit on call {n}")
        return grown

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@dataclass
class Rig:
    root: Path
    cert: CertInfo = field(init=False)
    enrollment: Enrollment = field(init=False)
    store: Store = field(init=False)
    server: IngestServer = field(init=False)
    package: Package = field(init=False)
    port: int = 0
    keys: list[str] = field(default_factory=list)
    transports: list[Transport] = field(default_factory=list)
    outboxes: list[Outbox] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cert = ensure_certificate(self.root / "tls")
        self.enrollment = Enrollment(self.root / "enrollment.db")
        self.store = Store(self.root / "manager.db", tz_offset_minutes=420)
        self.package = self.enrollment.create_package("Dev", "test")

    def make_server(self, store: Any = None, **kw: Any) -> IngestServer:
        self.server = IngestServer(
            store if store is not None else self.store,
            self.enrollment,
            self.cert,
            host="127.0.0.1",
            port=kw.pop("port", 0),
            **kw,
        )
        return self.server

    def start_server(self, store: Any = None, **kw: Any) -> int:
        self.port = self.make_server(store, **kw).start()
        return self.port

    def outbox(self, name: str = "outbox.db") -> Outbox:
        ob = Outbox(self.root / name)
        self.outboxes.append(ob)
        return ob

    def hello_factory(self, device_id: str = DEVICE) -> Callable[[], Hello]:
        def make() -> Hello:
            if self.keys:
                return Hello(device_id, None, self.keys[-1], "Dev", 1)
            return Hello(device_id, self.package.secret, None, "Dev", 1)

        return make

    def transport(
        self,
        outbox: Any,
        *,
        fingerprint: str | None = None,
        hosts: list[str] | None = None,
        port: int | None = None,
        hello_factory: Callable[[], Hello] | None = None,
        **kw: Any,
    ) -> Transport:
        t = Transport(
            outbox,
            Endpoint(
                hosts if hosts is not None else ["127.0.0.1"],
                port if port is not None else self.port,
                fingerprint if fingerprint is not None else self.cert.fingerprint_sha256,
            ),
            hello_factory or self.hello_factory(),
            on_device_key=self.keys.append,
            **kw,
        )
        self.transports.append(t)
        return t

    def enroll(self, device_id: str = DEVICE) -> str:
        key = self.enrollment.register_device(self.package.secret, device_id, "Dev")
        assert key is not None
        return key

    def close(self) -> None:
        for t in self.transports:
            t.stop()
        try:
            self.server.stop()
        except AttributeError:
            pass
        for ob in self.outboxes:
            ob.close()
        self.store.close()
        self.enrollment.close()


def send_recv(ws: ClientConnection, frame: str, timeout: float = 10) -> Any:
    ws.send(frame)
    return decode_msg(str(ws.recv(timeout=timeout)))


def batch_frame_ascii(events: list[UsageEvent], **override: Any) -> str:
    """A batch frame built by hand with ensure_ascii=True (so \\ud800 survives as an escape)."""
    from tokenvault.common.protocol import event_to_dict

    evs = []
    for e in events:
        d = event_to_dict(e)
        d.update(override)
        evs.append(d)
    return json.dumps({"v": 1, "type": "batch", "events": evs}, ensure_ascii=True)


class RawTLSRecorder:
    """TLS listener with its own cert that records every application byte it receives."""

    def __init__(self, cert: CertInfo) -> None:
        self.ctx = load_server_context(cert)
        # TLS 1.2: the server finishes its handshake by SENDING its Finished, so a
        # completed handshake is observable even though the client then aborts
        # (with TLS 1.3 the client's Finished and its RST race on the server side).
        self.ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = int(self.sock.getsockname()[1])
        self.received = bytearray()
        self.handshakes = 0  # COMPLETED TLS handshakes
        self.accepted = 0  # TCP accepts
        self.closed = 0  # connections fully finished (peer closed / reset)
        self._stop = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            self.accepted += 1
            try:
                conn.settimeout(5)
                with self.ctx.wrap_socket(conn, server_side=True) as tls:
                    self.handshakes += 1
                    while True:
                        chunk = tls.recv(65536)
                        if not chunk:
                            break
                        self.received += chunk
            except (OSError, ssl.SSLError):
                pass
            finally:
                conn.close()
                self.closed += 1

    def close(self) -> None:
        self._stop = True
        self._t.join(5)
        self.sock.close()


def pending_ids(ob: Outbox) -> list[str]:
    return [p.message_id for p in ob.pending(10_000)]


class GrowingOutbox:
    """Outbox proxy: right after the first pending() read, the first row grows (rev bump)."""

    def __init__(self, inner: Outbox, grown: UsageEvent) -> None:
        self.inner = inner
        self.grown = grown
        self.done = False
        self.ack_results: list[tuple[int, int]] = []

    def pending(self, limit: int) -> list[PendingEvent]:
        rows = self.inner.pending(limit)
        if rows and not self.done:
            self.done = True
            assert self.inner.enqueue([self.grown]) == 1
        return rows

    def ack(self, sent: Iterable[tuple[str, int]]) -> int:
        pairs = list(sent)
        n = self.inner.ack(pairs)
        self.ack_results.append((len(pairs), n))
        return n


class PoisonedOutbox:
    """Outbox proxy that also serves one row whose event cannot be framed (lone surrogate)."""

    def __init__(self, inner: Outbox, poison: UsageEvent) -> None:
        self.inner = inner
        self.poison = PendingEvent("poison", 7, poison)
        self.poison_acked = False
        self.acks: list[tuple[str, int]] = []

    def pending(self, limit: int) -> list[PendingEvent]:
        rows = self.inner.pending(limit)
        if not self.poison_acked:
            rows = [self.poison, *rows][:limit]
        return rows

    def ack(self, sent: Iterable[tuple[str, int]]) -> int:
        pairs = list(sent)
        self.acks.extend(pairs)
        if ("poison", 7) in pairs:
            self.poison_acked = True
            pairs = [p for p in pairs if p[0] != "poison"]
            return self.inner.ack(pairs) + 1
        return self.inner.ack(pairs)

    def stats(self) -> Any:
        return self.inner.stats()


class FlipEnrollment:
    """Enrollment proxy: verify_device answers True once, then False (revoked in between)."""

    def __init__(self, inner: Enrollment) -> None:
        self.inner = inner
        self.calls = 0

    def verify_device(self, device_id: str, device_key: str) -> bool:
        self.calls += 1
        return self.calls == 1 and self.inner.verify_device(device_id, device_key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)
