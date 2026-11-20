"""The brand logo renders in the title bar and the page declares a favicon."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.ui


def test_titlebar_logo_renders(open_shell: Any) -> None:
    page = open_shell("#/home")
    img = page.locator(".tv-titlebar__logo")
    img.wait_for(state="visible")
    page.wait_for_function("document.querySelector('.tv-titlebar__logo').complete")
    assert img.get_attribute("alt") == "TokenVault"
    assert page.evaluate("document.querySelector('.tv-titlebar__logo').naturalWidth") > 0
    box = img.bounding_box()
    assert box is not None and box["width"] >= 24 and box["height"] >= 24


def test_favicon_declared_and_served(open_shell: Any) -> None:
    page = open_shell("#/home")
    hrefs = page.evaluate(
        "[...document.querySelectorAll('link[rel=icon]')].map(l => l.href)")
    assert any(h.endswith("assets/logo/tokenvault-mark-small.svg") for h in hrefs)
    for href in hrefs:
        assert page.evaluate(
            "async (u) => (await fetch(u)).ok", href), href
