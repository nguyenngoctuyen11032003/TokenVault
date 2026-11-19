"""Skills (#/skills) and Orchestration (#/orchestration) pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.register_assert_rewrite("conftest_skills")

from conftest_skills import (  # noqa: E402
    LONG_NAME,
    XSS_NAME,
    console_problems,
    orchestration_view,
    rev,
    skills_data,
    skills_view,
    wait_rev_after,
)

pytestmark = pytest.mark.ui
pytest_plugins = ["conftest_skills"]

ARTIFACTS = Path(__file__).resolve().parent / "_artifacts"


def _calls(page: Any, name: str) -> list[list[Any]]:
    return [c["args"] for c in page.evaluate("window.__tvCalls") if c["name"] == name]


def _kpi(page: Any, kpi: str) -> str:
    return page.locator(f"#tv-view [data-kpi='{kpi}'] b").inner_text()


def _click_range(page: Any, rng: str) -> None:
    before = rev(page)
    page.locator(f"#tv-view .tv-rangeswitch button[data-range='{rng}']").click()
    wait_rev_after(page, before)


# ------------------------------------------------------------------ skills


def test_skills_range_buttons_change_data(open_skills_page: Any) -> None:
    page = open_skills_page("#/skills")
    labels = page.locator("#tv-view .tv-rangeswitch button").all_inner_texts()
    assert labels == ["7 ngày", "30 ngày", "90 ngày", "Tất cả"]
    # Default range is 30d and is exactly what the API accepts.
    assert _calls(page, "skills")[0] == ["30d"]
    t30 = skills_view("30d")["total_invocations"]
    assert _kpi(page, "total").replace(".", "") == str(t30)
    assert (
        page.locator("#tv-view .tv-rangeswitch button[data-range='30d']").get_attribute(
            "aria-pressed"
        )
        == "true"
    )
    seen = {t30}
    for rng in ("7d", "90d", "all"):
        _click_range(page, rng)
        assert _calls(page, "skills")[-1] == [rng]
        total = skills_view(rng)["total_invocations"]
        assert _kpi(page, "total").replace(".", "") == str(total)
        assert (
            page.locator(f"#tv-view .tv-rangeswitch button[data-range='{rng}']").get_attribute(
                "aria-pressed"
            )
            == "true"
        )
        seen.add(total)
    assert len(seen) == 4  # every range showed different data
    assert console_problems(page) == []


def test_skills_page_shows_ranking_table_chart_and_colours(open_skills_page: Any) -> None:
    page = open_skills_page("#/skills")
    view = skills_view("30d")
    rows = page.locator("#tv-view table.tv-skills-table tbody tr")
    assert rows.count() == len(view["by_employee"])
    assert "tuyen.nguyen@ics.vn" in rows.first.inner_text()
    ranking = page.locator("#tv-view .tv-skills-ranklist .tv-skills-rank")
    assert ranking.count() == len(view["by_employee"])
    colour = page.evaluate("import('./js/palette.js').then(p => p.colorFor('tuyen.nguyen@ics.vn'))")
    bar = ranking.first.locator(".tv-bar > i")
    expected = page.evaluate(
        "c => { const d = document.createElement('i'); d.style.background = c;"
        " document.body.append(d); const v = getComputedStyle(d).backgroundColor;"
        " d.remove(); return v; }",
        colour,
    )
    assert bar.evaluate("e => getComputedStyle(e).backgroundColor") == expected
    # Daily chart exists with an explicit size and an accessible data table.
    host = page.locator("#tv-view [data-chart='daily']")
    page.wait_for_selector(
        "#tv-view [data-chart='daily'] canvas, #tv-view [data-chart='daily'] svg"
    )
    box = host.bounding_box()
    assert box and box["width"] > 300 and box["height"] > 150
    assert host.get_attribute("role") == "img"
    assert console_problems(page) == []


def test_skills_page_shows_limitation_note(open_skills_page: Any) -> None:
    page = open_skills_page("#/skills")
    note = page.locator("#tv-view [data-note='limitation']")
    assert note.is_visible()
    text = note.inner_text()
    assert "Skill" in text and "chưa biết skill nào" in text.lower()
    # Still visible with no data at all.
    page2 = open_skills_page("#/skills", data=skills_data(empty=True))
    assert page2.locator("#tv-view [data-note='limitation']").is_visible()
    assert page2.locator("#tv-view [data-empty='skills']").is_visible()
    assert console_problems(page2) == []


def test_skills_escapes_names_as_text(open_skills_page: Any) -> None:
    page = open_skills_page("#/skills", width=1100, height=720)
    assert page.evaluate("window.__xss ?? null") is None
    assert page.locator("#tv-view img").count() == 0
    assert XSS_NAME in page.locator("#tv-view table.tv-skills-table").inner_text()
    name = page.locator(f"#tv-view .tv-skills-table a[title='{LONG_NAME}']")
    assert name.evaluate("e => e.scrollWidth > e.clientWidth || e.clientWidth > 0")
    assert name.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
    assert console_problems(page) == []


def test_skills_api_rejection_shows_error_banner_and_retry(open_skills_page: Any) -> None:
    page = open_skills_page("#/skills", extra_script="window.__tvSkillsFail = true;")
    banner = page.locator("#tv-view [data-load-error]")
    assert banner.is_visible()
    assert "boom: backend down" in banner.inner_text()
    page.evaluate("window.__tvSkillsFail = false")
    before = rev(page)
    banner.locator("button").click()
    wait_rev_after(page, before)
    assert not banner.is_visible()
    assert page.locator("#tv-view table.tv-skills-table tbody tr").count() > 0


def test_skills_live_event_refetches_once_debounced(open_skills_page: Any) -> None:
    page = open_skills_page("#/skills")
    n = len(_calls(page, "skills"))
    before = rev(page)
    page.evaluate("for (let i = 0; i < 5; i++) window.tv.emitLive({online: 1, total: 2})")
    wait_rev_after(page, before)
    page.wait_for_timeout(400)
    assert len(_calls(page, "skills")) == n + 1


# ------------------------------------------------------------ orchestration


def test_orchestration_splits_main_vs_sidechain_numbers(open_skills_page: Any) -> None:
    page = open_skills_page("#/orchestration")
    view = orchestration_view("30d")
    assert _calls(page, "orchestration")[0] == ["30d"]
    assert _kpi(page, "main") == "30M"
    assert _kpi(page, "sub") == "12M"
    assert _kpi(page, "share") == "29%"
    assert _kpi(page, "sessions") == str(len(view["agents_by_tokens"]))
    bar = page.locator("#tv-view .tv-orch-split__bar")
    assert "30.000.000" in bar.get_attribute("aria-label")
    assert "12.000.000" in bar.get_attribute("aria-label")
    w_main = page.locator("[data-seg='main']").evaluate("e => e.getBoundingClientRect().width")
    w_sub = page.locator("[data-seg='sub']").evaluate("e => e.getBoundingClientRect().width")
    assert w_main / (w_main + w_sub) == pytest.approx(30 / 42, abs=0.02)
    # Two model donuts and a ranked agent list.
    for side in ("main", "sub"):
        page.wait_for_selector(
            f"#tv-view [data-chart='models-{side}'] canvas, "
            f"#tv-view [data-chart='models-{side}'] svg"
        )
    agents = page.locator("#tv-view .tv-skills-ranklist .tv-skills-rank")
    assert agents.count() == 5
    assert "Thiết kế dashboard NitroSense" in agents.first.inner_text()
    assert page.locator("#tv-view [data-note='limitation']").is_visible()
    # Collapsible explanation.
    details = page.locator("#tv-view details[data-explain]")
    assert details.get_attribute("open") is None
    details.locator("summary").click()
    assert details.get_attribute("open") is not None
    assert console_problems(page) == []


def test_orchestration_range_buttons_requery(open_skills_page: Any) -> None:
    page = open_skills_page("#/orchestration")
    _click_range(page, "7d")
    assert _calls(page, "orchestration")[-1] == ["7d"]
    assert _kpi(page, "main") == "7M"
    _click_range(page, "all")
    assert _calls(page, "orchestration")[-1] == ["all"]
    assert _kpi(page, "main") == "120M"


def test_orchestration_handles_no_sidechain_data(open_skills_page: Any) -> None:
    page = open_skills_page("#/orchestration", data=skills_data(no_sidechain=True))
    assert page.locator("#tv-view [data-empty='sidechain']").is_visible()
    assert page.locator("#tv-view [data-empty='agents']").is_visible()
    assert _kpi(page, "sub") == "0"
    assert _kpi(page, "share") == "0%"
    assert page.locator("#tv-view .tv-skills-ranklist .tv-skills-rank").count() == 0
    # The main side is still shown.
    assert _kpi(page, "main") == "30M"
    page.wait_for_selector(
        "#tv-view [data-chart='models-main'] canvas, #tv-view [data-chart='models-main'] svg"
    )
    assert not page.locator("#tv-view [data-empty='all']").is_visible()
    assert console_problems(page) == []


def test_orchestration_with_no_data_at_all(open_skills_page: Any) -> None:
    data = skills_data()
    for r in list(data["orchestration"]):
        data["orchestration"][r] = {
            "range": r,
            "main_tokens": 0,
            "sidechain_tokens": 0,
            "main_by_model": [],
            "sidechain_by_model": [],
            "agents_by_tokens": [],
        }
    page = open_skills_page("#/orchestration", data=data)
    assert page.locator("#tv-view [data-empty='all']").is_visible()
    assert console_problems(page) == []


def test_orchestration_escapes_names_and_ellipsises(open_skills_page: Any) -> None:
    page = open_skills_page("#/orchestration", width=1100, height=720)
    assert page.evaluate("window.__xss ?? null") is None
    assert page.locator("#tv-view img").count() == 0
    assert XSS_NAME in page.locator("#tv-view .tv-skills-ranklist").inner_text()
    long = page.locator("#tv-view .tv-skills-rank__name", has_text="Sửa lỗi đồng bộ")
    assert long.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
    assert long.get_attribute("title").startswith("Sửa lỗi đồng bộ")
    assert console_problems(page) == []


def test_orchestration_api_rejection_shows_error_banner(open_skills_page: Any) -> None:
    page = open_skills_page("#/orchestration", extra_script="window.__tvSkillsFail = true;")
    banner = page.locator("#tv-view [data-load-error]")
    assert banner.is_visible()
    assert "boom: backend down" in banner.inner_text()
    page.evaluate("window.__tvSkillsFail = false")
    before = rev(page)
    banner.locator("button").click()
    wait_rev_after(page, before)
    assert not banner.is_visible()
    assert _kpi(page, "main") == "30M"


def test_charts_are_disposed_when_leaving(open_skills_page: Any) -> None:
    page = open_skills_page("#/orchestration")
    page.wait_for_selector(
        "#tv-view [data-chart='models-sub'] canvas, #tv-view [data-chart='models-sub'] svg"
    )
    assert page.evaluate("import('./js/charts.js').then(m => m.liveCount())") >= 2
    page.evaluate("location.hash = '#/skills'")
    page.wait_for_selector("#tv-view[data-route='skills'] [data-tv-rev]:not([data-tv-rev='0'])")
    page.wait_for_selector(
        "#tv-view [data-chart='daily'] canvas, #tv-view [data-chart='daily'] svg"
    )
    assert page.evaluate("import('./js/charts.js').then(m => m.liveCount())") == 1
    assert console_problems(page) == []


# ------------------------------------------------------------ visual QA


@pytest.mark.parametrize("route", ["skills", "orchestration"])
@pytest.mark.parametrize("size", [(1440, 900), (1100, 720)])
def test_screenshots(open_skills_page: Any, route: str, size: tuple[int, int]) -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    page = open_skills_page(f"#/{route}", width=size[0], height=size[1])
    page.wait_for_selector(
        "#tv-view canvas, #tv-view svg[role], #tv-view [data-chart] svg", timeout=5000
    )
    page.wait_for_timeout(700)
    page.screenshot(path=str(ARTIFACTS / f"{route}-{size[0]}x{size[1]}.png"))
    assert console_problems(page) == []


# ------------------------------------------------------------ review round 1


@pytest.mark.parametrize("tz", ["America/Los_Angeles", "Pacific/Kiritimati", "Asia/Ho_Chi_Minh"])
def test_skills_day_axis_is_exactly_the_server_days(open_skills_tz: Any, tz: str) -> None:
    # Server days that are not "today" in any browser zone; gaps in between get zeros.
    days = ["2026-11-10", "2026-11-12", "2026-11-14"]
    data = skills_data()
    for rng in data["skills"]:
        view = data["skills"][rng]
        view["daily"] = [{"day": d, "count": 2 + i} for i, d in enumerate(days)]
        view["total_invocations"] = 9
    page = open_skills_tz("#/skills", data, tz)
    page.wait_for_selector(
        "#tv-view [data-chart='daily'] canvas, #tv-view [data-chart='daily'] svg"
    )
    labels = page.evaluate(
        """() => {
          const host = document.querySelector("#tv-view [data-chart='daily']");
          const desc = document.getElementById(host.getAttribute('aria-describedby'));
          return [...desc.querySelectorAll('tbody tr')].map(r => r.cells[0].textContent);
        }"""
    )
    assert labels == ["10/11", "11/11", "12/11", "13/11", "14/11"]
    assert console_problems(page) == []


def _rgb(page: Any, colour: str) -> str:
    return page.evaluate(
        "c => { const d = document.createElement('i'); d.style.background = c;"
        " document.body.append(d); const v = getComputedStyle(d).backgroundColor;"
        " d.remove(); return v; }",
        colour,
    )


def test_orchestration_side_colours_are_outside_the_categorical_palette(
    open_skills_page: Any,
) -> None:
    page = open_skills_page("#/orchestration")
    palette = page.evaluate("import('./js/palette.js').then(p => [...p.SERIES_COLORS])")
    palette_rgb = {_rgb(page, c) for c in palette}
    main = page.locator("[data-seg='main']").evaluate("e => getComputedStyle(e).backgroundColor")
    sub = page.locator("[data-seg='sub']").evaluate("e => getComputedStyle(e).backgroundColor")
    assert main not in palette_rgb and sub not in palette_rgb and main != sub
    assert sub == _rgb(page, "var(--tv-red-hot)")
    assert main == _rgb(page, "var(--tv-text)")


def test_orchestration_tooltip_and_legend_render_model_names_as_text(
    open_skills_page: Any,
) -> None:
    evil = "<img src=x onerror=alert(1)>"
    data = skills_data()
    for view in data["orchestration"].values():
        view["main_by_model"][0]["model"] = evil
        view["sidechain_by_model"][0]["model"] = evil
    page = open_skills_page("#/orchestration", data=data)
    page.evaluate("window.__xss = null; window.alert = () => { window.__xss = 1; }; undefined")
    page.wait_for_selector(
        "#tv-view [data-chart='models-main'] canvas, #tv-view [data-chart='models-main'] svg"
    )
    for side in ("main", "sub"):
        page.evaluate(
            """side => {
              const el = document.querySelector(`#tv-view [data-chart='models-${side}']`);
              echarts.getInstanceByDom(el).dispatchAction(
                {type: 'showTip', seriesIndex: 0, dataIndex: 0});
            }""",
            side,
        )
        page.wait_for_selector("#tv-view [data-chart] div[style*='position'] >> text=onerror")
        tip = page.evaluate(
            "[...document.querySelectorAll('#tv-view [data-chart] div')]"
            ".filter(d => d.textContent.includes('onerror') && d.children.length)"
            ".map(d => d.innerHTML).join('|')"
        )
        assert "<img" not in tip.replace("&lt;img", "")
    assert page.locator("#tv-view img").count() == 0
    assert page.evaluate("window.__xss") is None
    assert console_problems(page) == []
