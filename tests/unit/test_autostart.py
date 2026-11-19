from __future__ import annotations

from typing import Any

import pytest

from tokenvault.adapter import autostart


class FakeRegistry:
    def __init__(self) -> None:
        self.values: dict[str, str] = {"OtherApp": '"C:\\other.exe"'}
        self.writes: list[str] = []

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.writes.append(name)
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.writes.append(name)
        self.values.pop(name, None)


def test_autostart_writes_hkcu_run_value_only() -> None:
    reg = FakeRegistry()
    assert not autostart.is_enabled(reg)
    exe = r"C:\Users\Nguyễn Văn A\AppData\Local\Programs\TokenVault Adapter\TokenVaultAdapter.exe"
    autostart.enable(exe, reg)
    assert reg.writes == ["TokenVaultAdapter"]
    assert reg.values["TokenVaultAdapter"] == f'"{exe}" run'
    assert reg.values["OtherApp"] == '"C:\\other.exe"'
    assert autostart.is_enabled(reg)
    autostart.disable(reg)
    assert reg.writes == ["TokenVaultAdapter", "TokenVaultAdapter"]
    assert "TokenVaultAdapter" not in reg.values
    assert not autostart.is_enabled(reg)
    autostart.disable(reg)  # idempotent


def test_enable_with_extra_args() -> None:
    reg = FakeRegistry()
    autostart.enable(r"C:\py\pythonw.exe", reg, args='-m tokenvault.adapter.main run')
    assert reg.values["TokenVaultAdapter"] == '"C:\\py\\pythonw.exe" -m tokenvault.adapter.main run'


class FakeKey:
    def __enter__(self) -> FakeKey:
        return self

    def __exit__(self, *a: Any) -> None:
        pass


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    KEY_QUERY_VALUE = 1
    REG_SZ = 1

    def __init__(self) -> None:
        self.opened: list[tuple[Any, str]] = []
        self.data: dict[str, str] = {}

    def CreateKeyEx(self, root: Any, sub: str, reserved: int, access: int) -> FakeKey:  # noqa: N802
        self.opened.append((root, sub))
        return FakeKey()

    def OpenKey(self, root: Any, sub: str, reserved: int, access: int) -> FakeKey:  # noqa: N802
        self.opened.append((root, sub))
        return FakeKey()

    def SetValueEx(self, key: Any, name: str, r: int, typ: int, value: str) -> None:  # noqa: N802
        self.data[name] = value

    def QueryValueEx(self, key: Any, name: str) -> tuple[str, int]:  # noqa: N802
        if name not in self.data:
            raise FileNotFoundError(name)
        return self.data[name], 1

    def DeleteValue(self, key: Any, name: str) -> None:  # noqa: N802
        if name not in self.data:
            raise FileNotFoundError(name)
        del self.data[name]


def test_windows_registry_uses_hkcu_run_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeWinreg()
    monkeypatch.setattr(autostart, "_winreg", lambda: fake)
    autostart.enable(r"C:\a b\x.exe")
    assert fake.data == {"TokenVaultAdapter": '"C:\\a b\\x.exe" run'}
    assert autostart.is_enabled()
    autostart.disable()
    autostart.disable()
    assert fake.data == {}
    assert not autostart.is_enabled()
    assert {o for o in fake.opened} == {
        ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run")
    }
