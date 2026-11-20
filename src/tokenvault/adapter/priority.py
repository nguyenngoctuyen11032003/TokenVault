"""Scheduling hints that keep the agent out of the employee's way.

* Process: BELOW_NORMAL priority class + EcoQoS ("power throttling" of execution
  speed: Windows may run the process on efficiency cores / at a low clock).
  BELOW_NORMAL rather than IDLE so a live response still gets collected on a busy
  machine.
* Backfill thread: ``THREAD_MODE_BACKGROUND_BEGIN`` = lowest CPU scheduling AND very
  low I/O and memory priority for that thread only, so reading weeks of history
  never competes with the user's disk work. Ended when the backfill finishes.

Every call is best effort: False when the OS refuses or is not Windows.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger("tokenvault.adapter.priority")

BELOW_NORMAL_PRIORITY_CLASS = 0x4000
PROCESS_POWER_THROTTLING = 4  # PROCESS_INFORMATION_CLASS.ProcessPowerThrottling
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
THREAD_MODE_BACKGROUND_BEGIN = 0x00010000
THREAD_MODE_BACKGROUND_END = 0x00020000


class _PowerThrottling(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong),
                ("StateMask", ctypes.c_ulong)]


_PROTOS = {  # name -> (restype, argtypes); handles must not be truncated to 32 bits
    "GetCurrentProcess": (ctypes.c_void_p, None),
    "GetCurrentThread": (ctypes.c_void_p, None),
    "SetPriorityClass": (None, [ctypes.c_void_p, ctypes.c_uint32]),
    "SetThreadPriority": (None, [ctypes.c_void_p, ctypes.c_int]),
    "SetProcessInformation": (None, [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                     ctypes.c_ulong]),
}


def _k32() -> object:
    k = ctypes.windll.kernel32
    for name, (res, args) in _PROTOS.items():
        fn = getattr(k, name, None)
        if fn is None:
            continue
        if res is not None:
            fn.restype = res
        if args is not None:
            fn.argtypes = args
    return k


def set_eco_qos(kernel32: object | None = None) -> bool:
    """Opt the process into EcoQoS (Windows 10 1709+). No-op elsewhere."""
    if sys.platform != "win32" and kernel32 is None:
        return False
    try:
        k = kernel32 if kernel32 is not None else _k32()
        st = _PowerThrottling(PROCESS_POWER_THROTTLING_CURRENT_VERSION,
                              PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
                              PROCESS_POWER_THROTTLING_EXECUTION_SPEED)
        return bool(k.SetProcessInformation(  # type: ignore[attr-defined]
            k.GetCurrentProcess(), PROCESS_POWER_THROTTLING,  # type: ignore[attr-defined]
            ctypes.byref(st), ctypes.sizeof(st)))
    except Exception:  # noqa: BLE001 - older Windows: no EcoQoS, still fine
        log.debug("EcoQoS unavailable", exc_info=True)
        return False


def set_background_priority() -> bool:
    """BELOW_NORMAL priority class + EcoQoS on Windows; nice/QoS on macOS."""
    if sys.platform == "darwin":
        from tokenvault.adapter.platform import mac

        return mac.set_background_priority()
    if sys.platform != "win32":
        return False
    try:
        k = _k32()
        ok = bool(k.SetPriorityClass(k.GetCurrentProcess(),  # type: ignore[attr-defined]
                                     BELOW_NORMAL_PRIORITY_CLASS))
    except Exception:
        log.exception("could not set background priority")
        return False
    set_eco_qos(k)
    return ok


def trim_working_set() -> bool:
    """Hand this process's resident pages back to the OS (standby list).

    Called when the agent goes idle (backfill finished, no file active for a minute):
    an idle agent then shows a few MB in Task Manager instead of ~40 MB. Pages it
    touches again come back as cheap soft faults; nothing is freed or lost.
    """
    if sys.platform != "win32":
        return False
    try:
        k = _k32()
        fn = k.SetProcessWorkingSetSizeEx  # type: ignore[attr-defined]
        fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_ulong]
        minus_one = ctypes.c_size_t(-1).value
        return bool(fn(k.GetCurrentProcess(), minus_one, minus_one, 0))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        log.debug("working set trim unavailable", exc_info=True)
        return False


def thread_background(on: bool) -> bool:
    """Enter/leave background mode (low CPU + I/O + memory priority) for THIS thread."""
    if sys.platform != "win32":
        return False
    try:
        k = _k32()
        mode = THREAD_MODE_BACKGROUND_BEGIN if on else THREAD_MODE_BACKGROUND_END
        return bool(k.SetThreadPriority(k.GetCurrentThread(), mode))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        log.debug("thread background mode unavailable", exc_info=True)
        return False
