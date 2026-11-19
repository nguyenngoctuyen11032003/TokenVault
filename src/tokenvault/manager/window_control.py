"""Window control for the frameless Manager window (contract: "Window methods").

Pure logic over a small native backend (:class:`Native`, physical pixels), so it is
unit-testable with a fake. Everything the UI sees is in LOGICAL (DPI-scaled) pixels,
the same as ``window.innerWidth``: ``logical = physical / scale``.

* maximise = the monitor's WORK AREA (never covers the taskbar); restore goes back to
  the saved normal bounds; full screen = the whole monitor. Both are done by setting
  the window bounds ourselves (a borderless WinForms maximise would cover the taskbar).
* ``set_bounds`` (used by the UI's resize handles) leaves maximise/full screen first,
  then clamps to the minimum size and to the work area of the monitor under the
  window's centre.
* ``on_native_change`` (OS moved/resized the window: drag region, snap) clears the
  maximised/full-screen flag when the window no longer matches that rect, and records
  new normal bounds.
* Every change calls ``on_change`` (state push) and ``persist(snapshot)`` where the
  snapshot is ``{x, y, width, height, maximized}`` of the NORMAL bounds (never full
  screen). :func:`initial_geometry` validates a saved snapshot against the monitors.
* Native edge resizing is not available (the WebView2 child covers the client area,
  so ``WM_NCHITTEST`` never reaches the borderless form): ``native_resize`` is false
  and the UI draws its own handles using ``get_bounds``/``set_bounds``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol

Rect = tuple[int, int, int, int]  # x, y, width, height
MIN_SIZE = (1100, 720)
DEFAULT_SIZE = (1440, 900)
_TOL = 3  # px tolerance when comparing with an expected rect


class Native(Protocol):
    def scale(self) -> float: ...
    def bounds(self) -> Rect: ...
    def set_bounds(self, r: Rect) -> None: ...
    def monitor(self) -> tuple[Rect, Rect]: ...  # (bounds, work area) of the window's monitor
    def monitors(self) -> list[tuple[Rect, Rect]]: ...
    def normalize_state(self) -> None: ...  # leave a native maximised/minimised state
    def is_minimized(self) -> bool: ...
    def is_zoomed(self) -> bool: ...  # natively maximised (Win+Up)
    def set_region(self, points: list[tuple[int, int]] | None) -> bool: ...


CHAMFER_CSS = 30  # --tv-chamfer in ui/css/theme.css; window.css cuts 1x on top, 0.75x below


def chamfer_polygon(w: int, h: int, scale: float,
                    chamfer: float = CHAMFER_CSS) -> list[tuple[int, int]]:
    """The UI's ``.tv-window`` clip-path polygon in window-relative PHYSICAL px."""
    lim = max(min(w, h) // 4, 0)
    c = min(round(chamfer * scale), lim)
    b = min(round(chamfer * 0.75 * scale), lim)
    return [(c, 0), (w - c, 0), (w, c), (w, h - b), (w - b, h), (b, h), (0, h - b), (0, c)]


def _close(a: Rect, b: Rect) -> bool:
    return all(abs(p - q) <= _TOL for p, q in zip(a, b, strict=True))


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(v, hi)) if hi >= lo else lo


def _overlap(a: Rect, b: Rect) -> tuple[int, int]:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(w, 0), max(h, 0)


def fit_rect(r: Rect, work: Rect, min_size: tuple[int, int] = MIN_SIZE) -> Rect:
    """Clamp size to [min, work] and position into ``work``."""
    wx, wy, ww, wh = work
    w = _clamp(r[2], min(min_size[0], ww), ww)
    h = _clamp(r[3], min(min_size[1], wh), wh)
    return _clamp(r[0], wx, wx + ww - w), _clamp(r[1], wy, wy + wh - h), w, h


def centred(work: Rect, size: tuple[int, int] = DEFAULT_SIZE,
            min_size: tuple[int, int] = MIN_SIZE) -> Rect:
    wx, wy, ww, wh = work
    w = _clamp(size[0], min(min_size[0], ww), ww)
    h = _clamp(size[1], min(min_size[1], wh), wh)
    return wx + (ww - w) // 2, wy + (wh - h) // 2, w, h


