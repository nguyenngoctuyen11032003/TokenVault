"""AdapterApp end to end: installer payload -> setup -> agent -> real IngestServer -> Store.

Manager modules are imported HERE (tests only); the adapter itself never imports them.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from a2m_support import store_count, wait_until

from tokenvault.adapter import main
from tokenvault.adapter.app import AdapterApp
from tokenvault.adapter.config import read_secret
from tokenvault.adapter.identity import Identity
from tokenvault.adapter.paths import DataFiles
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.ingest_server import IngestServer
from tokenvault.manager.packager import Packager
from tokenvault.manager.store import Store
from tokenvault.manager.tls import ensure_certificate

FIXTURE = Path(__file__).parent.parent / "fixtures" / "transcript_basic.jsonl"
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


@dataclass
class World:
    root: Path
    projects: Path
    transcript: Path
    data: Path
    store: Store
    enrollment: Enrollment
    server: IngestServer
    port: int

    def app(self) -> AdapterApp:
        return AdapterApp(DataFiles(self.data), self.projects, identity=IDENT, tray=False,
                          resolver=lambda: [], transport_options=FAST)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    projects = tmp_path / "claude" / "projects"
    (projects / "C--x-myproject").mkdir(parents=True)
    transcript = projects / "C--x-myproject" / "s-1.jsonl"
    transcript.write_bytes(FIXTURE.read_bytes())
    cert = ensure_certificate(tmp_path / "mgr" / "tls")
    enrollment = Enrollment(tmp_path / "mgr" / "enrollment.db")
    store = Store(tmp_path / "mgr" / "manager.db", tz_offset_minutes=420)
    server = IngestServer(store, enrollment, cert, host="127.0.0.1", port=0)
    port = server.start()
    template = tmp_path / "template.exe"
    template.write_bytes(b"MZ adapter template")
    built = Packager(template, enrollment, cert, tmp_path / "out",
                     lambda: (["127.0.0.1"], port), "mgr-test").build_for_team("Dev", None)
    data = tmp_path / "adapter-data"
    rc = main.main(["setup", "--silent", "--accept", "--from", str(built.path),
                    "--data-dir", str(data), "--no-autostart"])
    assert rc == 0
    w = World(tmp_path, projects, transcript, data, store, enrollment, server, port)
    yield w
    w.server.stop()
    store.close()
    enrollment.close()


def _state(files: DataFiles) -> str | None:
    try:
        return str(json.loads(files.state.read_text(encoding="utf-8"))["state"])
    except (OSError, ValueError, KeyError):
        return None


def _tv_threads() -> set[threading.Thread]:
    """Live threads, minus the (test-side) Manager's ingest pool which grows on demand."""
    return {t for t in threading.enumerate()
            if t.is_alive() and not t.name.startswith("tv-ingest")}


def test_adapter_app_end_to_end(world: World) -> None:
    files = DataFiles(world.data)
    assert files.enroll_secret.exists() and not files.device_key.exists()
    before = _tv_threads()

    # 1) first start: backfill delivers the fixture, enrollment swaps secret for key
    app = world.app()
    th = app.run_in_thread()
    try:
        assert wait_until(lambda: store_count(world.store) == 3, timeout=15), store_count(
            world.store)
        assert wait_until(lambda: not files.enroll_secret.exists(), timeout=5)
        assert files.device_key.exists()
        key1 = read_secret(files.device_key)
        assert key1
        assert wait_until(lambda: app.waiting() == 0, timeout=5)
        # state.json is throttled (<= 1 write / 5 s) but always catches up
        assert wait_until(lambda: _state(files) == "connected", timeout=8), _state(files)
    finally:
        app.stop()
    th.join(5)
    assert not th.is_alive()
    assert wait_until(lambda: _tv_threads() <= before, timeout=5), _tv_threads() - before

    # 2) second start reuses the device key (no re-enrollment)
    app2 = world.app()
    th2 = app2.run_in_thread()
    try:
        assert wait_until(lambda: app2.state == "connected", timeout=10), app2.state
        assert read_secret(files.device_key) == key1
        assert not files.enroll_secret.exists()

        # 3) Manager goes away; new transcript lines queue locally
        world.server.stop()
        assert wait_until(lambda: app2.state != "connected", timeout=10)
        with open(world.transcript, "a", encoding="utf-8") as fh:
            fh.write(_assistant(1))
            fh.write(_assistant(2))
        assert wait_until(lambda: app2.waiting() == 2, timeout=10), app2.waiting()
        assert store_count(world.store) == 3

        # 4) Manager back on the same port: everything delivered, no duplicates
        world.server.start(port=world.port)
        assert wait_until(lambda: store_count(world.store) == 5, timeout=15)
        assert wait_until(lambda: app2.waiting() == 0, timeout=5)
        with world.store.read_snapshot() as snap:
            keys = [r[0] for r in snap.fetchall("SELECT msg_key FROM events")]
        assert len(keys) == len(set(keys)) == 5
        assert read_secret(files.device_key) == key1
    finally:
        app2.stop()
    th2.join(5)
    assert wait_until(lambda: _tv_threads() <= before, timeout=5), _tv_threads() - before
    assert len(world.enrollment.list_devices()) == 1
