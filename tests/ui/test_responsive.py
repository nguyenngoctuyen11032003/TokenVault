"""Responsive layout (Task 20): every page from 1100x720 up to 3840x2160, normal and maximised.

Reuses the page stubs of the page tasks (conftest_home / _overview / _skills / _installer).
Screenshots of the key pages land in ``tests/ui/_artifacts/responsive/`` (git-ignored) for the
mandatory visual review.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

pytest.register_assert_rewrite("conftest_overview")

from conftest_overview import ORDER_ID  # noqa: E402

pytestmark = pytest.mark.ui
pytest_plugins = ["conftest_home", "conftest_overview", "conftest_skills", "conftest_installer"]

SIZES = [(1100, 720), (1440, 900), (1920, 1080), (2560, 1440), (3840, 2160)]

# page -> (opener fixture, hash route)
PAGES: dict[str, tuple[str, str]] = {
    "home": ("open_page", "#/home"),
    "employees": ("open_page", "#/employees"),
    "employee": ("open_page", "#/employee/" + quote("an.tran@ics.vn", safe="")),
    "overview": ("open_ov", "#/overview"),
    "projects": ("open_ov", "#/projects"),
    "sessions": ("open_ov", "#/sessions"),
    "session": ("open_ov", "#/session/" + quote(ORDER_ID, safe="")),
    "skills": ("open_skills_page", "#/skills"),
    "orchestration": ("open_skills_page", "#/orchestration"),
    "installer": ("open_mgr", "#/installer"),
    "settings": ("open_mgr", "#/settings"),
    "diagnostics": ("open_mgr", "#/diagnostics"),
}
SHOT_PAGES = {"home", "employee", "overview", "skills", "installer", "diagnostics"}



@pytest.fixture(scope="module")
def browser(request: pytest.FixtureRequest) -> Iterator[Any]:
    """Own chromium for this module (same Playwright driver as the session browser): 60 pages
    up to 3840x2160 leave a shared browser slow for timing-sensitive tests run after it
    (test_router boot time)."""
    shared = request.getfixturevalue("browser")
    own = shared.browser_type.launch(headless=True)
    yield own
    own.close()


MEASURE = """() => {
  const view = document.querySelector('#tv-view');
  const box = (el) => { const r = el.getBoundingClientRect();
    return {x: r.left, y: r.top, width: r.width, height: r.height}; };
  const visible = (el) => el.getClientRects().length > 0 && !el.closest('[hidden]');
  const panels = [...view.querySelectorAll('tv-panel')]
    .filter((p) => visible(p) && !p.parentElement.closest('tv-panel'))
    .map((p) => ({...box(p), name: p.getAttribute('heading') || p.className}));
  const charts = [...view.querySelectorAll('.tv-chart')].filter(visible)
    .map((c) => ({...box(c), svg: !!c.querySelector('svg')}));
  return {
    inner: innerWidth,
    innerH: innerHeight,
    docScroll: document.documentElement.scrollWidth,
    bodyScroll: document.body.scrollWidth,
    viewScroll: view.scrollWidth,
    viewClient: view.clientWidth,
    view: box(view),
    nav: box(document.querySelector('tv-navmenu')),
    panels,
    charts,
    scale: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--tv-scale')),
  };
}"""


def _overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    eps = 1.0
    return not (
        a["x"] + a["width"] <= b["x"] + eps
        or b["x"] + b["width"] <= a["x"] + eps
        or a["y"] + a["height"] <= b["y"] + eps
        or b["y"] + b["height"] <= a["y"] + eps
    )


def _check_layout(page: Any, width: int, height: int, label: str) -> None:
    m = page.evaluate(MEASURE)
    assert m["inner"] == width
    assert m["docScroll"] <= width, (label, "document scrolls horizontally", m["docScroll"])
    assert m["bodyScroll"] <= width, (label, "body scrolls horizontally")
    assert m["viewScroll"] <= m["viewClient"] + 1, (label, "view scrolls horizontally")

    view = m["view"]
    assert view["x"] + view["width"] <= width + 0.5 and view["y"] + view["height"] <= height + 0.5
    panels = m["panels"]
    assert panels, (label, "no panel rendered")
    for i, a in enumerate(panels):
        assert a["width"] > 0 and a["height"] > 0, (label, a)
        assert a["x"] >= view["x"] - 1 and a["x"] + a["width"] <= view["x"] + view["width"] + 1, (
            label, "panel outside the view", a,
        )
        for b in panels[i + 1 :]:
            assert not _overlap(a, b), (label, "panels overlap", a, b)
    for chart in m["charts"]:
        assert chart["width"] > 40 and chart["height"] > 40 and chart["svg"], (label, chart)

    if width >= 1920:
        # Content uses the screen instead of sitting in a corner.
        left = min([m["nav"]["x"], *(p["x"] for p in panels)])
        right = max(p["x"] + p["width"] for p in panels)
        assert (right - left) / width >= 0.85, (label, "content too narrow", left, right)
    if width >= 2560:
        assert m["scale"] >= 1.25, (label, "typography not scaled up", m["scale"])
    else:
        assert m["scale"] >= 1.0


def _settle(page: Any) -> None:
    # ResizeObserver -> rAF -> chart.resize(); gauge transitions.
    page.wait_for_timeout(250)
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


@pytest.mark.parametrize(("width", "height"), SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
@pytest.mark.parametrize("name", list(PAGES))
def test_page_layout_at_size(
    request: pytest.FixtureRequest, artifacts_dir: Path, name: str, width: int, height: int
) -> None:
    opener_name, route = PAGES[name]
    opener = request.getfixturevalue(opener_name)
    page = opener(route, width=width, height=height)
    _settle(page)
    _check_layout(page, width, height, f"{name} {width}x{height}")
    shots = artifacts_dir / "responsive"
    shots.mkdir(exist_ok=True)
    if name in SHOT_PAGES:
        page.screenshot(path=str(shots / f"{name}-{width}x{height}.png"))

    page.evaluate("tv.onWindowState({maximized: true, fullscreen: false})")
    assert page.evaluate("document.documentElement.classList.contains('window--maximized')")
    _settle(page)
    _check_layout(page, width, height, f"{name} {width}x{height} maximised")
    if name in SHOT_PAGES:
        page.screenshot(path=str(shots / f"{name}-{width}x{height}-max.png"))

    problems = [
        m for m in page.tv_console if m.startswith("error") or "missing key" in m
    ]
    assert [*page.tv_errors, *problems] == []
