"""Single Manager instance per data folder, and "show yourself" for a second launch.

* :class:`SingleInstance`: exclusive byte lock on ``manager.lock`` (the OS drops it if
  the process dies).
* :class:`InstanceSignal`: a per-session named auto-reset event
  (``Local\\TokenVault-Manager-Show-<hash of the data folder>``). The running
  Manager listens on it and shows/restores its window (also when hidden in the
  tray); a second launch only sets it and exits 0.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

log = logging.getLogger(__name__)

WAIT_OBJECT_0 = 0
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000


class SingleInstance:
    """Exclusive lock on ``manager.lock`` (released by the OS if the process dies)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._f: IO[bytes] | None = None

    def acquire(self) -> bool:
        f: IO[bytes] | None = None
        try:
            f = open(self.path, "a+b")  # noqa: SIM115 - held for the process lifetime
            if sys.platform == "win32":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - non-Windows dev boxes
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # held by another process, or no access (PermissionError)
            if f is not None:
                f.close()
            return False
        self._f = f
        return True

    def release(self) -> None:
        if self._f is not None:
            with contextlib.suppress(OSError):
                self._f.close()
            self._f = None


def event_name(data_dir: Path) -> str:
    h = hashlib.sha256(str(Path(data_dir).resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return f"Local\\TokenVault-Manager-Show-{h}"


def _k32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


class InstanceSignal:
    def __init__(self, name: str) -> None:
        self.name = name
        self._handle: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def listen(self, on_signal: Callable[[], None]) -> bool:
        """Create the event and call ``on_signal`` whenever another launch sets it."""
        if sys.platform != "win32":
            return False
        k = _k32()
        k.CreateEventW.restype = ctypes.c_void_p
        h = k.CreateEventW(None, False, False, self.name)
        if not h:
            return False
        self._handle = h

        def loop() -> None:
            k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            while not self._stop.is_set():
                if k.WaitForSingleObject(h, 250) == WAIT_OBJECT_0 and not self._stop.is_set():
                    try:
                        on_signal()
                    except Exception:  # noqa: BLE001
                        log.exception("show-window signal handler failed")

        self._thread = threading.Thread(target=loop, name="tv-instance", daemon=True)
        self._thread.start()
        return True

    def signal(self) -> bool:
        """Ask the running instance to show itself. False if there is none listening."""
        if sys.platform != "win32":
            return False
        k = _k32()
        k.OpenEventW.restype = ctypes.c_void_p
        h = k.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, self.name)
        if not h:
            return False
        try:
            k.SetEvent.argtypes = [ctypes.c_void_p]
            return bool(k.SetEvent(h))
        finally:
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            k.CloseHandle(h)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
            self._thread = None
        if self._handle is not None:
            k = _k32()
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            k.CloseHandle(self._handle)
            self._handle = None


def second_launch(lock: SingleInstance, signal: InstanceSignal) -> str | None:
    """Decide what this launch does: None = we are the first instance (lock held);
    "signalled" = the running one was asked to show itself; "running" = it is running
    but could not be signalled."""
    if lock.acquire():
        return None
    return "signalled" if signal.signal() else "running"
