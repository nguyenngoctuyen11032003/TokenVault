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


def test_responder_rate_limits_flooding_source_smoke(responder: DiscoveryResponder) -> None:
    port = _port(responder)
    q = build_query("mgr", "0" * 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for _ in range(60):
            s.sendto(q, ("127.0.0.1", port))
        s.settimeout(0.5)
        got = 0
        try:
            while True:
                s.recvfrom(1024)
                got += 1
        except OSError:
            pass
    assert 1 <= got <= 12  # per-source 5/s (+boundary), global burst 20


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _fake() -> tuple[DiscoveryResponder, FakeClock]:
    clk = FakeClock()
    return DiscoveryResponder("mgr", lambda: (1, FP), clock=clk), clk


def test_per_source_limit_deterministic() -> None:
    r, clk = _fake()
    assert sum(r._admit("10.0.0.1", clk()) for _ in range(40)) == 5
    clk.t += 1.01
    assert r._admit("10.0.0.1", clk())


def test_global_cap_bounds_rotating_sources() -> None:
    r, clk = _fake()
    ok = 0
    for step in range(10):  # 10 steps of 0.1 s, 50 distinct sources each = 500 queries
        for i in range(50):
            ok += r._admit(f"10.{step}.0.{i}", clk())
        clk.t += 0.1
    assert 20 <= ok <= 20 + 50 * 0.9 + 1  # burst + rate * elapsed (0.9 s between first and last)


def test_lru_table_bounded_and_legit_source_still_answered() -> None:
    from tokenvault.manager.discovery import MAX_TRACKED_SOURCES

    r, clk = _fake()
    for i in range(5000):
        r._admit(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}", clk())
        assert len(r._hits) <= MAX_TRACKED_SOURCES
    clk.t += 5.0  # global bucket refills
    assert r._admit("192.168.1.50", clk())
    assert len(r._hits) <= MAX_TRACKED_SOURCES


def test_amplification_for_largest_manager_id_below_2_5x() -> None:
    mid = "m" * 64
    q = build_query(mid, "0" * 16)
    a = build_answer(mid, 65535, FP, "0" * 16)
    assert len(a) / len(q) < 2.5


def test_persistent_oserror_does_not_spin_and_exits_when_closed() -> None:
    import threading

    calls = 0

    class Bad:
        def recvfrom(self, n: int) -> tuple[bytes, tuple[str, int]]:
            nonlocal calls
            calls += 1
            raise OSError("boom")

        def fileno(self) -> int:
            return 5

    r = DiscoveryResponder("mgr", lambda: (1, FP))
    th = threading.Thread(target=r._serve, args=(Bad(),), daemon=True)  # type: ignore[arg-type]
    th.start()
    time.sleep(0.35)
    assert calls <= 6  # ~0.1 s pause per failure
    r._stopping.set()
    th.join(1.0)
    assert not th.is_alive()

    class Closed(Bad):
        def fileno(self) -> int:
            return -1

    r2 = DiscoveryResponder("mgr", lambda: (1, FP))
    th2 = threading.Thread(target=r2._serve, args=(Closed(),), daemon=True)  # type: ignore[arg-type]
    th2.start()
    th2.join(1.0)
    assert not th2.is_alive()


def test_raising_get_endpoint_keeps_loop_alive() -> None:
    state = {"fail": True}

    def ep() -> tuple[int, str]:
        if state["fail"]:
            raise RuntimeError("not ready")
        return 4242, FP

    r = DiscoveryResponder("mgr", ep, bind_host="127.0.0.1", port=0)
    port = r.start()
    try:
        assert discover("mgr", FP, 0.4, LO, port) is None
        assert r.running
        state["fail"] = False
        found = discover("mgr", FP, 2.0, LO, port)
        assert found is not None and found.port == 4242
    finally:
        r.stop()


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
