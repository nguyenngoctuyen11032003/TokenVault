"""Tray icon (pystray) for the Manager: Open / Create employee installer / Quit.

The icon is the TokenVault logo (common/brand.py); when the file is missing a
chamfered red plate with a white "T" is drawn with Pillow instead. pystray's Win32 backend
runs its message loop in
its own thread, next to pywebview's GUI loop on the main thread.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PIL import Image, ImageDraw

from tokenvault.common import brand

log = logging.getLogger(__name__)

TITLE = "TokenVault Manager"
LABEL_OPEN = "Mở TokenVault"
LABEL_INSTALLER = "Tạo bộ cài nhân viên"
LABEL_QUIT = "Thoát"


def icon_image(size: int = 64) -> Image.Image:
    logo: Image.Image | None = brand.tray_image(size)
    return logo if logo is not None else _drawn_icon(size)


def _drawn_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size // 5
    s = size - 1
    d.polygon([(c, 0), (s, 0), (s, s - c), (s - c, s), (0, s), (0, c)], fill=(225, 11, 31, 255))
    w = max(2, size // 8)
    d.rectangle([size * 0.25, size * 0.22, size * 0.75, size * 0.22 + w], fill="white")
    d.rectangle([size * 0.5 - w / 2, size * 0.22, size * 0.5 + w / 2, size * 0.8], fill="white")
    return img


class Tray:
    def __init__(
        self,
        on_open: Callable[[], None],
        on_installer: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._on_open = on_open
        self._on_installer = on_installer
        self._on_quit = on_quit
        self._icon: Any = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def _safe(fn: Callable[[], None]) -> Callable[..., None]:
        def run(*_args: Any) -> None:
            try:
                fn()
            except Exception:  # noqa: BLE001 - a menu action must not kill the tray
                log.exception("tray action failed")

        return run

    def start(self) -> bool:
        try:
            import pystray
        except Exception as e:  # noqa: BLE001 - no tray available: the app still runs
            log.warning("tray icon unavailable: %s", e)
            return False
        menu = pystray.Menu(
            pystray.MenuItem(LABEL_OPEN, self._safe(self._on_open), default=True),
            pystray.MenuItem(LABEL_INSTALLER, self._safe(self._on_installer)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(LABEL_QUIT, self._safe(self._on_quit)),
        )
        self._icon = pystray.Icon("tokenvault-manager", icon_image(), TITLE, menu)
        self._thread = threading.Thread(target=self._icon.run, name="tv-tray", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                log.debug("tray stop failed", exc_info=True)
