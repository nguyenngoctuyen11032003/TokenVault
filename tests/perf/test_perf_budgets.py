"""Hard performance budgets (Task 30). Slow: ``pytest -m slow tests/perf``.

Smaller than the bench (300k events, 20 employees, 60 days) so it runs in about a
minute; the budgets are the ones docs/performance.md promises for the default pages.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from statistics import median

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "e2e"))

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def agg(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    import bench_manager

    from tokenvault.common.pricing import Pricing
    from tokenvault.manager.aggregates import Aggregates
    from tokenvault.manager.paths import pricing_path
    from tokenvault.manager.store import Store

    db = tmp_path_factory.mktemp("budget") / "m.db"
    bench_manager.build_db(db, 300_000, 20, 60)
    store = Store(db, tz_offset_minutes=bench_manager.TZ)
    yield Aggregates(store, Pricing.load(pricing_path()), bench_manager.TZ)
    store.close()


@pytest.mark.parametrize("call", ["overview", "employees"])
@pytest.mark.parametrize("rng", ["today", "7d"])
def test_default_pages_stay_within_150_ms(agg, call: str, rng: str) -> None:  # type: ignore[no-untyped-def]
    f = agg.ctx.range_filter(rng)
    fn = getattr(agg, call)
    fn(f)
    times = []
    for _ in range(7):
        t0 = time.perf_counter()
        fn(f)
        times.append(time.perf_counter() - t0)
    assert max(times) < 0.150, f"{call}/{rng}: median {median(times) * 1000:.0f} ms"


def test_backfill_budget_never_exceeds_its_share() -> None:
    from tokenvault.adapter.cpu_budget import CpuBudget

    b = CpuBudget(0.10, 2.0)
    t0, c0 = time.monotonic(), time.process_time()
    deadline = t0 + 4
    while time.monotonic() < deadline:  # busy "work" in 20 ms slices, paced
        end = time.perf_counter() + 0.02
        while time.perf_counter() < end:
            pass
        b.pace()
    used = (time.process_time() - c0) / (time.monotonic() - t0)
    assert used < 0.15, f"{used:.0%} of one core"
