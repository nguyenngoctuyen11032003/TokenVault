"""Embedded Unicode TTF fonts for the PDF (reportlab's base-14 fonts cannot draw Vietnamese).

Search order: ``$TOKENVAULT_REPORT_FONT_DIR/*.ttf``, then the Windows fonts folder
(Segoe UI, Arial, Tahoma, Calibri), then DejaVu Sans in a few well-known places.
A family is accepted only when its regular face has a glyph for every Vietnamese
letter (both cases); a bold face is used only when it qualifies too, otherwise the
regular face doubles as "bold" (emphasis then comes from size/colour, never from
faked glyphs). The registration is cached and thread-safe.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from tokenvault.manager.reports.errors import ReportFontError

ENV_VAR = "TOKENVAULT_REPORT_FONT_DIR"
VI_SAMPLE = (
    "ạ ả ã à á ă ằ ắ ẳ ẵ ặ â ầ ấ ẩ ẫ ậ đ ê ề ế ể ễ ệ ô ồ ố ổ ỗ ộ ơ ờ ớ ở ỡ ợ ư ừ ứ ử ữ ự ỳ ý ỷ ỹ ỵ"
)
# Characters the report itself prints besides letters and digits.
_REQUIRED = VI_SAMPLE + VI_SAMPLE.upper() + "0123456789$%+-–—·…/()<>&?“”"
WINDOWS_FAMILIES = (
    ("segoeui.ttf", "segoeuib.ttf"),
    ("arial.ttf", "arialbd.ttf"),
    ("tahoma.ttf", "tahomabd.ttf"),
    ("calibri.ttf", "calibrib.ttf"),
)
_BOLD_SUFFIXES = ("b", "bd", "-bold", "bold", "_bold", " bold")
_DEFAULT = object()

_lock = threading.Lock()
_cached: FontFamily | None = None
_cmaps: dict[str, frozenset[int]] = {}


@dataclass(frozen=True)
class FontFamily:
    regular: str  # registered reportlab font name
    bold: str  # == regular when no qualifying bold face exists
    regular_path: Path
    bold_path: Path | None


def _load(path: Path) -> TTFont | None:
    digest = hashlib.sha1(str(path).lower().encode("utf-8")).hexdigest()[:10]
    try:
        return TTFont(f"TV-{digest}", str(path))
    except Exception:  # unreadable / unsupported file: skip it
        return None


def _covers_font(font: TTFont, text: str) -> bool:
    cmap = font.face.charToGlyph
    return all(ord(c) in cmap for c in text if not c.isspace())


def _register(font: TTFont) -> str:
    if font.fontName not in _cmaps:
        pdfmetrics.registerFont(font)
        _cmaps[font.fontName] = frozenset(font.face.charToGlyph)
    return str(font.fontName)


def _try(regular: Path, bold: Path | None) -> FontFamily | None:
    if not regular.is_file():
        return None
    reg = _load(regular)
    if reg is None or not _covers_font(reg, _REQUIRED):
        return None
    bold_font = _load(bold) if bold is not None and bold.is_file() else None
    if bold_font is not None and not _covers_font(bold_font, _REQUIRED):
        bold_font = None
    reg_name = _register(reg)
    bold_name = _register(bold_font) if bold_font is not None else reg_name
    addMapping(reg_name, 0, 0, reg_name)
    addMapping(reg_name, 1, 0, bold_name)
    addMapping(reg_name, 0, 1, reg_name)
    addMapping(reg_name, 1, 1, bold_name)
    return FontFamily(reg_name, bold_name, regular, bold if bold_font is not None else None)


def _dir_candidates(folder: Path) -> list[tuple[Path, Path | None]]:
    try:
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".ttf" and p.is_file())
    except OSError:
        return []
    by_stem = {p.stem.lower(): p for p in files}
    bold_of: dict[str, Path] = {}
    bolds: set[Path] = set()
    for stem, p in by_stem.items():
        for suf in _BOLD_SUFFIXES:
            if stem.endswith(suf) and stem[: -len(suf)] in by_stem:
                bold_of.setdefault(stem[: -len(suf)], p)
                bolds.add(p)
                break
    return [(p, bold_of.get(p.stem.lower())) for p in files if p not in bolds]


def _dejavu_dirs() -> list[Path]:
    out: list[Path] = []
    spec = importlib.util.find_spec("matplotlib")
    if spec is not None and spec.origin:
        out.append(Path(spec.origin).parent / "mpl-data" / "fonts" / "ttf")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    out += [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu")]
    return out


def _system_candidates() -> list[tuple[Path, Path | None]]:
    win = Path(os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows")
    fonts_dir = win / "Fonts"
    out: list[tuple[Path, Path | None]] = [
        (fonts_dir / r, fonts_dir / b) for r, b in WINDOWS_FAMILIES
    ]
    for d in [fonts_dir, *_dejavu_dirs()]:
        out.append((d / "DejaVuSans.ttf", d / "DejaVuSans-Bold.ttf"))
    return out


def discover(env_dir: Path | str | None | object = _DEFAULT, *, system: bool = True) -> FontFamily:
    """Find, verify and register the first Vietnamese-capable family (no caching).

    ``env_dir`` defaults to ``$TOKENVAULT_REPORT_FONT_DIR``; ``system=False`` skips the
    Windows / DejaVu locations (tests).
    """
    if env_dir is _DEFAULT:
        env_dir = os.environ.get(ENV_VAR) or None
    candidates: list[tuple[Path, Path | None]] = []
    if isinstance(env_dir, (str, Path)) and str(env_dir):
        candidates += _dir_candidates(Path(env_dir))
    if system:
        candidates += _system_candidates()
    for regular, bold in candidates:
        fam = _try(regular, bold)
        if fam is not None:
            return fam
    raise ReportFontError(
        "Không tìm thấy phông chữ hỗ trợ tiếng Việt để tạo PDF. Hãy chép một tệp phông .ttf "
        "(ví dụ segoeui.ttf + segoeuib.ttf, arial.ttf + arialbd.ttf hoặc DejaVuSans.ttf) vào "
        f"một thư mục rồi đặt biến môi trường {ENV_VAR} trỏ tới thư mục đó. / "
        "No font able to render Vietnamese was found: copy a .ttf font (e.g. segoeui.ttf + "
        f"segoeuib.ttf, arial.ttf or DejaVuSans.ttf) into a folder and set {ENV_VAR} to it."
    )


def ensure_fonts() -> FontFamily:
    """The process-wide report font family (discovered and registered once)."""
    global _cached
    with _lock:
        if _cached is None:
            _cached = discover()
        return _cached


def reset_cache_for_tests() -> None:
    global _cached
    with _lock:
        _cached = None


def covers(font_name: str, text: str) -> bool:
    cmap = _cmaps.get(font_name, frozenset())
    return all(ord(c) in cmap for c in text if not c.isspace())


def clean(text: object, font_name: str) -> str:
    """Untrusted text -> one printable line: NFC, controls dropped (newlines/tabs become
    spaces) and characters the font has no glyph for replaced by ``?``."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFC", str(text))
    cmap = _cmaps.get(font_name, frozenset())
    out: list[str] = []
    for c in s:
        cat = unicodedata.category(c)
        if c in "\r\n\t":
            out.append(" ")
        elif cat in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        elif c == " " or ord(c) in cmap:
            out.append(c)
        elif cat.startswith("M"):  # lone combining mark without a glyph: drop
            continue
        else:
            out.append("?")
    return "".join(out)
