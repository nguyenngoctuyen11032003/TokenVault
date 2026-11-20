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
WM_SETICON = 0x0080
ICON_SMALL, ICON_BIG = 0, 1
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
SM_CXICON, SM_CYICON, SM_CXSMICON, SM_CYSMICON = 11, 12, 49, 50


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


def apply_window_icon(hwnd: int, ico_path: str, user32: Any = None) -> bool:
    """WM_SETICON with both ICON_BIG (taskbar, Alt+Tab) and ICON_SMALL (caption) loaded
    from the multi-size .ico at the system's metrics. False when nothing could be set."""
    u = user32 if user32 is not None else _user32()
    h = wintypes.HWND(hwnd)
    done = False
    for kind, cx_metric, cy_metric in ((ICON_BIG, SM_CXICON, SM_CYICON),
                                       (ICON_SMALL, SM_CXSMICON, SM_CYSMICON)):
        hicon = u.LoadImageW(None, ico_path, IMAGE_ICON, int(u.GetSystemMetrics(cx_metric)),
                             int(u.GetSystemMetrics(cy_metric)), LR_LOADFROMFILE)
        if hicon:
            u.SendMessageW(h, WM_SETICON, kind, hicon)
            done = True
        else:
            log.debug("cannot load window icon %s (kind %s)", ico_path, kind)
    return done


class Win32Native:
    def __init__(self, window: Any) -> None:
        self._window = window
        self._hwnd: int | None = None

    def hwnd(self) -> int:
        if self._hwnd is None:
            self._hwnd = int(self._window.native.Handle.ToInt64())
        return self._hwnd

    def set_icon(self, ico_path: str) -> bool:
        u = _user32()
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                   wintypes.LPARAM]
        u.SendMessageW.restype = wintypes.LPARAM
        return apply_window_icon(self.hwnd(), ico_path, u)

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

    def is_minimized(self) -> bool:
        return bool(_user32().IsIconic(wintypes.HWND(self.hwnd())))

    def is_zoomed(self) -> bool:
        return bool(_user32().IsZoomed(wintypes.HWND(self.hwnd())))

    def set_region(self, points: list[tuple[int, int]] | None) -> bool:
        """SetWindowRgn(polygon) or remove it (None). The OS owns a region once
        SetWindowRgn succeeds; it is deleted here only when that call fails."""
        u = _user32()
        u.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        u.SetWindowRgn.restype = ctypes.c_int
        h = wintypes.HWND(self.hwnd())
        if points is None:
            return bool(u.SetWindowRgn(h, None, True))
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi.CreatePolygonRgn.argtypes = [ctypes.POINTER(wintypes.POINT), ctypes.c_int,
                                         ctypes.c_int]
        gdi.CreatePolygonRgn.restype = wintypes.HRGN
        gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        arr = (wintypes.POINT * len(points))(*[wintypes.POINT(x, y) for x, y in points])
        rgn = gdi.CreatePolygonRgn(arr, len(points), 2)  # WINDING
        if not rgn:
            return False
        if not u.SetWindowRgn(h, rgn, True):
            gdi.DeleteObject(rgn)
            return False
        return True
