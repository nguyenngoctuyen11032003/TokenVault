"""Employees list (#/employees) and employee detail (#/employee/<key>)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

pytest.register_assert_rewrite("conftest_home")

from conftest_home import (  # noqa: E402
    ODD_KEY,
    XSS_KEY,
    XSS_NAME,
    console_problems,
    home_data,
    rev,
    wait_rev_after,
)

pytestmark = pytest.mark.ui
pytest_plugins = ["conftest_home"]

HEAD = "#tv-view tv-panel.tv-employee-head .tv-panel__tab"
LIST_ROWS = "#tv-view .tv-employees table.tv-emptable tbody tr[data-key]"


def _calls(page: Any, name: str) -> list[list[Any]]:
    return [c["args"] for c in page.evaluate("window.__tvCalls") if c["name"] == name]


def _detail_hash(key: str) -> str:
    return "#/employee/" + quote(key, safe="")


def _wait_detail(page: Any) -> None:
    page.wait_for_selector("#tv-view[data-route='employee'][data-route-state]")
    page.wait_for_selector("#tv-view .tv-employee[data-tv-rev]:not([data-tv-rev='0'])")


def test_employee_row_click_opens_detail_with_email_in_header(open_page: Any) -> None:
    page = open_page("#/home")
    page.locator("#tv-view table.tv-emptable tbody tr[data-key='an.tran@ics.vn'] td").nth(
        1
    ).click()
    _wait_detail(page)
    assert page.evaluate("location.hash") == "#/employee/an.tran%40ics.vn"
    head = page.locator(HEAD).inner_text()
    assert "an.tran@ics.vn" in head and "Dev" in head
    assert _calls(page, "employee")[-1] == ["an.tran@ics.vn", "today"]
    # Devices with online state.
    devices = page.locator("#tv-view .tv-employee-devices [data-device]")
    assert devices.count() == 2
    assert "LAPTOP-AN" in devices.nth(0).inner_text()
    assert devices.nth(0).locator(".tv-dot--on").count() == 1
    assert devices.nth(1).locator(".tv-dot--on").count() == 0
    # Session list: title, project, model, tokens, cost (a missing title has a placeholder).
    sessions = page.locator("#tv-view .tv-employee-sessions tbody tr")
    assert sessions.count() == 3
    row = sessions.nth(0).inner_text()
    assert "Fix login bug" in row and "TokenVault" in row and "claude-opus-4-7" in row
    assert console_problems(page) == []


def test_range_switch_today_7d_30d_changes_numbers(open_page: Any) -> None:
    page = open_page("#/employee/an.tran%40ics.vn")
    gauge = page.locator("#tv-view tv-gauge[data-kpi='range-tokens'] .tv-gauge__value")
    buttons = page.locator("#tv-view .tv-rangeswitch button[data-range]")
    assert [buttons.nth(i).get_attribute("data-range") for i in range(3)] == [
        "today", "7d", "30d"
    ]
    assert buttons.nth(0).get_attribute("aria-pressed") == "true"
    assert gauge.inner_text() == "940K"
    seen = {}
    for rng, expected in (("7d", "4.98M"), ("30d", "19.7M"), ("today", "940K")):
        before = rev(page)
        page.locator(f"#tv-view .tv-rangeswitch button[data-range='{rng}']").click()
        wait_rev_after(page, before)
        seen[rng] = gauge.inner_text()
        assert seen[rng] == expected
        assert _calls(page, "employee")[-1] == ["an.tran@ics.vn", rng]
        pressed = page.locator("#tv-view .tv-rangeswitch button[aria-pressed='true']")
        assert pressed.get_attribute("data-range") == rng
    assert page.locator("#tv-view .tv-employee-sessions tbody tr").count() == 3
    assert page.evaluate("import('./js/filters.js').then(m => m.filters.get().range)") == "today"
    assert console_problems(page) == []


def test_key_with_at_space_unicode_round_trips_in_url(open_page: Any) -> None:
    page = open_page("#/employees")
    page.locator(LIST_ROWS).first.wait_for()
    row = page.locator(LIST_ROWS).filter(has_text="Unknown account (ketoan@PC Kế toán)")
    assert row.get_attribute("data-key") == ODD_KEY
    row.locator("td").nth(2).click()
    _wait_detail(page)
    assert page.evaluate("decodeURIComponent(location.hash.slice('#/employee/'.length))") == (
        ODD_KEY
    )
    assert page.evaluate("location.hash") == _detail_hash(ODD_KEY)
    assert _calls(page, "employee")[-1][0] == ODD_KEY
    assert "Unknown account (ketoan@PC Kế toán)" in page.locator(HEAD).inner_text()
    # Deep link straight to the URL works too.
    page2 = open_page(_detail_hash(ODD_KEY))
    assert _calls(page2, "employee")[-1][0] == ODD_KEY
    assert console_problems(page) == [] and console_problems(page2) == []


def test_employees_list_search_team_filter_and_sort(open_page: Any) -> None:
    page = open_page("#/employees")
    rows = page.locator(LIST_ROWS)
    assert rows.count() == 12
    page.locator("#tv-view .tv-employees input[type='search']").fill("tran")
    assert rows.count() == 1 and rows.first.get_attribute("data-key") == "an.tran@ics.vn"
    # Accent-insensitive search on unicode names.
    page.locator("#tv-view .tv-employees input[type='search']").fill("ke toan")
    assert rows.count() == 1 and rows.first.get_attribute("data-key") == ODD_KEY
    page.locator("#tv-view .tv-employees input[type='search']").fill("")
    before = rev(page)
    page.locator("#tv-view .tv-employees select[data-filter='team']").select_option("QA")
    wait_rev_after(page, before)
    assert rows.count() == 3
    assert _calls(page, "employees")[-1][0]["team"] == "QA"
    # Sort by tokens ascending then descending.
    sort = page.locator("#tv-view .tv-employees th button[data-sort='tokens']")
    sort.click()
    first = rows.first.get_attribute("data-key")
    sort.click()
    assert rows.first.get_attribute("data-key") != first
    th = page.locator("#tv-view .tv-employees th[data-col='tokens']")
    assert th.get_attribute("aria-sort") in {"ascending", "descending"}
    assert console_problems(page) == []


def test_unknown_cost_on_detail_shows_UNKNOWN(open_page: Any) -> None:  # noqa: N802
    page = open_page("#/employee/an.tran%40ics.vn", data=home_data(unknown_cost=True))
    kpi = page.locator("#tv-view .tv-employee [data-kpi='range-cost']").inner_text()
    assert "UNKNOWN" in kpi
    costs = page.locator("#tv-view .tv-employee-sessions td[data-col='cost']").all_inner_texts()
    assert costs and set(costs) == {"UNKNOWN"}
    assert "$0" not in page.locator("#tv-view").inner_text()
    note = page.locator("#tv-view .tv-employee [data-cost-note]").first
    assert "ước tính theo giá API" in note.inner_text()
    assert console_problems(page) == []


def test_detail_untrusted_name_and_charts(open_page: Any) -> None:
    page = open_page(_detail_hash(XSS_KEY))
    assert page.locator("#tv-view img").count() == 0
    assert page.evaluate("window.__xss") is None
    assert XSS_NAME in page.locator("#tv-view .tv-employee").inner_text()
    for name in ("daily", "models"):
        chart = page.locator(f"#tv-view .tv-chart[data-chart='{name}']")
        box = chart.bounding_box()
        assert box and box["width"] > 150 and box["height"] > 120, name
        assert chart.locator("svg").count() == 1, name
        assert chart.get_attribute("aria-label"), name
    # Project breakdown bars in palette colours with share %.
    bars = page.locator("#tv-view .tv-employee-projects [data-project]")
    assert bars.count() == 3
    assert "%" in bars.first.inner_text()
    assert console_problems(page) == []


def test_live_event_on_detail_keeps_range(open_page: Any) -> None:
    page = open_page("#/employee/an.tran%40ics.vn")
    before = rev(page)
    page.locator("#tv-view .tv-rangeswitch button[data-range='7d']").click()
    wait_rev_after(page, before)
    page.evaluate(
        "() => { window.__tvHomeData.employee['an.tran@ics.vn']['7d'].totals.total_tokens"
        " = 7770000; }"
    )
    before = rev(page)
    page.evaluate("tv.onLive({online: 3, total: 4})")
    wait_rev_after(page, before)
    gauge = page.locator("#tv-view tv-gauge[data-kpi='range-tokens'] .tv-gauge__value")
    assert gauge.inner_text() == "7.77M"
    assert _calls(page, "employee")[-1] == ["an.tran@ics.vn", "7d"]
    assert console_problems(page) == []


def test_unknown_employee_shows_error_panel_not_crash(open_page: Any) -> None:
    page = open_page("#/employee/nobody%40x.vn", settle=False)
    page.wait_for_selector("#tv-view .tv-employee [data-load-error]")
    assert page.tv_errors == []


@pytest.mark.parametrize(("width", "height"), [(1440, 900), (1100, 720)])
def test_screenshot_employee(
    open_page: Any, artifacts_dir: Path, width: int, height: int
) -> None:
    page = open_page("#/employee/an.tran%40ics.vn", width=width, height=height)
    before = rev(page)
    page.locator("#tv-view .tv-rangeswitch button[data-range='7d']").click()
    wait_rev_after(page, before)
    page.wait_for_timeout(900)
    metrics = page.evaluate(
        "() => { const v = document.querySelector('#tv-view');"
        " return [v.scrollWidth, v.clientWidth]; }"
    )
    assert metrics[0] <= metrics[1]
    page.screenshot(path=str(artifacts_dir / f"employee-{width}x{height}.png"))
    page.locator("#tv-view").evaluate("v => { v.scrollTop = v.scrollHeight; }")
    page.wait_for_timeout(200)
    page.screenshot(path=str(artifacts_dir / f"employee-{width}x{height}-bottom.png"))
    lst = open_page("#/employees", width=width, height=height)
    lst.wait_for_timeout(300)
    lst.screenshot(path=str(artifacts_dir / f"employees-{width}x{height}.png"))
    assert console_problems(page) == [] and console_problems(lst) == []


TEAMS_REJECT = (
    "window.pywebview.api.teams = () => Promise.reject(new Error('teams() not exposed'));"
)


def test_employees_page_works_when_teams_api_rejects(open_page: Any) -> None:
    page = open_page("#/employees", extra_script=TEAMS_REJECT)
    assert page.locator(LIST_ROWS).count() == 12
    options = page.locator("#tv-view .tv-employees select[data-filter='team'] option")
    assert [options.nth(i).get_attribute("value") for i in range(options.count())] == [
        "", "Dev", "QA", "BA / PM"
    ]
    assert page.locator("#tv-view .tv-employees [data-load-error]").is_hidden()
    assert page.tv_errors == []


def _fill(page: Any) -> float:
    return float(page.evaluate(
        "getComputedStyle(document.querySelector(\"tv-gauge[data-kpi='range-tokens']"
        " .tv-gauge__dial\")).getPropertyValue('--tv-gauge-p')"
    ))


def test_gauge_scale_uses_busiest_day_reference(open_page: Any) -> None:
    an = open_page("#/employee/an.tran%40ics.vn")
    minh = open_page("#/employee/minh.le%40ics.vn")
    a, m = _fill(an), _fill(minh)
    assert 0 < a < 1 and 0 < m < 1
    assert abs(a - m) > 0.05
    note = an.locator("#tv-view .tv-employee [data-scale-note]")
    assert note.is_visible() and "Thang đo" in note.inner_text()
    # The 30-day reference is fetched once, not on every range switch.
    refs = [c for c in _calls(an, "employee") if c[1] == "30d"]
    before = rev(an)
    an.locator("#tv-view .tv-rangeswitch button[data-range='7d']").click()
    wait_rev_after(an, before)
    assert [c for c in _calls(an, "employee") if c[1] == "30d"] == refs
    assert "7" in note.inner_text()


def test_heading_shows_placeholder_until_loaded(open_page: Any) -> None:
    page = open_page("#/home")
    page.evaluate("window.__tvHomeDelay = 1500; location.hash = '#/employee/an.tran%40ics.vn'")
    page.wait_for_selector("#tv-view[data-route='employee'] tv-panel.tv-employee-head")
    tab = page.locator(HEAD)
    assert tab.inner_text() == "Đang tải thông tin nhân viên…"
    assert "an.tran" not in tab.inner_text()
    _wait_detail(page)
    assert "an.tran@ics.vn" in tab.inner_text()
