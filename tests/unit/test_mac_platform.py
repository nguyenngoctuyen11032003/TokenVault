"""macOS platform pieces tested on any OS with injected fakes (Task 24)."""

from __future__ import annotations

import plistlib
import py_compile
import queue
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from tokenvault.adapter import autostart, config, consent, paths, singleton
from tokenvault.adapter import platform as plat
from tokenvault.adapter.platform import mac, mac_singleton
from tokenvault.common import secret_store
from tokenvault.common.dpapi import DpapiError
from tokenvault.common.secret_store import MacKeychain, SecretStoreError

SRC = Path(mac.__file__).resolve().parents[2]


class Rec:
    """Fake command runner: records (argv, stdin) and answers from a queue / function."""

    def __init__(self, rc: int = 0, out: bytes = b"", err: bytes = b"") -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.reply = (rc, out, err)

    def __call__(self, argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        self.calls.append((list(argv), stdin))
        return self.reply


class MemStore:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    def set(self, account: str, value: bytes) -> None:
        self.items[account] = value

    def get(self, account: str) -> bytes | None:
        return self.items.get(account)

    def delete(self, account: str) -> None:
        self.items.pop(account, None)


@pytest.fixture
def mem_store() -> Iterator[MemStore]:
    s = MemStore()
    secret_store.set_store(s)
    yield s
    secret_store.set_store(None)


# ------------------------------------------------------------ import safety
def test_mac_modules_compile_and_import_on_this_os() -> None:
    for rel in ("adapter/platform/__init__.py", "adapter/platform/mac.py",
                "adapter/platform/mac_singleton.py", "common/secret_store.py"):
        py_compile.compile(str(SRC / rel), doraise=True)
    assert plat.is_mac("darwin") and not plat.is_mac("win32")


# ------------------------------------------------------------------ paths
def test_mac_paths(tmp_path: Path) -> None:
    assert mac.data_dir(tmp_path) == tmp_path / "Library" / "Application Support" / "TokenVault"
    assert mac.log_dir(tmp_path) == tmp_path / "Library" / "Logs" / "TokenVault"
    assert mac.plist_path(tmp_path) == (
        tmp_path / "Library" / "LaunchAgents" / "vn.ics.tokenvault.adapter.plist")


def test_paths_dispatch_on_darwin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.data_dir({"HOME": str(tmp_path)}) == mac.data_dir(tmp_path)
    assert paths.claude_config_dir({"HOME": str(tmp_path)}) == Path.home() / ".claude"
    root = mac.data_dir()
    assert paths.DataFiles(root).log == mac.log_dir() / "adapter.log"
    custom = tmp_path / "x"
    assert paths.DataFiles(custom).log == custom / "adapter.log"


def test_windows_data_dir_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.data_dir({"LOCALAPPDATA": r"C:\L"}) == Path(r"C:\L") / "TokenVault" / "Adapter"


# -------------------------------------------------------------- LaunchAgent
def test_plist_content() -> None:
    d = plistlib.loads(mac.plist_bytes(["/Apps/TokenVault Adapter", "run", "--data-dir", "/d x"],
                                       Path("/L")))
    assert d["Label"] == "vn.ics.tokenvault.adapter"
    assert d["ProgramArguments"] == ["/Apps/TokenVault Adapter", "run", "--data-dir", "/d x"]
    assert d["RunAtLoad"] is True and d["KeepAlive"] == {"SuccessfulExit": False}
    assert d["StandardOutPath"].endswith("adapter.out.log")
    assert "ProcessType" not in d  # one throttle only: the in-process background policy


def test_launch_agent_enable_disable(tmp_path: Path) -> None:
    run = Rec()
    la = mac.LaunchAgent(tmp_path, uid=501, runner=run)
    assert not la.is_enabled()
    la.enable("/A/bin", ("run",))
    assert la.is_enabled()
    assert plistlib.loads(la.path.read_bytes())["ProgramArguments"] == ["/A/bin", "run"]
    argvs = [c[0] for c in run.calls]
    assert argvs == [["launchctl", "bootout", "gui/501/vn.ics.tokenvault.adapter"],
                     ["launchctl", "bootstrap", "gui/501", str(la.path)]]
    la.disable()
    assert not la.is_enabled()
    assert run.calls[-1][0][:2] == ["launchctl", "bootout"]
    la.disable()  # idempotent


def test_launch_agent_bootstrap_failure_keeps_plist(tmp_path: Path) -> None:
    run = Rec(rc=5, err=b"nope")
    sleeps: list[float] = []
    la = mac.LaunchAgent(tmp_path, uid=1, runner=run, sleep=sleeps.append)
    la.enable("/A/bin")
    assert la.is_enabled()
    boots = [c for c in run.calls if c[0][1] == "bootstrap"]
    assert len(boots) == mac.BOOTSTRAP_TRIES and sleeps == [0.25, 0.5, 1.0, 2.0, 4.0]


def test_launch_agent_bootstrap_retries_until_bootout_finished(tmp_path: Path) -> None:
    results = iter([(0, b"", b""), (5, b"", b"io"), (5, b"", b"io"), (0, b"", b"")])
    calls: list[list[str]] = []

    def run(argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        calls.append(list(argv))
        return next(results)

    sleeps: list[float] = []
    mac.LaunchAgent(tmp_path, uid=7, runner=run, sleep=sleeps.append).enable("/A/bin")
    assert [c[1] for c in calls] == ["bootout", "bootstrap", "bootstrap", "bootstrap"]
    assert sleeps == [0.25, 0.5]


def test_autostart_dispatches_to_launch_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    class Fake:
        def enable(self, exe: str, args: Any) -> None:
            seen.append(("enable", exe, tuple(args)))

        def disable(self) -> None:
            seen.append("disable")

        def is_enabled(self) -> bool:
            return True

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(autostart, "_mac_agent", lambda: Fake())
    autostart.enable("/x", args=("run", "--a"))
    autostart.disable()
    assert autostart.is_enabled() is True
    assert seen == [("enable", "/x", ("run", "--a")), "disable"]


# ------------------------------------------------------------------ consent
def test_consent_osascript_results() -> None:
    ok = Rec(rc=0, out="button returned:Đồng ý, gave up:false\n".encode())
    assert mac.ask_consent("TEXT 'q' \"d\"", "T", "Đồng ý", "Từ chối", runner=ok) is True
    argv = ok.calls[0][0]
    assert argv[0] == "osascript" and "TEXT 'q' \"d\"" in argv  # text is argv, not script source
    assert "TEXT" not in argv[2]
    declined = Rec(rc=0, out=b"button returned:D\n")
    assert mac.ask_consent("t", "T", "A", "D", runner=declined) is False
    cancel = Rec(rc=1, err=b"execution error: User canceled. (-128)")
    assert mac.ask_consent("t", "T", "A", "D", runner=cancel) is False
    assert mac.ask_consent("t", "T", "A", "D", runner=Rec(rc=1, err=b"other")) is None
    assert mac.ask_consent("t", "T", "A", "D", runner=Rec(rc=2)) is None
    # exact match only: a substring of the accept label is not an accept
    assert mac.ask_consent("t", "T", "Accept", "D", runner=Rec(out=b"button returned:Acc")) is None

    def boom(argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        raise FileNotFoundError("osascript")

    assert mac.ask_consent("t", "T", "A", "D", runner=boom) is None


def test_consent_short_dialog_then_view_full_then_accept() -> None:
    full = "FULL TEXT WITH EVERY FIELD"
    replies = iter([(0, b"button returned:View\n", b""), (0, b"button returned:Accept\n", b"")])
    shown: list[list[str]] = []
    opened: list[list[str]] = []
    contents: list[str] = []

    def run(argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        shown.append(list(argv))
        return next(replies)

    def opener(argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        opened.append(list(argv))
        contents.append(Path(argv[-1]).read_text(encoding="utf-8"))
        return 0, b"", b""

    got = mac.ask_consent(full, "T", "Accept", "Decline", "View", short="SHORT",
                          runner=run, opener=opener)
    assert got is True and len(shown) == 2
    assert shown[0][3] == "SHORT" and shown[0][5:] == ["Decline", "View", "Accept"]
    assert opened[0][:2] == ["open", "-t"] and contents == [full]
    assert not Path(opened[0][-1]).exists()  # temp file removed afterwards


def test_short_consent_text_keeps_field_names_not_notes() -> None:
    full = "\n".join([
        "Title", "", "Intro line", "  - model: the model name", "  - project: folder name", "",
        "Not sent", "", "Who", "", "Tray note", "", "Remove note",
    ])
    s = mac.short_consent_text(full, "HINT")
    assert "  - model" in s and "the model name" not in s
    assert "Tray note" not in s and "Remove note" not in s and s.endswith("HINT")


def test_notify_never_raises() -> None:
    rec = Rec()
    mac.notify("msg", "Title", rec)
    assert rec.calls[0][0][0] == "osascript" and "msg" in rec.calls[0][0]

    def boom(argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        raise OSError("x")

    mac.notify("m", "t", boom)


def test_consent_dispatch_on_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(mac, "ask_consent", lambda *a, **k: False)
    assert consent.ask_consent("x") is False


# ---------------------------------------------------------------- priority
def test_background_priority_uses_nice_and_setpriority() -> None:
    calls: list[Any] = []
    assert mac.set_background_priority(lambda n: calls.append(("nice", n)),
                                       lambda *a: calls.append(("sp", a)))
    assert calls == [("nice", 10), ("sp", (4, 0, 0x1000))]

    def bad(*a: Any) -> None:
        raise OSError("denied")

    assert mac.set_background_priority(bad, bad) is False


# ---------------------------------------------------------------- Keychain
class FakeSecurity:
    """Emulates `security -i add-generic-password` and find/delete-generic-password."""

    def __init__(self, *, store: bool = True, exit_ok: bool = True) -> None:
        self.items: dict[str, bytes] = {}
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.store = store
        self.exit_ok = exit_ok

    def __call__(self, argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        self.calls.append((list(argv), stdin))
        if list(argv[:2]) == ["security", "-i"] and stdin is not None:
            parts = stdin.decode().split()
            if self.store:
                acct = parts[parts.index("-a") + 1].strip('"')
                self.items[acct] = bytes.fromhex(parts[parts.index("-w") + 1])
            return (0 if self.exit_ok else 1), b"", b""
        acct = argv[list(argv).index("-a") + 1]
        if argv[1] == "find-generic-password":
            v = self.items.get(acct)
            return (44, b"", b"") if v is None else (0, v.hex().encode() + b"\n", b"")
        if argv[1] == "delete-generic-password":
            return (0, b"", b"") if self.items.pop(acct, None) is not None else (44, b"", b"")
        return 1, b"", b""


def test_keychain_set_keeps_secret_out_of_argv_and_verifies_by_readback() -> None:
    run = FakeSecurity()
    MacKeychain(run).set("acct:device.key", b"SuperSecret-123")
    argv, stdin = run.calls[0]
    assert argv == ["security", "-i"]
    assert stdin is not None and stdin.startswith(b"add-generic-password -U -s ")
    assert b"SuperSecret" not in stdin  # hex-encoded, no quoting issues
    assert stdin.rstrip().endswith(b"SuperSecret-123".hex().encode())
    assert run.calls[1][0][1] == "find-generic-password"  # read back
    assert all(b"SuperSecret" not in " ".join(c[0]).encode() for c in run.calls)


def test_keychain_set_fails_when_readback_missing_or_different() -> None:
    # `security -i` exits 0 but stored nothing (its messages are not relied on)
    with pytest.raises(SecretStoreError):
        MacKeychain(FakeSecurity(store=False)).set("a", b"v")
    # exit status failure alone does not matter either: the read-back decides
    MacKeychain(FakeSecurity(exit_ok=False)).set("a", b"v")
    inner = FakeSecurity()

    def wrong(argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        rc, out, err = inner(argv, stdin)
        if argv[1] == "find-generic-password" and rc == 0:
            return 0, b"00", b""
        return rc, out, err

    with pytest.raises(SecretStoreError, match="mismatch"):
        MacKeychain(wrong).set("a", b"v")


def test_keychain_get_delete_command_lines_and_results() -> None:
    run = Rec(out=b"SuperSecret".hex().encode() + b"\n")
    kc = MacKeychain(run)
    assert kc.get("a") == b"SuperSecret"
    assert run.calls[0][0] == ["security", "find-generic-password", "-s",
                               "vn.ics.tokenvault.adapter", "-a", "a", "-w"]
    assert MacKeychain(Rec(rc=44)).get("a") is None
    with pytest.raises(SecretStoreError):
        MacKeychain(Rec(rc=1)).get("a")
    with pytest.raises(SecretStoreError):
        MacKeychain(Rec(out=b"zz")).get("a")
    MacKeychain(Rec(rc=44)).delete("a")
    with pytest.raises(SecretStoreError):
        MacKeychain(Rec(rc=1)).delete("a")


def test_keychain_fails_closed_and_is_a_dpapi_error() -> None:
    for run in (Rec(rc=1), Rec(rc=0, err=b"security: error: SecKeychain denied")):
        with pytest.raises(DpapiError):
            MacKeychain(run).set("a", b"v")

    def boom(argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        raise subprocess.TimeoutExpired("security", 1)

    with pytest.raises(SecretStoreError):
        MacKeychain(boom).set("a", b"v")
    with pytest.raises(SecretStoreError):
        MacKeychain(FakeSecurity()).set('bad"acct', b"v")


def test_secret_roundtrip_via_config_uses_marker_file(
    tmp_path: Path, mem_store: MemStore
) -> None:
    p = tmp_path / "device.key"
    config.write_secret(p, "k-very-secret")
    blob = p.read_bytes()
    assert blob.startswith(b"TVK1:") and b"k-very-secret" not in blob
    assert config.read_secret(p) == "k-very-secret" and config.secret_state(p) == "ok"
    config.delete_file(p)
    assert not p.exists() and mem_store.items == {}
    assert config.read_secret(p) is None


def test_missing_keychain_item_is_unusable_not_a_crash(
    tmp_path: Path, mem_store: MemStore
) -> None:
    p = tmp_path / "device.key"
    config.write_secret(p, "v")
    mem_store.items.clear()
    assert config.read_secret(p) is None and config.secret_state(p) == "unreadable"


def test_write_secret_fails_closed_without_file(tmp_path: Path) -> None:
    class Failing(MemStore):
        def set(self, account: str, value: bytes) -> None:
            raise SecretStoreError("denied")

    secret_store.set_store(Failing())
    try:
        with pytest.raises(DpapiError):
            config.write_secret(tmp_path / "enroll.secret", "s")
    finally:
        secret_store.set_store(None)
    assert not (tmp_path / "enroll.secret").exists()


def test_probe_uses_store_or_dpapi(mem_store: MemStore) -> None:
    secret_store.probe()
    assert mem_store.items == {}  # probe item removed again
    secret_store.set_store(None)
    # Windows path unchanged: delegates to dpapi.protect (plain fallback elsewhere)

    if sys.platform != "win32":
        with pytest.raises(DpapiError):
            secret_store.probe()


def test_account_is_stable_and_directory_scoped(tmp_path: Path) -> None:
    a = secret_store.account_for(tmp_path / "a" / "device.key")
    assert a == secret_store.account_for(tmp_path / "a" / "device.key")
    assert a != secret_store.account_for(tmp_path / "b" / "device.key")
    assert secret_store.marker_account(b"TVK1:" + a.encode()) == a
    assert secret_store.marker_account(b"TVD1:xx") is None


# --------------------------------------------------------------- singleton
class FakeFcntl:
    LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

    def __init__(self) -> None:
        self.fd_path: dict[int, str] = {}


class PathFcntl(FakeFcntl):
    """Locks are per file path, like real flock on separate open file descriptions."""

    def __init__(self) -> None:
        super().__init__()
        self.owner_by_path: dict[str, int] = {}

    def flock(self, fd: int, op: int) -> None:
        path = self._path(fd)
        if op & self.LOCK_UN:
            if self.owner_by_path.get(path) == fd:
                self.owner_by_path.pop(path)
            return
        cur = self.owner_by_path.get(path)
        if cur is not None and cur != fd:
            raise BlockingIOError("held")
        self.owner_by_path[path] = fd

    def _path(self, fd: int) -> str:
        return self.fd_path[fd]


def _install_fake_open(monkeypatch: pytest.MonkeyPatch, fc: PathFcntl) -> None:
    real_open = open

    def fake_open(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        fh = real_open(file, mode, *a, **k)
        fc.fd_path[fh.fileno()] = str(file)
        return fh

    monkeypatch.setattr(mac_singleton, "open", fake_open, raising=False)


def test_flock_single_instance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fc = PathFcntl()
    _install_fake_open(monkeypatch, fc)
    lock = tmp_path / "d" / "adapter.lock"
    a = mac_singleton.FlockInstance(lock, fc)
    b = mac_singleton.FlockInstance(lock, fc)
    assert a.acquire() is True and a.acquire() is True
    assert mac_singleton.read_pid(lock) is not None
    assert mac_singleton.is_running(lock, fc) is True
    assert b.acquire() is False
    a.release()
    assert mac_singleton.is_running(lock, fc) is False
    assert b.acquire() is True
    b.release()


def test_signal_reload_sends_sigusr1_to_lock_holder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fc = PathFcntl()
    _install_fake_open(monkeypatch, fc)
    lock = tmp_path / "adapter.lock"
    sent: list[tuple[int, int]] = []
    assert mac_singleton.signal_reload(lock, lambda p, s: sent.append((p, s)), fc) is False
    holder = mac_singleton.FlockInstance(lock, fc)
    assert holder.acquire()
    assert mac_singleton.signal_reload(lock, lambda p, s: sent.append((p, s)), fc) is True
    assert len(sent) == 1 and sent[0][0] == mac_singleton.read_pid(lock)
    holder.release()
    assert mac_singleton.signal_reload(lock, lambda p, s: sent.append((p, s)), fc) is False


class FakeSignals:
    """sigwait/mask/wake fakes: signals are queued by the test, like a pending signal."""

    def __init__(self) -> None:
        self.q: queue.Queue[int] = queue.Queue()
        self.masks: list[Any] = []

    def sigwait(self, wanted: Any) -> int:
        return self.q.get(timeout=5)

    def mask(self, how: int, sigs: Any) -> None:
        self.masks.append((how, set(sigs)))

    def wake(self) -> None:
        self.q.put(mac_singleton._sig("SIGUSR2", 31))


def _listener(fs: FakeSignals, cb: Any) -> mac_singleton.SignalReloadListener:
    return mac_singleton.SignalReloadListener(cb, fs.sigwait, fs.mask, fs.wake)


def test_signal_listener_handles_usr1_and_term_off_the_main_thread() -> None:
    for sig in (mac_singleton._sig("SIGUSR1", 30), mac_singleton._sig("SIGTERM", 15)):
        fs = FakeSignals()
        fired = threading.Event()
        where: list[str] = []

        def cb(fired: threading.Event = fired, where: list[str] = where) -> None:
            where.append(threading.current_thread().name)
            fired.set()

        lst = _listener(fs, cb)
        assert lst.start() is True
        assert fs.masks and mac_singleton._sig("SIGUSR1", 30) in fs.masks[0][1]
        assert mac_singleton._sig("SIGTERM", 15) in fs.masks[0][1]
        fs.q.put(sig)
        assert fired.wait(2) and where == ["tv-signals"]  # not the (Cocoa-owned) main thread
        lst.stop()


def test_signal_listener_stop_wakes_the_thread_without_callback() -> None:
    fs = FakeSignals()
    called: list[int] = []
    lst = _listener(fs, lambda: called.append(1))
    assert lst.start()
    t = lst._thread
    assert t is not None
    lst.stop()
    assert not t.is_alive() and called == []


def test_signal_listener_unavailable_without_sigwait() -> None:
    lst = mac_singleton.SignalReloadListener(lambda: None, mask=lambda *a: None)
    lst._sigwait = None
    assert lst.start() is False


def test_block_signals_blocks_usr1_usr2_term() -> None:
    got: list[Any] = []
    mac_singleton.block_signals(lambda how, sigs: got.append(set(sigs)))
    assert got == [{mac_singleton._sig("SIGUSR1", 30), mac_singleton._sig("SIGUSR2", 31),
                    mac_singleton._sig("SIGTERM", 15)}]


def test_singleton_module_dispatches_on_darwin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("TOKENVAULT_MUTEX_NAME", raising=False)
    assert singleton.mutex_name().endswith("adapter.lock")
    lock = str(tmp_path / "x.lock")
    assert singleton.reload_event_name(lock) == lock + "-reload"
    assert singleton._mac_lock_for(lock + "-reload") == lock
    assert singleton.is_running(str(tmp_path / "missing.lock")) is False


def test_enable_reports_whether_launchd_started_the_agent(tmp_path: Path) -> None:
    assert mac.LaunchAgent(tmp_path, uid=1, runner=Rec()).enable("/A/bin") is True
    assert mac.LaunchAgent(tmp_path, uid=1, runner=Rec(rc=5), sleep=lambda s: None
                           ).enable("/A/bin") is False


def test_consent_dialog_has_no_timeout_but_keychain_runner_keeps_one(
        monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def fake_run(argv: Any, **kw: Any) -> subprocess.CompletedProcess[bytes]:
        seen.append(kw["timeout"])
        return subprocess.CompletedProcess(argv, 0, b"button returned:OK", b"")

    monkeypatch.setattr(mac.subprocess, "run", fake_run)
    assert mac.ask_consent("t", "T", "OK", "No") is True
    assert seen == [None]
    mac.run_command(["x"])
    assert seen[-1] == mac.RUN_TIMEOUT_S
