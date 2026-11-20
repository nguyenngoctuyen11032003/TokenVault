"""TokenVault performance bench (NOT part of the default pytest run).

    <venv>\\Scripts\\python tests\\perf\\bench.py adapter [--messages 60000] [--idle-seconds 60]
    <venv>\\Scripts\\python tests\\perf\\bench.py manager [--events 2000000] [--clients 50]
    <venv>\\Scripts\\python tests\\perf\\bench.py ui      [--navigations 200]
    <venv>\\Scripts\\python tests\\perf\\bench.py all --label after

Every part runs the REAL code in separate processes (Manager back end =
``tests/e2e/manager_host.py``, Adapter = ``python -m tokenvault.adapter.main run``)
and measures them from outside with ``bench_util`` (CPU time, working set, private
bytes, context switches = wake-ups, bytes written). Results are printed and written
to ``tests/perf/results/<label>-<part>.json``; docs/performance.md quotes them.

The numbers are wall-clock measurements on a desktop shared with other work: repeat
a run before trusting a small difference.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "e2e"))
sys.path.insert(0, str(HERE.parents[1] / "src"))

RESULTS = HERE / "results"


def _write(label: str, part: str, data: dict[str, Any]) -> Path:
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{label}-{part}.json"
    meta = {"label": label, "part": part, "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cpus": os.cpu_count(), "python": sys.version.split()[0]}
    out.write_text(json.dumps({**meta, **data}, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def run_part(part: str, a: argparse.Namespace) -> None:
    work = Path(tempfile.mkdtemp(prefix=f"tv-bench-{part}-"))
    try:
        if part == "adapter":
            import bench_adapter

            data = bench_adapter.run(work, a)
        elif part == "manager":
            import bench_manager

            data = bench_manager.run(work, a)
        else:
            import bench_ui

            data = bench_ui.run(work, a)
        print(f"results: {_write(a.label, part, data)}", flush=True)
    finally:
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"work folder kept: {work}")


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("part", choices=("adapter", "manager", "ui", "all"))
    ap.add_argument("--label", default="run")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--idle-seconds", type=float, default=60)
    # adapter
    ap.add_argument("--messages", type=int, default=60_000)
    ap.add_argument("--files", type=int, default=120)
    ap.add_argument("--pad", type=int, default=3000, help="avg tool-output bytes per turn")
    ap.add_argument("--burst", type=int, default=5000)
    ap.add_argument("--latency-samples", type=int, default=15)
    ap.add_argument("--backfill-timeout", type=float, default=900)
    # manager
    ap.add_argument("--events", type=int, default=2_000_000)
    ap.add_argument("--employees", type=int, default=50)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--clients", type=int, default=50)
    ap.add_argument("--db-cache", default=str(Path(tempfile.gettempdir()) / "tv-bench-db"),
                    help="folder that keeps the generated big database between runs")
    # ui
    ap.add_argument("--navigations", type=int, default=200)
    ap.add_argument("--gui", action="store_true", help="also measure the real WebView2 window")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse(argv)
    parts = ("adapter", "manager", "ui") if a.part == "all" else (a.part,)
    for p in parts:
        print(f"\n== {p}", flush=True)
        run_part(p, a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
