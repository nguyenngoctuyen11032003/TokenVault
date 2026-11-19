from __future__ import annotations

import io
import json
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from tokenvault.adapter import app as appmod
from tokenvault.adapter import autostart, consent, main, singleton, strings, tray
from tokenvault.adapter.app import AdapterApp
from tokenvault.adapter.collector import Collector
from tokenvault.adapter.config import load_config, read_enroll_secret, read_secret, write_secret
from tokenvault.adapter.identity import Identity
from tokenvault.adapter.outbox import Outbox
from tokenvault.adapter.paths import DataFiles
from tokenvault.common import dpapi
from tokenvault.common.config_payload import PayloadConfig, append_payload
from tokenvault.common.models import UsageEvent

FIXTURE = Path(__file__).parent.parent / "fixtures" / "transcript_basic.jsonl"
FP = "cd" * 32
FP2 = "ef" * 32
SECRET = "Enr0llSecret_" + "x" * 30
IDENT = Identity("alice@example.com", "acc-1", "alice", "PC1", "PC1:alice")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Unique mutex/event names (never the real agent's) and Vietnamese default."""
    monkeypatch.setenv("TOKENVAULT_MUTEX_NAME", f"Local\\TokenVaultTest-{uuid.uuid4().hex}")
    monkeypatch.delenv("TOKENVAULT_LANG", raising=False)
    strings.set_lang(None)
    yield
    strings.set_lang(None)


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
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    c: dict[str, list[Any]] = {"autostart": [], "launch": []}
    monkeypatch.setattr(autostart, "enable", lambda *a, **k: c["autostart"].append((a, k)))
    monkeypatch.setattr(main, "launch_detached", lambda cmd: c["launch"].append(cmd))
    return c


def run_setup(data: Path, exe: Path | None, *extra: str) -> int:
    argv = ["setup", "--data-dir", str(data)]
    if exe is not None:
        argv += ["--from", str(exe)]
    return main.main([*argv, *extra])


def empty(data: Path) -> bool:
    return not data.exists() or not any(p for p in data.iterdir() if p.name != "adapter.log")


# ------------------------------------------------------------------ setup
def test_setup_without_payload_exits_with_clear_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], calls: dict[str, list[Any]]
) -> None:
    plain = tmp_path / "plain.exe"
    plain.write_bytes(b"MZ no payload here")
    data = tmp_path / "data"
    assert run_setup(data, plain, "--silent", "--accept") == 2
    err = capsys.readouterr().err
    assert "không chứa cấu hình TokenVault" in err and "Create installer" in err
    assert run_setup(data, tmp_path / "missing.exe", "--silent", "--accept", "--lang", "en") == 2
    assert "has no TokenVault configuration" in capsys.readouterr().err
    assert empty(data) and calls == {"autostart": [], "launch": []}


def test_setup_silent_accept_writes_config_and_protected_bound_secret(
    tmp_path: Path, calls: dict[str, list[Any]]
) -> None:
    data = tmp_path / "data"
    assert run_setup(data, fake_exe(tmp_path), "--silent", "--accept") == 0
    files = DataFiles(data)
    raw = files.config.read_text(encoding="utf-8")
    assert SECRET not in raw and "secret" not in raw
    cfg = load_config(files.config)
    assert cfg is not None and cfg.team == "Phòng Dev ✓" and cfg.hosts == ("10.0.0.5", "mgr-pc")
    assert SECRET.encode() not in files.enroll_secret.read_bytes()
    assert read_enroll_secret(files.enroll_secret, cfg) == SECRET
    assert len(calls["autostart"]) == 1 and "run" in calls["autostart"][0][1]["args"]
    assert len(calls["launch"]) == 1 and "run" in calls["launch"][0]


@pytest.mark.parametrize(("flag", "auto", "launch"),
                         [("--no-autostart", 0, 1), ("--no-start", 1, 0)])
def test_setup_no_autostart_and_no_start_are_separate(
    tmp_path: Path, calls: dict[str, list[Any]], flag: str, auto: int, launch: int
) -> None:
    assert run_setup(tmp_path / "d", fake_exe(tmp_path), "--silent", "--accept", flag) == 0
    assert (len(calls["autostart"]), len(calls["launch"])) == (auto, launch)


def test_setup_decline_writes_nothing_and_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: dict[str, list[Any]]
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(consent, "ask_consent", lambda text: shown.append(text) or False)
    data = tmp_path / "data"
    assert run_setup(data, fake_exe(tmp_path)) == 3
    assert len(shown) == 1 and "10.0.0.5" in shown[0] and "KHÔNG gửi" in shown[0]
    assert run_setup(data, fake_exe(tmp_path), "--silent") == 3  # no --accept = decline
    assert empty(data) and calls == {"autostart": [], "launch": []}


