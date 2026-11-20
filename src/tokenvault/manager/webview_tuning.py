"""WebView2 resource hints while the Manager window is hidden to the tray (Task 30).

When the window is hidden or minimised the page has nothing to show, so WebView2 is
asked to use as little memory as possible:

* ``CoreWebView2.MemoryUsageTargetLevel = Low`` - WebView2 trims caches and moves
  renderer memory to disk where it can (WebView2 SDK 1.0.1210+);
* NOT ``TrySuspendAsync()``: measured, a suspended page never answers
  ``evaluate_js``, so any script call while hidden (window-state push, smoke probe)
  blocked its thread forever. With live pushes paused and no page timers the hidden
  renderer is idle anyway (~0 % CPU measured).

On show the target goes back to ``Normal``. Everything is best effort: an older
runtime, another pywebview backend or a closed window just means nothing happens.
pywebview internals used: ``window.native`` (the WinForms form) and its
``browser.webview`` (the WebView2 control).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("tokenvault.manager.webview_tuning")


def _core(window: Any) -> tuple[Any, Any] | None:
    form = getattr(window, "native", None)
    ctl = getattr(getattr(form, "browser", None), "webview", None)
    core = getattr(ctl, "CoreWebView2", None)
    return (form, core) if form is not None and core is not None else None


def _on_ui_thread(form: Any, fn: Any) -> None:
    try:
        from System import Action

        if getattr(form, "InvokeRequired", False):
            form.BeginInvoke(Action(fn))
        else:
            fn()
    except Exception:  # noqa: BLE001 - not WinForms / form closing
        log.debug("cannot dispatch to the UI thread", exc_info=True)


def set_background(window: Any, background: bool) -> bool:
    """Low memory + suspend when ``background``; normal memory otherwise."""
    found = _core(window)
    if found is None:
        return False
    form, core = found

    def apply() -> None:
        try:
            from Microsoft.Web.WebView2.Core import CoreWebView2MemoryUsageTargetLevel as Lvl

            core.MemoryUsageTargetLevel = Lvl.Low if background else Lvl.Normal
        except Exception:  # noqa: BLE001 - old runtime: property/method missing
            log.debug("WebView2 background hints unavailable", exc_info=True)

    _on_ui_thread(form, apply)
    return True
