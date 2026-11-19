"""Playwright tests for the NitroSense-style UI shell (titlebar, nav, router, gauge, i18n)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.ui

NAV_ROUTES = [
    "home",
    "employees",
    "projects",
    "sessions",
    "skills",
    "orchestration",
    "installer",
    "settings",
    "diagnostics",
]

# Letters that only appear in Vietnamese text (Latin-1 accents + Vietnamese extended block).
VIETNAMESE_CHARS = re.compile(r"[À-ÿĂăĐđĨĩŨũƠơƯưẠ-ỹ]")


def _vi_strings(ui_dir: Path) -> list[str]:
    files = [ui_dir / "i18n" / "vi.json", *sorted((ui_dir / "i18n" / "vi").glob("*.json"))]
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, str):
            out.append(node)

    for path in files:
        walk(json.loads(path.read_text(encoding="utf-8")))
    return out


def _wait_route(page: Any, name: str) -> None:
    page.wait_for_selector(f"#tv-view[data-route='{name}'][data-route-state]")


def test_shell_renders_titlebar_and_live_indicator(open_shell: Any) -> None:
    page = open_shell("#/home")
    titlebar = page.locator("tv-titlebar")
    assert titlebar.is_visible()
    assert page.locator(".tv-titlebar__title").inner_text().replace("\n", "") == "TOKENVAULT"
    assert page.locator(".pywebview-drag-region").count() >= 1

    live = page.locator(".tv-live")
    assert live.get_attribute("data-state") == "waiting"

    # Python pushes via `tv.onLive(<json>)`; pages subscribe via `tv.onLive(cb)`.
    seen = page.evaluate(
        """() => { window.__seen = []; tv.onLive(p => window.__seen.push(p)); return true; }"""
    )
    assert seen
    page.evaluate("tv.onLive({online: 12, total: 14})")
    assert live.locator(".tv-live__text").inner_text() == "12 / 14 máy online"
    assert live.get_attribute("data-state") == "online"
    page.evaluate('tv.emitLive(\'{"online": 0, "total": 3}\')')
    assert live.locator(".tv-live__text").inner_text() == "0 / 3 máy online"
    assert live.get_attribute("data-state") == "offline"
    assert page.evaluate("window.__seen.length") == 2
    # Non-JSON string payloads are ignored (logged), not delivered and not fatal.
    page.evaluate("tv.onLive('not json')")
    assert page.evaluate("window.__seen.length") == 2
    assert live.locator(".tv-live__text").inner_text() == "0 / 3 máy online"

    minimize = page.get_by_role("button", name="Thu nhỏ")
    close = page.get_by_role("button", name="Ẩn xuống khay")
    minimize.click()
    close.click()
    calls = [c["name"] for c in page.evaluate("window.__tvCalls")]
    assert "window_min" in calls and "window_close" in calls
    assert page.tv_errors == []


def test_nav_routes_switch_without_reload(open_shell: Any) -> None:
    page = open_shell("#/home")
    page.evaluate("window.__noReload = 'still-here'")
    items = page.locator("tv-navmenu a.tv-nav__item")
    assert items.count() == len(NAV_ROUTES)
    for index, route in enumerate(NAV_ROUTES):
        items.nth(index).click()
        _wait_route(page, route)
        assert page.evaluate("location.hash") == f"#/{route}"
        assert items.nth(index).get_attribute("aria-current") == "page"
        assert page.locator("tv-navmenu a[aria-current='page']").count() == 1
    assert page.evaluate("window.__noReload") == "still-here"

    # Keyboard: Tab reaches the nav, Enter activates the focused item.
    page.locator("tv-navmenu a.tv-nav__item").first.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    _wait_route(page, "employees")
    # A detail route maps to its parent nav entry.
    page.evaluate("location.hash = '#/employee/an.tran%40ics.vn'")
    _wait_route(page, "employee")
    assert page.locator("tv-navmenu a[aria-current='page']").get_attribute("href") == (
        "#/employees"
    )
    assert page.evaluate("window.__noReload") == "still-here"
    assert page.tv_errors == []


def test_missing_route_shows_not_built_panel(open_shell: Any) -> None:
    page = open_shell("#/does-not-exist")
    _wait_route(page, "does-not-exist")
    view = page.locator("#tv-view")
    assert view.get_attribute("data-route-state") == "missing"
    panel = view.locator("tv-panel.tv-not-built")
    assert panel.is_visible()
    assert "chưa được xây dựng" in panel.inner_text()

    # Routes without a module render the same panel, naming the page (every nav page is built
    # now, so an unknown route is used).
    page.evaluate("location.hash = '#/not-a-page'")
    _wait_route(page, "not-a-page")
    assert "not-a-page" in page.locator("#tv-view tv-panel.tv-not-built").inner_text()

    # Route modules added later are picked up with no shared-file edits (test overlay).
    page.evaluate("location.hash = '#/demo/abc%20def'")
    _wait_route(page, "demo")
    assert page.locator("#tv-view").get_attribute("data-route-state") == "ready"
    assert page.locator("#tv-view [data-demo-args]").get_attribute("data-demo-args") == (
        '["abc def"]'
    )
    # Per-page strings (i18n/vi/<route>.json) are merged before render.
    assert page.locator("[data-demo-hello]").inner_text() == "Xin chào từ trang demo"
    page.evaluate("location.hash = '#/home'")
    _wait_route(page, "home")
    assert page.evaluate("window.__demoDestroyed") is True
    assert page.tv_errors == []


def test_gauge_sets_arc_from_value(open_shell: Any) -> None:
    page = open_shell("#/home")
    page.evaluate(
        """() => {
          const g = document.createElement('tv-gauge');
          g.id = 'g1';
          g.setAttribute('value', '50');
          g.setAttribute('max', '200');
          g.setAttribute('label', 'L');
          g.setAttribute('unit', 'U');
          document.querySelector('#tv-view').append(g);
        }"""
    )
    gauge = page.locator("#g1")

    def pct() -> float:
        raw = page.evaluate(
            "getComputedStyle(document.querySelector('#g1 .tv-gauge__dial'))"
            ".getPropertyValue('--tv-gauge-p')"
        )
        return float(raw)

    assert pct() == pytest.approx(0.25)
    assert gauge.get_attribute("role") == "meter"
    assert gauge.get_attribute("aria-valuenow") == "50"
    assert gauge.get_attribute("aria-valuemax") == "200"
    assert gauge.locator(".tv-gauge__value").inner_text() == "50"
    assert gauge.locator(".tv-gauge__unit").inner_text() == "U"
    assert gauge.locator(".tv-gauge__label").inner_text() == "L"

    page.evaluate("document.querySelector('#g1').setAttribute('value', '150')")
    assert pct() == pytest.approx(0.75)
    page.evaluate("document.querySelector('#g1').setAttribute('max', '100')")
    assert pct() == pytest.approx(1.0)  # clamped
    page.evaluate("document.querySelector('#g1').setAttribute('value', 'n/a')")
    assert pct() == pytest.approx(0.0)
    page.evaluate("document.querySelector('#g1').setAttribute('display', '$61.4')")
    assert gauge.locator(".tv-gauge__value").inner_text() == "$61.4"
    page.evaluate(
        "const g = document.querySelector('#g1');"
        "g.setAttribute('value', '4820000'); g.setAttribute('max', '10000000');"
        "g.removeAttribute('display')"
    )
    assert gauge.locator(".tv-gauge__value").inner_text() == "4.82M"
    assert pct() == pytest.approx(0.482)


def test_all_visible_strings_come_from_i18n(open_shell: Any, ui_dir: Path) -> None:
    # 1) Static: no Vietnamese text hardcoded in shipped js/html/css (vi.json and vendor excluded).
    offenders = []
    for path in ui_dir.rglob("*"):
        if path.suffix not in {".js", ".html", ".css"} or "vendor" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if VIETNAMESE_CHARS.search(line):
                offenders.append(f"{path.relative_to(ui_dir)}:{lineno}: {line.strip()}")
    assert offenders == []

    # 2) Runtime: every visible text node / aria-label equals a vi.json value (templates allowed).
    patterns = [
        re.compile(re.sub(r"\\\{\w+\\\}", ".+?", re.escape(s)) + r"\Z", re.S)
        for s in _vi_strings(ui_dir)
    ]
    page = open_shell("#/settings")
    _wait_route(page, "settings")
    page.evaluate("tv.onLive({online: 3, total: 4})")
    texts = page.evaluate(
        """() => {
          const out = [];
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {
            const n = walker.currentNode;
            const text = n.nodeValue.trim();
            const el = n.parentElement;
            if (!text || !el || el.closest('script,style,[hidden]')) continue;
            if (!el.getClientRects().length) continue;
            out.push(text);
          }
          document.querySelectorAll('[aria-label],[title],[placeholder]').forEach(el => {
            for (const a of ['aria-label', 'title', 'placeholder']) {
              const v = el.getAttribute(a);
              if (v) out.push(v.trim());
            }
          });
          return out;
        }"""
    )
    assert texts, "nothing rendered"
    unknown = [
        t
        for t in texts
        if not re.fullmatch(r"[\d\s.,:/%$+\-–—·]*", t) and not any(p.match(t) for p in patterns)
    ]
    assert unknown == []


def test_opaque_fallback_class_applied_when_setting_false(open_shell: Any) -> None:
    page = open_shell("#/home", settings={"transparent_window": False})
    win = page.locator(".tv-window")
    assert "window--opaque" in (win.get_attribute("class") or "")
    assert page.evaluate("getComputedStyle(document.querySelector('.tv-window')).clipPath") == (
        "none"
    )

    default = open_shell("#/home", settings={})
    assert "window--opaque" not in (default.locator(".tv-window").get_attribute("class") or "")
    assert default.evaluate(
        "getComputedStyle(document.querySelector('.tv-window')).clipPath"
    ).startswith("polygon")


def _boxes_overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    eps = 0.5
    return not (
        a["x"] + a["width"] <= b["x"] + eps
        or b["x"] + b["width"] <= a["x"] + eps
        or a["y"] + a["height"] <= b["y"] + eps
        or b["y"] + b["height"] <= a["y"] + eps
    )


@pytest.mark.parametrize(("width", "height"), [(1100, 720), (1440, 900)])
def test_layout_ok_at_1100x720_and_1440x900(open_shell: Any, width: int, height: int) -> None:
    page = open_shell("#/demo", width=width, height=height)
    _wait_route(page, "demo")
    metrics = page.evaluate(
        """() => {
          const view = document.querySelector('#tv-view');
          return {
            docScroll: document.documentElement.scrollWidth,
            bodyScroll: document.body.scrollWidth,
            inner: window.innerWidth,
            viewScroll: view.scrollWidth,
            viewClient: view.clientWidth,
          };
        }"""
    )
    assert metrics["docScroll"] <= metrics["inner"]
    assert metrics["bodyScroll"] <= metrics["inner"]
    assert metrics["viewScroll"] <= metrics["viewClient"]

    shell = {
        name: page.locator(selector).bounding_box()
        for name, selector in {
            "titlebar": "tv-titlebar",
            "nav": "tv-navmenu",
            "view": "#tv-view",
        }.items()
    }
    for box in shell.values():
        assert box is not None and box["width"] > 0 and box["height"] > 0
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 0.5
        assert box["y"] >= 0 and box["y"] + box["height"] <= height + 0.5
    names = list(shell)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            assert not _boxes_overlap(shell[first], shell[second]), (first, second)

    view = shell["view"]
    panels = [
        page.locator("#tv-view tv-panel").nth(i).bounding_box()
        for i in range(page.locator("#tv-view tv-panel").count())
    ]
    assert len(panels) >= 4
    for i, a in enumerate(panels):
        assert a is not None
        assert a["x"] >= view["x"] - 0.5 and a["x"] + a["width"] <= view["x"] + view["width"] + 0.5
        for b in panels[i + 1 :]:
            assert b is not None and not _boxes_overlap(a, b)
    assert page.tv_errors == []


def test_filters_store_persists_and_notifies(open_shell: Any) -> None:
    page = open_shell("#/home")
    result = page.evaluate(
        """async () => {
          const { filters } = await import('./js/filters.js');
          const seen = [];
          const off = filters.subscribe((state, changed) => seen.push([state.team, changed]));
          filters.set({ team: 'Dev', email: 'an.tran@ics.vn' });
          filters.set({ team: 'Dev' });  // no change -> no notification
          off();
          filters.set({ project: 'erp-api' });  // unsubscribed
          return { seen, state: filters.get(), stored: localStorage.getItem('tv.filters.v1') };
        }"""
    )
    assert result["seen"] == [["Dev", ["team", "email"]]]
    assert result["state"]["team"] == "Dev"
    assert result["state"]["project"] == "erp-api"
    assert set(result["state"]) >= {"team", "email", "project", "model", "range"}
    assert json.loads(result["stored"])["email"] == "an.tran@ics.vn"

    page.reload()
    page.wait_for_selector("html[data-tv-ready='1']")
    state = page.evaluate("import('./js/filters.js').then(m => m.filters.get())")
    assert state["team"] == "Dev" and state["project"] == "erp-api"

    # Corrupt storage falls back to defaults instead of breaking the shell.
    page.evaluate("localStorage.setItem('tv.filters.v1', '{not json')")
    page.reload()
    page.wait_for_selector("html[data-tv-ready='1']")
    state = page.evaluate("import('./js/filters.js').then(m => m.filters.get())")
    assert state["team"] is None and state["range"] == "today"


def test_api_falls_back_to_fetch_without_pywebview(open_shell: Any) -> None:
    page = open_shell("#/home", pywebview=False)
    result = page.evaluate(
        "import('./js/api.js').then(async m => ({mode: await m.api.mode(),"
        " settings: await m.api.settings('x', 2)}))"
    )
    assert result["mode"] == "http"
    assert result["settings"]["source"] == "http"
    assert result["settings"]["echo_args"] == ["x", 2]
    failed = page.evaluate(
        "import('./js/api.js').then(m => m.api.nope().then(() => 'ok', e => e.name))"
    )
    assert failed == "ApiError"


def test_screenshot_shell_demo(open_shell: Any, artifacts_dir: Path) -> None:
    page = open_shell("#/demo", width=1440, height=900)
    _wait_route(page, "demo")
    page.wait_for_timeout(700)  # let gauge transitions settle
    page.screenshot(path=str(artifacts_dir / "shell.png"), omit_background=True)
    assert (artifacts_dir / "shell.png").stat().st_size > 10_000
