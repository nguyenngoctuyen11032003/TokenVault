"""Task 25: portable ("no-exe") ZIP assembly with a FAKED runtime folder, scripts, selftest line."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from api_helpers import make_cfg

from tokenvault.common import runtime_info
from tokenvault.common.config_payload import read_payload
from tokenvault.manager import portable_packager as pp
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.lifecycle import Manager
from tokenvault.manager.packager import PackagerError
from tokenvault.manager.tls import CertInfo

FP = "ab" * 32
REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "packaging" / "portable"


@pytest.fixture
def enr(tmp_path: Path) -> Iterator[Enrollment]:
    e = Enrollment(tmp_path / "enrollment.db")
    yield e
    e.close()


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    root = tmp_path / "portable-runtime"
    rt = root / "runtime"
    for rel, data in {
        "python.exe": b"MZpy", "pythonw.exe": b"MZpyw", "python312._pth": b"python312.zip\r\n.\r\n",
        "Lib/site-packages/websockets/__init__.py": b"x",
        "Lib/site-packages/webview/__init__.py": b"x",
        "Lib/site-packages/pywebview-6.2.1.dist-info/METADATA": b"x",
        "Lib/site-packages/reportlab/__init__.py": b"x",
        "Lib/site-packages/charset_normalizer/__init__.py": b"x",
        "app/tokenvault/__init__.py": b"x", "app/tokenvault/adapter/__main__.py": b"x",
        "app/tokenvault/manager/app.py": b"x", "app/tokenvault/ui/index.html": b"x",
    }.items():
        p = rt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def make(tmp_path: Path, enr: Enrollment, runtime: Path) -> pp.PortablePackager:
    return pp.PortablePackager(runtime, enr, CertInfo(tmp_path / "c", tmp_path / "k", FP),
                               tmp_path / "out", lambda: (["192.168.1.5"], 47800), "mgr-1",
                               scripts_dir=SCRIPTS)


def test_team_zip_roundtrip(tmp_path: Path, enr: Enrollment, runtime: Path) -> None:
    b = make(tmp_path, enr, runtime).build_for_team("Dev", "batch 1")
    assert b.file_name == "TokenVault-Adapter-Dev-portable.zip" and b.path.is_file()
    with zipfile.ZipFile(b.path) as z:
        assert z.testzip() is None
        names = set(z.namelist())
        assert {"Install.cmd", "install.ps1", "Uninstall.cmd", "uninstall.ps1", "README.txt",
                "tokenvault-config.bin", "runtime/python.exe", "runtime/pythonw.exe",
                "runtime/python312._pth", "runtime/app/tokenvault/adapter/__main__.py",
                "runtime/Lib/site-packages/websockets/__init__.py"} <= names
        # manager-only content is left out of the per-team adapter package
        assert not any(n.startswith(("runtime/Lib/site-packages/webview/",
                                     "runtime/Lib/site-packages/reportlab/",
                                     "runtime/Lib/site-packages/pywebview-",
                                     "runtime/Lib/site-packages/charset_normalizer/",
                                     "runtime/app/tokenvault/manager/",
                                     "runtime/app/tokenvault/ui/")) for n in names)
        assert not any(n.endswith(".exe") and n != "runtime/python.exe"
                       and n != "runtime/pythonw.exe" for n in names)
        z.extract("tokenvault-config.bin", tmp_path / "x")
    cfg = read_payload(tmp_path / "x" / "tokenvault-config.bin")
    assert cfg is not None and cfg.fingerprint == FP and cfg.manager_id == "mgr-1"
    assert cfg.team == "Dev" and cfg.hosts == ["192.168.1.5"] and cfg.port == 47800
    assert enr.register_device(cfg.secret, "dev-1", "Dev") is not None
    assert [p.package_id for p in enr.list_packages()] == [b.package_id]
    # no temp leftovers, second build never overwrites and gets a new secret
    b2 = make(tmp_path, enr, runtime).build_for_team("Dev", None)
    assert b2.file_name == "TokenVault-Adapter-Dev-portable (2).zip"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(
        [b.file_name, b2.file_name])
    with zipfile.ZipFile(b2.path) as z:
        z.extract("tokenvault-config.bin", tmp_path / "y")
    cfg2 = read_payload(tmp_path / "y" / "tokenvault-config.bin")
    assert cfg2 is not None and cfg2.secret != cfg.secret


def test_missing_runtime_is_a_clear_error_and_creates_no_package(
        tmp_path: Path, enr: Enrollment) -> None:
    with pytest.raises(PackagerError, match="build-portable"):
        make(tmp_path, enr, tmp_path / "nothing").build_for_team("Dev", None)
    assert enr.list_packages() == []


def test_failed_zip_revokes_package_and_cleans_up(
        tmp_path: Path, enr: Enrollment, runtime: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(pp, "_write_zip", boom)
    with pytest.raises(PackagerError, match="disk full"):
        make(tmp_path, enr, runtime).build_for_team("Dev", None)
    assert [p for p in enr.list_packages() if not p.revoked] == []
    assert list((tmp_path / "out").glob(".tvl-*")) == []


def test_manager_zip_has_launcher_and_full_runtime(tmp_path: Path, runtime: Path) -> None:
    out = pp.build_manager_zip(runtime, tmp_path / "m.zip", scripts_dir=SCRIPTS)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
    assert {"Start-Manager.cmd", "Install-Manager.ps1", "Uninstall-Manager.ps1",
            "README-Manager.txt", "runtime/Lib/site-packages/webview/__init__.py",
            "runtime/app/tokenvault/manager/app.py"} <= names
    assert "tokenvault-config.bin" not in names  # generic: no credential


def test_scripts_are_ascii_and_reference_the_documented_commands() -> None:
    for f in SCRIPTS.iterdir():
        f.read_bytes().decode("ascii")  # PowerShell 5.1 reads BOM-less files as ANSI
    inst = (SCRIPTS / "install.ps1").read_text()
    assert "-m', 'tokenvault.adapter', 'setup', '--from'" in inst
    assert "Unblock-File" in inst and "LOCALAPPDATA" in inst and "pythonw.exe" in inst
    mgr = (SCRIPTS / "Install-Manager.ps1").read_text()
    assert "tokenvault.manager" in mgr and "firewall --ensure" in mgr and "-Verb RunAs" in mgr
    assert "WebView2" in mgr and "developer.microsoft.com/microsoft-edge/webview2" in mgr
    assert "tokenvault.manager" in (SCRIPTS / "Start-Manager.cmd").read_text()


@pytest.mark.skipif(shutil.which("powershell") is None, reason="needs Windows PowerShell")
@pytest.mark.parametrize("name", ["install.ps1", "uninstall.ps1", "Install-Manager.ps1",
                                  "Uninstall-Manager.ps1", "../../build/build-portable.ps1"])
def test_powershell_scripts_parse(name: str) -> None:
    path = (SCRIPTS / name).resolve()
    cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
           f"'{path}',[ref]$t,[ref]$e);if($e.Count){{$e|%{{$_.Message}};exit 1}}")
    p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                       text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr


def test_api_target_windows_portable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     runtime: Path) -> None:
    from tokenvault.manager.api import Api

    monkeypatch.setenv("TOKENVAULT_PORTABLE_RUNTIME", str(runtime))
    monkeypatch.setattr(pp, "default_scripts_dir", lambda: SCRIPTS)
    m = Manager(make_cfg(tmp_path))
    m.start()
    try:
        api = Api(m)
        res = api.create_installer("Dev", None, "windows-portable")
        assert res["target"] == "windows-portable" and res["file_name"].endswith("-portable.zip")
        assert Path(res["path"]).is_file()
        exe = api.create_installer("Dev", None)  # default target unchanged
        assert exe["file_name"].endswith(".exe")
        with pytest.raises(Exception, match="Unknown installer target"):
            api.create_installer("Dev", None, "amiga")
    finally:
        m.stop()


# -- selftest line ----------------------------------------------------------------------------
def test_runtime_line_reports_psf_signature() -> None:
    def ok(_a: object) -> str:
        return "Valid|CN=Python Software Foundation, O=Python Software Foundation, C=US\n"

    line = runtime_info.runtime_line(("json",), runner=ok, executable="C:/x/python.exe")
    assert "signature=Valid" in line and "publisher=Python Software Foundation" in line
    assert "signed_by_psf=yes" in line and "json=ok" in line


def test_runtime_line_unsigned_and_missing_dep() -> None:
    line = runtime_info.runtime_line(("no_such_mod_xyz",), runner=lambda _a: "NotSigned|\n",
                                     executable="C:/x/python.exe")
    assert "signed_by_psf=no" in line and "no_such_mod_xyz=MISSING" in line


def test_python_dash_m_entry_points_exist() -> None:
    assert (REPO / "src/tokenvault/adapter/__main__.py").is_file()
    assert (REPO / "src/tokenvault/manager/__main__.py").is_file()
