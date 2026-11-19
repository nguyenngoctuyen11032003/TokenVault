"""The pywebview window: frameless NitroSense shell, hide-to-tray, JS evaluation.

* Created with ``frameless=True, easy_drag=False`` (the UI marks its own drag
  regions), ``resizable=True``, ``min_size=(1100, 720)``, at the saved geometry
  (validated against the monitors; default centred 1440x900 fitted to the work area),
  ``transparent`` from settings (``background_color='#000000'``).
* Geometry (maximise/restore/full screen/bounds) lives in ``self.geometry``
  (:class:`window_control.WindowGeometry` over :class:`window_native.Win32Native`);
  OS moves/resizes resync it, every change calls ``on_window_state`` (the app pushes
  ``tv.onWindowState``) and the normal geometry is saved (debounced 1 s) in settings.
* Closing (Alt+F4, titlebar close -> ``Api.window_close``) only HIDES the window;
  the process keeps running in the tray. Only :meth:`quit` really closes it.
* Transparency fallback: before a transparent window is shown the shell sets
  ``transparent_pending``; the page's ``loaded`` event (or ``Api.window_ready``)
  clears it. If the flag is still set at the next start, the previous
  transparent window never finished loading, so the app starts opaque
  (``transparent_window=false``). A window that fails to be created transparent
  is retried opaque at once.
* :meth:`evaluate` is the live-push sink; it never touches a destroyed window.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from tokenvault.manager.settings_store import SettingsStore
from tokenvault.manager.window_control import (
    MIN_SIZE,
    Debouncer,
    Rect,
    WindowGeometry,
    initial_geometry,
)
from tokenvault.manager.window_native import Win32Native, all_monitors

log = logging.getLogger(__name__)

TITLE = "TokenVault Manager"


class WindowShell:
    def __init__(self, settings: SettingsStore, url: str, js_api: Any, *,
                 hidden: bool = False, on_closed: Callable[[], None] | None = None) -> None:
        self._settings = settings
        self._url = url
        self._js_api = js_api
        self._hidden = hidden
        self.on_closed = on_closed
        self._window: Any = None
        self._quitting = False
        self._destroyed = False
        self.loaded = threading.Event()
        self.transparent = False
        self.on_page_loaded: Callable[[], None] | None = None
        self.transparency_ok: bool | None = None  # result of the corner probe
        self.on_window_state: Callable[[], None] | None = None  # tv.onWindowState push
        self._persist = Debouncer(self._save_geometry, delay=1.0)
        self._start_maximized = False
        self._shown = False
        self.geometry: WindowGeometry | None = None

    # ----------------------------------------------------------- creation
    def create(self, transparent: bool) -> Any:
        import webview

        try:
            win = self._make(webview, transparent)
        except Exception:
            if not transparent:
                raise
            log.exception("transparent window failed; retrying opaque")
            self._settings.update_internal(transparent_window=False, transparent_pending=False)
            transparent = False
            win = self._make(webview, False)
        self.transparent = transparent
        if transparent:
            self._settings.update_internal(transparent_pending=True)
        self._window = win
        win.events.closing += self._on_closing
        win.events.closed += self._on_closed_event
        win.events.loaded += self._on_loaded
        self._attach_geometry(win)
        return win

    def _initial(self) -> tuple[Rect, bool]:
        """Saved geometry validated against the monitors (logical px before start)."""
        try:
            works = [w for _b, w in all_monitors()]
        except Exception:  # noqa: BLE001 - no monitor info: centred default
            works = []
        if not works:
            works = [(0, 0, 1536, 824)]
        return initial_geometry(self._settings.get()["window"], works)

    def _make(self, webview: Any, transparent: bool) -> Any:
        (x, y, width, height), self._start_maximized = self._initial()
        return webview.create_window(
            TITLE,
            self._url,
            js_api=self._js_api,
            x=x,
            y=y,
            width=width,
            height=height,
            resizable=True,
            min_size=MIN_SIZE,
            frameless=True,
            easy_drag=False,
            transparent=transparent,
            background_color="#000000",
            hidden=self._hidden,
        )

    def _attach_geometry(self, win: Any) -> None:
        geo = WindowGeometry(Win32Native(win), on_change=self._geo_changed,
                             persist=self._persist.push)
        self.geometry = geo

        def native_change() -> None:
            if self._shown and not self._destroyed:
                try:
                    geo.on_native_change()
                except Exception:  # noqa: BLE001 - never break the GUI on a sync glitch
                    log.debug("geometry sync failed", exc_info=True)

        def native_maximized() -> None:  # Win+Up etc.: maximise to the WORK AREA instead
            if self._shown and not self._destroyed and not geo.maximized:
                geo.maximize()

        win.events.resized += native_change
        win.events.moved += native_change
        win.events.maximized += native_maximized

    def _geo_changed(self) -> None:
        if self.on_window_state is not None:
            self.on_window_state()

    def _save_geometry(self, snap: dict[str, Any]) -> None:
        try:
            self._settings.update_internal(window=snap)
        except Exception:  # noqa: BLE001 - geometry is a convenience
            log.warning("cannot save window geometry", exc_info=True)

    # ------------------------------------------------------------- events
    def _on_closing(self) -> bool:
        if self._quitting:
            return True
        self.hide()
        return False  # cancel: closing only hides to the tray

    def _on_closed_event(self) -> None:
        self._destroyed = True
        if self.on_closed is not None:
            self.on_closed()

    def _on_loaded(self) -> None:
        first = not self.loaded.is_set()
        self._shown = True
        if first and self._start_maximized and self.geometry is not None:
            self.geometry.maximize()
        self.ready()
        if self.on_page_loaded is not None:
            self.on_page_loaded()  # e.g. push the current live state at once
        if first and self.transparent and not self._hidden:
            threading.Thread(target=self._probe_transparency, name="tv-tprobe",
                             daemon=True).start()

    def _probe_transparency(self) -> None:
        """Opaque fallback NOW (and for later starts) when the corners are not see-through."""
        from tokenvault.manager import transparency_probe as tp

        if not tp.set_back_color(self._window, tp.SENTINEL):
            return
        results = []
        for delay in (0.8, 1.2):  # twice: the window may still be moving / repainting
            time.sleep(delay)
            results.append(tp.corner_shows(self._window, tp.SENTINEL))
        failed = True if True in results else (None if None in results else False)
        self.transparency_ok = None if failed is None else not failed
        if failed:
            log.warning("window transparency does not work here: switching to opaque")
            self._settings.update_internal(transparent_window=False, transparent_pending=False)
            self.transparent = False
            tp.set_back_color(self._window, tp.BLACK)
            self.evaluate("location.reload()")  # the UI re-reads settings: opaque shell
        else:
            tp.set_back_color(self._window, tp.BLACK)  # harmless if transparency works

    # ------------------------------------------- WindowControl (for Api)
    def ready(self) -> None:
        if not self.loaded.is_set():
            self.loaded.set()
            if self.transparent:
                self._settings.update_internal(transparent_pending=False)

    def minimize(self) -> None:
        if self._window is not None and not self._destroyed:
            self._window.minimize()

    def hide(self) -> None:
        if self._window is not None and not self._destroyed:
            self._window.hide()

    def show(self) -> None:
        if self._window is not None and not self._destroyed:
            self._window.show()
            self._window.restore()

    def navigate(self, route: str) -> None:
        self.evaluate(f"location.hash = {json.dumps('#/' + route)}")

    def evaluate(self, script: str) -> Any:
        if self._window is None or self._destroyed:
            return None
        return self._window.evaluate_js(script)

    def quit(self) -> None:
        self._quitting = True
        self._persist.flush()
        if self._window is not None and not self._destroyed:
            self._window.destroy()
