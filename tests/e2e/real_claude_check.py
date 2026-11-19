"""READ-ONLY check of the Adapter parser/collector against a REAL ~/.claude/projects.

    <venv>\\Scripts\\python tests\\e2e\\real_claude_check.py [--projects DIR] [--json-out F]

* Never writes into the Claude folder (files are opened ``rb`` only) and never sends
  anything anywhere: the Adapter's ``Collector`` fills a throw-away Outbox in a temp
  folder that is deleted at the end.
* The independent :mod:`ground_truth` reader then reads EXACTLY the bytes the
  Collector consumed per file (its saved offset, so a transcript that is still being
  written cannot skew the comparison) and both sides are compared message by message.
* Output is aggregates only: counts, token sums, per-model and per-day totals. No
  prompt text, no project names, no paths, no e-mail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import ground_truth as gt  # noqa: E402

FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_create_5m_tokens",
          "cache_create_1h_tokens")
GT_FIELDS = ("input", "output", "cache_read", "c5", "c1")


def source_id(path: Path) -> str:  # same rule as the collector (re-implemented)
    return hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=str(Path.home() / ".claude" / "projects"))
    ap.add_argument("--json-out")
    ap.add_argument("--tz", type=int, default=420)
    a = ap.parse_args(argv)

    from tokenvault.adapter.collector import Collector
    from tokenvault.adapter.identity import Identity
    from tokenvault.adapter.outbox import Outbox

    root = Path(a.projects)
    if not root.is_dir():
        print(f"no projects folder at {root}")
        return 2
    files = gt.jsonl_files(root)
    before = {f: (f.stat().st_size, f.stat().st_mtime_ns) for f in files}
    tmp = Path(tempfile.mkdtemp(prefix="tv-real-"))
    res: dict[str, Any] = {"files": len(files),
                           "bytes": sum(s for s, _ in before.values())}
    try:
        ob = Outbox(tmp / "outbox.db")
        col = Collector(ob, Identity(None, None, "local", "local", "local:local"), "local")
        t0 = time.monotonic()
        r = col.collect_all(root)
        res["collector_s"] = round(time.monotonic() - t0, 1)
        res["collector_skipped_oversize_lines"] = r.skipped_oversize
        res["collector_skipped_invalid_rows"] = r.skipped_invalid
        ob.close()
        con = sqlite3.connect(tmp / "outbox.db")
        rows = con.execute("SELECT message_id, payload FROM events").fetchall()
        offsets = dict(con.execute("SELECT source_id, offset FROM source_offsets").fetchall())
        con.close()
        adapter = {k: json.loads(p) for k, p in rows}
        limits = {f: int(offsets.get(source_id(f), 0)) for f in files}
        t0 = time.monotonic()
        truth, _titles, stats = gt.collect(files, limits)
        res["ground_truth_s"] = round(time.monotonic() - t0, 1)
        res["ground_truth_lines"] = stats["lines"]
        res["ground_truth_usage_lines"] = stats["usage_lines"]
    finally:
        for p in sorted(tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        tmp.rmdir()
    after = {f: (f.stat().st_size, f.stat().st_mtime_ns) for f in files if f.exists()}
    res["files_changed_during_run_by_claude"] = sum(1 for f in files if after.get(f) != before[f])

    # message-level comparison
    a_keys, t_keys = set(adapter), set(truth)
    res["messages_adapter"] = len(a_keys)
    res["messages_truth"] = len(t_keys)
    res["only_in_adapter"] = len(a_keys - t_keys)
    res["only_in_truth"] = len(t_keys - a_keys)
    tok_diff = 0
    meta_diff = 0
    meta_fields: dict[str, int] = defaultdict(int)
    for k in a_keys & t_keys:
        ev, m = adapter[k], truth[k]
        if tuple(ev[f] for f in FIELDS) != tuple(m.tok[f] for f in GT_FIELDS):
            tok_diff += 1
        diff = [n for n, x, y in (("model", ev["model"] or "", m.model),
                                  ("session", ev["session_id"], m.session),
                                  ("project", ev["project"], m.project),
                                  ("sidechain", ev["is_sidechain"], m.sidechain)) if x != y]
        if diff:
            meta_diff += 1
            for n in diff:
                meta_fields[n] += 1
    res["token_mismatches"] = tok_diff
    # A message id written in two files with DIFFERENT metadata (e.g. once as a sub-agent
    # line, once in the main file): this reader keeps the first line, the Outbox the last
    # copy it saw. Tokens are identical; only the attribution differs.
    res["metadata_mismatches"] = meta_diff
    res["metadata_mismatch_fields"] = dict(meta_fields)

    # aggregates
    tz = timedelta(minutes=a.tz)
    prices = gt.Prices(HERE.parents[1] / "data" / "pricing.json")

    def agg_truth() -> dict[str, Any]:
        tot = dict.fromkeys(GT_FIELDS, 0)
        by_model: dict[str, int] = defaultdict(int)
        by_day: dict[str, int] = defaultdict(int)
        sessions, side, cost, unknown = set(), 0, 0.0, set()
        for m in truth.values():
            n = sum(m.tok.values())
            for f in GT_FIELDS:
                tot[f] += m.tok[f]
            by_model[m.model or "(no model)"] += n
            by_day[(m.ts.astimezone(UTC) + tz).strftime("%Y-%m-%d")] += n
            sessions.add(m.session)
            side += n if m.sidechain else 0
            c = prices.cost(m.model, m.tok)
            if c is None:
                if n:
                    unknown.add(m.model or "(no model)")
            else:
                cost += float(c)
        return {"tokens": tot, "total": sum(tot.values()), "by_model": dict(by_model),
                "days": len(by_day), "by_day": dict(by_day), "sessions": len(sessions),
                "sidechain_tokens": side, "cost_usd": round(cost, 2),
                "unknown_price_models": sorted(unknown)}

    def agg_adapter() -> dict[str, Any]:
        from datetime import datetime

        from tokenvault.common.pricing import Pricing

        pr = Pricing.load(HERE.parents[1] / "data" / "pricing.json")
        tot = dict.fromkeys(FIELDS, 0)
        by_model: dict[str, int] = defaultdict(int)
        by_day: dict[str, int] = defaultdict(int)
        sessions, side, cost = set(), 0, 0.0
        for ev in adapter.values():
            toks = [ev[f] for f in FIELDS]
            n = sum(toks)
            for f in FIELDS:
                tot[f] += ev[f]
            by_model[ev["model"] or "(no model)"] += n
            ts = datetime.fromisoformat(ev["timestamp"])
            by_day[(ts.astimezone(UTC) + tz).strftime("%Y-%m-%d")] += n
            sessions.add(ev["session_id"])
            side += n if ev["is_sidechain"] else 0
            c = pr.cost(ev["model"], *toks)
            cost += float(c) if c is not None else 0.0
        return {"tokens": dict(zip(GT_FIELDS, tot.values(), strict=True)),
                "total": sum(tot.values()), "by_model": dict(by_model), "days": len(by_day),
                "by_day": dict(by_day), "sessions": len(sessions), "sidechain_tokens": side,
                "cost_usd": round(cost, 2)}

    ta, aa = agg_truth(), agg_adapter()
    res["truth"] = {k: v for k, v in ta.items() if k != "by_day"}
    res["adapter_equal_to_truth"] = {
        k: aa[k] == ta[k] for k in ("tokens", "total", "by_model", "by_day", "sessions",
                                    "sidechain_tokens", "cost_usd")}
    res["sidechain_tokens_delta"] = aa["sidechain_tokens"] - ta["sidechain_tokens"]
    # counts/tokens/cost must match exactly; session / sidechain attribution may differ
    # only for messages listed in metadata_mismatches
    res["PASS"] = (res["only_in_adapter"] == 0 and res["only_in_truth"] == 0
                   and tok_diff == 0
                   and all(v for k, v in res["adapter_equal_to_truth"].items()
                           if meta_diff == 0 or k not in ("sessions", "sidechain_tokens")))
    out = json.dumps(res, indent=2, ensure_ascii=False)
    print(out)
    if a.json_out:
        Path(a.json_out).write_text(out, encoding="utf-8")
    return 0 if res["PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
