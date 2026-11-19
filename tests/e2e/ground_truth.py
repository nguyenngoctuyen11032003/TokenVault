"""INDEPENDENT ground truth computed straight from transcript files.

Deliberately does NOT import ``tokenvault``: its own tiny JSONL reader, its own
message-id de-duplication and its own cost math over the same ``pricing.json``.

Rules (from the product spec, re-implemented here):
* only complete lines (ending in ``\\n``) count; a partial last line is ignored;
* a usage row is a JSON object with ``type == "assistant"``, a dict ``message`` with a
  dict ``usage``, non-empty string ``uuid``/``sessionId`` and an ISO-8601 ``timestamp``;
* key = ``message.id`` (else ``uuid``); repeated lines of one key are merged by taking
  the MAX of each token field (one API response streams several lines);
* tokens: input, output, cache_read, cache 5m/1h (``cache_creation`` object, else the
  flat ``cache_creation_input_tokens`` counts as 5m);
* project = last path segment of ``cwd``; local day = UTC timestamp + tz offset;
* employee key = account e-mail, else ``device:<device_id>``; sessions are distinct
  (device, session id) pairs; cost = sum over models of tokens x rate / 1e6 (unknown
  model -> no cost, flagged partial).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

FIELDS = ("input", "output", "cache_read", "c5", "c1")


@dataclass
class Msg:
    key: str
    session: str
    ts: datetime
    model: str
    project: str
    sidechain: bool
    tok: dict[str, int]


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def complete_lines(path: Path, limit: int | None = None) -> Iterator[bytes]:
    """Complete lines of a file (optionally only within the first ``limit`` bytes)."""
    with open(path, "rb") as fh:  # read-only
        data = fh.read() if limit is None else fh.read(limit)
    end = data.rfind(b"\n")
    if end < 0:
        return
    yield from data[: end + 1].splitlines()


def parse_usage(line: bytes) -> tuple[Msg | None, tuple[str, str] | None]:
    """(usage row or None, (session, title) or None)."""
    try:
        o = json.loads(line)
    except (ValueError, RecursionError):
        return None, None
    if not isinstance(o, dict):
        return None, None
    if o.get("type") == "ai-title":
        s, t = o.get("sessionId"), o.get("aiTitle")
        if isinstance(s, str) and s and isinstance(t, str) and t:
            return None, (s, t)
        return None, None
    if o.get("type") != "assistant":
        return None, None
    m = o.get("message")
    if not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
        return None, None
    u = m["usage"]
    uid, sid, ts = o.get("uuid"), o.get("sessionId"), o.get("timestamp")
    if not (isinstance(uid, str) and uid and isinstance(sid, str) and sid):
        return None, None
    try:
        when = datetime.fromisoformat(ts) if isinstance(ts, str) and ts else None
    except ValueError:
        when = None
    if when is None:
        return None, None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    cc = u.get("cache_creation")
    if isinstance(cc, dict):
        c5 = _int(cc.get("ephemeral_5m_input_tokens"))
        c1 = _int(cc.get("ephemeral_1h_input_tokens"))
    else:
        c5, c1 = _int(u.get("cache_creation_input_tokens")), 0
    cwd = o.get("cwd")
    segs = [p for p in cwd.replace("\\", "/").split("/") if p] if isinstance(cwd, str) else []
    project = segs[-1] if segs and not (len(segs) == 1 and segs[0].endswith(":")) else "(unknown)"
    mid = m.get("id")
    model = m.get("model") if isinstance(m.get("model"), str) and m.get("model") else ""
    return Msg(
        key=mid if isinstance(mid, str) and mid else uid,
        session=sid, ts=when, model=model, project=project,
        sidechain=o.get("isSidechain") is True,
        tok={"input": _int(u.get("input_tokens")), "output": _int(u.get("output_tokens")),
             "cache_read": _int(u.get("cache_read_input_tokens")), "c5": c5, "c1": c1},
    ), None


def collect(files: Iterable[Path], limits: dict[Path, int] | None = None
            ) -> tuple[dict[str, Msg], dict[str, str], dict[str, int]]:
    """Merged messages by key, final titles by session, and line statistics."""
    msgs: dict[str, Msg] = {}
    titles: dict[str, str] = {}
    stats = {"files": 0, "lines": 0, "usage_lines": 0}
    for f in files:
        stats["files"] += 1
        for line in complete_lines(f, (limits or {}).get(f)):
            stats["lines"] += 1
            msg, title = parse_usage(line)
            if title:
                titles[title[0]] = title[1]
            if msg is None:
                continue
            stats["usage_lines"] += 1
            prev = msgs.get(msg.key)
            if prev is None:
                msgs[msg.key] = msg
            else:
                for k in FIELDS:
                    prev.tok[k] = max(prev.tok[k], msg.tok[k])
    return msgs, titles, stats


def jsonl_files(root: Path) -> list[Path]:
    out = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if n.lower().endswith(".jsonl"):
                out.append(Path(dirpath) / n)
    return sorted(out)


class Prices:
    def __init__(self, path: Path) -> None:
        doc = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
        self.models = doc["models"]
        self.aliases = doc.get("aliases", {})

    def rate(self, model: str) -> dict[str, Decimal] | None:
        r = self.models.get(model) or self.models.get(self.aliases.get(model, "\0"))
        if r is None:
            return None
        return {"input": Decimal(str(r["input"])), "output": Decimal(str(r["output"])),
                "cache_read": Decimal(str(r["cache_read"])),
                "c5": Decimal(str(r["cache_create_5m"])), "c1": Decimal(str(r["cache_create_1h"]))}

    def cost(self, model: str, tok: dict[str, int]) -> Decimal | None:
        r = self.rate(model)
        if r is None:
            return None
        return sum((r[k] * tok[k] for k in FIELDS), Decimal(0)) / Decimal(1_000_000)


@dataclass
class Bucket:
    tok: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    events: int = 0
    cost: Decimal = Decimal(0)
    partial: bool = False
    sessions: set[tuple[str, str]] = field(default_factory=set)
    projects: set[str] = field(default_factory=set)
    models: set[str] = field(default_factory=set)
    devices: set[str] = field(default_factory=set)

    def add(self, m: Msg, device: str, prices: Prices) -> None:
        for k in FIELDS:
            self.tok[k] += m.tok[k]
        self.events += 1
        c = prices.cost(m.model, m.tok)
        if c is None:
            if any(m.tok.values()):
                self.partial = True
        else:
            self.cost += c
        self.sessions.add((device, m.session))
        self.projects.add(m.project)
        self.models.add(m.model or "(no model)")
        self.devices.add(device)

    @property
    def total(self) -> int:
        return sum(self.tok.values())

    def summary(self) -> dict[str, Any]:
        return {"input": self.tok["input"], "output": self.tok["output"],
                "cache_read": self.tok["cache_read"], "c5": self.tok["c5"],
                "c1": self.tok["c1"], "total": self.total, "events": self.events,
                "sessions": len(self.sessions), "cost": float(self.cost),
                "partial": self.partial, "projects": sorted(self.projects),
                "models": sorted(self.models)}


@dataclass
class Device:
    device_id: str
    email: str | None
    team: str
    projects_dir: Path


@dataclass
class Truth:
    overall: Bucket
    by_employee: dict[str, Bucket]
    by_team: dict[str, Bucket]
    by_day: dict[str, Bucket]
    by_employee_day: dict[tuple[str, str], Bucket]
    by_model: dict[str, Bucket]
    by_project: dict[str, Bucket]
    titles: dict[tuple[str, str], str]  # (device, session) -> title
    sidechain_tokens: int
    stats: dict[str, int]


def compute(devices: list[Device], pricing: Path, tz_offset_minutes: int) -> Truth:
    prices = Prices(pricing)
    tz = timedelta(minutes=tz_offset_minutes)
    overall = Bucket()
    by_emp: dict[str, Bucket] = defaultdict(Bucket)
    by_team: dict[str, Bucket] = defaultdict(Bucket)
    by_day: dict[str, Bucket] = defaultdict(Bucket)
    by_emp_day: dict[tuple[str, str], Bucket] = defaultdict(Bucket)
    by_model: dict[str, Bucket] = defaultdict(Bucket)
    by_project: dict[str, Bucket] = defaultdict(Bucket)
    titles: dict[tuple[str, str], str] = {}
    side = 0
    stats: dict[str, int] = defaultdict(int)
    for d in devices:
        msgs, t, st = collect(jsonl_files(d.projects_dir))
        for k, v in st.items():
            stats[k] += v
        key = d.email or f"device:{d.device_id}"
        sessions_seen = set()
        for m in msgs.values():
            day = (m.ts.astimezone(UTC) + tz).strftime("%Y-%m-%d")
            for b in (overall, by_emp[key], by_team[d.team], by_day[day], by_emp_day[(key, day)],
                      by_model[m.model or "(no model)"], by_project[m.project]):
                b.add(m, d.device_id, prices)
            if m.sidechain:
                side += sum(m.tok.values())
            sessions_seen.add(m.session)
        for s, title in t.items():
            if s in sessions_seen:
                titles[(d.device_id, s)] = title
    stats["messages"] = overall.events
    return Truth(overall, dict(by_emp), dict(by_team), dict(by_day), dict(by_emp_day),
                 dict(by_model), dict(by_project), titles, side, dict(stats))
