"""Settings (#/settings) and Diagnostics (#/diagnostics) pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.register_assert_rewrite("conftest_installer")

from conftest_installer import (  # noqa: E402
    FINGERPRINT,
    LONG_NAME,
    XSS,
    calls,
    console_problems,
    mgr_data,
    rev,
    wait_rev_after,
)

pytestmark = pytest.mark.ui
pytest_plugins = ["conftest_installer"]

DEVICES = "#tv-view table.tv-settings-devices tbody tr[data-device]"
DIALOG = ".tv-installer-dlg[role='dialog']"
WARN = "#tv-view [data-diag-warning]"


# ---------------------------------------------------------------- settings


def test_settings_shows_port_actually_bound(open_mgr: Any) -> None:
    page = open_mgr("#/settings", data=mgr_data(port=47801))
    assert page.locator("#tv-view [data-bound-port]").inner_text() == "47801"
    warning = page.locator("#tv-view [data-port-warning]")
    assert warning.is_visible()
    assert "47800" in warning.inner_text() and "47801" in warning.inner_text()
    # Static-IP recommendation and the Manager's addresses are shown.
    text = page.locator("#tv-view").inner_text()
    assert "IP" in text
    hosts = page.locator("#tv-view .tv-settings-hosts [data-host]")
    assert [hosts.nth(i).get_attribute("data-host") for i in range(hosts.count())] == [
        "192.168.1.10", "MANAGER-PC"
    ]
    assert "mgr-7f3a2c" in text
    assert console_problems(page) == []


def test_settings_port_without_fallback_has_no_warning(open_mgr: Any) -> None:
    page = open_mgr("#/settings")
    assert page.locator("#tv-view [data-bound-port]").inner_text() == "47800"
    assert not page.locator("#tv-view [data-port-warning]").is_visible()


def test_settings_static_ip_hint_from_manager_is_shown(open_mgr: Any) -> None:
    data = mgr_data()
    data["settings"]["static_ip_hint"] = "Reserve 192.168.1.10 in the router (DHCP)."
    page = open_mgr("#/settings", data=data)
    assert "Reserve 192.168.1.10" in page.locator("#tv-view [data-static-ip]").inner_text()


def test_reassign_team_updates_employee_table(open_mgr: Any) -> None:
    page = open_mgr("#/settings")
    row = page.locator(f"{DEVICES}[data-device='dev-02']")
    assert row.locator("td[data-col='team'] select").input_value() == "Dev"
    before = rev(page)
    row.locator("td[data-col='team'] select").select_option("QA")
    wait_rev_after(page, before)
    assert calls(page, "set_device_team") == [["dev-02", "QA"]]
    row = page.locator(f"{DEVICES}[data-device='dev-02']")
    assert row.locator("td[data-col='team'] select").input_value() == "QA"
    assert row.get_attribute("data-team") == "QA"
    # The table was re-read from the Manager after the change.
    assert len(calls(page, "devices")) >= 2
    assert console_problems(page) == []


def test_reassign_team_to_new_team_via_dialog(open_mgr: Any) -> None:
    page = open_mgr("#/settings")
    row = page.locator(f"{DEVICES}[data-device='dev-01']")
    row.locator("td[data-col='team'] select").select_option("__new__")
    dialog = page.locator(DIALOG)
    dialog.wait_for()
    field = dialog.locator("input[data-dlg='input']")
    field.fill("Dev/Ops")
    page.locator(f"{DIALOG} button[data-dlg='confirm']").click()
    assert dialog.locator("[data-dlg='error']").is_visible()
    field.fill("Thiết kế")
    before = rev(page)
    page.locator(f"{DIALOG} button[data-dlg='confirm']").click()
    wait_rev_after(page, before)
    assert calls(page, "set_device_team") == [["dev-01", "Thiết kế"]]
    assert page.locator(
        f"{DEVICES}[data-device='dev-01'] td[data-col='team'] select"
    ).input_value() == "Thiết kế"


def test_reassign_cancel_restores_select(open_mgr: Any) -> None:
    page = open_mgr("#/settings")
    select = page.locator(f"{DEVICES}[data-device='dev-01'] td[data-col='team'] select")
    select.select_option("__new__")
    page.locator(DIALOG).wait_for()
    page.keyboard.press("Escape")
    page.locator(DIALOG).wait_for(state="detached")
    assert select.input_value() == "Dev"
    assert calls(page, "set_device_team") == []


def test_revoke_device_requires_confirmation(open_mgr: Any) -> None:
    page = open_mgr("#/settings")
    button = page.locator(f"{DEVICES}[data-device='qa-01'] button[data-action='revoke-device']")
    button.click()
    dialog = page.locator(DIALOG)
    dialog.wait_for()
    assert "hoa.pham@ics.vn" in dialog.inner_text()
    page.keyboard.press("Escape")
    dialog.wait_for(state="detached")
    assert calls(page, "revoke_device") == []
    button.click()
    before = rev(page)
    page.locator(f"{DIALOG} button[data-dlg='confirm']").click()
    wait_rev_after(page, before)
    assert calls(page, "revoke_device") == [["qa-01"]]
    assert page.locator(f"{DEVICES}[data-device='qa-01']").count() == 0


def test_settings_devices_show_online_names_and_escape_xss(open_mgr: Any) -> None:
    page = open_mgr("#/settings")
    assert page.locator(DEVICES).count() == 5
    on = page.locator(f"{DEVICES}[data-device='dev-01'] .tv-dot")
    assert "tv-dot--on" in (on.get_attribute("class") or "")
    off = page.locator(f"{DEVICES}[data-device='dev-02'] .tv-dot")
    assert "tv-dot--on" not in (off.get_attribute("class") or "")
    assert page.evaluate("window.__xss") is None
    assert page.locator("#tv-view img").count() == 0
    assert XSS in page.locator(f"{DEVICES}[data-device='x-01']").inner_text()
    name = page.locator(f"{DEVICES}[data-device='qa-02'] .tv-settings-name")
    assert name.get_attribute("title") == LONG_NAME
    assert name.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
    assert console_problems(page) == []


def test_settings_toggles_and_hosts_are_saved(open_mgr: Any) -> None:
    page = open_mgr("#/settings")
    toggle = page.locator("#tv-view button[role='switch'][data-setting='start_with_windows']")
    assert toggle.get_attribute("aria-checked") == "true"
    toggle.click()
    page.wait_for_function("window.__tvCalls.some(c => c.name === 'save_settings')")
    assert calls(page, "save_settings") == [[{"start_with_windows": False}]]
    page.wait_for_function(
        "document.querySelector(\"[data-setting='start_with_windows']\")"
        ".getAttribute('aria-checked') === 'false'"
    )
    transparent = page.locator("#tv-view button[role='switch'][data-setting='transparent_window']")
    assert page.locator("#tv-view [data-restart-note]").is_visible()
    transparent.click()
    page.wait_for_function("window.__tvCalls.filter(c => c.name === 'save_settings').length == 2")
    assert calls(page, "save_settings")[-1] == [{"transparent_window": False}]
    # Hosts: remove one, add one (invalid first), save.
    page.click("#tv-view .tv-settings-hosts [data-host='MANAGER-PC'] [data-action='remove-host']")
    page.fill("#tv-settings-host", "bad host!")
    page.click("#tv-view [data-action='add-host']")
    assert page.locator("#tv-view [data-host-error]").is_visible()
    page.fill("#tv-settings-host", "10.0.5.23")
    page.press("#tv-settings-host", "Enter")
    page.click("#tv-view [data-action='save-hosts']")
    page.wait_for_function("window.__tvCalls.filter(c => c.name === 'save_settings').length == 3")
    assert calls(page, "save_settings")[-1] == [{"hosts": ["192.168.1.10", "10.0.5.23"]}]
    assert console_problems(page) == []


def test_settings_toggle_failure_reverts_and_shows_error(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {"save_settings": "registry access denied"}
    page = open_mgr("#/settings", data=data)
    toggle = page.locator("#tv-view button[role='switch'][data-setting='start_with_windows']")
    toggle.click()
    page.locator("#tv-view [data-save-error]").wait_for()
    assert "registry access denied" in page.locator("#tv-view [data-save-error]").inner_text()
    assert toggle.get_attribute("aria-checked") == "true"


def test_settings_load_failure_shows_retry(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {"devices": "db locked"}
    page = open_mgr("#/settings", data=data)
    banner = page.locator("#tv-view [data-load-error]")
    banner.wait_for()
    before = rev(page)
    banner.locator("button").click()
    wait_rev_after(page, before)
    assert page.locator("#tv-view [data-load-error]").count() == 0
    assert page.locator(DEVICES).count() == 5


def test_settings_empty_device_list(open_mgr: Any) -> None:
    page = open_mgr("#/settings", data=mgr_data(empty=True))
    empty = page.locator("#tv-view [data-devices-empty]")
    assert empty.is_visible()
    assert empty.locator("a[href='#/installer']").count() == 1


# ---------------------------------------------------------------- diagnostics


def test_diagnostics_warns_when_firewall_rule_missing(open_mgr: Any) -> None:
    page = open_mgr("#/diagnostics", data=mgr_data(firewall=False))
    banner = page.locator(WARN)
    assert banner.is_visible()
    assert banner.get_attribute("role") == "alert"
    assert banner.get_attribute("data-kind") == "firewall"
    assert "47800" in banner.inner_text()
    page.wait_for_selector("#tv-view .tv-diagnostics-checks li[data-check='firewall_rule']")
    row = page.locator("#tv-view .tv-diagnostics-checks li[data-check='firewall_rule']")
    assert row.get_attribute("data-ok") == "false"
    assert "rule not found" in row.inner_text()
    # Manager warnings are listed too.
    assert "Firewall rule" in page.locator("#tv-view [data-warnings]").inner_text()
    assert console_problems(page) == []


def test_diagnostics_healthy_has_no_warning_banner(open_mgr: Any) -> None:
    page = open_mgr("#/diagnostics")
    page.wait_for_selector("#tv-view .tv-diagnostics-checks li[data-check]")
    assert page.locator(WARN).count() == 0
    checks = page.locator("#tv-view .tv-diagnostics-checks li[data-check]")
    assert checks.count() == 4
    assert {checks.nth(i).get_attribute("data-ok") for i in range(4)} == {"true"}
    assert page.locator("#tv-view [data-stat='connected']").inner_text().startswith("2")
    assert page.locator("#tv-view [data-stat='rejected']").inner_text() == "3"
    # Connected devices are listed by employee name.
    assert "tuyen.nguyen@ics.vn" in page.locator("#tv-view [data-connected]").inner_text()
    fp = page.locator("#tv-view [data-fingerprint]")
    assert fp.inner_text().replace(":", "").replace(" ", "").lower() == FINGERPRINT[:16]
    assert fp.get_attribute("title") == FINGERPRINT
    assert console_problems(page) == []


def test_diagnostics_port_fallback_warning(open_mgr: Any) -> None:
    page = open_mgr("#/diagnostics", data=mgr_data(port=47801))
    banner = page.locator(f"{WARN}[data-kind='port']")
    assert banner.is_visible()
    assert "47801" in banner.inner_text()


def test_copy_fingerprint_copies_full_value(open_mgr: Any) -> None:
    page = open_mgr("#/diagnostics", grant_clipboard=True)
    page.click("#tv-view button[data-action='copy-fingerprint']")
    page.wait_for_selector("#tv-view button[data-action='copy-fingerprint'][data-copied]")
    assert page.evaluate("navigator.clipboard.readText()") == FINGERPRINT


def test_regenerate_certificate_requires_checkbox(open_mgr: Any, artifacts_dir: Path) -> None:
    page = open_mgr("#/diagnostics")
    page.click("#tv-view button[data-action='regenerate']")
    dialog = page.locator(DIALOG)
    dialog.wait_for()
    page.wait_for_timeout(250)
    page.screenshot(path=str(artifacts_dir / "diagnostics-regenerate-dialog-1440x900.png"))
    assert dialog.get_attribute("role") == "dialog"
    confirm = dialog.locator("button[data-dlg='confirm']")
    assert confirm.is_disabled()
    assert dialog.locator("input[data-dlg='check']").count() == 1
    page.keyboard.press("Escape")
    dialog.wait_for(state="detached")
    assert calls(page, "regenerate_certificate") == []
    page.click("#tv-view button[data-action='regenerate']")
    dialog.wait_for()
    dialog.locator("input[data-dlg='check']").check()
    assert confirm.is_enabled()
    confirm.click()
    page.locator("#tv-view [data-regen-result]").wait_for()
    assert calls(page, "regenerate_certificate") == [[]]
    assert "All adapters must be reinstalled." in page.locator(
        "#tv-view [data-regen-result]"
    ).inner_text()
    page.wait_for_function(
        "document.querySelector('#tv-view [data-fingerprint]').title.startsWith('ab12')"
    )
    assert console_problems(page) == []


def test_diagnostics_auto_refresh_and_cleanup_on_route_change(open_mgr: Any) -> None:
    page = open_mgr("#/diagnostics", refresh_ms=250)
    page.wait_for_function(
        "window.__tvCalls.filter(c => c.name === 'diagnostics').length >= 3"
    )
    page.evaluate("window.__tvMgr.diagnostics.rejected_count = 9")
    page.wait_for_function("document.querySelector(\"[data-stat='rejected']\").textContent === '9'")
    page.evaluate("location.hash = '#/home'")
    page.wait_for_selector("#tv-view[data-route='home'][data-route-state]")
    n = len(calls(page, "diagnostics"))
    page.wait_for_timeout(900)
    page.evaluate("tv.onLive({online: 1, total: 2})")
    page.wait_for_timeout(500)
    assert len(calls(page, "diagnostics")) == n


def test_diagnostics_refreshes_on_live_event_debounced(open_mgr: Any) -> None:
    page = open_mgr("#/diagnostics", refresh_ms=60_000)
    n = len(calls(page, "diagnostics"))
    before = rev(page)
    page.evaluate("for (let i = 0; i < 8; i++) tv.emitLive({online: i, total: 9})")
    wait_rev_after(page, before)
    page.wait_for_timeout(500)
    assert 1 <= len(calls(page, "diagnostics")) - n <= 2


def test_diagnostics_load_failure_shows_retry(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {"diagnostics": "manager busy"}
    page = open_mgr("#/diagnostics", data=data, refresh_ms=60_000)
    banner = page.locator("#tv-view [data-load-error]")
    banner.wait_for()
    assert "manager busy" in banner.inner_text()
    before = rev(page)
    banner.locator("button").click()
    wait_rev_after(page, before)
    assert page.locator("#tv-view [data-load-error]").count() == 0
    assert page.locator("#tv-view [data-stat='rejected']").inner_text() == "3"


# ---------------------------------------------------------------- screenshots


@pytest.mark.parametrize(("width", "height"), [(1440, 900), (1100, 720)])
def test_screenshot_settings_diagnostics(
    open_mgr: Any, artifacts_dir: Path, width: int, height: int
) -> None:
    size = f"{width}x{height}"
    for name, route, data in [
        ("settings", "#/settings", mgr_data()),
        ("settings-fallback", "#/settings", mgr_data(port=47801)),
        ("diagnostics-healthy", "#/diagnostics", mgr_data()),
        ("diagnostics-warning", "#/diagnostics", mgr_data(firewall=False, port=47801)),
    ]:
        page = open_mgr(route, data=data, width=width, height=height, refresh_ms=60_000)
        page.wait_for_timeout(300)
        metrics = page.evaluate(
            "() => { const v = document.querySelector('#tv-view');"
            " return [v.scrollWidth, v.clientWidth]; }"
        )
        assert metrics[0] <= metrics[1], name
        page.screenshot(path=str(artifacts_dir / f"{name}-{size}.png"))
        page.locator("#tv-view").evaluate("v => { v.scrollTop = v.scrollHeight; }")
        page.wait_for_timeout(150)
        page.screenshot(path=str(artifacts_dir / f"{name}-bottom-{size}.png"))
        assert console_problems(page) == [], name
