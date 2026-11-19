"""One running Adapter per Windows user: a named mutex in the session namespace."""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import logging
import sys
from typing import Any

log = logging.getLogger("tokenvault.adapter.singleton")

SUPPORTED = sys.platform == "win32"
_ERROR_ALREADY_EXISTS = 183
_SYNCHRONIZE = 0x00100000


def mutex_name(user: str | None = None) -> str:
    u = (user if user is not None else getpass.getuser()).lower()
    return "Local\\TokenVaultAdapter-" + hashlib.sha256(u.encode("utf-8")).hexdigest()[:16]


def _kernel32() -> Any:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    k.CreateMutexW.restype = ctypes.c_void_p
    k.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    k.OpenMutexW.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.CloseHandle.restype = ctypes.c_int
    return k


class SingleInstance:
    """``acquire()`` is True for the first holder of ``name`` in this session."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._handle: int | None = None

    def acquire(self) -> bool:
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
        if self._handle is not None:
            _kernel32().CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> SingleInstance:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def is_running(name: str) -> bool:
    """True if some process in this session holds the mutex ``name``."""
    if not SUPPORTED:
        return False
    k = _kernel32()
    handle = k.OpenMutexW(_SYNCHRONIZE, False, name)
    if not handle:
        return False
    k.CloseHandle(handle)
    return True
