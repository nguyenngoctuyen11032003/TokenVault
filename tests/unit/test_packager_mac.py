"""macOS installer zip: structure, sidecar payload, Install.command, API target (Task 24)."""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path

import api_helpers as h
import pytest

from tokenvault.common.config_payload import PayloadConfig, encode_payload, read_payload
from tokenvault.manager import api_manage
from tokenvault.manager.api import Api
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.lifecycle import Manager
from tokenvault.manager.packager import Packager, PackagerError
from tokenvault.manager.tls import CertInfo

FP = "cd" * 32
ROOT = Path(__file__).resolve().parents[2]
APP = "TokenVault Adapter.app"
MACHO = b"\xcf\xfa\xed\xfe" + b"macho" * 200


def make_template(path: Path, *, install: bool = True, app: bool = True,
                  extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if app:
            i = zipfile.ZipInfo(f"{APP}/Contents/MacOS/TokenVault Adapter")
            i.create_system = 3
            i.external_attr = 0o100755 << 16
            z.writestr(i, MACHO)
            z.writestr(f"{APP}/Contents/Info.plist", b"<plist/>")
        if install:
            for script in ("Install.command", "Uninstall.command"):
                i = zipfile.ZipInfo(script)
                i.create_system = 3
                i.external_attr = 0o100644 << 16  # wrong on purpose: packager must fix
                z.writestr(i, b"#!/bin/bash\n")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return path


@pytest.fixture
def enr(tmp_path: Path) -> Iterator[Enrollment]:
    e = Enrollment(tmp_path / "enrollment.db")
    yield e
    e.close()


def packager(tmp_path: Path, enr: Enrollment, tpl: Path) -> Packager:
    return Packager(tpl, enr, CertInfo(tmp_path / "c.pem", tmp_path / "k.pem", FP),
                    tmp_path / "out", lambda: (["10.0.0.5"], 47800), "mgr-1")


def test_mac_zip_structure_and_sidecar_payload_roundtrip(tmp_path: Path, enr: Enrollment) -> None:
    tpl = make_template(tmp_path / "adapter-mac.zip")
    b = packager(tmp_path, enr, tpl).build_for_team("Kế toán", "t3", "mac")
    assert b.file_name == "TokenVault-Adapter-Kế toán-mac.zip" and b.bytes == b.path.stat().st_size
    with zipfile.ZipFile(b.path) as z:
        names = z.namelist()
        assert "Install.command" in names and "tokenvault-config.bin" in names
        assert f"{APP}/Contents/MacOS/TokenVault Adapter" in names
        # the Mach-O is byte-identical: payload is a sidecar, never appended
        assert z.read(f"{APP}/Contents/MacOS/TokenVault Adapter") == MACHO
        modes = {i.filename: stat.S_IMODE(i.external_attr >> 16) for i in z.infolist()}
        assert modes["Install.command"] == 0o755 and modes["Uninstall.command"] == 0o755
        assert modes[f"{APP}/Contents/MacOS/TokenVault Adapter"] == 0o755
        assert modes["tokenvault-config.bin"] == 0o600
        z.extract("tokenvault-config.bin", tmp_path / "x")
    cfg = read_payload(tmp_path / "x" / "tokenvault-config.bin")
    assert cfg is not None and cfg.team == "Kế toán" and cfg.fingerprint == FP
    assert cfg.hosts == ["10.0.0.5"] and cfg.manager_id == "mgr-1"
    assert enr.register_device(cfg.secret, "dev-1", "Kế toán") is not None
    # second build: new name suffix + new secret, template untouched
    b2 = packager(tmp_path, enr, tpl).build_for_team("Kế toán", None, "mac")
    assert b2.file_name == "TokenVault-Adapter-Kế toán-mac (2).zip"
    with zipfile.ZipFile(b2.path) as z:
        z.extract("tokenvault-config.bin", tmp_path / "y")
    cfg2 = read_payload(tmp_path / "y" / "tokenvault-config.bin")
    assert cfg2 is not None and cfg2.secret != cfg.secret


def test_encode_payload_matches_footer_format() -> None:
    cfg = PayloadConfig("m", ["1.2.3.4"], 1, FP, "s" * 43, "T")
    assert encode_payload(cfg).endswith(b"TVLTCFG1")


@pytest.mark.parametrize("kw", [{"install": False}, {"app": False},
                                {"extra": {"tokenvault-config.bin": b"x"}},
                                {"extra": {"../evil": b"x"}}])
def test_bad_templates_rejected_and_package_revoked(
    tmp_path: Path, enr: Enrollment, kw: dict[str, object]
) -> None:
    tpl = make_template(tmp_path / "t.zip", **kw)  # type: ignore[arg-type]
    with pytest.raises(PackagerError):
        packager(tmp_path, enr, tpl).build_for_team("Dev", None, "mac")
    assert all(p.revoked for p in enr.list_packages())
    assert list((tmp_path / "out").glob("*")) == []


def test_not_a_zip_and_unknown_target(tmp_path: Path, enr: Enrollment) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip" * 10)
    with pytest.raises(PackagerError):
        packager(tmp_path, enr, bad).build_for_team("Dev", None, "mac")
    with pytest.raises(PackagerError):
        packager(tmp_path, enr, bad).build_for_team("Dev", None, "linux")


def test_windows_build_unchanged_default_target(tmp_path: Path, enr: Enrollment) -> None:
    exe = tmp_path / "a.exe"
    exe.write_bytes(b"MZ" + b"x" * 500)
    b = packager(tmp_path, enr, exe).build_for_team("Dev", None)
    assert b.file_name == "TokenVault-Adapter-Dev.exe"
    assert read_payload(b.path) is not None


# ------------------------------------------------------------- API + files
def test_api_target_mac_and_template_missing_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TOKENVAULT_MAC_TEMPLATE", raising=False)
    m = Manager(h.make_cfg(tmp_path))
    m.start()
    try:
        api = Api(m)
        msg = h.call_error(api.create_installer, "Dev", None, "mac")
        assert msg.startswith("[template_missing]") and "Mac" in msg and "build-mac.sh" in msg
        assert str(tmp_path / "data" / "templates" / "adapter-mac.zip") in msg
        tpl = tmp_path / "data" / "templates"
        tpl.mkdir(parents=True)
        make_template(tpl / "adapter-mac.zip")
        res = api.create_installer("Dev", None, "mac")
        assert res["target"] == "mac" and res["file_name"] == "TokenVault-Adapter-Dev-mac.zip"
        assert Path(res["path"]).is_file()
        win = api.create_installer("Dev", None)
        assert win["file_name"].endswith(".exe") and win["target"] == "windows"
        assert h.call_error(api.create_installer, "Dev", None, "beos").startswith(
            "[invalid_args]")
    finally:
        m.stop()


def test_api_manage_exposes_target_param() -> None:
    import inspect

    assert "target" in inspect.signature(api_manage.create_installer).parameters


def _render(name: str) -> str:
    tmpl = (ROOT / "packaging" / "mac" / name).read_text(encoding="utf-8")
    return tmpl.replace("@@APP_NAME@@", APP).replace("@@BIN_NAME@@", "TokenVault Adapter")


def _rendered_install_command() -> str:
    return _render("Install.command.tmpl")


def test_install_command_content() -> None:
    s = _rendered_install_command()
    assert "@@" not in s and s.startswith("#!/bin/bash\n") and "set -euo pipefail" in s
    assert "xattr -dr com.apple.quarantine" in s
    assert '"$HOME/Applications"' in s and 'setup --silent --accept --from "$PAYLOAD"' in s
    assert "sudo" not in s and "curl" not in s and "rm -rf /" not in s
    for line in s.splitlines():  # every rm -rf targets a known, quoted variable
        if "rm -rf" in line:
            assert re.search(r'rm -rf "\$(STAGE_DIR|BACKUP|OLD_APP)"', line), line
    assert "\r" not in s
    # staged copy -> consent -> only then is the previous install touched
    assert s.index('cp -R "$HERE/$APP_NAME"') < s.index('consent --from "$PAYLOAD"')
    assert s.index('consent --from "$PAYLOAD"') < s.index('mv "$OLD_APP" "$BACKUP"')
    assert "khôi phục bản cũ" in s  # rollback when setup fails


def test_uninstall_command_content() -> None:
    s = _render("Uninstall.command.tmpl")
    assert "@@" not in s and "launchctl bootout" in s and "delete-generic-password" in s
    assert 'rm -f "$PLIST"' in s and 'rm -rf "$APP_PATH"' in s and 'rm -rf "$SUPPORT_DIR"' in s
    assert "sudo" not in s
    for line in s.splitlines():
        if "rm -rf" in line:
            assert re.search(r'rm -rf "\$(APP_PATH|SUPPORT_DIR|LOG_DIR)"', line), line


def test_build_script_is_not_git_ignored() -> None:
    if shutil.which("git") is None:
        pytest.skip("no git")
    r = subprocess.run(["git", "check-ignore", "-q", "build/build-mac.sh"], cwd=ROOT,
                       capture_output=True, timeout=30)
    assert r.returncode == 1  # 1 = not ignored


def _working_bash() -> str | None:
    exe = shutil.which("bash")
    if exe is None:
        return None
    try:  # the Windows "bash" may be a WSL stub without a distro
        ok = subprocess.run([exe, "-c", "exit 0"], capture_output=True, timeout=30).returncode
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if ok == 0 else None


def test_shell_scripts_pass_bash_syntax_check() -> None:
    bash = _working_bash()
    if bash is None:
        pytest.skip("no working bash")
    scripts = {
        "Install.command": _rendered_install_command(),
        "Uninstall.command": _render("Uninstall.command.tmpl"),
        "build-mac.sh": (ROOT / "build" / "build-mac.sh").read_text(encoding="utf-8"),
    }
    for name, text in scripts.items():  # script on stdin: no path translation issues
        r = subprocess.run([bash, "-n"], input=text.encode("utf-8"), capture_output=True,
                           timeout=30)
        assert r.returncode == 0, (name, r.stderr.decode(errors="replace"))


def test_info_plist_is_menu_bar_agent() -> None:
    import plistlib

    d = plistlib.loads((ROOT / "packaging" / "mac" / "Info.plist").read_bytes())
    assert d["LSUIElement"] is True and d["CFBundleIdentifier"] == "vn.ics.tokenvault.adapter"
