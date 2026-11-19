"""Adapter Outbox -> Transport -> IngestServer -> Store over real loopback TLS sockets."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from a2m_support import (
    DEVICE,
    FlakyStore,
    GrowingOutbox,
    RawTLSRecorder,
    Rig,
    batch_frame_ascii,
    hello_frame,
    make_event,
    raw_client,
    send_recv,
    store_count,
    store_rows,
    wait_until,
)
from websockets.exceptions import ConnectionClosed

from tokenvault.common.protocol import (
    MAX_FRAME_BYTES,
    Batch,
    Receipt,
    Reject,
    Welcome,
    decode_msg,
    encode_msg,
)
from tokenvault.manager.tls import ensure_certificate


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.close()


def _delivered(rig: Rig, ob: object, n: int) -> bool:
    return store_count(rig.store) == n and ob.stats().waiting == 0  # type: ignore[attr-defined]


# --------------------------------------------------------------- happy path


def test_event_reaches_store_under_2s(rig: Rig) -> None:
    rig.start_server()
    ob = rig.outbox()
    t = rig.transport(ob)
    t.start()
    assert wait_until(lambda: t.state == "connected"), t.state
    assert len(rig.keys) == 1  # enrolled with the package secret, got a device key
    t0 = time.perf_counter()
    ob.enqueue([make_event(1)])
    t.notify()
    assert wait_until(lambda: store_count(rig.store) == 1, timeout=5, interval=0.005)
    latency = time.perf_counter() - t0
    print(f"\nend-to-end latency: {latency * 1000:.1f} ms")
    assert latency < 2.0
    assert wait_until(lambda: ob.stats().waiting == 0)
    (dev,) = rig.store.list_devices()
    assert (dev.device_id, dev.team, dev.email, dev.windows_user, dev.hostname) == (
        DEVICE,
        "Dev",
        "alice@example.com",
        "alice",
        "PC1",
    )
    assert rig.server.connected_devices() == {DEVICE}
    assert rig.server.last_event_at is not None


def test_device_key_reconnect_works_without_secret(rig: Rig) -> None:
    rig.start_server()
    ob = rig.outbox()
    t1 = rig.transport(ob)
    t1.start()
    assert wait_until(lambda: len(rig.keys) == 1)
    t1.stop()
    hellos: list[tuple[str | None, str | None]] = []
    base = rig.hello_factory()

    def factory():  # type: ignore[no-untyped-def]
        h = base()
        hellos.append((h.enroll_secret, h.device_key))
        return h

    t2 = rig.transport(ob, hello_factory=factory)
    t2.start()
    ob.enqueue([make_event(1), make_event(2)])
    t2.notify()
    assert wait_until(lambda: _delivered(rig, ob, 2))
    assert hellos and all(s is None and k == rig.keys[0] for s, k in hellos)
    assert len(rig.keys) == 1  # no new key was issued


def test_wrong_secret_rejected_transport(rig: Rig) -> None:
    from tokenvault.common.protocol import Hello

    rig.start_server()
    ob = rig.outbox()
    ob.enqueue([make_event(1)])
    states: list[str] = []
    t = rig.transport(
        ob, hello_factory=lambda: Hello(DEVICE, "wrong", None, "Dev", 1), on_state=states.append
    )
    t.start()
    assert wait_until(lambda: t.state == "rejected")
    assert "rejected" in states and "connected" not in states
    assert ob.stats().waiting == 1 and store_count(rig.store) == 0
    assert rig.server.rejected_count >= 1


def test_revoked_device_rejected(rig: Rig) -> None:
    rig.start_server()
    key = rig.enroll()
    rig.enrollment.revoke_device(DEVICE)
    with raw_client(rig.port) as ws:
        reply = send_recv(ws, hello_frame(key=key))
        assert isinstance(reply, Reject) and reply.reason.startswith("auth")
        with pytest.raises(ConnectionClosed):
            ws.recv(timeout=10)


# ----------------------------------------------------------- Review Focus 3


def test_fingerprint_mismatch_sends_nothing_keeps_queue(rig: Rig, tmp_path: Path) -> None:
    rig.start_server()
    other = ensure_certificate(tmp_path / "other-tls")
    recorder = RawTLSRecorder(other)
    try:
        ob = rig.outbox()
        ob.enqueue([make_event(i) for i in range(5)])
        states: list[str] = []
        # Host 1: an impostor serving another cert. Host 2: the real Manager, but pinned to
        # the impostor's fingerprint, so it is "something else" too.
        t = rig.transport(
            ob,
            hosts=["127.0.0.1"],
            port=recorder.port,
            fingerprint=rig.cert.fingerprint_sha256,
            on_state=states.append,
        )
        t.start()
        assert wait_until(lambda: recorder.handshakes >= 1 and t.state == "offline")
        assert "fingerprint" in (t.last_error or "")
        t.stop()
        t2 = rig.transport(ob, fingerprint=other.fingerprint_sha256)
        t2.start()
        assert wait_until(lambda: t2.state == "offline" and "fingerprint" in (t2.last_error or ""))
        t2.stop()
        # The impostor's connections must be fully finished before asserting nothing arrived.
        assert wait_until(
            lambda: recorder.handshakes >= 1 and recorder.closed == recorder.accepted
        )
        assert bytes(recorder.received) == b""
        assert rig.server.hellos_received == 0
        assert rig.server.connected_devices() == set()
        assert ob.stats().waiting == 5
        assert store_count(rig.store) == 0
        assert rig.keys == []
        assert "connected" not in states
    finally:
        recorder.close()


# ------------------------------------------------------- offline / restarts


def test_offline_then_reconnect_delivers_all_no_duplicates(rig: Rig) -> None:
    port = rig.start_server()
    rig.server.stop()
    ob = rig.outbox()
    ob.enqueue([make_event(i) for i in range(500)])
    t = rig.transport(ob, port=port)
    t.start()
    assert wait_until(lambda: t.state == "offline")
    assert rig.start_server(port=port) == port
    assert wait_until(lambda: _delivered(rig, ob, 500), timeout=60), (
        store_count(rig.store),
        ob.stats(),
    )
    assert sum(o for _, o in store_rows(rig.store)) == 500 * 20


def test_server_restart_mid_batch_delivers_all_no_duplicates(rig: Rig) -> None:
    flaky = FlakyStore(rig.store)
    flaky.fail_after_commit = {2}  # 2nd batch is committed but its receipt never leaves
    port = rig.start_server(store=flaky)
    ob = rig.outbox()
    ob.enqueue([make_event(i) for i in range(500)])
    t = rig.transport(ob)
    t.start()
    assert flaky.failed.wait(30)
    rig.server.stop()
    assert rig.start_server(store=flaky, port=port) == port
    assert wait_until(lambda: _delivered(rig, ob, 500), timeout=60), (
        store_count(rig.store),
        ob.stats(),
    )
    assert flaky.calls >= 4  # the committed-but-unacked batch was re-sent
    assert sum(o for _, o in store_rows(rig.store)) == 500 * 20


def test_duplicate_batch_resend_counted_once(rig: Rig) -> None:
    rig.start_server()
    events = [make_event(i) for i in range(10)]
    frame = encode_msg(Batch(events))
    with raw_client(rig.port) as ws:
        assert isinstance(send_recv(ws, hello_frame(secret=rig.package.secret)), Welcome)
        r1 = send_recv(ws, frame)
        r2 = send_recv(ws, frame)
    ids = [f"msg_{i}" for i in range(10)]
    assert isinstance(r1, Receipt) and sorted(r1.message_ids) == sorted(ids)
    assert isinstance(r2, Receipt) and sorted(r2.message_ids) == sorted(ids)
    assert store_count(rig.store) == 10
    assert sum(o for _, o in store_rows(rig.store)) == 10 * 20


def test_receipt_only_after_store_commit(rig: Rig) -> None:
    flaky = FlakyStore(rig.store)
    flaky.fail_before_commit = {1, 2}
    rig.start_server(store=flaky)
    ob = rig.outbox()
    ob.enqueue([make_event(i) for i in range(3)])
    t = rig.transport(ob)
    t.start()
    assert flaky.failed.wait(20)
    assert ob.stats().waiting == 3  # no receipt for the failed write
    assert wait_until(lambda: _delivered(rig, ob, 3), timeout=30)
    assert flaky.calls >= 3


def test_stale_ack_when_row_grew_keeps_row_pending(rig: Rig) -> None:
    rig.start_server()
    inner = rig.outbox()
    inner.enqueue([make_event(i) for i in range(3)])
    grown = make_event(0, output_tokens=999)
    ob = GrowingOutbox(inner, grown)
    t = rig.transport(ob)
    t.start()
    assert wait_until(lambda: dict(store_rows(rig.store)).get("msg_0") == 999, timeout=30)
    assert wait_until(lambda: inner.stats().waiting == 0)
    # First receipt acked only 2 of 3: the grown row stayed pending and was re-sent.
    assert ob.ack_results[0] == (3, 2)
    assert store_count(rig.store) == 3


# ---------------------------------------------------------- Review Focus 5


def test_port_taken_falls_back_to_next_free(rig: Rig) -> None:
    blocker = socket.socket()
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    taken = blocker.getsockname()[1]
    try:
        bound = rig.start_server(port=taken)
        assert taken < bound <= taken + 20
        assert rig.server.port == bound
        ob = rig.outbox()
        ob.enqueue([make_event(1)])
        t = rig.transport(ob, port=bound)
        t.start()
        assert wait_until(lambda: _delivered(rig, ob, 1))
    finally:
        blocker.close()


# ------------------------------------------------------ hostile / bad input


def test_oversize_frame_closes_connection(rig: Rig) -> None:
    rig.start_server()
    with raw_client(rig.port) as ws:
        assert isinstance(send_recv(ws, hello_frame(secret=rig.package.secret)), Welcome)
        with pytest.raises(ConnectionClosed) as ei:
            # The server may close (1009) while the client is still writing the frame.
            ws.send("x" * (MAX_FRAME_BYTES + 1024))
            ws.recv(timeout=10)
        assert ei.value.rcvd is not None and ei.value.rcvd.code == 1009
    assert wait_until(lambda: rig.server.rejected_count >= 1)
    assert wait_until(lambda: rig.server.connected_devices() == set())
    with raw_client(rig.port) as ws2:  # still serving
        assert isinstance(send_recv(ws2, hello_frame("dev-2", key=rig.enroll("dev-2"))), Welcome)


def test_lone_surrogate_frame_does_not_crash_server(rig: Rig) -> None:
    rig.start_server()
    with raw_client(rig.port) as bad:
        assert isinstance(send_recv(bad, hello_frame(secret=rig.package.secret)), Welcome)
        frame = batch_frame_ascii([make_event(1)], session_title="\ud800 bad", project="\udfff")
        assert "\\ud800" in frame
        reply = send_recv(bad, frame)  # the bad event is dropped but acknowledged
        assert isinstance(reply, Receipt) and reply.message_ids == ["msg_1"]
        frame = batch_frame_ascii([make_event(9)], message_id="\ud800")
        reply = send_recv(bad, frame)  # its id cannot even be echoed: dropped, not receipted
        assert isinstance(reply, Receipt) and reply.message_ids == []
        assert store_count(rig.store) == 0
    with raw_client(rig.port) as bad_hello:
        raw_hello = (
            '{"v":1,"type":"hello","device_id":"\\ud800","enroll_secret":"x",'
            '"device_key":null,"team":"Dev","version":1}'
        )
        assert isinstance(send_recv(bad_hello, raw_hello), Reject)
    assert rig.server.rejected_count >= 1
    rig.enrollment.reset_device(DEVICE)  # the raw client above already enrolled it
    ob = rig.outbox()
    ob.enqueue([make_event(2, session_title="Tiếng Việt 😀")])
    t = rig.transport(ob)
    t.start()
    assert wait_until(lambda: _delivered(rig, ob, 1))
    assert store_rows(rig.store) == [("msg_2", 20)]


def test_unauthenticated_batch_before_hello_rejected(rig: Rig) -> None:
    rig.start_server()
    with raw_client(rig.port) as ws:
        reply = send_recv(ws, encode_msg(Batch([make_event(1)])))
        assert isinstance(reply, Reject)
        with pytest.raises(ConnectionClosed):
            ws.recv(timeout=10)
    assert rig.server.rejected_count == 1
    assert store_count(rig.store) == 0


def test_hello_timeout_closes_connection(rig: Rig) -> None:
    rig.start_server(hello_timeout=0.5)
    with raw_client(rig.port) as ws:
        reply = decode_msg(str(ws.recv(timeout=10)))
        assert isinstance(reply, Reject) and "timeout" in reply.reason
        with pytest.raises(ConnectionClosed):
            ws.recv(timeout=10)
    assert rig.server.hellos_received == 0


def test_second_connection_replaces_first(rig: Rig) -> None:
    rig.start_server()
    key = rig.enroll()
    with raw_client(rig.port) as first:
        assert isinstance(send_recv(first, hello_frame(key=key)), Welcome)
        assert wait_until(lambda: rig.server.connected_devices() == {DEVICE})
        with raw_client(rig.port) as second:
            assert isinstance(send_recv(second, hello_frame(key=key)), Welcome)
            with pytest.raises(ConnectionClosed):
                first.recv(timeout=10)
            assert rig.server.connected_devices() == {DEVICE}
            reply = send_recv(second, encode_msg(Batch([make_event(1)])))
            assert isinstance(reply, Receipt) and reply.message_ids == ["msg_1"]
    assert wait_until(lambda: rig.server.connected_devices() == set())


def test_on_event_callback_reports_device_and_count(rig: Rig) -> None:
    seen: list[tuple[str, int]] = []
    rig.start_server(on_event=lambda d, n: seen.append((d, n)))
    ob = rig.outbox()
    ob.enqueue([make_event(i) for i in range(4)])
    t = rig.transport(ob)
    t.start()
    assert wait_until(lambda: _delivered(rig, ob, 4))
    assert wait_until(lambda: sum(n for _, n in seen) == 4)
    assert {d for d, _ in seen} == {DEVICE}


def test_reenroll_of_active_device_is_refused_until_reset(rig: Rig) -> None:
    rig.start_server()
    rig.enroll()
    with raw_client(rig.port) as ws:
        reply = send_recv(ws, hello_frame(secret=rig.package.secret))
        assert isinstance(reply, Reject) and "reset the device" in reply.reason
    assert rig.server.rejected_count >= 1
    assert rig.server.reset_device(DEVICE)
    with raw_client(rig.port) as ws:
        assert isinstance(send_recv(ws, hello_frame(secret=rig.package.secret)), Welcome)
