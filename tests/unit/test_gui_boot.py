"""Startup failure handling, crash hooks, second launch signal, demo-folder guard."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tokenvault.manager import app_support, gui
from tokenvault.manager.api import Api
from tokenvault.manager.instance import InstanceSignal, SingleInstance, second_launch
from tokenvault.manager.lifecycle import Manager, ManagerConfig
from tokenvault.manager.paths import DEMO_MARKER, DemoDirError, prepare_demo_dir, pricing_path
from tokenvault.manager.webserver import UiServer


def cfg(tmp_path: Path) -> ManagerConfig:
    return ManagerConfig(data_dir=tmp_path / "data", template=tmp_path / "t.exe",
                         pricing=pricing_path(), ingest_host="127.0.0.1", ingest_port=0,
                         discovery=False, tz_offset_minutes=420, firewall_check=None)


@pytest.fixture
def stops(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = Manager.stop

    def spy(self: Manager) -> None:
        calls.append("stop")
        real(self)

    monkeypatch.setattr(Manager, "stop", spy)
    return calls


def boom(*_a: Any, **_k: Any) -> None:
    raise ValueError("boom")


@pytest.mark.parametrize("stage", ["manager", "api", "ui server"])
def test_every_boot_stage_failure_is_logged_and_cleaned_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    stops: list[str], stage: str,
) -> None:
    target = {"manager": (Manager, "start"), "api": (Api, "__init__"),
              "ui server": (UiServer, "start")}[stage]
    monkeypatch.setattr(*target, boom)
    with caplog.at_level(logging.ERROR), pytest.raises(gui.BootError) as ei:
        gui.boot(cfg(tmp_path))
    assert f"starting the {stage}" in str(ei.value) and "boom" in str(ei.value)
    assert "Traceback" in caplog.text and "ValueError: boom" in caplog.text
    assert stops == ["stop"]


@pytest.fixture
def no_root_handlers() -> Iterator[None]:
    before = list(logging.getLogger().handlers)
    yield
    for h in logging.getLogger().handlers[:]:
        if h not in before:
            logging.getLogger().removeHandler(h)
            h.close()


def test_run_gui_returns_2_on_startup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_root_handlers: None
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(gui, "message_box", lambda text, error=True: shown.append(text))
    monkeypatch.setattr(Manager, "start", boom)
    a = argparse.Namespace(data_dir=str(tmp_path / "data"), template=None, minimized=False)
    assert gui.run_gui(a) == 2
    assert shown and "cannot start" in shown[0] and "manager.log" in shown[0]
    assert "ValueError: boom" in (tmp_path / "data" / "manager.log").read_text("utf-8")
    lock = SingleInstance(tmp_path / "data" / "manager.lock")
    assert lock.acquire()  # released on the failure path
    lock.release()


def test_crash_hooks_log_with_traceback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(app_support, "message_box", lambda t, error=True: shown.append(t))
    old_sys, old_thread = sys.excepthook, threading.excepthook
    try:
        app_support.install_crash_hooks()
        with caplog.at_level(logging.ERROR):
            try:
                raise RuntimeError("fatal main")
            except RuntimeError as e:
                sys.excepthook(type(e), e, e.__traceback__)
            t = threading.Thread(target=lambda: 1 / 0, name="tv-test-crash")
            t.start()
            t.join()
        assert "uncaught exception" in caplog.text and "fatal main" in caplog.text
        assert "thread tv-test-crash crashed" in caplog.text
        assert "ZeroDivisionError" in caplog.text
        assert len(shown) == 1 and "RuntimeError: fatal main" in shown[0]
    finally:
        sys.excepthook, threading.excepthook = old_sys, old_thread


@pytest.mark.skipif(sys.platform != "win32", reason="named events are Windows-only")
def test_second_launch_signals_the_first(tmp_path: Path) -> None:
    name = f"Local\\TokenVault-test-{uuid.uuid4().hex}"
    first_lock = SingleInstance(tmp_path / "manager.lock")
    assert second_launch(first_lock, InstanceSignal(name)) is None  # we are first
    listener = InstanceSignal(name)
    shown = threading.Event()
    assert listener.listen(shown.set)
    try:
        assert second_launch(SingleInstance(tmp_path / "manager.lock"),
                             InstanceSignal(name)) == "signalled"
        assert shown.wait(3)
    finally:
        listener.close()
        first_lock.release()
    held = SingleInstance(tmp_path / "manager.lock")
    held.acquire()
    try:
        assert second_launch(SingleInstance(tmp_path / "manager.lock"),
                             InstanceSignal(name)) == "running"  # nobody listening
    finally:
        held.release()


def test_demo_dir_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "real"
    monkeypatch.setenv("TOKENVAULT_DATA_DIR", str(real))
    with pytest.raises(DemoDirError):
        prepare_demo_dir(real)
    other = tmp_path / "other"
    other.mkdir()
    (other / "manager.db").write_bytes(b"SQLite format 3\0")
    with pytest.raises(DemoDirError):
        prepare_demo_dir(other)
    (other / "manager.db").write_bytes(b"")  # empty db file is fine
    assert prepare_demo_dir(other) == other.resolve()
    assert (other / DEMO_MARKER).exists()
    (other / "manager.db").write_bytes(b"SQLite format 3\0")
    assert prepare_demo_dir(other) == other.resolve()  # marked demo dir: reusable
    fresh = prepare_demo_dir(tmp_path / "new")
    assert (fresh / DEMO_MARKER).exists()
