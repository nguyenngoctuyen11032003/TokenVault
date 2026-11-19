"""PDF report export dialog and the Export buttons (Employees, Employee, Home, Overview)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.register_assert_rewrite("conftest_home")

from conftest_home import console_problems  # noqa: E402

pytestmark = pytest.mark.ui
pytest_plugins = ["conftest_home"]

STUB = (Path(__file__).resolve().parent / "fixtures" / "api_stub_report.js").read_text(
    encoding="utf-8"
)
DLG = ".tv-report-dlg"
ROWS = "#tv-view .tv-employees table.tv-emptable tbody tr[data-key]"
EXPORT = "[data-report='export']"


def _calls(page: Any, name: str) -> list[list[Any]]:
    return [c["args"] for c in page.evaluate("window.__tvCalls") if c["name"] == name]


def _open(open_page: Any, route: str) -> Any:
    return open_page(route, extra_script=STUB)


def _click_export(page: Any, which: str = "all") -> None:
    page.locator(f"[data-report-open='{which}']").click()
    page.wait_for_selector(DLG)


def _run(page: Any) -> None:
    page.locator(EXPORT).click()
    page.wait_for_selector("[data-report='result']:not([hidden])")


def test_employees_buttons_and_selection(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    sel = page.locator("[data-report-open='selected']")
    assert page.locator("[data-report-open='all']").inner_text() == "Xuất PDF"
    assert sel.is_disabled() and "(0)" in sel.inner_text()
    page.locator(f"{ROWS} input[data-select]").nth(0).check()
    page.locator(f"{ROWS} input[data-select]").nth(1).check()
    assert sel.is_enabled() and "(2)" in sel.inner_text()
    assert page.evaluate("location.hash") == "#/employees"  # ticking does not open a row
    page.locator("th input[data-select-all]").check()
    total = page.locator(ROWS).count()
    assert f"({total})" in sel.inner_text()
    page.locator("th input[data-select-all]").uncheck()
    assert sel.is_disabled()
    assert not console_problems(page)


def test_export_all_flow_and_open_buttons(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    _click_export(page, "all")
    assert page.locator(f"{DLG} h2").inner_text() == "Xuất báo cáo PDF"
    assert page.locator("[data-report='scope']:checked").get_attribute("value") == "all"
    assert page.locator("[data-report='range']:checked").get_attribute("value") == "today"
    assert "Documents" in page.locator("[data-report='default-dir']").inner_text()
    _run(page)
    assert _calls(page, "export_report") == [[{
        "scope": "all", "range": "today", "per_employee_pages": True, "destination": "default",
    }]]
    assert page.locator("[data-report='file']").inner_text() == "TokenVault-BaoCao-TatCa.pdf"
    page.locator("[data-report='open-file']").click()
    page.locator("[data-report='open-folder']").click()
    page.wait_for_function("window.__tvCalls.filter(c => c.name === 'open_path').length === 2")
    path = "C:\\r\\TokenVault-BaoCao-TatCa.pdf"
    assert _calls(page, "open_path") == [[path, False], [path, True]]
    page.locator("[data-report='close']").click()
    assert page.locator(DLG).count() == 0
    assert not console_problems(page)


def test_export_selected_spec(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    boxes = page.locator(f"{ROWS} input[data-select]")
    keys = [page.locator(ROWS).nth(i).get_attribute("data-key") for i in (0, 2)]
    boxes.nth(0).check()
    boxes.nth(2).check()
    _click_export(page, "selected")
    assert page.locator("[data-report='scope']").count() == 3
    checked = page.locator("[data-report='scope']:checked")
    assert checked.get_attribute("value") == "employee"
    assert "(2)" in page.locator(f"{DLG} .tv-report-dlg__group").first.inner_text()
    page.locator("[data-report='range'][value='30d']").check()
    page.locator("[data-report='per-employee']").uncheck()
    page.locator("[data-report='dest'][value='dialog']").check()
    _run(page)
    spec = _calls(page, "export_report")[0][0]
    assert spec["scope"] == "employee" and sorted(spec["employee_keys"]) == sorted(keys)
    assert spec["range"] == "30d" and spec["per_employee_pages"] is False
    assert spec["destination"] == "dialog"


def test_team_and_custom_range(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    _click_export(page, "all")
    assert page.locator("[data-report='team']").is_disabled()
    page.locator("[data-report='scope'][value='team']").check()
    page.locator("[data-report='team']").select_option("QA")
    page.locator("[data-report='range'][value='custom']").check()
    page.locator("[data-report='start']").fill("2026-11-01")
    page.locator("[data-report='end']").fill("2026-11-10")
    _run(page)
    spec = _calls(page, "export_report")[0][0]
    assert spec == {"scope": "team", "team": "QA", "range": "custom", "start": "2026-11-01",
                    "end": "2026-11-10", "per_employee_pages": True, "destination": "default"}


def test_choose_location_disabled_without_dialog(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    page.evaluate("window.__tvReport.options.can_pick_path = false")
    _click_export(page, "all")
    assert page.locator("[data-report='dest'][value='dialog']").is_disabled()


def test_employee_page_button(open_page: Any) -> None:
    page = _open(open_page, "#/employee/an.tran%40ics.vn")
    btn = page.locator("[data-report-open='employee']")
    assert btn.inner_text() == "Xuất báo cáo PDF"
    btn.click()
    page.wait_for_selector(DLG)
    assert page.locator("[data-report='scope']:checked").get_attribute("value") == "employee"
    assert page.locator("[data-report='per-employee']").is_hidden()
    _run(page)
    spec = _calls(page, "export_report")[0][0]
    assert spec["scope"] == "employee" and spec["employee_keys"] == ["an.tran@ics.vn"]


def test_home_and_overview_buttons(open_page: Any) -> None:
    page = _open(open_page, "#/home")
    _click_export(page, "all")
    assert page.locator("[data-report='range']:checked").get_attribute("value") == "7d"
    page.keyboard.press("Escape")
    assert page.locator(DLG).count() == 0
    page = _open(open_page, "#/overview")
    _click_export(page, "all")
    assert page.locator("[data-report='scope']:checked").get_attribute("value") == "all"
    _run(page)
    assert _calls(page, "export_report")[0][0]["scope"] == "all"
    assert not console_problems(page)


def test_progress_while_running_then_result(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    page.evaluate("window.__tvReport.delay = 1500")
    _click_export(page, "all")
    page.locator(EXPORT).click()
    page.wait_for_selector("[data-report='progress']", state="visible")
    assert page.locator(EXPORT).is_disabled()
    page.wait_for_function("document.querySelector(\"[data-report='progress']\").value === 40")
    assert "biểu đồ" in page.locator(f"{DLG} .tv-report-dlg__progress").inner_text()
    page.keyboard.press("Escape")  # cannot be closed while running
    assert page.locator(DLG).count() == 1
    page.wait_for_selector("[data-report='result']:not([hidden])")


@pytest.mark.parametrize("code,text", [
    ("[empty_scope] x", "Không tìm thấy"),
    ("[invalid_report_spec] Chọn một nhóm", "Chọn một nhóm"),
    ("[busy] x", "Đang có một báo cáo khác"),
    ("[write_failed] disk full", "disk full"),
    ("boom", "boom"),
])
def test_error_codes_show_vietnamese_text(open_page: Any, code: str, text: str) -> None:
    page = _open(open_page, "#/employees")
    page.evaluate("c => { window.__tvReport.fail.export_report = c; }", code)
    _click_export(page, "all")
    page.locator(EXPORT).click()
    err = page.locator("[data-report='error']")
    err.wait_for(state="visible")
    assert text in err.inner_text()
    assert page.locator(EXPORT).is_enabled()
    page.evaluate("window.__tvReport.fail.export_report = null")
    _run(page)  # retry works


def test_cancelled_save_dialog_is_silent(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    page.evaluate("window.__tvReport.fail.export_report = '[cancelled] export cancelled'")
    _click_export(page, "all")
    page.locator(EXPORT).click()
    page.wait_for_function("window.__tvCalls.some(c => c.name === 'export_report')")
    page.wait_for_function("!document.querySelector(\"[data-report='export']\").disabled")
    assert page.locator("[data-report='error']").is_hidden()
    assert page.locator(DLG).count() == 1


def test_dialog_is_accessible_and_traps_focus(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    _click_export(page, "all")
    dlg = page.locator(DLG)
    assert dlg.get_attribute("role") == "dialog" and dlg.get_attribute("aria-modal") == "true"
    for _ in range(40):
        page.keyboard.press("Tab")
    assert page.evaluate(f"document.querySelector('{DLG}').contains(document.activeElement)")
    page.locator("[data-report='cancel']").click()
    assert page.evaluate("document.activeElement.dataset.reportOpen") == "all"


def test_selection_survives_search_and_label_counts_hidden(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    sel = page.locator("[data-report-open='selected']")
    total = page.locator(ROWS).count()
    page.locator(f"{ROWS} input[data-select]").nth(0).check()
    page.locator(f"{ROWS} input[data-select]").nth(1).check()
    first = page.locator(ROWS).nth(0).get_attribute("data-key")
    page.locator("input.tv-employees-search").fill(first.split("@")[0])
    assert page.locator(ROWS).count() < total
    assert sel.is_enabled() and "(2" in sel.inner_text() and "ẩn" in sel.inner_text()
    page.locator("input.tv-employees-search").fill("")
    assert page.locator(f"{ROWS} input[data-select]:checked").count() == 2
    assert "ẩn" not in sel.inner_text() and "(2)" in sel.inner_text()
    sel.click()
    page.wait_for_selector(DLG)
    _run(page)
    assert len(_calls(page, "export_report")[0][0]["employee_keys"]) == 2


def test_open_buttons_hidden_when_unavailable(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    page.evaluate("Object.assign(window.__tvReport.options, {can_open: false, can_reveal: false})")
    _click_export(page, "all")
    _run(page)
    assert page.locator("[data-report='open-file']").count() == 0
    assert page.locator("[data-report='open-folder']").count() == 0
    assert page.locator("[data-report='folder']").is_visible()
    assert page.locator("[data-report='close']").is_visible()


def test_range_fallback_note_and_employee_cap(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    page.evaluate("window.__tvReport.options.max_employees = 1")
    page.evaluate("localStorage.setItem('tv.filters.v1', JSON.stringify({range: '90d'}))")
    boxes = page.locator(f"{ROWS} input[data-select]")
    boxes.nth(0).check()
    boxes.nth(1).check()
    page.locator("[data-report-open='selected']").click()
    page.wait_for_selector(DLG)
    assert page.locator("[data-report='cap-note']").is_visible()
    assert page.locator(EXPORT).is_disabled()
    page.locator("[data-report='scope'][value='all']").check()
    assert page.locator(EXPORT).is_enabled()
    assert page.locator("[data-report='cap-note']").is_hidden()


def test_route_abort_waits_for_running_export(open_page: Any) -> None:
    page = _open(open_page, "#/employees")
    page.evaluate("window.__tvReport.delay = 800")
    _click_export(page, "all")
    page.locator(EXPORT).click()
    page.evaluate("location.hash = '#/home'")
    page.wait_for_timeout(300)
    assert page.locator(DLG).count() == 1  # still running: not torn down
    page.wait_for_function("document.querySelectorAll('.tv-report-dlg').length === 0")
    assert len(_calls(page, "export_report")) == 1
