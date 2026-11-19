"""Window shell decisions: close vs hide (session end), window shape, probe verdict."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tokenvault.manager.settings_store import SettingsStore
from tokenvault.manager.window_shell import (
    WindowShell,
    decide_shape,
    probe_verdict,
    should_cancel_close,
)


class FakeWindow:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def hide(self) -> None:
        self.calls.append("hide")

    def destroy(self) -> None:
        self.calls.append("destroy")


class Args:
    def __init__(self, reason: str) -> None:
        self.CloseReason = reason
        self.Cancel = True  # pywebview's handler already cancelled it (hide to tray)


def make(tmp_path: Path) -> tuple[WindowShell, FakeWindow, list[str]]:
    shell = WindowShell(SettingsStore(tmp_path / "s.json"), "http://x/", object())
    win = FakeWindow()
    shell._window = win
    ended: list[str] = []
    shell.on_session_end = lambda: ended.append("end")
    return shell, win, ended


@pytest.mark.parametrize(
    ("quitting", "ending", "reason", "cancel"),
    [
        (False, False, None, True),
        (False, False, "UserClosing", True),
        (False, False, "WindowsShutDown", False),
        (False, False, "TaskManagerClosing", False),
        (False, False, "ApplicationExitCall", False),
        (True, False, "UserClosing", False),
        (False, True, None, False),
    ],
)
def test_should_cancel_close(quitting: bool, ending: bool, reason: str | None,
                             cancel: bool) -> None:
    assert should_cancel_close(quitting, ending, reason) is cancel


def test_user_close_hides_to_tray(tmp_path: Path) -> None:
    shell, win, ended = make(tmp_path)
    assert shell._on_closing() is False
    assert win.calls == ["hide"] and ended == []
    args = Args("UserClosing")
    shell._on_form_closing(args)
    assert args.Cancel is True and ended == []


def test_windows_shutdown_is_never_cancelled(tmp_path: Path) -> None:
    shell, win, ended = make(tmp_path)
    saved: list[Any] = []
    shell._persist._fn = saved.append  # type: ignore[assignment]
    shell._persist.push({"x": 1})
    shell._on_closing()  # pywebview's closing event comes first and cancels...
    args = Args("WindowsShutDown")
    shell._on_form_closing(args)  # ...our FormClosing handler runs after it
    assert args.Cancel is False
    assert ended == ["end"] and saved == [{"x": 1}]  # quit path ran, geometry flushed
    assert shell._on_closing() is True  # any later closing is allowed
    shell._on_form_closing(Args("WindowsShutDown"))
    assert ended == ["end"]  # once


def test_session_ending_event_allows_close(tmp_path: Path) -> None:
    shell, win, ended = make(tmp_path)
    shell._begin_session_end()
    assert ended == ["end"] and shell._on_closing() is True and win.calls == []


@pytest.mark.parametrize(
    ("settings", "shape"),
    [
        ({"transparent_window": False}, "opaque"),
        ({"transparent_window": False, "window_shape": "region"}, "opaque"),
        ({"transparent_window": True}, "transparent"),
        ({"transparent_window": True, "window_shape": "region"}, "region"),
        ({"transparent_window": True, "transparent_pending": True}, "region"),
    ],
)
def test_decide_shape(settings: dict[str, Any], shape: str) -> None:
    assert decide_shape(settings) == shape


@pytest.mark.parametrize(
    ("samples", "verdict"),
    [([True, True], False), ([False, False], True), ([True, False], None),
     ([True], None), ([None, True], None), ([], None)],
)
def test_probe_verdict_needs_two_consecutive_failures(samples: list[Any], verdict: Any) -> None:
    assert probe_verdict(samples) is verdict


def test_probe_waits_for_visible_unmaximized_window(tmp_path: Path) -> None:
    shell, _, _ = make(tmp_path)
    started: list[str] = []
    shell._probe_transparency = lambda: started.append("probe")  # type: ignore[method-assign]

    class Geo:
        maximized = True
        fullscreen = False

    shell.geometry = Geo()  # type: ignore[assignment]
    shell._probe_state = "waiting"
    shell._hidden = True
    shell._shown = True
    shell._maybe_probe()  # hidden (--minimized): not yet
    shell._hidden = False
    shell._maybe_probe()  # maximised: not yet
    assert shell._probe_state == "waiting"
    Geo.maximized = False
    shell._maybe_probe()
    assert shell._probe_state == "running"


class ShowWindow:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def show(self) -> None:
        self.calls.append("show")

    def restore(self) -> None:
        self.calls.append("restore")


class NativeStub:
    def __init__(self, minimized: bool) -> None:
        self.minimized = minimized

    def is_minimized(self) -> bool:
        return self.minimized


class GeoStub:
    def __init__(self, maximized: bool, minimized: bool) -> None:
        self.maximized = maximized
        self.fullscreen = False
        self._n = NativeStub(minimized)


@pytest.mark.parametrize(
    ("maximized", "minimized", "restored"),
    [(True, True, True), (True, False, False), (False, False, True), (False, True, True)],
)
def test_show_restores_minimised_window_even_when_maximized(
    tmp_path: Path, maximized: bool, minimized: bool, restored: bool
) -> None:
    shell, _, _ = make(tmp_path)
    win = ShowWindow()
    shell._window = win
    shell.geometry = GeoStub(maximized, minimized)  # type: ignore[assignment]
    shell.show()
    assert ("restore" in win.calls) is restored and win.calls[0] == "show"


class EventStub:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, h: Any) -> EventStub:
        self.handlers.append(h)
        return self


class FormStub:
    def __init__(self) -> None:
        self.FormClosing = EventStub()


class NativeWin:
    def __init__(self) -> None:
        self.native = FormStub()


def test_session_end_hooks_installed_once(tmp_path: Path) -> None:
    shell, _, _ = make(tmp_path)
    win = NativeWin()
    shell._window = win
    shell._hook_session_end()
    shell._hook_session_end()
    assert len(win.native.FormClosing.handlers) == 1
