"""One running Adapter per Windows user session, plus the setup -> agent reload signal.

Names live in the session namespace (``Local\\``), so "per user" means per logon
session: a user logged on twice (e.g. console + RDP) can run two agents. That
is safe: both share one SQLite outbox whose writes are transactional and whose
rows are idempotent per message_id, and the Manager upserts on
(device_id, message_id).

Reload: the running agent waits (blocking, no polling) on the auto-reset event
``<mutex name>-reload``. ``setup`` sets it before rewriting config/secrets; the
agent then stops cleanly and exits, releasing the mutex, and setup starts the
new agent (see ``main.cmd_setup``).

``TOKENVAULT_MUTEX_NAME`` (only when set) overrides the mutex name - used by
tests so they never collide with a real agent on the developer's machine.
"""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger("tokenvault.adapter.singleton")

SUPPORTED = sys.platform == "win32"
_ERROR_ALREADY_EXISTS = 183
_SYNCHRONIZE = 0x00100000
_EVENT_MODIFY_STATE = 0x0002
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0


def _mac() -> bool:
    return sys.platform == "darwin"


def mutex_name(user: str | None = None) -> str:
    override = os.environ.get("TOKENVAULT_MUTEX_NAME")
    if override and user is None:
        return override
    if _mac():  # macOS: the "name" is the flock lock-file path (per user home)
        from tokenvault.adapter.platform import mac, mac_singleton

        return str(mac_singleton.lock_path(mac.data_dir()))
    u = (user if user is not None else getpass.getuser()).lower()
    return "Local\\TokenVaultAdapter-" + hashlib.sha256(u.encode("utf-8")).hexdigest()[:16]


def reload_event_name(mutex: str | None = None) -> str:
    return (mutex or mutex_name()) + "-reload"


def _mac_lock_for(event_name: str) -> str:
    return event_name[: -len("-reload")] if event_name.endswith("-reload") else event_name


def _kernel32() -> Any:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    h, b, s, u = ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_uint32
    k.CreateMutexW.argtypes = [h, b, s]
    k.CreateMutexW.restype = h
    k.OpenMutexW.argtypes = [u, b, s]
    k.OpenMutexW.restype = h
    k.CreateEventW.argtypes = [h, b, b, s]
    k.CreateEventW.restype = h
    k.OpenEventW.argtypes = [u, b, s]
    k.OpenEventW.restype = h
    k.SetEvent.argtypes = [h]
    k.SetEvent.restype = b
    k.WaitForMultipleObjects.argtypes = [u, ctypes.POINTER(h), b, u]
    k.WaitForMultipleObjects.restype = u
    k.CloseHandle.argtypes = [h]
    k.CloseHandle.restype = b
    return k


class SingleInstance:
    """``acquire()`` is True for the first holder of ``name`` in this session."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._handle: int | None = None
        self._mac_inst: Any = None

    def acquire(self) -> bool:
        if _mac():
            if self._mac_inst is None:
                from tokenvault.adapter.platform.mac_singleton import FlockInstance

                self._mac_inst = FlockInstance(self.name)
            return bool(self._mac_inst.acquire())
        if not SUPPORTED:
            return True  # Windows-only product; dev runs elsewhere are not guarded
        if self._handle is not None:
            return True
        k = _kernel32()
        handle = k.CreateMutexW(None, False, self.name)
        err = ctypes.get_last_error()
        if not handle:
            log.warning("CreateMutexW failed (error %s); not guarding single instance", err)
            return True
        if err == _ERROR_ALREADY_EXISTS:
            k.CloseHandle(handle)
            return False
        self._handle = int(handle)
        return True

    def release(self) -> None:
        if self._mac_inst is not None:
            self._mac_inst.release()
        if self._handle is not None:
            _kernel32().CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> SingleInstance:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def is_running(name: str) -> bool:
    """True if some process in this session holds the mutex ``name``."""
    if _mac():
        from tokenvault.adapter.platform import mac_singleton

        return bool(mac_singleton.is_running(name))
    if not SUPPORTED:
        return False
    k = _kernel32()
    handle = k.OpenMutexW(_SYNCHRONIZE, False, name)
    if not handle:
        return False
    k.CloseHandle(handle)
    return True


def wait_released(name: str, timeout_s: float, step_s: float = 0.1) -> bool:
    """Wait (bounded; used by short-lived ``setup`` only) until nobody holds ``name``."""
    deadline = time.monotonic() + timeout_s
    while is_running(name):
        if time.monotonic() >= deadline:
            return False
        time.sleep(step_s)
    return True


def signal_reload(event_name: str) -> bool:
    """Set the agent's reload event. False if no agent is listening."""
    if _mac():
        from tokenvault.adapter.platform import mac_singleton

        return bool(mac_singleton.signal_reload(_mac_lock_for(event_name)))
    if not SUPPORTED:
        return False
    k = _kernel32()
    handle = k.OpenEventW(_EVENT_MODIFY_STATE, False, event_name)
    if not handle:
        return False
    try:
        return bool(k.SetEvent(handle))
    finally:
        k.CloseHandle(handle)


class ReloadListener:
    """Calls ``on_reload`` (once) when setup signals ``event_name``. Blocking wait."""

    def __init__(self, event_name: str, on_reload: Callable[[], None]) -> None:
        self.event_name = event_name
        self._on_reload = on_reload
        self._handles: tuple[int, int] | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._mac_listener: Any = None

    def start(self) -> bool:
        if _mac():
            from tokenvault.adapter.platform.mac_singleton import SignalReloadListener

            self._mac_listener = SignalReloadListener(self._on_reload)
            return bool(self._mac_listener.start())
        if not SUPPORTED:
            return False
        k = _kernel32()
        reload_h = k.CreateEventW(None, False, False, self.event_name)
        stop_h = k.CreateEventW(None, True, False, None)
        if not reload_h or not stop_h:
            log.warning("reload event unavailable (error %s)", ctypes.get_last_error())
            for h in (reload_h, stop_h):
                if h:
                    k.CloseHandle(h)
            return False
        self._handles = (int(reload_h), int(stop_h))
        self._thread = threading.Thread(target=self._run, name="tv-reload", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        assert self._handles is not None
        k = _kernel32()
        arr = (ctypes.c_void_p * 2)(*self._handles)
        r = k.WaitForMultipleObjects(2, arr, False, _INFINITE)
        if r == _WAIT_OBJECT_0 and not self._stopping:
            log.info("setup asked the agent to reload; stopping")
            try:
                self._on_reload()
            except Exception:
                log.exception("reload callback failed")

    def stop(self) -> None:
        if self._mac_listener is not None:
            self._mac_listener.stop()
            self._mac_listener = None
        handles, self._handles = self._handles, None
        if handles is None:
            return
        self._stopping = True
        k = _kernel32()
        k.SetEvent(handles[1])
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(5)
        for h in handles:
            k.CloseHandle(h)
