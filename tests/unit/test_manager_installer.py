"""Structure lint of packaging/manager.iss (Task 29): the installed Manager must not depend
on an unsigned exe, because Application Control / Smart App Control blocks those (4551).

ISCC itself compiles the script in packaging/build.ps1; these tests pin the properties that a
compile cannot check."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISS = (ROOT / "packaging" / "manager.iss").read_text(encoding="utf-8-sig")


def _section(name: str) -> str:
    m = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", ISS.replace("\r\n", "\n"), re.S | re.M)
    assert m, name
    return m.group(1)


def _code_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(";")]


def test_privileges_stay_lowest() -> None:
    assert "PrivilegesRequired=lowest" in ISS


def test_launchers_start_the_signed_runtime_not_an_exe() -> None:
    for sec in ("Icons", "Run", "Registry"):
        body = "\n".join(_code_lines(_section(sec)))
        assert "TokenVault-Manager.exe" not in body, sec
        assert r"{app}\runtime\pythonw.exe" in body or r"{#PyW}" in body, sec
    assert '#define PyW "{app}\\runtime\\pythonw.exe"' in ISS
    assert '#define PyArgs "-m tokenvault.manager"' in ISS
    icons = "\n".join(_code_lines(_section("Icons")))
    assert icons.count('Parameters: "{#PyArgs}"') == 2  # Start menu + Desktop
    assert icons.count('WorkingDir: "{app}"') >= 2
    assert icons.count('IconFilename: "{app}\\tokenvault.ico"') >= 3
    run = "\n".join(_code_lines(_section("Run")))
    assert "postinstall" in run and "runasoriginaluser" in run


def test_autostart_uses_the_same_command_as_the_app() -> None:
    reg = "\n".join(_code_lines(_section("Registry")))
    assert '""{#PyW}"" {#PyArgs} --minimized' in reg
    assert "Tasks: startatlogin" in reg and "uninsdeletevalue" in reg


def test_no_unsigned_manager_exe_is_installed() -> None:
    files = "\n".join(_code_lines(_section("Files")))
    assert "SourceDir" not in files and "TokenVault-Manager" not in files
    assert 'DestDir: "{app}\\runtime"' in files and 'DestDir: "{app}\\scripts"' in files
    assert "adapter-setup-template.exe" in files  # the windows .exe employee target
    assert "Open-Firewall.cmd" in files and "tokenvault.ico" in files
    # a previous PyInstaller-based install is cleaned up on upgrade
    dele = _section("InstallDelete")
    assert "TokenVault-Manager.exe" in dele and "_internal" in dele and r"{app}\runtime" in dele
    assert "UninstallDisplayIcon={app}\\tokenvault.ico" in ISS


def test_firewall_steps_elevate_the_signed_python() -> None:
    code = ISS[ISS.index("[Code]"):]
    assert "ShellExec('runas', RuntimePython" in code
    assert re.search(r"ShellExec\('runas',\s*RuntimePython,\s*'-m tokenvault.manager firewall --remove'", code)
    assert "firewall --check --port" in code and "firewall --ensure --port" in code
    assert "powershell.exe" in code  # only for stopping our own pythonw (not for the rules)
    assert "Remove-NetFirewallRule" not in code
    # a declined/failed UAC gives both languages and the exact one-line command
    assert "FirewallCommand(Port)" in code and "Mở cổng tường lửa cho TokenVault" in code
    assert "The Windows Firewall rules could not be created" in code


def test_start_menu_shortcut_retries_the_firewall_step() -> None:
    icons = _section("Icons")
    assert "Mở cổng tường lửa cho TokenVault (cần quyền admin)" in icons
    assert "Open-Firewall.cmd" in icons
    cmd = (ROOT / "packaging" / "manager-extras" / "Open-Firewall.cmd").read_text(encoding="utf-8")
    assert "-Verb RunAs" in cmd and r"runtime\python.exe" in cmd
    assert "-m tokenvault.manager firewall --ensure --port" in cmd
    assert "1223" in cmd  # UAC declined is reported


def test_build_script_wires_the_portable_runtime() -> None:
    ps = (ROOT / "packaging" / "build.ps1").read_text(encoding="utf-8")
    assert "build-portable.ps1" in ps and "-RefreshOnly" in ps
    assert "/DRuntimeDir=" in ps and "/DTemplateExe=" in ps and "/DSourceDir" not in ps
    assert "-EmbedZip" in ps or "EmbedZip" in ps and "WheelDir" in ps  # offline flags kept
    assert "Python Software Foundation" in ps  # never ship an unsigned interpreter
