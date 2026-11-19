"""Projects (#/projects), sessions list (#/sessions) and session detail (#/session/<id>)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

pytest.register_assert_rewrite("conftest_overview")

from conftest_overview import (  # noqa: E402
    HUGE_ID,
    ORDER_ID,
    calls,
    console_problems,
    go,
    overview_data,
    rev,
    wait_page,
    wait_rev_after,
)

pytestmark = pytest.mark.ui
pytest_plugins = ["conftest_overview"]

PROJ_ROWS = "#tv-view .tv-projects table.tv-projtable tbody tr[data-project]"
SESS_ROWS = "#tv-view .tv-sessions table.tv-sesstable tbody tr[data-session]"
TURN_ROWS = "#tv-view .tv-session table.tv-turntable tbody tr[data-turn]"


def test_projects_table_and_chart(open_ov: Any) -> None:
    page = open_ov("#/projects")
    rows = page.locator(PROJ_ROWS)
    assert rows.count() == 6
    tokens = [
        int(r.get_attribute("data-tokens") or 0) for r in rows.all()
    ]
    assert tokens == sorted(tokens, reverse=True)
    first = rows.first
    for col in ["project", "tokens", "share", "cost", "sessions", "employees", "last"]:
        assert first.locator(f"[data-col='{col}']").count() == 1, col
    assert page.locator("#tv-view .tv-projects [data-chart='projects'] svg path").count() > 3
    note = page.locator("#tv-view .tv-projects [data-cost-note]").inner_text()
    assert "ước tính theo giá API" in note
    assert console_problems(page) == []


def test_project_click_sets_filter_and_opens_sessions(open_ov: Any) -> None:
    page = open_ov("#/projects")
    page.locator(f"{PROJ_ROWS}[data-project='erp-api'] [data-col='project'] a").click()
    page.wait_for_selector("#tv-view[data-route='sessions'][data-route-state]")
    wait_page(page)
    assert page.evaluate("import('./js/filters.js').then(m => m.filters.get().project)") == (
        "erp-api"
    )
    assert calls(page, "sessions")[-1][0]["project"] == "erp-api"
    projects = page.locator(f"{SESS_ROWS} [data-col='project']").all_inner_texts()
    assert projects and set(projects) == {"erp-api"}
    # Back on projects, the chosen project is highlighted; the list still shows all projects.
    go(page, "#/projects")
    assert page.locator(PROJ_ROWS).count() == 6
    assert page.locator(f"{PROJ_ROWS}[aria-selected='true']").get_attribute("data-project") == (
        "erp-api"
    )


def test_sessions_list_search_sort_and_load_more(open_ov: Any) -> None:
    data = overview_data(count=520)
    page = open_ov("#/sessions", data=data, filters={"range": "all"})
    rows = page.locator(SESS_ROWS)
    assert rows.count() == 200
    total = len(data["sessions"])
    assert str(total) in page.locator("#tv-view .tv-sessions-count").inner_text()
    for col in ["title", "project", "employee", "model", "tokens", "cost", "started"]:
        assert rows.first.locator(f"[data-col='{col}']").count() == 1, col
    more = page.locator("#tv-view .tv-sessions [data-load-more]")
    more.click()
    assert rows.count() == 400
    more.click()
    assert rows.count() == total
    assert not more.is_visible()
    # Sort by tokens (descending first).
    page.locator("#tv-view .tv-sessions th[data-sort='tokens'] button").click()
    vals = [int(v) for v in page.locator(SESS_ROWS).evaluate_all(
        "rs => rs.slice(0, 50).map(r => r.dataset.tokens)")]
    assert vals == sorted(vals, reverse=True)
    assert page.locator("#tv-view .tv-sessions th[data-sort='tokens']").get_attribute(
        "aria-sort"
    ) == "descending"
    page.locator("#tv-view .tv-sessions th[data-sort='tokens'] button").click()
    vals = [int(v) for v in page.locator(SESS_ROWS).evaluate_all(
        "rs => rs.slice(0, 50).map(r => r.dataset.tokens)")]
    assert vals == sorted(vals)
    # Search (accent-insensitive) narrows the list and resets paging.
    page.locator("#tv-view .tv-sessions-search").fill("thiet ke dashboard")
    page.wait_for_function(
        f"document.querySelectorAll(\"{SESS_ROWS}\").length < 200"
    )
    titles = page.locator(f"{SESS_ROWS} [data-col='title']").all_inner_texts()
    assert titles and all("Thiết kế dashboard" in t for t in titles)
    # Click a row -> session detail with the id URL-encoded.
    page.locator(SESS_ROWS).first.locator("[data-col='title'] a").click()
    page.wait_for_selector("#tv-view[data-route='session'][data-route-state]")
    assert page.evaluate("location.hash").startswith("#/session/s-")
    assert console_problems(page) == []


def test_session_detail_lists_turns_in_time_order(open_ov: Any) -> None:
    page = open_ov("#/session/" + quote(ORDER_ID, safe=""))
    assert calls(page, "session_turns")[-1] == [ORDER_ID]
    rows = page.locator(TURN_ROWS)
    assert rows.count() == 12
    times = rows.evaluate_all("rs => rs.map(r => r.dataset.ts)")
    assert times == sorted(times)
    assert [r.get_attribute("data-turn") for r in rows.all()] == [str(i) for i in range(1, 13)]
    # Sidechain (sub-agent) turns are marked.
    side = page.locator(f"{TURN_ROWS}[data-sidechain]")
    assert side.count() == 2
    assert side.first.locator(".tv-session-badge").is_visible()
    # Header: title, project, employee, models, totals.
    head = page.locator("#tv-view .tv-session-head").inner_text()
    assert "Phiên kiểm tra thứ tự lượt" in head
    assert "docs-site" in head and "an.tran@ics.vn" in head
    assert "claude-sonnet-4-6" in head
    # Chart: stacked bars per turn with a sub-agent series.
    assert page.locator("#tv-view [data-chart='turns'] svg path").count() > 10
    legend = page.locator("#tv-view [data-chart='turns'] + .tv-chart-sr thead").inner_text()
    assert "sub-agent" in legend.lower() or "tác tử" in legend.lower()
    assert console_problems(page) == []


def test_session_with_5000_turns_renders_quickly_and_stays_paginated(
    open_ov: Any, artifacts_dir: Path
) -> None:
    page = open_ov("#/home", settle=False)
    page.wait_for_selector("#tv-view[data-route-state]")
    start = time.perf_counter()
    page.evaluate(f"location.hash = '#/session/{HUGE_ID}'")
    page.wait_for_selector("#tv-view .tv-session[data-tv-rev]:not([data-tv-rev='0'])")
    page.wait_for_selector("#tv-view [data-chart='turns'] svg")
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, elapsed
    rows = page.locator(TURN_ROWS)
    assert 0 < rows.count() <= 200
    # The chart is downsampled (no 5,000 bars in the DOM).
    paths = page.locator("#tv-view [data-chart='turns'] svg path").count()
    assert paths < 2500
    page.wait_for_timeout(300)
    page.screenshot(path=str(artifacts_dir / "session-5000-turns-1440x900.png"))
    info = page.locator("#tv-view .tv-session-pager").inner_text()
    assert "5.000" in info
    first_turn = rows.first.get_attribute("data-turn")
    before = rev(page)
    page.locator("#tv-view .tv-session-pager [data-page-next]").click()
    page.wait_for_function(
        f"document.querySelector(\"{TURN_ROWS}\").dataset.turn !== '{first_turn}'"
    )
    assert rev(page) >= before
    last = page.locator("#tv-view .tv-session-pager [data-page-last]")
    last.click()
    page.wait_for_function(
        f"[...document.querySelectorAll(\"{TURN_ROWS}\")].pop().dataset.turn === '5000'"
    )
    assert console_problems(page) == []


def test_session_header_falls_back_when_session_row_unknown(open_ov: Any) -> None:
    data = overview_data()
    data["turns"]["ghost"] = data["turns"][ORDER_ID]
    page = open_ov("#/session/ghost", data=data)
    assert page.locator(TURN_ROWS).count() == 12
    head = page.locator("#tv-view .tv-session-head").inner_text()
    assert "ghost" in head
    assert console_problems(page) == []


def test_live_refetch_keeps_sessions_scroll(open_ov: Any) -> None:
    page = open_ov("#/sessions", width=1100, height=720)
    view = page.locator("#tv-view")
    view.evaluate("v => { v.scrollTop = 300; }")
    scroll = view.evaluate("v => v.scrollTop")
    before = rev(page)
    page.evaluate("tv.onLive({online: 1, total: 2})")
    wait_rev_after(page, before)
    assert view.evaluate("v => v.scrollTop") == scroll
