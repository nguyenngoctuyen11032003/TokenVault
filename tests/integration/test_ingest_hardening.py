"""Review round 1: revocation, pre-auth limits, poisoned events, lifecycle edge cases."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from a2m_support import (
    DEVICE,
    FlipEnrollment,
    PoisonedOutbox,
    Rig,
    hello_frame,
    make_event,
    raw_client,
    send_recv,
    store_count,
    store_rows,
    wait_until,
)
from websockets.exceptions import ConnectionClosed

from tokenvault.adapter.transport import Endpoint, Transport
from tokenvault.common.protocol import Batch, Hello, Receipt, Reject, Welcome, encode_msg
from tokenvault.manager import ingest_server as ingest_mod
from tokenvault.manager.ingest_server import IngestServer
from tokenvault.manager.tls import _write_new_certificate


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.close()


def _closed_code(ws: object) -> int | None:
    with pytest.raises(ConnectionClosed) as ei:
        while True:
            ws.recv(timeout=10)  # type: ignore[attr-defined]
    return ei.value.rcvd.code if ei.value.rcvd is not None else None


# ------------------------------------------------------------ 1. revocation


def test_unnoticed_revocation_enforced_on_next_batch(rig: Rig) -> None:
    rig.start_server()
    key = rig.enroll()
    with raw_client(rig.port) as ws:
        assert isinstance(send_recv(ws, hello_frame(key=key)), Welcome)
        assert isinstance(send_recv(ws, encode_msg(Batch([make_event(1)]))), Receipt)
        rig.enrollment.revoke_device(DEVICE)  # behind the server's back
        reply = send_recv(ws, encode_msg(Batch([make_event(2)])))
        assert isinstance(reply, Reject) and reply.reason.startswith("auth")
        assert _closed_code(ws) == 4003
    assert [k for k, _ in store_rows(rig.store)] == ["msg_1"]


def test_server_revoke_device_closes_live_session(rig: Rig) -> None:
    rig.start_server()
    key = rig.enroll()
    with raw_client(rig.port) as ws:
        assert isinstance(send_recv(ws, hello_frame(key=key)), Welcome)
        assert wait_until(lambda: rig.server.connected_devices() == {DEVICE})
        rig.server.revoke_device(DEVICE)
        assert _closed_code(ws) == 4003
    assert [d.revoked for d in rig.enrollment.list_devices()] == [True]
    assert wait_until(lambda: rig.server.connected_devices() == set())


def test_server_revoke_package_closes_all_its_devices(rig: Rig) -> None:
    rig.start_server()
    other = rig.enrollment.create_package("Ops", None)
    k1, k2 = rig.enroll("a-1"), rig.enroll("a-2")
    k3 = rig.enrollment.register_device(other.secret, "b-1", "Ops")
    assert k3
    with raw_client(rig.port) as a1, raw_client(rig.port) as a2, raw_client(rig.port) as b1:
        assert isinstance(send_recv(a1, hello_frame("a-1", key=k1)), Welcome)
        assert isinstance(send_recv(a2, hello_frame("a-2", key=k2)), Welcome)
        assert isinstance(send_recv(b1, hello_frame("b-1", key=k3)), Welcome)
        assert wait_until(lambda: len(rig.server.connected_devices()) == 3)
        rig.server.revoke_package(rig.package.package_id)
        assert _closed_code(a1) == 4003
        assert _closed_code(a2) == 4003
        reply = send_recv(b1, encode_msg(Batch([make_event(1, device_id="b-1")])))
        assert isinstance(reply, Receipt)
    assert not rig.enrollment.verify_device("a-1", k1)


def test_revocation_between_verify_and_register_is_caught(rig: Rig) -> None:
    key = rig.enroll()
    flip = FlipEnrollment(rig.enrollment)
    rig.server = IngestServer(rig.store, flip, rig.cert, host="127.0.0.1", port=0)  # type: ignore[arg-type]
    rig.port = rig.server.start()
    with raw_client(rig.port) as ws:
        reply = send_recv(ws, hello_frame(key=key))
        assert isinstance(reply, Reject) and reply.reason.startswith("auth")
    assert flip.calls >= 2
    assert rig.server.connected_devices() == set()


# -------------------------------------------------------- 2. pre-auth limits


def test_oversized_preauth_frame_closes_connection(rig: Rig) -> None:
    rig.start_server()
    with raw_client(rig.port) as ws:
        with pytest.raises(ConnectionClosed) as ei:
            ws.send("x" * 5000)  # > 4 KiB before authenticating
            ws.recv(timeout=10)
        assert ei.value.rcvd is not None and ei.value.rcvd.code == 1009
    assert rig.server.hellos_received == 0


def test_large_batch_after_auth_still_works(rig: Rig) -> None:
    rig.start_server()
    events = [make_event(i, session_title="x" * 200) for i in range(200)]
    frame = encode_msg(Batch(events))
    assert len(frame) > 50_000
    with raw_client(rig.port) as ws:
        assert isinstance(send_recv(ws, hello_frame(secret=rig.package.secret)), Welcome)
        reply = send_recv(ws, frame)
        assert isinstance(reply, Receipt) and len(reply.message_ids) == 200
    assert store_count(rig.store) == 200


def test_unauthenticated_connections_per_ip_capped(rig: Rig) -> None:
    rig.start_server(max_unauth_per_ip=3)
    idle = [raw_client(rig.port) for _ in range(3)]
    try:
        assert wait_until(lambda: rig.server.unauthenticated_count() == 3)
        with pytest.raises(Exception):  # noqa: B017 - refused at accept: handshake fails
            extra = raw_client(rig.port)
            extra.recv(timeout=10)
        idle.pop().close()
        assert wait_until(lambda: rig.server.unauthenticated_count() == 2)
        with raw_client(rig.port) as ws:  # a slot is free again; authenticated ones don't count
            assert isinstance(send_recv(ws, hello_frame(secret=rig.package.secret)), Welcome)
            assert wait_until(lambda: rig.server.unauthenticated_count() == 2)
            key2 = rig.enroll("dev-2")
            with raw_client(rig.port) as ws2:
                assert isinstance(send_recv(ws2, hello_frame("dev-2", key=key2)), Welcome)
    finally:
        for ws in idle:
            ws.close()


# ----------------------------------------------------- 5. poisoned events


def test_server_drops_bad_timestamp_stores_neighbours(rig: Rig) -> None:
    rig.start_server()
    events = [make_event(1), make_event(2, timestamp="not-a-date"), make_event(3)]
    with raw_client(rig.port) as ws:
        assert isinstance(send_recv(ws, hello_frame(secret=rig.package.secret)), Welcome)
        reply = send_recv(ws, encode_msg(Batch(events)))
        assert isinstance(reply, Receipt)
        assert sorted(reply.message_ids) == ["msg_1", "msg_2", "msg_3"]
        # connection still usable
        assert isinstance(send_recv(ws, encode_msg(Batch([make_event(4)]))), Receipt)
    assert [k for k, _ in store_rows(rig.store)] == ["msg_1", "msg_3", "msg_4"]


def test_adapter_drops_unencodable_row_and_delivers_rest(rig: Rig) -> None:
    rig.start_server()
    inner = rig.outbox()
    inner.enqueue([make_event(i) for i in range(3)])
    ob = PoisonedOutbox(inner, make_event(99, session_title="bad \ud800"))
    t = rig.transport(ob)
    t.start()
    assert wait_until(lambda: store_count(rig.store) == 3 and inner.stats().waiting == 0)
    assert ("poison", 7) in ob.acks
    assert t.state == "connected"


# --------------------------------------------- 3. expired cert still pinned


def test_expired_manager_cert_still_accepted_by_pin(tmp_path: Path) -> None:
    expired = _write_new_certificate(
        tmp_path / "tls", valid_for=timedelta(days=-1), backdate=timedelta(days=2)
    )
    r = Rig(tmp_path)  # ensure_certificate keeps the expired pair (warning only)
    try:
        assert r.cert.fingerprint_sha256 == expired.fingerprint_sha256
        r.start_server()
        ob = r.outbox()
        ob.enqueue([make_event(1)])
        t = r.transport(ob)
        t.start()
        assert wait_until(lambda: store_count(r.store) == 1)
    finally:
        r.close()


# ------------------------------------------------------------- 6. pin format


@pytest.mark.parametrize("pin", ["", "abc", "g" * 64, "a" * 63, "a" * 65, "١" * 64])
def test_malformed_pin_refuses_to_start(pin: str) -> None:
    with pytest.raises(ValueError):
        Transport(object(), Endpoint(["127.0.0.1"], 1, pin), lambda: Hello("d", "s", None, "t", 1))  # type: ignore[arg-type]


def test_pin_normalisation_accepts_colons_and_case(rig: Rig) -> None:
    rig.start_server()
    fp = rig.cert.fingerprint_sha256.upper()
    pretty = " " + ":".join(fp[i : i + 2] for i in range(0, 64, 2)) + " "
    ob = rig.outbox()
    ob.enqueue([make_event(1)])
    t = rig.transport(ob, fingerprint=pretty)
    t.start()
    assert wait_until(lambda: store_count(rig.store) == 1)


# ------------------------------------------------- 7/8/9. lifecycle edges


def test_transport_stop_from_its_own_callback(rig: Rig) -> None:
    rig.start_server()
    ob = rig.outbox()
    holder: list[Transport] = []
    errors: list[BaseException] = []

    def on_state(s: str) -> None:
        if s == "connected":
            try:
                holder[0].stop()
            except BaseException as e:  # pragma: no cover - the bug we guard against
                errors.append(e)

    t = rig.transport(ob, on_state=on_state)
    holder.append(t)
    t.start()
    assert wait_until(lambda: not t.running)
    assert errors == []
    t.start()  # restartable after a self-stop
    assert wait_until(lambda: t.running)


def test_transport_double_start_refused(rig: Rig) -> None:
    rig.start_server()
    t = rig.transport(rig.outbox())
    t.start()
    with pytest.raises(RuntimeError):
        t.start()
    t.stop()
    assert not t.running
    t.start()
    assert wait_until(lambda: t.state == "connected")


def test_server_stop_from_on_event_callback(rig: Rig) -> None:
    errors: list[BaseException] = []
    done = threading.Event()

    def on_event(_d: str, _n: int) -> None:
        try:
            rig.server.stop()
        except BaseException as e:  # pragma: no cover
            errors.append(e)
        done.set()

    rig.start_server(on_event=on_event)
    ob = rig.outbox()
    ob.enqueue([make_event(1)])
    rig.transport(ob).start()
    assert done.wait(20)
    assert wait_until(lambda: not rig.server.running)
    assert errors == []
    assert rig.server.start(port=0) > 0  # restartable


def test_server_start_fails_cleanly_when_no_port_binds(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_bind(host: str, port: int) -> object:
        raise OSError("address in use")

    monkeypatch.setattr(ingest_mod, "_bind", no_bind)
    srv = rig.make_server(port=47800)
    with pytest.raises(RuntimeError, match="address in use"):
        srv.start()
    assert not srv.running and srv.port is None


def test_server_start_fails_cleanly_when_serve_fails(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_serve(*_a: object, **_k: object) -> object:
        raise OSError("serve exploded")

    monkeypatch.setattr(ingest_mod, "serve", broken_serve)
    srv = rig.make_server()
    before = {t.name for t in threading.enumerate()}
    with pytest.raises(RuntimeError, match="serve exploded"):
        srv.start()
    assert not srv.running and srv.port is None
    assert wait_until(
        lambda: not ({t.name for t in threading.enumerate()} - before)
    ), "server threads leaked"
    monkeypatch.undo()
    assert srv.start() > 0
