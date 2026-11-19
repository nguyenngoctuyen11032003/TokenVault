"""TokenVault Manager entry point.

CLI (``TokenVault-Manager.exe`` / ``python -m tokenvault.manager.app``)::

    (no args)                    run the Manager: tray + window + ingest server
      --minimized                start hidden in the tray (used by start-with-Windows)
      --data-dir DIR             data folder (default %APPDATA%\\TokenVault\\Manager)
      --template EXE             adapter template (env TOKENVAULT_TEMPLATE; default
                                 <install dir>\\adapter-setup-template.exe)
    --devserver [--port N]       UI in a normal browser against demo data (default port 8765)
    --selftest                   headless start/exercise/stop of every component; exit 0 = ok
    --selftest-window [--screenshot PNG] [--seconds S]
                                 open the real window on demo data, screenshot the desktop, exit
    firewall --ensure|--check [--port N] [--program EXE]
                                 add (elevated) / check the inbound firewall rules; prints JSON
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any

from tokenvault.manager import firewall
from tokenvault.manager.paths import (
    ManagerPaths,
    default_data_dir,
    pricing_path,
    template_path,
    ui_dir,
)

log = logging.getLogger("tokenvault.manager")


def setup_logging(path: Path | None, verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")
    if path is not None:
        h = logging.handlers.RotatingFileHandler(path, maxBytes=1 << 20, backupCount=3,
                                                 encoding="utf-8")
        h.setFormatter(fmt)
        root.addHandler(h)
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        s = logging.StreamHandler()
        s.setFormatter(fmt)
        root.addHandler(s)


def message_box(text: str, error: bool = True) -> None:
    """Native message box (falls back to stderr when there is no GUI)."""
    print(text, file=sys.stderr)
    if sys.platform == "win32" and not os.environ.get("TOKENVAULT_NO_MSGBOX"):
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, "TokenVault Manager",
                                             0x10 if error else 0x40)


def reveal_explorer(path: Path) -> None:
    subprocess.Popen(["explorer", f"/select,{path}"])  # noqa: S603,S607 - fixed program


class SingleInstance:
    """Exclusive lock on ``manager.lock`` (released by the OS if the process dies)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._f: IO[bytes] | None = None

    def acquire(self) -> bool:
        f = open(self.path, "a+b")  # noqa: SIM115 - held for the process lifetime
        try:
            if sys.platform == "win32":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - non-Windows dev boxes
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._f = f
        return True

    def release(self) -> None:
        if self._f is not None:
            with contextlib.suppress(OSError):
                self._f.close()
            self._f = None


def run_gui(a: argparse.Namespace, *, demo: bool = False, screenshot: Path | None = None,
            seconds: float = 6.0) -> int:
    from tokenvault.manager import api_manage, win_autostart
    from tokenvault.manager.api import Api, dispatch
    from tokenvault.manager.lifecycle import Manager, ManagerConfig, StartupError
    from tokenvault.manager.live import LivePusher
    from tokenvault.manager.settings_store import SettingsStore
    from tokenvault.manager.tray import Tray
    from tokenvault.manager.webserver import UiServer
    from tokenvault.manager.window_shell import WindowShell

    data = Path(a.data_dir) if a.data_dir else default_data_dir()
    if demo and not a.data_dir:
        data = Path(tempfile.mkdtemp(prefix="tokenvault-window-"))
    try:
        paths = ManagerPaths(data).create()
    except OSError as e:
        message_box(f"TokenVault Manager cannot create its data folder {data}:\n{e}")
        return 2
    setup_logging(paths.log)
    lock = SingleInstance(paths.lock)
    if not lock.acquire():
        message_box("TokenVault Manager is already running (see the tray icon).", error=False)
        return 0
    pre = SettingsStore(paths.settings).get()
    if pre["transparent_window"] and pre["transparent_pending"]:
        log.warning("the transparent window did not load last time: switching to opaque")
        SettingsStore(paths.settings).update_internal(transparent_window=False,
                                                      transparent_pending=False)
    smoke = screenshot is not None
    cfg = ManagerConfig(
        data_dir=paths.root, template=template_path(a.template), pricing=pricing_path(),
        ingest_host="127.0.0.1" if smoke else "0.0.0.0", ingest_port=0 if smoke else None,
        discovery=not smoke, firewall_check=firewall.check_rules, reveal=reveal_explorer,
        autostart=win_autostart.set_autostart,
    )
    m = Manager(cfg)
    try:
        m.start()
    except StartupError as e:
        lock.release()
        message_box(f"TokenVault Manager cannot start:\n\n{e}")
        return 2
    if demo:
        from tokenvault.manager.demo_data import seed_now

        info = seed_now(m.store, m.enrollment, m.tz_offset_minutes)
        m.presence_source = lambda: set(info.online_ids)
        m.touch_presence()
    api = Api(m)
    ui = UiServer(ui_dir(), api_call=lambda meth, args: dispatch(api, meth, args))
    ui.start()
    shell = WindowShell(m.settings, ui.entry_url, api, hidden=bool(a.minimized))
    pusher = LivePusher(shell.evaluate, lambda: api_manage.live_payload(m), max_per_s=2)
    shell.on_page_loaded = pusher.notify  # push the current live state on every page load
    m.on_change = pusher.notify
    state_pusher = LivePusher(shell.evaluate, lambda: api.window_state(), max_per_s=10,
                              fn="onWindowState")
    shell.on_window_state = state_pusher.notify

    def window_gone() -> None:  # never evaluate JS on a destroyed window
        pusher.close()
        state_pusher.close()

    shell.on_closed = window_gone
    api._set_window(shell)

    def quit_app() -> None:
        pusher.close()
        state_pusher.close()
        shell.quit()

    def open_installer() -> None:
        shell.show()
        shell.navigate("installer")

    tray = Tray(shell.show, open_installer, quit_app)
    report: dict[str, Any] = {}
    try:
        import webview

        shell.create(bool(m.settings.get()["transparent_window"]))
        if not smoke:
            tray.start()
        pusher.start()
        state_pusher.start()

        def started() -> None:
            if smoke:
                threading.Thread(target=_smoke, args=(shell, screenshot, seconds, report,
                                                      quit_app), daemon=True).start()

        webview.start(started, private_mode=False, storage_path=str(paths.webview))
    except Exception as e:  # noqa: BLE001 - e.g. WebView2 runtime missing
        log.exception("window failed")
        message_box(f"TokenVault Manager window failed:\n\n{e}\n\n"
                    "Is the Microsoft Edge WebView2 Runtime installed?")
        return_code = 3
    else:
        return_code = 0
    finally:
        pusher.stop()
        state_pusher.stop()
        tray.stop()
        ui.stop()
        m.stop()  # stops the ingest server cleanly
        lock.release()
    if smoke:
        print(json.dumps(report, ensure_ascii=False, indent=1), flush=True)
        return 0 if report.get("screenshot") and return_code == 0 else 4
    return return_code


