"""Heuristic check that the transparent window really is transparent.

With some WebView2 / pywebview / GPU combinations a "transparent" frameless
window paints its WinForms background colour where the page is transparent, so
the chamfered corners show a solid (light grey by default) triangle. To detect
it, the form's background is set to a sentinel colour that no desktop is likely
to show, and a few screen pixels inside the top-left chamfer (the UI cuts 30 CSS
px there) are sampled: if they all show the sentinel, transparency failed.

Only works with pywebview's WinForms backend (``window.native`` is the form);
anything unexpected returns ``None`` ("cannot tell") and nothing changes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

SENTINEL = (1, 2, 3)
BLACK = (0, 0, 0)
_PATCH = (3, 3, 9, 9)  # pixels inside the top-left corner of the window


def _invoke(form: Any, fn: Callable[[], None]) -> None:
    import System  # pythonnet, present with pywebview's WinForms backend

    form.Invoke(System.Action(fn))


def set_back_color(window: Any, rgb: tuple[int, int, int]) -> bool:
    try:
        from System.Drawing import Color

        form = window.native

        def apply() -> None:
            form.BackColor = Color.FromArgb(255, *rgb)

        _invoke(form, apply)
        return True
    except Exception as e:  # noqa: BLE001 - not WinForms / window gone
        log.debug("cannot set window back colour: %s", e)
        return False


def window_bounds(window: Any) -> tuple[int, int, int, int] | None:
    out: list[tuple[int, int, int, int]] = []
    try:
        form = window.native

        def read() -> None:
            b = form.Bounds
            out.append((int(b.X), int(b.Y), int(b.Width), int(b.Height)))

        _invoke(form, read)
    except Exception as e:  # noqa: BLE001
        log.debug("cannot read window bounds: %s", e)
        return None
    return out[0] if out else None


def patch_is_colour(pixels: list[tuple[int, ...]], rgb: tuple[int, int, int],
                    tolerance: int = 2) -> bool:
    return bool(pixels) and all(
        all(abs(p[i] - rgb[i]) <= tolerance for i in range(3)) for p in pixels
    )


def corner_shows(window: Any, rgb: tuple[int, int, int] = SENTINEL,
                 grab: Callable[..., Any] | None = None) -> bool | None:
    b = window_bounds(window)
    if b is None or b[2] < 50 or b[3] < 50:
        return None
    x, y = b[0], b[1]
    try:
        if grab is None:
            from PIL import ImageGrab

            grab = ImageGrab.grab
        x0, y0, x1, y1 = _PATCH
        img = grab(bbox=(x + x0, y + y0, x + x1, y + y1), all_screens=True).convert("RGB")
        pixels = list(img.getdata())
        log.debug("transparency probe at %s: %s", (x, y), pixels[:3])
    except Exception as e:  # noqa: BLE001 - no screen access (locked session, service)
        log.debug("screen grab failed: %s", e)
        return None
    return patch_is_colour(pixels, rgb)
