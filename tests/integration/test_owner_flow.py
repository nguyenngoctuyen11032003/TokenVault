"""The owner's key flow, end to end with real components:

Manager creates an installer -> an adapter configured ONLY from that installer's
payload enrolls and sends events -> the Manager shows the device online in the right
team with exactly the sent (message_id-deduplicated) totals -> revocation cuts it off.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from a2m_support import make_event, wait_until

from tokenvault.adapter.outbox import Outbox
from tokenvault.adapter.transport import Endpoint, Transport
from tokenvault.common.config_payload import PayloadConfig, read_payload
from tokenvault.common.protocol import Hello
from tokenvault.manager.api import Api
from tokenvault.manager.firewall import FirewallStatus
from tokenvault.manager.lifecycle import Manager, ManagerConfig
from tokenvault.manager.paths import pricing_path


def start_manager(root: Path, **kw: Any) -> Manager:
    tpl = root / "adapter-setup-template.exe"
    tpl.write_bytes(b"MZ" + bytes(4096))
    cfg: dict[str, Any] = dict(
        data_dir=root / "data", template=tpl, pricing=pricing_path(), ingest_host="127.0.0.1",
        ingest_port=0, discovery=False, tz_offset_minutes=420,
        firewall_check=lambda p: FirewallStatus(True, True, True, "ok"),
        presence_interval=0.2, hosts_provider=lambda: ["127.0.0.1"],
    )
    cfg.update(kw)
    m = Manager(ManagerConfig(**cfg))
    m.start()
    return m


class InstalledAdapter:
    """What an installed adapter does with its payload (hosts, port, pin, secret, team)."""

    def __init__(self, root: Path, payload: PayloadConfig, device_id: str) -> None:
        self.device_id = device_id
        self.payload = payload
        self.keys: list[str] = []
        self.outbox = Outbox(root / f"outbox-{device_id}.db")
        self.transport = Transport(
            self.outbox, Endpoint(list(payload.hosts), payload.port, payload.fingerprint),
            self.hello, on_device_key=self.keys.append,
            backoff_min=0.05, backoff_max=0.2, rejected_retry_s=0.2,
        )

    def hello(self) -> Hello:
        if self.keys:
            return Hello(self.device_id, None, self.keys[-1], self.payload.team, 1)
        return Hello(self.device_id, self.payload.secret, None, self.payload.team, 1)

    def send(self, events: list[Any]) -> None:
        self.outbox.enqueue(events)
        self.transport.notify()

    def close(self) -> None:
        self.transport.stop()
        self.outbox.close()


@pytest.fixture
def made(tmp_path: Path) -> Iterator[Callable[..., Any]]:
    things: list[Any] = []

    def add(x: Any) -> Any:
        things.append(x)
        return x

    yield add
    for x in reversed(things):
        x.close() if isinstance(x, InstalledAdapter) else x.stop()


def ev(n: int, device: str, **kw: Any) -> Any:
    return make_event(n, device_id=device, claude_account_email=f"{device}@congty.vn",
                      session_id=f"s-{device}", input_tokens=100 * n, output_tokens=10,
                      **kw)


def test_installer_to_dashboard_round_trip(tmp_path: Path, made: Callable[..., Any]) -> None:
    m = made(start_manager(tmp_path))
    api = Api(m)
    pushes: list[float] = []
    m.on_change = lambda: pushes.append(time.monotonic())

    res = api.create_installer("Dev", "Đợt 1")
    payload = read_payload(Path(res["path"]))
    assert payload is not None
    assert (payload.port, payload.fingerprint, payload.team, payload.hosts) == (
        m.bound_port(), m.cert.fingerprint_sha256, "Dev", ["127.0.0.1"])

    ad = made(InstalledAdapter(tmp_path, payload, "pc01-an"))
    ad.transport.start()
    assert wait_until(lambda: ad.transport.state == "connected", timeout=10)
    # 3 responses; msg_2 arrives twice (second copy larger) -> counted once, larger wins
    ad.send([ev(1, "pc01-an"), ev(2, "pc01-an"), ev(3, "pc01-an"),
             replace(ev(2, "pc01-an"), output_tokens=50, uuid="u2-dup")])

    def landed() -> bool:
        devs = api.devices()
        return bool(devs) and devs[0]["online"] and api.overview(None)["totals"]["events"] == 3

    assert wait_until(landed, timeout=5)
    dev = api.devices()[0]
    assert (dev["device_id"], dev["team"], dev["email"], dev["package_id"]) == (
        "pc01-an", "Dev", "pc01-an@congty.vn", res["package_id"])
    totals = api.overview(None)["totals"]
    assert totals["input_tokens"] == 100 + 200 + 300
    assert totals["output_tokens"] == 10 + 50 + 10
    emp = api.employees({"team": "Dev"})
    assert [e["key"] for e in emp] == ["pc01-an@congty.vn"] and emp[0]["online"] is True
    assert emp[0]["input_tokens"] == 600
    assert {t["name"]: t["online"] for t in api.teams()} == {"Dev": 1}
    assert pushes, "live push listener fired"

    # second installer: separate package, device lands in QA
    res_qa = api.create_installer("QA", None)
    assert res_qa["package_id"] != res["package_id"]
    payload_qa = read_payload(Path(res_qa["path"]))
    assert payload_qa is not None and payload_qa.secret != payload.secret
    qa = made(InstalledAdapter(tmp_path, payload_qa, "qa01-lan"))
    qa.transport.start()
    qa.send([ev(7, "qa01-lan")])
    assert wait_until(lambda: {d["device_id"]: d["team"] for d in api.devices()}.get(
        "qa01-lan") == "QA", timeout=5)
    assert wait_until(lambda: api.overview({"team": "QA"})["totals"]["events"] == 1)
    assert {p["package_id"]: p["devices"] for p in api.packages()} == {
        res["package_id"]: 1, res_qa["package_id"]: 1}

    # revoke: the live session closes and later events are refused
    assert api.revoke_device("pc01-an") == {"ok": True}
    assert wait_until(lambda: "pc01-an" not in m.connected(), timeout=10)
    assert wait_until(lambda: ad.transport.state == "rejected", timeout=10)
    ad.send([ev(9, "pc01-an")])
    time.sleep(1.0)
    assert api.overview({"team": "Dev"})["totals"]["events"] == 3
    assert all(d["device_id"] != "pc01-an" for d in api.devices())


def test_port_fallback_is_carried_by_new_installers(tmp_path: Path,
                                                    made: Callable[..., Any]) -> None:
    blocker = socket.socket()
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    taken = blocker.getsockname()[1]
    try:
        m = made(start_manager(tmp_path, ingest_port=taken))
        assert m.bound_port() != taken
        api = Api(m)
        payload = read_payload(Path(api.create_installer("Dev", None)["path"]))
        assert payload is not None and payload.port == m.bound_port()
        ad = made(InstalledAdapter(tmp_path, payload, "pc02-binh"))
        ad.transport.start()
        ad.send([ev(1, "pc02-binh")])
        assert wait_until(lambda: api.overview(None)["totals"]["events"] == 1, timeout=10)
    finally:
        blocker.close()
