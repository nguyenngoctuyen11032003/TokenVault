"""Stub Manager data + page fixture for the home / employees / employee page tests.

Loaded by ``pytest_plugins = ["conftest_home"]`` in the Task 14 test modules. The data mirrors
``tokenvault.manager.agg_models`` (Overview, EmployeeRow, EmployeeView, ...) serialised with
``dataclasses.asdict``; ``api.teams()`` returns ``[{name, devices, online}]``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

STUB_JS = Path(__file__).resolve().parent / "fixtures" / "api_stub_home.js"

XSS_NAME = '<img src=x onerror="window.__xss=1">'
XSS_KEY = "x@evil.test"
ODD_KEY = "device:PC Kế toán@HN 01"  # space, '@', ':' and unicode in an employee key

# (key, email, display_name, team, devices[(id, host, user, online)], tokens_today, cost)
PEOPLE: list[tuple[str, str | None, str, str, list[tuple[str, str, str, bool]], int, float]] = [
    ("tuyen.nguyen@ics.vn", "tuyen.nguyen@ics.vn", "tuyen.nguyen@ics.vn", "Dev",
     [("dev-01", "DESKTOP-TUYEN", "TUYEN", True)], 1_210_000, 15.4),
    ("an.tran@ics.vn", "an.tran@ics.vn", "an.tran@ics.vn", "Dev",
     [("dev-02", "LAPTOP-AN", "an.tran", True), ("dev-09", "DEV-BOX-02", "an", False)],
     940_000, 12.1),
    ("minh.le@ics.vn", "minh.le@ics.vn", "minh.le@ics.vn", "Dev",
     [("dev-03", "DESKTOP-MINH", "minh", False)], 610_000, 7.9),
    ("hoa.pham@ics.vn", "hoa.pham@ics.vn", "hoa.pham@ics.vn", "QA",
     [("qa-01", "QA-STATION-1", "hoa", True)], 400_000, 4.6),
    ("nguyen.thi.anh.tuyet.phong.kiem.thu.chat.luong@ics.vn",
     "nguyen.thi.anh.tuyet.phong.kiem.thu.chat.luong@ics.vn",
     "nguyen.thi.anh.tuyet.phong.kiem.thu.chat.luong@ics.vn", "QA",
     [("qa-02", "QA-STATION-2", "tuyet", True)], 352_000, 4.1),
    ("quang.vo@ics.vn", "quang.vo@ics.vn", "quang.vo@ics.vn", "Dev",
     [("dev-04", "DESKTOP-QUANG", "quang", True)], 298_000, 3.5),
    ("linh.dang@ics.vn", "linh.dang@ics.vn", "linh.dang@ics.vn", "BA / PM",
     [("ba-01", "LAPTOP-LINH", "linh", False)], 220_000, 2.4),
    (ODD_KEY, None, "Unknown account (ketoan@PC Kế toán)", "BA / PM",
     [("PC Kế toán@HN 01", "PC Kế toán", "ketoan", True)], 180_000, 2.0),
    ("bao.huynh@ics.vn", "bao.huynh@ics.vn", "bao.huynh@ics.vn", "Dev",
     [("dev-05", "DESKTOP-BAO", "bao", False)], 156_000, 1.8),
    ("thao.ngo@ics.vn", "thao.ngo@ics.vn", "thao.ngo@ics.vn", "Dev",
     [("dev-06", "DESKTOP-THAO", "thao", True)], 120_000, 1.4),
    (XSS_KEY, XSS_KEY, XSS_NAME, "QA", [("qa-03", "QA-03", "x", False)], 90_000, 1.0),
    ("khanh.do@ics.vn", "khanh.do@ics.vn", "khanh.do@ics.vn", "Dev",
     [("dev-07", "DESKTOP-KHANH", "khanh", False)], 64_000, 0.7),
]

PROJECTS = ["TokenVault", "erp-api", "mobile-app", "docs-site", "infra-terraform", "hr-portal"]
MODELS = ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"]
RANGE_FACTOR = {"today": 1.0, "7d": 5.3, "30d": 21.0}
RANGE_DAYS = {"today": 1, "7d": 7, "30d": 30}
TITLES = [
    "Fix login bug",
    "Thiết kế dashboard NitroSense",
    "Viết test cho collector",
    "Refactor pricing tables",
    "Sửa lỗi đồng bộ dữ liệu khi mất mạng",
    "Review PR #214",
]


def _tok(total: int) -> dict[str, int]:
    parts = {
        "input_tokens": int(total * 0.10),
        "output_tokens": int(total * 0.08),
        "cache_read_tokens": int(total * 0.70),
        "cache_create_5m_tokens": int(total * 0.07),
    }
    parts["cache_create_1h_tokens"] = total - sum(parts.values())
    return {**parts, "total_tokens": total}


def _costed(total: int, cost: float | None, **extra: Any) -> dict[str, Any]:
    return {**_tok(total), "cost": cost, "cost_partial": False, "unknown_cost_models": [], **extra}


HOUR_SHAPE = [0, 0, 0, 0, 0, 0, 0, 2, 9, 30, 52, 44, 18, 31, 47, 40, 36, 24, 12, 6, 3, 1, 0, 0]


def _hourly(day: date, total: int, cost: float | None) -> list[dict[str, Any]]:
    weight = sum(HOUR_SHAPE)
    out = []
    for h, w in enumerate(HOUR_SHAPE):
        tokens = int(total * w / weight)
        c = None if cost is None else round(cost * w / weight, 6)
        out.append(_costed(tokens, c, hour=f"{day.isoformat()}T{h:02d}:00"))
    return out


def _daily(end: date, days: int, total: int, cost: float | None) -> list[dict[str, Any]]:
    shape = [0.6, 0.9, 1.0, 0.8, 1.2, 0.3, 0.2] * 5
    pts = shape[:days]
    weight = sum(pts)
    out = []
    for i, w in enumerate(pts):
        day = end - timedelta(days=days - 1 - i)
        c = None if cost is None else round(cost * w / weight, 6)
        out.append(_costed(int(total * w / weight), c, day=day.isoformat()))
    return out


def _split(total: int, names: list[str], weights: list[float]) -> list[tuple[str, int]]:
    s = sum(weights)
    return [(n, int(total * w / s)) for n, w in zip(names, weights, strict=True)]


def _overview(day: date, scale: float, *, ref: bool, unknown: bool) -> dict[str, Any]:
    total = int(4_820_000 * scale * (21 if ref else 1))
    cost = None if unknown else round(61.4 * scale * (21 if ref else 1), 6)
    sessions = max(1, int(37 * scale * (21 if ref else 1)))
    projects = _split(total, PROJECTS, [1.4, 0.9, 0.7, 0.4, 0.25, 0.15])
    models = _split(total, MODELS, [0.55, 0.35, 0.10])
    by_model = [
        {**_tok(t), "model": m, "events": 10,
         "cost": None if unknown else round((cost or 0) * t / max(total, 1), 6)}
        for m, t in models
    ]
    return {
        "totals": {**_tok(total), "events": 900, "sessions": sessions, "cost": cost,
                   "cost_partial": False},
        "hourly": _hourly(day, total, cost),
        "daily": _daily(day, 30 if ref else 1, total, cost),
        "monthly": [],
        "cache_daily": [],
        "by_project": [
            {**_costed(t, None if cost is None else round(cost * t / max(total, 1), 6)),
             "project": p, "events": 50, "sessions": 4, "employees": 3, "last_active": None}
            for p, t in projects
        ],
        "by_model": by_model,
        "top_tools": [{"tool": "Edit", "calls": 120}, {"tool": "Bash", "calls": 90}],
        "recent_sessions": [],
        "unknown_cost_models": ["claude-experimental-x"] if unknown else [],
    }


def _employee_view(person: tuple[Any, ...], rng: str, day: date, unknown: bool) -> dict[str, Any]:
    key, email, name, team, devices, tokens, cost = person
    total = int(tokens * RANGE_FACTOR[rng])
    c = None if unknown else round(cost * RANGE_FACTOR[rng], 6)
    projects = _split(total, PROJECTS[:3], [0.62, 0.28, 0.10])
    models = _split(total, MODELS[:2], [0.7, 0.3])
    n_sessions = {"today": 3, "7d": 6, "30d": 6}[rng]
    sessions = []
    for i in range(n_sessions):
        st = int(total / (i + 2))
        sessions.append({
            **_tok(st), "session_id": f"s-{key}-{rng}-{i}", "device_id": devices[0][0],
            "title": TITLES[i % len(TITLES)] if i != 4 else None,
            "project": PROJECTS[i % 3], "projects": [PROJECTS[i % 3]],
            "models": [MODELS[i % 2]], "employee_key": key, "display_name": name,
            "events": 12, "sidechain_tokens": 0,
            "cost": None if c is None else round(c / (i + 2), 6), "cost_partial": False,
            "started_at": f"{day.isoformat()}T09:{10 + i:02d}:00+07:00",
            "last_at": f"{day.isoformat()}T10:{10 + i:02d}:00+07:00",
        })
    return {
        "key": key, "email": email, "display_name": name, "range": rng,
        "totals": {**_tok(total), "events": 40 * RANGE_DAYS[rng],
                   "sessions": n_sessions * RANGE_DAYS[rng], "cost": c, "cost_partial": False},
        "hourly": _hourly(day, total if rng == "today" else int(tokens), c),
        "daily": _daily(day, RANGE_DAYS[rng], total, c),
        "monthly": [],
        "by_project": [
            {**_costed(t, None if c is None else round(c * t / total, 6)), "project": p,
             "events": 20, "sessions": 2, "employees": 1, "last_active": None}
            for p, t in projects
        ],
        "by_model": [
            {**_tok(t), "model": m, "events": 10,
             "cost": None if c is None else round(c * t / total, 6)}
            for m, t in models
        ],
        "sessions": sessions,
        "devices": [
            {"device_id": d, "team": team, "windows_user": u, "hostname": h, "online": on,
             "last_seen": f"{day.isoformat()}T10:00:00+07:00"}
            for d, h, u, on in devices
        ],
        "unknown_cost_models": ["claude-experimental-x"] if unknown else [],
    }


def home_data(*, unknown_cost: bool = False, empty: bool = False) -> dict[str, Any]:
    """Seeded fake Manager data (see module docstring)."""
    today = date.today()
    if empty:
        zero = _overview(today, 0.0, ref=False, unknown=False)
        zero["by_project"], zero["by_model"], zero["unknown_cost_models"] = [], [], []
        zero["totals"]["sessions"] = 0
        return {"teams": [], "employees": [], "employee": {},
                "overview": {"|today": zero, "|ref": copy.deepcopy(zero)}}
    teams: dict[str, dict[str, int]] = {}
    employees = []
    for person in PEOPLE:
        key, email, name, team, devices, tokens, cost = person
        t = teams.setdefault(team, {"devices": 0, "online": 0})
        t["devices"] += len(devices)
        t["online"] += sum(1 for d in devices if d[3])
        employees.append({
            **_tok(tokens), "key": key, "email": email, "display_name": name,
            "devices": [d[0] for d in devices], "teams": [team],
            "online": any(d[3] for d in devices),
            "last_seen": f"{today.isoformat()}T10:00:00+07:00", "events": 100, "sessions": 5,
            "cost": None if unknown_cost else cost, "cost_partial": False, "share": 0.0,
        })
    scales = {"": 1.0, "Dev": 0.62, "QA": 0.2, "BA / PM": 0.18}
    overview = {}
    for team, scale in scales.items():
        overview[f"{team}|today"] = _overview(today, scale, ref=False, unknown=unknown_cost)
        overview[f"{team}|ref"] = _overview(today, scale, ref=True, unknown=unknown_cost)
    return {
        "teams": [{"name": n, **v} for n, v in teams.items()],
        "employees": employees,
        "overview": overview,
        "employee": {
            p[0]: {r: _employee_view(p, r, today, unknown_cost) for r in RANGE_FACTOR}
            for p in PEOPLE
        },
    }


def stub_init_script(data: dict[str, Any]) -> str:
    return f"window.__tvHomeData = {json.dumps(data)};\n" + STUB_JS.read_text(encoding="utf-8")


OpenPage = Callable[..., Any]


@pytest.fixture
def open_page(open_shell: Any) -> OpenPage:
    """Open a Task 14 route with stub data; waits until the page finished its first draw."""

    def _open(
        route: str = "#/home",
        *,
        data: dict[str, Any] | None = None,
        width: int = 1440,
        height: int = 900,
        settle: bool = True,
    ) -> Any:
        page = open_shell(
            route, width=width, height=height,
            init_script=stub_init_script(data if data is not None else home_data()),
        )
        page.wait_for_selector("#tv-view[data-route-state]")
        if settle:
            page.wait_for_selector("#tv-view [data-tv-rev]:not([data-tv-rev='0'])")
        return page

    return _open


def wait_rev_after(page: Any, before: int) -> None:
    page.wait_for_function(
        "n => Number(document.querySelector('#tv-view [data-tv-rev]')?.dataset.tvRev) > n",
        arg=before,
    )


def rev(page: Any) -> int:
    return int(page.evaluate("document.querySelector('#tv-view [data-tv-rev]').dataset.tvRev"))


def console_problems(page: Any) -> list[str]:
    bad = [m for m in page.tv_console if m.startswith("error") or "missing key" in m]
    return [*page.tv_errors, *bad]
