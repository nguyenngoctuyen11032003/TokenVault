"""TokenVault Adapter command line.

    TokenVaultAdapter.exe [run]            background agent (default; HKCU Run uses this)
    TokenVaultAdapter.exe setup [--from EXE] [--silent --accept] [--no-autostart] [--no-start]
    TokenVaultAdapter.exe status [--json]
    TokenVaultAdapter.exe about
    TokenVaultAdapter.exe show {what-is-sent,about}   (window; started by the tray)

Common options (after the subcommand): --data-dir DIR, --claude-dir DIR (Claude
config dir that contains ``projects``), --no-tray, --lang {vi,en}.

Exit codes: 0 ok (also: a second ``run`` while one is running), 1 ``run``
crashed (logged + state.json "crashed"), 2 no installer payload / not
configured, 3 consent declined, 4 consent screen could not be shown, 5 DPAPI
could not protect the secret (nothing written).
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
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from tokenvault.adapter import autostart, consent, paths, singleton, status_view, strings
from tokenvault.adapter.app import AdapterApp, NotConfigured
from tokenvault.adapter.config import (
    AdapterConfig,
    delete_file,
    load_config,
    save_config,
    write_enroll_secret,
)
from tokenvault.adapter.identity import Identity, default_identity, read_identity
from tokenvault.adapter.paths import DataFiles
from tokenvault.adapter.strings import t
from tokenvault.adapter.watcher import set_background_priority
from tokenvault.common import dpapi
from tokenvault.common.config_payload import read_payload

log = logging.getLogger("tokenvault.adapter.main")

EXIT_OK, EXIT_CRASH, EXIT_NO_PAYLOAD, EXIT_DECLINED, EXIT_NO_UI, EXIT_DPAPI = 0, 1, 2, 3, 4, 5
LOG_BYTES, LOG_BACKUPS = 1_000_000, 3
RELOAD_WAIT_S = 10.0
_SUBCOMMANDS = ("run", "setup", "status", "about", "show")
_ATTACH_PARENT_PROCESS = 0xFFFFFFFF
_log_handler: logging.Handler | None = None


# ------------------------------------------------------------------- console
def attach_parent_console(
    kernel32: Any = None,
    opener: Callable[..., Any] = open,
    *,
    frozen: bool | None = None,
    platform: str | None = None,
) -> bool:
    """Windowed exe (no stdout/stderr): attach to the parent console so ``status``,
    ``about`` and setup messages are visible. No-op when output already goes
    somewhere (console or redirect), when not frozen, or off Windows."""
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if (platform or sys.platform) != "win32" or not is_frozen:
        return False
    if sys.stdout is not None and sys.stderr is not None:
        return False
    try:
        k = kernel32 if kernel32 is not None else __import__("ctypes").windll.kernel32
        if not k.AttachConsole(_ATTACH_PARENT_PROCESS):
            return False
        try:
            k.SetConsoleOutputCP(65001)
        except Exception:  # noqa: BLE001 - best effort
            pass
        out = opener("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        if sys.stdout is None:
            sys.stdout = out
        if sys.stderr is None:
            sys.stderr = out
        if sys.stdin is None:
            try:
                sys.stdin = opener("CONIN$", "r", encoding="utf-8", errors="replace")
            except OSError:
                pass
        return True
    except Exception:  # noqa: BLE001 - never fail a command over console plumbing
        return False


def _out(text: str, stream: TextIO | None = None) -> None:
    s = stream if stream is not None else sys.stdout
    if s is None:  # windowed exe without a console
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


# ------------------------------------------------------------------- logging
def _start_file_logging(files: DataFiles) -> None:
    global _log_handler
    _stop_file_logging()
    try:
        files.root.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(
            files.log, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8", delay=True
        )
    except OSError as e:
        _err(f"log: {e}")
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


# ------------------------------------------------------------------- helpers
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


def _common_args(args: argparse.Namespace) -> list[str]:
    out: list[str] = []
    if args.data_dir:
        out += ["--data-dir", str(Path(args.data_dir).resolve())]
    if args.claude_dir:
        out += ["--claude-dir", str(Path(args.claude_dir).resolve())]
    if args.lang:
        out += ["--lang", args.lang]
    if getattr(args, "no_tray", False):
        out.append("--no-tray")
    return out


def agent_command(args: argparse.Namespace) -> list[str]:
    """argv that starts the installed agent (also what HKCU Run points at)."""
    return [*paths.self_argv(windowed=True), "run", *_common_args(args)]


def launch_detached(cmd: list[str]) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(  # argv list -> list2cmdline quoting
        cmd, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _stop_old_agent() -> None:
    """Ask a running agent to stop and wait (bounded) until it has released the mutex.

    Must happen BEFORE credentials change. Even if it does not stop in time, it
    cannot misuse the new secret: the secret is bound to the new Manager.
    """
    mutex = singleton.mutex_name()
    signalled = singleton.signal_reload(singleton.reload_event_name(mutex))
    if (signalled or singleton.is_running(mutex)) and not singleton.wait_released(
        mutex, RELOAD_WAIT_S
    ):
        log.warning("old agent still running after %.0f s", RELOAD_WAIT_S)
        _err(t("setup.old_running", seconds=int(RELOAD_WAIT_S)))


# ------------------------------------------------------------------ commands
def cmd_setup(args: argparse.Namespace) -> int:
    src = Path(args.from_exe) if args.from_exe else Path(sys.executable)
    try:
        payload = read_payload(src)
    except OSError:
        payload = None
    if payload is None:
        _err(t("setup.no_payload", name=src.name))
        return EXIT_NO_PAYLOAD
    text = consent.render_text(consent.WIRE_FIELDS, payload.hosts)
    accepted = bool(args.accept) if args.silent else consent.ask_consent(text)
    if accepted is None:
        _err(t("setup.no_ui"))
        return EXIT_NO_UI
    if not accepted:
        _err(t("setup.declined"))
        return EXIT_DECLINED
    try:
        dpapi.protect(b"probe")  # fail before touching anything (or the old agent)
    except dpapi.DpapiError as e:
        _err(t("setup.dpapi", reason=e))
        return EXIT_DPAPI
    files = _files(args)
    _start_file_logging(files)
    _stop_old_agent()
    new = AdapterConfig.from_payload(payload)
    old = load_config(files.config)
    try:  # secret first: config.json is the commit point
        write_enroll_secret(files.enroll_secret, payload.secret, new)
    except dpapi.DpapiError as e:
        _err(t("setup.dpapi", reason=e))
        return EXIT_DPAPI
    if old is None or (old.manager_id, old.fingerprint) != (new.manager_id, new.fingerprint):
        log.info("new or different Manager (or unreadable old config): dropping old device key")
        delete_file(files.device_key)
        delete_file(files.state)  # stale last_good_host
    save_config(files.config, new)
    log.info("setup: config saved for team %r, Manager %s:%d", new.team, ",".join(new.hosts),
             new.port)
    cmd = agent_command(args)
    if not args.no_autostart:
        autostart.enable(cmd[0], args=cmd[1:])
        log.info("setup: autostart registered")
    if not args.no_start:
        launch_detached(cmd)
        log.info("setup: agent started")
    _out(t("setup.done"))
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    files = _files(args)
    _start_file_logging(files)  # before any risky work, so a crash is recorded
    inst = singleton.SingleInstance(singleton.mutex_name())
    if not inst.acquire():
        return EXIT_OK  # another agent of this Windows session is running: quietly leave
    try:
        set_background_priority()
        try:
            app = AdapterApp(files, _projects_dir(args), identity=_identity(args),
                             tray=not args.no_tray, child_args=_common_args(args)
                             or ["--data-dir", str(files.root)])
        except NotConfigured as e:
            log.error("%s", e)
            _err(str(e))
            return EXIT_NO_PAYLOAD
        return int(app.run())
    except Exception as e:
        log.exception("agent crashed")
        reason = status_view.sanitize(f"{type(e).__name__}: {e}")
        status_view.write_state(files, "crashed", reason)
        _err(t("run.crashed", reason=reason))
        return EXIT_CRASH
    finally:
        if _log_handler is not None:
            _log_handler.flush()
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


def cmd_show(args: argparse.Namespace) -> int:
    """Window for the tray (child process). Reads config/outbox read-only."""
    files = _files(args)
    if args.what == "what-is-sent":
        cfg = load_config(files.config)
        sample = status_view.read_outbox(files.outbox).sample
        title = t("wis.window_title")
        text = status_view.what_is_sent_text(sample, list(cfg.hosts) if cfg else [])
    else:
        title = t("about.window_title")
        text = status_view.about_text(_identity(args), files)
    if args.print_only:
        _out(text)
        return EXIT_OK
    return status_view.show_text_window(title, text)


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", help="data folder (default %%LOCALAPPDATA%%\\TokenVault\\"
                        "Adapter)")
    common.add_argument("--claude-dir", help="Claude config folder that contains 'projects'")
    common.add_argument("--no-tray", action="store_true", help="run without the tray icon")
    common.add_argument("--lang", choices=strings.LANGS, help="vi (default) or en")
    p = argparse.ArgumentParser(prog="TokenVaultAdapter", description="TokenVault Adapter")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("run", parents=[common], help="background agent (default)")
    s = sub.add_parser("setup", parents=[common], help="first run from the installer")
    s.add_argument("--from", dest="from_exe", help="installer exe carrying the payload")
    s.add_argument("--silent", action="store_true", help="no consent window")
    s.add_argument("--accept", action="store_true", help="with --silent: consent given")
    s.add_argument("--no-autostart", action="store_true", help="do not register HKCU Run")
    s.add_argument("--no-start", action="store_true", help="do not start the agent")
    st = sub.add_parser("status", parents=[common], help="show status and queue")
    st.add_argument("--json", action="store_true", help="JSON incl. one queued event")
    sub.add_parser("about", parents=[common], help="version information")
    sh = sub.add_parser("show", parents=[common], help="window opened from the tray")
    sh.add_argument("what", choices=("what-is-sent", "about"))
    sh.add_argument("--print", dest="print_only", action="store_true", help=argparse.SUPPRESS)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list[0] not in (*_SUBCOMMANDS, "-h", "--help"):
        args_list.insert(0, "run")
    if args_list[0] not in ("run", "show"):
        attach_parent_console()
    args = build_parser().parse_args(args_list)
    strings.set_lang(args.lang)
    handlers = {"run": cmd_run, "setup": cmd_setup, "status": cmd_status, "about": cmd_about,
                "show": cmd_show}
    try:
        return handlers[args.command](args)
    finally:
        _stop_file_logging()
        strings.set_lang(None)


if __name__ == "__main__":
    sys.exit(main())
