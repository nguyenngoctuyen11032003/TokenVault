"""Test-only harness for the HTML/JS UI.

Serves ``src/tokenvault/ui`` over http on a free port (ES modules do not load from file://)
and injects a stub ``window.pywebview.api`` so the shell can be driven without Python's
real Manager API. The real dev server belongs to the manager package; this one exists only
for tests.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

UI_DIR = Path(__file__).resolve().parents[2] / "src" / "tokenvault" / "ui"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "_artifacts"

# Test-only files mounted into the served tree (exercise the route extension contract):
# fixtures/routes/<name>.js -> js/routes/<name>.js, fixtures/i18n/<name>.json -> i18n/vi/<name>.json
OVERLAY = {
    **{f"/js/routes/{p.name}": p for p in (FIXTURES_DIR / "routes").glob("*.js")},
    **{f"/i18n/vi/{p.name}": p for p in (FIXTURES_DIR / "i18n").glob("*.json")},
}

# Canned answers for the http fallback (`fetch('/api/<method>')`) used when pywebview is absent.
HTTP_API: dict[str, Any] = {"settings": {"transparent_window": True, "source": "http"}}


class _UiHandler(SimpleHTTPRequestHandler):
    # Explicit types: on Windows the registry may map .js to text/plain, which breaks modules.
    extensions_map = {
        "": "application/octet-stream",
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".txt": "text/plain; charset=utf-8",
    }

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_HEAD(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in OVERLAY:
            self.send_response(200)
            self.send_header("Content-Type", self.extensions_map[OVERLAY[path].suffix])
            self.end_headers()
            return
        super().do_HEAD()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        overlay = OVERLAY.get(path)
        if overlay is not None:
            body = overlay.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", self.extensions_map[overlay.suffix])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"[]"
        method = path.removeprefix("/api/")
        if not path.startswith("/api/") or method not in HTTP_API:
            self.send_error(404)
            return
        args = json.loads(raw or b"[]")
        body = json.dumps(HTTP_API[method] | {"echo_args": args}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    # The default listen backlog (5) drops connections when Chromium opens many at once.
    request_queue_size = 128
    daemon_threads = True


@pytest.fixture(scope="session")
def ui_server() -> Iterator[str]:
    handler = partial(_UiHandler, directory=str(UI_DIR))
    httpd = _Server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(scope="session")
def browser() -> Iterator[Any]:
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - environment without chromium
            pytest.skip(f"chromium not available: {exc}")
        yield b
        b.close()


def stub_script(settings: dict[str, Any] | None, late_api_ms: int | None = None) -> str:
    """Init script installing a fake pywebview bridge that records calls.

    With ``late_api_ms``, ``window.pywebview`` exists at once but its api only appears (and
    ``pywebviewready`` fires) after that delay, like a slow real bridge.
    """
    late = "null" if late_api_ms is None else str(int(late_api_ms))
    settings_json = json.dumps(settings if settings is not None else {"transparent_window": True})
    return f"""
    (() => {{
      const calls = [];
      window.__tvCalls = calls;
      const settings = {settings_json};
      const rec = (name, value) => (...args) => {{
        calls.push({{name, args}});
        return Promise.resolve(value);
      }};
      const api = {{
        settings: rec('settings', settings),
        window_min: rec('window_min', null),
        window_close: rec('window_close', null),
        echo: (...args) => Promise.resolve({{args}}),
      }};
      const late = {late};
      if (late === null) {{
        window.pywebview = {{ api }};
      }} else {{
        window.pywebview = {{}};
        setTimeout(() => {{
          window.pywebview.api = api;
          window.dispatchEvent(new Event('pywebviewready'));
        }}, late);
      }}
    }})();
    """


OpenShell = Callable[..., Any]


@pytest.fixture
def open_shell(browser: Any, ui_server: str) -> Iterator[OpenShell]:
    """Factory: open the shell at a hash route with a given viewport and stub settings."""
    contexts: list[Any] = []

    def _open(
        route: str = "#/home",
        *,
        width: int = 1440,
        height: int = 900,
        settings: dict[str, Any] | None = None,
        pywebview: bool = True,
        late_api_ms: int | None = None,
        init_script: str | None = None,
        setup: Callable[[Any], None] | None = None,
        ready: str = "1",
    ) -> Any:
        ctx = browser.new_context(viewport={"width": width, "height": height})
        ctx.set_default_timeout(10_000)
        contexts.append(ctx)
        if pywebview:
            ctx.add_init_script(stub_script(settings, late_api_ms))
        if init_script:
            ctx.add_init_script(init_script)
        if setup:
            setup(ctx)
        page = ctx.new_page()
        errors: list[str] = []
        console: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}"))
        page.goto(f"{ui_server}/index.html{route}")
        try:
            page.wait_for_selector(f"html[data-tv-ready='{ready}']", state="attached")
        except Exception as exc:
            state = page.evaluate("document.documentElement.dataset.tvReady ?? null")
            raise AssertionError(
                f"shell not ready (data-tv-ready={state!r}); errors={errors}; console={console}"
            ) from exc
        page.tv_errors = errors  # type: ignore[attr-defined]
        page.tv_console = console  # type: ignore[attr-defined]
        return page

    yield _open
    for ctx in contexts:
        ctx.close()


@pytest.fixture(scope="session")
def ui_dir() -> Path:
    return UI_DIR


@pytest.fixture(scope="session")
def artifacts_dir() -> Path:
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    return ARTIFACTS_DIR