def _smoke(shell: Any, screenshot: Path | None, seconds: float, report: dict[str, Any],
           quit_app: Any) -> None:
    try:
        report["loaded"] = shell.loaded.wait(20)
        time.sleep(seconds)
        probe = ("JSON.stringify({ready: document.documentElement.dataset.tvReady || null, "
                 "route: (document.querySelector('#tv-view')||{}).dataset?.route || null, "
                 "state: (document.querySelector('#tv-view')||{}).dataset?.routeState || null, "
                 "bridge: !!(window.pywebview && window.pywebview.api && "
                 "window.pywebview.api.overview), opaque: document.documentElement.className})")
        with contextlib.suppress(Exception):
            report["page"] = json.loads(shell.evaluate(probe))
        if screenshot is not None:
            from PIL import ImageGrab

            screenshot.parent.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab(all_screens=True).save(screenshot)
            report["screenshot"] = str(screenshot)
        report["transparent"] = shell.transparent
        report["transparency_ok"] = shell.transparency_ok
        geo = shell.geometry
        if geo is not None and screenshot is not None:
            from PIL import ImageGrab

            def short(st: dict[str, Any]) -> dict[str, Any]:
                return {k: st[k] for k in ("maximized", "fullscreen", "x", "y", "width",
                                           "height")}

            steps: dict[str, Any] = {"initial": short(geo.state())}
            for name, act in (("maximize", geo.maximize), ("restore", geo.restore),
                              ("fullscreen", lambda: geo.set_fullscreen(True)),
                              ("fullscreen_off", lambda: geo.set_fullscreen(False)),
                              ("set_bounds", lambda: geo.set_bounds(60, 40, 1150, 740))):
                steps[name] = short(act())
                time.sleep(0.8)
                steps[name]["native"] = geo.get_bounds()
                if name in ("maximize", "set_bounds"):
                    shot = screenshot.with_name(f"{screenshot.stem}-{name}.png")
                    ImageGrab.grab(all_screens=True).save(shot)
            steps["screen"] = geo.state()["screen"]
            report["geometry"] = steps
    except Exception as e:  # noqa: BLE001
        report["error"] = f"{type(e).__name__}: {e}"
    finally:
        quit_app()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "firewall":
        return firewall.main(args[1:])
    ap = argparse.ArgumentParser(prog="TokenVault-Manager", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--devserver", action="store_true")
    mode.add_argument("--selftest", action="store_true")
    mode.add_argument("--selftest-window", action="store_true")
    ap.add_argument("--port", type=int, default=None, help="dev server port (default 8765)")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--template", default=None)
    ap.add_argument("--minimized", action="store_true")
    ap.add_argument("--screenshot", default=None)
    ap.add_argument("--seconds", type=float, default=6.0)
    a = ap.parse_args(args)
    if a.devserver:
        from tokenvault.manager import devserver

        return devserver.run(port=devserver.DEFAULT_PORT if a.port is None else a.port,
                             data_dir=Path(a.data_dir) if a.data_dir else None,
                             template=Path(a.template) if a.template else None)
    if a.selftest:
        from tokenvault.manager import selftest

        logging.basicConfig(level=logging.WARNING)
        return selftest.run(Path(a.template) if a.template else None)
    if a.selftest_window:
        shot = Path(a.screenshot) if a.screenshot else Path(tempfile.gettempdir()) / "tv-window.png"
        return run_gui(a, demo=True, screenshot=shot, seconds=a.seconds)
    return run_gui(a)


if __name__ == "__main__":
    sys.exit(main())
