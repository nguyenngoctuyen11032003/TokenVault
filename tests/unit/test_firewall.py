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
        "dir=in", "action=allow", "enable=yes", "profile=any", "protocol=TCP",
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
