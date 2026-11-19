"""AdapterApp end to end: installer payload -> setup -> agent -> real IngestServer -> Store.

Manager modules are imported HERE (tests only); the adapter itself never imports them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from a2m_support import store_count, wait_until

from tokenvault.adapter import main, strings
from tokenvault.adapter.app import AdapterApp
from tokenvault.adapter.config import read_secret, write_enroll_secret, write_secret
from tokenvault.adapter.identity import Identity
from tokenvault.adapter.paths import DataFiles
from tokenvault.adapter.transport import Endpoint
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.ingest_server import IngestServer
from tokenvault.manager.packager import Packager
from tokenvault.manager.store import Store
from tokenvault.manager.tls import ensure_certificate

FIXTURE = Path(__file__).parent.parent / "fixtures" / "transcript_basic.jsonl"
SRC = Path(__file__).parents[2] / "src"
IDENT = Identity("alice@example.com", "acc-1", "alice", "PC1", "PC1:alice")
FAST = {"backoff_min": 0.05, "backoff_max": 0.3, "open_timeout": 5.0}


def _assistant(n: int) -> str:
    return json.dumps({
        "type": "assistant", "uuid": f"late-{n}", "sessionId": "s-1",
        "timestamp": f"2026-01-01T11:00:0{n}.000Z", "cwd": "C:\\x\\myproject",
        "isSidechain": False,
        "message": {"id": f"msg_late_{n}", "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": n, "output_tokens": 2 * n}, "content": []},
    }) + "\n"


class RecordingEnrollment:
    """Enrollment proxy that records every enrollment secret a Manager receives."""

    def __init__(self, inner: Enrollment) -> None:
        self.inner = inner
        self.secrets: list[str] = []

    def register_device(self, secret: str, device_id: str, team: str) -> str | None:
        self.secrets.append(secret)
        return self.inner.register_device(secret, device_id, team)

    def try_register(self, secret: str, device_id: str, team: str) -> tuple[str | None, str]:
        self.secrets.append(secret)
        return self.inner.try_register(secret, device_id, team)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class Mgr:
    """One test Manager: cert, enrollment (recorded), store, ingest server, installer."""

    def __init__(self, root: Path, manager_id: str) -> None:
        self.cert = ensure_certificate(root / "tls")
        self.enrollment = Enrollment(root / "enrollment.db")
        self.recorder = RecordingEnrollment(self.enrollment)
        self.store = Store(root / "manager.db", tz_offset_minutes=420)
        self.server = IngestServer(self.store, self.recorder, self.cert,  # type: ignore[arg-type]
                                   host="127.0.0.1", port=0)
        self.port = self.server.start()
        template = root / "template.exe"
        template.write_bytes(b"MZ adapter template")
        built = Packager(template, self.enrollment, self.cert, root / "out",
                         lambda: (["127.0.0.1"], self.port), manager_id).build_for_team("Dev", None)
        self.installer = built.path
        self.secret = self.enrollment.package_secret(built.package_id)

    def close(self) -> None:
        self.server.stop()
        self.store.close()
        self.enrollment.close()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> str:
    name = f"Local\\TokenVaultTest-{uuid.uuid4().hex}"
    monkeypatch.setenv("TOKENVAULT_MUTEX_NAME", name)
    strings.set_lang(None)
    return name


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict[str, Any]]:
    projects = tmp_path / "claude" / "projects"
    (projects / "C--x-myproject").mkdir(parents=True)
    transcript = projects / "C--x-myproject" / "s-1.jsonl"
    transcript.write_bytes(FIXTURE.read_bytes())
    mgr = Mgr(tmp_path / "mgrA", "mgr-a")
    data = tmp_path / "adapter-data"
    assert main.main(["setup", "--silent", "--accept", "--from", str(mgr.installer),
                      "--data-dir", str(data), "--no-autostart", "--no-start"]) == 0
    yield {"projects": projects, "transcript": transcript, "mgr": mgr, "data": data,
           "root": tmp_path}
    mgr.close()


def _app(env: dict[str, Any], **kw: Any) -> AdapterApp:
    return AdapterApp(DataFiles(env["data"]), env["projects"], identity=IDENT, tray=False,
                      resolver=lambda: [], transport_options=FAST, **kw)


def _state(files: DataFiles) -> dict[str, Any]:
    try:
        return dict(json.loads(files.state.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _tv_threads() -> set[threading.Thread]:
    """Live threads, minus the (test-side) Manager's ingest pool which grows on demand."""
    return {t for t in threading.enumerate()
            if t.is_alive() and not t.name.startswith("tv-ingest")}


