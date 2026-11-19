"""Stub Manager data + page fixture for the overview / projects / sessions / session tests.

Loaded by ``pytest_plugins = ["conftest_overview"]`` in the Task 15 test modules. Python builds a
deterministic list of sessions (``SessionRow`` fields plus stub-only ``tools`` / ``turn_count`` /
``day``); ``fixtures/api_stub_overview.js`` filters them and aggregates ``Overview`` /
``ProjectRow`` / ``EmployeeRow`` like the Manager would, and generates ``Turn`` lists.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

STUB_JS = Path(__file__).resolve().parent / "fixtures" / "api_stub_overview.js"

XSS = '<img src=x onerror="window.__xss=1">'
XSS_PROJECT = f"{XSS}proj"
XSS_TITLE = f"<script>window.__xss=2</script>{XSS}title"
LONG_PROJECT = (
    "Dự án chuyển đổi số toàn diện hệ thống quản lý nhân sự và tiền lương khu vực miền Bắc "
    "🚀 giai đoạn hai — Phân hệ chấm công và tính thưởng cuối năm"
)
LONG_TITLE = (
    "Sửa lỗi đồng bộ dữ liệu chấm công khi mất kết nối mạng giữa chi nhánh Hà Nội và "
    "TP. Hồ Chí Minh, kèm kiểm thử hồi quy toàn bộ luồng duyệt đơn nghỉ phép 🧪🧪🧪"
)
UNPRICED = "claude-experimental-x"
NO_MODEL = "(no model)"
ORDER_ID = "s-order/ä 1"  # '/', space and unicode in a session id
HUGE_ID = "s-huge"

PEOPLE = [  # key, display_name, team
    ("tuyen.nguyen@ics.vn", "tuyen.nguyen@ics.vn", "Dev"),
    ("an.tran@ics.vn", "an.tran@ics.vn", "Dev"),
    ("minh.le@ics.vn", "minh.le@ics.vn", "Dev"),
    ("hoa.pham@ics.vn", "hoa.pham@ics.vn", "QA"),
    ("tuyet.nguyen@ics.vn", "tuyet.nguyen@ics.vn", "QA"),
    ("linh.dang@ics.vn", "linh.dang@ics.vn", "BA / PM"),
    ("device:PC-KETOAN", "Unknown account (ketoan@PC Kế toán)", "BA / PM"),
]
PROJECTS = ["TokenVault", "erp-api", "mobile-app", "docs-site", LONG_PROJECT, XSS_PROJECT]
MODELS = ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"]
TOOLS = ["Edit", "Bash", "Read", "Grep", "Write", "Task", "WebFetch", "TodoWrite"]
TITLES = [
    "Fix login bug",
    "Thiết kế dashboard NitroSense",
    "Viết test cho collector",
    "Refactor pricing tables",
    "Review PR #214",
    None,
]
PRICE = {"claude-opus-4-7": 15.0, "claude-sonnet-4-6": 4.0, "claude-haiku-4-5": 1.0}


def _tok(total: int) -> dict[str, int]:
    parts = {
        "input_tokens": int(total * 0.10),
        "output_tokens": int(total * 0.08),
        "cache_read_tokens": int(total * 0.70),
        "cache_create_5m_tokens": int(total * 0.07),
    }
    parts["cache_create_1h_tokens"] = total - sum(parts.values())
    return {**parts, "total_tokens": total}


def _session(i: int, today: date) -> dict[str, Any]:
    key, name, team = PEOPLE[i % len(PEOPLE)]
    project = PROJECTS[(i * 7) % 4] if i % 9 else PROJECTS[4 + (i // 9) % 2]
    if i % 23 == 6:
        project = ""  # the server's value for "no project"
    models = [MODELS[i % 3]] + ([MODELS[(i + 1) % 3]] if i % 4 == 0 else [])
    if i % 13 == 5:
        models = [UNPRICED]
    elif i % 13 == 8:
        models = [MODELS[1], UNPRICED]
    elif i % 17 == 3:
        models = [NO_MODEL]
    day = today - timedelta(days=(i * 37) % 150 if i >= 12 else i % 3)
    total = 40_000 + (i * 7919) % 900_000
    priced = [m for m in models if m in PRICE]
    cost: float | None = round(total / 1_000_000 * PRICE[priced[0]], 6) if priced else None
    title: str | None = TITLES[i % len(TITLES)]
    if i == 0:
        title = LONG_TITLE
    if i == 9:
        title = XSS_TITLE
    hour = 8 + (i * 5) % 10
    return {
        **_tok(total),
        "session_id": f"s-{i:03d}",
        "device_id": f"dev-{i % len(PEOPLE):02d}",
        "title": title,
        "project": project,
        "projects": [project],
        "models": models,
        "employee_key": key,
        "display_name": name,
        "events": 10 + i % 40,
        "sidechain_tokens": int(total * 0.2) if i % 5 == 0 else 0,
        "cost": cost,
        "cost_partial": bool(priced) and len(priced) < len(models),
        "started_at": f"{day.isoformat()}T{hour:02d}:{i % 60:02d}:00+07:00",
        "last_at": f"{day.isoformat()}T{hour + 1:02d}:{i % 60:02d}:00+07:00",
        # stub-only
        "team": team,
        "day": day.isoformat(),
        "tools": {TOOLS[(i + k) % len(TOOLS)]: 3 + (i * (k + 1)) % 40 for k in range(3)},
        "turn_count": 12 + i % 30,
    }


def _order_turns(day: date) -> list[dict[str, Any]]:
    """Out-of-order turns (the UI must still list them by time); two are sidechain.

    Timestamps are raw UTC instants (``...Z``) like ``Aggregates.session_turns`` returns.
    """
    out = []
    for n in range(12):
        minute = (n * 7) % 60
        hour = 2 + (n * 7) // 60
        out.append({
            **_tok(5_000 + n * 1_000),
            "timestamp": f"{day.isoformat()}T{hour:02d}:{minute:02d}:{n:02d}Z",
            "device_id": "dev-00",
            "model": MODELS[n % 2],
            "reasoning_effort": None,
            "is_sidechain": n in (4, 5),
            "tool_names": ["Edit"] if n % 3 == 0 else [],
            "cost": round(0.01 * (n + 1), 6),
            "n": n,  # stub-only: position in time order
        })
    shuffled = out[5:] + out[:5]
    shuffled.reverse()
    return shuffled


def overview_data(*, count: int = 80, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    sessions = [_session(i, today) for i in range(count)]
    order = {
        **sessions[1],
        "session_id": ORDER_ID,
        "title": "Phiên kiểm tra thứ tự lượt",
        "day": today.isoformat(),
        "started_at": f"{today.isoformat()}T09:00:00+07:00",
        "turn_count": 12,
    }
    huge = {
        **sessions[2],
        "session_id": HUGE_ID,
        "title": "Phiên rất dài (5.000 lượt)",
        "day": today.isoformat(),
        "started_at": f"{today.isoformat()}T06:00:00+07:00",
        "turn_count": 5000,
    }
    teams: dict[str, dict[str, int]] = {}
    for _key, _name, team in PEOPLE:
        t = teams.setdefault(team, {"devices": 0, "online": 0})
        t["devices"] += 1
        t["online"] += 1
    return {
        "today": today.isoformat(),
        "teams": [{"name": n, **v} for n, v in teams.items()],
        "people": [{"key": k, "display_name": n, "team": t} for k, n, t in PEOPLE],
        "sessions": [*sessions, order, huge],
        "turns": {ORDER_ID: _order_turns(today)},
        "unpriced": [UNPRICED],
    }


def stub_init_script(data: dict[str, Any], filters: dict[str, Any] | None = None) -> str:
    preset = ""
    if filters is not None:
        # Only on the first load of the tab: a reload must see what the app persisted.
        preset = (
            "try { if (!sessionStorage.getItem('tv.test.preset')) {"
            " sessionStorage.setItem('tv.test.preset', '1');"
            f" localStorage.setItem('tv.filters.v1', {json.dumps(json.dumps(filters))}); }}"
            " } catch {}\n"
        )
    return (
        preset
        + f"window.__tvOvData = {json.dumps(data)};\n"
        + STUB_JS.read_text(encoding="utf-8")
    )


OpenPage = Callable[..., Any]

DEFAULT_FILTERS = {"team": None, "email": None, "project": None, "model": None, "range": "90d"}


@pytest.fixture
def open_ov(open_shell: Any) -> OpenPage:
    """Open a Task 15 route with stub data; waits until the page finished its first draw.

    ``filters`` presets ``localStorage['tv.filters.v1']`` before boot (default: 90-day range,
    no other filter); pass ``None`` to keep whatever the context has.
    """

    def _open(
        route: str = "#/overview",
        *,
        data: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = DEFAULT_FILTERS,
        width: int = 1440,
        height: int = 900,
        settle: bool = True,
        extra_script: str = "",
    ) -> Any:
        script = stub_init_script(data if data is not None else overview_data(), filters)
        page = open_shell(
            route, width=width, height=height, init_script=f"{script};{extra_script}"
        )
        if settle:
            wait_page(page)
        return page

    return _open


def wait_page(page: Any) -> None:
    page.wait_for_selector("#tv-view[data-route-state]")
    page.wait_for_selector("#tv-view .tv-route > [data-tv-rev]:not([data-tv-rev='0'])")


def rev(page: Any) -> int:
    return int(
        page.evaluate("document.querySelector('#tv-view .tv-route > [data-tv-rev]').dataset.tvRev")
    )


def wait_rev_after(page: Any, before: int) -> None:
    page.wait_for_function(
        "n => Number(document.querySelector('#tv-view .tv-route > [data-tv-rev]')"
        "?.dataset.tvRev) > n",
        arg=before,
    )


def go(page: Any, hash_: str) -> None:
    """Navigate inside the app and wait for the new page's first draw."""
    page.evaluate("h => { location.hash = h; }", hash_)
    route = hash_.split("/")[1]
    page.wait_for_selector(f"#tv-view[data-route='{route}'][data-route-state]")
    wait_page(page)


