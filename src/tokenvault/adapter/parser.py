"""Claude Code transcript line parser.

Extracts only token counts and metadata. Message text, tool inputs and full
paths are never read into any output structure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

UNKNOWN_PROJECT = "(unknown)"


@dataclass(frozen=True)
class AssistantUsage:
    uuid: str
    session_id: str
    message_id: str
    timestamp: str
    model: str | None
    reasoning_effort: str | None
    is_sidechain: bool
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_5m_tokens: int
    cache_create_1h_tokens: int
    project: str
    tool_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class TitleUpdate:
    session_id: str
    title: str


ParsedLine = AssistantUsage | TitleUpdate


@dataclass
class SessionContext:
    """Latest ai-title per session id."""

    titles: dict[str, str] = field(default_factory=dict)


def project_name(cwd: str) -> str:
    parts = [p for p in cwd.replace("\\", "/").split("/") if p]
    if not parts:
        return UNKNOWN_PROJECT
    last = parts[-1]
    if len(parts) == 1 and last.endswith(":"):
        return UNKNOWN_PROJECT
    return last


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_line(line: bytes, ctx: SessionContext) -> ParsedLine | None:
    try:
        obj = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(obj, dict):
        return None
    kind = obj.get("type")
    if kind == "ai-title":
        sid, title = _str(obj.get("sessionId")), _str(obj.get("aiTitle"))
        if sid is None or title is None:
            return None
        ctx.titles[sid] = title
        return TitleUpdate(sid, title)
    if kind != "assistant":
        return None
    return _parse_assistant(obj)


def _parse_assistant(obj: dict[str, Any]) -> AssistantUsage | None:
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    uuid = _str(obj.get("uuid"))
    session_id = _str(obj.get("sessionId"))
    if uuid is None or session_id is None:
        return None
    cc = usage.get("cache_creation")
    if isinstance(cc, dict):
        c5 = _int(cc.get("ephemeral_5m_input_tokens"))
        c1 = _int(cc.get("ephemeral_1h_input_tokens"))
    else:
        c5 = _int(usage.get("cache_creation_input_tokens"))
        c1 = 0
    tools: list[str] = []
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = _str(block.get("name"))
                if name and name not in tools:
                    tools.append(name)
    cwd = obj.get("cwd")
    return AssistantUsage(
        uuid=uuid,
        session_id=session_id,
        message_id=_str(msg.get("id")) or uuid,
        timestamp=_str(obj.get("timestamp")) or "",
        model=_str(msg.get("model")),
        reasoning_effort=_str(obj.get("effort")),
        is_sidechain=obj.get("isSidechain") is True,
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
        cache_create_5m_tokens=c5,
        cache_create_1h_tokens=c1,
        project=project_name(cwd) if isinstance(cwd, str) else UNKNOWN_PROJECT,
        tool_names=tuple(tools),
    )


class MessageDeduper:
    """Merges repeated lines of one API response (same message_id)."""

    def __init__(self) -> None:
        self._by_id: dict[str, AssistantUsage] = {}

    def add(self, usage: AssistantUsage) -> AssistantUsage | None:
        """Buffer a line; merged rows are emitted by flush(). Always None."""
        prev = self._by_id.get(usage.message_id)
        if prev is None:
            self._by_id[usage.message_id] = usage
            return None
        tools = prev.tool_names + tuple(t for t in usage.tool_names if t not in prev.tool_names)
        self._by_id[usage.message_id] = replace(
            prev,
            input_tokens=max(prev.input_tokens, usage.input_tokens),
            output_tokens=max(prev.output_tokens, usage.output_tokens),
            cache_read_tokens=max(prev.cache_read_tokens, usage.cache_read_tokens),
            cache_create_5m_tokens=max(prev.cache_create_5m_tokens, usage.cache_create_5m_tokens),
            cache_create_1h_tokens=max(prev.cache_create_1h_tokens, usage.cache_create_1h_tokens),
            tool_names=tools,
        )
        return None

    def flush(self) -> list[AssistantUsage]:
        out = list(self._by_id.values())
        self._by_id.clear()
        return out
