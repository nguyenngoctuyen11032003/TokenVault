"""Release Security Report: evaluates the facts collected by verify-release.ps1.

Input  : a facts JSON (per-PE signature + version info, gathered with
         Get-AuthenticodeSignature / signtool verify on Windows), the scanned folders,
         the Inno Setup scripts and pyproject.toml.
Output : release-report.json + release-report.md (+ SHA256SUMS.txt for the artifacts);
         exit 1 when a check FAILs (Production turns unsigned first-party files into FAILs).

Pure standard library so it runs with any Python 3.11+.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import struct
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_ORDER = {PASS: 0, WARN: 1, FAIL: 2}


@dataclass
class Check:
    id: str
    status: str
    message: str
    details: list[str] = field(default_factory=list)


@dataclass
class PeEntry:
    path: str  # relative, forward slashes, prefixed with the scan root label
    role: str
    sha256: str
    size: int
    signature: dict[str, Any]
    version: dict[str, str]


# --------------------------------------------------------------------------- primitives
def is_pe(path: Path) -> bool:
    """True when the file has an MZ header pointing at a 'PE\\0\\0' signature."""
    try:
        with path.open("rb") as fh:
            head = fh.read(64)
            if len(head) < 64 or head[:2] != b"MZ":
                return False
            (offset,) = struct.unpack_from("<I", head, 0x3C)
            fh.seek(offset)
            return fh.read(4) == b"PE\0\0"
    except OSError:
        return False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_version(v: str) -> str:
    """'0.1.0.0' -> '0.1.0', '0, 1, 0, 0' -> '0.1.0'; keeps pre-release text."""
    parts = [p.strip() for p in re.split(r"[.,]", v.strip()) if p.strip()]
    while len(parts) > 3 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def role_of(name: str, policy: dict[str, Any]) -> str:
    low = name.lower()
    if any(fnmatch.fnmatch(low, p.lower()) for p in policy.get("unsigned_by_design", {})):
        return "unsigned_by_design"
    if any(fnmatch.fnmatch(low, p.lower()) for p in policy.get("first_party", [])):
        return "first_party"
    return "bundled"


def parse_signtool_verify(text: str) -> dict[str, Any]:
    """Facts from `signtool verify /pa /v` output (digest algorithm, timestamp, success)."""
    digest = re.search(r"Hash of file \((\w+)\)", text)
    ts = re.search(r"The signature is timestamped:\s*(.+)", text)
    return {
        "digest": digest.group(1).lower() if digest else "",
        "timestamp": ts.group(1).strip() if ts else "",
        "verified": "Successfully verified" in text,
    }


def parse_inno_setup(text: str) -> dict[str, str]:
    """[Setup] directives of an .iss file with simple #define substitutions resolved."""
    defines: dict[str, str] = {}
    for m in re.finditer(r'^\s*#define\s+(\w+)\s+"([^"]*)"', text, re.M):
        defines.setdefault(m.group(1), m.group(2))
    out: dict[str, str] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#", "//")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
            continue
        if section == "setup" and "=" in line:
            key, val = line.split("=", 1)
            val = re.sub(r"\{#(\w+)\}", lambda m: defines.get(m.group(1), m.group(0)), val.strip())
            out[key.strip()] = val
    return out


