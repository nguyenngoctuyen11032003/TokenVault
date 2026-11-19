"""LAN discovery wire format (pure, no I/O).

Query  (Adapter -> broadcast):  {"v":1,"q":"who-is-tokenvault","manager_id":..,"nonce":<16 hex>}
Answer (Manager -> Adapter):    {"v":1,"a":"tokenvault","manager_id":..,"port":..,
                                 "fingerprint":<64 lowercase hex>,"nonce":<echo>}

Everything is strict: unknown shapes, wrong types, bad lengths or extra keys -> ``None``.
Datagrams are at most ``MAX_DATAGRAM`` bytes. Reflection amplification: a minimal
query is ~85 bytes, an answer ~195 bytes (factor < 2.5), and the responder
additionally rate-limits answers per source IP.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

DISCOVERY_PORT = 47801
MAX_DATAGRAM = 512
VERSION = 1
QUERY_TAG = "who-is-tokenvault"
ANSWER_TAG = "tokenvault"

_MANAGER_ID = re.compile(r"[\x21-\x7e]{1,64}\Z")  # printable ASCII, no spaces
_NONCE = re.compile(r"[0-9a-f]{16}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Query:
    manager_id: str
    nonce: str


@dataclass(frozen=True)
class Answer:
    manager_id: str
    port: int
    fingerprint: str
    nonce: str


def _is_str(v: Any, rx: re.Pattern[str]) -> bool:
    return isinstance(v, str) and rx.match(v) is not None


def _encode(obj: dict[str, Any]) -> bytes:
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(data) > MAX_DATAGRAM:
        raise ValueError("discovery datagram too large")
    return data


def _decode(data: bytes) -> dict[str, Any] | None:
    if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= MAX_DATAGRAM:
        return None
    try:
        obj = json.loads(bytes(data).decode("ascii"))
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        return None
    return obj if isinstance(obj, dict) else None


def build_query(manager_id: str, nonce: str) -> bytes:
    if not _is_str(manager_id, _MANAGER_ID) or not _is_str(nonce, _NONCE):
        raise ValueError("invalid manager_id or nonce")
    return _encode({"v": VERSION, "q": QUERY_TAG, "manager_id": manager_id, "nonce": nonce})


def parse_query(data: bytes) -> Query | None:
    obj = _decode(data)
    if obj is None or set(obj) != {"v", "q", "manager_id", "nonce"}:
        return None
    v = obj["v"]
    if type(v) is not int or v != VERSION or obj["q"] != QUERY_TAG:
        return None
    if not _is_str(obj["manager_id"], _MANAGER_ID) or not _is_str(obj["nonce"], _NONCE):
        return None
    return Query(obj["manager_id"], obj["nonce"])


def build_answer(manager_id: str, port: int, fingerprint: str, nonce: str) -> bytes:
    if (
        not _is_str(manager_id, _MANAGER_ID)
        or not _is_str(nonce, _NONCE)
        or not _is_str(fingerprint, _FINGERPRINT)
        or type(port) is not int
        or not 1 <= port <= 65535
    ):
        raise ValueError("invalid answer field")
    return _encode(
        {
            "v": VERSION,
            "a": ANSWER_TAG,
            "manager_id": manager_id,
            "port": port,
            "fingerprint": fingerprint,
            "nonce": nonce,
        }
    )


def parse_answer(data: bytes, expected_manager_id: str, expected_nonce: str) -> Answer | None:
    obj = _decode(data)
    if obj is None or set(obj) != {"v", "a", "manager_id", "port", "fingerprint", "nonce"}:
        return None
    v, port = obj["v"], obj["port"]
    if type(v) is not int or v != VERSION or obj["a"] != ANSWER_TAG:
        return None
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    if not _is_str(obj["manager_id"], _MANAGER_ID) or not _is_str(obj["nonce"], _NONCE):
        return None
    if not _is_str(obj["fingerprint"], _FINGERPRINT):
        return None
    if obj["manager_id"] != expected_manager_id or obj["nonce"] != expected_nonce:
        return None
    return Answer(obj["manager_id"], port, obj["fingerprint"], obj["nonce"])
