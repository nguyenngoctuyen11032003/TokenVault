from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tokenvault.manager import settings_store
from tokenvault.manager.settings_store import DEFAULTS, SettingsError, SettingsStore


def test_defaults_when_missing(tmp_path: Path) -> None:
    s = SettingsStore(tmp_path / "settings.json")
    v = s.get()
    assert v["transparent_window"] is True and v["start_with_windows"] is False
    assert v["port"] == 47800 and v["hosts"] == []
    assert not (tmp_path / "settings.json").exists()  # nothing written until a change


def test_patch_persists_and_reloads(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    s = SettingsStore(p)
    out = s.save_patch({"transparent_window": False, "hosts": ["192.168.1.10", "pc-quanly"]})
    assert out["transparent_window"] is False and out["hosts"] == ["192.168.1.10", "pc-quanly"]
    again = SettingsStore(p).get()
    assert again["transparent_window"] is False and again["hosts"] == ["192.168.1.10", "pc-quanly"]
    assert json.loads(p.read_text("utf-8"))["hosts"] == ["192.168.1.10", "pc-quanly"]


@pytest.mark.parametrize(
    "patch",
    [
        {"port": 1},  # not user-editable
        {"manager_id": "x"},
        {"nonsense": True},
        {"transparent_window": "yes"},
        {"start_with_windows": 1},
        {"hosts": "192.168.1.1"},
        {"hosts": ["bad host"]},
        {"hosts": ["1.2.3.4", "1.2.3.4"]},
        {"hosts": ["A.example", "a.example"]},
        {"hosts": [f"10.0.0.{i}" for i in range(9)]},
        {"hosts": ["999.1.1.1"]},
        {"hosts": [""]},
        {"hosts": [5]},
    ],
)
def test_rejects_unknown_keys_and_invalid_values(tmp_path: Path, patch: dict) -> None:
    s = SettingsStore(tmp_path / "settings.json")
    with pytest.raises(SettingsError):
        s.save_patch(patch)
    assert s.get() == SettingsStore(tmp_path / "settings.json").get()


def test_rejects_non_dict_patch(tmp_path: Path) -> None:
    with pytest.raises(SettingsError):
        SettingsStore(tmp_path / "s.json").save_patch(["hosts"])  # type: ignore[arg-type]


def test_hosts_are_trimmed_and_empty_list_means_auto(tmp_path: Path) -> None:
    s = SettingsStore(tmp_path / "s.json")
    assert s.save_patch({"hosts": [" 10.0.0.5 "]})["hosts"] == ["10.0.0.5"]
    assert s.save_patch({"hosts": []})["hosts"] == []


def test_corrupt_json_falls_back_to_defaults_with_backup(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text("{not json", encoding="utf-8")
    s = SettingsStore(p)
    assert s.get() == DEFAULTS
    backups = list(tmp_path.glob("settings.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text("utf-8") == "{not json"
    s.save_patch({"start_with_windows": True})
    assert SettingsStore(p).get()["start_with_windows"] is True


def test_wrong_types_in_file_fall_back_per_key(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps({"transparent_window": "no", "port": 47805, "hosts": ["bad host"], "x": 1}),
        encoding="utf-8",
    )
    v = SettingsStore(p).get()
    assert v["transparent_window"] is True and v["port"] == 47805 and v["hosts"] == []
    assert "x" not in v


def test_internal_update_validates(tmp_path: Path) -> None:
    s = SettingsStore(tmp_path / "s.json")
    s.update_internal(port=47801, manager_id="PC-QUANLY-a1b2c3")
    assert s.get()["port"] == 47801
    with pytest.raises(SettingsError):
        s.update_internal(port=0)
    with pytest.raises(SettingsError):
        s.update_internal(manager_id="has space")
    with pytest.raises(SettingsError):
        s.update_internal(unknown=1)


def test_atomic_write_keeps_old_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "settings.json"
    s = SettingsStore(p)
    s.save_patch({"hosts": ["10.0.0.1"]})
    before = p.read_bytes()

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(settings_store.os, "replace", boom)
    with pytest.raises(SettingsError):
        s.save_patch({"hosts": ["10.0.0.2"]})
    monkeypatch.undo()
    assert p.read_bytes() == before
    assert s.get()["hosts"] == ["10.0.0.1"]  # memory not changed either
    assert [f.name for f in tmp_path.iterdir()] == ["settings.json"]  # no temp left


def test_written_file_is_utf8_json(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    SettingsStore(p).update_internal(static_ip_hint="Đặt IP tĩnh")
    assert json.loads(p.read_bytes().decode("utf-8"))["static_ip_hint"] == "Đặt IP tĩnh"
    assert os.path.getsize(p) > 0


def test_window_geometry_internal_key(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    s = SettingsStore(p)
    geo = {"x": 10, "y": 20, "width": 1440, "height": 900, "maximized": True}
    s.update_internal(window=geo)
    assert SettingsStore(p).get()["window"] == geo
    for bad in ({"x": 1}, {**geo, "maximized": 1}, {**geo, "width": 5}, {**geo, "x": "1"}):
        with pytest.raises(SettingsError):
            s.update_internal(window=bad)
    with pytest.raises(SettingsError):
        s.save_patch({"window": geo})  # not user-editable
    s.update_internal(window=None)
    assert s.get()["window"] is None
