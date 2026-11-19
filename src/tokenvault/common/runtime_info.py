"""``--selftest`` diagnostics: does this process run under a signed interpreter?

The "no-exe" portable packages run on the official embeddable Python, whose
python.exe / pythonw.exe are Authenticode-signed by the Python Software
Foundation (accepted by Application Control / Smart App Control publisher
rules). ``runtime_line()`` reports that, plus whether the vendored dependencies
import (so a broken runtime folder is visible in one line).
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

PSF = "Python Software Foundation"
_PS = (
    "$s = Get-AuthenticodeSignature -LiteralPath $args[0]; "
    "$c = ''; if ($s.SignerCertificate) { $c = $s.SignerCertificate.Subject }; "
    "Write-Output ($s.Status.ToString() + '|' + $c)"
)

Runner = Callable[[Sequence[str]], str]


def _run_ps(argv: Sequence[str]) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run(list(argv), capture_output=True, timeout=60, creationflags=flags,
                       check=False)
    return p.stdout.decode("utf-8", errors="replace")


def signature_of(path: str | Path, runner: Runner | None = None) -> tuple[str, str]:
    """(status, signer subject) of ``path``; status 'Unknown' when it cannot be asked."""
    if sys.platform != "win32" and runner is None:
        return "Unknown", ""
    run = runner or _run_ps
    try:
        out = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   f"& {{ {_PS} }}", str(path)]).strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return "Unknown", ""
    if not out or "|" not in out[-1]:
        return "Unknown", ""
    status, _, subject = out[-1].partition("|")
    return status.strip() or "Unknown", subject.strip()


def publisher_of(subject: str) -> str:
    for part in subject.split(","):
        k, _, v = part.strip().partition("=")
        if k.upper() == "CN":
            return v.strip().strip('"')
    return subject


def deps_status(modules: Sequence[str]) -> str:
    out = []
    for m in modules:
        try:
            importlib.import_module(m)
            out.append(f"{m}=ok")
        except Exception as e:  # noqa: BLE001 - diagnostics only
            out.append(f"{m}=MISSING({type(e).__name__})")
    return " ".join(out)


def runtime_line(modules: Sequence[str] = (), runner: Runner | None = None,
                 executable: str | None = None) -> str:
    exe = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False))
    status, subject = signature_of(exe, runner)
    pub = publisher_of(subject)
    signed_psf = status == "Valid" and pub == PSF
    kind = "frozen-exe" if frozen else "python"
    parts = [f"runtime: {kind} {exe}", f"signature={status}",
             f"publisher={pub or '-'}", f"signed_by_psf={'yes' if signed_psf else 'no'}"]
    if modules:
        parts.append("deps: " + deps_status(modules))
    return "; ".join(parts)
