"""Overview (#/overview): chart set, totals, global filter bar shared with other pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.register_assert_rewrite("conftest_overview")

from conftest_overview import (  # noqa: E402
    LONG_PROJECT,
    LONG_TITLE,
    UNPRICED,
    XSS_PROJECT,
    XSS_TITLE,
    calls,
    console_problems,
    css_color,
    go,
    overview_data,
    rev,
    wait_rev_after,
)

pytestmark = pytest.mark.ui
pytest_plugins = ["conftest_overview"]

OV = "#tv-view .tv-overview"
CHARTS = ["daily", "cache", "projects", "models", "tools"]


def _chart_marks(page: Any, name: str) -> int:
    """Number of drawn data marks (paths) in the chart's SVG."""
    return int(
        page.evaluate(
            "n => { const el = document.querySelector(`#tv-view [data-chart='${n}']`);"
            " const svg = el && el.querySelector('svg');"
            " return svg ? svg.querySelectorAll('path').length : -1; }",
            name,
        )
    )


def _set_filter(page: Any, name: str, value: str) -> None:
    before = rev(page)
    page.locator(f"#tv-view .tv-ovfilter select[data-filter='{name}']").select_option(value)
    wait_rev_after(page, before)


def test_each_overview_chart_renders_svg_with_data(open_ov: Any) -> None:
    page = open_ov("#/overview")
    page.wait_for_timeout(300)
    for name in CHARTS:
        host = page.locator(f"{OV} [data-chart='{name}']")
        assert host.count() == 1, name
        box = host.bounding_box()
        assert box and box["width"] > 200 and box["height"] > 150, (name, box)
        assert _chart_marks(page, name) > 3, name
    # Daily tokens uses the 90-day `daily` series; 90d range -> 90 points in the a11y table.
    rows = page.locator(f"{OV} [data-chart='daily'] + .tv-chart-sr tbody tr")
    assert rows.count() == 90
    # Totals strip with the API-equivalent cost label.
    for key in ["tokens", "input", "output", "cache_read", "cache_write", "sessions", "cost"]:
        assert page.locator(f"{OV} [data-total='{key}']").count() == 1, key
    assert "ước tính theo giá API" in page.locator(f"{OV} [data-cost-note]").inner_text()
    # Recent sessions table links to the session detail.
    recent = page.locator(f"{OV} table.tv-ovrecent tbody tr[data-session]")
    assert 1 <= recent.count() <= 10
    href = recent.first.locator("a").first.get_attribute("href") or ""
    assert href.startswith("#/session/")
    # Overview asks for a date-only range of 90 local days.
    flt = calls(page, "overview")[-1][0]
    assert set(flt) <= {"team", "email", "project", "model", "start", "end"}
    assert len(flt["start"]) == 10 and len(flt["end"]) == 10
    assert console_problems(page) == []


def test_monthly_toggle_for_long_ranges(open_ov: Any) -> None:
    page = open_ov("#/overview", filters={"range": "all"})
    toggle = page.locator(f"{OV} [data-granularity='month']")
    assert toggle.is_visible()
    toggle.click()
    page.wait_for_function(
        "document.querySelector(\"#tv-view [data-chart='daily']\").dataset.series === 'monthly'"
    )
    rows = page.locator(f"{OV} [data-chart='daily'] + .tv-chart-sr tbody tr")
    assert 5 <= rows.count() <= 7
    # Short ranges have no monthly toggle.
    page2 = open_ov("#/overview", filters={"range": "30d"})
    assert not page2.locator(f"{OV} [data-granularity='month']").is_visible()


