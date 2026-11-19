"""TokenVault Adapter command line.

    TokenVaultAdapter.exe [run]            background agent (default; HKCU Run uses this)
    TokenVaultAdapter.exe setup [--from EXE] [--silent --accept] [--no-autostart]
    TokenVaultAdapter.exe status [--json]
    TokenVaultAdapter.exe about

Common dev/test options (after the subcommand): --data-dir DIR, --claude-dir DIR
(Claude config dir that contains ``projects``), --no-tray.

Exit codes: 0 ok (also: a second ``run`` while one is running), 2 no
installer payload / not configured, 3 consent declined.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from tokenvault.adapter import autostart, consent, paths, singleton, status_view
from tokenvault.adapter.app import AdapterApp, NotConfigured
from tokenvault.adapter.config import (
    AdapterConfig,
    delete_file,
    load_config,
    save_config,
    write_secret,
)
from tokenvault.adapter.identity import Identity, default_identity, read_identity
from tokenvault.adapter.paths import DataFiles
from tokenvault.adapter.watcher import set_background_priority
from tokenvault.common.config_payload import read_payload

log = logging.getLogger("tokenvault.adapter.main")

EXIT_OK, EXIT_NO_PAYLOAD, EXIT_DECLINED = 0, 2, 3
LOG_BYTES, LOG_BACKUPS = 1_000_000, 3
_SUBCOMMANDS = ("run", "setup", "status", "about")
_log_handler: logging.Handler | None = None


# ------------------------------------------------------------------- helpers
def _out(text: str, stream: TextIO | None = None) -> None:
    s = stream if stream is not None else sys.stdout
    if s is None:  # windowed exe: no console
        return
    try:
        print(text, file=s)
    except UnicodeEncodeError:
        s.flush()
        buf = getattr(s, "buffer", None)
        if buf is not None:
            buf.write((text + "\n").encode("utf-8"))
            buf.flush()


def _err(text: str) -> None:
    _out(text, sys.stderr)


def _start_file_logging(files: DataFiles) -> None:
    global _log_handler
    _stop_file_logging()
    try:
        files.root.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(
            files.log, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8", delay=True
        )
    except OSError as e:
        _err(f"cannot open log file: {e}")
        return
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("tokenvault")
    root.addHandler(h)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    _log_handler = h


def _stop_file_logging() -> None:
    global _log_handler
    if _log_handler is not None:
        logging.getLogger("tokenvault").removeHandler(_log_handler)
        _log_handler.close()
        _log_handler = None


def _files(args: argparse.Namespace) -> DataFiles:
    return DataFiles(Path(args.data_dir) if args.data_dir else paths.data_dir())


def _claude_dir(args: argparse.Namespace) -> Path | None:
    if args.claude_dir:
        return Path(args.claude_dir)
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else None


def _projects_dir(args: argparse.Namespace) -> Path:
    cd = _claude_dir(args)
    return cd / "projects" if cd is not None else paths.claude_projects_dir()


def _identity(args: argparse.Namespace) -> Identity:
    """With a custom Claude config dir, Claude Code keeps .claude.json inside it."""
    cd = _claude_dir(args)
    if cd is not None and (cd / ".claude.json").is_file():
        return read_identity(cd / ".claude.json", getpass.getuser(), socket.gethostname())
    return default_identity()


def agent_command(args: argparse.Namespace) -> list[str]:
    """argv that starts the installed agent (also what HKCU Run points at)."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "run"]
    else:  # dev checkout: prefer the console-less interpreter
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        cmd = [str(pyw if pyw.is_file() else exe), "-m", "tokenvault.adapter.main", "run"]
    if args.data_dir:
        cmd += ["--data-dir", str(Path(args.data_dir).resolve())]
    if args.claude_dir:
        cmd += ["--claude-dir", str(Path(args.claude_dir).resolve())]
    return cmd


