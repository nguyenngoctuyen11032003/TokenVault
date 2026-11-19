"""Report export against the real dev server: a real PDF in the demo folder, via HTTP and the UI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.ui

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def devserver(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    data = tmp_path_factory.mktemp("devserver-report")
    p = subprocess.Popen(
        [sys.executable, "-m", "tokenvault.manager.app", "--devserver", "--port", "0",
         "--data-dir", str(data)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=REPO,
    )
    try:
        assert p.stdout is not None
        url = json.loads(p.stdout.readline())["devserver"].rstrip("/")
        yield url, data
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


def _post(base: str, method: str, *args: Any) -> Any:
    req = urllib.request.Request(
        f"{base}/api/{method}", data=json.dumps(list(args)).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - localhost
        return json.loads(r.read() or b"null")


def test_http_export_writes_real_pdf_into_demo_folder(devserver: tuple[str, Path]) -> None:
    base, data = devserver
    opts = _post(base, "report_options")
    assert Path(opts["default_dir"]).resolve() == (data / "reports").resolve()
    res = _post(base, "export_report", {"scope": "all", "range": "30d"})
    pdf = Path(res["path"])
    assert pdf.parent.resolve() == (data / "reports").resolve()
    assert pdf.read_bytes().startswith(b"%PDF") and res["pages"] >= 2
    with pytest.raises(urllib.error.HTTPError):  # devserver has no opener
        _post(base, "open_path", res["path"])
    with pytest.raises(urllib.error.HTTPError):  # path outside the reports folder
        _post(base, "open_path", str(data / ".tokenvault-demo"))


def test_ui_exports_pdf_from_employees_page(devserver: tuple[str, Path], browser: Any) -> None:
    base, data = devserver
    before = set((data / "reports").glob("*.pdf"))
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        page.goto(f"{base}/#/employees")
        page.wait_for_selector("#tv-view .tv-employees table.tv-emptable tbody tr[data-key]")
        page.locator("input[data-select]").nth(0).check()
        page.locator("[data-report-open='selected']").click()
        page.wait_for_selector(".tv-report-dlg")
        page.locator("[data-report='export']").click()
        page.wait_for_selector("[data-report='result']:not([hidden])", timeout=60_000)
        name = page.locator("[data-report='file']").inner_text()
        assert name.startswith("TokenVault-BaoCao-") and name.endswith(".pdf")
        assert page.locator("[data-report='error']").is_hidden()
    finally:
        page.close()
    created = set((data / "reports").glob("*.pdf")) - before
    assert [p.name for p in created] == [name]
    assert not errors
