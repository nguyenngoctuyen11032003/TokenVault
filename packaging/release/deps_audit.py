"""Dependency audit for a release build (offline by default).

Run it with the build interpreter (the one PyInstaller used):

    .venv/Scripts/python packaging/release/deps_audit.py --pyproject pyproject.toml \
        --bundle-dir dist/manager/TokenVault-Manager/_internal \
        --warn-file build/pyi-manager/warn-TokenVault-Manager.txt --out deps.json

Checks: declared runtime dependencies installed (`pip list --format json`), `pip check`,
optional `pip-audit` (only with --online, never required), and what the PyInstaller bundle
contains: distributions outside the declared dependency closure and the heaviest binaries
(candidates for an exclude - only change the .spec after a rebuild + Manager --selftest).
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
# Packages PyInstaller itself / the runtime hooks put into every bundle.
BUILD_TOOLING = {"pyinstaller", "pyinstaller-hooks-contrib", "setuptools", "tokenvault", "pip"}


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def req_name(req: str) -> str:
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
    return canon(m.group(1)) if m else ""


def declared(pyproject: Path) -> list[str]:
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    return [req_name(r) for r in data["project"].get("dependencies", [])]


def closure(roots: list[str], requires: dict[str, list[str]]) -> set[str]:
    """Names reachable from roots, ignoring requirements that only apply to an extra."""
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        for r in requires.get(n, []):
            if "extra ==" in r.replace("'", '"').replace('extra=="', 'extra == "'):
                continue
            todo.append(req_name(r))
    return seen


def installed_requires() -> dict[str, list[str]]:
    return {canon(d.metadata["Name"]): list(d.requires or []) for d in md.distributions()}


def check_installed(decl: list[str], pip_list: list[dict[str, str]]) -> dict[str, Any]:
    have = {canon(p["name"]): p["version"] for p in pip_list}
    missing = [d for d in decl if d not in have]
    lines = [f"{d}=={have[d]}" for d in decl if d in have]
    if missing:
        return {"id": "deps.declared", "status": FAIL,
                "message": f"declared runtime dependencies missing: {', '.join(missing)}",
                "details": lines}
    return {"id": "deps.declared", "status": PASS,
            "message": f"all {len(decl)} declared runtime dependencies installed", "details": lines}


def bundle_entries(bundle: Path) -> list[tuple[str, int]]:
    """(top-level name, bytes) for every entry directly below PyInstaller's _internal dir."""
    out = []
    for p in sorted(bundle.iterdir()):
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() \
            else p.stat().st_size
        out.append((p.name, size))
    return out


def check_bundle(bundle: Path, allowed: set[str],
                 pkg_map: Mapping[str, list[str]]) -> list[dict[str, Any]]:
    entries = bundle_entries(bundle)
    extra = []
    for name, size in entries:
        if name.endswith(".dist-info"):
            continue
        dists = {canon(d) for d in pkg_map.get(name, [])}
        if dists and not dists & (allowed | BUILD_TOOLING):
            extra.append(f"{name} ({', '.join(sorted(dists))}, {size / 1e6:.2f} MB)")
    binaries = sorted(((f.stat().st_size, f.relative_to(bundle).as_posix())
                       for f in bundle.rglob("*")
                       if f.suffix.lower() in (".pyd", ".dll", ".jar")), reverse=True)[:10]
    heavy = [f"{rel} ({size / 1e6:.2f} MB)" for size, rel in binaries]
    total = sum(s for _, s in entries)
    return [
        {"id": "deps.bundle_extra", "status": WARN if extra else PASS,
         "message": "bundled packages outside the declared dependency closure", "details": extra},
        {"id": "deps.bundle_heaviest", "status": PASS,
         "message": f"bundle {total / 1e6:.1f} MB; 10 largest binaries listed for review",
         "details": heavy},
    ]


def check_warn_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = re.findall(r"^missing module named (\S+) - imported by (.*)$", text, re.M)
    top = sorted({m for m, by in missing if "(top-level)" in by and "." not in m})
    return {"id": "deps.pyinstaller_warnings", "status": PASS,
            "message": f"{len(missing)} unresolved optional imports in {path.name} "
                       "(normal for cross-platform packages)",
            "details": [f"top-level, unresolved: {', '.join(top[:40])}"] if top else []}


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pyproject", required=True)
    ap.add_argument("--bundle-dir", action="append", default=[])
    ap.add_argument("--warn-file", action="append", default=[])
    ap.add_argument("--online", action="store_true", help="also run pip-audit if installed")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    decl = declared(Path(args.pyproject))
    checks: list[dict[str, Any]] = []
    rc, out = run([sys.executable, "-m", "pip", "list", "--format", "json",
                   "--disable-pip-version-check"])
    pip_list = json.loads(out.splitlines()[0]) if rc == 0 and out.startswith("[") else []
    checks.append(check_installed(decl, pip_list) if pip_list else
                  {"id": "deps.declared", "status": FAIL, "message": "pip list failed",
                   "details": [out[:500]]})
    rc, out = run([sys.executable, "-m", "pip", "check", "--disable-pip-version-check"])
    checks.append({"id": "deps.pip_check", "status": PASS if rc == 0 else FAIL,
                   "message": "pip check: " + (out.splitlines()[0] if out else f"exit {rc}"),
                   "details": out.splitlines()[1:20]})
    if args.online and run([sys.executable, "-m", "pip_audit", "--version"])[0] == 0:
        rc, out = run([sys.executable, "-m", "pip_audit", "--local", "--progress-spinner", "off"])
        checks.append({"id": "deps.pip_audit", "status": PASS if rc == 0 else FAIL,
                       "message": "pip-audit (PyPI advisory DB)",
                       "details": out.splitlines()[-20:]})
    else:
        checks.append({"id": "deps.pip_audit", "status": WARN,
                       "message": "pip-audit not run (offline default; install pip-audit and "
                                  "pass -Online to enable)", "details": []})
    allowed = closure(decl, installed_requires())
    pkg_map = md.packages_distributions()
    for b in args.bundle_dir:
        if Path(b).is_dir():
            checks += check_bundle(Path(b), allowed, pkg_map)
    for w in args.warn_file:
        if Path(w).is_file():
            checks.append(check_warn_file(Path(w)))
    data = {"declared": decl, "installed": {canon(p["name"]): p["version"] for p in pip_list}}
    Path(args.out).write_text(json.dumps({"checks": checks, "data": data}, indent=2),
                              encoding="utf-8")
    for c in checks:
        print(f"  {c['status']:4}  {c['id']}: {c['message']}")
    return 1 if any(c["status"] == FAIL for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
