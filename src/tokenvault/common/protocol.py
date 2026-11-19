"""Wire protocol v1: JSON frames with explicit field validation. No I/O."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any

from tokenvault.common.models import UsageEvent

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 262144
MAX_BATCH = 200
MAX_TOKENS = 10**12


class ProtocolError(ValueError):
    """Malformed or invalid protocol data."""


@dataclass(frozen=True)
class Hello:
    device_id: str
    enroll_secret: str | None
    device_key: str | None
    team: str
    version: int


@dataclass(frozen=True)
class Welcome:
    device_key: str


@dataclass(frozen=True)
class Batch:
    events: Sequence[UsageEvent]


@dataclass(frozen=True)
class Receipt:
    message_ids: list[str]


@dataclass(frozen=True)
class Reject:
    reason: str


Msg = Hello | Welcome | Batch | Receipt | Reject

_STR_FIELDS = (
    "uuid",
    "session_id",
    "timestamp",
    "project",
    "windows_user",
    "hostname",
    "team",
    "device_id",
)
_OPT_STR_FIELDS = (
    "message_id",
    "model",
    "reasoning_effort",
    "session_title",
    "claude_account_email",
    "account_uuid",
)
_INT_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_create_5m_tokens",
    "cache_create_1h_tokens",
)
_EVENT_KEYS = frozenset(f.name for f in fields(UsageEvent))


def _str(d: Mapping[str, Any], k: str) -> str:
    v = d[k]
    if not isinstance(v, str):
        raise ProtocolError(f"field {k!r} must be a string")
    return v


def _opt_str(d: Mapping[str, Any], k: str) -> str | None:
    v = d[k]
    if v is not None and not isinstance(v, str):
        raise ProtocolError(f"field {k!r} must be a string or null")
    return v


def _check_keys(d: Mapping[str, Any], expected: frozenset[str], what: str) -> None:
    keys = set(d)
    if keys != expected:
        unknown = sorted(keys - expected)
        missing = sorted(expected - keys)
        raise ProtocolError(f"{what}: unknown fields {unknown}, missing fields {missing}")


def event_to_dict(ev: UsageEvent) -> dict[str, Any]:
    d: dict[str, Any] = {f.name: getattr(ev, f.name) for f in fields(UsageEvent)}
    d["tool_names"] = list(ev.tool_names)
    return d


def event_from_dict(d: object) -> UsageEvent:
    if not isinstance(d, dict):
        raise ProtocolError("event must be an object")
    _check_keys(d, _EVENT_KEYS, "event")
    vals: dict[str, Any] = {}
    for k in _STR_FIELDS:
        vals[k] = _str(d, k)
    for k in _OPT_STR_FIELDS:
        vals[k] = _opt_str(d, k)
    for k in _INT_FIELDS:
        v = d[k]
        if type(v) is not int or v < 0 or v > MAX_TOKENS:
            raise ProtocolError(f"field {k!r} must be an int in [0, {MAX_TOKENS}]")
        vals[k] = v
    if type(d["is_sidechain"]) is not bool:
        raise ProtocolError("field 'is_sidechain' must be a bool")
    vals["is_sidechain"] = d["is_sidechain"]
    tn = d["tool_names"]
    if not isinstance(tn, list) or not all(isinstance(t, str) for t in tn):
        raise ProtocolError("field 'tool_names' must be a list of strings")
    vals["tool_names"] = tuple(tn)
    return UsageEvent(**vals)


def _frame(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ProtocolError("frame exceeds MAX_FRAME_BYTES")
    return raw


def _validate_hello(m: Hello) -> None:
    if (m.enroll_secret is None) == (m.device_key is None):
        raise ProtocolError("hello needs exactly one of enroll_secret/device_key")


def encode_msg(msg: Msg) -> str:
    body: dict[str, Any]
    if isinstance(msg, Hello):
        _validate_hello(msg)
        body = {
            "type": "hello",
            "device_id": msg.device_id,
            "enroll_secret": msg.enroll_secret,
            "device_key": msg.device_key,
            "team": msg.team,
            "version": msg.version,
        }
    elif isinstance(msg, Welcome):
        body = {"type": "welcome", "device_key": msg.device_key}
    elif isinstance(msg, Batch):
        if len(msg.events) > MAX_BATCH:
            raise ProtocolError("batch exceeds MAX_BATCH")
        body = {"type": "batch", "events": [event_to_dict(e) for e in msg.events]}
    elif isinstance(msg, Receipt):
        body = {"type": "receipt", "message_ids": list(msg.message_ids)}
    elif isinstance(msg, Reject):
        body = {"type": "reject", "reason": msg.reason}
    else:
        raise ProtocolError(f"unsupported message {type(msg).__name__}")
    return _frame({"v": PROTOCOL_VERSION, **body})


def decode_msg(raw: str) -> Msg:
    if not isinstance(raw, str):
        raise ProtocolError("frame must be a string")
    if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ProtocolError("frame exceeds MAX_FRAME_BYTES")
    try:
        d = json.loads(raw)
    except (ValueError, RecursionError) as e:
        raise ProtocolError(f"invalid JSON: {e}") from e
    if not isinstance(d, dict):
        raise ProtocolError("frame must be an object")
    if type(d.get("v")) is not int or d["v"] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    t = d.get("type")
    if t == "hello":
        _check_keys(
            d,
            frozenset({"v", "type", "device_id", "enroll_secret", "device_key", "team", "version"}),
            "hello",
        )
        if type(d["version"]) is not int:
            raise ProtocolError("hello.version must be an int")
        h = Hello(
            _str(d, "device_id"),
            _opt_str(d, "enroll_secret"),
            _opt_str(d, "device_key"),
            _str(d, "team"),
            d["version"],
        )
        _validate_hello(h)
        return h
    if t == "welcome":
        _check_keys(d, frozenset({"v", "type", "device_key"}), "welcome")
        return Welcome(_str(d, "device_key"))
    if t == "batch":
        _check_keys(d, frozenset({"v", "type", "events"}), "batch")
        evs = d["events"]
        if not isinstance(evs, list):
            raise ProtocolError("batch.events must be a list")
        if len(evs) > MAX_BATCH:
            raise ProtocolError("batch exceeds MAX_BATCH")
        return Batch([event_from_dict(e) for e in evs])
    if t == "receipt":
        _check_keys(d, frozenset({"v", "type", "message_ids"}), "receipt")
        ids = d["message_ids"]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ProtocolError("receipt.message_ids must be a list of strings")
        return Receipt(ids)
    if t == "reject":
        _check_keys(d, frozenset({"v", "type", "reason"}), "reject")
        return Reject(_str(d, "reason"))
    raise ProtocolError(f"unknown message type {t!r}")


def encode_batch(events: Sequence[UsageEvent]) -> str:
    return encode_msg(Batch(list(events)))


def decode_batch(raw: str) -> list[UsageEvent]:
    m = decode_msg(raw)
    if not isinstance(m, Batch):
        raise ProtocolError("expected a batch message")
    return list(m.events)
