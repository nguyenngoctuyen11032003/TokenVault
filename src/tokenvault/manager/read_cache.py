"""Short-lived cache for the UI's aggregate reads (Task 30).

Every page re-fetches on a live push, and several widgets of one page often ask the
same question (e.g. the home page's ``overview(today)``). A result is reused when the
store has not committed anything since (``PRAGMA data_version`` of the read
connection) AND it is at most ``ttl_s`` old (so clock-dependent fields such as
"online" or "today" are never staler than that). Values are the JSON-able results
the Api returns (plain dicts/lists), so a cached value is never mutated by callers.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

DEFAULT_TTL_S = 2.0
DEFAULT_SIZE = 64


class ReadCache:
    def __init__(self, revision: Callable[[], int], ttl_s: float = DEFAULT_TTL_S,
                 size: int = DEFAULT_SIZE, clock: Callable[[], float] = time.monotonic) -> None:
        self._revision = revision
        self.ttl_s = ttl_s
        self._size = size
        self._clock = clock
        self._lock = threading.Lock()
        self._items: OrderedDict[Hashable, tuple[int, float, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        if self.ttl_s <= 0:
            return compute()
        try:
            rev = self._revision()
        except Exception:  # noqa: BLE001 - store closed / unavailable: just compute
            return compute()
        now = self._clock()
        with self._lock:
            hit = self._items.get(key)
            if hit is not None and hit[0] == rev and now - hit[1] <= self.ttl_s:
                self._items.move_to_end(key)
                self.hits += 1
                return hit[2]
        value = compute()  # outside the lock: concurrent different reads run in parallel
        with self._lock:
            self.misses += 1
            self._items[key] = (rev, now, value)
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
