"""Secret storage seam: DPAPI on Windows, the login Keychain on macOS.

Keychain access goes through the ``security`` CLI. The secret is NEVER put in
argv (``ps`` would show it): the command is fed to ``security -i`` on stdin, and
the value is hex-encoded so no quoting is needed. Fail closed: if Keychain
refuses, :class:`SecretStoreError` (a ``DpapiError``, so existing handlers
apply) is raised and nothing is written in plaintext. Secrets are never logged.

On macOS the secret FILE (``device.key`` / ``enroll.secret``) holds only a marker
``TVK1:<account>`` pointing at the Keychain item, so "file exists" checks keep
working.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from tokenvault.common.dpapi import DpapiError

log = logging.getLogger(__name__)

SERVICE = "vn.ics.tokenvault.adapter"
MARKER = b"TVK1:"
_NOT_FOUND = 44  # `security` exit code: item could not be found

Runner = Callable[[Sequence[str], bytes | None], tuple[int, bytes, bytes]]


class SecretStoreError(DpapiError):
    """The secret store could not complete the operation."""


class SecretStore(Protocol):
    def set(self, account: str, value: bytes) -> None: ...

    def get(self, account: str) -> bytes | None: ...

    def delete(self, account: str) -> None: ...


def _default_runner() -> Runner:
    from tokenvault.adapter.platform.mac import run_command

    return run_command


def _quote(s: str) -> str:
    """Quote one token for the ``security -i`` command parser."""
    if any(c in s for c in "\"\\\n\r\0"):
        raise SecretStoreError("unsafe characters in keychain account")
    return f'"{s}"'


class MacKeychain:
    def __init__(self, runner: Runner | None = None, service: str = SERVICE) -> None:
        self._runner = runner
        self.service = service

    @property
    def run(self) -> Runner:
        if self._runner is None:
            self._runner = _default_runner()
        return self._runner

    def set(self, account: str, value: bytes) -> None:
        cmd = (f"add-generic-password -U -s {_quote(self.service)} -a {_quote(account)} "
               f"-w {value.hex()}\n")
        try:
            self.run(["security", "-i"], cmd.encode("ascii"))
        except Exception as e:  # noqa: BLE001 - OSError/timeout: fail closed
            raise SecretStoreError(f"Keychain unavailable: {type(e).__name__}") from e
        # `security -i` may exit 0 even when the command failed and its messages are not
        # a contract: the only proof is reading the item back.
        try:
            stored = self.get(account)
        except SecretStoreError as e:
            raise SecretStoreError(f"Keychain did not store the secret: {e}") from e
        if stored != value:
            raise SecretStoreError("Keychain did not store the secret (read-back mismatch)")

    def get(self, account: str) -> bytes | None:
        argv = ["security", "find-generic-password", "-s", self.service, "-a", account, "-w"]
        try:
            rc, out, _ = self.run(argv, None)
        except Exception as e:  # noqa: BLE001
            raise SecretStoreError(f"Keychain unavailable: {type(e).__name__}") from e
        if rc == _NOT_FOUND:
            return None
        if rc != 0:
            raise SecretStoreError(f"Keychain read failed (exit {rc})")
        try:
            return bytes.fromhex(out.decode("ascii").strip())
        except ValueError as e:
            raise SecretStoreError("Keychain item has an unexpected format") from e

    def delete(self, account: str) -> None:
        argv = ["security", "delete-generic-password", "-s", self.service, "-a", account]
        try:
            rc, _, _ = self.run(argv, None)
        except Exception as e:  # noqa: BLE001
            raise SecretStoreError(f"Keychain unavailable: {type(e).__name__}") from e
        if rc not in (0, _NOT_FOUND):
            raise SecretStoreError(f"Keychain delete failed (exit {rc})")


_override: SecretStore | None = None


def set_store(store: SecretStore | None) -> None:
    """Tests: force a store regardless of the OS."""
    global _override
    _override = store


def active_store() -> SecretStore | None:
    """The Keychain store on macOS (or an injected one); None = use DPAPI."""
    if _override is not None:
        return _override
    return MacKeychain() if sys.platform == "darwin" else None


def account_for(path: Path) -> str:
    """Stable Keychain account for a secret file (data dir hash keeps dirs apart)."""
    h = hashlib.sha256(str(path.parent).encode("utf-8")).hexdigest()[:12]
    return f"{h}:{path.name}"


def marker_account(blob: bytes) -> str | None:
    if blob.startswith(MARKER):
        try:
            return blob[len(MARKER):].decode("ascii")
        except UnicodeDecodeError:
            return None
    return None


def probe() -> None:
    """Fail before touching anything if secrets cannot be stored. Raises DpapiError."""
    store = active_store()
    if store is None:
        from tokenvault.common import dpapi

        dpapi.protect(b"probe")
        return
    store.set("probe", b"probe")  # includes the read-back check
    try:
        store.delete("probe")
    except DpapiError:
        log.warning("could not remove the Keychain probe item")