def launch_detached(cmd: list[str]) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(  # noqa: S603 - our own executable
        cmd, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# ------------------------------------------------------------------ commands
def cmd_setup(args: argparse.Namespace) -> int:
    src = Path(args.from_exe) if args.from_exe else Path(sys.executable)
    try:
        payload = read_payload(src)
    except OSError:
        payload = None
    if payload is None:
        _err(
            f"'{src.name}' has no TokenVault configuration, so it cannot be set up.\n"
            "Ask your manager for your team's installer (made with 'Create installer' in "
            "the TokenVault Manager) and run that file."
        )
        return EXIT_NO_PAYLOAD
    text = consent.render_text(consent.WIRE_FIELDS, payload.hosts)
    accepted = bool(args.accept) if args.silent else consent.ask_consent(text)
    if not accepted:
        _err("Declined: nothing was installed or changed.")
        return EXIT_DECLINED
    files = _files(args)
    _start_file_logging(files)
    new = AdapterConfig.from_payload(payload)
    old = load_config(files.config)
    if old is not None and (old.manager_id, old.fingerprint) != (new.manager_id, new.fingerprint):
        log.info("installer is for a different Manager; dropping the old device key")
        delete_file(files.device_key)
    save_config(files.config, new)
    write_secret(files.enroll_secret, payload.secret)
    log.info("setup: config saved for team %r, Manager %s:%d", new.team, ",".join(new.hosts),
             new.port)
    if not args.no_autostart:
        cmd = agent_command(args)
        autostart.enable(cmd[0], args=" ".join(_quote(a) for a in cmd[1:]))
        launch_detached(cmd)
        log.info("setup: autostart registered and agent started")
    _out("TokenVault Adapter is set up.")
    return EXIT_OK


def _quote(a: str) -> str:
    return f'"{a}"' if (" " in a or not a) else a


def cmd_run(args: argparse.Namespace) -> int:
    files = _files(args)
    inst = singleton.SingleInstance(singleton.mutex_name())
    if not inst.acquire():
        return EXIT_OK  # another agent of this Windows user is running: quietly leave
    try:
        _start_file_logging(files)
        set_background_priority()
        try:
            app = AdapterApp(files, _projects_dir(args), identity=_identity(args),
                             tray=not args.no_tray)
        except NotConfigured as e:
            log.error("%s", e)
            _err(str(e))
            return EXIT_NO_PAYLOAD
        return int(app.run())
    finally:
        inst.release()


def cmd_status(args: argparse.Namespace) -> int:
    files = _files(args)
    running = singleton.is_running(singleton.mutex_name())
    d = status_view.gather(files, _identity(args), running)
    _out(json.dumps(d, ensure_ascii=False, indent=2) if args.json else status_view.format_text(d))
    return EXIT_OK


def cmd_about(args: argparse.Namespace) -> int:
    _out(status_view.about_text(_identity(args), _files(args)))
    return EXIT_OK


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", help="data folder (default %%LOCALAPPDATA%%\\TokenVault\\"
                        "Adapter)")
    common.add_argument("--claude-dir", help="Claude config folder that contains 'projects'")
    common.add_argument("--no-tray", action="store_true", help="run without the tray icon")
    common.add_argument("--no-autostart", action="store_true",
                        help="setup: do not register autostart or start the agent")
    p = argparse.ArgumentParser(prog="TokenVaultAdapter", description="TokenVault Adapter")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("run", parents=[common], help="background agent (default)")
    s = sub.add_parser("setup", parents=[common], help="first run from the installer")
    s.add_argument("--from", dest="from_exe", help="installer exe carrying the payload")
    s.add_argument("--silent", action="store_true", help="no consent window")
    s.add_argument("--accept", action="store_true", help="with --silent: consent given")
    st = sub.add_parser("status", parents=[common], help="show status and queue")
    st.add_argument("--json", action="store_true", help="JSON incl. one queued event")
    sub.add_parser("about", parents=[common], help="version information")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list[0] not in (*_SUBCOMMANDS, "-h", "--help"):
        args_list.insert(0, "run")
    args = build_parser().parse_args(args_list)
    handlers = {"run": cmd_run, "setup": cmd_setup, "status": cmd_status, "about": cmd_about}
    try:
        return handlers[args.command](args)
    finally:
        _stop_file_logging()


if __name__ == "__main__":
    sys.exit(main())
