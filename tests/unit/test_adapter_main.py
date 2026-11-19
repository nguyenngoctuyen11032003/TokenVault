from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from tokenvault.adapter import autostart, consent, main, singleton, tray
from tokenvault.adapter.app import AdapterApp
from tokenvault.adapter.collector import Collector
from tokenvault.adapter.config import load_config, read_secret, write_secret
from tokenvault.adapter.identity import Identity
from tokenvault.adapter.outbox import Outbox
from tokenvault.adapter.paths import DataFiles
from tokenvault.common.config_payload import PayloadConfig, append_payload
from tokenvault.common.models import UsageEvent

FIXTURE = Path(__file__).parent.parent / "fixtures" / "transcript_basic.jsonl"
FP = "cd" * 32
FP2 = "ef" * 32
SECRET = "Enr0llSecret_" + "x" * 30
IDENT = Identity("alice@example.com", "acc-1", "alice", "PC1", "PC1:alice")


def fake_exe(tmp: Path, name: str = "setup.exe", **kw: Any) -> Path:
    template = tmp / "template.bin"
    template.write_bytes(b"MZ fake installer body")
    args: dict[str, Any] = dict(
        manager_id="mgr-1", hosts=["10.0.0.5", "mgr-pc"], port=47800,
        fingerprint=FP, secret=SECRET, team="Phòng Dev ✓",
    )
    args.update(kw)
    out = tmp / name
    append_payload(template, out, PayloadConfig(**args))
    return out


@pytest.fixture
def no_side_effects(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"autostart": [], "launch": [], "ask": []}
    monkeypatch.setattr(autostart, "enable", lambda *a, **k: calls["autostart"].append(a))
    monkeypatch.setattr(main, "launch_detached", lambda cmd: calls["launch"].append(cmd))
    return calls


def run_setup(data: Path, exe: Path | None, *extra: str) -> int:
    argv = ["setup", "--data-dir", str(data)]
    if exe is not None:
        argv += ["--from", str(exe)]
    return main.main([*argv, *extra])


# ------------------------------------------------------------------ setup


def test_setup_without_payload_exits_with_clear_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_side_effects: dict[str, list[Any]]
) -> None:
    plain = tmp_path / "plain.exe"
    plain.write_bytes(b"MZ no payload here")
    data = tmp_path / "data"
    assert run_setup(data, plain, "--silent", "--accept") == 2
    err = capsys.readouterr().err
    assert "no TokenVault configuration" in err
    assert "Create installer" in err
    assert not (data / "config.json").exists()
    assert run_setup(data, tmp_path / "missing.exe", "--silent", "--accept") == 2
    assert no_side_effects["autostart"] == [] and no_side_effects["launch"] == []


def test_setup_silent_accept_writes_config_and_protected_secret(
    tmp_path: Path, no_side_effects: dict[str, list[Any]]
) -> None:
    data = tmp_path / "data"
    assert run_setup(data, fake_exe(tmp_path), "--silent", "--accept") == 0
    files = DataFiles(data)
    raw = files.config.read_text(encoding="utf-8")
    assert SECRET not in raw and "secret" not in raw
    cfg = load_config(files.config)
    assert cfg is not None and cfg.team == "Phòng Dev ✓" and cfg.hosts == ("10.0.0.5", "mgr-pc")
    blob = files.enroll_secret.read_bytes()
    assert SECRET.encode() not in blob
    assert read_secret(files.enroll_secret) == SECRET
    assert len(no_side_effects["autostart"]) == 1
    assert len(no_side_effects["launch"]) == 1
    assert "run" in no_side_effects["launch"][0]


def test_setup_no_autostart_flag_skips_registry_and_launch(
    tmp_path: Path, no_side_effects: dict[str, list[Any]]
) -> None:
    data = tmp_path / "data"
    rc = run_setup(data, fake_exe(tmp_path), "--silent", "--accept", "--no-autostart")
    assert rc == 0
    assert no_side_effects["autostart"] == [] and no_side_effects["launch"] == []


def test_setup_decline_writes_nothing_and_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_side_effects: dict[str, list[Any]]
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(consent, "ask_consent", lambda text: shown.append(text) or False)
    data = tmp_path / "data"
    assert run_setup(data, fake_exe(tmp_path)) == 3
    assert len(shown) == 1 and "10.0.0.5" in shown[0]
    assert not data.exists() or list(data.iterdir()) == []
    assert no_side_effects["autostart"] == [] and no_side_effects["launch"] == []
    # --silent without --accept is a decline too
    assert run_setup(data, fake_exe(tmp_path), "--silent") == 3
    assert not data.exists() or list(data.iterdir()) == []


def test_setup_dialog_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_side_effects: dict[str, list[Any]]
) -> None:
    monkeypatch.setattr(consent, "ask_consent", lambda text: True)
    data = tmp_path / "data"
    assert run_setup(data, fake_exe(tmp_path), "--no-autostart") == 0
    assert (data / "config.json").exists()


