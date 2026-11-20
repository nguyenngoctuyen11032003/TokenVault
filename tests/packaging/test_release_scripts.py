"""packaging/release/*.ps1: PowerShell parse check and real dry runs of sign.ps1 (no Pester).

No certificate is needed: these runs exercise the policy/plan, -WhatIf, the UNSIGNED
banner, exit codes and credential validation. Windows only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell scripts")

ROOT = Path(__file__).resolve().parents[2]
REL = ROOT / "packaging" / "release"
SIGN = REL / "sign.ps1"
SCRIPTS = sorted(REL.glob("*.ps1")) + [ROOT / "packaging" / "build.ps1"]


def ps(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("TOKENVAULT_SIGN")}
    clean.update(env or {})
    return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
                          capture_output=True, text=True, env=clean, timeout=180)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_without_errors(script: Path) -> None:
    cmd = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
           f"'{script}', [ref]$null, [ref]$e); $e | ForEach-Object {{ $_.ToString() }}; "
           "exit $e.Count")
    r = ps(["-Command", cmd])
    assert r.returncode == 0, r.stdout + r.stderr


def _unsigned_pe() -> Path:
    import PIL

    for p in sorted(Path(PIL.__file__).parent.glob("*.pyd")):
        return p
    pytest.skip("no unsigned .pyd available to act as a fake first-party exe")


@pytest.fixture
def package(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    shutil.copy(_unsigned_pe(), pkg / "TokenVault-Manager.exe")
    shutil.copy(_unsigned_pe(), pkg / "adapter-setup-template.exe")
    shutil.copy(_unsigned_pe(), pkg / "thirdparty.pyd")
    py_dll = next(Path(sys.base_prefix).glob("python3*.dll"), None)
    if py_dll:
        shutil.copy(py_dll, pkg / py_dll.name)  # vendor-signed (PSF) when from python.org
    return pkg


def test_whatif_dry_run_without_credentials(package: Path) -> None:
    r = ps(["-File", str(SIGN), "-Path", str(package), "-WhatIf"])
    out = r.stdout
    assert r.returncode == 0, out + r.stderr
    assert "UNSIGNED build" in out
    lines = out.splitlines()
    assert any(ln.startswith("SIGN") and "TokenVault-Manager.exe" in ln for ln in lines)
    assert any(ln.startswith("SKIP") and "adapter-setup-template.exe" in ln
               and "by design" in ln for ln in lines)
    assert any(ln.startswith("SIGN") and "thirdparty.pyd" in ln for ln in lines)
    assert "would need a signature" in out


def test_production_without_credentials_fails(package: Path) -> None:
    r = ps(["-File", str(SIGN), "-Path", str(package), "-Mode", "Production"])
    assert r.returncode == 2
    assert "UNSIGNED build" in r.stdout


def test_check_credentials_exit_codes() -> None:
    assert ps(["-File", str(SIGN), "-CheckCredentials"]).returncode == 10
    assert ps(["-File", str(SIGN), "-CheckCredentials", "-Mode", "Production"]).returncode == 2


def test_ambiguous_credentials_rejected(tmp_path: Path) -> None:
    r = ps(["-File", str(SIGN), "-CheckCredentials"],
           {"TOKENVAULT_SIGN_THUMBPRINT": "AB" * 20, "TOKENVAULT_SIGN_PFX": str(tmp_path / "x")})
    assert r.returncode == 2 and "several credential sources" in r.stdout


def test_unknown_thumbprint_rejected() -> None:
    r = ps(["-File", str(SIGN), "-CheckCredentials"], {"TOKENVAULT_SIGN_THUMBPRINT": "00" * 20})
    assert r.returncode == 2 and "certificate not found" in r.stdout


def test_pfx_password_only_from_environment(tmp_path: Path) -> None:
    pfx = tmp_path / "c.pfx"
    pfx.write_bytes(b"not a pfx")
    r = ps(["-File", str(SIGN), "-CheckCredentials"], {"TOKENVAULT_SIGN_PFX": str(pfx)})
    assert r.returncode == 2 and "TOKENVAULT_SIGN_PFX_PASSWORD" in r.stdout
    secret = "S3cr3t-should-not-leak"
    r = ps(["-File", str(SIGN), "-CheckCredentials"],
           {"TOKENVAULT_SIGN_PFX": str(pfx), "TOKENVAULT_SIGN_PFX_PASSWORD": secret})
    assert r.returncode == 2 and "could not be opened" in r.stdout
    assert secret not in r.stdout + r.stderr


def test_artifact_signing_metadata_validated(tmp_path: Path) -> None:
    dlib = tmp_path / "Azure.CodeSigning.Dlib.dll"
    dlib.write_bytes(b"")
    md = tmp_path / "metadata.json"
    md.write_text('{"Endpoint": "https://weu.codesigning.azure.net", '
                  '"CodeSigningAccountName": "a"}')
    r = ps(["-File", str(SIGN), "-CheckCredentials"],
           {"TOKENVAULT_SIGN_ARTIFACT_DLIB": str(dlib),
            "TOKENVAULT_SIGN_ARTIFACT_METADATA": str(md)})
    assert r.returncode == 2 and "CertificateProfileName" in r.stdout
