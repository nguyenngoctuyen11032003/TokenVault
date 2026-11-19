from __future__ import annotations

from collections.abc import Callable

import pytest
from reportlab.graphics.shapes import Drawing, Group, String

from tokenvault.manager.reports import charts
from tokenvault.manager.reports import palette as P
from tokenvault.manager.reports import strings_vi as S
from tokenvault.manager.reports.chart_kit import fit, nice_ticks, top_with_other, width

W, H = 260.0, 150.0
DAYS = [f"2026-10-{d:02d}" for d in range(1, 32)] + [f"2026-11-{d:02d}" for d in range(1, 31)]
LONG = "dự-án-có-tên-cực-kỳ-dài-" * 8
DATASETS: dict[str, tuple[list[str], list[object]]] = {
    "empty": ([], []),
    "zeros": (["a", "b"], [0, 0]),
    "one": (["2026-11-19"], [1234]),
    "many": (DAYS, [i * 997 % 50_000 for i in range(len(DAYS))]),
    "huge": (["a", "b", "c"], [9.9e15, 1, 3e12]),
    "neg_zero": (["a", "b", "c"], [-0.0, -50, float("nan")]),
    "long": ([LONG, "<b>x</b>", "ok"], [3, 2, 1]),
    "emoji": (["😀 dự án", "עברית", "x\x00y"], [5, 4, 3]),
}


def strings(d: Drawing) -> list[str]:
    out: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, String):
            out.append(node.text)
        elif isinstance(node, (Group, Drawing)):
            for c in node.contents:
                walk(c)

    walk(d)
    return out


def within(d: Drawing, w: float, h: float) -> None:
    assert (d.width, d.height) == (w, h)
    x0, y0, x1, y1 = d.getBounds()
    assert x0 >= -1 and y0 >= -1 and x1 <= w + 1 and y1 <= h + 1, d.getBounds()


CHARTS: dict[str, Callable[[list[str], list[object]], Drawing]] = {
    "daily_area": lambda lb, v: charts.daily_area("Token theo ngày", lb, v, W, H),
    "hbars": lambda lb, v: charts.hbars("Dự án", lb, v, W, H),
    "hbars_single": lambda lb, v: charts.hbars("Công cụ", lb, v, W, H, single=P.SERIES[3]),
    "donut": lambda lb, v: charts.donut("Model", lb, v, W, H),
    "hour_bars": lambda lb, v: charts.hour_bars("Giờ", lb, v, W, H),
    "stacked": lambda lb, v: charts.stacked_days(
        "Theo nhân viên", lb, [("an@x.vn", v), ("Khác", v)], W, H
    ),
}


@pytest.mark.parametrize("chart", sorted(CHARTS))
@pytest.mark.parametrize("dataset", sorted(DATASETS))
def test_chart_is_within_bounds_for_odd_data(chart: str, dataset: str) -> None:
    labels, values = DATASETS[dataset]
    d = CHARTS[chart](labels, values)
    within(d, W, H)
    texts = strings(d)
    if dataset in ("empty", "zeros", "neg_zero"):
        assert S.EMPTY in texts
    for t in texts:
        assert "\x00" not in t and "😀" not in t


def test_long_labels_are_truncated_with_ellipsis() -> None:
    d = charts.hbars("Dự án", [LONG, "b"], [10, 5], W, H)
    long_ = [t for t in strings(d) if t.startswith("dự-án")]
    assert long_ and long_[0].endswith("…") and width(long_[0], 7.2) <= W * 0.38
    assert fit("abc", 8, 0) == "" and fit("x" * 500, 8, 40).endswith("…")


def test_top_with_other_and_ticks() -> None:
    labs, vals = top_with_other([f"p{i}" for i in range(12)], list(range(12, 0, -1)), 8)
    assert labs[-1] == "Khác" and vals[-1] == 4 + 3 + 2 + 1 and len(labs) == 9
    assert nice_ticks(1234)[-1] >= 1234 and nice_ticks(0) == [0.0, 1.0]
    assert len(nice_ticks(9.9e15)) <= 6


def test_print_palette_contrast_and_hash_parity() -> None:
    assert len(P.SERIES) == 8
    for c in (*P.SERIES, P.OTHER, P.MUTED, P.ACCENT):
        assert P.contrast(c, "#ffffff") >= 3.0, c
    # same FNV-1a home slots as palette.js (verified against node)
    assert [P.home_slot(k) for k in ("erp-api", "Đội", "claude-sonnet-4-6", "😀")] == [7, 1, 7, 0]
    cols = P.assign(["a", "b", "c", "d", "e", "f", "g", "h", "Khác"])
    assert len(set(cols[:8])) == 8 and cols[-1] == P.OTHER


def test_donut_shows_percentages_summing_to_whole() -> None:
    d = charts.donut("Model", ["sonnet", "opus", "haiku"], [50, 30, 20], W, H)
    texts = strings(d)
    assert {"50%", "30%", "20%"} <= set(texts)