def test_filters_apply_across_pages_and_persist_on_navigation(open_ov: Any) -> None:
    page = open_ov("#/overview")
    team = page.locator("#tv-view .tv-ovfilter select[data-filter='team']")
    options = team.locator("option").all_inner_texts()
    assert {"Dev", "QA", "BA / PM"} <= set(options)
    _set_filter(page, "team", "QA")
    assert calls(page, "overview")[-1][0]["team"] == "QA"
    _set_filter(page, "model", "claude-sonnet-4-6")
    flt = calls(page, "overview")[-1][0]
    assert flt["team"] == "QA" and flt["model"] == "claude-sonnet-4-6"
    # Every recent session belongs to QA employees.
    emps = page.locator(f"{OV} table.tv-ovrecent tbody tr [data-col='employee']").all_inner_texts()
    assert emps and all(e in {"hoa.pham@ics.vn", "tuyet.nguyen@ics.vn"} for e in emps)

    go(page, "#/projects")
    assert calls(page, "projects")[-1][0]["team"] == "QA"
    assert calls(page, "projects")[-1][0]["model"] == "claude-sonnet-4-6"
    bar = "#tv-view .tv-ovfilter select[data-filter='{}']"
    assert page.locator(bar.format("team")).input_value() == "QA"

    go(page, "#/sessions")
    assert calls(page, "sessions")[-1][0]["team"] == "QA"
    assert page.locator(bar.format("model")).input_value() == "claude-sonnet-4-6"

    # Reload the app: the filters come back from storage.
    page.reload()
    page.wait_for_selector("html[data-tv-ready='1']", state="attached")
    page.wait_for_selector("#tv-view .tv-sessions[data-tv-rev]:not([data-tv-rev='0'])")
    assert page.locator(bar.format("team")).input_value() == "QA"
    assert calls(page, "sessions")[-1][0]["team"] == "QA"

    # Clearing resets team/employee/project/model but keeps the range.
    before = rev(page)
    page.locator("#tv-view [data-filter-clear]").click()
    wait_rev_after(page, before)
    state = page.evaluate("import('./js/filters.js').then(m => m.filters.get())")
    assert state["team"] is None and state["model"] is None and state["range"] == "90d"
    assert console_problems(page) == []


def test_range_switch_and_custom_dates(open_ov: Any) -> None:
    page = open_ov("#/overview")
    before = rev(page)
    page.locator("#tv-view .tv-ovfilter [data-range='7d']").click()
    wait_rev_after(page, before)
    flt = calls(page, "overview")[-1][0]
    assert flt["start"] < flt["end"]
    rows = page.locator(f"{OV} [data-chart='daily'] + .tv-chart-sr tbody tr")
    assert rows.count() == 7
    # Custom range from the two date inputs.
    data_today = page.evaluate("window.__tvOvData.today")
    before = rev(page)
    page.locator("#tv-view .tv-ovfilter input[data-filter='start']").fill("2026-01-01")
    page.locator("#tv-view .tv-ovfilter input[data-filter='end']").fill(data_today)
    page.locator("#tv-view .tv-ovfilter input[data-filter='end']").dispatch_event("change")
    wait_rev_after(page, before)
    flt = calls(page, "overview")[-1][0]
    assert flt["start"] == "2026-01-01" and flt["end"] == data_today


def test_empty_filter_result_shows_empty_state_not_blank_chart(
    open_ov: Any, artifacts_dir: Path
) -> None:
    page = open_ov(
        "#/overview",
        filters={"range": "7d", "project": "docs-site", "model": "no-such-model"},
    )
    empty = page.locator(f"{OV} [data-empty-state]")
    assert empty.is_visible()
    page.screenshot(path=str(artifacts_dir / "overview-empty-1440x900.png"))
    assert empty.inner_text().strip()
    for name in CHARTS:
        host = page.locator(f"{OV} [data-chart='{name}']")
        assert host.count() == 0 or not host.is_visible(), name
    # One click clears the narrowing filters and the charts come back.
    before = rev(page)
    empty.locator("button").click()
    wait_rev_after(page, before)
    assert not page.locator(f"{OV} [data-empty-state]").is_visible()
    assert _chart_marks(page, "daily") > 3

    # The sessions page shows its own empty state for the same kind of filter.
    page.evaluate(
        "import('./js/filters.js').then(m => m.filters.set({project: 'docs-site',"
        " model: 'no-such-model'}))"
    )
    go(page, "#/sessions")
    assert page.locator("#tv-view .tv-sessions [data-empty-state]").is_visible()
    assert page.locator("#tv-view .tv-sessions tbody tr[data-session]").count() == 0
    assert console_problems(page) == []


