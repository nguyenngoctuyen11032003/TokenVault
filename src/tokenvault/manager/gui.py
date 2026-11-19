"""Manager GUI run loop: boot components, window + tray + live pushes, clean shutdown.

``boot()`` starts Manager, Api and the UI server; ANY exception there is logged with
its traceback, everything already started is stopped, and :class:`BootError` (a short
user message) is raised -> ``run_gui`` shows a message box and returns 2.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from tokenvault.manager import api_manage, firewall, win_autostart
from tokenvault.manager.api import Api, dispatch
from tokenvault.manager.app_support import message_box, reveal_explorer, setup_logging
from tokenvault.manager.instance import InstanceSignal, SingleInstance, event_name, second_launch
from tokenvault.manager.lifecycle import Manager, ManagerConfig, StartupError
from tokenvault.manager.live import LivePusher
from tokenvault.manager.paths import (
    DemoDirError,
    ManagerPaths,
    default_data_dir,
    prepare_demo_dir,
    pricing_path,
    template_path,
    ui_dir,
)
from tokenvault.manager.webserver import UiServer

log = logging.getLogger("tokenvault.manager.gui")


class BootError(RuntimeError):
    """Startup failed (message is user-presentable)."""


def boot(cfg: ManagerConfig, *, demo: bool = False) -> tuple[Manager, Api, UiServer]:
    m = Manager(cfg)
    ui: UiServer | None = None
    stage = "manager"
    try:
        m.start()
        if demo:
            from tokenvault.manager.demo_data import seed_now

            stage = "demo data"
            info = seed_now(m.store, m.enrollment, m.tz_offset_minutes)
            m.presence_source = lambda: set(info.online_ids)
            m.touch_presence()
        stage = "api"
        api = Api(m)
        stage = "ui server"
        ui = UiServer(ui_dir(), api_call=lambda meth, args: dispatch(api, meth, args))
        ui.start()
        return m, api, ui
    except StartupError as e:
        log.error("startup failed (%s): %s", stage, e)
        _stop_quietly(m, ui)
        raise BootError(str(e)) from e
    except Exception as e:
        log.exception("startup failed (%s)", stage)
        _stop_quietly(m, ui)
        raise BootError(f"unexpected error while starting the {stage}: "
                        f"{type(e).__name__}: {e}") from e


def _stop_quietly(m: Manager, ui: UiServer | None) -> None:
    if ui is not None:
        with contextlib.suppress(Exception):
            ui.stop()
    try:
        m.stop()
    except Exception:  # noqa: BLE001
        log.exception("stop after failed startup failed")


def run_gui(a: argparse.Namespace, *, demo: bool = False, screenshot: Path | None = None,
            seconds: float = 6.0) -> int:
    from tokenvault.manager.tray import Tray
    from tokenvault.manager.window_shell import WindowShell

    try:
        if demo:
            data = (prepare_demo_dir(a.data_dir) if a.data_dir
                    else prepare_demo_dir(tempfile.mkdtemp(prefix="tokenvault-window-")))
        else:
            data = Path(a.data_dir) if a.data_dir else default_data_dir()
        paths = ManagerPaths(data).create()
    except (OSError, DemoDirError) as e:
        message_box(f"TokenVault Manager cannot use the data folder:\n{e}")
        return 2
    setup_logging(paths.log)
    lock = SingleInstance(paths.lock)
    signal = InstanceSignal(event_name(paths.root))
    other = second_launch(lock, signal)
    if other is not None:
        log.info("another Manager is running (%s)", other)
        if other == "running":
            message_box("TokenVault Manager is already running (see the tray icon).", False)
        return 0
    smoke = screenshot is not None
    cfg = ManagerConfig(
        data_dir=paths.root, template=template_path(a.template), pricing=pricing_path(),
        ingest_host="127.0.0.1" if smoke else "0.0.0.0", ingest_port=0 if smoke else None,
        discovery=not smoke, firewall_check=firewall.check_rules, reveal=reveal_explorer,
        autostart=win_autostart.set_autostart,
    )
    try:
        m, api, ui = boot(cfg, demo=demo)
    except BootError as e:
        lock.release()
        message_box(f"TokenVault Manager cannot start:\n\n{e}\n\nDetails: {paths.log}")
        return 2
    shell = WindowShell(m.settings, ui.entry_url, api, hidden=bool(a.minimized))
    pusher = LivePusher(shell.evaluate, lambda: api_manage.live_payload(m), max_per_s=2)
    state_pusher = LivePusher(shell.evaluate, lambda: api.window_state(), max_per_s=10,
                              fn="onWindowState")
    shell.on_page_loaded = pusher.notify  # push the current live state on every page load
    shell.on_window_state = state_pusher.notify
    m.on_change = pusher.notify

    def window_gone() -> None:  # never evaluate JS on a destroyed window
        pusher.close()
        state_pusher.close()

    shell.on_closed = window_gone
    shell.on_session_end = window_gone  # Windows logs off: the form closes by itself
    api._set_window(shell)

    def quit_app() -> None:
        window_gone()
        shell.quit()

    def open_installer() -> None:
        shell.show()
        shell.navigate("installer")

    tray = Tray(shell.show, open_installer, quit_app)
    signal.listen(shell.show)  # a second launch brings this window to the front
    report: dict[str, Any] = {}
    return_code = 0
    try:
        import webview

        shell.create()
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
    finally:
        signal.close()
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


def _grab(path: Path) -> str:
    from PIL import ImageGrab

    path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(all_screens=True).save(path)
    return str(path)


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
            report["screenshot"] = _grab(screenshot)
        report["shape"] = shell.shape
        report["transparency_ok"] = shell.transparency_ok
        geo = shell.geometry
        if geo is not None and screenshot is not None:
            def short(st: dict[str, Any]) -> dict[str, Any]:
                return {k: st[k] for k in ("maximized", "fullscreen", "native_region", "x", "y",
                                           "width", "height")}

            steps: dict[str, Any] = {"initial": short(geo.state())}
            for name, act in (("maximize", geo.maximize), ("restore", geo.restore),
                              ("fullscreen", lambda: geo.set_fullscreen(True)),
                              ("fullscreen_off", lambda: geo.set_fullscreen(False)),
                              ("set_bounds", lambda: geo.set_bounds(60, 40, 1150, 740))):
                steps[name] = short(act())
                time.sleep(0.8)
                steps[name]["native"] = geo.get_bounds()
                if name in ("maximize", "set_bounds"):
                    _grab(screenshot.with_name(f"{screenshot.stem}-{name}.png"))
            steps["screen"] = geo.state()["screen"]
            report["geometry"] = steps
    except Exception as e:  # noqa: BLE001
        report["error"] = f"{type(e).__name__}: {e}"
    finally:
        quit_app()
