"""watchdog observer that sleeps while nothing changes (Windows).

watchdog's Windows emitter reads change buffers with ``queue.get(timeout=T)`` in a
loop: T is BOTH the quiet gap that ends a batch and the idle poll period. The
agent needs T = 0.1 s for a low live latency (Task 23), which made the emitter wake
10 times a second forever, even when no transcript changed for hours.

:class:`QuietEmitter` first waits WITHOUT a timeout until the reader thread has
queued a buffer (or the emitter is stopped), then lets watchdog drain the batch with
the short gap as before. Idle cost: zero wake-ups. Latency: unchanged.

It relies on two watchdog internals (``emitter._reader`` and ``reader._buf_queue``,
a :class:`queue.Queue`); when they are missing (another watchdog version / OS) the
standard emitter behaviour is used unchanged.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

log = logging.getLogger("tokenvault.adapter.fswatch")

SAFETY_WAKE_S = 60.0  # re-check the stop flag at least this often (never needed in practice)
_emitter_cls: Any = None


def _quiet_emitter_class() -> Any:
    global _emitter_cls
    if _emitter_cls is not None:
        return _emitter_cls
    from watchdog.observers.read_directory_changes import WindowsApiEmitter

    class QuietEmitter(WindowsApiEmitter):
        def _buffers(self) -> Any:
            reader = getattr(self, "_reader", None)
            q = getattr(reader, "_buf_queue", None)
            return q if q is not None and hasattr(q, "not_empty") else None

        def queue_events(self, timeout: float) -> None:
            q = self._buffers()
            if q is not None:
                with q.not_empty:  # same lock Queue.put() notifies
                    q.not_empty.wait_for(
                        lambda: q._qsize() > 0 or self.stopped_event.is_set(),  # noqa: SLF001
                        SAFETY_WAKE_S)
                if self.stopped_event.is_set():
                    return
            super().queue_events(timeout)

        def on_thread_stop(self) -> None:
            q = self._buffers()
            super().on_thread_stop()
            if q is not None:
                with q.not_empty:
                    q.not_empty.notify_all()  # wake a waiter blocked above

    _emitter_cls = QuietEmitter
    return QuietEmitter


def make_observer(timeout: float) -> Any:
    """An observer with ``timeout`` as the batch gap; idle-silent on Windows."""
    if sys.platform == "win32":
        try:
            from watchdog.observers.api import BaseObserver

            return BaseObserver(_quiet_emitter_class(), timeout=timeout)
        except Exception:  # noqa: BLE001 - fall back to the stock observer
            log.warning("quiet observer unavailable; using the default", exc_info=True)
    from watchdog.observers import Observer

    return Observer(timeout=timeout)