def pyproject_version(path: Path) -> str:
    with path.open("rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


# --------------------------------------------------------------------------- checks
def _sev(mode: str) -> str:
    return FAIL if mode == "Production" else WARN


def check_signatures(pes: list[PeEntry], mode: str, policy: dict[str, Any]) -> list[Check]:
    first = [p for p in pes if p.role == "first_party"]
    bundled = [p for p in pes if p.role == "bundled"]
    design = [p for p in pes if p.role == "unsigned_by_design"]
    checks: list[Check] = []

    unsigned = [p.path for p in first if p.signature.get("status") != "Valid"]
    broken = [f"{p.path}: {p.signature.get('status')}" for p in pes
              if p.signature.get("status") not in ("Valid", "NotSigned")]
    if broken:
        checks.append(Check("signature.broken", FAIL, "files with an invalid signature", broken))
    if not first:
        checks.append(Check("signature.first_party", WARN, "no first-party PE found"))
    elif unsigned:
        checks.append(Check("signature.first_party", _sev(mode),
                            f"{len(unsigned)}/{len(first)} first-party PE files are UNSIGNED "
                            "or not validly signed",
                            unsigned))
    else:
        checks.append(Check("signature.first_party", PASS,
                            f"all {len(first)} first-party PE files carry a valid signature"))

    weak = []
    for p in first:
        s = p.signature
        if s.get("status") != "Valid":
            continue
        if not s.get("timestamped"):
            weak.append(f"{p.path}: no RFC 3161 timestamp")
        if s.get("digest") and s["digest"] != "sha256":
            weak.append(f"{p.path}: digest {s['digest']}")
        if not s.get("digest"):
            weak.append(f"{p.path}: digest unknown (signtool not available)")
    if weak:
        checks.append(Check("signature.quality", _sev(mode), "signature details", weak))
    elif any(p.signature.get("status") == "Valid" for p in first):
        checks.append(Check("signature.quality", PASS,
                            "SHA-256 digest + timestamp on signed files"))

    no_vendor = [p.path for p in bundled if p.signature.get("status") == "NotSigned"]
    vendors = sorted({_cn(p.signature.get("signer_subject", "")) for p in bundled
                      if p.signature.get("status") == "Valid"})
    if no_vendor:
        msg = (f"{len(no_vendor)}/{len(bundled)} redistributed binaries have no signature "
               "(Smart App Control also checks DLL/.pyd loads)")
        checks.append(Check("signature.bundled", _sev(mode), msg, no_vendor))
    else:
        checks.append(Check("signature.bundled", PASS,
                            f"none of the {len(bundled)} redistributed binaries is unsigned",
                            [f"vendors: {', '.join(vendors)}"]))
    if design:
        reasons = policy.get("unsigned_by_design", {})
        names = {p.path.rsplit("/", 1)[-1].lower() for p in design}
        why = [v for k, v in reasons.items() if any(fnmatch.fnmatch(n, k.lower()) for n in names)]
        # a signature on these files would be broken by the payload appended later
        signed = [p.path for p in design if p.signature.get("status") != "NotSigned"]
        checks.append(Check("signature.unsigned_by_design", FAIL if signed else WARN,
                            f"{len(design)} file(s) stay unsigned by design",
                            [p.path for p in design] + why))
    return checks


def _cn(subject: str) -> str:
    m = re.search(r"CN=\"?([^,\"]+)", subject)
    return m.group(1) if m else subject


def check_versions(pes: list[PeEntry], expected: str, setups: dict[str, dict[str, str]],
                   publisher: str) -> list[Check]:
    checks: list[Check] = []
    bad: list[str] = []
    meta: list[str] = []
    for p in (x for x in pes if x.role in ("first_party", "unsigned_by_design")):
        v = {k: str(x).strip() for k, x in p.version.items()}  # Inno pads its strings
        pv = norm_version(v.get("ProductVersion", ""))
        if pv != expected:
            bad.append(f"{p.path}: ProductVersion {v.get('ProductVersion')!r} != {expected}")
        for key in ("CompanyName", "FileDescription", "ProductName"):
            if not v.get(key):
                meta.append(f"{p.path}: {key} empty")
        if v.get("CompanyName") and v["CompanyName"] != publisher:
            meta.append(f"{p.path}: CompanyName {v['CompanyName']!r} != {publisher!r}")
        if not v.get("LegalCopyright"):
            meta.append(f"{p.path}: LegalCopyright empty")
    for name, s in setups.items():
        if norm_version(s.get("AppVersion", "")) != expected:
            bad.append(f"{name}: AppVersion {s.get('AppVersion')!r} != {expected}")
    checks.append(Check("version.consistency", FAIL if bad else PASS,
                        "ProductVersion / AppVersion match pyproject " + expected, bad))
    checks.append(Check("version.metadata", WARN if meta else PASS,
                        "publisher metadata in version resources", meta))
    return checks


def check_inno(setups: dict[str, dict[str, str]], publisher: str,
               policy: dict[str, Any] | None = None) -> list[Check]:
    checks: list[Check] = []
    for name, s in setups.items():
        output = s.get("OutputBaseFilename", "") + ".exe"
        by_design = role_of(output, policy or {}) == "unsigned_by_design"
        errs, warns = [], []
        if not re.fullmatch(r"\{\{[0-9A-Fa-f-]{36}\}", s.get("AppId", "")):
            errs.append(f"AppId {s.get('AppId')!r} is not a fixed GUID")
        for key in ("AppName", "AppVersion", "AppPublisher", "OutputBaseFilename"):
            if not s.get(key):
                errs.append(f"{key} missing")
        if s.get("AppPublisher") and s["AppPublisher"] != publisher:
            warns.append(f"AppPublisher {s['AppPublisher']!r} != {publisher!r}")
        if s.get("PrivilegesRequired", "admin").lower() != "lowest":
            warns.append("PrivilegesRequired is not 'lowest' (per-user install expected)")
        for key in ("AppPublisherURL", "AppSupportURL", "VersionInfoCopyright"):
            if not s.get(key):
                warns.append(f"{key} not set (shown in Apps & Features / file properties)")
        if not s.get("SignTool") and not by_design:
            warns.append("no SignTool hook: Setup and uninstaller can never be signed by ISCC")
        status = FAIL if errs else (WARN if warns else PASS)
        checks.append(Check(f"inno.{name}", status, f"[Setup] of {name}", errs + warns))
    return checks


def check_contents(files: list[tuple[str, Path]], pe_facts: set[str],
                   policy: dict[str, Any]) -> list[Check]:
    pe_ext = {e.lower() for e in policy.get("pe_extensions", [])}
    scr_ext = {e.lower() for e in policy.get("script_extensions", [])}
    allowed = [a.lower() for a in policy.get("allowed_scripts", [])]
    hidden_pe, odd_scripts = [], []
    for rel, path in files:
        ext = path.suffix.lower()
        if ext not in pe_ext and is_pe(path):
            hidden_pe.append(f"{rel}: PE image with extension {ext or '(none)'}")
        elif ext in pe_ext and rel not in pe_facts:
            hidden_pe.append(f"{rel}: PE file not inventoried")
        if ext in scr_ext and not any(fnmatch.fnmatch(rel.lower(), a) for a in allowed):
            odd_scripts.append(rel)
    return [
        Check("content.executables", FAIL if hidden_pe else PASS,
              "no disguised or un-inventoried executables", hidden_pe),
        Check("content.scripts", WARN if odd_scripts else PASS,
              "scripts/launchers outside the allow-list", odd_scripts),
    ]


# --------------------------------------------------------------------------- driver
def overall(checks: list[Check]) -> str:
    return max((c.status for c in checks), key=lambda s: _ORDER[s], default=PASS)


def write_sums(artifacts: list[Path], out: Path) -> list[str]:
    lines = [f"{sha256_file(a)} *{a.name}" for a in sorted(artifacts, key=lambda p: p.name)]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return lines


def collect(args: argparse.Namespace, policy: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = json.loads(Path(args.facts).read_text(encoding="utf-8-sig"))
    by_rel = {f["path"]: f for f in facts.get("files", [])}
    files: list[tuple[str, Path]] = []
    for root in facts.get("roots", []):
        base = Path(root["path"])
        for p in sorted(x for x in base.rglob("*") if x.is_file()):
            files.append((f"{root['label']}/{p.relative_to(base).as_posix()}", p))
    pes: list[PeEntry] = []
    for rel, path in files:
        f = by_rel.get(rel)
        if f is None:
            continue
        sig = dict(f.get("signature", {}))
        sig.update({k: v for k, v in parse_signtool_verify(f.get("signtool", "")).items() if v})
        pes.append(PeEntry(rel, role_of(path.name, policy), sha256_file(path), path.stat().st_size,
                           sig, f.get("version", {})))
    setups = {Path(i).name: parse_inno_setup(Path(i).read_text(encoding="utf-8-sig"))
              for i in args.iss}
    expected = pyproject_version(Path(args.pyproject))
    publisher = str(policy.get("expected_publisher", ""))
    checks = check_signatures(pes, args.mode, policy)
    checks += check_versions(pes, expected, setups, publisher)
    checks += check_inno(setups, publisher, policy)
    checks += check_contents(files, set(by_rel), policy)
    for extra in args.extra or []:
        data = json.loads(Path(extra).read_text(encoding="utf-8-sig"))
        checks += [Check(**c) for c in data.get("checks", [])]
    first = [p for p in pes if p.role == "first_party"]
    signed = bool(first) and all(p.signature.get("status") == "Valid" for p in first)
    return {
        "title": "Release Security Report", "mode": args.mode, "version": expected,
        "signed": signed, "overall": overall(checks), "tools": facts.get("tools", {}),
        "checks": [asdict(c) for c in checks], "pe": [asdict(p) for p in pes],
        "file_count": len(files),
    }