def test_setup_without_consent_ui_exits_4_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    calls: dict[str, list[Any]],
) -> None:
    monkeypatch.setattr(consent, "ask_consent", lambda text: None)
    data = tmp_path / "data"
    assert run_setup(data, fake_exe(tmp_path)) == 4
    assert "Không hiển thị được màn hình đồng ý" in capsys.readouterr().err
    assert empty(data) and calls == {"autostart": [], "launch": []}
    assert run_setup(data, fake_exe(tmp_path), "--silent", "--accept") == 0  # unaffected


def test_setup_fails_closed_when_dpapi_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: dict[str, list[Any]]
) -> None:
    def boom(data: bytes) -> bytes:
        raise OSError("dpapi down")

    monkeypatch.setattr(dpapi.sys, "platform", "win32")
    monkeypatch.setattr(dpapi, "_dpapi_protect", boom)
    data = tmp_path / "data"
    assert run_setup(data, fake_exe(tmp_path), "--silent", "--accept") == 5
    assert empty(data) and calls == {"autostart": [], "launch": []}


def test_setup_dialog_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: dict[str, list[Any]]
) -> None:
    monkeypatch.setattr(consent, "ask_consent", lambda text: True)
    assert run_setup(tmp_path / "d", fake_exe(tmp_path), "--no-start") == 0
    assert (tmp_path / "d" / "config.json").exists()


def test_setup_stops_old_agent_then_writes_secret_before_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: dict[str, list[Any]]
) -> None:
    order: list[str] = []
    monkeypatch.setattr(singleton, "signal_reload", lambda name: order.append("signal") or True)
    monkeypatch.setattr(singleton, "wait_released",
                        lambda name, timeout: order.append(f"wait{timeout:.0f}") or True)
    real_secret, real_save = main.write_enroll_secret, main.save_config
    monkeypatch.setattr(main, "write_enroll_secret",
                        lambda *a: (order.append("secret"), real_secret(*a))[1])
    monkeypatch.setattr(main, "save_config", lambda *a: (order.append("config"), real_save(*a))[1])
    assert run_setup(tmp_path / "d", fake_exe(tmp_path), "--silent", "--accept") == 0
    assert order == ["signal", "wait10", "secret", "config"]


def test_setup_is_idempotent_and_keeps_device_key_only_for_same_manager(
    tmp_path: Path, calls: dict[str, list[Any]]
) -> None:
    data = tmp_path / "data"
    files = DataFiles(data)
    assert run_setup(data, fake_exe(tmp_path), "--silent", "--accept") == 0
    write_secret(files.device_key, "devkey-1")
    files.enroll_secret.unlink()
    exe2 = fake_exe(tmp_path, "setup2.exe", team="QA", secret="Other_" + "y" * 40)
    assert run_setup(data, exe2, "--silent", "--accept") == 0
    cfg = load_config(files.config)
    assert cfg is not None and cfg.team == "QA"
    assert read_secret(files.device_key) == "devkey-1"
    assert read_enroll_secret(files.enroll_secret, cfg) == "Other_" + "y" * 40
    # another Manager (fingerprint changed) -> old device key dropped
    assert run_setup(data, fake_exe(tmp_path, "s3.exe", fingerprint=FP2), "--silent",
                     "--accept") == 0
    assert not files.device_key.exists()
    # corrupt previous config -> cannot prove the key belongs here -> dropped
    write_secret(files.device_key, "devkey-2")
    files.config.write_text("{broken", encoding="utf-8")
    assert run_setup(data, fake_exe(tmp_path, "s4.exe", fingerprint=FP2), "--silent",
                     "--accept") == 0
    assert not files.device_key.exists()


# ------------------------------------------------------------------ status
def _populate(data: Path) -> None:
    with Outbox(DataFiles(data).outbox) as ob:
        Collector(ob, IDENT, "Phòng Dev ✓").collect_file(FIXTURE)


@pytest.fixture
def configured(tmp_path: Path, calls: dict[str, list[Any]]) -> Path:
    data = tmp_path / "data"
    assert run_setup(data, fake_exe(tmp_path), "--silent", "--accept") == 0
    return data


