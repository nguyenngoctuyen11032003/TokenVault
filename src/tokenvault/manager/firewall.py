"""Windows Firewall inbound rules for the Manager (``netsh advfirewall``).

Two rules, named without spaces (no quoting pitfalls with netsh):

* ``TokenVault-Manager-Ingest-TCP-47800-47820``: TCP ``<base>-<base+20>`` (the
  ingest port and every fallback port the ingest server may pick when it is busy).
* ``TokenVault-Manager-Discovery-UDP-47801``: UDP 47801 (LAN discovery answers).

Both use ``remoteip=localsubnet`` (only PCs on the Manager's own subnets; adapters
on another routed subnet need the rule widened by an admin) and
``profile=private,domain`` (never the Public profile: a laptop in a cafe must not
accept ingest connections). If the office network is classified "Public", set it to
Private in Windows.

With ``program`` the rules only apply to that program; ``firewall --ensure`` run from
the frozen Manager exe defaults it to that exe. Adding rules needs elevation: only the
Manager installer runs ``TokenVault-Manager.exe firewall --ensure`` (one UAC prompt). The running
app only *checks* (``check_rules``) and reports a missing rule in Diagnostics.

``ensure_rules`` deletes then re-adds each rule, so re-running it updates the
port range. ``check_rules`` parses ``netsh ... show rule name=<n> verbose``;
labels may be localised, so when no ``LocalPort``/``Enabled`` line is found
it falls back to "rule name present and some port token covers the port".
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

from tokenvault.common.discovery_wire import DISCOVERY_PORT

PORT_FALLBACKS = 20  # same as ingest_server.PORT_FALLBACKS
DEFAULT_PORT = 47800


def tcp_range(port: int) -> str:
    return f"{port}-{min(port + PORT_FALLBACKS, 65535)}"


def tcp_rule(base: int = DEFAULT_PORT) -> str:
    return f"TokenVault-Manager-Ingest-TCP-{tcp_range(base)}"


UDP_RULE = f"TokenVault-Manager-Discovery-UDP-{DISCOVERY_PORT}"

Runner = Callable[[Sequence[str]], tuple[int, str]]
_NETSH = ["netsh", "advfirewall", "firewall"]
_PORT_TOKEN = re.compile(r"\b(\d{1,5})(?:\s*-\s*(\d{1,5}))?\b")


def run_netsh(args: Sequence[str]) -> tuple[int, str]:
    """Run netsh without a console window; output decoded with the OEM code page."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run(
        list(args), capture_output=True, timeout=30, creationflags=flags, check=False
    )
    enc = "oem" if sys.platform == "win32" else "utf-8"
    out = (p.stdout or b"") + (p.stderr or b"")
    return p.returncode, out.decode(enc, errors="replace")


@dataclass(frozen=True)
class FirewallStatus:
    present: bool  # both rules present, enabled and covering the ports
    tcp: bool
    udp: bool
    detail: str


def _check_port(port: int) -> None:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(f"invalid port {port!r}")


TCP_RULE = tcp_rule(DEFAULT_PORT)


def _rules(base: int) -> list[tuple[str, str, str]]:
    return [(tcp_rule(base), "TCP", tcp_range(base)), (UDP_RULE, "UDP", str(DISCOVERY_PORT))]


def ensure_rules(
    port: int = DEFAULT_PORT, *, program: str | None = None, runner: Runner | None = None
) -> dict[str, object]:
    """(Re)create both inbound allow rules. Needs elevation. Never raises for netsh errors."""
    _check_port(port)
    run = runner or run_netsh
    results = []
    for name, proto, ports in _rules(port):
        try:
            run([*_NETSH, "delete", "rule", f"name={name}"])  # absent is fine
            args = [*_NETSH, "add", "rule", f"name={name}", "dir=in", "action=allow",
                    "enable=yes", "profile=private,domain", "remoteip=localsubnet",
                    f"protocol={proto}", f"localport={ports}"]
            if program:
                args.append(f"program={program}")
            rc, out = run(args)
            results.append({"name": name, "ok": rc == 0, "detail": out.strip()[:500]})
        except (OSError, subprocess.SubprocessError) as e:
            results.append({"name": name, "ok": False, "detail": f"{type(e).__name__}: {e}"})
    return {"ok": all(r["ok"] for r in results), "rules": results}


def _value_lines(text: str, label: str) -> list[str]:
    out = []
    for line in text.splitlines():
        head, sep, value = line.partition(":")
        if sep and head.strip().lower() == label.lower():
            out.append(value.strip())
    return out


def _covers(text: str, port: int) -> bool:
    for m in _PORT_TOKEN.finditer(text):
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if lo <= port <= hi:
            return True
    return False


def _rule_ok(rc: int, text: str, name: str, port: int) -> bool:
    if rc != 0 or name not in text:
        return False
    enabled = _value_lines(text, "Enabled")
    if enabled and not any(v.lower() == "yes" for v in enabled):
        return False
    ports = _value_lines(text, "LocalPort")
    if ports:
        return any(_covers(v, port) for v in ports)
    after_name = text.split(name, 1)[1]
    return _covers(after_name, port)


def check_rules(port: int = DEFAULT_PORT, *, base_port: int = DEFAULT_PORT,
                runner: Runner | None = None) -> FirewallStatus:
    """Read-only: both rules (TCP range from ``base_port``) exist, are enabled and cover
    ``port`` (the bound ingest port, possibly a fallback) and UDP 47801."""
    run = runner or run_netsh
    ok: dict[str, bool] = {}
    details = []
    tcp_name = tcp_rule(base_port)
    for name, _proto, _ports in _rules(base_port):
        want = port if name == tcp_name else DISCOVERY_PORT
        try:
            rc, out = run([*_NETSH, "show", "rule", f"name={name}", "verbose"])
            ok[name] = _rule_ok(rc, out, name, want)
            details.append(f"{name}: {'ok' if ok[name] else 'missing'}")
        except (OSError, subprocess.SubprocessError) as e:
            ok[name] = False
            details.append(f"{name}: check failed ({type(e).__name__}: {e})")
    return FirewallStatus(
        present=ok[tcp_name] and ok[UDP_RULE],
        tcp=ok[tcp_name],
        udp=ok[UDP_RULE],
        detail="; ".join(details),
    )


def main(argv: Sequence[str]) -> int:
    """``firewall --ensure|--check [--port N] [--program EXE]``: prints JSON, exit 0 = ok."""
    ap = argparse.ArgumentParser(prog="TokenVault-Manager firewall")
    act = ap.add_mutually_exclusive_group(required=True)
    act.add_argument("--ensure", action="store_true", help="add/update the rules (elevated)")
    act.add_argument("--check", action="store_true", help="report whether the rules exist")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--program", default=None, help="restrict the rules to this exe")
    a = ap.parse_args(list(argv))
    try:
        _check_port(a.port)
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    if a.ensure:
        program = a.program or (sys.executable if getattr(sys, "frozen", False) else None)
        res = ensure_rules(a.port, program=program, runner=run_netsh)
        print(json.dumps({**res, "port": a.port}, ensure_ascii=False))
        return 0 if res["ok"] else 1
    st = check_rules(a.port, runner=run_netsh)
    print(json.dumps({**asdict(st), "port": a.port}, ensure_ascii=False))
    return 0 if st.present else 1
