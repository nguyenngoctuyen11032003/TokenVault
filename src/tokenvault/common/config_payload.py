"""Config payload appended to the employee installer exe.

Layout appended after the (unchanged) template bytes::

    <UTF-8 JSON, sorted keys> | CRC32(payload) 4B BE | payload length 8B BE | b"TVLTCFG1"

SECURITY: the payload contains the enrollment secret. An installer carrying it
must be handled like a credential. It is only usable against the Manager whose
certificate fingerprint it pins, and the package is revocable on the Manager.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAGIC = b"TVLTCFG1"
FOOTER_SIZE = 4 + 8 + len(MAGIC)
MAX_PAYLOAD = 64 * 1024
CHUNK = 1 << 20
CURRENT_VERSION = 1

_LABEL = r"[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
_HOST_RE = re.compile(rf"{_LABEL}(\.{_LABEL})*")
_NUMERIC_RE = re.compile(r"[0-9.]+")
_FP_RE = re.compile(r"[0-9a-f]{64}")
_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{32,}")
_ID_RE = re.compile(r"[\w.@-]{1,64}")
MAX_HOSTS = 8


def valid_host(h: object) -> bool:
    """Plausible IPv4 / IPv6 (no zone id) / hostname; nothing else."""
    if not isinstance(h, str) or not h or len(h) > 253:
        return False
    if ":" in h or _NUMERIC_RE.fullmatch(h):
        # IP forms must parse exactly; a dotted-numeric string is never a hostname.
        if "%" in h or h != h.strip():
            return False
        try:
            ipaddress.ip_address(h)
        except ValueError:
            return False
        return True
    return _HOST_RE.fullmatch(h) is not None


def _valid_team(t: str) -> bool:
    return (
        1 <= len(t) <= 40
        and bool(t.strip())
        and not any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in t)
    )


@dataclass(frozen=True)
class PayloadConfig:
    manager_id: str
    hosts: list[str]
    port: int
    fingerprint: str
    secret: str = field(repr=False)
    team: str
    version: int = CURRENT_VERSION

    def validate(self) -> None:
        """Raise ValueError when any field is unusable."""
        if (
            not isinstance(self.hosts, list)
            or not self.hosts
            or len(self.hosts) > MAX_HOSTS
            or not all(valid_host(h) for h in self.hosts)
            or len({h.lower() for h in self.hosts}) != len(self.hosts)
        ):
            raise ValueError("hosts must be 1..8 distinct valid IP addresses / host names")
        if (
            not isinstance(self.port, int)
            or isinstance(self.port, bool)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("port must be 1..65535")
        if not isinstance(self.fingerprint, str) or not _FP_RE.fullmatch(self.fingerprint):
            raise ValueError("fingerprint must be 64 lowercase hex characters")
        if not isinstance(self.secret, str) or not _SECRET_RE.fullmatch(self.secret):
            raise ValueError("secret must be >= 32 characters of [A-Za-z0-9_-]")
        if not isinstance(self.team, str) or not _valid_team(self.team):
            raise ValueError("team must be 1..40 characters without control chars or slashes")
        if not isinstance(self.manager_id, str) or not _ID_RE.fullmatch(self.manager_id):
            raise ValueError("manager_id must be 1..64 safe characters")
        if type(self.version) is not int or self.version != CURRENT_VERSION:
            raise ValueError(f"unsupported payload version {self.version}")


def _to_json(cfg: PayloadConfig) -> bytes:
    d: dict[str, Any] = {
        "manager_id": cfg.manager_id, "hosts": list(cfg.hosts), "port": cfg.port,
        "fingerprint": cfg.fingerprint, "secret": cfg.secret, "team": cfg.team,
        "version": cfg.version,
    }
    return json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _from_json(data: bytes) -> PayloadConfig | None:
    try:
        d = json.loads(data.decode("utf-8"))
        if not isinstance(d, dict):
            return None
        cfg = PayloadConfig(
            manager_id=d["manager_id"], hosts=d["hosts"], port=d["port"],
            fingerprint=d["fingerprint"], secret=d["secret"], team=d["team"],
            version=d["version"],
        )
        cfg.validate()
        return cfg
    except Exception:  # noqa: BLE001 - bad content (incl. RecursionError) never raises
        return None


def _valid_footer(f: Any, size: int) -> tuple[int, PayloadConfig] | None:
    """(payload_start, cfg) when the tail of ``f`` is a fully valid footer, else None.

    Checks magic, length bounds, CRC, JSON and field validation. Shared by
    read_payload and append_payload so both agree on what a footer is.
    """
    if size < FOOTER_SIZE:
        return None
    f.seek(size - FOOTER_SIZE)
    tail = f.read(FOOTER_SIZE)
    if len(tail) != FOOTER_SIZE or tail[-len(MAGIC):] != MAGIC:
        return None
    (crc,) = struct.unpack(">I", tail[:4])
    (n,) = struct.unpack(">Q", tail[4:12])
    if n > MAX_PAYLOAD or n + FOOTER_SIZE > size:
        return None
    start = size - FOOTER_SIZE - n
    f.seek(start)
    data = f.read(n)
    if len(data) != n or zlib.crc32(data) != crc:
        return None
    cfg = _from_json(data)
    return None if cfg is None else (start, cfg)


def read_payload(exe: Path) -> PayloadConfig | None:
    """Read the payload from the tail of ``exe``; None for anything not valid.

    Only OSError (cannot open/read the file) propagates.
    """
    with open(exe, "rb") as f:
        f.seek(0, os.SEEK_END)
        found = _valid_footer(f, f.tell())
    return None if found is None else found[1]


def encode_payload(cfg: PayloadConfig) -> bytes:
    """The payload footer on its own (sidecar file for macOS; ``read_payload`` reads it)."""
    cfg.validate()
    payload = _to_json(cfg)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    return (
        payload + struct.pack(">I", zlib.crc32(payload))
        + struct.pack(">Q", len(payload)) + MAGIC
    )


def append_payload(src_exe: Path, dst_exe: Path, cfg: PayloadConfig) -> None:
    """Copy ``src_exe`` to ``dst_exe`` (1 MiB chunks) and append the payload footer.

    An existing footer on ``src_exe`` is dropped first (payloads never stack).
    """
    cfg.validate()
    payload = _to_json(cfg)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    footer = (
        payload + struct.pack(">I", zlib.crc32(payload))
        + struct.pack(">Q", len(payload)) + MAGIC
    )
    try:
        with open(src_exe, "rb") as src, open(dst_exe, "wb") as dst:
            src.seek(0, os.SEEK_END)
            size = src.tell()
            found = _valid_footer(src, size)
            remaining = size if found is None else found[0]
            src.seek(0)
            while remaining > 0:
                chunk = src.read(min(CHUNK, remaining))
                if not chunk:
                    raise OSError("template shrank while copying")
                dst.write(chunk)
                remaining -= len(chunk)
            dst.write(footer)
            dst.flush()
            os.fsync(dst.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            Path(dst_exe).unlink()
        raise
