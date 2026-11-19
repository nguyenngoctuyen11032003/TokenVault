"""Fake employee home folders with realistic Claude Code transcripts (E2E harness).

Every employee gets ``<root>/homes/<user>/`` with::

    .claude.json                                  oauthAccount e-mail (+ noise holding MARK)
    .claude/projects/<encoded cwd>/<session>.jsonl
    .claude/projects/<encoded cwd>/<session>/subagents/agent-<id>.jsonl   (sidechain)

The privacy marker :data:`MARK` is planted in every place that must never leave the
machine: prompt text, reply text, thinking, tool inputs, tool results (file content),
the full cwd path (NOT its last segment, which is the project name by design), the
git branch, summary lines, the encoded project folder name and .claude.json noise.

Edge cases on purpose: several lines per message id (streaming, growing output),
one message id copied into a second session file (resume), sidechain sub-agent
files, an unknown model, a ``<synthetic>`` zero-usage line, a line without
``message.id`` (falls back to uuid), garbage / non-object / invalid-timestamp lines,
a partial last line (no newline yet), session titles (one session renamed twice),
and timestamps on both sides of the Manager's local midnight (UTC+7).
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MARK = "TVPRIVMARK7f3a9c"  # unique; must never appear on the wire or in the Manager

KNOWN_MODELS = ("claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
                "claude-sonnet-4-5")
UNKNOWN_MODEL = "claude-mystery-9"


@dataclass
class Employee:
    user: str  # fake Windows user name (USERNAME) -> device_id = "<host>:<user>"
    email: str | None  # None -> no oauthAccount (unknown account)
    team: str
    seed: int
    home: Path = field(default=Path())

    @property
    def projects_dir(self) -> Path:
        return self.home / ".claude" / "projects"


EMPLOYEES = (
    Employee("alice", "alice@congty.vn", "Dev", 1),
    Employee("alice-laptop", "alice@congty.vn", "Dev", 2),  # same person, second device
    Employee("bob", "bob@congty.vn", "Dev", 3),
    Employee("carol", "carol@congty.vn", "QA", 4),
    Employee("dan", None, "QA", 5),  # no Claude account e-mail
)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def encode_cwd(cwd: str) -> str:
    """Claude Code's project folder name: every non-alphanumeric char becomes '-'."""
    return "".join(c if c.isalnum() else "-" for c in cwd)


