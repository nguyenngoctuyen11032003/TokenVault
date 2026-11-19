"""Enrollment packages and per-device keys (``enrollment.db``, separate from the event store).

* A *package* is what one employee installer carries: a team plus a random
  enrollment secret (``secrets.token_urlsafe(32)``, 43 chars). Only its SHA-256
  is used for lookups. The packager (Task 10) must embed the plain secret into
  installers it builds later, so a DPAPI-protected copy is also kept
  (:func:`Enrollment.package_secret`); it is deleted when the package is revoked.
* A *device* enrolls once with a package secret and receives a random device
  key; only the key's SHA-256 is stored and it is compared with
  ``hmac.compare_digest``. Re-enrolling with a valid secret of the SAME package
  issues a NEW key and invalidates the old one; a secret of a DIFFERENT package
  is refused (a device never moves between packages/teams by itself; the
  refusal looks exactly like a bad secret). The device's team is the package's.
* Revoking a device blocks its key and any re-enrollment of the same
  ``device_id`` (the installer still holds the secret);
  :meth:`Enrollment.delete_device` forgets it so it may enroll again.
  Revoking a package blocks new enrollments AND all devices enrolled with it.

Secrets are high-entropy random values, so a plain SHA-256 (no KDF) is enough.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tokenvault.manager import secrets as tv_secrets

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    package_id   TEXT PRIMARY KEY,
    team         TEXT NOT NULL,
    label        TEXT,
    secret_hash  TEXT NOT NULL UNIQUE,
    secret_blob  BLOB,                 -- DPAPI-protected plain secret (for the packager)
    created_at   REAL NOT NULL,
    revoked_at   REAL
);
CREATE TABLE IF NOT EXISTS devices (
    device_id    TEXT PRIMARY KEY,
    package_id   TEXT NOT NULL,
    team         TEXT NOT NULL,
    key_hash     TEXT NOT NULL,
    enrolled_at  REAL NOT NULL,
    revoked_at   REAL
);
PRAGMA user_version = 1;
"""


@dataclass(frozen=True)
class Package:
    package_id: str
    secret: str


@dataclass(frozen=True)
class PackageInfo:
    package_id: str
    team: str
    label: str | None
    created_at: float
    revoked: bool
    device_count: int


@dataclass(frozen=True)
class EnrolledDevice:
    device_id: str
    package_id: str
    team: str
    enrolled_at: float
    revoked: bool


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def _text_ok(*values: str) -> bool:
    """False for values SQLite cannot store as text (lone surrogates)."""
    try:
        for v in values:
            v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class Enrollment:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None, timeout=10
        )
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        with self._lock:
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------- packages
    def create_package(self, team: str, label: str | None) -> Package:
        """New package; the plain secret is returned here (and via package_secret)."""
        secret = secrets.token_urlsafe(32)
        package_id = "pkg_" + secrets.token_hex(6)
        with self._tx() as db:
            db.execute(
                "INSERT INTO packages (package_id, team, label, secret_hash, secret_blob, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (package_id, team, label, _h(secret), tv_secrets.protect(secret.encode()),
                 time.time()),
            )
        log.info("created enrollment package %s for team %r", package_id, team)
        return Package(package_id, secret)

    def package_secret(self, package_id: str) -> str | None:
        """Plain secret of an unrevoked package (for building installers), else None."""
        with self._lock:
            row = self._db.execute(
                "SELECT secret_blob FROM packages WHERE package_id = ? AND revoked_at IS NULL",
                (package_id,),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        try:
            return tv_secrets.unprotect(bytes(row[0])).decode()
        except (ValueError, UnicodeDecodeError) as e:
            log.warning("cannot read secret of package %s: %s", package_id, e)
            return None

    def revoke_package(self, package_id: str) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE packages SET revoked_at = COALESCE(revoked_at, ?), secret_blob = NULL "
                "WHERE package_id = ?",
                (time.time(), package_id),
            )
        log.info("revoked enrollment package %s", package_id)

    def list_packages(self) -> list[PackageInfo]:
        with self._lock:
            rows = self._db.execute(
                "SELECT p.package_id, p.team, p.label, p.created_at, p.revoked_at IS NOT NULL, "
                "(SELECT COUNT(*) FROM devices d WHERE d.package_id = p.package_id) "
                "FROM packages p ORDER BY p.created_at, p.package_id"
            ).fetchall()
        return [PackageInfo(r[0], r[1], r[2], r[3], bool(r[4]), int(r[5])) for r in rows]

    # -------------------------------------------------------------- devices
    def register_device(self, secret: str, device_id: str, team: str) -> str | None:
        """Enroll ``device_id`` with a package secret; return a new device key or None.

        ``team`` is what the adapter claims; the package's team is used instead.
        """
        if not secret or not device_id or not _text_ok(secret, device_id):
            return None
        key = secrets.token_urlsafe(32)
        with self._tx() as db:
            pkg = db.execute(
                "SELECT package_id, team FROM packages WHERE secret_hash = ? "
                "AND revoked_at IS NULL",
                (_h(secret),),
            ).fetchone()
            if pkg is None:
                return None
            dev = db.execute(
                "SELECT revoked_at, package_id FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if dev is not None and dev[0] is not None:
                return None
            if dev is not None and dev[1] != pkg[0]:
                log.warning(
                    "device %r tried to re-enroll with package %s but belongs to package %s; "
                    "refused",
                    device_id,
                    pkg[0],
                    dev[1],
                )
                return None
            db.execute(
                "INSERT INTO devices (device_id, package_id, team, key_hash, enrolled_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET "
                "package_id = excluded.package_id, team = excluded.team, "
                "key_hash = excluded.key_hash, enrolled_at = excluded.enrolled_at",
                (device_id, pkg[0], pkg[1], _h(key), time.time()),
            )
        if team != pkg[1]:
            log.info("device enrolled into team %r (it claimed %r)", pkg[1], team)
        return key

    def verify_device(self, device_id: str, device_key: str) -> bool:
        if not device_key or not _text_ok(device_id, device_key):
            return False
        with self._lock:
            row = self._db.execute(
                "SELECT d.key_hash FROM devices d JOIN packages p ON p.package_id = d.package_id "
                "WHERE d.device_id = ? AND d.revoked_at IS NULL AND p.revoked_at IS NULL",
                (device_id,),
            ).fetchone()
        expected = row[0] if row is not None else _h("\x00no-such-device")
        return hmac.compare_digest(_h(device_key), expected) and row is not None

    def device_team(self, device_id: str) -> str | None:
        if not _text_ok(device_id):
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT team FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
        return None if row is None else str(row[0])

    def device_package(self, device_id: str) -> str | None:
        if not _text_ok(device_id):
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT package_id FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
        return None if row is None else str(row[0])

    def devices_of_package(self, package_id: str) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT device_id FROM devices WHERE package_id = ? ORDER BY device_id",
                (package_id,),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def revoke_device(self, device_id: str) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE devices SET revoked_at = COALESCE(revoked_at, ?) WHERE device_id = ?",
                (time.time(), device_id),
            )

    def delete_device(self, device_id: str) -> None:
        """Forget a device entirely (it may then enroll again with a valid secret)."""
        with self._tx() as db:
            db.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))

    def list_devices(self) -> list[EnrolledDevice]:
        with self._lock:
            rows = self._db.execute(
                "SELECT device_id, package_id, team, enrolled_at, revoked_at IS NOT NULL "
                "FROM devices ORDER BY device_id"
            ).fetchall()
        return [EnrolledDevice(r[0], r[1], r[2], r[3], bool(r[4])) for r in rows]
