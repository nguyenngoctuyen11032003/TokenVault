"""Realistic, deterministic demo dataset for the dev server, self-test and UI checks.

14 devices in teams Dev / QA / BA-PM (one device has no Claude account), 3
projects per employee, 30 days of work-hour sessions in the Manager's local
time, sidechain (sub-agent) turns, tool names including "Skill", one model with
NO price (cost UNKNOWN), 12 devices online and 2 offline (last seen days ago).
Each team has an enrollment package; every device is enrolled in its team's.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from tokenvault.common.models import UsageEvent
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.store import Store

UNPRICED_MODEL = "claude-nova-preview"
MODELS = [("claude-sonnet-4-6", 55), ("claude-opus-4-7", 15), ("claude-haiku-4-5", 12),
          ("claude-sonnet-4-5", 10), (UNPRICED_MODEL, 8)]
TOOLS = ["Read", "Edit", "Bash", "Grep", "Glob", "Write", "Task", "Skill", "WebFetch",
         "TodoWrite"]
PROJECTS = {
    "Dev": ["cong-thanh-toan", "app-di-dong", "core-api", "bao-cao-noi-bo", "tokenvault"],
    "QA": ["kiem-thu-tu-dong", "cong-thanh-toan", "app-di-dong", "core-api"],
    "BA-PM": ["dac-ta-yeu-cau", "bao-cao-noi-bo", "lo-trinh-2027", "cong-thanh-toan"],
}
TITLES = [
    "Sửa lỗi đăng nhập SSO", "Viết test cho API thanh toán", "Tối ưu truy vấn báo cáo",
    "Refactor module giỏ hàng", "Phân tích yêu cầu tính năng mới", "Rà soát mã nguồn PR #482",
    "Cập nhật tài liệu hướng dẫn", "Điều tra lỗi hiệu năng 🔥", "Thiết kế lược đồ dữ liệu",
    "Tự động hoá kịch bản kiểm thử", "Chuẩn bị demo cho khách hàng", "Di chuyển sang Python 3.12",
]
# (email or None, windows_user, hostname, team, online)
Person = tuple[str | None, str, str, str, bool]
PEOPLE: list[Person] = [
    ("an.nguyen@congty.vn", "an.nguyen", "DEV-PC01", "Dev", True),
    ("binh.tran@congty.vn", "binh.tran", "DEV-PC02", "Dev", True),
    ("chau.le@congty.vn", "chau.le", "DEV-PC03", "Dev", True),
    ("dung.pham@congty.vn", "dung.pham", "DEV-PC04", "Dev", True),
    ("giang.hoang@congty.vn", "giang.hoang", "DEV-PC05", "Dev", False),
    ("hanh.vu@congty.vn", "hanh.vu", "DEV-PC06", "Dev", True),
    (None, "ktv-lab", "LAB-PC07", "Dev", True),
    ("khoa.dang@congty.vn", "khoa.dang", "QA-PC01", "QA", True),
    ("lan.bui@congty.vn", "lan.bui", "QA-PC02", "QA", True),
    ("minh.do@congty.vn", "minh.do", "QA-PC03", "QA", False),
    ("ngoc.ho@congty.vn", "ngoc.ho", "QA-PC04", "QA", True),
    ("phuong.ngo@congty.vn", "phuong.ngo", "BA-PC01", "BA-PM", True),
    ("quang.duong@congty.vn", "quang.duong", "PM-PC02", "BA-PM", True),
    ("thao.ly@congty.vn", "thao.ly", "BA-PC03", "BA-PM", True),
]


@dataclass(frozen=True)
class DemoInfo:
    device_ids: list[str]
    online_ids: set[str]
    events: int
    unknown_account_device: str


def device_id(windows_user: str, hostname: str) -> str:
    return f"{hostname.lower()}-{windows_user}"


def _pick_model(rng: random.Random) -> str:
    return rng.choices([m for m, _ in MODELS], weights=[w for _, w in MODELS])[0]


def _session_events(rng: random.Random, start: datetime, sid: str, person: Person, project: str,
                     title: str, n: int) -> list[UsageEvent]:
    email, user, host, team, _ = person
    dev = device_id(user, host)
    model = _pick_model(rng)
    sidechain_from = n - rng.randint(3, 7) if rng.random() < 0.3 else n + 1
    out: list[UsageEvent] = []
    t = start
    for i in range(n):
        side = i >= sidechain_from
        tools = tuple(rng.sample(TOOLS[:7], rng.randint(0, 3)))
        if rng.random() < 0.12:
            tools = (*tools, "Skill")
        t += timedelta(seconds=rng.randint(20, 240))
        out.append(UsageEvent(
            uuid=f"{sid}-u{i}", session_id=sid, message_id=f"msg_{sid}_{i}",
            timestamp=t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            model="claude-haiku-4-5" if side else model,
            reasoning_effort=rng.choice([None, None, "medium", "high"]),
            is_sidechain=side,
            input_tokens=rng.randint(5, 900), output_tokens=rng.randint(80, 4000),
            cache_read_tokens=rng.randint(2_000, 90_000),
            cache_create_5m_tokens=rng.randint(0, 6_000) if i == 0 or rng.random() < 0.2 else 0,
            cache_create_1h_tokens=rng.randint(0, 3_000) if rng.random() < 0.05 else 0,
            project=project, session_title=title, tool_names=tools,
            claude_account_email=email, account_uuid=None if email is None else f"acc-{user}",
            windows_user=user, hostname=host, team=team, device_id=dev,
        ))
    return out


def seed(store: Store, enrollment: Enrollment | None, *, now: datetime,
         tz_offset_minutes: int, days: int = 30, rnd_seed: int = 7,
         scale: float = 1.0) -> DemoInfo:
    """Insert the demo dataset; ``now`` is aware. Returns ids for the dev server."""
    rng = random.Random(rnd_seed)
    tz = timezone(timedelta(minutes=tz_offset_minutes))
    local_now = now.astimezone(tz)
    packages = {}
    if enrollment is not None:
        for team in ("Dev", "QA", "BA-PM"):
            packages[team] = enrollment.create_package(team, "Đợt 1")
        old = enrollment.create_package("Dev", "Bản cũ")
        enrollment.revoke_package(old.package_id)
    total = 0
    ids: list[str] = []
    online: set[str] = set()
    for idx, person in enumerate(PEOPLE):
        email, user, host, team, is_online = person
        dev = device_id(user, host)
        ids.append(dev)
        projects = rng.sample(PROJECTS[team], 3)
        last_seen = now if is_online else now - timedelta(days=2 + 7 * (idx % 2))
        events: list[UsageEvent] = []
        for d in range(days - 1, -1, -1):
            day = (local_now - timedelta(days=d)).date()
            if day.weekday() >= 5 and rng.random() < 0.8:
                continue
            for s in range(rng.choice([0, 1, 1, 2, 2, 3]) if rng.random() < scale else 0):
                start = datetime(day.year, day.month, day.day, rng.randint(8, 17),
                                 rng.randint(0, 59), tzinfo=tz)
                n = rng.randint(4, 22)
                if start + timedelta(seconds=240 * n) > min(local_now, last_seen.astimezone(tz)):
                    continue
                sid = f"{dev}-{day.isoformat()}-{s}"
                events += _session_events(rng, start, sid, person, rng.choice(projects),
                                          rng.choice(TITLES), n)
        if events:
            store.upsert_events(dev, events)
            total += len(events)
        store.upsert_device(dev, team, email, last_seen.timestamp(), user, host)
        if enrollment is not None:
            enrollment.register_device(packages[team].secret, dev, team)
        if is_online:
            online.add(dev)
    unknown = device_id("ktv-lab", "LAB-PC07")
    return DemoInfo(ids, online, total, unknown)


def seed_now(store: Store, enrollment: Enrollment | None, tz_offset_minutes: int,
             **kw: object) -> DemoInfo:
    return seed(store, enrollment, now=datetime.now(UTC), tz_offset_minutes=tz_offset_minutes,
                **kw)  # type: ignore[arg-type]