def test_setup_is_idempotent_and_keeps_device_key_for_same_manager(
    tmp_path: Path, no_side_effects: dict[str, list[Any]]
) -> None:
    data = tmp_path / "data"
    files = DataFiles(data)
    assert run_setup(data, fake_exe(tmp_path), "--silent", "--accept") == 0
    write_secret(files.device_key, "devkey-1")
    files.enroll_secret.unlink()
    # same manager, new package / team -> key kept, secret + config replaced
    exe2 = fake_exe(tmp_path, "setup2.exe", team="QA", secret="Other_" + "y" * 40)
    assert run_setup(data, exe2, "--silent", "--accept") == 0
    assert read_secret(files.device_key) == "devkey-1"
    assert read_secret(files.enroll_secret) == "Other_" + "y" * 40
    cfg = load_config(files.config)
    assert cfg is not None and cfg.team == "QA"
    # another Manager (fingerprint changed) -> old device key dropped
    exe3 = fake_exe(tmp_path, "setup3.exe", fingerprint=FP2)
    assert run_setup(data, exe3, "--silent", "--accept") == 0
    assert not files.device_key.exists()
    assert read_secret(files.enroll_secret) == SECRET


# ------------------------------------------------------------------ status


def _populate(data: Path) -> None:
    with Outbox(DataFiles(data).outbox) as ob:
        Collector(ob, IDENT, "Phòng Dev ✓").collect_file(FIXTURE)


def test_status_json_prints_event_without_message_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_side_effects: dict[str, list[Any]]
) -> None:
    data = tmp_path / "data"
    assert run_setup(data, fake_exe(tmp_path), "--silent", "--accept") == 0
    write_secret(DataFiles(data).device_key, "DEVICEKEY_must_not_print")
    _populate(data)
    capsys.readouterr()
    assert main.main(["status", "--json", "--data-dir", str(data)]) == 0
    out = capsys.readouterr().out
    d = json.loads(out)
    assert d["outbox"] == {"queued": 3, "delivered": 0, "waiting": 3}
    assert d["agent_running"] in (True, False)
    assert d["data_dir"] == str(data)
    ev = d["sample_event"]
    assert set(ev) == {f.name for f in fields(UsageEvent)}
    assert ev["message_id"] == "msg_A" and ev["project"] == "myproject"
    for forbidden in ("SECRET_", SECRET, "DEVICEKEY", "fingerprint_secret"):
        assert forbidden not in out
    assert "secret" not in d and "device_key" not in d


