"""Start with Windows: ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``.

The value starts the Manager hidden in the tray (``--minimized``). Per user, no
elevation. Only meaningful for the frozen exe; in development the command uses
``pythonw -m tokenvault.manager.app``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "TokenVault Manager"


def command() -> str:
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--minimized"])
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return subprocess.list2cmdline(
        [str(pyw if pyw.exists() else exe), "-m", "tokenvault.manager.app", "--minimized"]
    )


def set_autostart(enabled: bool) -> None:
    """Raises OSError when the registry cannot be written."""
    if sys.platform != "win32":
        raise OSError("start with Windows is only available on Windows")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, VALUE)
            except FileNotFoundError:
                pass


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, VALUE)
            return True
    except OSError:
        return False
