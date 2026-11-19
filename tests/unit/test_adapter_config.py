from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenvault.adapter import config as cfgmod
from tokenvault.adapter import paths
from tokenvault.adapter.config import AdapterConfig
from tokenvault.common.config_payload import PayloadConfig

FP = "ab" * 32
SECRET = "S" * 43


def _payload(team: str = "Dev") -> PayloadConfig:
    return PayloadConfig("mgr-1", ["10.0.0.5", "mgr-pc"], 47800, FP, SECRET, team)


def test_config_round_trips_unicode_team_and_has_no_secret(tmp_path: Path) -> None:
    cfg = AdapterConfig.from_payload(_payload("Phòng Kỹ thuật ✓"))
    p = tmp_path / "config.json"
    cfgmod.save_config(p, cfg)
    raw = p.read_text(encoding="utf-8")
    assert "Phòng Kỹ thuật ✓" in raw  # stored readable, not \u-escaped
    assert SECRET not in raw
    assert "secret" not in json.loads(raw)
    assert set(json.loads(raw)) == {"manager_id", "hosts", "port", "fingerprint", "team"}
    assert cfgmod.load_config(p) == cfg


def test_load_config_missing_or_invalid_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    assert cfgmod.load_config(p) is None
    p.write_text("{not json", encoding="utf-8")
    assert cfgmod.load_config(p) is None
    p.write_text(json.dumps({"manager_id": "m", "hosts": [], "port": 1,
                             "fingerprint": FP, "team": "x"}), encoding="utf-8")
    assert cfgmod.load_config(p) is None  # no hosts


def test_secret_file_is_protected_and_round_trips(tmp_path: Path) -> None:
    p = tmp_path / "enroll.secret"
    cfgmod.write_secret(p, SECRET)
    assert SECRET.encode() not in p.read_bytes() or p.read_bytes().startswith(b"TVP1:")
    assert cfgmod.read_secret(p) == SECRET
    cfgmod.delete_file(p)
    assert not p.exists()
    assert cfgmod.read_secret(p) is None
    cfgmod.delete_file(p)  # idempotent


def test_unreadable_secret_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "device.key"
    assert cfgmod.secret_state(p) == "missing"
    p.write_bytes(b"garbage")
    assert cfgmod.read_secret(p) is None
    assert cfgmod.secret_state(p) == "unreadable"
    cfgmod.write_secret(p, "k")
    assert cfgmod.secret_state(p) == "ok"


def test_enroll_secret_is_bound_to_its_manager(tmp_path: Path) -> None:
    p = tmp_path / "enroll.secret"
    a = AdapterConfig.from_payload(_payload())
    b = AdapterConfig("mgr-2", ("10.0.0.9",), 47800, "cd" * 32, "Dev")
    cfgmod.write_enroll_secret(p, SECRET, a)
    assert SECRET.encode() not in p.read_bytes()
    assert cfgmod.read_enroll_secret(p, a) == SECRET
    assert cfgmod.read_enroll_secret(p, b) is None  # never handed to another Manager
    same_mgr_other_team = AdapterConfig("mgr-1", ("x",), 1, FP, "QA")
    assert cfgmod.read_enroll_secret(p, same_mgr_other_team) == SECRET
    cfgmod.write_secret(p, SECRET)  # legacy/unbound format is not used
    assert cfgmod.read_enroll_secret(p, a) is None


def test_write_secret_fails_closed_without_dpapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenvault.common import dpapi

    def boom(data: bytes) -> bytes:
        raise OSError("dpapi down")

    monkeypatch.setattr(dpapi.sys, "platform", "win32")
    monkeypatch.setattr(dpapi, "_dpapi_protect", boom)
    p = tmp_path / "enroll.secret"
    with pytest.raises(dpapi.DpapiError):
        cfgmod.write_secret(p, SECRET)
    assert not p.exists()


def test_paths_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "TokenVault" / "Adapter"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert paths.claude_projects_dir() == Path.home() / ".claude" / "projects"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert paths.claude_projects_dir() == tmp_path / "cc" / "projects"
    files = paths.DataFiles(tmp_path)
    assert files.config.name == "config.json"
    assert files.enroll_secret.name == "enroll.secret"
    assert files.device_key.name == "device.key"
    assert files.outbox.name == "outbox.db"
    assert files.log.name == "adapter.log"
    assert files.state.name == "state.json"
