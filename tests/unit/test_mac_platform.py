"""macOS platform pieces tested on any OS with injected fakes (Task 24)."""

from __future__ import annotations

import plistlib
import py_compile
import signal
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
    la = mac.LaunchAgent(tmp_path, uid=1, runner=Rec(rc=5, err=b"nope"))
    la.enable("/A/bin")
    assert la.is_enabled()


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
    ok = Rec(rc=0, out=b"button returned:Dong y\n")
    assert mac.ask_consent("TEXT 'q' \"d\"", "T", "Dong y", "Tu choi", ok) is True
    argv = ok.calls[0][0]
    assert argv[0] == "osascript" and "TEXT 'q' \"d\"" in argv  # text is argv, not script source
    assert "TEXT" not in argv[2]
    assert mac.ask_consent("t", "T", "A", "D", Rec(rc=1)) is False
    assert mac.ask_consent("t", "T", "A", "D", Rec(rc=2)) is None

    def boom(argv: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        raise FileNotFoundError("osascript")

    assert mac.ask_consent("t", "T", "A", "D", boom) is None


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
def test_keychain_set_keeps_secret_out_of_argv() -> None:
    run = Rec()
    MacKeychain(run).set("acct:device.key", b"SuperSecret-123")
    argv, stdin = run.calls[0]
    assert argv == ["security", "-i"]
    assert b"SuperSecret" not in b" ".join(a.encode() for a in argv)
    assert stdin is not None and stdin.startswith(b"add-generic-password -U -s ")
    assert b"SuperSecret" not in stdin  # hex-encoded, no quoting issues
    assert stdin.rstrip().endswith(b"SuperSecret-123".hex().encode())


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
        MacKeychain(Rec()).set('bad"acct', b"v")


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


def test_signal_reload_listener_calls_back_once() -> None:
    handlers: dict[int, Any] = {}
    fired = threading.Event()
    n: list[int] = []

    def install(sig: int, h: Any) -> Any:
        handlers[sig] = h
        return signal.SIG_DFL

    def on_reload() -> None:
        n.append(1)
        fired.set()

    lst = mac_singleton.SignalReloadListener(on_reload, install)
    assert lst.start() is True
    (handler,) = handlers.values()
    handler(0, None)
    handler(0, None)
    assert fired.wait(2) and n == [1]
    lst.stop()


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
