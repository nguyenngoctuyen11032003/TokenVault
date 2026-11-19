"""Adapter config (config.json, no secrets) and DPAPI-protected secret files."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tokenvault.common import dpapi, secret_store
from tokenvault.common.config_payload import PayloadConfig

log = logging.getLogger("tokenvault.adapter.config")

_DUMMY_SECRET = "x" * 43  # only to reuse PayloadConfig.validate for the non-secret fields
MAX_FILE_BYTES = 64 * 1024


@dataclass(frozen=True)
class AdapterConfig:
    manager_id: str
    hosts: tuple[str, ...]
    port: int
    fingerprint: str
    team: str

    @classmethod
    def from_payload(cls, p: PayloadConfig) -> AdapterConfig:
        return cls(p.manager_id, tuple(p.hosts), p.port, p.fingerprint, p.team)

    def validate(self) -> None:
        PayloadConfig(self.manager_id, list(self.hosts), self.port, self.fingerprint,
                      _DUMMY_SECRET, self.team).validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "manager_id": self.manager_id,
            "hosts": list(self.hosts),
            "port": self.port,
            "fingerprint": self.fingerprint,
            "team": self.team,
        }


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def save_config(path: Path, cfg: AdapterConfig) -> None:
    cfg.validate()
    text = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    atomic_write(path, text.encode("utf-8"))


def load_config(path: Path) -> AdapterConfig | None:
    """The saved config, or None if missing / unreadable / invalid (logged)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("cannot read config: %s", e)
        return None
    try:
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("config too large")
        d = json.loads(raw.decode("utf-8"))
        if not isinstance(d, dict) or not isinstance(d.get("hosts"), list):
            raise ValueError("config must be an object with a hosts list")
        cfg = AdapterConfig(d["manager_id"], tuple(d["hosts"]), d["port"], d["fingerprint"],
                            d["team"])
        cfg.validate()
        return cfg
    except (ValueError, KeyError, TypeError, RecursionError) as e:
        log.warning("config.json is invalid: %s", e)
        return None


def write_secret(path: Path, value: str) -> None:
    """Store ``value`` DPAPI-protected (current user) or, on macOS, in the Keychain
    (the file then holds only a pointer). Never logs the value."""
    store = secret_store.active_store()
    if store is None:
        atomic_write(path, dpapi.protect(value.encode("utf-8")))
        return
    account = secret_store.account_for(path)
    store.set(account, value.encode("utf-8"))  # raises (fail closed) before any file exists
    atomic_write(path, secret_store.MARKER + account.encode("ascii"))


def read_secret(path: Path) -> str | None:
    """The stored secret, or None if missing or unusable (logged without content)."""
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("cannot read %s: %s", path.name, e)
        return None
    try:
        account = secret_store.marker_account(blob)
        if account is not None:
            store = secret_store.active_store()
            raw = store.get(account) if store is not None else None
            if raw is None:
                log.warning("%s: Keychain item is missing", path.name)
                return None
            value = raw.decode("utf-8")
        else:
            value = dpapi.unprotect(blob).decode("utf-8")
    except (ValueError, UnicodeDecodeError, OSError) as e:
        log.warning("%s is unusable: %s", path.name, e)
        return None
    return value or None


def secret_state(path: Path) -> str:
    """"ok" (decrypts), "missing" or "unreadable"."""
    if not path.exists():
        return "missing"
    return "ok" if read_secret(path) is not None else "unreadable"


def write_enroll_secret(path: Path, secret: str, cfg: AdapterConfig) -> None:
    """Store the enrollment secret BOUND to the Manager it belongs to.

    An agent still running with another Manager's config will refuse to use it
    (see :func:`read_enroll_secret`), so a secret can never be sent to the wrong
    Manager while setup swaps configs.
    """
    bound = {"manager_id": cfg.manager_id, "fingerprint": cfg.fingerprint, "secret": secret}
    write_secret(path, json.dumps(bound, separators=(",", ":")))


def read_enroll_secret(path: Path, cfg: AdapterConfig) -> str | None:
    """The enrollment secret if it is bound to ``cfg``'s Manager, else None."""
    raw = read_secret(path)
    if raw is None:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        log.warning("%s has an unknown format; ignored", path.name)
        return None
    if not isinstance(d, dict) or not isinstance(d.get("secret"), str):
        return None
    if (d.get("manager_id"), d.get("fingerprint")) != (cfg.manager_id, cfg.fingerprint):
        log.warning("enrollment secret belongs to another Manager; not using it")
        return None
    return str(d["secret"]) or None


def delete_file(path: Path) -> None:
    store = secret_store.active_store()
    if store is not None:
        try:
            with open(path, "rb") as fh:
                account = secret_store.marker_account(fh.read(256))
            if account is not None:
                store.delete(account)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("could not remove the Keychain item for %s", path.name)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
