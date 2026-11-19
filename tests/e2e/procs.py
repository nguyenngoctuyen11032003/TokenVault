"""Process plumbing for the E2E harness: real child processes, their env, metrics, API.

Children run on the BASE interpreter of the venv (``sys._base_executable``) with
``PYTHONPATH=<worktree>/src;<venv site-packages>``: the venv's ``python.exe`` on
Windows is a launcher that starts the real interpreter as a CHILD, so its pid would
be the wrong one to kill or measure.
"""

from __future__ import annotations

import ctypes
import json
import os
import site
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SRC = REPO / "src"


def child_python() -> str:
    return getattr(sys, "_base_executable", None) or sys.executable


def child_pythonpath() -> str:
    paths = [str(SRC)]
    for p in site.getsitepackages():
        if p.lower().endswith("site-packages"):
            paths.append(p)
    return os.pathsep.join(dict.fromkeys(paths))


def base_env() -> dict[str, str]:
    env = dict(os.environ)
    for k in ("CLAUDE_CONFIG_DIR", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP"):
        env.pop(k, None)
    env["PYTHONPATH"] = child_pythonpath()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def spawn(args: list[str], *, env: dict[str, str] | None = None, log: Path | None = None,
          pipe_stdout: bool = False, stdin_pipe: bool = False) -> subprocess.Popen[Any]:
    out: Any = subprocess.PIPE if pipe_stdout else (open(log, "ab") if log else subprocess.DEVNULL)  # noqa: SIM115
    err: Any = open(log, "ab") if log else subprocess.DEVNULL  # noqa: SIM115
    return subprocess.Popen(
        [child_python(), "-s", *args], env=env or base_env(), stdout=out, stderr=err,
        stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL, cwd=str(REPO),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def read_json_line(proc: subprocess.Popen[Any], timeout: float = 60.0) -> dict[str, Any]:
    box: dict[str, Any] = {}

    def rd() -> None:
        assert proc.stdout is not None
        box["line"] = proc.stdout.readline()

    t = threading.Thread(target=rd, daemon=True)
    t.start()
    t.join(timeout)
    line = box.get("line")
    if not line:
        raise RuntimeError(f"child produced no ready line (exit={proc.poll()})")
    return json.loads(line.decode("utf-8"))


def run(args: list[str], *, env: dict[str, str] | None = None, timeout: float = 120
        ) -> subprocess.CompletedProcess[str]:
    return subprocess.run([child_python(), "-s", *args], env=env or base_env(), cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL)


def wait_until(pred: Any, timeout: float, interval: float = 0.1) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001 - keep polling (e.g. Manager restarting)
            pass
        time.sleep(interval)
    try:
        return bool(pred())
    except Exception:  # noqa: BLE001
        return False


class ApiError(RuntimeError):
    pass


class ManagerApi:
    """``POST /api/<method>`` against the Manager's loopback UI server (with its token)."""

    def __init__(self, url: str, token: str) -> None:
        self.url, self.token = url, token
        self.calls = 0

    def __call__(self, method: str, *args: Any, timeout: float = 60) -> Any:
        self.calls += 1
        req = urllib.request.Request(
            f"{self.url}/api/{method}", data=json.dumps(list(args)).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-TV-Token": self.token},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - loopback
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ApiError(f"{method}: HTTP {e.code} {e.read()[:300]!r}") from None

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **kw: self(name, *a, **kw)


# ---------------------------------------------------------------- Windows metrics
class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


def _handle(proc: subprocess.Popen[Any]) -> int:
    return int(proc._handle)  # type: ignore[attr-defined]  # noqa: SLF001


def cpu_seconds(proc: subprocess.Popen[Any]) -> float:
    k = ctypes.windll.kernel32
    c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
    if not k.GetProcessTimes(wintypes.HANDLE(_handle(proc)), ctypes.byref(c), ctypes.byref(e),
                             ctypes.byref(kt), ctypes.byref(ut)):
        raise OSError("GetProcessTimes failed")

    def v(ft: wintypes.FILETIME) -> int:
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    return (v(kt) + v(ut)) / 1e7


def memory(proc: subprocess.Popen[Any]) -> tuple[int, int]:
    """(working set bytes, private bytes)."""
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(wintypes.HANDLE(_handle(proc)),
                                                     ctypes.byref(pmc), pmc.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return int(pmc.WorkingSetSize), int(pmc.PrivateUsage)


def stop(proc: subprocess.Popen[Any] | None, timeout: float = 10) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.kill()
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        pass
