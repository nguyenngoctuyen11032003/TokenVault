"""Router robustness, boot failure, i18n extension safety and api mode detection."""

from __future__ import annotations

import re
from typing import Any

import pytest

pytestmark = pytest.mark.ui

ROUTE_REQUEST = re.compile(r"^/js/routes/[a-z0-9][a-z0-9-]*\.js$")


def _wait_state(page: Any, name: str) -> str:
    page.wait_for_function(
        """name => { const v = document.querySelector('#tv-view');
                     return v.dataset.route === name && !!v.dataset.routeState; }""",
        arg=name,
    )
    return page.evaluate("document.querySelector('#tv-view').dataset.routeState")


def _go(page: Any, hash_: str) -> None:
    page.evaluate("h => { location.hash = h; }", hash_)


def test_hash_traversal_never_leaves_routes_dir(open_shell: Any) -> None:
    page = open_shell("#/home")
    paths: list[str] = []
    page.on("request", lambda req: paths.append(re.sub(r"^https?://[^/]+", "", req.url)))

    for hash_, name in [
        ("#/..%2fx", "../x"),
        ("#/%2e%2e", ".."),
        ("#/..%2f..%2fi18n%2fvi", "../../i18n/vi"),
        ("#/a/b%2f..", "a"),
    ]:
        _go(page, hash_)
        assert _wait_state(page, name) == "missing"
        assert page.locator("#tv-view tv-panel.tv-not-built").is_visible()

    module_requests = [p for p in paths if p.endswith(".js")]
    assert module_requests, "expected the 'a' route lookup"
    assert all(ROUTE_REQUEST.match(p.split("?")[0]) for p in module_requests), module_requests
    assert not [p for p in paths if ".." in p or "%2f" in p.lower() or "%2e" in p.lower()]
    assert page.tv_errors == []


def test_rapid_navigation_discards_stale_async_render(open_shell: Any) -> None:
    page = open_shell("#/home")
    page.evaluate(
        """() => { window.__slowDelay = 300; location.hash = '#/slow';
                   setTimeout(() => { location.hash = '#/fast'; }, 80); }"""
    )
    assert _wait_state(page, "fast") == "ready"
    page.wait_for_function("window.__slowConnectedAfter !== undefined")
    page.wait_for_timeout(100)

    view = page.locator("#tv-view")
    assert view.locator("[data-fast]").count() == 1
    assert page.locator("[data-slow]").count() == 0  # nothing from A anywhere in the document
    assert view.locator(".tv-route").count() == 1
    state = page.evaluate(
        """() => ({
          connected: window.__slowConnectedAfter,
          containerAttached: window.__slowContainer.isConnected,
          aborted: window.__slowAborted,
          destroy: window.__slowDestroy,
          cleanup: window.__slowCleanup,
        })"""
    )
    assert state == {
        "connected": False,
        "containerAttached": False,
        "aborted": True,
        "destroy": 1,
        "cleanup": 1,
    }

    # A's late subscriptions were never kept alive.
    page.evaluate("tv.onLive({online: 1, total: 2}); tv.filters.set({team: 'late-check'})")
    assert page.evaluate("[window.__slowLive ?? 0, window.__slowFilter ?? 0]") == [0, 0]
    page.evaluate("location.hash = '#/home'")
    _wait_state(page, "home")
    assert page.evaluate("window.__slowDestroy") == 1
    assert page.tv_errors == []


def test_route_render_error_shows_error_panel_and_nav_still_works(open_shell: Any) -> None:
    page = open_shell("#/boom")
    assert _wait_state(page, "boom") == "error"
    panel = page.locator("#tv-view tv-panel.tv-route-error")
    assert panel.is_visible()
    assert "kaboom" in panel.inner_text()
    assert page.locator("#tv-view tv-panel.tv-not-built").count() == 0
    assert page.evaluate("window.__boomDestroy") == 1
    page.evaluate("tv.onLive({online: 1, total: 1})")
    assert page.evaluate("window.__boomLive ?? 0") == 0

    page.locator("tv-navmenu a[href='#/projects']").click()
    assert _wait_state(page, "projects") in ("missing", "ready")  # built by Task 15
    assert page.locator("tv-navmenu a[aria-current='page']").get_attribute("href") == "#/projects"


