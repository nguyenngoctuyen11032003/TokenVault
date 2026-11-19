"""Create-installer wizard (#/installer): team -> generate -> path, instructions, packages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.register_assert_rewrite("conftest_installer")

from conftest_installer import (  # noqa: E402
    OUT_DIR,
    XSS,
    calls,
    console_problems,
    mgr_data,
    rev,
    wait_rev_after,
)

pytestmark = pytest.mark.ui
pytest_plugins = ["conftest_installer"]

WIZ = "#tv-view .tv-installer-wizard"
TEAM = "#tv-view input[name='team']"
LABEL = "#tv-view input[name='label']"
GENERATE = "#tv-view button[data-action='generate']"
TEAM_ERROR = "#tv-view [data-field-error='team']"
PACKAGES = "#tv-view table.tv-installer-packages tbody tr[data-package]"
DIALOG = ".tv-installer-dlg[role='dialog']"


def _state(page: Any) -> str:
    return str(page.locator(WIZ).get_attribute("data-state"))


def _generate(page: Any, team: str, label: str = "") -> None:
    page.fill(TEAM, team)
    page.fill(LABEL, label)
    page.click(GENERATE)


def test_wizard_generates_installer_and_shows_path(open_mgr: Any) -> None:
    page = open_mgr("#/installer")
    assert _state(page) == "form"
    assert page.locator("#tv-view .tv-installer-steps [aria-current='step']").get_attribute(
        "data-step"
    ) == "1"
    _generate(page, "  Kế toán  ", "Tầng 2")
    page.wait_for_selector(f"{WIZ}[data-state='done']")
    assert calls(page, "create_installer") == [["Kế toán", "Tầng 2"]]
    assert page.locator("#tv-view [data-result-file]").inner_text() == (
        "TokenVault-Adapter-Kế toán.exe"
    )
    path = f"{OUT_DIR}\\TokenVault-Adapter-Kế toán.exe"
    assert page.locator("#tv-view [data-result-path]").inner_text() == path
    assert page.locator("#tv-view .tv-installer-steps [aria-current='step']").get_attribute(
        "data-step"
    ) == "3"
    # Open folder -> reveal_in_explorer(path).
    page.click("#tv-view button[data-action='reveal']")
    page.wait_for_function("window.__tvCalls.some(c => c.name === 'reveal_in_explorer')")
    assert calls(page, "reveal_in_explorer") == [[path]]
    # Instructions mention the file, SmartScreen and uninstalling via Apps & Features.
    text = page.locator("#tv-view [data-instructions]").inner_text()
    assert "TokenVault-Adapter-Kế toán.exe" in text
    assert "SmartScreen" in text and "More info" in text and "Run anyway" in text
    assert "Apps & Features" in text
    # The package list was refreshed and shows the new package first.
    page.wait_for_function(
        "document.querySelectorAll('table.tv-installer-packages tbody tr[data-package]')"
        ".length === 4"
    )
    assert "Kế toán" in page.locator(PACKAGES).first.inner_text()
    # Empty label is sent as null.
    page.click("#tv-view button[data-action='again']")
    page.wait_for_selector(f"{WIZ}[data-state='form']")
    _generate(page, "QA")
    page.wait_for_selector(f"{WIZ}[data-state='done']")
    assert calls(page, "create_installer")[-1] == ["QA", None]
    assert console_problems(page) == []


def test_wizard_rejects_empty_team(open_mgr: Any) -> None:
    page = open_mgr("#/installer")
    _generate(page, "   ")
    error = page.locator(TEAM_ERROR)
    assert error.is_visible()
    assert error.inner_text().strip()
    assert page.locator(TEAM).get_attribute("aria-invalid") == "true"
    assert page.evaluate("document.activeElement.name") == "team"
    assert calls(page, "create_installer") == []
    assert _state(page) == "form"
    # Typing a valid name clears the error.
    page.fill(TEAM, "Dev")
    assert not page.locator(TEAM_ERROR).is_visible()
    assert console_problems(page) == []


@pytest.mark.parametrize(
    ("team", "reason"),
    [("Dev/QA", "separator"), ("..\\evil", "separator"), ("A" * 41, "tooLong"),
     ("Dev\u0007", "control"), ('Team "A"?', "invalid")],
)
def test_wizard_rejects_bad_team_names(open_mgr: Any, team: str, reason: str) -> None:
    page = open_mgr("#/installer")
    _generate(page, team)
    error = page.locator(TEAM_ERROR)
    assert error.is_visible()
    assert error.get_attribute("data-reason") == reason
    assert calls(page, "create_installer") == []


def test_wizard_accepts_vietnamese_and_suggests_existing_teams(open_mgr: Any) -> None:
    page = open_mgr("#/installer")
    chips = page.locator("#tv-view .tv-installer-suggest button[data-team]")
    names = [chips.nth(i).get_attribute("data-team") for i in range(chips.count())]
    assert "Dev" in names and "QA" in names
    assert XSS not in names  # not a valid installer team name
    chips.filter(has_text="QA").first.click()
    assert page.input_value(TEAM) == "QA"
    # 40 characters with Vietnamese letters is fine.
    name = "Phòng Kỹ thuật Đường sắt Hà Nội – Nhóm 1"[:40]
    _generate(page, name)
    page.wait_for_selector(f"{WIZ}[data-state='done']")
    assert calls(page, "create_installer") == [[name, None]]


def test_create_installer_failure_shows_error_and_retry(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {"create_installer": "[template_missing] adapter-setup-template.exe"}
    data["delay"] = {"create_installer": 400}
    page = open_mgr("#/installer", data=data)
    _generate(page, "Dev")
    # Busy: step 2, button disabled, inputs locked.
    page.wait_for_selector(f"{WIZ}[data-state='busy']")
    assert page.locator(GENERATE).is_disabled()
    assert page.locator(TEAM).is_disabled()
    assert page.locator("#tv-view .tv-installer-steps [aria-current='step']").get_attribute(
        "data-step"
    ) == "2"
    banner = page.locator("#tv-view [data-create-error]")
    banner.wait_for()
    assert banner.get_attribute("data-kind") == "template"
    assert "Manager" in banner.inner_text()
    assert _state(page) == "form"
    assert page.input_value(TEAM) == "Dev"
    page.click("#tv-view [data-create-error] button[data-action='retry']")
    page.wait_for_selector(f"{WIZ}[data-state='done']")
    assert calls(page, "create_installer") == [["Dev", None], ["Dev", None]]
    assert page.locator("#tv-view [data-create-error]").count() == 0


def test_create_installer_generic_failure_shows_detail(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {"create_installer": "disk full"}
    page = open_mgr("#/installer", data=data)
    _generate(page, "Dev")
    banner = page.locator("#tv-view [data-create-error]")
    banner.wait_for()
    assert banner.get_attribute("data-kind") == "generic"
    assert "disk full" in banner.inner_text()


def test_copy_instructions_copies_exact_text(open_mgr: Any) -> None:
    page = open_mgr("#/installer", grant_clipboard=True)
    _generate(page, "Dev")
    page.wait_for_selector(f"{WIZ}[data-state='done']")
    shown = page.locator("#tv-view [data-instructions]").evaluate("e => e.textContent")
    page.click("#tv-view button[data-action='copy-instructions']")
    page.wait_for_selector("#tv-view button[data-action='copy-instructions'][data-copied]")
    # The Windows clipboard stores CRLF line ends; the text itself must be identical.
    copied = page.evaluate("navigator.clipboard.readText()")
    assert copied.replace(chr(13) + chr(10), chr(10)) == shown
    assert shown.count("\n") >= 6
    page.click("#tv-view button[data-action='copy-path']")
    page.wait_for_selector("#tv-view button[data-action='copy-path'][data-copied]")
    assert page.evaluate("navigator.clipboard.readText()") == (
        f"{OUT_DIR}\\TokenVault-Adapter-Dev.exe"
    )
    assert console_problems(page) == []


def test_revoke_package_requires_confirmation_and_escape_cancels(open_mgr: Any) -> None:
    page = open_mgr("#/installer")
    row = page.locator(f"{PACKAGES}[data-package='pkg-2']")
    assert "Tầng 3" in row.inner_text()
    # Already revoked packages cannot be revoked again.
    assert page.locator(
        f"{PACKAGES}[data-package='pkg-1'] button[data-action='revoke']"
    ).count() == 0
    button = row.locator("button[data-action='revoke']")
    button.click()
    dialog = page.locator(DIALOG)
    dialog.wait_for()
    assert dialog.get_attribute("aria-modal") == "true"
    assert dialog.get_attribute("aria-labelledby")
    assert "QA" in dialog.inner_text()
    # Focus is trapped inside the dialog.
    for _ in range(6):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "!!document.activeElement.closest('.tv-installer-dlg[role=dialog]')"
        )
    page.keyboard.press("Escape")
    dialog.wait_for(state="detached")
    assert calls(page, "revoke_package") == []
    assert page.evaluate("document.activeElement.dataset.action") == "revoke"
    # Confirm for real.
    before = rev(page)
    button.click()
    page.locator(f"{DIALOG} button[data-dlg='confirm']").click()
    wait_rev_after(page, before)
    assert calls(page, "revoke_package") == [["pkg-2"]]
    row = page.locator(f"{PACKAGES}[data-package='pkg-2']")
    assert row.get_attribute("data-revoked") == "true"
    assert row.locator("button[data-action='revoke']").count() == 0
    assert console_problems(page) == []


def test_untrusted_package_names_render_as_text(open_mgr: Any) -> None:
    page = open_mgr("#/installer")
    assert page.evaluate("window.__xss") is None
    assert page.locator("#tv-view img").count() == 0
    row = page.locator(f"{PACKAGES}[data-package='pkg-3']")
    assert XSS in row.inner_text()
    row.locator("button[data-action='revoke']").click()
    page.locator(DIALOG).wait_for()
    assert XSS in page.locator(DIALOG).inner_text()
    assert page.locator(f"{DIALOG} img").count() == 0
    page.keyboard.press("Escape")


def test_packages_failure_shows_retry_and_empty_state(open_mgr: Any) -> None:
    data = mgr_data(empty=True)
    data["failOnce"] = {"packages": "db locked"}
    page = open_mgr("#/installer", data=data)
    banner = page.locator("#tv-view [data-load-error]")
    banner.wait_for()
    assert "db locked" in banner.inner_text()
    before = rev(page)
    banner.locator("button").click()
    wait_rev_after(page, before)
    assert page.locator("#tv-view [data-load-error]").count() == 0
    assert page.locator("#tv-view [data-packages-empty]").is_visible()


@pytest.mark.parametrize(("width", "height"), [(1440, 900), (1100, 720)])
def test_screenshot_installer(open_mgr: Any, artifacts_dir: Path, width: int, height: int) -> None:
    data = mgr_data()
    data["delay"] = {"create_installer": 60_000}
    page = open_mgr("#/installer", data=data, width=width, height=height)
    metrics = page.evaluate(
        "() => { const v = document.querySelector('#tv-view');"
        " return [v.scrollWidth, v.clientWidth]; }"
    )
    assert metrics[0] <= metrics[1]
    page.screenshot(path=str(artifacts_dir / f"installer-1-form-{width}x{height}.png"))
    _generate(page, "")
    page.screenshot(path=str(artifacts_dir / f"installer-1-error-{width}x{height}.png"))
    _generate(page, "Kế toán", "Tầng 2")
    page.wait_for_selector(f"{WIZ}[data-state='busy']")
    page.wait_for_timeout(300)
    page.screenshot(path=str(artifacts_dir / f"installer-2-busy-{width}x{height}.png"))
    assert console_problems(page) == []
    page = open_mgr("#/installer", width=width, height=height)
    _generate(page, "Kế toán", "Tầng 2")
    page.wait_for_selector(f"{WIZ}[data-state='done']")
    page.wait_for_timeout(200)
    page.screenshot(path=str(artifacts_dir / f"installer-3-done-{width}x{height}.png"))
    page.locator("#tv-view").evaluate("v => { v.scrollTop = v.scrollHeight; }")
    page.wait_for_timeout(150)
    page.screenshot(path=str(artifacts_dir / f"installer-3-done-bottom-{width}x{height}.png"))
    assert console_problems(page) == []


# ---------------------------------------------------------------- error classification (fix 1)


def _create_error(page: Any) -> Any:
    banner = page.locator("#tv-view [data-create-error]")
    banner.wait_for()
    return banner


def test_template_missing_prefix_shows_reinstall_message_without_prefix(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {
        "create_installer": "[template_missing] adapter-setup-template.exe not found"
    }
    page = open_mgr("#/installer", data=data)
    _generate(page, "Dev")
    banner = _create_error(page)
    assert banner.get_attribute("data-kind") == "template"
    text = banner.inner_text()
    assert "cài lại TokenVault Manager" in text
    assert "[" not in text and "template_missing" not in text


def test_plain_error_mentioning_template_is_not_template_missing(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {"create_installer": "could not read template cache: access denied"}
    page = open_mgr("#/installer", data=data)
    _generate(page, "Dev")
    banner = _create_error(page)
    assert banner.get_attribute("data-kind") == "generic"
    assert "could not read template cache: access denied" in banner.inner_text()
    assert "cài lại TokenVault Manager" not in banner.inner_text()


def test_unknown_error_prefix_is_stripped(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {"create_installer": "[disk_full] no space left on device"}
    page = open_mgr("#/installer", data=data)
    _generate(page, "Dev")
    banner = _create_error(page)
    assert banner.get_attribute("data-kind") == "generic"
    assert "no space left on device" in banner.inner_text()
    assert "[disk_full]" not in banner.inner_text()


def test_invalid_team_prefix_marks_the_team_field(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {"create_installer": "[invalid_team] name reserved"}
    page = open_mgr("#/installer", data=data)
    _generate(page, "Dev")
    error = page.locator(f"{TEAM_ERROR}[data-reason='server']")
    error.wait_for()
    assert "[invalid_team]" not in error.inner_text()
    assert page.locator("#tv-view [data-create-error]").count() == 0
    assert _state(page) == "form"


def test_revoked_prefix_on_revoke_package_shows_notice(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {"revoke_package": "[revoked] package pkg-2 already revoked"}
    page = open_mgr("#/installer", data=data)
    page.locator(f"{PACKAGES}[data-package='pkg-2'] button[data-action='revoke']").click()
    page.locator(f"{DIALOG} button[data-dlg='confirm']").click()
    notice = page.locator("#tv-view [data-revoke-notice]")
    notice.wait_for()
    assert notice.get_attribute("data-kind") == "revoked"
    assert "[revoked]" not in notice.inner_text()
    assert "đã được thu hồi" in notice.inner_text()


# ---------------------------------------------------------------- copy fallback (fix 2)

DENY_CLIPBOARD = """
Object.defineProperty(navigator, 'clipboard', {
  configurable: true,
  value: { writeText: () => Promise.reject(new Error('NotAllowedError')),
           readText: () => Promise.reject(new Error('NotAllowedError')) },
});
document.execCommand = () => false;
"""


def test_copy_denied_selects_text_and_shows_ctrl_c_hint(open_mgr: Any) -> None:
    page = open_mgr("#/installer", extra_script=DENY_CLIPBOARD)
    _generate(page, "Dev")
    page.wait_for_selector(f"{WIZ}[data-state='done']")
    page.click("#tv-view button[data-action='copy-instructions']")
    hint = page.locator("#tv-view [data-copy-hint='copy-instructions']")
    hint.wait_for()
    assert "Ctrl+C" in hint.inner_text()
    shown = page.locator("#tv-view [data-instructions]").evaluate("e => e.textContent")
    selected = page.evaluate("window.getSelection().toString()")
    assert selected.replace(chr(13) + chr(10), chr(10)) == shown
    page.click("#tv-view button[data-action='copy-path']")
    page.locator("#tv-view [data-copy-hint='copy-path']").wait_for()
    assert page.evaluate("window.getSelection().toString()") == (
        f"{OUT_DIR}\\TokenVault-Adapter-Dev.exe"
    )
    assert console_problems(page) == []
