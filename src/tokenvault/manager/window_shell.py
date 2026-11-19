"""The pywebview window: frameless NitroSense shell, hide-to-tray, JS evaluation.

* Created with ``frameless=True, easy_drag=False`` (the UI marks its own drag
  regions), ``resizable=True``, ``min_size=(1100, 720)``, at the saved geometry
  (validated against the monitors; default centred 1440x900 fitted to the work area).
* Geometry (maximise/restore/full screen/bounds) lives in ``self.geometry``
  (:class:`window_control.WindowGeometry` over :class:`window_native.Win32Native`);
  OS moves/resizes resync it, every change calls ``on_window_state`` (the app pushes
  ``tv.onWindowState``) and the normal geometry is saved (debounced 1 s) in settings.
* Closing (Alt+F4, titlebar close -> ``Api.window_close``) only HIDES the window;
  the process keeps running in the tray. Only :meth:`quit` really closes it - and a
  Windows shutdown / log-off: the form's ``FormClosing`` reason (WindowsShutDown,
  TaskManagerClosing, ApplicationExitCall) and ``SystemEvents.SessionEnding`` are
  hooked through pythonnet so the close is never cancelled; ``on_session_end`` runs
  the normal quit path (geometry flushed, Manager stopped by the app).
* Window shape (:func:`decide_shape`):
  - ``opaque``: the user turned the chamfered window off (``transparent_window``
    false): rectangle, the UI draws the chamfer inside.
  - ``transparent``: WebView2 transparency; the UI clips itself. Verified once by
    :mod:`transparency_probe` the first time the window is visible and not
    maximised (two consecutive samples must agree); a failure switches to
    ``region`` at once and remembers it (``window_shape="region"``).
  - ``region``: an opaque window with a native Win32 window REGION matching the
    UI's chamfered clip-path (``WindowGeometry.enable_region``), removed while
    maximised / full screen. Used when transparency failed or was never verified
    (``transparent_pending`` still set at start). If the OS refuses the region the
    app falls back to ``opaque`` (``transparent_window=false``, page reload).
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
SESSION_END_REASONS = {"WindowsShutDown", "TaskManagerClosing", "ApplicationExitCall"}


def decide_shape(settings: dict[str, Any]) -> str:
    if not settings.get("transparent_window", True):
        return "opaque"
    if settings.get("window_shape") == "region" or settings.get("transparent_pending"):
        return "region"
    return "transparent"


def should_cancel_close(quitting: bool, session_ending: bool, reason: str | None) -> bool:
    """True = cancel the close and hide to the tray (only for a user close)."""
    if quitting or session_ending:
        return False
    return reason not in SESSION_END_REASONS


def probe_verdict(samples: list[bool | None]) -> bool | None:
    """True = transparency works, False = failed (all samples saw the sentinel)."""
    if len(samples) >= 2 and all(s is True for s in samples):
        return False
    if samples and all(s is False for s in samples):
        return True
    return None


class WindowShell:
    def __init__(self, settings: SettingsStore, url: str, js_api: Any, *,
                 hidden: bool = False, on_closed: Callable[[], None] | None = None) -> None:
        self._settings = settings
        self._url = url
        self._js_api = js_api
        self._hidden = hidden
        self.on_closed = on_closed
        self.on_session_end: Callable[[], None] | None = None
        self._window: Any = None
        self._quitting = False
        self._session_ending = False
        self._hooks_installed = False
        self._destroyed = False
        self.loaded = threading.Event()
        self.shape = "opaque"
        self.on_page_loaded: Callable[[], None] | None = None
        self.transparency_ok: bool | None = None  # result of the corner probe
        self.on_window_state: Callable[[], None] | None = None  # tv.onWindowState push
        self._persist = Debouncer(self._save_geometry, delay=1.0)
        self._start_maximized = False
        self._shown = False
        self._probe_state = "none"  # none | waiting | running | done
        self.geometry: WindowGeometry | None = None

    @property
    def transparent(self) -> bool:
        return self.shape == "transparent"

    # ----------------------------------------------------------- creation
    def create(self, shape: str | None = None) -> Any:
        import webview

        self.shape = shape or decide_shape(self._settings.get())
        try:
            win = self._make(webview, self.shape == "transparent")
        except Exception:
            if self.shape != "transparent":
                raise
            log.exception("transparent window failed; retrying with a window region")
            self.shape = "region"
            win = self._make(webview, False)
        if self.shape == "transparent":
            self._settings.update_internal(transparent_pending=True)
            self._probe_state = "waiting"
        self._window = win
        win.events.closing += self._on_closing
        win.events.closed += self._on_closed_event
        win.events.loaded += self._on_loaded
        win.events.shown += self._on_shown
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
            TITLE, self._url, js_api=self._js_api, x=x, y=y, width=width, height=height,
            resizable=True, min_size=MIN_SIZE, frameless=True, easy_drag=False,
            transparent=transparent, background_color="#000000", hidden=self._hidden,
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
        self._maybe_probe()

    def _save_geometry(self, snap: dict[str, Any]) -> None:
        try:
            self._settings.update_internal(window=snap)
        except Exception:  # noqa: BLE001 - geometry is a convenience
            log.warning("cannot save window geometry", exc_info=True)

    # ---------------------------------------------------- session ending
    def _hook_session_end(self) -> None:
        """FormClosing reason + SystemEvents.SessionEnding (pythonnet, WinForms only).
        Idempotent: ``shown`` may fire more than once."""
        if self._hooks_installed:
            return
        self._hooks_installed = True
        try:
            form = self._window.native
            form.FormClosing += lambda _s, args: self._on_form_closing(args)
        except Exception:  # noqa: BLE001 - not WinForms
            log.debug("FormClosing hook unavailable", exc_info=True)
        try:
            from Microsoft.Win32 import SystemEvents

            SystemEvents.SessionEnding += lambda _s, _a: self._begin_session_end()
        except Exception:  # noqa: BLE001
            log.debug("SessionEnding hook unavailable", exc_info=True)

    def _on_form_closing(self, args: Any) -> None:
        """Runs AFTER pywebview's handler (which cancels for our hide-to-tray)."""
        reason = str(getattr(args, "CloseReason", ""))
        if not should_cancel_close(self._quitting, self._session_ending, reason):
            args.Cancel = False
            if not self._quitting:
                self._begin_session_end()

    def _begin_session_end(self) -> None:
        if self._session_ending:
            return
        self._session_ending = True
        self._quitting = True
        log.info("Windows session ending: quitting")
        self._persist.flush()
        if self.on_session_end is not None:
            try:
                self.on_session_end()
            except Exception:  # noqa: BLE001
                log.exception("session-end handler failed")

    # ------------------------------------------------------------- events
    def _on_closing(self) -> bool:
        if not should_cancel_close(self._quitting, self._session_ending, None):
            return True
        self.hide()
        return False  # cancel: closing only hides to the tray

    def _on_closed_event(self) -> None:
        self._destroyed = True
        if self.on_closed is not None:
            self.on_closed()

    def _on_shown(self) -> None:
        self._hook_session_end()

    def _on_loaded(self) -> None:
        first = not self.loaded.is_set()
        self._shown = True
        geo = self.geometry
        if first and geo is not None:
            if self._start_maximized:
                geo.maximize()
            else:
                geo.on_native_change()
            if self.shape == "region":
                self._apply_region()
        self.ready()
        if self.on_page_loaded is not None:
            self.on_page_loaded()  # e.g. push the current live state at once
        self._maybe_probe()

    def _apply_region(self) -> None:
        geo = self.geometry
        if geo is None or geo.enable_region():
            return
        log.warning("window region refused: falling back to the opaque window")
        self.shape = "opaque"
        self._settings.update_internal(transparent_window=False, transparent_pending=False)
        self.evaluate("location.reload()")

    # -------------------------------------------------------- transparency
    def _maybe_probe(self) -> None:
        geo = self.geometry
        if (self._probe_state != "waiting" or self._hidden or not self._shown
                or self._destroyed or geo is None or geo.maximized or geo.fullscreen):
            return  # retried on the next show / restore
        self._probe_state = "running"
        threading.Thread(target=self._probe_transparency, name="tv-tprobe", daemon=True).start()

    def _probe_transparency(self) -> None:
        from tokenvault.manager import transparency_probe as tp

        if not tp.set_back_color(self._window, tp.SENTINEL):
            self._probe_state = "done"
            return
        samples: list[bool | None] = []
        for delay in (0.8, 0.6):  # two consecutive samples must agree
            time.sleep(delay)
            samples.append(tp.corner_shows(self._window, tp.SENTINEL))
        verdict = probe_verdict(samples)
        self.transparency_ok = verdict
        tp.set_back_color(self._window, tp.BLACK)
        self._probe_state = "done"
        if verdict is True:
            self._settings.update_internal(transparent_pending=False)
        elif verdict is False:
            log.warning("WebView2 transparency does not work here: using a window region")
            self.shape = "region"
            self._settings.update_internal(window_shape="region", transparent_pending=False)
            self._apply_region()
        # None (ambiguous): pending stays set -> the next start uses the region shape

    # ------------------------------------------- WindowControl (for Api)
    def ready(self) -> None:
        self.loaded.set()

    def minimize(self) -> None:
        if self._window is not None and not self._destroyed:
            self._window.minimize()

    def hide(self) -> None:
        if self._window is not None and not self._destroyed:
            self._window.hide()

    def show(self) -> None:
        if self._window is not None and not self._destroyed:
            self._window.show()
            self._hidden = False
            # A minimised window is always restored (SW_RESTORE returns it to its last
            # rect, i.e. the work area when maximised); a visible maximised one is kept.
            if self.geometry is None or not self.geometry.maximized or self._minimized():
                self._window.restore()
            self._maybe_probe()

    def _minimized(self) -> bool:
        geo = self.geometry
        try:
            return geo is not None and bool(geo._n.is_minimized())
        except Exception:  # noqa: BLE001 - native window not available
            return False

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
