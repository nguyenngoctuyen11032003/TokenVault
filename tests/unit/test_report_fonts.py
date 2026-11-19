from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path

import pytest
import reportlab

from tokenvault.manager.reports import fonts
from tokenvault.manager.reports.errors import ReportFontError

VERA = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"


def test_vietnamese_font_found_and_registered() -> None:
    fam = fonts.ensure_fonts()
    assert fam.regular and fam.bold
    assert fonts.covers(fam.regular, fonts.VI_SAMPLE + fonts.VI_SAMPLE.upper())
    assert fonts.ensure_fonts() is fam  # cached


@pytest.mark.skipif(sys.platform != "win32", reason="Windows font folder")
def test_windows_prefers_segoe_ui() -> None:
    fam = fonts.discover(env_dir=None)
    assert fam.regular_path.name.lower() == "segoeui.ttf"
    assert fam.bold_path is not None and fam.bold_path.name.lower() == "segoeuib.ttf"


def test_limited_font_is_rejected_with_clear_message(tmp_path: Path) -> None:
    shutil.copy(VERA, tmp_path / "Vera.ttf")
    with pytest.raises(ReportFontError) as exc:
        fonts.discover(env_dir=tmp_path, system=False)
    msg = str(exc.value)
    assert "TOKENVAULT_REPORT_FONT_DIR" in msg and "tiếng Việt" in msg and ".ttf" in msg
    assert "Vietnamese" in msg


def test_env_dir_font_is_used_first(tmp_path: Path) -> None:
    src = fonts.discover(env_dir=None).regular_path
    shutil.copy(src, tmp_path / "custom.ttf")
    shutil.copy(VERA, tmp_path / "aaa-limited.ttf")  # skipped: no Vietnamese glyphs
    fam = fonts.discover(env_dir=tmp_path, system=False)
    assert fam.regular_path == tmp_path / "custom.ttf"
    assert fam.bold_path is None  # no bold file: regular is reused, never fake glyphs


def test_unreadable_font_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "broken.ttf").write_bytes(b"not a font")
    with pytest.raises(ReportFontError):
        fonts.discover(env_dir=tmp_path, system=False)


def test_clean_replaces_missing_glyphs_and_controls() -> None:
    fam = fonts.ensure_fonts()
    out = fonts.clean("Phiên 😀 ok\x00\x07\nnext", fam.regular)
    assert "😀" not in out and "?" in out and "\x00" not in out and "\n" not in out
    assert fonts.clean("Tiếng Việt đầy đủ", fam.regular) == "Tiếng Việt đầy đủ"
    assert fonts.clean(None, fam.regular) == ""


def test_registration_is_thread_safe() -> None:
    fonts.reset_cache_for_tests()
    got: list[object] = []
    threads = [threading.Thread(target=lambda: got.append(fonts.ensure_fonts())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 8 and all(g is got[0] for g in got)
