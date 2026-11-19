"""Home cockpit (#/home): team list, today gauges, live hourly chart, employee table."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.register_assert_rewrite("conftest_home")

from conftest_home import XSS_NAME, console_problems, home_data, rev, wait_rev_after  # noqa: E402

pytestmark = pytest.mark.ui
pytest_plugins = ["conftest_home"]

ROWS = "#tv-view .tv-home table.tv-emptable tbody tr[data-key]"


def _calls(page: Any, name: str) -> list[list[Any]]:
    return [c["args"] for c in page.evaluate("window.__tvCalls") if c["name"] == name]


def _gauge_text(page: Any, kpi: str) -> str:
    return page.locator(f"tv-gauge[data-kpi='{kpi}'] .tv-gauge__value").inner_text()


def test_home_shows_three_gauges_team_list_and_employee_table(open_page: Any) -> None:
    page = open_page("#/home")
    gauges = page.locator("#tv-view .tv-home tv-gauge")
    assert gauges.count() == 3
    assert _gauge_text(page, "tokens") == "4.82M"
    assert _gauge_text(page, "cost") == "$61.40"
    assert _gauge_text(page, "sessions") == "37"
    teams = page.locator("#tv-view .tv-teamlist button.tv-list__item")
    assert teams.count() == 4
    assert teams.nth(0).get_attribute("aria-pressed") == "true"
    assert "Tất cả" in teams.nth(0).inner_text()
    assert [teams.nth(i).get_attribute("data-team") for i in range(4)] == [
        "", "Dev", "QA", "BA / PM"
    ]
    assert page.locator(ROWS).count() == 12
    first = page.locator(ROWS).first
    assert "tuyen.nguyen@ics.vn" in first.inner_text()
    assert "1.21M" in first.inner_text()
    # Share bar uses the employee's palette colour.
    colour = page.evaluate(
        "import('./js/palette.js').then(p => p.colorFor('tuyen.nguyen@ics.vn'))"
    )
    bar = first.locator(".tv-bar > i")
    assert bar.evaluate("e => getComputedStyle(e).backgroundColor") == page.evaluate(
        "c => { const d = document.createElement('i'); d.style.background = c;"
        " document.body.append(d); const v = getComputedStyle(d).backgroundColor;"
        " d.remove(); return v; }",
        colour,
    )
    # Today = a single local day sent as date-only start/end.
    flt = _calls(page, "overview")[0][0]
    assert flt["start"] == flt["end"] and len(flt["start"]) == 10
    assert set(flt) <= {"team", "email", "project", "model", "start", "end"}
    # Top projects strip.
    chips = page.locator("#tv-view .tv-home-projects [data-project]")
    assert chips.count() >= 3
    assert chips.first.get_attribute("data-project") == "TokenVault"
    assert console_problems(page) == []


def test_selecting_team_filters_table(open_page: Any) -> None:
    page = open_page("#/home")
    before = rev(page)
    page.locator("#tv-view .tv-teamlist button[data-team='QA']").click()
    wait_rev_after(page, before)
    assert page.locator(ROWS).count() == 3
    assert page.locator("#tv-view .tv-teamlist button[data-team='QA']").get_attribute(
        "aria-pressed"
    ) == "true"
    assert page.locator("#tv-view .tv-teamlist button[data-team='']").get_attribute(
        "aria-pressed"
    ) == "false"
    assert _calls(page, "employees")[-1][0]["team"] == "QA"
    assert _calls(page, "overview")[-1][0]["team"] == "QA"
    assert page.evaluate("import('./js/filters.js').then(m => m.filters.get().team)") == "QA"
    assert _gauge_text(page, "tokens") == "964K"
    # Back to all.
    before = rev(page)
    page.locator("#tv-view .tv-teamlist button[data-team='']").click()
    wait_rev_after(page, before)
    assert page.locator(ROWS).count() == 12
    assert console_problems(page) == []


def test_unknown_cost_shows_UNKNOWN_not_zero(  # noqa: N802
    open_page: Any, artifacts_dir: Path
) -> None:
    page = open_page("#/home", data=home_data(unknown_cost=True))
    page.wait_for_timeout(700)
    page.screenshot(path=str(artifacts_dir / "home-unknown-cost-1440x900.png"))
    assert _gauge_text(page, "cost") == "UNKNOWN"
    costs = page.locator(f"{ROWS} td[data-col='cost']")
    assert costs.count() == 12
    assert set(costs.all_inner_texts()) == {"UNKNOWN"}
    text = page.locator("#tv-view").inner_text()
    assert "$0" not in text
    # The unpriced model is named so the owner knows why.
    assert "claude-experimental-x" in text
    assert console_problems(page) == []


def test_cost_labelled_api_equivalent_estimate(open_page: Any) -> None:
    page = open_page("#/home")
    note = page.locator("#tv-view .tv-home [data-cost-note]").first
    assert note.is_visible()
    assert "ước tính theo giá API" in note.inner_text()
    header = page.locator("#tv-view table.tv-emptable th[data-col='cost']")
    assert "ước tính" in (header.get_attribute("title") or "") + header.inner_text()


def test_live_event_updates_gauge_without_resetting_scroll_or_selected_team(
    open_page: Any,
) -> None:
    page = open_page("#/home", width=1100, height=720)
    before = rev(page)
    page.locator("#tv-view .tv-teamlist button[data-team='Dev']").click()
    wait_rev_after(page, before)
    view = page.locator("#tv-view")
    assert view.evaluate("v => v.scrollHeight > v.clientHeight + 100")
    view.evaluate("v => { v.scrollTop = 140; }")
    scroll = view.evaluate("v => v.scrollTop")
    assert scroll > 100
    page.evaluate(
        "() => { const o = window.__tvHomeData.overview['Dev|today'];"
        " o.totals.total_tokens = 9990000; o.totals.sessions = 52; }"
    )
    before = rev(page)
    page.evaluate("tv.onLive({online: 5, total: 14})")
    wait_rev_after(page, before)
    assert _gauge_text(page, "tokens") == "9.99M"
    assert _gauge_text(page, "sessions") == "52"
    assert view.evaluate("v => v.scrollTop") == scroll
    assert page.locator("#tv-view .tv-teamlist button[data-team='Dev']").get_attribute(
        "aria-pressed"
    ) == "true"
    assert page.evaluate("import('./js/filters.js').then(m => m.filters.get().team)") == "Dev"
    assert _calls(page, "overview")[-1][0]["team"] == "Dev"
    # The chart instance is reused, not recreated.
    assert page.locator("#tv-view .tv-chart[data-chart='hourly'] svg").count() == 1
    assert console_problems(page) == []


def test_online_dot_reflects_connected_devices(open_page: Any) -> None:
    page = open_page("#/home")

    def dot(key: str) -> Any:
        return page.locator(f"{ROWS}[data-key='{key}'] .tv-dot")

    assert "tv-dot--on" in (dot("tuyen.nguyen@ics.vn").get_attribute("class") or "")
    assert "tv-dot--on" not in (dot("minh.le@ics.vn").get_attribute("class") or "")
    dev = page.locator("#tv-view .tv-teamlist button[data-team='Dev']")
    assert "4/8" in dev.inner_text().replace(" ", "")
    assert dev.locator(".tv-dot--on").count() == 1
    page.evaluate(
        "() => { const d = window.__tvHomeData;"
        " d.employees.find(e => e.key === 'minh.le@ics.vn').online = true;"
        " d.teams.find(t => t.name === 'Dev').online = 5; }"
    )
    before = rev(page)
    page.evaluate("tv.onLive({devices: {online: 8, total: 14}})")
    wait_rev_after(page, before)
    assert "tv-dot--on" in (dot("minh.le@ics.vn").get_attribute("class") or "")
    assert "5/8" in dev.inner_text().replace(" ", "")
    assert console_problems(page) == []


def test_empty_state_when_no_devices_shows_how_to_create_installer(
    open_page: Any, artifacts_dir: Path
) -> None:
    page = open_page("#/home", data=home_data(empty=True), width=1100, height=720)
    page.screenshot(path=str(artifacts_dir / "home-empty-1100x720.png"))
    empty = page.locator("#tv-view .tv-home-empty")
    assert empty.is_visible()
    link = empty.locator("a[href='#/installer']")
    assert link.is_visible()
    assert "bộ cài" in empty.inner_text()
    assert page.locator("#tv-view .tv-home tv-gauge").count() == 0
    assert console_problems(page) == []
    link.click()  # (the installer page belongs to another task; it may not exist yet)
    page.wait_for_selector("#tv-view[data-route='installer'][data-route-state]")


def test_teams_api_failure_falls_back_to_employee_rows(open_page: Any) -> None:
    page = open_page("#/home")
    # Simulate a Manager without api.teams(): the team list is derived from the employees.
    page.evaluate("() => { window.pywebview.api.teams = () => Promise.reject(new Error('x')); }")
    before = rev(page)
    page.evaluate("tv.onLive({online: 1, total: 2})")
    wait_rev_after(page, before)
    teams = page.locator("#tv-view .tv-teamlist button.tv-list__item")
    assert teams.count() == 4
    assert "4/8" in teams.nth(1).inner_text().replace(" ", "")
    assert page.locator(ROWS).count() == 12
    assert page.locator("#tv-view .tv-home [data-load-error]").count() == 0
    assert page.tv_errors == []


def test_untrusted_names_render_as_text(open_page: Any) -> None:
    page = open_page("#/home")
    assert page.locator("#tv-view img").count() == 0
    assert page.evaluate("window.__xss") is None
    assert XSS_NAME in page.locator(ROWS).nth(10).inner_text()
    # Long names are truncated with an ellipsis and keep the full text in a tooltip.
    long_cell = page.locator(f"{ROWS} .tv-emptable__name").nth(4)
    assert long_cell.get_attribute("title", timeout=1000) and long_cell.evaluate(
        "e => getComputedStyle(e).textOverflow"
    ) == "ellipsis"
    assert long_cell.evaluate("e => e.scrollWidth > e.clientWidth")


def test_hourly_chart_renders_svg_with_size_and_peak(open_page: Any) -> None:
    page = open_page("#/home")
    chart = page.locator("#tv-view .tv-chart[data-chart='hourly']")
    box = chart.bounding_box()
    assert box and box["width"] > 200 and box["height"] > 100
    svg = chart.locator("svg").first
    svg_box = svg.bounding_box()
    assert svg_box and svg_box["width"] > 200 and svg_box["height"] > 100
    assert chart.get_attribute("role") == "img"
    assert chart.get_attribute("aria-label")
    described = chart.get_attribute("aria-describedby")
    assert described and page.locator(f"#{described} table tr").count() >= 24
    meta = page.locator("#tv-view .tv-home [data-peak]").inner_text()
    assert "10:00" in meta
    # Leaving the route disposes the chart.
    page.evaluate("location.hash = '#/settings'")
    page.wait_for_selector("#tv-view[data-route='settings'][data-route-state]")
    assert page.evaluate("import('./js/charts.js').then(c => c.liveCount())") == 0


@pytest.mark.parametrize(("width", "height"), [(1440, 900), (1100, 720)])
def test_screenshot_home(open_page: Any, artifacts_dir: Path, width: int, height: int) -> None:
    page = open_page("#/home", width=width, height=height)
    page.wait_for_timeout(900)
    metrics = page.evaluate(
        "() => { const v = document.querySelector('#tv-view');"
        " return [v.scrollWidth, v.clientWidth, document.documentElement.scrollWidth,"
        " innerWidth]; }"
    )
    assert metrics[0] <= metrics[1] and metrics[2] <= metrics[3]
    path = artifacts_dir / f"home-{width}x{height}.png"
    page.screenshot(path=str(path))
    page.locator("#tv-view").evaluate("v => { v.scrollTop = v.scrollHeight; }")
    page.wait_for_timeout(200)
    page.screenshot(path=str(artifacts_dir / f"home-{width}x{height}-bottom.png"))
    assert path.stat().st_size > 10_000
    assert console_problems(page) == []


def test_charts_skip_animation_when_reduced_motion(open_page: Any) -> None:
    page = open_page("#/home")
    anim = "echarts.getInstanceByDom(document.querySelector('[data-chart=hourly]')).getOption()"
    assert page.evaluate(f"{anim}.animation") is True
    page.emulate_media(reduced_motion="reduce")
    before = rev(page)
    page.evaluate("tv.onLive({online: 1, total: 2})")
    wait_rev_after(page, before)
    assert page.evaluate(f"{anim}.animation") is False


def test_burst_of_live_events_triggers_one_refetch(open_page: Any) -> None:
    page = open_page("#/home")
    before_calls = len(_calls(page, "overview"))
    before = rev(page)
    page.evaluate("for (let i = 0; i < 10; i++) tv.emitLive({online: i, total: 14})")
    wait_rev_after(page, before)
    page.wait_for_timeout(600)  # anything still queued would have run by now
    assert len(_calls(page, "overview")) - before_calls <= 2
    assert rev(page) == before + 1
