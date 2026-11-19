from __future__ import annotations

import json

import pytest

from tokenvault.common.discovery_wire import (
    MAX_DATAGRAM,
    Answer,
    Query,
    build_answer,
    build_query,
    parse_answer,
    parse_query,
)

FP = "ab" * 32
NONCE = "0123456789abcdef"


def test_query_roundtrip() -> None:
    data = build_query("mgr-1", NONCE)
    assert len(data) <= MAX_DATAGRAM
    assert parse_query(data) == Query("mgr-1", NONCE)


def test_answer_roundtrip_and_small_amplification() -> None:
    q = build_query("m", NONCE)
    a = build_answer("m", 47800, FP, NONCE)
    assert parse_answer(a, "m", NONCE) == Answer("m", 47800, FP, NONCE)
    assert len(a) <= 3 * len(q)


def _ans(**kw: object) -> bytes:
    d: dict[str, object] = {
        "v": 1, "a": "tokenvault", "manager_id": "m", "port": 1, "fingerprint": FP, "nonce": NONCE,
    }
    d.update(kw)
    return json.dumps(d).encode()


@pytest.mark.parametrize(
    "kw",
    [
        {"v": 2},
        {"v": True},
        {"a": "x"},
        {"port": 0},
        {"port": 65536},
        {"port": "80"},
        {"port": True},
        {"fingerprint": "AB" * 32},
        {"fingerprint": "ab" * 31},
        {"fingerprint": 5},
        {"nonce": "ffffffffffffffff"},
        {"nonce": "xyz"},
        {"manager_id": "other"},
        {"manager_id": ""},
    ],
)
def test_parse_answer_rejects(kw: dict[str, object]) -> None:
    assert parse_answer(_ans(**kw), "m", NONCE) is None


def test_parse_answer_rejects_extra_key_garbage_and_oversize() -> None:
    assert parse_answer(_ans(extra=1), "m", NONCE) is None
    assert parse_answer(b"\xff\xfe", "m", NONCE) is None
    assert parse_answer(b"[]", "m", NONCE) is None
    assert parse_answer(b"", "m", NONCE) is None
    assert parse_answer(_ans(pad="x" * 600), "m", NONCE) is None


def test_parse_query_rejects() -> None:
    assert parse_query(b"not json") is None
    assert parse_query(b'{"v":1,"q":"who-is-tokenvault","manager_id":"m","nonce":"zz"}') is None
    assert parse_query(b"x" * 513) is None


def test_build_validates() -> None:
    with pytest.raises(ValueError):
        build_answer("m", 0, FP, NONCE)
    with pytest.raises(ValueError):
        build_query("m" * 65, NONCE)
