import dataclasses
import json

import pytest

from tokenvault.common.models import UsageEvent
from tokenvault.common.protocol import (
    MAX_BATCH,
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    Batch,
    Hello,
    ProtocolError,
    Receipt,
    Reject,
    Welcome,
    decode_batch,
    decode_msg,
    encode_batch,
    encode_msg,
)


def make_event(**over: object) -> UsageEvent:
    base: dict[str, object] = dict(
        uuid="u-1",
        session_id="s-1",
        message_id="m-1",
        timestamp="2026-11-19T10:00:00.000Z",
        model="claude-opus-4",
        reasoning_effort=None,
        is_sidechain=False,
        input_tokens=1,
        output_tokens=2,
        cache_read_tokens=3,
        cache_create_5m_tokens=4,
        cache_create_1h_tokens=5,
        project="proj",
        session_title="Sửa lỗi đăng nhập 🚀",
        tool_names=("Bash", "Read"),
        claude_account_email=None,
        account_uuid=None,
        windows_user="Nguyễn",
        hostname="PC1",
        team="ics",
        device_id="dev-1",
    )
    base.update(over)
    return UsageEvent(**base)  # type: ignore[arg-type]


def test_roundtrip_preserves_unicode() -> None:
    ev = make_event()
    out = decode_batch(encode_batch([ev]))
    assert out == [ev]
    assert out[0].session_title == "Sửa lỗi đăng nhập 🚀"
    assert out[0].windows_user == "Nguyễn"
    assert isinstance(out[0].tool_names, tuple)


@pytest.mark.parametrize("bad", [-1, 10**12 + 1, True, 1.5, "3"])
def test_rejects_negative_or_huge_tokens(bad: object) -> None:
    raw = encode_batch([make_event()])
    d = json.loads(raw)
    d["events"][0]["input_tokens"] = bad
    with pytest.raises(ProtocolError):
        decode_batch(json.dumps(d))


def test_accepts_max_tokens() -> None:
    ev = make_event(input_tokens=10**12)
    assert decode_batch(encode_batch([ev])) == [ev]


def test_rejects_unknown_fields() -> None:
    d = json.loads(encode_batch([make_event()]))
    d["events"][0]["extra"] = 1
    with pytest.raises(ProtocolError):
        decode_batch(json.dumps(d))


def test_rejects_missing_fields() -> None:
    d = json.loads(encode_batch([make_event()]))
    del d["events"][0]["team"]
    with pytest.raises(ProtocolError):
        decode_batch(json.dumps(d))


def test_rejects_oversized_frame() -> None:
    with pytest.raises(ProtocolError):
        decode_msg(" " * (MAX_FRAME_BYTES + 1))
    big = make_event(session_title="x" * MAX_FRAME_BYTES)
    with pytest.raises(ProtocolError):
        encode_batch([big])


def test_rejects_batch_over_max() -> None:
    with pytest.raises(ProtocolError):
        encode_batch([make_event()] * (MAX_BATCH + 1))
    d = json.loads(encode_batch([make_event()]))
    d["events"] = d["events"] * (MAX_BATCH + 1)
    with pytest.raises(ProtocolError):
        decode_batch(json.dumps(d))


def test_rejects_wrong_version() -> None:
    d = json.loads(encode_batch([make_event()]))
    d["v"] = 2
    with pytest.raises(ProtocolError):
        decode_batch(json.dumps(d))
    assert PROTOCOL_VERSION == 1


def test_rejects_garbage() -> None:
    for raw in ["", "not json", "[]", '{"v":1}', '{"v":1,"type":"nope"}']:
        with pytest.raises(ProtocolError):
            decode_msg(raw)


def test_messages_roundtrip() -> None:
    msgs = [
        Hello("d", "secret", None, "ics", 1),
        Hello("d", None, "key", "ics", 1),
        Welcome("key"),
        Batch([make_event(), make_event(uuid="u-2")]),
        Receipt(["a", "b"]),
        Reject("bad ✗"),
    ]
    for m in msgs:
        assert decode_msg(encode_msg(m)) == m


def test_hello_requires_exactly_one_credential() -> None:
    with pytest.raises(ProtocolError):
        encode_msg(Hello("d", None, None, "ics", 1))
    with pytest.raises(ProtocolError):
        encode_msg(Hello("d", "s", "k", "ics", 1))
    with pytest.raises(ProtocolError):
        decode_msg(
            '{"v":1,"type":"hello","device_id":"d","enroll_secret":"s",'
            '"device_key":"k","team":"t","version":1}'
        )


def test_event_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_event().uuid = "x"  # type: ignore[misc]
