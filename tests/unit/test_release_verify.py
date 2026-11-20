"""Release Security Report helper (packaging/release/release_verify.py + release_report.py).

Signature facts are faked (they normally come from Get-AuthenticodeSignature/signtool)."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging" / "release"))
import release_report  # noqa: E402
import release_verify as rv  # noqa: E402

POLICY: dict[str, Any] = json.loads(
    (ROOT / "packaging" / "release" / "signing.json").read_text(encoding="utf-8"))
VERSION = rv.pyproject_version(ROOT / "pyproject.toml")


def fake_pe(path: Path, size: int = 256) -> Path:
    data = bytearray(size)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    return path


SIGNED = {"status": "Valid", "signer_subject": "CN=TokenVault, O=TokenVault", "timestamped": True,
          "digest": "sha256"}
UNSIGNED = {"status": "NotSigned", "timestamped": False}
VER = {"CompanyName": "TokenVault", "FileDescription": "x", "ProductName": "TokenVault",
       "ProductVersion": f"{VERSION}.0", "LegalCopyright": "(C) TokenVault"}


def pe(path: str, role: str, sig: dict[str, Any], ver: dict[str, str] | None = None) -> rv.PeEntry:
    return rv.PeEntry(path, role, "0" * 64, 1, dict(sig), dict(ver or VER))


def by_id(checks: list[rv.Check]) -> dict[str, rv.Check]:
    return {c.id: c for c in checks}


def test_is_pe_detects_header_not_extension(tmp_path: Path) -> None:
    assert rv.is_pe(fake_pe(tmp_path / "data.bin"))
    (tmp_path / "mz.txt").write_bytes(b"MZ" + bytes(100))
    (tmp_path / "small").write_bytes(b"MZ")
    assert not rv.is_pe(tmp_path / "mz.txt")
    assert not rv.is_pe(tmp_path / "small")


@pytest.mark.parametrize(("raw", "want"), [("0.1.0.0", "0.1.0"), ("0, 1, 0, 0", "0.1.0"),
                                          ("1.2.3", "1.2.3"), ("1.2.3.4", "1.2.3.4"), ("", "")])
def test_norm_version(raw: str, want: str) -> None:
    assert rv.norm_version(raw) == want


def test_roles_from_policy() -> None:
    assert rv.role_of("TokenVault-Manager.exe", POLICY) == "first_party"
    assert rv.role_of("tokenvault-manager-setup.EXE", POLICY) == "first_party"
    assert rv.role_of("adapter-setup-template.exe", POLICY) == "unsigned_by_design"
    assert rv.role_of("python312.dll", POLICY) == "bundled"


def test_parse_signtool_verify() -> None:
    text = ("Verifying: x.exe\nSigning Certificate Chain:\n    Issued to: Contoso\n"
            "The signature is timestamped: Fri Nov 20 11:08:59 2026\n"
            "Hash of file (sha256): 09FF\nSuccessfully verified: x.exe\n")
    facts = rv.parse_signtool_verify(text)
    assert facts == {"digest": "sha256", "timestamp": "Fri Nov 20 11:08:59 2026",
                     "verified": True}
    assert rv.parse_signtool_verify("SignTool Error: no signature")["verified"] is False


def test_parse_real_manager_iss() -> None:
    s = rv.parse_inno_setup((ROOT / "packaging" / "manager.iss").read_text(encoding="utf-8"))
    assert s["AppVersion"] == VERSION
    assert s["AppPublisher"] == POLICY["expected_publisher"]
    assert s["SignTool"] == "{#TvSignTool}"  # conditional Task 31 hook present


@pytest.mark.parametrize(("mode", "want"), [("Development", rv.WARN), ("Production", rv.FAIL)])
def test_unsigned_first_party_depends_on_mode(mode: str, want: str) -> None:
    checks = by_id(rv.check_signatures(
        [pe("r/TokenVault-Manager.exe", "first_party", UNSIGNED)], mode, POLICY))
    assert checks["signature.first_party"].status == want
    assert checks["signature.first_party"].details == ["r/TokenVault-Manager.exe"]


def test_fully_signed_release_passes_in_production() -> None:
    entries = [pe("r/TokenVault-Manager.exe", "first_party", SIGNED),
               pe("r/python312.dll", "bundled", {**SIGNED, "signer_subject": "CN=Python Software"
                                                 " Foundation"}),
               pe("r/adapter-setup-template.exe", "unsigned_by_design", UNSIGNED)]
    checks = by_id(rv.check_signatures(entries, "Production", POLICY))
    assert checks["signature.first_party"].status == rv.PASS
    assert checks["signature.quality"].status == rv.PASS
    assert checks["signature.bundled"].status == rv.PASS
    assert checks["signature.unsigned_by_design"].status == rv.WARN


def test_signature_problems_fail() -> None:
    entries = [pe("r/a.exe", "first_party", {**SIGNED, "timestamped": False, "digest": "sha1"}),
               pe("r/b.dll", "bundled", {"status": "HashMismatch"}),
               pe("r/adapter-setup-template.exe", "unsigned_by_design", SIGNED)]
    checks = by_id(rv.check_signatures(entries, "Production", POLICY))
    assert checks["signature.broken"].status == rv.FAIL
    assert any("timestamp" in d for d in checks["signature.quality"].details)
    assert any("sha1" in d for d in checks["signature.quality"].details)
    assert checks["signature.quality"].status == rv.FAIL
    # a signature on the template would be destroyed by the appended payload
    assert checks["signature.unsigned_by_design"].status == rv.FAIL


def test_unsigned_bundled_binaries_reported() -> None:
    checks = by_id(rv.check_signatures(
        [pe("r/_rust.pyd", "bundled", UNSIGNED)], "Development", POLICY))
    assert checks["signature.bundled"].status == rv.WARN
    assert checks["signature.bundled"].details == ["r/_rust.pyd"]


def test_versions_and_metadata() -> None:
    good = pe("r/TokenVault-Manager.exe", "first_party", SIGNED)
    bad = pe("r/TokenVaultAdapter.exe", "first_party", SIGNED,
             {"CompanyName": "Other", "ProductVersion": "9.9.9.0"})
    setups = {"manager.iss": {"AppVersion": VERSION}, "old.iss": {"AppVersion": "0.0.1"}}
    checks = by_id(rv.check_versions([good, bad], VERSION, setups, "TokenVault"))
    assert checks["version.consistency"].status == rv.FAIL
    assert len(checks["version.consistency"].details) == 2
    meta = " ".join(checks["version.metadata"].details)
    assert "CompanyName 'Other'" in meta and "LegalCopyright empty" in meta
    ok = by_id(rv.check_versions([good], VERSION, {"m.iss": {"AppVersion": VERSION}},
                                 "TokenVault"))
    assert ok["version.consistency"].status == rv.PASS
    assert ok["version.metadata"].status == rv.PASS


def test_inno_sanity() -> None:
    setup = {"AppId": "{{B2F5E7C3-8A14-4D69-A0B7-5E93D1C64F28}", "AppName": "A",
             "AppVersion": "1", "AppPublisher": "TokenVault", "OutputBaseFilename": "S",
             "PrivilegesRequired": "lowest", "AppPublisherURL": "u", "AppSupportURL": "u",
             "VersionInfoCopyright": "c", "SignTool": "{#TvSignTool}"}
    assert rv.check_inno({"m.iss": setup}, "TokenVault", POLICY)[0].status == rv.PASS
    broken = {**setup, "AppId": "random", "AppPublisher": ""}
    res = rv.check_inno({"m.iss": broken}, "TokenVault", POLICY)[0]
    assert res.status == rv.FAIL
    tmpl = {**setup, "OutputBaseFilename": "adapter-setup-template"}
    del tmpl["SignTool"]
    assert rv.check_inno({"t.iss": tmpl}, "TokenVault", POLICY)[0].status == rv.PASS


def test_contents_flags_disguised_pe_and_scripts(tmp_path: Path) -> None:
    fake_pe(tmp_path / "payload.dat")
    fake_pe(tmp_path / "ok.dll")
    (tmp_path / "_internal" / "tokenvault" / "ui").mkdir(parents=True)
    (tmp_path / "_internal" / "tokenvault" / "ui" / "app.js").write_text("//")
    (tmp_path / "run.ps1").write_text("#")
    files = [(f"pkg/{p.relative_to(tmp_path).as_posix()}", p)
             for p in sorted(tmp_path.rglob("*")) if p.is_file()]
    checks = by_id(rv.check_contents(files, {"pkg/ok.dll"}, POLICY))
    assert checks["content.executables"].status == rv.FAIL
    assert checks["content.executables"].details == [
        "pkg/payload.dat: PE image with extension .dat"]
    assert checks["content.scripts"].details == ["pkg/run.ps1"]


def _facts(tmp_path: Path, signed: bool) -> Path:
    rel = tmp_path / "rel"
    setup = fake_pe(rel / "TokenVault-Manager-Setup.exe")
    fake_pe(rel / "pkg" / "TokenVault-Manager.exe")
    sig = SIGNED if signed else UNSIGNED
    facts = {"roots": [{"label": "release", "path": str(rel)}], "tools": {"python": "3.12"},
             "files": [{"path": "release/TokenVault-Manager-Setup.exe", "signature": sig,
                        "version": VER, "signtool": ""},
                       {"path": "release/pkg/TokenVault-Manager.exe", "signature": sig,
                        "version": VER, "signtool": ""}]}
    p = tmp_path / "facts.json"
    p.write_text(json.dumps(facts), encoding="utf-8")
    assert setup.exists()
    return p


@pytest.mark.parametrize(("mode", "signed", "rc"), [("Development", False, 0),
                                                    ("Production", False, 1),
                                                    ("Production", True, 0)])
def test_cli_writes_report_and_sums(tmp_path: Path, mode: str, signed: bool, rc: int) -> None:
    facts = _facts(tmp_path, signed)
    out = tmp_path / "out"
    setup = tmp_path / "rel" / "TokenVault-Manager-Setup.exe"
    code = release_report.main(["--facts", str(facts), "--pyproject", str(ROOT / "pyproject.toml"),
                                "--mode", mode, "--out-dir", str(out),
                                "--artifact", str(setup)])
    assert code == rc
    report = json.loads((out / "release-report.json").read_text(encoding="utf-8"))
    assert report["signed"] is signed and report["mode"] == mode
    md = (out / "release-report.md").read_text(encoding="utf-8")
    assert ("## UNSIGNED" in md) is (not signed)
    sums = (out / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert sums == f"{rv.sha256_file(setup)} *TokenVault-Manager-Setup.exe\n"


def test_cli_includes_extra_checks(tmp_path: Path) -> None:
    extra = tmp_path / "deps.json"
    extra.write_text(json.dumps({"checks": [{"id": "deps.pip_check", "status": "FAIL",
                                             "message": "broken", "details": ["x"]}]}))
    code = release_report.main(["--facts", str(_facts(tmp_path, True)), "--pyproject",
                                str(ROOT / "pyproject.toml"), "--out-dir", str(tmp_path / "o"),
                                "--extra", str(extra)])
    assert code == 1
