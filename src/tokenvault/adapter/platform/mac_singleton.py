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


class SignalReloadListener:
    """Calls ``on_reload`` (once) on SIGUSR1. Needs the main thread to install the handler."""

    def __init__(self, on_reload: Callable[[], None],
                 install: Callable[[int, Any], Any] | None = None) -> None:
        self._on_reload = on_reload
        self._install = install or signal.signal
        self._old: Any = None
        self._installed = False
        self._fired = False

    def start(self) -> bool:
        if threading.current_thread() is not threading.main_thread():
            return False
        try:
            self._old = self._install(_usr1(), self._handle)
        except (ValueError, OSError, AttributeError):
            return False
        self._installed = True
        return True

    def _handle(self, signum: int, frame: Any) -> None:
        if self._fired:
            return
        self._fired = True
        log.info("setup asked the agent to reload; stopping")
        # never block inside a signal handler
        threading.Thread(target=self._on_reload, name="tv-reload", daemon=True).start()

    def stop(self) -> None:
        if self._installed:
            self._installed = False
            with contextlib.suppress(ValueError, OSError):
                self._install(_usr1(), self._old if self._old is not None else signal.SIG_DFL)
