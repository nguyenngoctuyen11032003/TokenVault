from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tokenvault.manager.enrollment import Enrollment


@pytest.fixture
def enr(tmp_path: Path) -> Iterator[Enrollment]:
    e = Enrollment(tmp_path / "enrollment.db")
    yield e
    e.close()


def test_package_secret_is_long_and_urlsafe(enr: Enrollment) -> None:
    pkg = enr.create_package("Dev", "first batch")
    assert len(pkg.secret) >= 32
    assert all(c.isalnum() or c in "-_" for c in pkg.secret)
    assert pkg.package_id


def test_wrong_secret_rejected(enr: Enrollment) -> None:
    enr.create_package("Dev", None)
    assert enr.register_device("not-the-secret", "dev-1", "Dev") is None
    assert enr.register_device("", "dev-1", "Dev") is None
    assert enr.list_devices() == []


def test_valid_secret_registers_and_key_verifies(enr: Enrollment) -> None:
    pkg = enr.create_package("Dev", None)
    key = enr.register_device(pkg.secret, "dev-1", "Dev")
    assert key is not None and len(key) >= 32
    assert enr.verify_device("dev-1", key)
    assert not enr.verify_device("dev-1", key + "x")
    assert not enr.verify_device("dev-2", key)


def test_device_key_reconnect_works_without_secret(tmp_path: Path) -> None:
    path = tmp_path / "enrollment.db"
    e1 = Enrollment(path)
    pkg = e1.create_package("Dev", None)
    key = e1.register_device(pkg.secret, "dev-1", "Dev")
    assert key is not None
    e1.close()
    e2 = Enrollment(path)  # e.g. after a Manager restart
    try:
        assert e2.verify_device("dev-1", key)
        assert e2.device_team("dev-1") == "Dev"
    finally:
        e2.close()


def test_revoked_device_rejected(enr: Enrollment) -> None:
    pkg = enr.create_package("Dev", None)
    key = enr.register_device(pkg.secret, "dev-1", "Dev")
    assert key is not None
    enr.revoke_device("dev-1")
    assert not enr.verify_device("dev-1", key)
    # The installer still holds the package secret: re-enrolling must not undo the revoke.
    assert enr.register_device(pkg.secret, "dev-1", "Dev") is None
    assert [d.revoked for d in enr.list_devices()] == [True]


def test_deleted_device_may_enroll_again(enr: Enrollment) -> None:
    pkg = enr.create_package("Dev", None)
    enr.register_device(pkg.secret, "dev-1", "Dev")
    enr.revoke_device("dev-1")
    enr.delete_device("dev-1")
    assert enr.register_device(pkg.secret, "dev-1", "Dev") is not None


def test_reregister_issues_new_key_and_invalidates_old(enr: Enrollment) -> None:
    pkg = enr.create_package("Dev", None)
    k1 = enr.register_device(pkg.secret, "dev-1", "Dev")
    k2 = enr.register_device(pkg.secret, "dev-1", "Dev")
    assert k1 and k2 and k1 != k2
    assert not enr.verify_device("dev-1", k1)
    assert enr.verify_device("dev-1", k2)


def test_revoked_package_blocks_enrollment_and_its_devices(enr: Enrollment) -> None:
    pkg = enr.create_package("Dev", None)
    other = enr.create_package("Ops", None)
    key = enr.register_device(pkg.secret, "dev-1", "Dev")
    okey = enr.register_device(other.secret, "ops-1", "Ops")
    assert key and okey
    enr.revoke_package(pkg.package_id)
    assert enr.register_device(pkg.secret, "dev-2", "Dev") is None
    assert not enr.verify_device("dev-1", key)
    assert enr.verify_device("ops-1", okey)
    assert enr.package_secret(pkg.package_id) is None
    assert {p.package_id: p.revoked for p in enr.list_packages()} == {
        pkg.package_id: True,
        other.package_id: False,
    }


def test_team_comes_from_package_not_hello(enr: Enrollment) -> None:
    pkg = enr.create_package("Dev", None)
    enr.register_device(pkg.secret, "dev-1", "Hacker-chosen")
    assert enr.device_team("dev-1") == "Dev"
    assert enr.device_team("nope") is None
    (dev,) = enr.list_devices()
    assert (dev.device_id, dev.team, dev.package_id, dev.revoked) == (
        "dev-1",
        "Dev",
        pkg.package_id,
        False,
    )


def test_package_secret_retrievable_for_packager(enr: Enrollment) -> None:
    pkg = enr.create_package("Dev", "lbl")
    assert enr.package_secret(pkg.package_id) == pkg.secret
    assert enr.package_secret("missing") is None
    (info,) = enr.list_packages()
    assert (info.team, info.label, info.device_count) == ("Dev", "lbl", 0)


def test_enrollment_secret_stored_only_hashed(tmp_path: Path) -> None:
    path = tmp_path / "enrollment.db"
    e = Enrollment(path)
    pkg = e.create_package("Dev", None)
    key = e.register_device(pkg.secret, "dev-1", "Dev")
    assert key is not None
    e.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    assert pkg.secret.encode() not in raw
    assert key.encode() not in raw
    con = sqlite3.connect(path)
    try:
        dumped = "\n".join(con.iterdump())
    finally:
        con.close()
    assert pkg.secret not in dumped
    assert key not in dumped


def test_unicode_and_surrogate_inputs_do_not_raise(enr: Enrollment) -> None:
    pkg = enr.create_package("Phòng Kỹ thuật", None)
    assert enr.register_device("\ud800", "dev-1", "Dev") is None
    assert enr.verify_device("\ud800", "\ud800") is False
    key = enr.register_device(pkg.secret, "máy-1", "x")
    assert key and enr.device_team("máy-1") == "Phòng Kỹ thuật"
