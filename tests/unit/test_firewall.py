"""Firewall rule commands are built correctly; tests NEVER run netsh for real."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from tokenvault.manager import firewall
from tokenvault.manager.firewall import TCP_RULE, UDP_RULE, check_rules, ensure_rules


class FakeRunner:
    def __init__(self, replies: dict[str, tuple[int, str]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.replies = replies or {}

    def __call__(self, args: Sequence[str]) -> tuple[int, str]:
        self.calls.append(list(args))
        verb = args[3]
        name = next(a for a in args if a.startswith("name=")).removeprefix("name=")
        return self.replies.get(f"{verb}:{name}", (0, "Ok.\n"))


def rule_output(name: str, proto: str, port: str, enabled: str = "Yes") -> str:
    return (
        f"\nRule Name:                            {name}\n"
        "----------------------------------------------------------------------\n"
        f"Enabled:                              {enabled}\n"
        "Direction:                            In\n"
        "Profiles:                             Domain,Private,Public\n"
        f"Protocol:                             {proto}\n"
        f"LocalPort:                            {port}\n"
        "Action:                               Allow\n\nOk.\n"
    )


def test_ensure_builds_delete_then_add_for_both_rules() -> None:
    run = FakeRunner()
    res = ensure_rules(47800, program=r"C:\Program Files\TokenVault\TokenVault-Manager.exe",
                       runner=run)
    assert res["ok"] is True
    assert run.calls[0] == [
        "netsh", "advfirewall", "firewall", "delete", "rule", f"name={TCP_RULE}"
    ]
    assert run.calls[1] == [
        "netsh", "advfirewall", "firewall", "add", "rule", f"name={TCP_RULE}",
        "dir=in", "action=allow", "enable=yes", "profile=private,domain", "remoteip=localsubnet",
        "protocol=TCP",
        "localport=47800-47820",
        r"program=C:\Program Files\TokenVault\TokenVault-Manager.exe",
    ]
    assert run.calls[2][3:6] == ["delete", "rule", f"name={UDP_RULE}"]
    assert run.calls[3][-2:] == [
        "localport=47801",
        r"program=C:\Program Files\TokenVault\TokenVault-Manager.exe",
    ]
    assert "protocol=UDP" in run.calls[3]
    assert " " not in TCP_RULE and " " not in UDP_RULE  # no quoting issues with netsh


def test_ensure_without_program_and_high_port_clamped() -> None:
    run = FakeRunner()
    ensure_rules(65530, runner=run)
    add = run.calls[1]
    assert "localport=65530-65535" in add and not any(a.startswith("program=") for a in add)


def test_ensure_reports_failure() -> None:
    run = FakeRunner({f"add:{TCP_RULE}": (1, "The requested operation requires elevation.")})
    res = ensure_rules(47800, runner=run)
    assert res["ok"] is False
    tcp = next(r for r in res["rules"] if r["name"] == TCP_RULE)
    assert tcp["ok"] is False and "elevation" in tcp["detail"]


@pytest.mark.parametrize("port", [0, 70000, -1])
def test_ensure_rejects_bad_port(port: int) -> None:
    with pytest.raises(ValueError):
        ensure_rules(port, runner=FakeRunner())


def test_check_parses_netsh_output_present() -> None:
    run = FakeRunner({
        f"show:{TCP_RULE}": (0, rule_output(TCP_RULE, "TCP", "47800-47820")),
        f"show:{UDP_RULE}": (0, rule_output(UDP_RULE, "UDP", "47801")),
    })
    st = check_rules(47805, runner=run)
    assert st.present and st.tcp and st.udp
    assert run.calls[0] == [
        "netsh", "advfirewall", "firewall", "show", "rule", f"name={TCP_RULE}", "verbose"
    ]


def test_check_port_outside_rule_range_is_missing() -> None:
    run = FakeRunner({
        f"show:{TCP_RULE}": (0, rule_output(TCP_RULE, "TCP", "47800-47820")),
        f"show:{UDP_RULE}": (0, rule_output(UDP_RULE, "UDP", "47801")),
    })
    st = check_rules(47900, runner=run)
    assert st.tcp is False and st.udp is True and st.present is False


def test_check_disabled_rule_is_missing() -> None:
    run = FakeRunner({
        f"show:{TCP_RULE}": (0, rule_output(TCP_RULE, "TCP", "47800-47820", enabled="No")),
        f"show:{UDP_RULE}": (0, rule_output(UDP_RULE, "UDP", "47801")),
    })
    assert check_rules(47800, runner=run).tcp is False


def test_check_absent_rule() -> None:
    run = FakeRunner({
        f"show:{TCP_RULE}": (1, "\nNo rules match the specified criteria.\n"),
        f"show:{UDP_RULE}": (1, "\nNo rules match the specified criteria.\n"),
    })
    st = check_rules(47800, runner=run)
    assert (st.present, st.tcp, st.udp) == (False, False, False)


def test_check_comma_list_and_localised_labels_tolerated() -> None:
    # Unknown (localised) labels: presence by rule name + port token anywhere is accepted.
    text = f"Tên quy tắc: {TCP_RULE}\nCổng: 47800-47820\n"
    run = FakeRunner({
        f"show:{TCP_RULE}": (0, text),
        f"show:{UDP_RULE}": (0, rule_output(UDP_RULE, "UDP", "47801,50000")),
    })
    st = check_rules(47800, runner=run)
    assert st.tcp and st.udp


def test_check_runner_crash_is_not_present() -> None:
    def boom(args: Sequence[str]) -> tuple[int, str]:
        raise OSError("netsh missing")

    st = check_rules(47800, runner=boom)
    assert st.present is False and "netsh missing" in st.detail


def test_cli_check_and_ensure_print_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = FakeRunner({
        f"show:{TCP_RULE}": (0, rule_output(TCP_RULE, "TCP", "47800-47820")),
        f"show:{UDP_RULE}": (0, rule_output(UDP_RULE, "UDP", "47801")),
    })
    monkeypatch.setattr(firewall, "run_netsh", run)
    assert firewall.main(["--check"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["present"] is True and out["port"] == 47800
    assert firewall.main(["--ensure", "--port", "47810", "--program", "C:\\x.exe"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["port"] == 47810
    assert any("localport=47810-47830" in c for c in run.calls[-3])


def test_cli_requires_an_action(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        firewall.main([])


def test_rule_names_carry_their_port_range() -> None:
    assert TCP_RULE == "TokenVault-Manager-Ingest-TCP-47800-47820"
    assert UDP_RULE == "TokenVault-Manager-Discovery-UDP-47801"
    run = FakeRunner()
    ensure_rules(47900, runner=run)
    assert run.calls[1][5] == "name=TokenVault-Manager-Ingest-TCP-47900-47920"
    assert all("remoteip=localsubnet" in c for c in run.calls if c[3] == "add")


def test_check_fallback_port_inside_base_range() -> None:
    run = FakeRunner({
        f"show:{TCP_RULE}": (0, rule_output(TCP_RULE, "TCP", "47800-47820")),
        f"show:{UDP_RULE}": (0, rule_output(UDP_RULE, "UDP", "47801")),
    })
    assert check_rules(47805, runner=run).present is True


EXE = r"C:\TV\TokenVault-Manager.exe"


def test_ensure_from_frozen_exe_defaults_program(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = FakeRunner({})
    monkeypatch.setattr(firewall, "run_netsh", run)
    monkeypatch.setattr(firewall.sys, "frozen", True, raising=False)
    monkeypatch.setattr(firewall.sys, "executable", EXE)
    assert firewall.main(["--ensure"]) == 0
    adds = [c for c in run.calls if "add" in c]
    assert adds and all("program=" + EXE in c for c in adds)
    assert all("profile=private,domain" in c for c in adds)


def _fake_runtime(tmp_path: pytest.TempPathFactory | object, with_pth: bool = True) -> str:
    from pathlib import Path

    rt = Path(str(tmp_path)) / "runtime"
    rt.mkdir()
    (rt / "python.exe").write_bytes(b"x")
    (rt / "pythonw.exe").write_bytes(b"x")
    if with_pth:
        (rt / "python312._pth").write_text("python312.zip\n", encoding="utf-8")
    return str(rt / "python.exe")


def test_default_program_embedded_runtime_is_pythonw(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    py = _fake_runtime(tmp_path)
    monkeypatch.setattr(firewall.sys, "frozen", False, raising=False)
    monkeypatch.setattr(firewall.sys, "executable", py)
    assert firewall.default_program() == py.replace("python.exe", "pythonw.exe")


def test_default_program_plain_interpreter_is_unrestricted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    py = _fake_runtime(tmp_path, with_pth=False)  # a venv-like folder: no ._pth
    monkeypatch.setattr(firewall.sys, "frozen", False, raising=False)
    monkeypatch.setattr(firewall.sys, "executable", py)
    assert firewall.default_program() is None


def test_manual_command_uses_python_module_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    py = _fake_runtime(tmp_path)
    monkeypatch.setattr(firewall.sys, "frozen", False, raising=False)
    monkeypatch.setattr(firewall.sys, "executable", py.replace("python.exe", "pythonw.exe"))
    cmd = firewall.manual_command(47900)
    assert cmd == f'"{py}" -m tokenvault.manager firewall --ensure --port 47900' or \
        firewall.sys.platform != "win32"


def test_ensure_dry_run_changes_nothing_and_lists_commands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = FakeRunner({})
    monkeypatch.setattr(firewall, "run_netsh", run)
    assert firewall.main(["--ensure", "--dry-run", "--port", "47900",
                          "--program", r"C:\rt\pythonw.exe"]) == 0
    assert run.calls == []  # the real runner was never used
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["program"] == r"C:\rt\pythonw.exe"
    adds = [c for c in out["commands"] if " add rule " in c]
    assert len(adds) == 2 and all(r"program=C:\rt\pythonw.exe" in c for c in adds)
    assert any("localport=47900-47920" in c for c in adds)


def test_ensure_no_program_flag(monkeypatch: pytest.MonkeyPatch,
                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(firewall.sys, "frozen", True, raising=False)
    monkeypatch.setattr(firewall.sys, "executable", EXE)
    assert firewall.main(["--ensure", "--dry-run", "--no-program"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["program"] is None and not any("program=" in c for c in out["commands"])


def test_failed_ensure_prints_manual_command_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = FakeRunner({f"add:{UDP_RULE}": (1, "Access denied")})
    monkeypatch.setattr(firewall, "run_netsh", run)
    monkeypatch.setattr(firewall, "default_program", lambda: None)
    assert firewall.main(["--ensure"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert "firewall --ensure --port 47800" in out["hint"] and "administrator" in out["hint"]


def test_remove_deletes_every_own_rule_including_other_ports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other = "TokenVault-Manager-Ingest-TCP-48000-48020"
    listing = rule_output(other, "TCP", "48000-48020") + "Rule Name: Unrelated-Rule\n"
    run = FakeRunner({"show:all": (0, listing)})
    res = firewall.remove_rules(runner=run)
    deleted = [c[-1].removeprefix("name=") for c in run.calls if c[3] == "delete"]
    assert set(deleted) == {TCP_RULE, UDP_RULE, other} and res["ok"] is True
    run2 = FakeRunner({})
    monkeypatch.setattr(firewall, "run_netsh", run2)
    assert firewall.main(["--remove", "--dry-run"]) == 0
    assert run2.calls == []
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


def test_install_dir_of_installed_runtime_is_folder_holding_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    from pathlib import Path

    from tokenvault.manager import paths

    app = Path(str(tmp_path)) / "app"
    pkg = app / "runtime" / "app" / "tokenvault" / "manager"
    pkg.mkdir(parents=True)
    (app / "runtime" / "python.exe").write_bytes(b"x")
    monkeypatch.setattr(paths, "_PKG_DIR", pkg)
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)
    assert paths.install_dir() == app
    assert paths.template_path() == app / "adapter-setup-template.exe"