def test_long_unicode_project_and_title_do_not_overflow_panel(
    open_ov: Any, artifacts_dir: Path
) -> None:
    for width, height in [(1440, 900), (1100, 720)]:
        page = open_ov("#/overview", width=width, height=height)
        _assert_no_overflow(page)
        recent = page.locator(f"{OV} table.tv-ovrecent")
        assert LONG_TITLE in (
            recent.locator(f"[title='{LONG_TITLE}']").first.get_attribute("title") or ""
        )
        go(page, "#/projects")
        _assert_no_overflow(page)
        cell = page.locator("#tv-view .tv-projects tbody tr[data-project] .tv-clip").filter(
            has_text=LONG_PROJECT[:30]
        )
        assert cell.first.get_attribute("title") == LONG_PROJECT
        go(page, "#/sessions")
        _assert_no_overflow(page)
        go(page, "#/session/s-000")
        _assert_no_overflow(page)
        assert LONG_TITLE in page.locator("#tv-view .tv-session-head").inner_text()


def _assert_no_overflow(page: Any) -> None:
    page.wait_for_timeout(150)
    problems = page.evaluate(
        """() => {
          const out = [];
          const view = document.querySelector('#tv-view');
          if (view.scrollWidth > view.clientWidth + 1) out.push('view ' + view.scrollWidth);
          for (const p of view.querySelectorAll('tv-panel')) {
            const r = p.getBoundingClientRect();
            const v = view.getBoundingClientRect();
            if (r.right > v.right + 1) out.push('panel past view ' + p.className);
            for (const el of p.querySelectorAll('*')) {
              const b = el.getBoundingClientRect();
              if (!b.width || el.closest('.tv-chart-sr')) continue;
              if (el.closest('[class*="tablewrap"]')) continue;  // scrolls horizontally
              if (b.right > r.right + 2) out.push(el.tagName + '.' + el.className + ' ' + b.right
                + '>' + r.right);
            }
          }
          return out.slice(0, 5);
        }"""
    )
    assert problems == []


def test_same_project_has_same_colour_on_overview_projects_sessions(open_ov: Any) -> None:
    page = open_ov("#/overview")
    project = "erp-api"
    expected = css_color(
        page, page.evaluate(f"import('./js/palette.js').then(p => p.colorFor('{project}'))")
    )
    swatch = f".tv-ov-swatch[data-key='{project}']"
    ov = page.locator(f"{OV} {swatch}").first
    assert ov.evaluate("e => getComputedStyle(e).backgroundColor") == expected
    # Bar of that project in the "tokens by project" chart has the same fill.
    fills = page.evaluate(
        "() => [...document.querySelectorAll(\"#tv-view [data-chart='projects'] svg path\")]"
        ".map(p => p.getAttribute('fill'))"
    )
    colour = page.evaluate(f"import('./js/palette.js').then(p => p.colorFor('{project}'))")
    assert colour in fills
    go(page, "#/projects")
    proj = page.locator(f"#tv-view .tv-projects tr[data-project='{project}'] {swatch}")
    assert proj.evaluate("e => getComputedStyle(e).backgroundColor") == expected
    bar = page.locator(f"#tv-view .tv-projects tr[data-project='{project}'] .tv-bar > i")
    assert bar.evaluate("e => getComputedStyle(e).backgroundColor") == expected
    go(page, "#/sessions")
    sess = page.locator(f"#tv-view .tv-sessions tbody {swatch}").first
    assert sess.evaluate("e => getComputedStyle(e).backgroundColor") == expected


def test_unknown_and_partial_cost_flagged_never_zero(open_ov: Any) -> None:
    data = overview_data()
    for s in data["sessions"]:
        s["models"], s["cost"], s["cost_partial"] = [UNPRICED], None, False
    page = open_ov("#/overview", data=data)
    cost = page.locator(f"{OV} [data-total='cost']")
    assert "UNKNOWN" in cost.inner_text()
    text = page.locator("#tv-view").inner_text()
    assert UNPRICED in text
    assert "$0.00" not in text
    # Mixed priced + unpriced -> partial marker "+" and the unpriced model named.
    page2 = open_ov("#/overview")
    cost2 = page2.locator(f"{OV} [data-total='cost']").inner_text()
    assert "+" in cost2 and "$" in cost2
    assert UNPRICED in page2.locator(f"{OV} [data-cost-info]").inner_text()
    # A missing model is labelled "(no model)" in the model chart.
    names = page2.locator(f"{OV} [data-chart='models'] + .tv-chart-sr tbody td:first-child")
    assert "(no model)" in names.all_inner_texts()
    go(page2, "#/sessions")
    unknown_rows = page2.locator("#tv-view .tv-sessions tbody td[data-col='cost']").filter(
        has_text="UNKNOWN"
    )
    assert unknown_rows.count() > 0