def test_status_json_prints_event_without_message_text(
    configured: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_secret(DataFiles(configured).device_key, "DEVICEKEY_must_not_print")
    _populate(configured)
    capsys.readouterr()
    assert main.main(["status", "--json", "--data-dir", str(configured)]) == 0
    out = capsys.readouterr().out
    d = json.loads(out)
    assert d["outbox"] == {"queued": 3, "delivered": 0, "waiting": 3}
    assert d["enrolled"] is True and d["device_key"] == "ok"
    assert d["data_dir"] == str(configured)
    ev = d["sample_event"]
    assert set(ev) == {f.name for f in fields(UsageEvent)}
    assert ev["message_id"] == "msg_A" and ev["project"] == "myproject"
    for forbidden in ("SECRET_", SECRET, "DEVICEKEY"):
        assert forbidden not in out


def test_status_reports_unreadable_key_as_not_enrolled(
    configured: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    DataFiles(configured).device_key.write_bytes(b"TVD1:garbage")
    capsys.readouterr()
    assert main.main(["status", "--json", "--data-dir", str(configured)]) == 0
    d = json.loads(capsys.readouterr().out)
    assert (d["enrolled"], d["device_key"]) == (False, "unreadable")
    assert main.main(["status", "--data-dir", str(configured)]) == 0
    assert "không đọc được khoá" in capsys.readouterr().out


def test_status_text_vietnamese_without_config_or_outbox(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "empty"
    assert main.main(["status", "--data-dir", str(data)]) == 0
    out = capsys.readouterr().out
    assert "Mã thiết bị:" in out and "chưa cài đặt" in out and "Hàng đợi:" in out
    assert not (data / "outbox.db").exists()  # status never creates files
    assert main.main(["status", "--data-dir", str(data), "--lang", "en"]) == 0
    assert "Device id:" in capsys.readouterr().out


def test_status_reads_outbox_read_only_while_agent_has_it_open(tmp_path: Path) -> None:
    from tokenvault.adapter import status_view

    db = tmp_path / "outbox.db"
    with Outbox(db) as ob:  # the "agent" keeps its WAL connection open
        Collector(ob, IDENT, "Dev").collect_file(FIXTURE)
        before = db.stat().st_mtime_ns
        view = status_view.read_outbox(db)
        assert (view.queued, view.waiting) == (3, 3) and view.sample is not None
        assert db.stat().st_mtime_ns == before
        ob.ack([(p.message_id, p.revision) for p in ob.pending(10)])
        assert status_view.read_outbox(db).waiting == 0


def test_status_json_no_events(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["status", "--json", "--data-dir", str(tmp_path)]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["sample_event"] is None
    assert d["outbox"] == {"queued": 0, "delivered": 0, "waiting": 0}


def test_about_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["about"]) == 0
    out = capsys.readouterr().out
    assert "TokenVault Adapter" in out and "Gỡ cài đặt" in out


def test_show_subcommand_prints_what_is_sent(
    configured: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _populate(configured)
    capsys.readouterr()
    assert main.main(["show", "what-is-sent", "--print", "--data-dir", str(configured)]) == 0
    out = capsys.readouterr().out
    assert "Một sự kiện đang chờ" in out and '"message_id": "msg_A"' in out
    assert "SECRET_" not in out and "10.0.0.5" in out
    args = main.build_parser().parse_args(["show", "about", "--data-dir", "x"])
    assert (args.command, args.what, args.data_dir) == ("show", "about", "x")
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["show", "bogus"])


# ------------------------------------------------------------------ console
class FakeK32:
    def __init__(self, attach_ok: bool = True) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.ok = attach_ok

    def AttachConsole(self, pid: int) -> int:  # noqa: N802
        self.calls.append(("AttachConsole", pid))
        return int(self.ok)

    def SetConsoleOutputCP(self, cp: int) -> int:  # noqa: N802
        self.calls.append(("SetConsoleOutputCP", cp))
        return 1


def test_attach_parent_console_for_windowed_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("stdout", "stderr", "stdin"):
        monkeypatch.setattr(sys, name, None)
    opened: list[str] = []

    def opener(path: str, mode: str, **kw: Any) -> io.StringIO:
        opened.append(path)
        assert path in ("CONOUT$", "CONIN$") and kw.get("encoding") == "utf-8"
        return io.StringIO()

    k = FakeK32()
    assert main.attach_parent_console(k, opener, frozen=True, platform="win32")
    assert ("AttachConsole", 0xFFFFFFFF) in k.calls and ("SetConsoleOutputCP", 65001) in k.calls
    assert opened == ["CONOUT$", "CONIN$"] and sys.stdout is sys.stderr
    sys.stdout.write("Mã thiết bị")  # Vietnamese goes through


def test_attach_console_is_noop_when_output_exists_or_not_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    k = FakeK32()
    redirected = io.StringIO()
    monkeypatch.setattr(sys, "stdout", redirected)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert not main.attach_parent_console(k, frozen=True, platform="win32")
    assert not main.attach_parent_console(k, frozen=False, platform="win32")
    assert k.calls == [] and sys.stdout is redirected
    monkeypatch.setattr(sys, "stdout", None)
    assert not main.attach_parent_console(FakeK32(attach_ok=False), frozen=True,
                                          platform="win32")  # no parent console: stay quiet


# ------------------------------------------------------------------ run
class FakeApp:
    instances: list[FakeApp] = []
    error: Exception | None = None

    def __init__(self, *a: Any, **k: Any) -> None:
        FakeApp.instances.append(self)

    def run(self) -> int:
        if FakeApp.error is not None:
            raise FakeApp.error
        return 0


@pytest.fixture
def fake_app(monkeypatch: pytest.MonkeyPatch) -> type[FakeApp]:
    FakeApp.instances.clear()
    FakeApp.error = None
    monkeypatch.setattr(main, "AdapterApp", FakeApp)
    return FakeApp


class FakeFn:
    def __init__(self, ret: Any = 1) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.ret = ret

    def __call__(self, *a: Any) -> Any:
        self.calls.append(a)
        return self.ret


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_run_uses_below_normal_priority(
    configured: Path, monkeypatch: pytest.MonkeyPatch, fake_app: type[FakeApp]
) -> None:
    import ctypes

    k32 = type("K", (), {})()
    k32.GetCurrentProcess = FakeFn(1234)
    k32.SetPriorityClass = FakeFn(1)
    monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": k32})())
    assert main.main(["run", "--data-dir", str(configured), "--no-tray"]) == 0
    assert k32.SetPriorityClass.calls == [(1234, 0x4000)]
    assert len(fake_app.instances) == 1


def test_second_instance_exits(
    configured: Path, monkeypatch: pytest.MonkeyPatch, fake_app: type[FakeApp]
) -> None:
    monkeypatch.setattr(main, "set_background_priority", lambda: True)
    name = singleton.mutex_name()
    assert name.startswith("Local\\TokenVaultTest-")  # never the real agent's mutex
    first = singleton.SingleInstance(name)
    assert first.acquire()
    try:
        assert main.main(["run", "--data-dir", str(configured), "--no-tray"]) == 0
        assert fake_app.instances == []  # quietly exited, nothing started
        if singleton.SUPPORTED:
            assert singleton.is_running(name)
    finally:
        first.release()
    assert main.main(["run", "--data-dir", str(configured), "--no-tray"]) == 0
    assert len(fake_app.instances) == 1


def test_run_without_config_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "set_background_priority", lambda: True)
    assert main.main(["run", "--data-dir", str(tmp_path / "nothing"), "--no-tray"]) == 2


def test_run_crash_is_logged_and_recorded(
    configured: Path, monkeypatch: pytest.MonkeyPatch, fake_app: type[FakeApp]
) -> None:
    monkeypatch.setattr(main, "set_background_priority", lambda: True)
    fake_app.error = ValueError("boom at start\nsecond line")
    assert main.main(["run", "--data-dir", str(configured), "--no-tray"]) == 1
    files = DataFiles(configured)
    log_text = files.log.read_text(encoding="utf-8")
    assert "agent crashed" in log_text and "Traceback" in log_text and "boom at start" in log_text
    st = json.loads(files.state.read_text(encoding="utf-8"))
    assert st["state"] == "crashed" and st["last_error"] == "ValueError: boom at start second line"


# ------------------------------------------------------------- tray / menu
def test_tray_menu_has_exactly_four_entries() -> None:
    entries = tray.build_menu(lambda: "x", lambda: None, lambda: None, lambda: None)
    labels = [e.label() for e in entries]
    assert labels == ["x", "Dữ liệu gửi đi là gì?", "Mở thư mục nhật ký", "Giới thiệu"]
    assert [e.enabled for e in entries] == [False, True, True, True]
    strings.set_lang("en")
    labels = [e.label() for e in tray.build_menu(lambda: "x", lambda: None, lambda: None,
                                                 lambda: None)]
    assert labels == ["x", "What is sent?", "Open log folder", "About"]
    for word in ("pause", "quit", "exit", "hide"):
        assert all(word not in lbl.lower() for lbl in labels)
    assert len(list(tray.to_pystray_menu(entries).items)) == 4


def test_tray_status_text() -> None:
    assert tray.status_line("connected", 0) == "Đã kết nối – 0 đang chờ gửi"
    assert tray.status_line("not_configured", 2) == "Chưa cấu hình – 2 đang chờ gửi"
    strings.set_lang("en")
    assert tray.status_line("offline", 5) == "Offline - 5 queued"
    assert tray.status_line("rejected", 1) == "Rejected - 1 queued"


def test_tray_icon_image_is_generated() -> None:
    img = tray.make_icon("connected")
    assert img.size == (64, 64)
    assert tray.make_icon("offline").getpixel((32, 32)) != img.getpixel((32, 32))


def test_tray_what_is_sent_spawns_child_process(
    configured: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv: list[str], **kw: Any) -> None:
            spawned.append(argv)

        def poll(self) -> int | None:
            return None  # still open

    monkeypatch.setattr(appmod.subprocess, "Popen", FakePopen)
    app = AdapterApp(DataFiles(configured), tmp_path / "p", identity=IDENT, tray=False,
                     child_args=["--data-dir", str(configured), "--lang", "vi"])
    app.show_what_is_sent()
    app.show_what_is_sent()  # window still open: no second child
    assert len(spawned) == 1
    argv = spawned[0]
    assert argv[-6:] == ["show", "what-is-sent", "--data-dir", str(configured), "--lang", "vi"]
    assert argv[:-6] == main.paths.self_argv(windowed=True)


def test_agent_run_path_never_imports_tkinter(configured: Path, tmp_path: Path) -> None:
    code = (
        "import sys, pathlib\n"
        "from tokenvault.adapter.app import AdapterApp\n"
        "from tokenvault.adapter.paths import DataFiles\n"
        "from tokenvault.adapter import tray\n"
        "app = AdapterApp(DataFiles(pathlib.Path(sys.argv[1])), pathlib.Path(sys.argv[2]),"
        " tray=False, resolver=lambda: [])\n"
        "app.start(); tray.to_pystray_menu(tray.build_menu(str, print, print, print));"
        " tray.make_icon('connected'); app.stop()\n"
        "print('TK' if 'tkinter' in sys.modules else 'NO_TK')\n"
    )
    env = {**__import__("os").environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
           "TOKENVAULT_MUTEX_NAME": f"Local\\TokenVaultTest-{uuid.uuid4().hex}"}
    out = subprocess.run([sys.executable, "-c", code, str(configured), str(tmp_path / "p")],
                         capture_output=True, text=True, timeout=60, env=env)
    assert out.stdout.strip().endswith("NO_TK"), out.stderr


# ------------------------------------------------- macOS: indicator + consent
def test_run_refuses_to_collect_without_indicator_and_exits_zero(
    configured: Path, monkeypatch: pytest.MonkeyPatch, fake_app: type[FakeApp]
) -> None:
    """macOS privacy rule: no visible indicator -> no collection; exit 0 (no respawn loop)."""
    from tokenvault.adapter.app import IndicatorUnavailable

    monkeypatch.setattr(main, "set_background_priority", lambda: True)
    notes: list[str] = []
    monkeypatch.setattr(main, "_notify", notes.append)
    fake_app.error = IndicatorUnavailable("ImportError: no pystray")
    assert main.main(["run", "--data-dir", str(configured)]) == 0
    st = json.loads(DataFiles(configured).state.read_text(encoding="utf-8"))
    assert st["state"] == "no_indicator" and "pystray" in st["last_error"]
    assert notes and "KHÔNG thu thập" in notes[0]


def test_tray_failure_on_mac_raises_indicator_unavailable(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenvault.adapter.app import IndicatorUnavailable

    class BrokenTray:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def start(self) -> None:
            raise ImportError("pystray")

    monkeypatch.setattr(appmod, "Tray", BrokenTray)
    monkeypatch.setattr(sys, "platform", "darwin")
    app = AdapterApp(DataFiles(configured), configured / "p", identity=IDENT, tray=True,
                     transport_options={})
    with pytest.raises(IndicatorUnavailable):
        app.run()


def test_consent_subcommand_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: dict[str, list[Any]]
) -> None:
    exe = fake_exe(tmp_path)
    for answer, code in ((True, 0), (False, 3), (None, 4)):
        monkeypatch.setattr(consent, "ask_consent", lambda text, a=answer: a)
        assert main.main(["consent", "--from", str(exe)]) == code
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"nothing")
    assert main.main(["consent", "--from", str(plain)]) == 2
    assert calls == {"autostart": [], "launch": []}  # consent alone changes nothing


def test_setup_skips_launch_when_launchd_already_started_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: dict[str, list[Any]]
) -> None:
    monkeypatch.setattr(autostart, "enable", lambda *a, **k: True)
    assert run_setup(tmp_path / "data", fake_exe(tmp_path), "--silent", "--accept") == 0
    assert calls["launch"] == []
