"""Manager app shell end to end: real ingest server + adapter Transport, certificate
regeneration, CLI subprocesses (--selftest, --devserver, firewall), startup errors."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from a2m_support import make_event, wait_until

from tokenvault.adapter.outbox import Outbox
from tokenvault.adapter.transport import Endpoint, Transport
from tokenvault.common.discovery_wire import build_query, parse_answer
from tokenvault.common.protocol import Hello
from tokenvault.manager import app as app_mod
from tokenvault.manager import firewall
from tokenvault.manager.api import Api
from tokenvault.manager.firewall import FirewallStatus
from tokenvault.manager.instance import SingleInstance
from tokenvault.manager.lifecycle import Manager, ManagerConfig, StartupError
from tokenvault.manager.paths import pricing_path
from tokenvault.manager.transparency_probe import patch_is_colour

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable


def make_manager(root: Path, **kw: Any) -> Manager:
    tpl = root / "template.exe"
    tpl.write_bytes(b"MZ" + b"\0" * 2048)
    cfg: dict[str, Any] = dict(
        data_dir=root / "data", template=tpl, pricing=pricing_path(), ingest_host="127.0.0.1",
        ingest_port=0, discovery=False, tz_offset_minutes=420,
        firewall_check=lambda p: FirewallStatus(True, True, True, "ok"),
        presence_interval=0.2, hosts_provider=lambda: ["127.0.0.1"],
    )
    cfg.update(kw)
    return Manager(ManagerConfig(**cfg))


@pytest.fixture
def manager(tmp_path: Path) -> Iterator[Manager]:
    m = make_manager(tmp_path, discovery=True, discovery_port=0)
    m.start()
    yield m
    m.stop()


class Adapter:
    """A real adapter Transport enrolled with a package of the Manager."""

    def __init__(self, root: Path, m: Manager, device: str, fingerprint: str | None = None,
                 secret: str | None = None) -> None:
        self.device = device
        self.keys: list[str] = []
        self.states: list[str] = []
        self.secret = secret or m.enrollment.create_package("Dev", None).secret
        self.outbox = Outbox(root / f"{device}.db")
        self.t = Transport(
            self.outbox,
            Endpoint(["127.0.0.1"], m.bound_port(), fingerprint or m.cert.fingerprint_sha256),
            self._hello, on_state=self.states.append, on_device_key=self.keys.append,
            backoff_min=0.05, backoff_max=0.2, rejected_retry_s=0.2,
        )

    def _hello(self) -> Hello:
        if self.keys:
            return Hello(self.device, None, self.keys[-1], "Dev", 1)
        return Hello(self.device, self.secret, None, "Dev", 1)

    def close(self) -> None:
        self.t.stop()
        self.outbox.close()


@pytest.fixture
def adapters(tmp_path: Path) -> Iterator[Callable[..., Adapter]]:
    made: list[Adapter] = []

    def make(*a: Any, **kw: Any) -> Adapter:
        ad = Adapter(tmp_path, *a, **kw)
        made.append(ad)
        return ad

    yield make
    for ad in made:
        ad.close()


# ------------------------------------------------------------- revocation
def test_revoke_device_closes_live_session(
    manager: Manager, adapters: Callable[..., Adapter]
) -> None:
    api = Api(manager)
    ad = adapters(manager, "dev-a")
    ad.t.start()
    assert wait_until(lambda: ad.t.state == "connected"), ad.states
    ad.outbox.enqueue([make_event(1, device_id="dev-a")])
    ad.t.notify()
    assert wait_until(lambda: any(d["device_id"] == "dev-a" for d in api.devices()))
    assert wait_until(lambda: manager.connected() == {"dev-a"})
    assert api.devices()[0]["online"] is True
    assert api.revoke_device("dev-a") == {"ok": True}
    assert wait_until(lambda: manager.connected() == set(), timeout=10)
    assert wait_until(lambda: ad.t.state == "rejected", timeout=10), ad.states
    assert all(d["device_id"] != "dev-a" for d in api.devices())
    time.sleep(0.5)
    assert manager.connected() == set()  # it cannot come back


def test_live_change_on_event_and_presence(
    manager: Manager, adapters: Callable[..., Adapter]
) -> None:
    hits: list[float] = []
    manager.on_change = lambda: hits.append(time.monotonic())
    ad = adapters(manager, "dev-b")
    ad.t.start()
    assert wait_until(lambda: ad.t.state == "connected")
    assert wait_until(lambda: len(hits) >= 1, timeout=5)  # presence ticker saw the connect
    n = len(hits)
    ad.outbox.enqueue([make_event(7, device_id="dev-b")])
    ad.t.notify()
    assert wait_until(lambda: len(hits) > n, timeout=5)  # on_event
    # an idle connected adapter stays "online" in employees() thanks to presence
    emp = Api(manager).employees(None)
    assert emp and emp[0]["online"] is True


# ------------------------------------------------------ certificate change
def test_regenerate_certificate_keeps_port_and_repins(
    manager: Manager, adapters: Callable[..., Adapter]
) -> None:
    api = Api(manager)
    port = manager.bound_port()
    old_fp = manager.cert.fingerprint_sha256
    pkg = manager.enrollment.create_package("Dev", None)
    res = api.regenerate_certificate()
    assert res["fingerprint"] != old_fp and len(res["fingerprint"]) == 64
    assert "reinstall" in res["warning"].lower()
    assert manager.bound_port() == port and api.diagnostics()["port"] == port
    assert api.settings()["fingerprint"] == res["fingerprint"]
    old = adapters(manager, "dev-old", fingerprint=old_fp, secret=pkg.secret)
    new = adapters(manager, "dev-new", fingerprint=res["fingerprint"], secret=pkg.secret)
    old.t.start()
    new.t.start()
    assert wait_until(lambda: new.t.state == "connected"), new.states
    time.sleep(1.0)
    assert old.t.state != "connected" and "connected" not in old.states
    assert manager.connected() == {"dev-new"}
    # discovery answers with the NEW fingerprint
    d = manager.discovery
    assert d is not None and d.bound_port
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(3)
        s.sendto(build_query(manager.manager_id, "00112233445566ff"), ("127.0.0.1", d.bound_port))
        ans = parse_answer(s.recvfrom(2048)[0], manager.manager_id, "00112233445566ff")
    assert ans is not None and ans.fingerprint == res["fingerprint"] and ans.port == port
    assert api.connectivity_test()["checks"][3]["ok"] is True


# ------------------------------------------------------------- startup
def test_unusable_certificate_is_a_startup_error_and_files_kept(tmp_path: Path) -> None:
    certs = tmp_path / "data" / "certs"
    certs.mkdir(parents=True)
    (certs / "manager-cert.pem").write_text("garbage")
    m = make_manager(tmp_path)
    with pytest.raises(StartupError) as ei:
        m.start()
    assert str(ei.value).startswith("[cert_error]")
    assert (certs / "manager-cert.pem").read_text() == "garbage"
    m.stop()  # safe after a failed start


def test_manager_id_generated_once(tmp_path: Path) -> None:
    m = make_manager(tmp_path)
    m.start()
    first = m.manager_id
    m.stop()
    m2 = make_manager(tmp_path)
    m2.start()
    try:
        assert m2.manager_id == first and first
    finally:
        m2.stop()


def test_single_instance_lock(tmp_path: Path) -> None:
    a = SingleInstance(tmp_path / "manager.lock")
    b = SingleInstance(tmp_path / "manager.lock")
    assert a.acquire() is True
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()


def test_transparency_patch_check() -> None:
    assert patch_is_colour([(1, 2, 3), (2, 3, 4)], (1, 2, 3)) is True
    assert patch_is_colour([(1, 2, 3), (40, 40, 40)], (1, 2, 3)) is False
    assert patch_is_colour([], (1, 2, 3)) is False


# ----------------------------------------------------------------- CLI
def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["TOKENVAULT_NO_MSGBOX"] = "1"
    return env


def test_selftest_subprocess_exits_zero_fast() -> None:
    t0 = time.monotonic()
    p = subprocess.run([PY, "-m", "tokenvault.manager.app", "--selftest"], env=_env(),
                       capture_output=True, text=True, timeout=60, cwd=REPO)
    elapsed = time.monotonic() - t0
    assert p.returncode == 0, p.stdout + p.stderr
    report = json.loads(p.stdout)
    assert report["ok"] is True
    names = {s["step"] for s in report["steps"]}
    assert {"start", "api_reads", "ingest_tls", "discovery", "create_installer",
            "ui_server", "live_push"} <= names
    assert elapsed < 15, elapsed


def test_firewall_cli_dispatch(monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[list[str]] = []

    def fake(args: Any) -> tuple[int, str]:
        calls.append(list(args))
        return 1, "No rules match the specified criteria."

    monkeypatch.setattr(firewall, "run_netsh", fake)
    assert app_mod.main(["firewall", "--check", "--port", "47800"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {"present": False, "tcp": False, "udp": False, "port": 47800,
                   "detail": out["detail"]}
    assert all(c[3] == "show" for c in calls)  # read-only


@pytest.fixture
def devserver(tmp_path: Path) -> Iterator[str]:
    p = subprocess.Popen(
        [PY, "-m", "tokenvault.manager.app", "--devserver", "--port", "0", "--data-dir",
         str(tmp_path / "dev")],
        env=_env(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=REPO,
    )
    try:
        assert p.stdout is not None
        line = p.stdout.readline()
        info = json.loads(line)
        yield info["devserver"].rstrip("/")
    finally:
        p.terminate()
        p.wait(10)


def _post(url: str, args: list[Any]) -> Any:
    req = urllib.request.Request(url, data=json.dumps(args).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_devserver_serves_demo_data(devserver: str) -> None:
    ov = _post(f"{devserver}/api/overview", [None])
    assert ov["totals"]["total_tokens"] > 0 and ov["unknown_cost_models"]
    emps = _post(f"{devserver}/api/employees", [None])
    assert len(emps) == 14
    teams = {t["name"]: t for t in _post(f"{devserver}/api/teams", [])}
    assert set(teams) == {"Dev", "QA", "BA-PM"} and teams["Dev"]["online"] == 6
    res = _post(f"{devserver}/api/create_installer", ["Dev", None])
    assert Path(res["path"]).is_file()
    with urllib.request.urlopen(f"{devserver}/js/app.js", timeout=10) as r:
        assert r.headers["Content-Type"].startswith("text/javascript")
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post(f"{devserver}/api/create_installer", ["a/b", None])
    assert ei.value.code == 500
    assert json.loads(ei.value.read())["error"].startswith("[invalid_team]")



def test_selftest_require_template_fails_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.exe"
    p = subprocess.run([PY, "-m", "tokenvault.manager.app", "--selftest", "--require-template",
                        "--template", str(missing)], env=_env(), capture_output=True,
                       text=True, timeout=60, cwd=REPO)
    assert p.returncode == 1, p.stdout
    step = next(s for s in json.loads(p.stdout)["steps"] if s["step"] == "real_template")
    assert step["ok"] is False and "exists=False" in step["detail"]
    present = tmp_path / "tpl.exe"
    present.write_bytes(b"MZ" + bytes(100))
    p = subprocess.run([PY, "-m", "tokenvault.manager.app", "--selftest", "--require-template",
                        "--template", str(present)], env=_env(), capture_output=True,
                       text=True, timeout=60, cwd=REPO)
    assert p.returncode == 0, p.stdout
    assert "exists=True" in p.stdout


def test_devserver_refuses_a_real_data_dir(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "manager.db").write_bytes(b"SQLite format 3 real data")
    p = subprocess.run([PY, "-m", "tokenvault.manager.app", "--devserver", "--port", "0",
                        "--data-dir", str(real)], env=_env(), capture_output=True, text=True,
                       timeout=60, cwd=REPO)
    assert p.returncode == 2
    assert "refusing" in json.loads(p.stdout.splitlines()[0])["error"]
    assert (real / "manager.db").read_bytes() == b"SQLite format 3 real data"
