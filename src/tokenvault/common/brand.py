"""Brand assets (logo) shipped inside the package: ``tokenvault/ui/assets/logo``.

``assets/logo`` in the repo is the source of truth; the package copy is kept identical
by a test. Works from source, from a PyInstaller bundle (``_MEIPASS``) and from the
portable runtime (package folder). Every lookup returns ``None`` when the file is
missing, so callers can fall back to something drawn in code.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SMALL_MAX = 48  # sizes up to here use the simplified mark
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def logo_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "tokenvault" / "ui" / "assets" / "logo"
    return Path(__file__).resolve().parents[1] / "ui" / "assets" / "logo"


def _file(name: str) -> Path | None:
    p = logo_dir() / name
    return p if p.is_file() else None


def logo_png(size: int) -> Path | None:
    """The pre-rendered PNG whose size is the smallest one >= ``size`` (largest if none)."""
    pick = next((s for s in SIZES if s >= size), SIZES[-1])
    return _file(f"tokenvault-{pick}.png")


def icon_ico() -> Path | None:
    return _file("tokenvault.ico")


def wordmark_png() -> Path | None:
    return _file("tokenvault-wordmark.png")


def tray_image(size: int = 64, dot: tuple[int, int, int] | None = None) -> Any | None:
    """RGBA tray icon: the logo at ``size`` px, with a state dot in the bottom-right corner.

    ``dot`` is an RGB colour (None = no indicator). Returns None when the logo file is
    missing or unreadable, so the caller can draw its own fallback. Pillow is imported
    here, not at module load (the agent without a tray never loads it).
    """
    path = logo_png(size)
    if path is None:
        return None
    try:
        from PIL import Image, ImageDraw

        with Image.open(path) as src:
            img = src.convert("RGBA")
        if img.size != (size, size):
            img = img.resize((size, size), Image.Resampling.LANCZOS)
    except Exception:  # noqa: BLE001 - corrupt file: fall back to the drawn icon
        return None
    if dot is not None:
        d = ImageDraw.Draw(img)
        r = max(3, round(size * 0.20))  # dot radius
        ring = max(1, round(size / 32))  # dark outline keeps it visible on any taskbar
        cx = cy = size - r - ring
        d.ellipse((cx - r - ring, cy - r - ring, cx + r + ring, cy + r + ring),
                  fill=(10, 15, 32, 255))
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*dot, 255))
    return img
