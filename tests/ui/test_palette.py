"""Categorical palette (ui/js/palette.js): stable per key, distinct, legible on --tv-panel."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.ui


def _token(css: str, name: str) -> str:
    match = re.search(rf"--{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})\s*;", css)
    assert match, name
    return match.group(1).lower()


def _lum(hex_color: str) -> float:
    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _ratio(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _palette(page: Any, expr: str) -> Any:
    return page.evaluate(f"import('./js/palette.js').then(p => {expr})")


def test_same_key_same_colour(open_shell: Any) -> None:
    page = open_shell("#/settings")
    first = _palette(page, "[p.colorFor('erp-api'), p.colorFor('erp-api'), p.colorFor('Kế toán')]")
    assert first[0] == first[1]
    assert re.fullmatch(r"#[0-9a-f]{6}", first[0])
    # Stable across a reload (same key -> same colour on every page / chart / visit).
    page.reload()
    page.wait_for_selector("html[data-tv-ready='1']")
    again = _palette(page, "[p.colorFor('Kế toán'), p.colorFor('erp-api')]")
    assert again == [first[2], first[0]]


def test_eight_distinct_keys_get_eight_distinct_colours(open_shell: Any) -> None:
    page = open_shell("#/settings")
    keys = ["TokenVault", "erp-api", "mobile-app", "docs-site", "claude-opus-4-7",
            "claude-sonnet-4-6", "an.tran@ics.vn", "Bash"]
    colours = _palette(page, f"{keys!r}.map(k => p.colorFor(k))")
    series = _palette(page, "[...p.SERIES_COLORS]")
    assert len(set(colours)) == 8
    assert set(colours) == set(series)


def test_every_series_colour_legible_on_panel(open_shell: Any, ui_dir: Path) -> None:
    css = (ui_dir / "css" / "theme.css").read_text(encoding="utf-8")
    panel = _token(css, "tv-panel")
    tokens = [_token(css, f"tv-series-{i}") for i in range(1, 9)]
    page = open_shell("#/settings")
    series = _palette(page, "[...p.SERIES_COLORS]")
    assert [c.lower() for c in series] == tokens  # JS palette == CSS tokens
    ratios = _palette(page, f"p.SERIES_COLORS.map(c => p.contrastRatio(c, {panel!r}))")
    for colour, ratio in zip(series, ratios, strict=True):
        assert ratio == pytest.approx(_ratio(colour, panel), abs=0.01)
        assert ratio >= 4.5, colour
    # ensureLegible lifts a too-dark colour to >= 4.5:1 and keeps legible ones unchanged.
    fixed = _palette(page, f"[p.ensureLegible('#5a0010', {panel!r}), "
                           f"p.ensureLegible('#4da3ff', {panel!r})]")
    assert _ratio(fixed[0], panel) >= 4.5
    assert fixed[1].lower() == "#4da3ff"


NO_STORAGE = """
Object.defineProperty(window, 'localStorage', {
  configurable: true,
  get() { throw new DOMException('storage disabled', 'SecurityError'); },
});
"""

KEYS = ["TokenVault", "erp-api", "mobile-app", "claude-opus-4-7", "an.tran@ics.vn",
        "device:PC Kế toán@HN 01", "Bash", "docs-site", "infra-terraform", "hr-portal"]


def _fnv_slot(key: str) -> int:
    h = 0x811C9DC5
    data = key.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h % 8


def test_without_storage_colours_are_identical_across_fresh_loads(open_shell: Any) -> None:
    first = open_shell("#/settings", init_script=NO_STORAGE)
    probe = "(() => { try { localStorage; return 'ok'; } catch { return 'no'; } })()"
    assert first.evaluate(probe) == "no"
    a = _palette(first, f"{KEYS!r}.map(k => p.colorFor(k))")
    # A brand-new page (fresh launch, no storage), keys asked in the opposite order.
    second = open_shell("#/settings", init_script=NO_STORAGE)
    b = _palette(second, f"{KEYS[::-1]!r}.map(k => p.colorFor(k))")[::-1]
    assert a == b
    series = _palette(first, "[...p.SERIES_COLORS]")
    assert a == [series[_fnv_slot(k)] for k in KEYS]  # home slot = FNV-1a(UTF-16) mod 8
    assert first.tv_errors == [] and second.tv_errors == []


def test_corrupt_storage_json_does_not_throw(open_shell: Any) -> None:
    corrupt = "try { localStorage.setItem('tv.palette.v1', '{not json'); } catch {}"
    page = open_shell("#/settings", init_script=corrupt)
    colours = _palette(page, "['a', 'b', 'c'].map(k => p.colorFor(k))")
    assert all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in colours)
    assert len(set(colours)) == 3
    stored = page.evaluate("JSON.parse(localStorage.getItem('tv.palette.v1'))")
    assert [e[0] for e in stored] == ["a", "b", "c"]  # overwritten with valid JSON
    assert page.tv_errors == []
