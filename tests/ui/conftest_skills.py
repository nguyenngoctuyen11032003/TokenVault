"""Stub Manager data + page fixture for the Skills / Orchestration page tests.

Loaded by ``pytest_plugins = ["conftest_skills"]``. Shapes mirror ``SkillsView`` and
``OrchestrationView`` in ``tokenvault.manager.agg_models`` serialised with ``dataclasses.asdict``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

STUB_JS = Path(__file__).resolve().parent / "fixtures" / "api_stub_skills.js"
XSS_NAME = '<img src=x onerror="window.__xss=1">'
LONG_NAME = "nguyen.thi.anh.tuyet.phong.kiem.thu.chat.luong.rat.dai@ics.vn"
RANGES = {"7d": 7, "30d": 30, "90d": 90, "all": 120}
NOTE = "Counts calls of the 'Skill' tool; per-skill names need a protocol field."

# (key, display_name, weight)
PEOPLE = [
    ("tuyen.nguyen@ics.vn", "tuyen.nguyen@ics.vn", 9),
    ("an.tran@ics.vn", "an.tran@ics.vn", 7),
    ("minh.le@ics.vn", "minh.le@ics.vn", 5),
    (LONG_NAME, LONG_NAME, 4),
    ("device:PC Kế toán@HN 01", "Unknown account (ketoan@PC Kế toán)", 3),
    ("x@evil.test", XSS_NAME, 2),
]
MODELS = ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"]


def skills_view(rng: str, *, empty: bool = False) -> dict[str, Any]:
    days = RANGES[rng]
    if empty:
        return {"range": rng, "total_invocations": 0, "daily": [], "by_employee": [], "note": NOTE}
    today = date.today()
    shape = [3, 0, 5, 8, 2, 0, 6, 4]
    daily = []
    for i in range(days):
        n = shape[i % len(shape)]
        if n:  # days without use are absent, like the real aggregate
            daily.append({"day": (today - timedelta(days=days - 1 - i)).isoformat(), "count": n})
    total = sum(d["count"] for d in daily)
    weights = sum(p[2] for p in PEOPLE)
    emp = [
        {"key": k, "display_name": n, "count": max(1, total * w // weights)} for k, n, w in PEOPLE
    ]
    emp.sort(key=lambda e: -e["count"])
    return {
        "range": rng,
        "total_invocations": total,
        "daily": daily,
        "by_employee": emp,
        "note": NOTE,
    }


def orchestration_view(rng: str, *, no_sidechain: bool = False) -> dict[str, Any]:
    scale = RANGES[rng]
    main = 1_000_000 * scale
    sub = 0 if no_sidechain else 400_000 * scale
    agents = []
    if sub:
        for i, (name, title) in enumerate(
            [
                ("tuyen.nguyen@ics.vn", "Thiết kế dashboard NitroSense"),
                ("an.tran@ics.vn", "Refactor pricing tables"),
                (LONG_NAME, "Sửa lỗi đồng bộ dữ liệu khi mất mạng, kèm bài kiểm thử rất dài"),
                (XSS_NAME, XSS_NAME),
                ("minh.le@ics.vn", None),
            ]
        ):
            agents.append(
                {
                    "session_id": f"s-{i}",
                    "device_id": f"dev-{i}",
                    "title": title,
                    "display_name": name,
                    "sidechain_tokens": int(sub / (i + 2)),
                }
            )
    return {
        "range": rng,
        "main_tokens": main,
        "sidechain_tokens": sub,
        "main_by_model": [
            {"model": m, "tokens": int(main * w)}
            for m, w in zip(MODELS, (0.55, 0.35, 0.10), strict=True)
        ],
        "sidechain_by_model": (
            []
            if no_sidechain
            else [
                {"model": m, "tokens": int(sub * w)}
                for m, w in zip(MODELS, (0.2, 0.3, 0.5), strict=True)
            ]
        ),
        "agents_by_tokens": agents,
    }


def skills_data(*, empty: bool = False, no_sidechain: bool = False) -> dict[str, Any]:
    return {
        "skills": {r: skills_view(r, empty=empty) for r in RANGES},
        "orchestration": {r: orchestration_view(r, no_sidechain=no_sidechain) for r in RANGES},
    }


OpenPage = Callable[..., Any]


@pytest.fixture
def open_skills_page(open_shell: Any) -> OpenPage:
    def _open(
        route: str = "#/skills",
        *,
        data: dict[str, Any] | None = None,
        width: int = 1440,
        height: int = 900,
        extra_script: str = "",
        settle: bool = True,
    ) -> Any:
        payload = data if data is not None else skills_data()
        script = f"window.__tvSkillsData = {json.dumps(payload)};\n" + STUB_JS.read_text(
            encoding="utf-8"
        )
        page = open_shell(route, width=width, height=height, init_script=f"{script};{extra_script}")
        if settle:
            page.wait_for_selector("#tv-view[data-route-state]")
            page.wait_for_selector("#tv-view [data-tv-rev]:not([data-tv-rev='0'])")
        return page

    return _open


def rev(page: Any) -> int:
    return int(page.evaluate("document.querySelector('#tv-view [data-tv-rev]').dataset.tvRev"))


def wait_rev_after(page: Any, before: int) -> None:
    page.wait_for_function(
        "n => Number(document.querySelector('#tv-view [data-tv-rev]')?.dataset.tvRev) > n",
        arg=before,
    )


def console_problems(page: Any) -> list[str]:
    bad = [m for m in page.tv_console if m.startswith("error") or "missing key" in m]
    return [*page.tv_errors, *bad]
