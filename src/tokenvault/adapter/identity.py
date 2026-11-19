"""Account/user/host identity. Reads only emailAddress and accountUuid."""

from __future__ import annotations

import getpass
import json
import socket
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_EMAIL = 254
MAX_UUID = 128


@dataclass(frozen=True)
class Identity:
    email: str | None
    account_uuid: str | None
    windows_user: str
    hostname: str
    device_id: str


def _clean(value: object, limit: int) -> str | None:
    if isinstance(value, str) and value and len(value) <= limit:
        return value
    return None


def read_identity(claude_json: Path, user: str, host: str) -> Identity:
    email: str | None = None
    uuid: str | None = None
    try:
        with open(claude_json, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
        if len(raw) <= MAX_FILE_BYTES:
            data = json.loads(raw.decode("utf-8"))
            acct = data.get("oauthAccount") if isinstance(data, dict) else None
            if isinstance(acct, dict):
                email = _clean(acct.get("emailAddress"), MAX_EMAIL)
                uuid = _clean(acct.get("accountUuid"), MAX_UUID)
    except (OSError, ValueError, RecursionError):
        pass
    return Identity(email, uuid, user, host, f"{host}:{user}")


def display_account(i: Identity) -> str:
    return i.email or f"Unknown account ({i.windows_user}@{i.hostname})"


def default_identity() -> Identity:
    return read_identity(
        Path.home() / ".claude.json", getpass.getuser(), socket.gethostname()
    )