class SessionBuilder:
    """Builds the lines of one session (main file) and its sub-agent files."""

    def __init__(self, emp: Employee, rng: random.Random, cwd: str, session_id: str) -> None:
        self.emp = emp
        self.rng = rng
        self.cwd = cwd
        self.sid = session_id
        self.lines: list[str] = []
        self.sub_files: dict[str, list[str]] = {}
        self.n = 0

    def _uuid(self) -> str:
        return f"{self.rng.getrandbits(128):032x}"

    def _base(self, ts: datetime, kind: str, sidechain: bool = False) -> dict[str, Any]:
        return {
            "parentUuid": self._uuid(), "isSidechain": sidechain, "userType": "external",
            "cwd": self.cwd, "sessionId": self.sid, "version": "2.1.0",
            "gitBranch": f"feature/{MARK}-branch", "type": kind, "uuid": self._uuid(),
            "timestamp": iso(ts),
        }

    def user(self, ts: datetime, sidechain: bool = False) -> str:
        d = self._base(ts, "user", sidechain)
        d["message"] = {"role": "user",
                        "content": f"Xin chào, please fix {MARK} prompt text in login.py"}
        return json.dumps(d, ensure_ascii=False)

    def tool_result(self, ts: datetime, sidechain: bool = False) -> str:
        d = self._base(ts, "user", sidechain)
        d["message"] = {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_" + self._uuid()[:20],
             "content": f"file content line 1\nSECRET={MARK} file content\n"}]}
        d["toolUseResult"] = {"type": "text", "file": {
            "filePath": f"C:\\Users\\{self.emp.user}\\{MARK}\\src\\login.py",
            "content": f"password = '{MARK}'\n", "numLines": 1}}
        return json.dumps(d, ensure_ascii=False)

    def response(self, ts: datetime, model: str, *, parts: int = 2, sidechain: bool = False,
                 msg_id: str | None = None, flat_cache: bool = False, tools: tuple[str, ...] = (),
                 usage: dict[str, int] | None = None, no_id: bool = False) -> list[str]:
        """One API response written as `parts` lines sharing message.id (output grows)."""
        self.n += 1
        mid = msg_id or f"msg_{self.emp.user}_{self.sid[:8]}_{self.n}_{self._uuid()[:6]}"
        r = self.rng
        u = usage or {
            "input_tokens": r.randint(1, 60), "output": r.randint(50, 4000),
            "cache_read_input_tokens": r.randint(0, 90_000),
            "c5": r.randint(0, 6000), "c1": r.randint(0, 3000),
        }
        out: list[str] = []
        for p in range(parts):
            d = self._base(ts + timedelta(milliseconds=300 * p), "assistant", sidechain)
            d["requestId"] = "req_" + mid
            if p == 0:
                content: list[dict[str, Any]] = [
                    {"type": "thinking", "thinking": f"thinking about {MARK}", "signature": "x"}]
            elif p == parts - 1:
                content = [{"type": "text", "text": f"Here is the reply {MARK} text"}]
            else:
                content = []
            if tools and p >= 1:
                for t in tools:
                    content.append({"type": "tool_use", "id": "toolu_" + self._uuid()[:20],
                                    "name": t, "input": {
                                        "file_path": f"C:\\Users\\{self.emp.user}\\{MARK}\\a.py",
                                        "command": f"grep -r {MARK} .",
                                        "prompt": f"sub agent prompt {MARK}"}})
            output = u["output"] if p == parts - 1 else max(1, u["output"] // (parts - p + 1))
            usage_obj: dict[str, Any] = {
                "input_tokens": u["input_tokens"], "output_tokens": output,
                "cache_read_input_tokens": u["cache_read_input_tokens"],
                "service_tier": "standard",
            }
            if flat_cache:
                usage_obj["cache_creation_input_tokens"] = u["c5"]
            else:
                usage_obj["cache_creation_input_tokens"] = u["c5"] + u["c1"]
                usage_obj["cache_creation"] = {"ephemeral_5m_input_tokens": u["c5"],
                                               "ephemeral_1h_input_tokens": u["c1"]}
            msg: dict[str, Any] = {"model": model, "type": "message", "role": "assistant",
                                   "content": content, "usage": usage_obj}
            if not no_id:
                msg["id"] = mid
            d["message"] = msg
            out.append(json.dumps(d, ensure_ascii=False))
        return out

    def title(self, text: str) -> str:
        return json.dumps({"type": "ai-title", "sessionId": self.sid, "aiTitle": text},
                          ensure_ascii=False)

    def turn(self, ts: datetime, model: str, **kw: Any) -> None:
        self.lines.append(self.user(ts))
        self.lines.extend(self.response(ts + timedelta(seconds=2), model, **kw))
        if kw.get("tools"):
            self.lines.append(self.tool_result(ts + timedelta(seconds=3)))

    def sidechain(self, agent: str, ts: datetime, n: int, model: str) -> None:
        lines = [self.user(ts, sidechain=True)]
        for i in range(n):
            lines.extend(self.response(ts + timedelta(seconds=5 * i + 1), model, sidechain=True,
                                       tools=("Read",)))
        self.sub_files[agent] = lines


@dataclass
class Generated:
    root: Path
    employees: list[Employee]
    now: datetime
    live_file: Path  # bob's live session file (latency / catch-up writes go here)
    live_session: str
    partial_file: Path  # file whose last line is incomplete
    partial_completion: str  # bytes that complete it (incl. newline)
    titled_sessions: dict[str, str]  # session id -> final title (for reporting)


def _write(path: Path, lines: list[str], *, trailing_newline: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + ("\n" if trailing_newline else "")
    path.write_text(text, encoding="utf-8", newline="")


def generate(root: Path, now: datetime | None = None) -> Generated:
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    # Local (UTC+7) midnight of "today"-ish: 17:00Z. Put events around it on purpose.
    midnight_z = now.replace(hour=17, minute=0, second=0)
    if midnight_z > now - timedelta(hours=3):  # sessions run up to ~100 min past it
        midnight_z -= timedelta(days=1)
    emps: list[Employee] = []
    titles: dict[str, str] = {}
    live_file = partial_file = Path()
    live_session = ""
    partial_completion = ""
    for proto in EMPLOYEES:
        emp = Employee(proto.user, proto.email, proto.team, proto.seed,
                       root / "homes" / proto.user)
        rng = random.Random(1000 + emp.seed)
        emp.home.mkdir(parents=True, exist_ok=True)
        claude_json: dict[str, Any] = {
            "numStartups": 12, "userID": f"{rng.getrandbits(64):016x}",
            "projects": {f"C:/Users/{emp.user}/{MARK}/webapp": {
                "history": [{"display": f"old prompt {MARK}"}]}},
        }
        if emp.email:
            claude_json["oauthAccount"] = {
                "accountUuid": f"acc-{emp.user}-{rng.getrandbits(32):08x}",
                "emailAddress": emp.email, "organizationName": f"Org {MARK}",
                "displayName": emp.user.title()}
        (emp.home / ".claude.json").write_text(json.dumps(claude_json), encoding="utf-8")
        projects = ["webapp", "api-server", "dữ-liệu"]
        for pi, proj in enumerate(projects[: 2 + (emp.seed % 2)]):
            cwd = f"C:\\Users\\{emp.user}\\{MARK}-work\\{proj}"
            pdir = emp.projects_dir / encode_cwd(cwd)
            for si in range(2 + (emp.seed + pi) % 2):
                sid = f"{rng.getrandbits(128):032x}"
                sid = f"{sid[:8]}-{sid[8:12]}-{sid[12:16]}-{sid[16:20]}-{sid[20:]}"
                sb = SessionBuilder(emp, rng, cwd, sid)
                # spread sessions over the last 6 days, some straddling local midnight
                start = midnight_z - timedelta(days=(si + pi + emp.seed) % 6,
                                               minutes=40 if (si + emp.seed) % 2 else 0)
                for ti in range(rng.randint(4, 9)):
                    ts = start + timedelta(minutes=9 * ti, seconds=rng.randint(0, 59))
                    model = KNOWN_MODELS[(ti + si + emp.seed) % len(KNOWN_MODELS)]
                    sb.turn(ts, model, parts=rng.randint(1, 3),
                            tools=("Read", "Bash") if ti % 3 == 1 else (),
                            flat_cache=(ti % 4 == 3))
                    if ti == 0:
                        t = f"{emp.user} {proj} phiên {si + 1}"
                        sb.lines.append(sb.title(t))
                        titles[sid] = t
                    if ti == 2 and si == 0:  # renamed later: latest title wins
                        t = f"{emp.user} {proj} đổi tên ✓"
                        sb.lines.append(sb.title(t))
                        titles[sid] = t
                if si == 0:
                    sb.sidechain(f"a{rng.getrandbits(32):08x}", start + timedelta(minutes=3),
                                 rng.randint(2, 4), "claude-haiku-4-5-20251001")
                # per-employee edge cases
                if emp.user == "bob" and pi == 0 and si == 0:
                    sb.turn(start + timedelta(minutes=95), UNKNOWN_MODEL, parts=2)
                if emp.user == "carol" and pi == 0 and si == 0:
                    sb.lines.extend(sb.response(start + timedelta(minutes=97), "<synthetic>",
                                                parts=1, usage={"input_tokens": 0, "output": 0,
                                                                "cache_read_input_tokens": 0,
                                                                "c5": 0, "c1": 0}))
                if emp.user == "dan" and pi == 0 and si == 0:
                    sb.lines.extend(sb.response(start + timedelta(minutes=98),
                                                "claude-sonnet-4-6", parts=1, no_id=True))
                if si == 0 and pi == 0:  # junk the parsers must skip
                    sb.lines += ["not json {", "[1, 2, 3]", json.dumps(
                        {"type": "summary", "summary": f"summary {MARK}", "leafUuid": "x"})]
                    bad = json.loads(sb.response(start, "claude-sonnet-4-6", parts=1)[0])
                    bad["timestamp"] = "yesterday"
                    sb.lines.append(json.dumps(bad))
                _write(pdir / f"{sid}.jsonl", sb.lines)
                for agent, lines in sb.sub_files.items():
                    _write(pdir / sid / "subagents" / f"agent-{agent}.jsonl", lines)
                # alice: a "resumed" session file that repeats 3 lines of this session
                if emp.user == "alice" and pi == 0 and si == 0:
                    sid2 = f"{rng.getrandbits(128):032x}"
                    sid2 = f"{sid2[:8]}-{sid2[8:12]}-{sid2[12:16]}-{sid2[16:20]}-{sid2[20:]}"
                    copied = [ln for ln in sb.lines if '"type": "assistant"' in ln][:3]
                    sb2 = SessionBuilder(emp, rng, cwd, sid2)
                    sb2.lines = list(copied)
                    sb2.turn(start + timedelta(hours=2), "claude-opus-4-7", parts=2)
                    _write(pdir / f"{sid2}.jsonl", sb2.lines)
        if emp.user == "bob":  # live session (appended to during the run)
            cwd = f"C:\\Users\\bob\\{MARK}-work\\live-proj"
            sid = "11111111-2222-4333-8444-555555555555"
            sb = SessionBuilder(emp, rng, cwd, sid)
            sb.turn(now - timedelta(minutes=30), "claude-sonnet-4-6", parts=2)
            sb.lines.append(sb.title("bob live session"))
            titles[sid] = "bob live session"
            live_file = emp.projects_dir / encode_cwd(cwd) / f"{sid}.jsonl"
            _write(live_file, sb.lines)
            live_session = sid
        if emp.user == "carol":  # file whose last line is still being written
            cwd = f"C:\\Users\\carol\\{MARK}-work\\webapp"
            sid = "99999999-8888-4777-8666-555555555555"
            sb = SessionBuilder(emp, rng, cwd, sid)
            sb.turn(now - timedelta(minutes=50), "claude-opus-4-7", parts=1)
            last = sb.response(now - timedelta(minutes=45), "claude-opus-4-7", parts=1,
                               msg_id="msg_carol_partial")[0]
            cut = len(last) // 2
            partial_file = emp.projects_dir / encode_cwd(cwd) / f"{sid}.jsonl"
            _write(partial_file, [*sb.lines, last[:cut]], trailing_newline=False)
            partial_completion = last[cut:] + "\n"
        emps.append(emp)
    return Generated(root, emps, now, live_file, live_session, partial_file, partial_completion,
                     titles)


def live_line_pair(emp_user: str, session_id: str, cwd: str, ts: datetime, msg_id: str,
                   model: str = "claude-sonnet-4-6", output: int = 321) -> str:
    """Two complete lines (user + assistant) for a live append; returns text with newlines."""
    emp = Employee(emp_user, None, "", 0)
    sb = SessionBuilder(emp, random.Random(hash(msg_id) & 0xFFFF), cwd, session_id)
    lines = [sb.user(ts), *sb.response(ts + timedelta(seconds=1), model, parts=1, msg_id=msg_id,
                                       usage={"input_tokens": 7, "output": output,
                                              "cache_read_input_tokens": 1000, "c5": 50,
                                              "c1": 0})]
    return "\n".join(lines) + "\n"
