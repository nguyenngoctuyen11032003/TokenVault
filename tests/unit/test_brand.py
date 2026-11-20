"""Brand assets: package copy in sync with assets/logo, lookups, tray image, window icon."""

from __future__ import annotations

import filecmp
from pathlib import Path
from typing import Any

import pytest

from tokenvault.adapter import tray as adapter_tray
from tokenvault.common import brand
from tokenvault.manager import tray as manager_tray
from tokenvault.manager.settings_store import SettingsStore
from tokenvault.manager.window_native import (
    ICON_BIG,
    ICON_SMALL,
    WM_SETICON,
    apply_window_icon,
)
from tokenvault.manager.window_shell import WindowShell

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "src" / "tokenvault" / "ui" / "assets" / "logo"
SRC = ROOT / "assets" / "logo"


def test_package_logo_copy_matches_source_of_truth() -> None:
    names = [p.name for p in SRC.iterdir() if p.is_file() and p.name != "preview.png"]
    assert "tokenvault.ico" in names and "tokenvault-mark-small.svg" in names
    for n in names:
        assert (PKG / n).is_file(), f"{n} missing in the package copy (run: cp assets/logo/*)"
        assert filecmp.cmp(SRC / n, PKG / n, shallow=False), f"{n} differs from assets/logo"


def test_lookups_resolve_and_pick_png_size() -> None:
    assert brand.logo_dir() == PKG
    assert brand.icon_ico() is not None and brand.wordmark_png() is not None
    assert brand.logo_png(16).name == "tokenvault-16.png"
    assert brand.logo_png(20).name == "tokenvault-24.png"
    assert brand.logo_png(9999).name == "tokenvault-512.png"


def test_frozen_lookup_uses_meipass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    assert brand.logo_dir() == tmp_path / "tokenvault" / "ui" / "assets" / "logo"
    assert brand.icon_ico() is None  # missing file -> None, no exception


@pytest.mark.parametrize("size", [16, 32, 64, 100])
def test_tray_image_size_and_dot(size: int) -> None:
    plain = brand.tray_image(size)
    marked = brand.tray_image(size, (0, 200, 0))
    assert plain.size == marked.size == (size, size)
    r = max(3, round(size * 0.20))
    c = size - r - max(1, round(size / 32))
    assert marked.getpixel((c, c)) == (0, 200, 0, 255)
    assert plain.getpixel((c, c)) != (0, 200, 0, 255)


def test_missing_logo_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(brand, "logo_dir", lambda: tmp_path)
    assert brand.tray_image(64, (1, 2, 3)) is None


def test_corrupt_logo_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "tokenvault-64.png").write_bytes(b"not a png")
    monkeypatch.setattr(brand, "logo_dir", lambda: tmp_path)
    assert brand.tray_image(64) is None


def test_adapter_tray_icon_uses_logo_and_state_colour() -> None:
    img = adapter_tray.make_icon("connected")
    assert img.size == (64, 64)
    assert img.getpixel((49, 49))[:3] == adapter_tray._STATE_COLOR["connected"]
    assert adapter_tray.make_icon("offline").getpixel((49, 49))[:3] == (128, 128, 128)
    assert img.getpixel((8, 8))[3] > 0  # the logo's tile, not the old transparent corner


def test_adapter_tray_falls_back_to_drawn_icon(monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
    monkeypatch.setattr(brand, "logo_dir", lambda: tmp_path)
    img = adapter_tray.make_icon("connected")
    assert img.size == (64, 64)
    assert img.getpixel((32, 32))[:3] == adapter_tray._STATE_COLOR["connected"]  # old centre dot


def test_manager_tray_icon_uses_logo_and_falls_back(monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path) -> None:
    assert manager_tray.icon_image(48).size == (48, 48)
    assert manager_tray.icon_image(48).getpixel((24, 24)) != (225, 11, 31, 255)
    monkeypatch.setattr(brand, "logo_dir", lambda: tmp_path)
    fb = manager_tray.icon_image(64)
    assert fb.size == (64, 64) and fb.getpixel((5, 63 - 40)) == (225, 11, 31, 255)


class FakeUser32:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.loaded: list[tuple[Any, ...]] = []
        self.sent: list[tuple[int, int, int]] = []

    def GetSystemMetrics(self, i: int) -> int:  # noqa: N802
        return {11: 32, 12: 32, 49: 16, 50: 16}[i]

    def LoadImageW(self, *a: Any) -> int:  # noqa: N802
        self.loaded.append(a)
        return 0 if self.fail else 1000 + len(self.loaded)

    def SendMessageW(self, _h: Any, msg: int, kind: int, hicon: int) -> int:  # noqa: N802
        self.sent.append((msg, kind, hicon))
        return 0


def test_apply_window_icon_sets_big_and_small() -> None:
    u = FakeUser32()
    assert apply_window_icon(42, "x.ico", u) is True
    assert [(m, k) for m, k, _ in u.sent] == [(WM_SETICON, ICON_BIG), (WM_SETICON, ICON_SMALL)]
    assert (u.loaded[0][3], u.loaded[1][3]) == (32, 16)  # sized to the system metrics
    assert all(a[1] == "x.ico" for a in u.loaded)


def test_apply_window_icon_reports_failure() -> None:
    u = FakeUser32(fail=True)
    assert apply_window_icon(42, "missing.ico", u) is False
    assert u.sent == []


def test_shell_applies_icon_on_shown_and_survives_errors(tmp_path: Path) -> None:
    shell = WindowShell(SettingsStore(tmp_path / "s.json"), "http://x/", object())
    seen: list[str] = []

    class N:
        def set_icon(self, p: str) -> None:
            seen.append(p)

    shell._native = N()  # type: ignore[assignment]
    shell._apply_icon()
    assert seen and seen[0].endswith("tokenvault.ico")

    class Bad:
        def set_icon(self, p: str) -> None:
            raise OSError("boom")

    shell._native = Bad()  # type: ignore[assignment]
    shell._apply_icon()  # must not raise


def test_packaging_references_the_logo() -> None:
    import plistlib
    import struct

    pk = ROOT / "packaging"
    for spec in ("adapter.spec", "manager.spec"):
        assert "tokenvault.ico" in (pk / spec).read_text(encoding="utf-8"), spec
    assert "ui/assets/logo" in (pk / "adapter.spec").read_text(encoding="utf-8")
    for iss in ("adapter-template.iss", "manager.iss"):
        t = (pk / iss).read_text(encoding="utf-8")
        for key in ("SetupIconFile=", "WizardImageFile=", "WizardSmallImageFile=",
                    "UninstallDisplayIcon="):
            assert key in t, (iss, key)
    for name, size in (("wizard-large.bmp", (164, 314)), ("wizard-small.bmp", (55, 58))):
        raw = (pk / "brand" / name).read_bytes()
        assert raw[:2] == b"BM" and struct.unpack("<ii", raw[18:26]) == size
    plist = plistlib.loads((pk / "mac" / "Info.plist").read_bytes())
    assert plist["CFBundleIconFile"] == "tokenvault"
    assert "iconutil" in (ROOT / "build" / "build-mac.sh").read_text(encoding="utf-8")
    assert "tokenvault.ico" in (pk / "portable" / "Install-Manager.ps1").read_text("utf-8")
