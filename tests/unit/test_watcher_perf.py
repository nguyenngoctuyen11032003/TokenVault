"""Task 30: the watcher sleeps while idle and paces its backfill."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

import tokenvault.adapter.watcher as wmod
from tokenvault.adapter.collector import CollectResult
from tokenvault.adapter.cpu_budget import CpuBudget
from tokenvault.adapter.watcher import BACKFILL_CHUNK_BYTES, Watcher


def wait_for(pred, timeout: float = 5.0) -> bool:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


class Chunky:
    """Each file yields `chunks` steps (more=True until the last)."""

    def __init__(self, chunks: int = 3) -> None:
        self.chunks = chunks
        self.calls: list[tuple[str, int | None]] = []
        self._left: dict[Path, int] = {}
        self.threads: set[str] = set()

    def collect_file(self, path: Path, max_bytes: int | None = None) -> CollectResult:
        self.calls.append((path.name, max_bytes))
        self.threads.add(threading.current_thread().name)
        left = self._left.get(path, self.chunks) - 1
        self._left[path] = left
        return CollectResult(new_events=1, more=left > 0)


class CountingBudget(CpuBudget):
    def __init__(self) -> None:
        super().__init__()
        self.paced = 0

    def pace(self, stop: threading.Event | None = None) -> bool:
        self.paced += 1
        return False


@pytest.mark.skipif(sys.platform != "win32", reason="quiet emitter is Windows only")
def test_idle_observer_does_not_poll(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the stock emitter polled its buffer queue every 0.1 s (10 wake-ups/s)."""
    from watchdog.observers import winapi

    calls: list[float] = []
    real = winapi.DirectoryChangeReader.get_events

    def spy(self, timeout):  # type: ignore[no-untyped-def]
        calls.append(time.monotonic())
        return real(self, timeout)

    monkeypatch.setattr(winapi.DirectoryChangeReader, "get_events", spy)
    obs = wmod.make_observer(0.1)
    assert type(obs).__name__ == "BaseObserver"
    obs.schedule(wmod._Handler(lambda p: None), str(tmp_path), recursive=True)
    obs.start()
    try:
        time.sleep(1.2)
        idle_calls = len(calls)
        (tmp_path / "a.jsonl").write_text("x\n")
        assert wait_for(lambda: len(calls) > idle_calls, 2.0)  # a change still wakes it
    finally:
        t0 = time.monotonic()
        obs.stop()
        obs.join(5)
        stop_s = time.monotonic() - t0
    assert idle_calls <= 1, idle_calls  # stock watchdog: ~12 in 1.2 s
    assert stop_s < 2.0


def test_backfill_reads_small_chunks_and_paces_between_them(tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"f{i}.jsonl").write_text("x\n")
    col = Chunky(chunks=3)
    budget = CountingBudget()
    w = Watcher(tmp_path, col, lambda: None, budget=budget)
    w.backfill(rate_files_per_s=100)
    try:
        assert w.backfill_done.wait(10)
    finally:
        w.stop()
    bf = [c for c in col.calls]
    assert bf and all(mb == BACKFILL_CHUNK_BYTES for _n, mb in bf)
    assert len(bf) == 9
    assert budget.paced >= 6 + 3  # between chunks of a file + after each file


def test_live_collection_is_never_paced(tmp_path: Path) -> None:
    f = tmp_path / "live.jsonl"
    f.write_text("x\n")
    col = Chunky(chunks=2)
    budget = CountingBudget()
    w = Watcher(tmp_path, col, lambda: None, debounce_s=0.05, budget=budget)
    w.start()
    try:
        time.sleep(0.3)
        with open(f, "a") as fh:
            fh.write("y\n")
        assert wait_for(lambda: any(mb is None for _n, mb in col.calls), 5)
    finally:
        w.stop()
    assert budget.paced == 0


def test_quiet_hot_file_is_polled_less_often(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wmod, "HOT_QUIET_S", 0.0)
    f = tmp_path / "a.jsonl"
    f.write_text("x\n")
    w = Watcher(tmp_path, Chunky(), lambda: None)
    w._mark_hot(f, baseline=True)
    time.sleep(0.01)
    w._poll_hot()
    assert w._next_poll - time.monotonic() > wmod.HOT_POLL_S + 0.5
    w._mark_hot(f)  # activity again: fast polling resumes
    assert w._next_poll - time.monotonic() <= wmod.HOT_POLL_S + 0.01