def test_status_text_works_without_config_or_outbox(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "empty"
    assert main.main(["status", "--data-dir", str(data)]) == 0
    out = capsys.readouterr().out
    assert "Device id" in out and "not set up" in out.lower()
    assert not (data / "outbox.db").exists()  # status never creates files


def test_status_json_no_events(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["status", "--json", "--data-dir", str(tmp_path)]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["sample_event"] is None
    assert d["outbox"] == {"queued": 0, "delivered": 0, "waiting": 0}


def test_about_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["about"]) == 0
    assert "TokenVault Adapter" in capsys.readouterr().out


# ------------------------------------------------------------------ run


class FakeApp:
    instances: list[FakeApp] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        FakeApp.instances.append(self)

    def run(self) -> int:
        return 0


class FakeFn:
    def __init__(self, ret: Any = 1) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.ret = ret

    def __call__(self, *a: Any) -> Any:
        self.calls.append(a)
        return self.ret


class FakeKernel32:
    def __init__(self) -> None:
        self.GetCurrentProcess = FakeFn(1234)  # noqa: N815
        self.SetPriorityClass = FakeFn(1)  # noqa: N815


class FakeWindll:
    def __init__(self) -> None:
        self.kernel32 = FakeKernel32()


@pytest.fixture
def configured(tmp_path: Path, no_side_effects: dict[str, list[Any]]) -> Path:
    data = tmp_path / "data"
    assert run_setup(data, fake_exe(tmp_path), "--silent", "--accept") == 0
    FakeApp.instances.clear()
    return data


@pytest.mark.skipif(not hasattr(__import__("ctypes"), "windll"), reason="Windows only")
def test_run_uses_below_normal_priority(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ctypes

    fake = FakeWindll()
    monkeypatch.setattr(ctypes, "windll", fake)
    monkeypatch.setattr(main, "AdapterApp", FakeApp)
    assert main.main(["run", "--data-dir", str(configured), "--no-tray"]) == 0
    assert fake.kernel32.SetPriorityClass.calls == [(1234, 0x4000)]
    assert len(FakeApp.instances) == 1


def test_second_instance_exits(configured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "AdapterApp", FakeApp)
    monkeypatch.setattr(main, "set_background_priority", lambda: True)
    first = singleton.SingleInstance(singleton.mutex_name())
    assert first.acquire()
    try:
        assert main.main(["run", "--data-dir", str(configured), "--no-tray"]) == 0
        assert FakeApp.instances == []  # quietly exited, nothing started
        if singleton.SUPPORTED:
            assert singleton.is_running(singleton.mutex_name())
    finally:
        first.release()
    assert main.main(["run", "--data-dir", str(configured), "--no-tray"]) == 0
    assert len(FakeApp.instances) == 1


def test_run_without_config_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "set_background_priority", lambda: True)
    assert main.main(["run", "--data-dir", str(tmp_path / "nothing"), "--no-tray"]) == 2


# ------------------------------------------------------------- app pieces


def _app(data: Path, tmp: Path) -> AdapterApp:
    return AdapterApp(DataFiles(data), tmp / "projects", identity=IDENT, tray=False)


def test_hello_uses_enroll_secret_before_enrollment_then_device_key(
    configured: Path, tmp_path: Path
) -> None:
    app = _app(configured, tmp_path)
    h = app.hello_factory()
    assert (h.enroll_secret, h.device_key) == (SECRET, None)
    assert h.device_id == "PC1:alice" and h.team == "Phòng Dev ✓"
    app.on_device_key("NEWKEY_123")
    files = DataFiles(configured)
    assert not files.enroll_secret.exists()
    assert b"NEWKEY_123" not in files.device_key.read_bytes() or not _dpapi_on()
    h2 = app.hello_factory()
    assert (h2.enroll_secret, h2.device_key) == (None, "NEWKEY_123")


def _dpapi_on() -> bool:
    import sys

    return sys.platform == "win32"


def test_rejected_device_key_falls_back_to_new_enroll_secret(
    configured: Path, tmp_path: Path
) -> None:
    files = DataFiles(configured)
    write_secret(files.device_key, "OLDKEY")
    app = _app(configured, tmp_path)
    assert app.hello_factory().device_key == "OLDKEY"
    app.on_state("rejected")  # key revoked, but setup left a fresh enroll secret
    h = app.hello_factory()
    assert (h.enroll_secret, h.device_key) == (SECRET, None)


def test_connected_with_device_key_deletes_leftover_secret(
    configured: Path, tmp_path: Path
) -> None:
    files = DataFiles(configured)
    write_secret(files.device_key, "KEY")
    app = _app(configured, tmp_path)
    app.on_state("connected")
    assert not files.enroll_secret.exists()
    state = json.loads(files.state.read_text(encoding="utf-8"))
    assert state["state"] == "connected"


def test_app_without_config_raises(tmp_path: Path) -> None:
    from tokenvault.adapter.app import NotConfigured

    with pytest.raises(NotConfigured):
        _app(tmp_path / "none", tmp_path)


def test_resolver_port_change_rebuilds_endpoint(configured: Path, tmp_path: Path) -> None:
    from tokenvault.adapter.transport import Endpoint

    class Res:
        last_port: int | None = None

        def __call__(self) -> list[str]:
            self.last_port = 47801
            return ["10.0.0.9"]

    app = AdapterApp(DataFiles(configured), tmp_path / "p", identity=IDENT, tray=False,
                     resolver=Res())

    class T:
        endpoint = Endpoint(["10.0.0.5"], 47800, FP)

    t = T()
    app._transport = t  # type: ignore[assignment]
    assert app._resolve() == ["10.0.0.9"]
    assert t.endpoint.port == 47801
    cfg = load_config(DataFiles(configured).config)
    assert cfg is not None and cfg.port == 47801


# ------------------------------------------------------------------ tray


def test_tray_menu_has_exactly_four_entries() -> None:
    entries = tray.build_menu(lambda: "Connected - 0 queued", lambda: None, lambda: None,
                              lambda: None)
    labels = [e.label() for e in entries]
    assert labels == ["Connected - 0 queued", "What is sent?", "Open log folder", "About"]
    assert [e.enabled for e in entries] == [False, True, True, True]
    for word in ("pause", "quit", "exit", "hide"):
        assert all(word not in lbl.lower() for lbl in labels)
    menu = tray.to_pystray_menu(entries)
    assert len(list(menu.items)) == 4


def test_tray_status_text() -> None:
    assert tray.status_line("connected", 0) == "Connected - 0 queued"
    assert tray.status_line("offline", 5) == "Offline - 5 queued"
    assert tray.status_line("rejected", 1) == "Rejected - 1 queued"
    assert tray.status_line("connecting", 2) == "Connecting - 2 queued"


def test_tray_icon_image_is_generated() -> None:
    img = tray.make_icon("connected")
    assert img.size == (64, 64)
    assert tray.make_icon("offline").getpixel((32, 32)) != img.getpixel((32, 32))


def test_what_is_sent_text_shows_one_event_or_note(tmp_path: Path) -> None:
    from tokenvault.adapter import status_view

    with Outbox(tmp_path / "o.db") as ob:
        assert "No events" in status_view.what_is_sent_text(ob, ["h"])
        Collector(ob, IDENT, "Dev").collect_file(FIXTURE)
        text = status_view.what_is_sent_text(ob, ["h"])
    assert '"message_id": "msg_A"' in text and "SECRET_" not in text
    assert "  - session_title:" in text
