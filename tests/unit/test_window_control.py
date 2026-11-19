"""Window geometry logic (maximise to work area, full screen, clamping, persistence)."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from tokenvault.manager.window_control import (
    Debouncer,
    Rect,
    WindowGeometry,
    centred,
    fit_rect,
    initial_geometry,
)

# Two monitors in PHYSICAL px at 125 %: primary 1920x1080 (taskbar 48 px), right 2560x1440.
PRIMARY = ((0, 0, 1920, 1080), (0, 0, 1920, 1032))
RIGHT = ((1920, 0, 2560, 1440), (1920, 0, 2560, 1400))


class FakeNative:
    def __init__(self, bounds: Rect = (100, 125, 1800, 900), scale: float = 1.25) -> None:
        self.b = bounds
        self.s = scale
        self.sets: list[Rect] = []
        self.normalized = 0

    def scale(self) -> float:
        return self.s

    def bounds(self) -> Rect:
        return self.b

    def set_bounds(self, r: Rect) -> None:
        self.sets.append(r)
        self.b = r

    def monitor(self) -> tuple[Rect, Rect]:
        cx = self.b[0] + self.b[2] // 2
        return RIGHT if cx >= 1920 else PRIMARY

    def monitors(self) -> list[tuple[Rect, Rect]]:
        return [PRIMARY, RIGHT]

    def normalize_state(self) -> None:
        self.normalized += 1


def make(**kw: Any) -> tuple[WindowGeometry, FakeNative, list[str], list[dict[str, Any]]]:
    n = FakeNative(**kw)
    pushes: list[str] = []
    saved: list[dict[str, Any]] = []
    g = WindowGeometry(n, on_change=lambda: pushes.append("x"), persist=saved.append)
    return g, n, pushes, saved


def test_state_is_logical_pixels() -> None:
    g, _, _, _ = make()
    st = g.state()
    assert (st["x"], st["y"], st["width"], st["height"]) == (80, 100, 1440, 720)
    assert st["screen"] == {"x": 0, "y": 0, "width": 1536, "height": 864, "work_x": 0,
                            "work_y": 0, "work_width": 1536, "work_height": 826}
    assert st["min_width"] == 1100 and st["min_height"] == 720
    assert st["maximized"] is False and st["fullscreen"] is False
    assert st["native_resize"] is False
    assert g.get_bounds() == {"x": 80, "y": 100, "width": 1440, "height": 720}


def test_maximize_uses_work_area_and_restore_returns() -> None:
    g, n, pushes, saved = make()
    st = g.maximize()
    assert n.b == (0, 0, 1920, 1032)  # never covers the taskbar
    assert st["maximized"] is True and (st["width"], st["height"]) == (1536, 826)
    assert saved[-1] == {"x": 80, "y": 100, "width": 1440, "height": 720, "maximized": True}
    st = g.toggle_maximize()
    assert st["maximized"] is False and n.b == (100, 125, 1800, 900)
    assert len(pushes) == 2 and saved[-1]["maximized"] is False
    assert n.normalized >= 2


def test_fullscreen_covers_monitor_and_returns_to_previous_state() -> None:
    g, n, _, saved = make()
    g.maximize()
    st = g.set_fullscreen(True)
    assert n.b == (0, 0, 1920, 1080) and st["fullscreen"] and not st["maximized"]
    assert saved[-1]["maximized"] is True  # snapshot never stores full screen
    st = g.toggle_fullscreen()
    assert st["maximized"] is True and n.b == (0, 0, 1920, 1032)
    g.restore()
    g.set_fullscreen(True)
    st = g.set_fullscreen(False)
    assert not st["fullscreen"] and not st["maximized"] and n.b == (100, 125, 1800, 900)
    assert g.set_fullscreen(False)["fullscreen"] is False  # idempotent


def test_set_bounds_clamps_and_leaves_maximize() -> None:
    g, n, _, saved = make()
    g.maximize()
    st = g.set_bounds(-50, 20, 500, 300)  # too small, partly off-screen
    assert st["maximized"] is False
    assert (st["width"], st["height"]) == (1100, 720) and st["x"] == 0 and st["y"] == 20
    st = g.set_bounds(900, 500, 1200, 800)  # would leave the work area at the bottom right
    assert st["x"] + st["width"] <= 1536 and st["y"] + st["height"] <= 826
    st = g.set_bounds(0, 0, 5000, 5000)
    assert (st["width"], st["height"]) == (1536, 826)
    assert saved[-1] == {"x": 0, "y": 0, "width": 1536, "height": 826, "maximized": False}


def test_set_bounds_on_second_monitor() -> None:
    g, n, _, _ = make()
    st = g.set_bounds(1700, 100, 1400, 900)  # centre on the right monitor (logical x >= 1536)
    assert st["screen"]["x"] == 1536 and st["x"] >= 1536
    assert n.b[0] >= 1920


def test_native_drag_out_of_maximized_clears_flag() -> None:
    g, n, pushes, _ = make()
    g.maximize()
    n.b = (300, 200, 1800, 1000)  # user dragged the title bar / snapped
    g.on_native_change()
    assert g.maximized is False and g.state()["x"] == 240
    assert g.snapshot()["x"] == 240
    n.b = (0, 0, 1920, 1032)
    g.maximized = True
    g.on_native_change()  # matching rect: stays maximised
    assert g.maximized is True
    assert len(pushes) == 3


def test_restore_without_saved_normal_centres() -> None:
    g, n, _, _ = make(bounds=(0, 0, 1920, 1032))
    g.maximized = True
    st = g.restore()
    assert (st["width"], st["height"]) == (1440, 826)


def test_fit_and_centred_helpers() -> None:
    assert fit_rect((10, 10, 100, 100), (0, 0, 1536, 826)) == (10, 10, 1100, 720)
    assert fit_rect((0, 0, 3000, 3000), (0, 0, 1000, 600)) == (0, 0, 1000, 600)
    assert centred((0, 0, 1536, 826)) == (48, 0, 1440, 826)
    assert centred((0, 0, 2560, 1400)) == (560, 250, 1440, 900)


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        (None, ((48, 0, 1440, 826), False)),
        ({"x": 100, "y": 50, "width": 1200, "height": 760, "maximized": True},
         ((100, 50, 1200, 760), True)),
        ({"x": 9000, "y": 50, "width": 1200, "height": 760, "maximized": False},
         ((48, 0, 1440, 826), False)),  # off-screen -> centred
        ({"x": 0, "y": 0, "width": 4000, "height": 3000}, ((48, 0, 1440, 826), False)),
        ({"x": "a"}, ((48, 0, 1440, 826), False)),
        ({"x": 1600, "y": 10, "width": 1500, "height": 900, "maximized": False},
         ((1600, 10, 1500, 900), False)),  # fits the second monitor
    ],
)
def test_initial_geometry_validates(saved: Any, expected: Any) -> None:
    works = [(0, 0, 1536, 826), (1536, 0, 2048, 1120)]
    assert initial_geometry(saved, works) == expected


def test_debouncer_keeps_last_value() -> None:
    got: list[int] = []
    done = threading.Event()

    def fn(v: int) -> None:
        got.append(v)
        done.set()

    d = Debouncer(fn, delay=0.05)
    for i in range(5):
        d.push(i)
    assert done.wait(2)
    time.sleep(0.1)
    assert got == [4]
    d.push(9)
    d.flush()
    assert got == [4, 9]
