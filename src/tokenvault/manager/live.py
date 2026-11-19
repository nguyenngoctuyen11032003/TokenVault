"""Push to the UI: ``window.evaluate_js("tv.onLive(<json>)")``, at most N per second.

Also used for ``tv.onWindowState(<json>)`` (``fn="onWindowState"``, 10/s).

``notify()`` is cheap and thread-safe (called from the ingest server thread on
every stored batch and from the presence ticker). A dedicated pusher thread
coalesces notifications: the first one is pushed at once, later ones within the
interval are merged into ONE trailing push at the end of the interval, so the
last event is never lost and the UI never gets more than ``max_per_s`` calls.

The payload is produced by ``build_payload()`` at push time (always fresh) and
embedded with ``json.dumps(..., ensure_ascii=True)``: a JSON text is a valid JS
literal, so no raw data is ever concatenated into the script. After
:meth:`close` (window destroyed) ``window_eval`` is never called again.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


def live_script(payload: Any, fn: str = "onLive") -> str:
    """``window.tv.<fn>(<json>)``, a no-op until the UI has defined ``tv.<fn>``."""
    if not fn.isidentifier():
        raise ValueError(f"bad function name {fn!r}")
    literal = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return f"window.tv && window.tv.{fn} && window.tv.{fn}({literal})"


class LivePusher:
    def __init__(
        self,
        window_eval: Callable[[str], Any],
        build_payload: Callable[[], Any],
        max_per_s: float = 2,
        *,
        clock: Callable[[], float] = time.monotonic,
        fn: str = "onLive",
    ) -> None:
        live_script(None, fn)  # validates the name
        self._fn = fn
        self._eval = window_eval
        self._build = build_payload
        self.interval = 1.0 / max_per_s
        self._clock = clock
        self._cond = threading.Condition()
        self._dirty = False
        self._closed = False
        self._stopped = False
        self._last = float("-inf")
        self._thread: threading.Thread | None = None
        self._errors = 0
        self.pushed = 0

    # ------------------------------------------------------------ producers
    def notify(self) -> None:
        with self._cond:
            self._dirty = True
            self._cond.notify()

    def close(self) -> None:
        """The window is gone: never evaluate JS again."""
        with self._cond:
            self._closed = True
            self._cond.notify()

    # ---------------------------------------------------------------- core
    def pump(self) -> float | None:
        """Push if something is pending and the interval allows it.

        Returns the seconds to wait before a pending push becomes due, else None.
        """
        with self._cond:
            if self._closed or not self._dirty:
                return None
            wait = self._last + self.interval - self._clock()
            if wait > 0:
                return wait
            self._dirty = False
            self._last = self._clock()
        self._push()
        return None

    def _push(self) -> None:
        try:
            script = live_script(self._build(), self._fn)
        except Exception:  # noqa: BLE001 - never let a payload bug kill the pusher
            log.exception("live payload failed")
            return
        with self._cond:
            if self._closed:
                return
        try:
            self._eval(script)
            self.pushed += 1
            self._errors = 0
        except Exception as e:  # noqa: BLE001 - window may be closing / page reloading
            self._errors += 1
            if self._errors <= 3:
                log.warning("live push failed: %s", e)

    # -------------------------------------------------------------- thread
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="tv-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(2)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._stopped and not (self._dirty and not self._closed):
                    self._cond.wait()
                if self._stopped:
                    return
            wait = self.pump()
            if wait:
                with self._cond:
                    if not self._stopped:
                        self._cond.wait(wait)
