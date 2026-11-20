"""CLI + Markdown rendering for the Release Security Report (see release_verify.py).

    python packaging/release/release_report.py --facts facts.json --pyproject pyproject.toml \
        --iss packaging/manager.iss --mode Development --out-dir release/v0.1.0 \
        [--artifact release/v0.1.0/TokenVault-Manager-Setup.exe ...] [--extra deps.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_verify as rv  # noqa: E402

UNSIGNED_TEXT = [
    "> **UNSIGNED / CHƯA KÝ SỐ** - first-party executables carry no Authenticode signature.",
    ">",
    "> * **Smart App Control ON** (Windows 11): the exe files and the Setup are **blocked** "
    "(error 4551 \"An Application Control policy has blocked this file\", no *Run anyway*). "
    "Use the *no-exe* portable package (PSF-signed python.exe) on those machines.",
    "> * **Smart App Control off**: SmartScreen shows *Windows protected your PC* for files "
    "carrying the Mark-of-the-Web; users must choose *More info -> Run anyway*.",
    "> * Managed PCs with App Control for Business / AppLocker: blocked unless IT allows the "
    "file hashes (or the path) - see docs/release-security.md.",
    "> * Microsoft Defender Antivirus still scans the files; signing does not change that.",
    "> * Fix: configure a signing credential (BUILD.md) and release with `-Mode Production`.",
]


def _signer(sig: dict[str, Any]) -> str:
    if sig.get("status") != "Valid":
        return ""
    return rv._cn(str(sig.get("signer_subject", "")))


def render_markdown(report: dict[str, Any], sums: list[str]) -> str:
    out = [f"# {report['title']} - TokenVault v{report['version']}", ""]
    out += [
        f"* Mode: **{report['mode']}**",
        f"* Overall: **{report['overall']}**",
        f"* Signed: **{'yes' if report['signed'] else 'NO - UNSIGNED'}**",
        f"* Generated (UTC): {report['generated']}",
        f"* Files scanned: {report['file_count']}, PE files: {len(report['pe'])}",
        "",
    ]
    if not report["signed"]:
        out += ["## UNSIGNED", ""] + UNSIGNED_TEXT + [""]
    out += ["## Checks", "", "| Result | Check | Summary |", "|---|---|---|"]
    for c in report["checks"]:
        out.append(f"| {c['status']} | `{c['id']}` | {c['message']} |")
    out.append("")
    for c in report["checks"]:
        if c["details"] and c["status"] != rv.PASS:
            out += [f"<details><summary>{c['id']} ({len(c['details'])})</summary>", ""]
            out += [f"- {d}" for d in c["details"]] + ["", "</details>", ""]
    if sums:
        out += ["## Artifacts (SHA-256)", "", "```"] + sums + ["```", ""]
    out += ["## PE inventory", "",
            "| File | Role | Signature | Signer | Timestamp | ProductVersion |",
            "|---|---|---|---|---|---|"]
    for p in report["pe"]:
        s = p["signature"]
        out.append(f"| `{p['path']}` | {p['role']} | {s.get('status', '?')} | {_signer(s)} | "
                   f"{'yes' if s.get('timestamped') else 'no'} | "
                   f"{p['version'].get('ProductVersion', '')} |")
    out += ["", "## Tools", ""]
    out += [f"* {k}: {v}" for k, v in sorted(report.get("tools", {}).items())]
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--facts", required=True)
    ap.add_argument("--pyproject", required=True)
    ap.add_argument("--iss", action="append", default=[])
    ap.add_argument("--policy", default=str(Path(__file__).with_name("signing.json")))
    ap.add_argument("--mode", choices=["Development", "Production"], default="Development")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--artifact", action="append", default=[])
    ap.add_argument("--extra", action="append", default=[])
    args = ap.parse_args(argv)

    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    report = rv.collect(args, policy)
    report["generated"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sums = rv.write_sums([Path(a) for a in args.artifact], out / "SHA256SUMS.txt") \
        if args.artifact else []
    report["artifacts"] = sums
    (out / "release-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "release-report.md").write_text(render_markdown(report, sums), encoding="utf-8")
    print(f"Release Security Report: {report['overall']} "
          f"({'signed' if report['signed'] else 'UNSIGNED'}, mode {report['mode']}) -> "
          f"{out / 'release-report.md'}")
    for c in report["checks"]:
        print(f"  {c['status']:4}  {c['id']}: {c['message']}")
    return 1 if report["overall"] == rv.FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
