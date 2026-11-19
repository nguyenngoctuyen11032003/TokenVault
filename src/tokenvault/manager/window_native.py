"""Win32 backend for :class:`window_control.WindowGeometry` (physical pixels, ctypes).

Only the window handle comes from pywebview (``window.native`` is the WinForms form);
everything else is plain Win32 (GetWindowRect / SetWindowPos / MonitorFromWindow /
GetMonitorInfo / GetDpiForWindow), which is safe to call from any thread.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Any

from tokenvault.manager.window_control import Rect

log = logging.getLogger(__name__)

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SW_RESTORE = 9
MONITOR_DEFAULTTONEAREST = 2


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _rect(r: wintypes.RECT) -> Rect:
    return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)


def _user32() -> Any:
    return ctypes.WinDLL("user32", use_last_error=True)


def _monitor_rects(hmon: Any) -> tuple[Rect, Rect]:
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    _user32().GetMonitorInfoW(hmon, ctypes.byref(info))
    return _rect(info.rcMonitor), _rect(info.rcWork)


def all_monitors() -> list[tuple[Rect, Rect]]:
    """(bounds, work area) of every monitor, primary first, in this process's pixels."""
    if sys.platform != "win32":
        return []
    out: list[tuple[Rect, Rect, bool]] = []
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon: Any, _hdc: Any, _rc: Any, _lp: Any) -> int:
        b, w = _monitor_rects(hmon)
        out.append((b, w, b[0] == 0 and b[1] == 0))
        return 1

    _user32().EnumDisplayMonitors(None, None, proc_type(cb), 0)
    out.sort(key=lambda m: not m[2])
    return [(b, w) for b, w, _ in out]


class Win32Native:
    def __init__(self, window: Any) -> None:
        self._window = window
        self._hwnd: int | None = None

    def hwnd(self) -> int:
        if self._hwnd is None:
            self._hwnd = int(self._window.native.Handle.ToInt64())
        return self._hwnd

    def scale(self) -> float:
        try:
            dpi = int(_user32().GetDpiForWindow(wintypes.HWND(self.hwnd())))
        except (AttributeError, OSError):
            dpi = 96
        return dpi / 96 if dpi > 0 else 1.0

    def bounds(self) -> Rect:
        r = wintypes.RECT()
        _user32().GetWindowRect(wintypes.HWND(self.hwnd()), ctypes.byref(r))
        return _rect(r)

    def set_bounds(self, r: Rect) -> None:
        _user32().SetWindowPos(wintypes.HWND(self.hwnd()), None, r[0], r[1], r[2], r[3],
                               SWP_NOZORDER | SWP_NOACTIVATE)

    def monitor(self) -> tuple[Rect, Rect]:
        u = _user32()
        u.MonitorFromWindow.restype = wintypes.HMONITOR
        hmon = u.MonitorFromWindow(wintypes.HWND(self.hwnd()), MONITOR_DEFAULTTONEAREST)
        return _monitor_rects(hmon)

    def monitors(self) -> list[tuple[Rect, Rect]]:
        return all_monitors() or [self.monitor()]

    def normalize_state(self) -> None:
        u = _user32()
        h = wintypes.HWND(self.hwnd())
        if u.IsZoomed(h) or u.IsIconic(h):
            u.ShowWindow(h, SW_RESTORE)