def initial_geometry(saved: Any, work_areas: list[Rect]) -> tuple[Rect, bool]:
    """Logical start rect + maximised flag from a saved snapshot (validated)."""
    if not work_areas:
        return (0, 0, *DEFAULT_SIZE), False
    primary = work_areas[0]
    if isinstance(saved, dict):
        try:
            r = (int(saved["x"]), int(saved["y"]), int(saved["width"]), int(saved["height"]))
            maxed = bool(saved.get("maximized", False))
        except (KeyError, TypeError, ValueError):
            return centred(primary), False
        for work in work_areas:
            ow, oh = _overlap(r, work)
            if ow >= 200 and oh >= 150 and r[2] <= work[2] + _TOL and r[3] <= work[3] + _TOL:
                return fit_rect(r, work), maxed
        return centred(primary), maxed
    return centred(primary), False


class WindowGeometry:
    def __init__(
        self,
        native: Native,
        *,
        on_change: Callable[[], None] | None = None,
        persist: Callable[[dict[str, Any]], None] | None = None,
        min_size: tuple[int, int] = MIN_SIZE,
    ) -> None:
        self._n = native
        self._on_change = on_change
        self._persist = persist
        self.min_size = min_size
        self._lock = threading.RLock()
        self.maximized = False
        self.fullscreen = False
        self._fs_was_max = False
        self._normal: Rect | None = None  # logical
        self.region_enabled = False  # native chamfered region (transparency fallback)
        self.region_active = False
        self._region_size: tuple[int, int] | None = None
        try:  # remember the starting normal bounds (no window yet -> on first sync)
            if not native.is_minimized() and not native.is_zoomed():
                self._normal = self._log(native.bounds())
        except Exception:  # noqa: BLE001 - native window not created yet
            self._normal = None

    # ----------------------------------------------------------- helpers
    def _log(self, r: Rect) -> Rect:
        s = self._n.scale() or 1.0
        return (round(r[0] / s), round(r[1] / s), round(r[2] / s), round(r[3] / s))

    def _phys(self, r: Rect) -> Rect:
        s = self._n.scale() or 1.0
        return (round(r[0] * s), round(r[1] * s), round(r[2] * s), round(r[3] * s))

    def _work_for(self, r_log: Rect) -> Rect:
        cx, cy = r_log[0] + r_log[2] // 2, r_log[1] + r_log[3] // 2
        mons = [(self._log(b), self._log(w)) for b, w in self._n.monitors()]
        for b, w in mons:
            if b[0] <= cx < b[0] + b[2] and b[1] <= cy < b[1] + b[3]:
                return w
        return self._log(self._n.monitor()[1])

    def _apply(self, r_log: Rect) -> None:
        self._n.normalize_state()
        self._n.set_bounds(self._phys(r_log))
        self._update_region()

    def _update_region(self) -> bool:
        """Chamfered region while normal; none while maximised/full screen."""
        want = self.region_enabled and not self.maximized and not self.fullscreen
        if want:
            _x, _y, w, h = self._n.bounds()
            if self.region_active and self._region_size == (w, h):
                return True
            ok = self._n.set_region(chamfer_polygon(w, h, self._n.scale() or 1.0))
            self.region_active = ok
            self._region_size = (w, h) if ok else None
            return ok
        if self.region_active:
            self._n.set_region(None)
            self.region_active = False
            self._region_size = None
        return True

    def enable_region(self, on: bool = True) -> bool:
        """Turn the native chamfered region on/off; False if the OS refused it."""
        with self._lock:
            self.region_enabled = on
            ok = self._update_region()
            if not ok:
                self.region_enabled = False
            return ok

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()
        if self._persist is not None:
            self._persist(self.snapshot())

    def _current_normal(self) -> Rect:
        return self._normal or self._log(self._n.bounds())

    # ------------------------------------------------------------ public
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            x, y, w, h = self._current_normal()
            return {"x": x, "y": y, "width": w, "height": h, "maximized": self.maximized
                    or (self.fullscreen and self._fs_was_max)}

    def get_bounds(self) -> dict[str, int]:
        x, y, w, h = self._log(self._n.bounds())
        return {"x": x, "y": y, "width": w, "height": h}

    def state(self) -> dict[str, Any]:
        with self._lock:
            b = self.get_bounds()
            mb, mw = (self._log(r) for r in self._n.monitor())
            return {
                "maximized": self.maximized, "fullscreen": self.fullscreen, **b,
                "min_width": self.min_size[0], "min_height": self.min_size[1],
                "native_resize": False, "native_region": self.region_active,
                "screen": {"x": mb[0], "y": mb[1], "width": mb[2], "height": mb[3],
                           "work_x": mw[0], "work_y": mw[1], "work_width": mw[2],
                           "work_height": mw[3]},
            }

    def set_bounds(self, x: int, y: int, width: int, height: int) -> dict[str, Any]:
        with self._lock:
            if self.maximized or self.fullscreen:
                self.maximized = self.fullscreen = self._fs_was_max = False
            r = (int(x), int(y), int(width), int(height))
            r = fit_rect(r, self._work_for(r), self.min_size)
            self._normal = r
            self._apply(r)
            self._changed()
            return self.state()

    def maximize(self) -> dict[str, Any]:
        with self._lock:
            if not self.maximized:
                native_max = self._n.is_zoomed() or self._n.is_minimized()
                if not self.fullscreen and not native_max:
                    self._normal = self._log(self._n.bounds())
                self.fullscreen = self._fs_was_max = False
                self.maximized = True
                self._apply(self._log(self._n.monitor()[1]))
                self._changed()
            return self.state()

    def _restore_normal(self) -> None:
        self.maximized = self.fullscreen = self._fs_was_max = False
        work = self._log(self._n.monitor()[1])
        normal = self._normal or centred(work, min_size=self.min_size)
        self._normal = fit_rect(normal, self._work_for(normal), self.min_size)
        self._apply(self._normal)
        self._changed()

    def restore(self) -> dict[str, Any]:
        with self._lock:
            if self.maximized or self.fullscreen:
                self._restore_normal()
            return self.state()

    def toggle_maximize(self) -> dict[str, Any]:
        return self.restore() if self.maximized else self.maximize()

    def set_fullscreen(self, on: bool) -> dict[str, Any]:
        with self._lock:
            if on and not self.fullscreen:
                if not self.maximized:
                    self._normal = self._log(self._n.bounds())
                self._fs_was_max = self.maximized
                self.maximized = False
                self.fullscreen = True
                self._apply(self._log(self._n.monitor()[0]))
                self._changed()
            elif not on and self.fullscreen:
                was_max = self._fs_was_max
                self.fullscreen = self._fs_was_max = False
                if was_max:
                    self.maximized = True
                    self._apply(self._log(self._n.monitor()[1]))
                    self._changed()
                else:
                    self._restore_normal()
            return self.state()

    def toggle_fullscreen(self) -> dict[str, Any]:
        return self.set_fullscreen(not self.fullscreen)

    def on_native_change(self) -> None:
        """The OS moved/resized the window (drag, snap, DPI change): resync and push."""
        with self._lock:
            if self._n.is_minimized() or self._n.is_zoomed():
                return  # iconic rect (-32000) / native maximise: never recorded
            cur = self._n.bounds()
            if self.fullscreen and not _close(cur, self._n.monitor()[0]):
                self.fullscreen = self._fs_was_max = False
            if self.maximized and not _close(cur, self._n.monitor()[1]):
                self.maximized = False
            if not self.maximized and not self.fullscreen:
                self._normal = self._log(cur)
            self._update_region()
            self._changed()


