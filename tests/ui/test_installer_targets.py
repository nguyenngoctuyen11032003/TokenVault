"""Installer targets (#/installer): windows exe, windows-portable zip, macOS zip."""

from __future__ import annotations

from typing import Any

import pytest

pytest.register_assert_rewrite("conftest_installer")

from conftest_installer import calls, console_problems, mgr_data  # noqa: E402

pytestmark = pytest.mark.ui
pytest_plugins = ["conftest_installer"]

WIZ = "#tv-view .tv-installer-wizard"
TEAM = "#tv-view input[name='team']"
GENERATE = "#tv-view button[data-action='generate']"


def _generate(page: Any, team: str) -> None:
    page.fill(TEAM, team)
    page.click(GENERATE)


def _create_error(page: Any) -> Any:
    banner = page.locator("#tv-view [data-create-error]")
    banner.wait_for()
    return banner


TARGET_CASES = [
    ("windows", None, "TokenVault-Adapter-Dev.exe", "Run anyway"),
    ("windows-portable", "windows-portable", "TokenVault-Adapter-Dev-portable.zip", "Install.cmd"),
    ("mac", "mac", "TokenVault-Adapter-Dev-mac.zip", "Install.command"),
]


def test_target_selector_has_three_targets_with_hints(open_mgr: Any) -> None:
    page = open_mgr("#/installer")
    radios = page.locator("#tv-view input[name='target']")
    assert [radios.nth(i).get_attribute("value") for i in range(radios.count())] == [
        "windows", "windows-portable", "mac"]
    assert page.locator("#tv-view #tv-installer-target-windows").is_checked()
    hint = page.locator("#tv-view [data-target-hint]")
    before = hint.inner_text()
    page.check("#tv-view #tv-installer-target-windows-portable")
    assert hint.inner_text() != before and "build-portable.ps1" in hint.inner_text()


@pytest.mark.parametrize(("radio", "api_target", "file_name", "marker"), TARGET_CASES)
def test_each_target_calls_api_and_shows_its_instructions(
    open_mgr: Any, radio: str, api_target: str | None, file_name: str, marker: str
) -> None:
    page = open_mgr("#/installer")
    page.check(f"#tv-view #tv-installer-target-{radio}")
    _generate(page, "Dev")
    page.wait_for_selector(f"{WIZ}[data-state='done']")
    expected = ["Dev", None] if api_target is None else ["Dev", None, api_target]
    assert calls(page, "create_installer") == [expected]
    assert page.locator("#tv-view [data-result-file]").inner_text() == file_name
    text = page.locator("#tv-view [data-instructions]").inner_text()
    assert file_name in text and marker in text
    if radio == "windows-portable":
        assert "quyền quản trị" in text and "Unblock-File" in text
    assert console_problems(page) == []


def test_portable_template_missing_tells_to_run_build_script(open_mgr: Any) -> None:
    data = mgr_data()
    data["failOnce"] = {
        "create_installer": "[template_missing] The portable runtime is missing: "
        "run build\build-portable.ps1"
    }
    page = open_mgr("#/installer", data=data)
    page.check("#tv-view #tv-installer-target-windows-portable")
    _generate(page, "Dev")
    banner = _create_error(page)
    assert banner.get_attribute("data-kind") == "template"
    text = banner.inner_text()
    assert "build-portable.ps1" in text and "portable-runtime" in text
    assert "[" not in text and "template_missing" not in text
    # retry keeps the selected target
    page.click("#tv-view [data-create-error] button")
    page.wait_for_selector(f"{WIZ}[data-state='done']")
    assert calls(page, "create_installer")[-1] == ["Dev", None, "windows-portable"]

