"""LAN discovery over real loopback UDP sockets (ephemeral ports)."""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from a2m_support import Rig, make_event, store_count, wait_until

from tokenvault.adapter.discovery import discover, make_resolver
from tokenvault.common.discovery_wire import build_answer, build_query
from tokenvault.manager.discovery import DiscoveryResponder

FP = "cd" * 32
LO = ["127.0.0.1"]


@pytest.fixture
def responder() -> Iterator[DiscoveryResponder]:
    state = {"ep": (47800, FP)}
    r = DiscoveryResponder("mgr", lambda: state["ep"], bind_host="127.0.0.1", port=0)
    r.state = state  # type: ignore[attr-defined]
    assert r.start() > 0
    yield r
    r.stop()


def _port(r: DiscoveryResponder) -> int:
    return r.bound_port


def test_discover_finds_responder(responder: DiscoveryResponder) -> None:
    ep = discover("mgr", FP, 2.0, LO, _port(responder))
    assert ep is not None
    assert (ep.hosts, ep.port, ep.fingerprint) == (["127.0.0.1"], 47800, FP)


def test_wrong_manager_id_ignored(responder: DiscoveryResponder) -> None:
    t0 = time.monotonic()
    assert discover("someone-else", FP, 0.5, LO, _port(responder)) is None
    assert time.monotonic() - t0 >= 0.4


def test_response_with_wrong_fingerprint_rejected(responder: DiscoveryResponder) -> None:
    assert discover("mgr", "ee" * 32, 0.5, LO, _port(responder)) is None


def test_discover_never_raises_on_bad_input() -> None:
    assert discover("mgr", "nothex", 0.2, LO, 9) is None
    assert discover("mgr", FP, 0.2, ["not an address"], 9) is None


def test_nonce_mismatch_ignored() -> None:
    """A fake Manager that echoes a wrong nonce is ignored."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    srv.settimeout(2)
    port = srv.getsockname()[1]

    import threading

    def fake() -> None:
        try:
            _, addr = srv.recvfrom(600)
            srv.sendto(build_answer("mgr", 1234, FP, "f" * 16), addr)
        except OSError:
            pass

    th = threading.Thread(target=fake, daemon=True)
    th.start()
    try:
        assert discover("mgr", FP, 0.6, LO, port) is None
    finally:
        th.join(3)
        srv.close()


def test_malformed_and_oversized_datagrams_ignored_responder_survives(
    responder: DiscoveryResponder,
) -> None:
    port = _port(responder)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for junk in (b"", b"\x00\xff", b"{", b"[]", b"x" * 2000, b'{"v":1}', b"9" * 513):
            s.sendto(junk, ("127.0.0.1", port))
        big = json.dumps({"v": 1, "q": "who-is-tokenvault", "manager_id": "mgr",
                          "nonce": "0" * 16, "pad": "p" * 600}).encode()
        s.sendto(big, ("127.0.0.1", port))
        s.settimeout(0.4)
        with pytest.raises(OSError):  # no answer to any of them
            s.recvfrom(1024)
    assert responder.running
    assert discover("mgr", FP, 2.0, LO, port) is not None


def test_responder_rate_limits_flooding_source(responder: DiscoveryResponder) -> None:
    port = _port(responder)
    q = build_query("mgr", "0" * 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for _ in range(40):
            s.sendto(q, ("127.0.0.1", port))
        s.settimeout(0.5)
        got = 0
        try:
            while True:
                s.recvfrom(1024)
                got += 1
        except OSError:
            pass
    assert 1 <= got <= 6  # max 5/s (+1 if a second boundary fell inside the burst)


def test_start_with_port_in_use_degrades_without_raising() -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    try:
        r = DiscoveryResponder(
            "mgr", lambda: (1, FP), bind_host="127.0.0.1", port=blocker.getsockname()[1]
        )
        assert r.start() == 0
        assert not r.running
        r.stop()
    finally:
        blocker.close()


def test_stop_is_fast_and_idempotent() -> None:
    r = DiscoveryResponder("mgr", lambda: (1, FP), bind_host="127.0.0.1", port=0)
    r.start()
    t0 = time.monotonic()
    r.stop()
    r.stop()
    assert time.monotonic() - t0 < 1.0
    assert not r.running
    DiscoveryResponder("x", lambda: (1, FP), bind_host="127.0.0.1", port=0).stop()  # never started


def test_answer_reflects_changed_endpoint(responder: DiscoveryResponder) -> None:
    port = _port(responder)
    new_fp = "12" * 32
    responder.state["ep"] = (47811, new_fp)  # type: ignore[attr-defined]
    ep = discover("mgr", new_fp, 2.0, LO, port)
    assert ep is not None and ep.port == 47811
    assert discover("mgr", FP, 0.4, LO, port) is None  # old pin no longer matches


def test_transport_falls_back_to_discovery_when_ip_changed(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    responder: DiscoveryResponder | None = None
    try:
        port = rig.start_server()
        fp = rig.cert.fingerprint_sha256
        responder = DiscoveryResponder(
            "mgr", lambda: (port, fp), bind_host="127.0.0.1", port=0
        )
        responder.start()
        resolver = make_resolver(
            "mgr", fp, port=_port(responder), timeout_s=1.0, broadcast_addrs=LO
        )
        ob = rig.outbox()
        t = rig.transport(ob, hosts=["127.0.0.2"], resolver=resolver, open_timeout=2.0)
        t.start()
        assert wait_until(lambda: t.state == "connected"), (t.state, t.last_error)
        assert t.last_good_host == "127.0.0.1"
        assert resolver.last_port == port
        ob.enqueue([make_event(1)])
        t.notify()
        assert wait_until(lambda: store_count(rig.store) == 1)
    finally:
        if responder is not None:
            responder.stop()
        rig.close()
