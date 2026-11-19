from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from tokenvault.common.config_payload import read_payload
from tokenvault.manager.enrollment import Enrollment
from tokenvault.manager.packager import Packager, PackagerError, safe_team_name
from tokenvault.manager.tls import CertInfo

FP = "cd" * 32


@pytest.fixture
def enr(tmp_path: Path) -> Iterator[Enrollment]:
    e = Enrollment(tmp_path / "enrollment.db")
    yield e
    e.close()


@pytest.fixture
def template(tmp_path: Path) -> Path:
    p = tmp_path / "adapter-setup.exe"
    p.write_bytes(b"MZ\x90\x00" + b"template-bytes" * 100)
    return p


def cert_of(tmp_path: Path) -> CertInfo:
    return CertInfo(tmp_path / "c.pem", tmp_path / "k.pem", FP)


def make(tmp_path: Path, enr: Enrollment, template: Path, port: int = 47800,
         hosts: list[str] | None = None) -> Packager:
    h = ["192.168.1.5"] if hosts is None else hosts
    return Packager(template, enr, cert_of(tmp_path), tmp_path / "out",
                    lambda: (h, port), "mgr-1")


def test_build_for_team_creates_package_and_secret_registered(
    tmp_path: Path, enr: Enrollment, template: Path
) -> None:
    b = make(tmp_path, enr, template).build_for_team("Dev", "batch 1")
    assert b.file_name == "TokenVault-Adapter-Dev.exe" and b.path.is_file()
    assert b.bytes == b.path.stat().st_size and b.team == "Dev"
    cfg = read_payload(b.path)
    assert cfg is not None and cfg.fingerprint == FP and cfg.manager_id == "mgr-1"
    assert enr.register_device(cfg.secret, "dev-1", "Dev") is not None
    pk = enr.list_packages()
    assert [p.package_id for p in pk] == [b.package_id] and pk[0].label == "batch 1"
    b2 = make(tmp_path, enr, template).build_for_team("Dev", None)
    cfg2 = read_payload(b2.path)
    assert cfg2 is not None and cfg2.secret != cfg.secret
    assert b2.package_id != b.package_id


def test_team_name_with_vietnamese_and_slashes_sanitised(
    tmp_path: Path, enr: Enrollment, template: Path
) -> None:
    assert safe_team_name("Phát triển / QA: \"x\"?") == "Phát triển _ QA_ _x_"
    b = make(tmp_path, enr, template).build_for_team("Phát triển/QA", None)
    assert b.file_name == "TokenVault-Adapter-Phát triển_QA.exe"
    assert b.path.parent == tmp_path / "out"
    cfg = read_payload(b.path)
    assert cfg is not None and cfg.team == "Phát triển_QA"
    # the enrolled package keeps the real team name
    assert enr.list_packages()[0].team == "Phát triển/QA"


def test_installer_carries_bound_port(tmp_path: Path, enr: Enrollment, template: Path) -> None:
    state = {"port": 47800}
    p = Packager(template, enr, cert_of(tmp_path), tmp_path / "out",
                 lambda: (["10.0.0.2"], state["port"]), "m")
    state["port"] = 47811  # fallback after construction
    cfg = read_payload(p.build_for_team("Dev", None).path)
    assert cfg is not None and cfg.port == 47811 and cfg.hosts == ["10.0.0.2"]


def test_existing_output_gets_suffix(tmp_path: Path, enr: Enrollment, template: Path) -> None:
    p = make(tmp_path, enr, template)
    names = [p.build_for_team("Dev", None).file_name for _ in range(3)]
    assert names == ["TokenVault-Adapter-Dev.exe", "TokenVault-Adapter-Dev (2).exe",
                     "TokenVault-Adapter-Dev (3).exe"]


@pytest.mark.parametrize("raw,expected", [
    ("CON", "CON_"), ("nul", "nul_"), ("com1", "com1_"), ("LPT9", "LPT9_"),
    ("Dev...", "Dev"), ("Dev  ", "Dev"), ("a//b\\\\c", "a_b_c"), ("a\x00\x1fb", "a_b"),
    ("", "Team"), ("...", "Team"), ("///", "_"), ("x" * 100, "x" * 40),
    ("Nhóm_1-2 ok", "Nhóm_1-2 ok"),
])
def test_safe_team_name(raw: str, expected: str) -> None:
    assert safe_team_name(raw) == expected


def test_missing_or_empty_template_raises(tmp_path: Path, enr: Enrollment) -> None:
    c = cert_of(tmp_path)
    p = Packager(tmp_path / "nope.exe", enr, c, tmp_path / "out", lambda: (["h"], 1), "m")
    with pytest.raises(PackagerError, match="template"):
        p.build_for_team("Dev", None)
    empty = tmp_path / "empty.exe"
    empty.write_bytes(b"")
    p2 = Packager(empty, enr, c, tmp_path / "out", lambda: (["h"], 1), "m")
    with pytest.raises(PackagerError, match="template"):
        p2.build_for_team("Dev", None)
    assert enr.list_packages() == []


def test_no_hosts_raises_without_creating_package(
    tmp_path: Path, enr: Enrollment, template: Path
) -> None:
    with pytest.raises(PackagerError, match="address"):
        make(tmp_path, enr, template, hosts=[]).build_for_team("Dev", None)
    assert enr.list_packages() == []


def test_out_dir_unwritable_raises(tmp_path: Path, enr: Enrollment, template: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    p = Packager(template, enr, cert_of(tmp_path), blocker / "sub", lambda: (["h"], 1), "m")
    with pytest.raises(PackagerError, match="output"):
        p.build_for_team("Dev", None)


def test_secret_never_logged_and_unicode_names(
    tmp_path: Path, enr: Enrollment, template: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        b = make(tmp_path, enr, template).build_for_team("Đội Ngũ", "lô 1")
    cfg = read_payload(b.path)
    assert cfg is not None and cfg.team == "Đội Ngũ"
    assert cfg.secret not in caplog.text
    assert b.file_name == "TokenVault-Adapter-Đội Ngũ.exe"
    assert b.path.read_bytes()[:4] == b"MZ\x90\x00"
