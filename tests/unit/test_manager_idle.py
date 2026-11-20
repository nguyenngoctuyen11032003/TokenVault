"""Task 30: a hidden Manager window does no live work; the UI loads ECharts lazily."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tokenvault.manager.live import LivePusher
from tokenvault.manager.window_shell import WindowShell

UI = Path(__file__).resolve().parents[2] / "src" / "tokenvault" / "ui"


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_paused_pusher_never_evaluates_and_pushes_once_on_resume() -> None:
    sent: list[str] = []
    built = {"n": 0}

    def payload() -> dict[str, int]:
        built["n"] += 1
        return {"n": built["n"]}

    clock = FakeClock()
    p = LivePusher(sent.append, payload, max_per_s=2, clock=clock)
    p.set_paused(True)
    for _ in range(20):
        p.notify()
        clock.t += 1
        assert p.pump() is None
    assert sent == [] and built["n"] == 0  # not even the payload is built while hidden
    p.set_paused(False)
    p.pump()
    assert len(sent) == 1 and '"n":1' in sent[0]


def test_paused_pusher_thread_sleeps_until_resumed() -> None:
    sent: list[str] = []
    p = LivePusher(sent.append, lambda: {}, max_per_s=50)
    p.set_paused(True)
    p.start()
    try:
        for _ in range(10):
            p.notify()
        time.sleep(0.3)
        assert sent == []
        p.set_paused(False)
        deadline = time.monotonic() + 3
        while not sent and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(sent) == 1
    finally:
        p.stop()


class FakeWindow:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def hide(self) -> None:
        self.calls.append("hide")

    def show(self) -> None:
        self.calls.append("show")

    def restore(self) -> None:
        self.calls.append("restore")


def test_shell_hide_and_show_report_visibility() -> None:
    shell = WindowShell.__new__(WindowShell)
    seen: list[bool] = []
    shell._window = FakeWindow()
    shell._destroyed = False
    shell._hidden = False
    shell.geometry = None
    shell._probe_state = "none"
    shell.on_visibility = seen.append
    shell.hide()
    assert shell._hidden is True
    shell.show()
    assert seen == [False, True] and shell._hidden is False


def test_hidden_window_asks_webview2_for_low_memory_and_suspend(
        monkeypatch: Any) -> None:
    import sys
    import types

    from tokenvault.manager import webview_tuning

    core_mod = types.ModuleType("Microsoft.Web.WebView2.Core")
    core_mod.CoreWebView2MemoryUsageTargetLevel = types.SimpleNamespace(  # type: ignore[attr-defined]
        Low="low", Normal="normal")
    system = types.ModuleType("System")
    system.Action = lambda fn: fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "Microsoft.Web.WebView2.Core", core_mod)
    monkeypatch.setitem(sys.modules, "System", system)

    class Core:
        MemoryUsageTargetLevel = "normal"
        suspended = 0

        def TrySuspendAsync(self) -> None:  # noqa: N802 - .NET name
            self.suspended += 1

    core = Core()
    form = types.SimpleNamespace(InvokeRequired=False, browser=types.SimpleNamespace(
        webview=types.SimpleNamespace(CoreWebView2=core)))
    win = types.SimpleNamespace(native=form)
    assert webview_tuning.set_background(win, True) is True
    # never suspended: a suspended page would block evaluate_js forever
    assert core.MemoryUsageTargetLevel == "low" and core.suspended == 0
    assert webview_tuning.set_background(win, False) is True
    assert core.MemoryUsageTargetLevel == "normal" and core.suspended == 0
    assert webview_tuning.set_background(types.SimpleNamespace(native=None), True) is False


def test_index_does_not_load_echarts_eagerly() -> None:
    """The 1 MB bundle is loaded by charts-core.loadECharts() on the first chart page."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "echarts" not in html
    assert "loadECharts" in (UI / "js" / "charts-core.js").read_text(encoding="utf-8")


def test_router_gates_live_pushes() -> None:
    js: Any = (UI / "js" / "router.js").read_text(encoding="utf-8")
    assert "gateLive(" in js
    gate = (UI / "js" / "livegate.js").read_text(encoding="utf-8")
    assert "document.hidden" in gate and "visibilitychange" in gate
