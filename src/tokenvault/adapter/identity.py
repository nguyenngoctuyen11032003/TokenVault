"""Account/user/host identity. Reads only emailAddress and accountUuid."""

from __future__ import annotations

import getpass
import json
import logging
import os
import re
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEVICE_ID_FILE = "device.id"
_ID_OK = re.compile(r"^[^\s]{1,200}$")
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


def load_device_id(data_dir: Path, user: str, host: str) -> str:
    """Stable per-install device id, persisted in ``<data_dir>/device.id``.

    New installs get ``host:user:<8 hex>`` (a random part so the id cannot be guessed
    from names). An install that predates this file (it already has a config or a
    device key) keeps its old ``host:user`` id so its enrolled key stays valid. The file
    survives reinstalls, so a reinstall is the same device.
    """
    path = data_dir / DEVICE_ID_FILE
    try:
        text = path.read_text(encoding="utf-8").strip()
        if _ID_OK.match(text):
            return text
    except (OSError, UnicodeDecodeError):
        pass
    legacy = (data_dir / "device.key").exists() or (data_dir / "config.json").exists()
    did = f"{host}:{user}" if legacy else f"{host}:{user}:{secrets.token_hex(4)}"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(did, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("cannot persist the device id (%s); using it for this run only", e)
    return did


def read_identity(claude_json: Path, user: str, host: str,
                  device_id: str | None = None) -> Identity:
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
    return Identity(email, uuid, user, host, device_id or f"{host}:{user}")


def display_account(i: Identity) -> str:
    return i.email or f"Unknown account ({i.windows_user}@{i.hostname})"


def default_identity(data_dir: Path | None = None) -> Identity:
    user, host = getpass.getuser(), socket.gethostname()
    did = load_device_id(data_dir, user, host) if data_dir is not None else None
    return read_identity(Path.home() / ".claude.json", user, host, did)
