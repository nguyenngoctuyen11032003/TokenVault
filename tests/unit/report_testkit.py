"""Shared builders for the report tests (not collected: no ``test_`` prefix)."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tokenvault.common.models import UsageEvent
from tokenvault.common.pricing import Pricing
from tokenvault.manager.aggregates import Aggregates
from tokenvault.manager.store import Store

PRICING = {
    "_comment": "test",
    "models": {
        "claude-sonnet-4-6": {
            "input": 3.0,
            "output": 15.0,
            "cache_read": 0.3,
            "cache_create_5m": 3.75,
            "cache_create_1h": 6.0,
        },
        "claude-opus-4-6": {
            "input": 5.0,
            "output": 25.0,
            "cache_read": 0.5,
            "cache_create_5m": 6.25,
            "cache_create_1h": 10.0,
        },
        "claude-haiku-4-5": {
            "input": 1.0,
            "output": 5.0,
            "cache_read": 0.1,
            "cache_create_5m": 1.25,
            "cache_create_1h": 2.0,
        },
    },
}
# 2026-11-19 12:00 UTC == 19:00 in Vietnam (UTC+7)
NOW_DT = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
NOW = NOW_DT.timestamp()
VN = 7 * 60

PROJECTS = [
    "erp-api",
    "web-portal",
    "mobile-app",
    "data-pipeline",
    "infra",
    "docs-site",
    "billing",
    "search",
    "auth-service",
    "analytics",
    "design-system",
    "qa-tools",
]
MODELS = ["claude-sonnet-4-6", "claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5"]
TOOLS = ["Read", "Edit", "Bash", "Grep", "Glob", "Write", "Skill", "Task", "WebFetch"]
TITLES = [
    "Sửa lỗi đăng nhập SSO",
    "Tối ưu truy vấn báo cáo doanh thu",
    "Thêm kiểm thử cho module thanh toán",
    "Refactor API đơn hàng",
    "Viết tài liệu triển khai",
    "Điều tra rò rỉ bộ nhớ ở worker",
    "Cập nhật giao diện bảng điều khiển",
    "Di chuyển cơ sở dữ liệu sang Postgres 17",
]
NAMES = [
    "an.nguyen",
    "binh.tran",
    "chi.le",
    "dung.pham",
    "giang.hoang",
    "hai.vu",
    "khanh.do",
    "linh.bui",
    "minh.dang",
    "ngoc.ngo",
    "phuc.duong",
    "quang.ly",
    "thao.mai",
    "trang.ha",
]
TEAMS = ["Dev", "QA", "Data", "Ops"]


def write_pricing(folder: Path) -> Pricing:
    p = folder / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return Pricing.load(p)


def make_store(folder: Path, tz: int = VN) -> Store:
    return Store(folder / "m.db", tz_offset_minutes=tz)


def make_agg(store: Store, pricing: Pricing, tz: int = VN) -> Aggregates:
    return Aggregates(store, pricing, tz_offset_minutes=tz, now=lambda: NOW)


def ev(n: int | str = 1, **kw: object) -> UsageEvent:
    base = UsageEvent(
        uuid=f"u{n}",
        session_id="s1",
        message_id=f"msg_{n}",
        timestamp="2026-11-19T10:00:00Z",
        model="claude-sonnet-4-6",
        reasoning_effort=None,
        is_sidechain=False,
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=0,
        cache_create_5m_tokens=0,
        cache_create_1h_tokens=0,
        project="proj",
        session_title=None,
        tool_names=(),
        claude_account_email="a@x.com",
        account_uuid="acc",
        windows_user="alice",
        hostname="PC1",
        team="Dev",
        device_id="dev1",
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def put(store: Store, *events: UsageEvent) -> None:
    by_dev: dict[str, list[UsageEvent]] = {}
    for e in events:
        by_dev.setdefault(e.device_id, []).append(e)
    for dev, evs in by_dev.items():
        store.upsert_events(dev, evs)


def email_of(i: int) -> str:
    base = NAMES[i % len(NAMES)]
    return f"{base}{i // len(NAMES) or ''}@congty.vn"


def demo_dataset(
    store: Store,
    *,
    employees: int = 8,
    days: int = 30,
    events_per_day: int = 3,
    session_every: int = 2,
    unpriced: bool = False,
    seed: int = 7,
) -> None:
    """Deterministic demo-like data ending 'today' (2026-11-19 local)."""
    rng = random.Random(seed)
    end = NOW_DT - timedelta(hours=2)
    for i in range(employees):
        email = email_of(i)
        dev = f"dev{i:03d}"
        team = TEAMS[i % len(TEAMS)]
        weight = 0.3 + 2.5 / (1 + i)  # a few heavy users, a long tail
        store.upsert_device(
            dev,
            team,
            email,
            NOW - (5 if i % 3 == 0 else 7200 * (i + 1)),
            email.split("@")[0],
            f"PC-{i:03d}",
        )
        evs: list[UsageEvent] = []
        for d in range(days):
            if rng.random() < 0.15:  # some idle days
                continue
            for k in range(events_per_day):
                ts = end - timedelta(days=d, hours=rng.randint(0, 9), minutes=rng.randint(0, 59))
                model = rng.choice(MODELS)
                if unpriced and rng.random() < 0.2:
                    model = "claude-mystery-9"
                sid = f"{dev}-s{d // session_every}"
                evs.append(
                    ev(
                        f"{dev}-{d}-{k}",
                        session_id=sid,
                        timestamp=ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        model=model,
                        input_tokens=int(rng.randint(2_000, 60_000) * weight),
                        output_tokens=int(rng.randint(500, 9_000) * weight),
                        cache_read_tokens=int(rng.randint(10_000, 400_000) * weight),
                        cache_create_5m_tokens=int(rng.randint(0, 30_000) * weight),
                        cache_create_1h_tokens=int(rng.randint(0, 8_000) * weight),
                        project=PROJECTS[(i + d // 7 + k) % len(PROJECTS)],
                        session_title=TITLES[(i + d) % len(TITLES)],
                        tool_names=tuple(rng.sample(TOOLS, rng.randint(0, 3))),
                        claude_account_email=email,
                        windows_user=email.split("@")[0],
                        hostname=f"PC-{i:03d}",
                        team=team,
                        device_id=dev,
                        is_sidechain=rng.random() < 0.2,
                    )
                )
        for j in range(0, len(evs), 2000):
            store.upsert_events(dev, evs[j : j + 2000])