def test_adapter_app_end_to_end(env: dict[str, Any]) -> None:
    mgr: Mgr = env["mgr"]
    files = DataFiles(env["data"])
    assert files.enroll_secret.exists() and not files.device_key.exists()
    before = _tv_threads()

    # 1) first start: backfill delivers the fixture, enrollment swaps secret for key
    app = _app(env)
    th = app.run_in_thread()
    try:
        assert wait_until(lambda: store_count(mgr.store) == 3, timeout=15), store_count(mgr.store)
        assert wait_until(lambda: not files.enroll_secret.exists(), timeout=5)
        key1 = read_secret(files.device_key)
        assert key1
        assert wait_until(lambda: app.waiting() == 0, timeout=5)
        # state.json is throttled (<= 1 write / 5 s) but always catches up
        assert wait_until(lambda: _state(files).get("state") == "connected", timeout=8)
        assert _state(files).get("last_good_host") == "127.0.0.1"
    finally:
        app.stop()
    th.join(5)
    assert not th.is_alive()
    assert wait_until(lambda: _tv_threads() <= before, timeout=5), _tv_threads() - before

    # 2) second start reuses the device key (no re-enrollment) and the last good host
    app2 = _app(env)
    th2 = app2.run_in_thread()
    try:
        assert app2.transport is not None and app2.transport.last_good_host == "127.0.0.1"
        assert wait_until(lambda: app2.state == "connected", timeout=10), app2.state
        assert read_secret(files.device_key) == key1

        # 3) Manager goes away; new transcript lines queue locally
        mgr.server.stop()
        assert wait_until(lambda: app2.state != "connected", timeout=10)
        with open(env["transcript"], "a", encoding="utf-8") as fh:
            fh.write(_assistant(1))
            fh.write(_assistant(2))
        assert wait_until(lambda: app2.waiting() == 2, timeout=10), app2.waiting()
        assert store_count(mgr.store) == 3

        # 4) Manager back on the same port: everything delivered, no duplicates
        mgr.server.start(port=mgr.port)
        assert wait_until(lambda: store_count(mgr.store) == 5, timeout=15)
        assert wait_until(lambda: app2.waiting() == 0, timeout=5)
        with mgr.store.read_snapshot() as snap:
            keys = [r[0] for r in snap.fetchall("SELECT msg_key FROM events")]
        assert len(keys) == len(set(keys)) == 5
        assert read_secret(files.device_key) == key1
    finally:
        app2.stop()
    th2.join(5)
    assert wait_until(lambda: _tv_threads() <= before, timeout=5), _tv_threads() - before
    assert len(mgr.enrollment.list_devices()) == 1
    assert len(mgr.recorder.secrets) == 1  # enrolled exactly once


def _agent_env(mutex: str) -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(SRC), "TOKENVAULT_MUTEX_NAME": mutex}


@pytest.mark.skipif(sys.platform != "win32", reason="named events are Windows-only")
def test_rerun_setup_switches_running_agent_without_leaking_secret(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, _isolated: str
) -> None:
    a: Mgr = env["mgr"]
    b = Mgr(env["root"] / "mgrB", "mgr-b")
    procs: list[subprocess.Popen[bytes]] = []
    try:
        claude = str(env["projects"].parent)
        old = subprocess.Popen(
            [sys.executable, "-m", "tokenvault.adapter.main", "run", "--data-dir",
             str(env["data"]), "--claude-dir", claude, "--no-tray"], env=_agent_env(_isolated))
        procs.append(old)
        assert wait_until(lambda: store_count(a.store) == 3, timeout=60)

        def launch(cmd: list[str]) -> None:  # the "detached" new agent, tracked by the test
            procs.append(subprocess.Popen(cmd, env=_agent_env(_isolated)))

        monkeypatch.setattr(main, "launch_detached", launch)
        rc = main.main(["setup", "--silent", "--accept", "--from", str(b.installer),
                        "--data-dir", str(env["data"]), "--claude-dir", claude,
                        "--no-autostart", "--no-tray"])
        assert rc == 0
        # setup waited (bounded, 10 s) for the old agent; under heavy load allow a bit more
        assert wait_until(lambda: old.poll() is not None, timeout=20)
        assert len(procs) == 2
        # the new agent talks to B within a few seconds, without a logoff
        assert wait_until(lambda: len(b.enrollment.list_devices()) == 1, timeout=60)
        with open(env["transcript"], "a", encoding="utf-8") as fh:
            fh.write(_assistant(3))
        assert wait_until(lambda: store_count(b.store) == 1, timeout=30)
        # B's secret never reached A; A only ever saw its own secret
        assert b.secret not in a.recorder.secrets
        assert set(a.recorder.secrets) == {a.secret}
        assert b.recorder.secrets == [b.secret]
    finally:
        # graceful stop through the reload event (the venv python.exe is only a launcher,
        # killing it would leave the real interpreter running)
        from tokenvault.adapter import singleton

        singleton.signal_reload(singleton.reload_event_name())
        singleton.wait_released(singleton.mutex_name(), 10)
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        b.close()


