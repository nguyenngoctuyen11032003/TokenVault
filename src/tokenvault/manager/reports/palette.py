"""Print palette: the UI's 8 series colours (same order, same FNV-1a home slot) darkened
until each reaches 3:1 contrast on white, plus the report's ink/accent tokens.

Colour never carries meaning alone: every chart has a legend and/or direct labels.
"""

from __future__ import annotations

from collections.abc import Sequence

UI_SERIES = (
    "#ff4d5e",  # red
    "#ffb020",  # amber
    "#2dd4bf",  # teal
    "#4da3ff",  # blue
    "#a78bfa",  # violet
    "#7ee787",  # green
    "#f472b6",  # pink
    "#94a3b8",  # slate
)
WHITE = "#ffffff"
ACCENT = "#e10b1f"  # red-hot brand accent (headings, rules)
INK = "#16181d"  # primary text
INK_2 = "#3f4550"  # secondary text
MUTED = "#6b7280"  # axis labels, captions (4.8:1 on white)
GRID = "#e6e8ec"
RULE = "#d4d7dd"
ZEBRA = "#f6f7f9"
HEAD_BG = "#fbe9eb"  # table header tint of the accent
CARD_BG = "#fafafb"
MIN_CONTRAST = 3.2  # margin over 3:1 for off-white paper


def _rgb(hex_: str) -> tuple[int, int, int]:
    v = int(hex_.lstrip("#"), 16)
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


def _hex(rgb: Sequence[float]) -> str:
    return "#" + "".join(f"{round(c):02x}" for c in rgb)


def luminance(hex_: str) -> float:
    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(v) for v in _rgb(hex_))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def darken_to(hex_: str, bg: str = WHITE, minimum: float = MIN_CONTRAST) -> str:
    """``hex_`` mixed toward black in 2% steps until it has ``minimum`` contrast on ``bg``."""
    base = _rgb(hex_)
    for i in range(51):
        mixed = _hex([c * (1 - i * 0.02) for c in base])
        if contrast(mixed, bg) >= minimum:
            return mixed
    return "#000000"


SERIES = tuple(darken_to(c) for c in UI_SERIES)
OTHER = "#5c6370"  # "Khác" (rest): neutral, darker than slate so the two never merge


def fnv1a(text: str) -> int:
    """FNV-1a 32-bit over UTF-16 code units (identical to palette.js ``hash``)."""
    h = 0x811C9DC5
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def home_slot(key: str) -> int:
    return fnv1a(key) % len(SERIES)


def color_for(key: str) -> str:
    """The key's home colour (what the UI shows without stored assignments)."""
    return SERIES[home_slot(key)]


def assign(keys: Sequence[str]) -> list[str]:
    """Distinct colours for up to 8 keys of one chart: each key gets its home slot
    unless taken, then the next free slot (like the UI's persisted assignment).
    ``"Khác"`` (other) always gets the neutral :data:`OTHER`."""
    used: set[int] = set()
    out: list[str] = []
    for k in keys:
        if k == "Khác":
            out.append(OTHER)
            continue
        slot = home_slot(k)
        for step in range(len(SERIES)):
            cand = (slot + step) % len(SERIES)
            if cand not in used or len(used) >= len(SERIES):
                slot = cand
                break
        used.add(slot)
        out.append(SERIES[slot])
    return out
