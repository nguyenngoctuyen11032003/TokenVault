"""The real UI against the real dev server (``--devserver``, demo data), headless chromium.

Each route must render (``#tv-view[data-route-state=ready]``, a titled panel) without
page errors or console errors. Routes whose module is not in this checkout yet (other
page tasks) are skipped with the reason. Screenshots: ``tests/_artifacts/devserver-*.png``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.ui

REPO = Path(__file__).resolve().parents[2]
ROUTES_DIR = REPO / "src" / "tokenvault" / "ui" / "js" / "routes"
ARTIFACTS = REPO / "tests" / "_artifacts"
ROUTES = ["home", "employees", "overview", "skills", "installer", "diagnostics"]


@pytest.fixture(scope="module")
def devserver(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    data = tmp_path_factory.mktemp("devserver")
    p = subprocess.Popen(
        [sys.executable, "-m", "tokenvault.manager.app", "--devserver", "--port", "0",
         "--data-dir", str(data)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=REPO,
    )
    try:
        assert p.stdout is not None
        yield json.loads(p.stdout.readline())["devserver"].rstrip("/")
    finally:
        p.terminate()
        p.wait(10)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - no chromium installed
            pytest.skip(f"chromium not available: {exc}")
        yield b
        b.close()


@pytest.mark.parametrize("route", ROUTES)
def test_route_renders_against_devserver(devserver: str, browser: Any, route: str) -> None:
    if not (ROUTES_DIR / f"{route}.js").exists():
        pytest.skip(f"js/routes/{route}.js is not in this checkout yet (page task not merged)")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(15_000)
    page = ctx.new_page()
    errors: list[str] = []
    console: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    try:
        page.goto(f"{devserver}/#/{route}")
        page.wait_for_selector("html[data-tv-ready='1']", state="attached")
        page.wait_for_selector(f"#tv-view[data-route='{route}'][data-route-state]",
                               state="attached")
        state = page.get_attribute("#tv-view", "data-route-state")
        assert state == "ready", (state, errors, console)
        page.wait_for_function(
            "() => [...document.querySelectorAll('#tv-view tv-panel')]"
            ".some(p => (p.getAttribute('heading') || p.textContent || '').trim())"
        )
        page.wait_for_timeout(800)  # let async data fetches finish / fail
        if route in ("home", "employees"):
            page.wait_for_function(
                "() => document.querySelector('#tv-view').textContent.includes('@congty.vn')"
            )
            ARTIFACTS.mkdir(exist_ok=True)
            page.screenshot(path=str(ARTIFACTS / f"devserver-{route}.png"))
        assert errors == [], errors
        assert console == [], console
        assert not page.query_selector("#tv-view .tv-route-error")
    finally:
        ctx.close()
