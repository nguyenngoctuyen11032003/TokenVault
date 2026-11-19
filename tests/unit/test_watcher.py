from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from tokenvault.adapter.collector import CollectResult
from tokenvault.adapter.watcher import Watcher, set_background_priority


class FakeCollector:
    """Counts lines appended since last call; yields them as new events."""

    def __init__(self) -> None:
        self.calls: list[Path] = []
        self._seen: dict[Path, int] = {}
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def collect_file(self, path: Path) -> CollectResult:
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            self.calls.append(path)
            n = len(path.read_bytes().splitlines())
            new = n - self._seen.get(path, 0)
            self._seen[path] = n
            return CollectResult(new_events=new)
        finally:
            with self._lock:
                self._active -= 1


def wait_for(cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def append(path: Path, text: str = "{}\n") -> None:
    with open(path, "ab") as fh:
        fh.write(text.encode())


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "projects"
    r.mkdir()
    return r


def test_append_triggers_on_new_within_2s(root: Path) -> None:
    f = root / "a.jsonl"
    f.write_text("")
    hits: list[int] = []
    w = Watcher(root, FakeCollector(), lambda: hits.append(1), debounce_s=0.1)
    w.start()
    try:
        time.sleep(0.2)
        append(f)
        assert wait_for(lambda: len(hits) >= 1, 2.0)
    finally:
        w.stop()


def test_burst_of_20_appends_coalesces_to_few_calls(root: Path) -> None:
    f = root / "a.jsonl"
    f.write_text("")
    col = FakeCollector()
    hits: list[int] = []
    w = Watcher(root, col, lambda: hits.append(1), debounce_s=0.5)
    w.start()
    try:
        time.sleep(0.2)
        before = len(col.calls)
        for _ in range(20):
            append(f)
        assert wait_for(lambda: len(hits) >= 1, 3.0)
        time.sleep(1.0)
        assert 1 <= len(col.calls) - before <= 3
        assert col.max_active == 1
    finally:
        w.stop()


def test_new_file_in_new_project_dir_is_picked_up(root: Path) -> None:
    hits: list[int] = []
    col = FakeCollector()
    w = Watcher(root, col, lambda: hits.append(1), debounce_s=0.1)
    w.start()
    try:
        time.sleep(0.2)
        d = root / "new-project"
        d.mkdir()
        time.sleep(0.2)
        append(d / "s.jsonl")
        assert wait_for(lambda: len(hits) >= 1, 3.0)
        assert any(p.name == "s.jsonl" for p in col.calls)
    finally:
        w.stop()


def test_non_jsonl_ignored(root: Path) -> None:
    col = FakeCollector()
    w = Watcher(root, col, lambda: None, debounce_s=0.1)
    w.start()
    try:
        time.sleep(0.2)
        append(root / "x.txt")
        time.sleep(0.6)
        assert col.calls == []
    finally:
        w.stop()


def test_backfill_respects_max_age(root: Path) -> None:
    old = root / "old.jsonl"
    new = root / "new.jsonl"
    mid = root / "mid.jsonl"
    for p in (old, new, mid):
        append(p)
    now = time.time()
    os.utime(old, (now - 200 * 86400,) * 2)
    os.utime(mid, (now - 10 * 86400,) * 2)
    os.utime(new, (now - 1,) * 2)
    col = FakeCollector()
    hits: list[int] = []
    w = Watcher(root, col, lambda: hits.append(1))
    try:
        w.backfill(max_age_days=90, rate_files_per_s=100)
        assert w.backfill_done.wait(5.0)
        assert [p.name for p in col.calls] == ["new.jsonl", "mid.jsonl"]
        assert len(hits) == 2
    finally:
        w.stop()


def test_missing_root_does_not_crash_and_attaches_later(tmp_path: Path) -> None:
    root = tmp_path / "later" / "projects"
    hits: list[int] = []
    w = Watcher(root, FakeCollector(), lambda: hits.append(1), debounce_s=0.1)
    w._retry_s = 0.1
    w.start()
    try:
        root.mkdir(parents=True)
        assert wait_for(lambda: w._attached, 3.0)
        time.sleep(0.2)
        append(root / "a.jsonl")
        assert wait_for(lambda: len(hits) >= 1, 3.0)
    finally:
        w.stop()


def test_collector_exception_does_not_kill_worker(root: Path) -> None:
    calls: list[int] = []

    class Boom(FakeCollector):
        def collect_file(self, path: Path) -> CollectResult:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return super().collect_file(path)

    hits: list[int] = []
    f = root / "a.jsonl"
    f.write_text("")
    w = Watcher(root, Boom(), lambda: hits.append(1), debounce_s=0.1)
    w.start()
    try:
        time.sleep(0.2)
        append(f)
        assert wait_for(lambda: len(calls) >= 1, 2.0)  # first append hit the exception
        time.sleep(0.3)
        assert hits == []
        append(f)
        assert wait_for(lambda: len(hits) >= 1, 3.0)
        assert len(calls) >= 2
    finally:
        w.stop()


def test_stop_is_clean_no_thread_leak(root: Path) -> None:
    baseline = threading.active_count()
    w = Watcher(root, FakeCollector(), lambda: None, debounce_s=0.1)
    w.start()
    w.backfill()
    w.backfill_done.wait(2.0)
    w.stop()
    w.stop()  # idempotent
    assert wait_for(lambda: threading.active_count() == baseline, 3.0)


def test_set_background_priority_returns_bool() -> None:
    assert isinstance(set_background_priority(), bool)


def test_start_does_not_scan_existing_files(root: Path) -> None:
    for n in range(5):
        append(root / f"s{n}.jsonl")
    col = FakeCollector()
    w = Watcher(root, col, lambda: None, debounce_s=0.1)
    w.start()
    try:
        time.sleep(0.6)
        assert col.calls == []
    finally:
        w.stop()


def test_continuous_events_still_delivered_before_they_stop(root: Path) -> None:
    # Events are injected directly: on Windows, NTFS/ReadDirectoryChangesW can
    # withhold notifications entirely while a file is written every ~100 ms, so a
    # real writer cannot exercise the debounce cap deterministically.
    f = root / "a.jsonl"
    f.write_text("")
    hits: list[float] = []
    w = Watcher(root, FakeCollector(), lambda: hits.append(time.monotonic()), debounce_s=0.5)
    w.start()
    try:
        t0 = time.monotonic()
        end = t0 + 2.5
        while time.monotonic() < end and not hits:
            append(f)
            w._schedule(f)  # event every 100 ms, always inside the debounce window
            time.sleep(0.1)
        assert hits, "on_new never fired while events were still arriving"
        assert hits[0] - t0 < 2.0
    finally:
        w.stop()


def test_stop_right_after_start_never_leaks_threads(root: Path) -> None:
    baseline = threading.active_count()
    for _ in range(8):  # each stop() costs ~1 s (watchdog join)
        w = Watcher(root, FakeCollector(), lambda: None, debounce_s=0.1)
        w.start()
        w.stop()
    assert wait_for(lambda: threading.active_count() == baseline, 3.0)


def test_more_flag_loops_same_file(root: Path) -> None:
    f = root / "a.jsonl"
    f.write_text("")
    results = [CollectResult(more=True), CollectResult(more=True), CollectResult()]
    calls: list[Path] = []

    class More:
        def collect_file(self, path: Path) -> CollectResult:
            calls.append(path)
            return results[len(calls) - 1] if len(calls) <= 3 else CollectResult()

    hits: list[int] = []
    w = Watcher(root, More(), lambda: hits.append(1), debounce_s=0.1)
    w.start()
    try:
        time.sleep(0.2)
        append(f)
        assert wait_for(lambda: len(calls) >= 3, 3.0)
        time.sleep(0.4)
        assert len(calls) == 3
        assert hits == []
    finally:
        w.stop()


def test_uppercase_extension_is_collected(root: Path) -> None:
    hits: list[int] = []
    w = Watcher(root, FakeCollector(), lambda: hits.append(1), debounce_s=0.1)
    w.start()
    try:
        time.sleep(0.2)
        append(root / "A.JSONL")
        assert wait_for(lambda: len(hits) >= 1, 3.0)
    finally:
        w.stop()


def test_file_moved_into_place_is_collected(root: Path, tmp_path: Path) -> None:
    tmp = tmp_path / "staging.tmp"
    tmp.write_text("{}\n")
    hits: list[int] = []
    col = FakeCollector()
    w = Watcher(root, col, lambda: hits.append(1), debounce_s=0.1)
    w.start()
    try:
        time.sleep(0.2)
        os.replace(tmp, root / "moved.jsonl")
        assert wait_for(lambda: len(hits) >= 1, 3.0)
        assert col.calls[-1].name == "moved.jsonl"
    finally:
        w.stop()


def test_modification_during_collect_is_collected_again(root: Path) -> None:
    f = root / "a.jsonl"
    f.write_text("")
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    class Slow(FakeCollector):
        def collect_file(self, path: Path) -> CollectResult:
            calls.append(1)
            if len(calls) == 1:
                started.set()
                release.wait(3.0)
            return super().collect_file(path)

    hits: list[int] = []
    w = Watcher(root, Slow(), lambda: hits.append(1), debounce_s=0.1)
    w.start()
    try:
        time.sleep(0.2)
        append(f)
        assert started.wait(3.0)
        append(f)  # arrives while the first collect is running
        time.sleep(0.3)
        release.set()
        assert wait_for(lambda: len(calls) >= 2, 3.0)
    finally:
        release.set()
        w.stop()


def test_stop_during_backfill_sleep_returns_promptly(root: Path) -> None:
    for n in range(4):
        append(root / f"s{n}.jsonl")
    w = Watcher(root, FakeCollector(), lambda: None)
    w.backfill(rate_files_per_s=1)
    time.sleep(0.3)  # first file done, now sleeping ~1 s
    t0 = time.monotonic()
    w.stop()
    assert time.monotonic() - t0 < 1.0
    assert w.backfill_done.is_set()