def test_xss_in_project_and_title_renders_as_text(open_ov: Any) -> None:
    page = open_ov("#/overview", filters={"range": "all"})
    page.wait_for_timeout(300)
    # Hover the bars so tooltips with untrusted names are rendered too.
    host = page.locator(f"{OV} [data-chart='projects']")
    box = host.bounding_box()
    assert box
    for frac in (0.2, 0.4, 0.6, 0.8):
        page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * frac)
        page.wait_for_timeout(60)
    assert XSS_PROJECT in page.locator(OV).inner_text() or XSS_PROJECT in page.content()
    go(page, "#/projects")
    assert page.locator("#tv-view .tv-projects tbody").get_by_text(XSS_PROJECT).count() >= 1
    go(page, "#/sessions")
    page.locator("#tv-view .tv-sessions-search").fill("script")
    page.wait_for_timeout(200)
    assert page.locator("#tv-view .tv-sessions tbody").get_by_text(XSS_TITLE).count() == 1
    go(page, "#/session/s-009")
    assert XSS_TITLE in page.locator("#tv-view .tv-session-head").inner_text()
    assert page.evaluate("window.__xss") is None
    assert page.locator("#tv-view img[src='x']").count() == 0
    assert page.locator("#tv-view script").count() == 0
    assert console_problems(page) == []


def test_api_rejection_shows_error_banner_with_retry(open_ov: Any) -> None:
    page = open_ov("#/overview", extra_script="window.__tvFail = {overview: true};")
    banner = page.locator(f"{OV} [data-load-error]")
    assert banner.is_visible()
    assert "stub failure" in banner.inner_text()
    page.evaluate("window.__tvFail = {}")
    before = rev(page)
    banner.locator("button").click()
    wait_rev_after(page, before)
    assert not page.locator(f"{OV} [data-load-error]").is_visible()
    assert _chart_marks(page, "daily") > 3
    for route, method, root in [
        ("#/projects", "projects", ".tv-projects"),
        ("#/sessions", "sessions", ".tv-sessions"),
        ("#/session/s-001", "session_turns", ".tv-session"),
    ]:
        page.evaluate(f"window.__tvFail = {{{method}: true}}")
        go(page, route)
        assert page.locator(f"#tv-view {root} [data-load-error]").is_visible(), route
    page.evaluate("window.__tvFail = {}")


def test_live_event_refetch_is_debounced(open_ov: Any) -> None:
    page = open_ov("#/overview")
    page.wait_for_timeout(300)
    n = len(calls(page, "overview"))
    before = rev(page)
    for _ in range(5):
        page.evaluate("tv.onLive({online: 1, total: 2})")
    wait_rev_after(page, before)
    page.wait_for_timeout(400)
    assert len(calls(page, "overview")) == n + 1


def test_no_console_errors_on_any_page_and_screenshots(open_ov: Any, artifacts_dir: Path) -> None:
    for width, height in [(1440, 900), (1100, 720)]:
        page = open_ov("#/overview", width=width, height=height)
        page.wait_for_timeout(800)
        page.screenshot(path=str(artifacts_dir / f"overview-{width}x{height}.png"))
        page.screenshot(
            path=str(artifacts_dir / f"overview-full-{width}x{height}.png"), full_page=False
        )
        page.locator("#tv-view").evaluate("v => { v.scrollTop = v.scrollHeight; }")
        page.wait_for_timeout(300)
        page.screenshot(path=str(artifacts_dir / f"overview-bottom-{width}x{height}.png"))
        for route, name in [
            ("#/projects", "projects"),
            ("#/sessions", "sessions"),
            ("#/session/s-000", "session"),
        ]:
            go(page, route)
            page.wait_for_timeout(700)
            page.screenshot(path=str(artifacts_dir / f"{name}-{width}x{height}.png"))
        assert console_problems(page) == []