def calls(page: Any, name: str) -> list[list[Any]]:
    return [c["args"] for c in page.evaluate("window.__tvCalls") if c["name"] == name]


def console_problems(page: Any) -> list[str]:
    bad = [m for m in page.tv_console if m.startswith("error") or "missing key" in m]
    return [*page.tv_errors, *bad]


def css_color(page: Any, colour: str) -> str:
    return str(
        page.evaluate(
            "c => { const d = document.createElement('i'); d.style.background = c;"
            " document.body.append(d); const v = getComputedStyle(d).backgroundColor;"
            " d.remove(); return v; }",
            colour,
        )
    )


_BASE_BRIDGE = """
(() => {
  const calls = [];
  window.__tvCalls = calls;
  const rec = (name, value) => (...args) => {
    calls.push({name, args});
    return Promise.resolve(value);
  };
  window.pywebview = { api: {
    settings: rec('settings', {transparent_window: true}),
    window_min: rec('window_min', null),
    window_close: rec('window_close', null),
  } };
})();
"""


@pytest.fixture
def open_tz(browser: Any, ui_server: str) -> Iterator[OpenPage]:
    """Like ``open_ov`` but in a browser context with a forced IANA time zone."""
    contexts: list[Any] = []

    def _open(
        route: str,
        *,
        timezone_id: str,
        data: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = DEFAULT_FILTERS,
        width: int = 1440,
        height: int = 900,
    ) -> Any:
        ctx = browser.new_context(
            viewport={"width": width, "height": height}, timezone_id=timezone_id
        )
        ctx.set_default_timeout(10_000)
        contexts.append(ctx)
        ctx.add_init_script(_BASE_BRIDGE)
        script = stub_init_script(data if data is not None else overview_data(), filters)
        ctx.add_init_script(script)
        page = ctx.new_page()
        errors: list[str] = []
        console: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}"))
        page.goto(f"{ui_server}/index.html{route}")
        page.wait_for_selector("html[data-tv-ready='1']", state="attached")
        page.tv_errors = errors  # type: ignore[attr-defined]
        page.tv_console = console  # type: ignore[attr-defined]
        wait_page(page)
        return page

    yield _open
    for ctx in contexts:
        ctx.close()
