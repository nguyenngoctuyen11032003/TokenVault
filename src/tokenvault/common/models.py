"""Shared data models. Pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UsageEvent:
    uuid: str
    session_id: str
    message_id: str | None
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
    session_title: str | None
    tool_names: tuple[str, ...]
    claude_account_email: str | None
    account_uuid: str | None
    windows_user: str
    hostname: str
    team: str
    device_id: str
