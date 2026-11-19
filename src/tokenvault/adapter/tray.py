"""Tray icon: always present, menu = status line + "What is sent?" + "Open log folder"
+ "About" (Vietnamese by default, see strings.py). There is deliberately NO pause,
quit or hide entry. Windows opened from the menu run in a child process (app.py),
so the agent never loads tkinter.

pystray runs its own Win32 message loop on the tray thread (no polling). The
status line is re-read when the menu is rebuilt, which happens on state
callbacks and on new events, coalesced to at most once every 5 s by
:class:`Throttle`. pystray and Pillow are imported lazily so ``--no-tray`` runs
never load them.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tokenvault.adapter.strings import STRINGS, t

log = logging.getLogger("tokenvault.adapter.tray")

REFRESH_MIN_S = 5.0
_STATE_COLOR = {
    "connected": (225, 11, 31),  # #e10b1f
    "offline": (128, 128, 128),
    "rejected": (255, 140, 0),
    "connecting": (230, 190, 40),
    "not_configured": (255, 140, 0),
}


def state_label(state: str) -> str:
    key = f"state.{state}"
    return t(key) if key in STRINGS else state


def status_line(state: str, waiting: int) -> str:
    return t("tray.status", state=state_label(state), n=waiting)


@dataclass(frozen=True)
class MenuEntry:
    text: str | Callable[[], str]
    action: Callable[[], None] | None
    enabled: bool = True

    def label(self) -> str:
        return self.text() if callable(self.text) else self.text


def build_menu(
    status_text: Callable[[], str],
    on_what_is_sent: Callable[[], None],
    on_open_log: Callable[[], None],
    on_about: Callable[[], None],
) -> list[MenuEntry]:
    """The complete tray menu. Exactly four entries; the first is a disabled status line."""
    return [
        MenuEntry(status_text, None, enabled=False),
        MenuEntry(t("tray.what"), on_what_is_sent),
        MenuEntry(t("tray.open_log"), on_open_log),
        MenuEntry(t("tray.about"), on_about),
    ]


def to_pystray_menu(entries: list[MenuEntry]) -> Any:
    import pystray

    def item(e: MenuEntry) -> Any:
        action = e.action

        def run(icon: Any, it: Any) -> None:
            if action is not None:
                try:
                    action()
                except Exception:
                    log.exception("tray action failed")

        return pystray.MenuItem(lambda it: e.label(), run, enabled=e.enabled)

    return pystray.Menu(*[item(e) for e in entries])


def make_icon(state: str) -> Any:
    """64x64 icon drawn in code (no image files): dark tile, ring + dot in the state colour."""
    from PIL import Image, ImageDraw

    color = _STATE_COLOR.get(state, (128, 128, 128))
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 61, 61), radius=12, fill=(20, 20, 22, 255))
    d.ellipse((10, 10, 53, 53), outline=color + (255,), width=6)
    d.ellipse((24, 24, 39, 39), fill=color + (255,))
    return img


class Throttle:
    """Run ``fn`` now, or once later so it runs at most every ``interval`` seconds.

    No timer exists unless a call is pending, so an idle agent wakes nothing.
    """

    def __init__(self, fn: Callable[[], None], interval: float = REFRESH_MIN_S) -> None:
        self._fn = fn
        self._interval = interval
        self._lock = threading.Lock()
        self._last = float("-inf")
        self._timer: threading.Timer | None = None
        self._closed = False

    def __call__(self) -> None:
        with self._lock:
            if self._closed or self._timer is not None:
                return
            wait = self._last + self._interval - time.monotonic()
            if wait > 0:
                self._timer = threading.Timer(wait, self._fire)
                self._timer.name = "tv-throttle"
                self._timer.daemon = True
                self._timer.start()
                return
            self._last = time.monotonic()
        self._run()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
            if self._closed:
                return
            self._last = time.monotonic()
        self._run()

    def _run(self) -> None:
        try:
            self._fn()
        except Exception:
            log.exception("throttled callback failed")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            t, self._timer = self._timer, None
        if t is not None:
            t.cancel()
            if t is not threading.current_thread():
                t.join(5)


class Tray:
    def __init__(
        self,
        state: Callable[[], str],
        waiting: Callable[[], int],
        on_what_is_sent: Callable[[], None],
        on_open_log: Callable[[], None],
        on_about: Callable[[], None],
    ) -> None:
        self._state = state
        self._waiting = waiting
        self._entries = build_menu(self._status_text, on_what_is_sent, on_open_log, on_about)
        self._icon: Any = None
        self._thread: threading.Thread | None = None
        self._shown_state: str | None = None
        self._ready = threading.Event()
        self.failed = False  # the icon loop died on its own
        self._main_thread: bool | None = None  # tests; None = decide by OS
        self.refresh = Throttle(self._refresh_now)

    def _status_text(self) -> str:
        try:
            return status_line(self._state(), self._waiting())
        except Exception:
            log.exception("tray status failed")
            return t("tray.status_unavailable")

    def start(self) -> None:
        import pystray

        state = self._state()
        self._shown_state = state
        self._icon = pystray.Icon(
            "TokenVaultAdapter", make_icon(state),
            t("tray.tooltip", status=self._status_text()),
            menu=to_pystray_menu(self._entries),
        )
        if self.main_thread_mode:
            return  # macOS: pystray must own the main thread; the app calls run_blocking()
        self._thread = threading.Thread(target=self._run, name="tv-tray", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    @property
    def main_thread_mode(self) -> bool:
        return self._main_thread if self._main_thread is not None else sys.platform == "darwin"

    def run_blocking(self) -> None:
        """Run the icon on the CALLING (main) thread until :meth:`stop`."""
        if self._icon is not None:
            self._run()

    def _run(self) -> None:
        def setup(icon: Any) -> None:
            icon.visible = True
            self._ready.set()

        try:
            self._icon.run(setup=setup)
        except Exception:
            self.failed = True
            log.exception("tray icon failed")
        finally:
            self._ready.set()

    def _refresh_now(self) -> None:
        icon = self._icon
        if icon is None:
            return
        state = self._state()
        if state != self._shown_state:
            self._shown_state = state
            icon.icon = make_icon(state)
        icon.title = t("tray.tooltip", status=self._status_text())
        icon.update_menu()

    def stop(self) -> None:
        self.refresh.close()
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                log.exception("stopping tray icon failed")
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(5)