# ------------------------------------------------------------- app pieces
def test_hello_uses_enroll_secret_before_enrollment_then_device_key(env: dict[str, Any]) -> None:
    app = _app(env)
    files = DataFiles(env["data"])
    h = app.hello_factory()
    assert (h.enroll_secret, h.device_key) == (env["mgr"].secret, None)
    assert h.device_id == "PC1:alice" and h.team == "Dev"
    app.on_device_key("NEWKEY_123")
    assert not files.enroll_secret.exists()
    assert b"NEWKEY_123" not in files.device_key.read_bytes()
    h2 = app.hello_factory()
    assert (h2.enroll_secret, h2.device_key) == (None, "NEWKEY_123")


def test_unreadable_key_falls_back_to_secret(env: dict[str, Any]) -> None:
    DataFiles(env["data"]).device_key.write_bytes(b"TVD1:garbage")
    h = _app(env).hello_factory()
    assert (h.enroll_secret, h.device_key) == (env["mgr"].secret, None)


def test_no_credential_halts_as_not_configured(env: dict[str, Any]) -> None:
    files = DataFiles(env["data"])
    files.enroll_secret.unlink()
    app = _app(env)

    class T:
        stopped = 0
        last_error = None
        last_good_host = None

        def stop(self) -> None:
            T.stopped += 1

    app._transport = T()  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        app.hello_factory()
    assert T.stopped == 1 and app.state == "not_configured"
    app.on_state("offline")  # sticky
    assert app.state == "not_configured"
    assert _state(files).get("state") == "not_configured"


def test_secret_bound_to_other_manager_is_never_sent(env: dict[str, Any]) -> None:
    files = DataFiles(env["data"])
    app = _app(env)
    other = app.config.__class__("mgr-other", ("10.9.9.9",), 1, "ab" * 32, "Dev")
    write_enroll_secret(files.enroll_secret, "S" * 40, other)
    app._transport = type("T", (), {"stop": lambda self: None, "last_error": None,  # type: ignore[assignment]
                                    "last_good_host": None})()
    with pytest.raises(RuntimeError):
        app.hello_factory()


@pytest.mark.parametrize(("reason", "dropped"),
                         [("auth: unknown or revoked device", True), ("busy", False)])
def test_rejected_key_dropped_only_for_auth_reason(
    env: dict[str, Any], reason: str, dropped: bool
) -> None:
    files = DataFiles(env["data"])
    write_secret(files.device_key, "OLDKEY")
    app = _app(env)
    assert app.hello_factory().device_key == "OLDKEY"
    app._transport = type("T", (), {"last_error": reason,  # type: ignore[assignment]
                                    "last_good_host": None})()
    app.on_state("rejected")
    assert files.device_key.exists() is not dropped
    if dropped:
        assert app.hello_factory().enroll_secret == env["mgr"].secret


def test_connected_with_device_key_deletes_leftover_secret(env: dict[str, Any]) -> None:
    files = DataFiles(env["data"])
    write_secret(files.device_key, "KEY")
    app = _app(env)
    app.hello_factory()
    app.on_state("connected")
    assert not files.enroll_secret.exists()
    assert _state(files).get("state") == "connected"


def test_device_key_dpapi_failure_keeps_secret(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenvault.common import dpapi

    def boom(data: bytes) -> bytes:
        raise OSError("dpapi down")

    files = DataFiles(env["data"])
    app = _app(env)
    monkeypatch.setattr(dpapi, "_dpapi_protect", boom)
    monkeypatch.setattr(dpapi.sys, "platform", "win32")
    app.on_device_key("KEY")
    assert files.enroll_secret.exists() and not files.device_key.exists()


def test_app_without_config_raises(tmp_path: Path) -> None:
    from tokenvault.adapter.app import NotConfigured

    with pytest.raises(NotConfigured):
        AdapterApp(DataFiles(tmp_path / "none"), tmp_path, identity=IDENT, tray=False)


def test_resolver_port_change_rebuilds_endpoint(env: dict[str, Any]) -> None:
    from tokenvault.adapter.config import load_config

    class Res:
        last_port: int | None = None

        def __call__(self) -> list[str]:
            self.last_port = 47801
            return ["10.0.0.9"]

    app = AdapterApp(DataFiles(env["data"]), env["projects"], identity=IDENT, tray=False,
                     resolver=Res())

    class T:
        endpoint = Endpoint(["10.0.0.5"], 47800, "ab" * 32)

    t = T()
    app._transport = t  # type: ignore[assignment]
    assert app._resolve() == ["10.0.0.9"]
    assert t.endpoint.port == 47801
    cfg = load_config(DataFiles(env["data"]).config)
    assert cfg is not None and cfg.port == 47801
