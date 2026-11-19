"""Filesystem watcher and rate-limited backfill for Claude Code transcripts.

watchdog delivers change notifications (ReadDirectoryChangesW on Windows, so no
polling). Events are coalesced per file with a debounce deadline and collected on
ONE worker thread. Backfill runs on its own daemon thread but takes the same
collect lock, so live and backfill collection never run concurrently.

If ``root`` does not exist yet (Claude never ran) the worker retries attaching
every ``_retry_s`` seconds (30 s by default; deliberately slow and simple).
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from tokenvault.adapter.collector import CollectResult

log = logging.getLogger("tokenvault.adapter.watcher")

BELOW_NORMAL_PRIORITY_CLASS = 0x4000
JOIN_TIMEOUT_S = 5.0
MAX_WAIT_S = 1.0  # a file is collected at most this long after its first event
# Hot-file stat poll: OS notifications can be withheld while a file is written
# continuously, so files that produced an event are stat-ed every HOT_POLL_S and
# dropped after HOT_TTL_S without activity. Nothing is polled when idle.
HOT_POLL_S = 1.0
HOT_TTL_S = 60.0
# watchdog's default (1 s) is how long the Windows emitter waits for a QUIET gap before
# it hands over a batch of notifications: every change arrived >= 1 s late, and a file
# written continuously produced none at all. 0.1 s costs ~10 idle wake-ups/s (Task 23
# E2E: idle agent ~0.3% of one core, was ~0.1%; append -> Manager median 0.65 s, was 1.6 s).
OBSERVER_TIMEOUT_S = 0.1


def _stat(path: Path) -> os.stat_result:
    return os.stat(path)


class _CollectorLike(Protocol):
    def collect_file(self, path: Path) -> CollectResult: ...


def set_background_priority() -> bool:
    """Set this process to BELOW_NORMAL priority on Windows; no-op elsewhere."""
    if sys.platform == "darwin":
        from tokenvault.adapter.platform import mac

        return mac.set_background_priority()
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        return bool(
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        )
    except Exception:
        log.exception("could not set background priority")
        return False


def _is_jsonl(p: str | bytes) -> bool:
    return os.fsdecode(p).lower().endswith(".jsonl")


class _Handler(FileSystemEventHandler):
    def __init__(self, schedule: Callable[[Path], None]) -> None:
        self._schedule = schedule

    def _hit(self, p: str | bytes) -> None:
        if _is_jsonl(p):
            self._schedule(Path(os.fsdecode(p)))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._hit(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._hit(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._hit(event.dest_path)


class Watcher:
    def __init__(
        self,
        root: Path,
        collector: _CollectorLike,
        on_new: Callable[[], None],
        debounce_s: float = 0.5,
    ) -> None:
        self._root = Path(root)
        self._collector = collector
        self._on_new = on_new
        self._debounce_s = debounce_s
        self._retry_s = 30.0
        self._attached = False
        self._observer: Any = None
        self._cond = threading.Condition()
        self._pending: dict[Path, tuple[float, float]] = {}  # path -> (first, last) monotonic
        self._next_attach = 0.0
        # path -> (last activity, last seen (size, mtime_ns) or None before first poll)
        self._hot: dict[Path, tuple[float, tuple[int, int] | None]] = {}
        self._next_poll = 0.0
        self._stopping = threading.Event()
        self._started = False
        self._worker: threading.Thread | None = None
        self._backfill_thread: threading.Thread | None = None
        self._collect_lock = threading.Lock()
        self.backfill_done = threading.Event()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        with self._cond:
            if self._started:
                return
            if self._stopping.is_set():
                return
            self._started = True
        self._try_attach()
        self._worker = threading.Thread(target=self._run, name="tv-watcher", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stopping.set()
        with self._cond:
            self._cond.notify_all()
        with self._cond:
            obs, self._observer = self._observer, None
        if obs is not None:
            try:
                obs.stop()
                obs.join(JOIN_TIMEOUT_S)
            except Exception:
                log.exception("error stopping observer")
        me = threading.current_thread()
        for t in (self._worker, self._backfill_thread):
            if t is not None and t is not me and t.is_alive():
                t.join(JOIN_TIMEOUT_S)
        self._attached = False

    # -- watching --------------------------------------------------------
    def _try_attach(self) -> None:
        if self._attached or self._stopping.is_set():
            return
        if not self._root.is_dir():
            self._next_attach = time.monotonic() + self._retry_s
            return
        try:
            obs = Observer(timeout=OBSERVER_TIMEOUT_S)
            obs.schedule(_Handler(self._schedule), str(self._root), recursive=True)
            obs.daemon = True
            obs.start()
        except Exception:
            log.exception("could not watch %s", self._root)
            self._next_attach = time.monotonic() + self._retry_s
            return
        with self._cond:
            if not self._stopping.is_set():
                self._observer = obs
                self._attached = True
                return
        # stop() won the race: do not leak the observer we just started.
        obs.stop()
        obs.join(JOIN_TIMEOUT_S)

    def _schedule(self, path: Path) -> None:
        """Handle a real OS event: mark the path hot and queue it."""
        self._mark_hot(path)
        self._enqueue(path)

    def _mark_hot(self, path: Path, baseline: bool = False) -> None:
        """Start (or refresh) stat polling of a file. Never touches directories.

        With ``baseline`` the current (size, mtime_ns) is recorded now, so a write
        that lands before the first poll is not mistaken for the baseline.
        """
        now = time.monotonic()
        with self._cond:
            seen = self._hot[path][1] if path in self._hot else None
        if baseline and seen is None:
            try:
                st = _stat(path)
                seen = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
        with self._cond:
            if not self._hot:
                self._next_poll = now + HOT_POLL_S
            self._hot[path] = (now, seen)
            self._cond.notify_all()

    def _enqueue(self, path: Path) -> None:
        now = time.monotonic()
        with self._cond:
            first, _ = self._pending.get(path, (now, now))
            self._pending[path] = (first, now)
            self._cond.notify_all()

    def _due(self, path: Path) -> float:
        first, last = self._pending[path]
        return min(first + MAX_WAIT_S, last + self._debounce_s)

    def _poll_hot(self) -> None:
        """Stat hot files; queue the ones that changed; prune stale entries."""
        with self._cond:
            snapshot = dict(self._hot)
            self._next_poll = time.monotonic() + HOT_POLL_S
        for path, (active, seen) in snapshot.items():
            try:
                st = _stat(path)
            except FileNotFoundError:
                with self._cond:
                    self._hot.pop(path, None)
                continue
            except OSError:
                continue
            cur = (st.st_size, st.st_mtime_ns)
            now = time.monotonic()
            if seen is not None and cur != seen:
                active = now
                self._enqueue(path)
            with self._cond:
                if path in self._hot:
                    prev_active = self._hot[path][0]
                    active = max(active, prev_active)
                    if now - active > HOT_TTL_S:
                        del self._hot[path]
                    else:
                        self._hot[path] = (active, cur)

    # -- worker ----------------------------------------------------------
    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                self._step()
            except Exception:
                log.exception("watcher worker iteration failed")
                self._stopping.wait(0.5)  # avoid a hot loop on a persistent error

    def _step(self) -> None:
        path: Path | None = None
        with self._cond:
            if self._stopping.is_set():
                return
            now = time.monotonic()
            poll = bool(self._hot) and now >= self._next_poll
            due = [p for p in self._pending if self._due(p) <= now]
            if poll:
                pass
            elif due:
                path = min(due, key=self._due)
                del self._pending[path]
            else:
                waits: list[float] = []
                if self._pending:
                    waits.append(min(self._due(p) for p in self._pending) - now)
                if self._hot:
                    waits.append(self._next_poll - now)
                if not self._attached:
                    waits.append(self._next_attach - now)
                self._cond.wait(max(0.0, min(waits)) if waits else None)
        if self._stopping.is_set():
            return
        if poll:
            self._poll_hot()
            return
        if path is None:
            if not self._attached and time.monotonic() >= self._next_attach:
                self._try_attach()
            return
        self._collect(path)

    def _collect(self, path: Path) -> None:
        """Collect one file (looping while `more`), serialised; notify if new."""
        found = False
        with self._collect_lock:
            while not self._stopping.is_set():
                try:
                    r = self._collector.collect_file(path)
                except Exception:
                    log.exception("collect failed for %s", path.name)
                    break
                found = found or r.new_events > 0
                if not r.more:
                    break
        if found:
            self._mark_hot(
                path, baseline=True
            )  # actively growing: keep polling it until it goes quiet
            try:
                self._on_new()
            except Exception:
                log.exception("on_new callback failed")

    # -- backfill --------------------------------------------------------
    def backfill(self, max_age_days: int = 90, rate_files_per_s: int = 5) -> None:
        if self._backfill_thread is not None:
            return
        self.backfill_done.clear()
        t = threading.Thread(
            target=self._backfill,
            args=(max_age_days, rate_files_per_s),
            name="tv-backfill",
            daemon=True,
        )
        self._backfill_thread = t
        t.start()

    def _backfill(self, max_age_days: int, rate: int) -> None:
        try:
            cutoff = time.time() - max_age_days * 86400
            files: list[tuple[float, Path]] = []
            if self._root.is_dir():
                for p in self._root.rglob("*.jsonl"):
                    try:
                        m = p.stat().st_mtime
                    except OSError:
                        continue
                    if m >= cutoff:
                        files.append((m, p))
            files.sort(key=lambda x: x[0], reverse=True)
            interval = 1.0 / max(1, rate)
            for m, p in files:
                if self._stopping.is_set():
                    return
                started = time.monotonic()
                if time.time() - m < HOT_TTL_S:
                    self._mark_hot(
                        p, baseline=True
                    )  # being written right now: poll it, not just events
                self._collect(p)
                if self._stopping.wait(max(0.0, interval - (time.monotonic() - started))):
                    return
        except Exception:
            log.exception("backfill failed")
        finally:
            self.backfill_done.set()
