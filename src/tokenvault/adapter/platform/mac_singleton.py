"""macOS single-instance lock (``fcntl.flock``) and the setup -> agent reload signal.

The lock file holds the agent's pid; ``setup`` signals a reload with SIGUSR1 sent
to that pid. ``fcntl``/``signal`` are injectable so this imports and tests on any OS.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("tokenvault.adapter.singleton")


def _fcntl() -> Any:
    import fcntl

    return fcntl


def _usr1() -> int:
    return int(getattr(signal, "SIGUSR1", 30))


def lock_path(data_dir: Path) -> Path:
    return data_dir / "adapter.lock"


class FlockInstance:
    """``acquire()`` is True for the first holder of the lock file."""

    def __init__(self, path: str | Path, fcntl_mod: Any = None) -> None:
        self.path = Path(path)
        self._fcntl = fcntl_mod
        self._fh: Any = None

    def _mod(self) -> Any:
        return self._fcntl if self._fcntl is not None else _fcntl()

    def acquire(self) -> bool:
        if self._fh is not None:
            return True
        fc = self._mod()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.path, "a+b")  # noqa: SIM115 - held for the process lifetime
        except OSError as e:
            log.warning("cannot open the lock file (%s); not guarding single instance", e)
            return True
        try:
            fc.flock(fh.fileno(), fc.LOCK_EX | fc.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode("ascii"))
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is not None:
            try:
                self._mod().flock(fh.fileno(), self._mod().LOCK_UN)
            finally:
                fh.close()


def is_running(path: str | Path, fcntl_mod: Any = None) -> bool:
    """True if some other process holds the lock."""
    p = Path(path)
    if not p.is_file():
        return False
    fc = fcntl_mod if fcntl_mod is not None else _fcntl()
    try:
        with open(p, "rb") as fh:
            try:
                fc.flock(fh.fileno(), fc.LOCK_EX | fc.LOCK_NB)
            except OSError:
                return True
            fc.flock(fh.fileno(), fc.LOCK_UN)
            return False
    except OSError:
        return False


def wait_released(path: str | Path, timeout_s: float, step_s: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout_s
    while is_running(path):
        if time.monotonic() >= deadline:
            return False
        time.sleep(step_s)
    return True


def read_pid(path: str | Path) -> int | None:
    try:
        pid = int(Path(path).read_bytes().decode("ascii", "ignore").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 1 else None


def signal_reload(path: str | Path, kill: Callable[[int, int], None] | None = None,
                  fcntl_mod: Any = None) -> bool:
    pid = read_pid(path)
    if pid is None or not is_running(path, fcntl_mod):
        return False
    try:
        (kill or os.kill)(pid, _usr1())
    except OSError:
        return False
    return True


def _sig(name: str, default: int) -> int:
    return int(getattr(signal, name, default))


def block_signals(mask: Callable[[int, Any], Any] | None = None) -> None:
    """Block SIGUSR1/SIGUSR2/SIGTERM in the calling thread (and every thread it creates
    afterwards). Call this EARLY in the main thread: pystray's Cocoa loop owns the main
    thread, so Python-level handlers would never run; a dedicated ``sigwait`` thread
    takes the signals instead."""
    fn = mask or getattr(signal, "pthread_sigmask", None)
    if fn is None:
        return
    fn(getattr(signal, "SIG_BLOCK", 0), {_sig("SIGUSR1", 30), _sig("SIGUSR2", 31),
                                         _sig("SIGTERM", 15)})


class SignalReloadListener:
    """Calls ``on_reload`` (once) on SIGUSR1 (setup reload) or SIGTERM (launchctl bootout /
    logout), using a ``sigwait`` thread so it works while the Cocoa loop owns the main
    thread. ``stop()`` wakes the thread with SIGUSR2. The three functions are injectable."""

    def __init__(self, on_reload: Callable[[], None],
                 sigwait: Callable[[Any], int] | None = None,
                 mask: Callable[[int, Any], Any] | None = None,
                 wake: Callable[[], None] | None = None) -> None:
        self._on_reload = on_reload
        self._sigwait = sigwait or getattr(signal, "sigwait", None)
        self._mask = mask
        self._wake = wake or (lambda: os.kill(os.getpid(), _sig("SIGUSR2", 31)))
        self._thread: threading.Thread | None = None
        self._stopping = False

    def start(self) -> bool:
        if self._sigwait is None:
            return False
        try:
            block_signals(self._mask)  # idempotent; threads started later inherit it
        except (ValueError, OSError):
            return False
        self._thread = threading.Thread(target=self._run, name="tv-signals", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        assert self._sigwait is not None
        wanted = {_sig("SIGUSR1", 30), _sig("SIGUSR2", 31), _sig("SIGTERM", 15)}
        while not self._stopping:
            try:
                sig = self._sigwait(wanted)
            except (OSError, ValueError):
                return
            if self._stopping or sig == _sig("SIGUSR2", 31):
                return
            log.info("asked to stop (signal %s); stopping", sig)
            try:
                self._on_reload()
            except Exception:
                log.exception("reload callback failed")
            return

    def stop(self) -> None:
        t, self._thread = self._thread, None
        if t is None:
            return
        self._stopping = True
        if t.is_alive() and t is not threading.current_thread():
            with contextlib.suppress(OSError):
                self._wake()
            t.join(5)
