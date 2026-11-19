"""Start the Adapter at login via ONE value in HKCU\\...\\Run (no admin needed).

Only the value ``TokenVaultAdapter`` under
``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` is ever written or
deleted. The registry is injectable for tests (anything with get/set/delete).
"""

from __future__ import annotations

from typing import Any, Protocol

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "TokenVaultAdapter"


class RunRegistry(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


def _winreg() -> Any:
    import winreg

    return winreg


class WinRunKey:
    """HKCU Run key via ``winreg``."""

    def get(self, name: str) -> str | None:
        wr = _winreg()
        try:
            with wr.OpenKey(wr.HKEY_CURRENT_USER, RUN_KEY, 0, wr.KEY_QUERY_VALUE) as key:
                value, _ = wr.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        return value if isinstance(value, str) else None

    def set(self, name: str, value: str) -> None:
        wr = _winreg()
        with wr.CreateKeyEx(wr.HKEY_CURRENT_USER, RUN_KEY, 0, wr.KEY_SET_VALUE) as key:
            wr.SetValueEx(key, name, 0, wr.REG_SZ, value)

    def delete(self, name: str) -> None:
        wr = _winreg()
        try:
            with wr.OpenKey(wr.HKEY_CURRENT_USER, RUN_KEY, 0, wr.KEY_SET_VALUE) as key:
                wr.DeleteValue(key, name)
        except FileNotFoundError:
            pass


def command_line(exe_path: str, args: str = "run") -> str:
    return f'"{exe_path}" {args}'.rstrip()


def enable(exe_path: str, registry: RunRegistry | None = None, args: str = "run") -> None:
    (registry or WinRunKey()).set(VALUE_NAME, command_line(str(exe_path), args))


def disable(registry: RunRegistry | None = None) -> None:
    (registry or WinRunKey()).delete(VALUE_NAME)


def is_enabled(registry: RunRegistry | None = None) -> bool:
    return bool((registry or WinRunKey()).get(VALUE_NAME))
