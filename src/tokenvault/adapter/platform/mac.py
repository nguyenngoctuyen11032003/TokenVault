"""macOS pieces of the Adapter: paths, LaunchAgent, osascript consent, priority.

Import-safe on every OS (no mac-only import at module level). Everything that
touches the system takes an injectable runner/function so it is unit-tested with
fakes; real-Mac behaviour is covered by the manual checklist.
"""

from __future__ import annotations

import contextlib
import logging
import os
import plistlib
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger("tokenvault.adapter.mac")

LABEL = "vn.ics.tokenvault.adapter"
PRIO_DARWIN_PROCESS = 4
PRIO_DARWIN_BG = 0x1000
RUN_TIMEOUT_S = 20
BOOTSTRAP_TRIES = 5

#: runner(argv, stdin) -> (returncode, stdout, stderr). Never logs its inputs.
Runner = Callable[[Sequence[str], bytes | None], tuple[int, bytes, bytes]]


def run_command(argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
    """Default runner: no shell, captured output, bounded time."""
    p = subprocess.run(  # noqa: S603 - fixed argv, no shell
        list(argv), input=stdin, capture_output=True, timeout=RUN_TIMEOUT_S, check=False
    )
    return p.returncode, p.stdout, p.stderr


def _posix(name: str) -> Any:
    """A POSIX-only ``os`` function (absent on Windows, so looked up lazily)."""
    return getattr(os, name)


# ------------------------------------------------------------------ paths
def data_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "Application Support" / "TokenVault"


def log_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "Logs" / "TokenVault"


def launch_agents_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents"


def plist_path(home: Path | None = None) -> Path:
    return launch_agents_dir(home) / f"{LABEL}.plist"


# ------------------------------------------------------------- LaunchAgent
def plist_bytes(program_args: Sequence[str], logs: Path) -> bytes:
    """The LaunchAgent plist: start at login, restart only after a crash."""
    d: dict[str, object] = {
        "Label": LABEL,
        "ProgramArguments": [str(a) for a in program_args],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # clean exit (reload/quit) stays stopped
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(logs / "adapter.out.log"),
        "StandardErrorPath": str(logs / "adapter.err.log"),
    }
    return plistlib.dumps(d, fmt=plistlib.FMT_XML, sort_keys=True)


class LaunchAgent:
    def __init__(self, home: Path | None = None, uid: int | None = None,
                 runner: Runner | None = None,
                 sleep: Callable[[float], None] | None = None) -> None:
        self.home = home
        self._uid = uid
        self.run = runner or run_command
        self._sleep = sleep or time.sleep

    @property
    def uid(self) -> int:
        return self._uid if self._uid is not None else int(_posix('getuid')())

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    @property
    def path(self) -> Path:
        return plist_path(self.home)

    def enable(self, exe: str, args: Sequence[str] = ("run",)) -> None:
        logs = log_dir(self.home)
        logs.mkdir(parents=True, exist_ok=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = plist_bytes([exe, *args], logs)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, self.path)
        self.run(["launchctl", "bootout", f"{self.domain}/{LABEL}"], None)  # may not be loaded
        # bootout is asynchronous: an immediate bootstrap can fail with "Input/output error"
        # while the old job is still going away, so retry with backoff.
        rc, err = 1, b""
        for attempt in range(BOOTSTRAP_TRIES):
            rc, _, err = self.run(["launchctl", "bootstrap", self.domain, str(self.path)], None)
            if rc == 0:
                return
            self._sleep(0.25 * (2 ** attempt))
        # The plist is in place, so it still starts at next login.
        log.warning("launchctl bootstrap failed (%d): %s", rc,
                    err.decode("utf-8", "replace").strip()[:200])

    def disable(self) -> None:
        self.run(["launchctl", "bootout", f"{self.domain}/{LABEL}"], None)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def is_enabled(self) -> bool:
        return self.path.is_file()


# ------------------------------------------------------------------ consent
_ASCRIPT = (
    "on run argv\n"
    "  set btns to items 3 thru -1 of argv\n"
    "  display dialog (item 1 of argv) with title (item 2 of argv) "
    "buttons btns default button 2 cancel button 1 with icon note\n"
    "end run\n"
)
_NOTIFY_SCRIPT = (
    "on run argv\n"
    "  display notification (item 1 of argv) with title (item 2 of argv)\n"
    "end run\n"
)


def short_consent_text(full: str, hint: str) -> str:
    """A dialog-sized version of the consent text: field names without their notes, and
    without the trailing tray/removal paragraphs; ``hint`` points at the full text."""
    blocks = full.split("\n\n")
    kept = blocks[:-2] if len(blocks) > 3 else blocks
    out: list[str] = []
    for line in "\n\n".join(kept).splitlines():
        if line.startswith("  - ") and ":" in line:
            line = line.split(":", 1)[0]
        out.append(line)
    return "\n".join(out) + "\n\n" + hint


def _button(out: bytes) -> str:
    """The pressed button from ``button returned:X, gave up:false``."""
    text = out.decode("utf-8", "replace").strip()
    if not text.startswith("button returned:"):
        return ""
    return text[len("button returned:"):].split(", gave up:")[0].strip()


def ask_consent(text: str, title: str, accept: str, decline: str, view: str = "",
                short: str | None = None, runner: Runner | None = None,
                opener: Runner | None = None) -> bool | None:
    """Consent dialog via ``osascript``. True = Accept, False = Decline/Esc, None = the
    dialog could not be shown (not a decline). A short text is shown first; the
    "view full" button opens the complete text in TextEdit (a private temp file) and the
    dialog is shown again. The text goes in as an argv item, never into the script."""
    run = runner or run_command
    open_run = opener or run_command
    two_step = short is not None and bool(view)
    shown = short if (two_step and short is not None) else text
    buttons = [decline, view, accept] if two_step else [decline, accept]
    tmpdir: str | None = None
    try:
        while True:
            argv = ["osascript", "-e", _ASCRIPT, shown, title, *buttons]
            try:
                rc, out, err = run(argv, None)
            except (OSError, subprocess.SubprocessError) as e:
                log.error("cannot show the consent dialog: %s", e)
                return None
            if rc == 0:
                pressed = _button(out)
                if pressed == accept:
                    return True
                if pressed == decline:
                    return False
                if view and pressed == view:
                    if tmpdir is None:
                        tmpdir = tempfile.mkdtemp(prefix="tokenvault-consent-")
                    path = Path(tmpdir) / "TokenVault-consent.txt"
                    path.write_text(text, encoding="utf-8")
                    try:
                        open_run(["open", "-t", str(path)], None)
                    except (OSError, subprocess.SubprocessError) as e:
                        log.error("cannot open the full consent text: %s", e)
                        return None
                    continue
                log.error("consent dialog returned an unexpected result")
                return None
            if rc == 1 and b"-128" in err:  # user cancelled: Esc / cancel button
                return False
            log.error("consent dialog failed (osascript exit %d)", rc)
            return None
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def notify(message: str, title: str, runner: Runner | None = None) -> None:
    """Best-effort macOS notification (never raises)."""
    try:
        (runner or run_command)(["osascript", "-e", _NOTIFY_SCRIPT, message, title], None)
    except (OSError, subprocess.SubprocessError):
        log.warning("cannot show a notification")


# ----------------------------------------------------------------- priority
def set_background_priority(
    nice: Callable[[int], object] | None = None,
    setpriority: Callable[[int, int, int], object] | None = None,
) -> bool:
    """Lower CPU priority (nice 10) and mark the process Darwin-background."""
    ok = False
    try:
        (nice or _posix('nice'))(10)
        ok = True
    except (OSError, AttributeError):
        log.warning("os.nice failed", exc_info=True)
    try:
        sp = setpriority or getattr(os, "setpriority", None)
        if sp is not None:
            sp(PRIO_DARWIN_PROCESS, 0, PRIO_DARWIN_BG)
            ok = True
    except (OSError, AttributeError):
        log.debug("darwin background policy not applied", exc_info=True)
    return ok


def env_home(env: Mapping[str, str] | None = None) -> Path | None:
    h = (env if env is not None else os.environ).get("HOME")
    return Path(h) if h else None
