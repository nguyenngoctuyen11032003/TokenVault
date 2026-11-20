"""CPU budget for BACKGROUND work (history backfill, draining a queue backlog).

The agent must stay invisible on the employee's machine, so catch-up work is paced:
over any window of ``window_s`` seconds the WHOLE process (every thread) may use at
most ``fraction`` of one core while paced work runs. Callers ask :meth:`delay` how
long to rest after a piece of work (or :meth:`pace` to rest on a stop event).

Live work (a response just written) is never paced: only backfill chunks and full
transport batches (a backlog) call in. Process CPU time comes from
``time.process_time`` (GetProcessTimes on Windows, ~16 ms resolution), which is
fine for chunks of tens of milliseconds.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

DEFAULT_FRACTION = 0.10  # of one core, averaged over the window
DEFAULT_WINDOW_S = 5.0


class CpuBudget:
    def __init__(
        self,
        fraction: float = DEFAULT_FRACTION,
        window_s: float = DEFAULT_WINDOW_S,
        *,
        clock: Callable[[], float] = time.monotonic,
        cpu: Callable[[], float] = time.process_time,
    ) -> None:
        if not 0 < fraction <= 1:
            raise ValueError("fraction must be in (0, 1]")
        self.fraction = fraction
        self.window_s = window_s
        self._clock = clock
        self._cpu = cpu
        self._lock = threading.Lock()
        self._t0: float | None = None  # window start; set by start() or the first delay()
        self._c0 = 0.0
        self.rested_s = 0.0  # total pause handed out (diagnostics/tests)

    def start(self) -> None:
        """Open a window now (paced work begins; earlier CPU, e.g. start-up, is not counted)."""
        with self._lock:
            self._t0, self._c0 = self._clock(), self._cpu()

    def delay(self) -> float:
        """Seconds to rest now so the window average stays within the budget."""
        with self._lock:
            now, used = self._clock(), self._cpu()
            if self._t0 is None:
                self._t0, self._c0 = now, used
                return 0.0
            wall = now - self._t0
            spent = used - self._c0
            need = spent / self.fraction - wall
            if wall >= self.window_s and need <= 0:
                # Start a new window. Unused budget is NOT carried over, so a long idle
                # period cannot be "saved up" for a burst.
                self._t0, self._c0 = now, used
            return max(0.0, need)

    def pace(self, stop: threading.Event | None = None) -> bool:
        """Rest as :meth:`delay` says. Returns True when ``stop`` was set meanwhile."""
        d = self.delay()
        if d <= 0:
            return bool(stop is not None and stop.is_set())
        with self._lock:
            self.rested_s += d
        if stop is not None:
            return stop.wait(d)
        time.sleep(d)
        return False
