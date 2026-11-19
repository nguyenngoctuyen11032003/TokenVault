import dataclasses
import json
from pathlib import Path

from tokenvault.adapter.parser import (
    AssistantUsage,
    MessageDeduper,
    SessionContext,
    TitleUpdate,
    parse_line,
    project_name,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "transcript_basic.jsonl"


def _lines() -> list[bytes]:
    return FIXTURE.read_bytes().splitlines()


def _parse_all(ctx: SessionContext | None = None):
    ctx = ctx or SessionContext()
    return [parse_line(line, ctx) for line in _lines()]


def _usages() -> list[AssistantUsage]:
    return [p for p in _parse_all() if isinstance(p, AssistantUsage)]


def test_assistant_line_extracts_all_usage_fields():
    first = _usages()[0]
    assert first.uuid == "a-1"
    assert first.session_id == "s-1"
    assert first.message_id == "msg_A"
    assert first.timestamp == "2026-01-01T10:00:01.000Z"
    assert first.model == "claude-opus-4-7"
    assert first.reasoning_effort == "high"
    assert first.is_sidechain is False
    assert first.input_tokens == 10
    assert first.output_tokens == 20
    assert first.cache_read_tokens == 300
    assert first.cache_create_5m_tokens == 40
    assert first.cache_create_1h_tokens == 50
    assert first.project == "myproject"


def test_legacy_cache_creation_total_is_5m():
    u = _usages()[3]
    assert u.cache_create_5m_tokens == 7
    assert u.cache_create_1h_tokens == 0


def test_missing_message_id_falls_back_to_uuid():
    u = _usages()[4]
    assert u.message_id == "a-5"
    assert u.reasoning_effort is None
    assert u.project == "(unknown)"


def test_repeated_message_id_yields_one_row():
    d = MessageDeduper()
    for u in _usages():
        assert d.add(u) is None
    out = d.flush()
    assert [u.message_id for u in out] == ["msg_A", "msg_B", "a-5"]
    assert d.flush() == []


def test_repeated_message_id_keeps_larger_usage():
    d = MessageDeduper()
    for u in _usages()[:3]:
        d.add(u)
    (merged,) = d.flush()
    assert merged.output_tokens == 25
    assert merged.input_tokens == 10
    assert merged.uuid == "a-1"


def test_tool_names_union_in_first_seen_order():
    d = MessageDeduper()
    for u in _usages()[:3]:
        d.add(u)
    (merged,) = d.flush()
    assert merged.tool_names == ("Bash", "Read")


def test_tool_names_only_never_inputs():
    blob = repr([dataclasses.asdict(u) for u in _usages()])
    assert "SECRET_TOOL_INPUT" not in blob
    assert "rm -rf" not in blob


def test_project_name_last_folder_windows_and_posix():
    assert project_name(r"C:\a\b") == "b"
    assert project_name("C:\\a\\b\\") == "b"
    assert project_name("/a/b/") == "b"
    assert project_name("/a/b") == "b"
    assert project_name("") == "(unknown)"
    assert project_name("/") == "(unknown)"


def test_ai_title_latest_wins():
    ctx = SessionContext()
    parsed = _parse_all(ctx)
    titles = [p for p in parsed if isinstance(p, TitleUpdate)]
    assert [t.title for t in titles] == ["First title", "Latest title"]
    assert ctx.titles["s-1"] == "Latest title"


def test_sidechain_flag():
    us = _usages()
    assert us[0].is_sidechain is False
    assert us[3].is_sidechain is True


def test_non_assistant_ignored():
    parsed = _parse_all()
    assert parsed[0] is None  # queue-operation
    assert parsed[1] is None  # user


def test_malformed_json_returns_none():
    ctx = SessionContext()
    assert parse_line(b"{not json", ctx) is None
    assert parse_line(b"", ctx) is None
    assert parse_line(b"[1,2]", ctx) is None
    assert parse_line(b'{"type":"assistant","message":"x"}', ctx) is None
    assert parse_line(b"\xff\xfe", ctx) is None


def test_non_numeric_tokens_treated_as_zero():
    line = json.dumps(
        {
            "type": "assistant",
            "uuid": "x",
            "sessionId": "s",
            "timestamp": "t",
            "message": {"id": "m", "usage": {"input_tokens": "bad", "output_tokens": -3}},
        }
    ).encode()
    u = parse_line(line, SessionContext())
    assert isinstance(u, AssistantUsage)
    assert u.input_tokens == 0 and u.output_tokens == 0


def test_no_prompt_or_reply_text_in_event():
    blob = repr([dataclasses.asdict(u) for u in _usages()])
    for secret in ("SECRET_PROMPT_TEXT", "SECRET_REPLY_TEXT", "SECRET_DIR"):
        assert secret not in blob