class Debouncer:
    """Call ``fn(latest_value)`` once, ``delay`` s after the last ``push``."""

    def __init__(self, fn: Callable[[Any], None], delay: float = 1.0) -> None:
        self._fn = fn
        self._delay = delay
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._value: Any = None

    def push(self, value: Any) -> None:
        with self._lock:
            self._value = value
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            t, self._timer = self._timer, None
            value, self._value = self._value, None
        if t is not None:
            t.cancel()
        if value is not None:
            self._fn(value)


class VirtualNative:
    """In-memory window on one 1920x1080 monitor (scale 1): the dev server's and
    headless Api's window, so window methods return plausible states without a GUI."""

    def __init__(self, bounds: Rect = (240, 90, 1440, 900),
                 monitor: tuple[Rect, Rect] = ((0, 0, 1920, 1080), (0, 0, 1920, 1040))) -> None:
        self.b = bounds
        self.mon = monitor

    def scale(self) -> float:
        return 1.0

    def bounds(self) -> Rect:
        return self.b

    def set_bounds(self, r: Rect) -> None:
        self.b = r

    def monitor(self) -> tuple[Rect, Rect]:
        return self.mon

    def monitors(self) -> list[tuple[Rect, Rect]]:
        return [self.mon]

    def normalize_state(self) -> None:
        return None

    def is_minimized(self) -> bool:
        return False

    def is_zoomed(self) -> bool:
        return False

    def set_region(self, points: list[tuple[int, int]] | None) -> bool:
        return True