def test_route_subscriptions_are_removed_on_route_change(open_shell: Any) -> None:
    page = open_shell("#/subs")
    assert _wait_state(page, "subs") == "ready"
    page.evaluate("tv.onLive({online: 1, total: 1}); tv.filters.set({model: 'opus'})")
    assert page.evaluate("[window.__subsLive, window.__subsFilter]") == [1, 1]
    _go(page, "#/fast")
    _wait_state(page, "fast")
    page.evaluate("tv.onLive({online: 2, total: 2}); tv.filters.set({model: 'sonnet'})")
    assert page.evaluate("[window.__subsLive, window.__subsFilter]") == [1, 1]


def test_boot_failure_shows_hardcoded_fallback_panel(open_shell: Any) -> None:
    page = open_shell(
        "#/home",
        setup=lambda ctx: ctx.route("**/i18n/vi.json", lambda route: route.abort()),
        ready="error",
    )
    panel = page.locator(".tv-boot-error")
    assert panel.is_visible()
    assert panel.get_attribute("role") == "alert"
    assert "could not start" in panel.inner_text()
    assert page.locator(".tv-boot-error__detail").inner_text().strip() != ""
    reload = panel.get_by_role("button", name="Reload")
    assert reload.is_visible()


def test_i18n_extension_cannot_pollute_prototype_or_override_shell(open_shell: Any) -> None:
    page = open_shell("#/evil")
    assert _wait_state(page, "evil") == "ready"
    assert page.locator("[data-evil]").inner_text() == "evil-ok"
    leaked = page.evaluate(
        """() => [({}).pollutedInner, ({}).pollutedTop, ({}).pollutedCtor,
                  Object.prototype.pollutedInner, Object.prototype.pollutedTop]"""
    )
    assert leaked == [None, None, None, None, None]
    assert page.evaluate("import('./js/i18n.js').then(m => m.t('nav.home'))") == "Tổng quan"
    assert page.locator("tv-navmenu a[href='#/home'] .tv-nav__label").inner_text() == "Tổng quan"
    assert any("ignoring keys outside" in line for line in page.tv_console)


def test_i18n_extension_failure_is_retried_on_next_navigation(open_shell: Any) -> None:
    calls = {"n": 0}

    def handler(route: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            route.abort()
        else:
            route.continue_()

    page = open_shell("#/home", setup=lambda ctx: ctx.route("**/i18n/vi/demo.json", handler))
    _go(page, "#/demo")
    _wait_state(page, "demo")
    assert page.locator("[data-demo-hello]").inner_text() == "demo.hello"  # key fallback
    _go(page, "#/fast")
    _wait_state(page, "fast")
    _go(page, "#/demo")
    _wait_state(page, "demo")
    assert page.locator("[data-demo-hello]").inner_text() == "Xin chào từ trang demo"
    assert calls["n"] == 2


def test_api_without_pywebview_falls_back_to_http_quickly(open_shell: Any) -> None:
    page = open_shell("#/home", pywebview=False)
    assert page.evaluate("import('./js/api.js').then(m => m.api.mode())") == "http"
    assert page.evaluate("document.documentElement.dataset.tvHost") == "http"
    # Old behaviour waited a fixed 1.5 s; now ~300 ms after DOMContentLoaded.
    assert page.evaluate("tv.bootMs") < 1200


def test_api_waits_for_late_pywebview_bridge(open_shell: Any) -> None:
    page = open_shell("#/home", late_api_ms=900)
    assert page.evaluate("import('./js/api.js').then(m => m.api.mode())") == "pywebview"
    assert page.evaluate("tv.bootMs") >= 900
    calls = [c["name"] for c in page.evaluate("window.__tvCalls")]
    assert "settings" in calls
