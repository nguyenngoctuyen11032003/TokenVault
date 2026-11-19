"""Slow end-to-end run (real Manager + Adapter processes). ``pytest -m slow tests/e2e``.

Shortened timings (idle 10 s, burst 1000 lines); run ``tests/e2e/run_e2e.py`` by hand
for the full 60 s idle / 5000-line burst numbers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ground_truth as gt  # noqa: E402
import sample_data as sd  # noqa: E402


def test_ground_truth_reader_rules(tmp_path: Path) -> None:
    """Fast self-test of the independent reader on the generated sample data."""
    gen = sd.generate(tmp_path)
    alice = next(e for e in gen.employees if e.user == "alice")
    msgs, titles, stats = gt.collect(gt.jsonl_files(alice.projects_dir))
    assert stats["usage_lines"] > len(msgs) > 0  # multi-line responses were merged
    assert all("TVPRIV" not in m.project for m in msgs.values())
    assert titles and all(sd.MARK not in t for t in titles.values())
    carol = next(e for e in gen.employees if e.user == "carol")
    cmsgs, _, _ = gt.collect(gt.jsonl_files(carol.projects_dir))
    assert "msg_carol_partial" not in cmsgs  # partial last line is not counted yet
    dan = next(e for e in gen.employees if e.user == "dan")
    dmsgs, _, _ = gt.collect(gt.jsonl_files(dan.projects_dir))
    assert any(not k.startswith("msg_") for k in dmsgs)  # uuid fallback key


@pytest.mark.slow
def test_full_e2e(tmp_path: Path) -> None:
    import run_e2e

    work = tmp_path / "w"
    rc = run_e2e.main(["--work", str(work), "--idle-seconds", "10", "--burst", "1000",
                       "--latency-samples", "8", "--keep"])
    res = json.loads((work / "e2e-results.json").read_text(encoding="utf-8"))
    failed = {k: v["failures"] or v["error"] for k, v in res["steps"].items()
              if v["status"] != "PASS"}
    assert rc == 0 and not failed, failed
