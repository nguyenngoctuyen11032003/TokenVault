"""Window controls (Task 20): minimise / maximise-restore / close, double-click, F11 / Esc,
state sync from ``tv.onWindowState`` and the JS-driven resize handles.

The Python side (Task 12) is stubbed by ``fixtures/api_stub_window.js``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.ui

WINDOW_STUB = Path(__file__).resolve().parent / "fixtures" / "api_stub_window.js"
IS_FULLSCREEN = "() => document.documentElement.classList.contains('window--fullscreen')"
HANDLE_DIRS = ["n", "s", "e", "w", "ne", "nw", "se", "sw"]


def _open_win(open_shell: Any, route: str = "#/home", **opts: Any) -> Any:
    script = f"window.__tvWinOpts = {json.dumps(opts)};\n" + WINDOW_STUB.read_text("utf-8")
    page = open_shell(route, init_script=script)
    page.wait_for_function("() => window.__tvCalls.some(c => c.name === 'window_state')")
    return page


def _calls(page: Any, name: str) -> list[list[Any]]:
    return [c["args"] for c in page.evaluate("window.__tvCalls") if c["name"] == name]


def _push(page: Any, **state: Any) -> None:
    full = {"maximized": False, "fullscreen": False, "min_width": 1100, "min_height": 720}
    page.evaluate("s => tv.onWindowState(JSON.stringify(s))", full | state)


def _max_button(page: Any) -> Any:
    return page.locator("tv-titlebar [data-action='max']")


def _root_classes(page: Any) -> dict[str, list[str]]:
    return page.evaluate(
        """() => ({
          html: [...document.documentElement.classList],
          win: [...document.querySelector('.tv-window').classList],
        })"""
    )


def test_titlebar_has_minimize_maximize_close_buttons_with_labels(open_shell: Any) -> None:
    page = _open_win(open_shell)
    buttons = page.locator("tv-titlebar .tv-titlebar__buttons button")
    actions = [buttons.nth(i).get_attribute("data-action") for i in range(buttons.count())]
    assert actions == ["min", "max", "close"]
    for name in ("Thu nhỏ", "Phóng to", "Ẩn xuống khay"):
        button = page.get_by_role("button", name=name, exact=True)
        assert button.is_visible()
        assert button.get_attribute("title")
        assert button.locator("svg").count() == 1
    # Buttons are never inside a pywebview drag region (a drag would swallow the click).
    inside_drag = page.evaluate(
        "[...document.querySelectorAll('tv-titlebar button')]"
        ".some(b => b.closest('.pywebview-drag-region'))"
    )
    assert inside_drag is False
    # Keyboard operable: focus + Enter on the maximise button calls the API.
    _max_button(page).focus()
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => window.__tvCalls.some(c => c.name === 'window_toggle_maximize')"
    )
    assert page.tv_errors == []


def test_maximize_button_calls_api_and_label_flips_on_state_push(open_shell: Any) -> None:
    page = _open_win(open_shell, passive=True)
    button = _max_button(page)
    assert button.get_attribute("aria-label") == "Phóng to"
    icon_before = button.inner_html()
    button.click()
    page.wait_for_function(
        "() => window.__tvCalls.some(c => c.name === 'window_toggle_maximize')"
    )
    # Passive stub: nothing changes until Python pushes the new state.
    assert button.get_attribute("aria-label") == "Phóng to"
    _push(page, maximized=True)
    assert button.get_attribute("aria-label") == "Khôi phục"
    assert button.get_attribute("title")
    assert button.inner_html() != icon_before
    # Object payloads work too.
    page.evaluate("tv.onWindowState({maximized: false, fullscreen: false})")
    assert button.get_attribute("aria-label") == "Phóng to"
    assert button.inner_html() == icon_before
    # While full screen the same button restores (leaves full screen).
    _push(page, fullscreen=True)
    assert button.get_attribute("aria-label") == "Khôi phục"
    button.click()
    page.wait_for_function("() => window.__tvCalls.some(c => c.name === 'window_set_fullscreen')")
    assert _calls(page, "window_set_fullscreen")[-1] == [False]
    assert page.tv_errors == []


def test_double_click_titlebar_toggles_maximize(open_shell: Any) -> None:
    page = _open_win(open_shell)
    page.locator("tv-titlebar .tv-titlebar__title").dblclick()
    page.wait_for_function("() => document.querySelector('.tv-window.window--maximized')")
    assert len(_calls(page, "window_toggle_maximize")) == 1
    page.locator("tv-titlebar .tv-titlebar__spacer").dblclick()
    page.wait_for_function("() => !document.querySelector('.tv-window.window--maximized')")
    assert len(_calls(page, "window_toggle_maximize")) == 2
    # Double-click on a button is not a title bar double-click.
    page.locator("tv-titlebar [data-action='min']").dblclick()
    assert len(_calls(page, "window_toggle_maximize")) == 2
    # Not while full screen.
    _push(page, fullscreen=True)
    page.locator("tv-titlebar .tv-titlebar__title").dblclick()
    page.wait_for_timeout(100)
    assert len(_calls(page, "window_toggle_maximize")) == 2


def test_f11_toggles_fullscreen_and_escape_leaves(open_shell: Any) -> None:
    page = _open_win(open_shell)
    page.keyboard.press("F11")
    page.wait_for_function(IS_FULLSCREEN)
    assert len(_calls(page, "window_toggle_fullscreen")) == 1
    hint = page.locator(".tv-fullscreen-hint")
    assert hint.is_visible()
    assert "F11" in hint.inner_text()
    # The hint fades after ~2.5 s.
    page.wait_for_function(
        "() => { const h = document.querySelector('.tv-fullscreen-hint');"
        " return !h || h.hidden || getComputedStyle(h).opacity === '0'; }",
        timeout=5000,
    )
    page.keyboard.press("Escape")
    page.wait_for_function(
        "() => !document.documentElement.classList.contains('window--fullscreen')"
    )
    assert _calls(page, "window_set_fullscreen") == [[False]]
    # Esc when not full screen does nothing.
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    assert _calls(page, "window_set_fullscreen") == [[False]]
    # F11 always works, even while typing; Esc in an input is left to the input.
    page.evaluate(
        "() => { const i = document.createElement('input'); i.id = 'probe';"
        " document.querySelector('#tv-view').prepend(i); }"
    )
    page.focus("#probe")
    page.keyboard.press("F11")
    page.wait_for_function(IS_FULLSCREEN)
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    assert _calls(page, "window_set_fullscreen") == [[False]]
    assert page.tv_errors == []


def test_escape_does_not_leave_when_dialog_open(open_shell: Any) -> None:
    page = _open_win(open_shell)
    _push(page, fullscreen=True)
    page.evaluate(
        "() => { const d = document.createElement('div'); d.id = 'dlg';"
        " d.setAttribute('role', 'dialog'); d.setAttribute('aria-modal', 'true');"
        " d.innerHTML = '<button>ok</button>'; document.body.append(d); }"
    )
    page.focus("#dlg button")
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    assert _calls(page, "window_set_fullscreen") == []
    page.evaluate("document.getElementById('dlg').remove()")
    page.keyboard.press("Escape")
    page.wait_for_function("() => window.__tvCalls.some(c => c.name === 'window_set_fullscreen')")


def _chamfer(page: Any) -> dict[str, str]:
    return page.evaluate(
        """() => {
          const s = getComputedStyle(document.querySelector('.tv-window'));
          return {chamfer: s.getPropertyValue('--tv-chamfer').trim(), clip: s.clipPath,
                  padLeft: s.paddingLeft};
        }"""
    )


@pytest.mark.parametrize("opaque", [False, True])
def test_state_push_applies_classes_and_removes_chamfer(open_shell: Any, opaque: bool) -> None:
    script = WINDOW_STUB.read_text("utf-8")
    page = open_shell("#/home", init_script=script, settings={"transparent_window": not opaque})
    page.wait_for_function("() => window.__tvCalls.some(c => c.name === 'window_state')")
    normal = _chamfer(page)
    assert normal["chamfer"] == "30px"
    assert normal["clip"].startswith("polygon") != opaque
    for state, cls in (({"maximized": True}, "window--maximized"),
                       ({"fullscreen": True}, "window--fullscreen")):
        _push(page, **state)
        classes = _root_classes(page)
        assert cls in classes["html"] and cls in classes["win"]
        assert ("window--opaque" in classes["win"]) == opaque
        flat = _chamfer(page)
        assert flat["chamfer"] in ("0", "0px")
        assert flat["clip"] == "none"
        assert float(flat["padLeft"].removesuffix("px")) < float(
            normal["padLeft"].removesuffix("px")
        )
        _push(page)
        classes = _root_classes(page)
        assert "window--maximized" not in classes["html"] + classes["win"]
        assert "window--fullscreen" not in classes["html"] + classes["win"]
        assert _chamfer(page) == normal


def _handles_visible(page: Any) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('.tv-resize__handle')]
             .filter(h => h.getClientRects().length && getComputedStyle(h).display !== 'none')
             .map(h => h.dataset.dir)"""
    )


def test_resize_handles_hidden_when_maximized_or_native(open_shell: Any) -> None:
    page = _open_win(open_shell)
    assert sorted(_handles_visible(page)) == sorted(HANDLE_DIRS)
    cursors = page.evaluate(
        "Object.fromEntries([...document.querySelectorAll('.tv-resize__handle')]"
        ".map(h => [h.dataset.dir, getComputedStyle(h).cursor]))"
    )
    assert cursors == {d: f"{d}-resize" for d in HANDLE_DIRS}
    _push(page, maximized=True)
    assert _handles_visible(page) == []
    _push(page, fullscreen=True)
    assert _handles_visible(page) == []
    _push(page)
    assert len(_handles_visible(page)) == 8
    _push(page, native_resize=True)
    assert _handles_visible(page) == []

    native = _open_win(open_shell, native_resize=True)
    assert native.locator(".tv-resize__handle").count() == 0 or _handles_visible(native) == []


def _drag(page: Any, start: tuple[float, float], delta: tuple[float, float]) -> None:
    page.mouse.move(*start)
    page.mouse.down()
    page.mouse.move(start[0] + delta[0] / 2, start[1] + delta[1] / 2, steps=12)
    page.mouse.move(start[0] + delta[0], start[1] + delta[1], steps=12)
    page.mouse.up()


def _wait_bounds(page: Any, expected: list[int]) -> None:
    try:
        _wait_bounds_raw(page, expected)
    except Exception as exc:
        sent = _calls(page, "window_set_bounds")[-6:]
        raise AssertionError(f"expected {expected}; last sent {sent}") from exc
    # Nothing still queued behind an in-flight call (the next drag starts from a clean state).
    page.wait_for_function("() => window.__tvWin.inflight === 0")
    page.wait_for_timeout(60)
    assert _calls(page, "window_set_bounds")[-1] == expected


def _wait_bounds_raw(page: Any, expected: list[int]) -> None:
    page.wait_for_function(
        "e => { const c = window.__tvCalls.filter(c => c.name === 'window_set_bounds');"
        " return c.length && JSON.stringify(c[c.length - 1].args) === JSON.stringify(e); }",
        arg=expected,
    )


def test_resize_drag_clamps_to_min_size_and_sends_final_bounds(open_shell: Any) -> None:
    page = _open_win(open_shell, bounds_delay_ms=40)
    w, h = 1440, 900
    # SE corner shrunk far below the minimum: clamped to 1100x720, origin unchanged.
    _drag(page, (w - 4, h - 4), (-600, -400))
    _wait_bounds(page, [100, 80, 1100, 720])
    assert len(_calls(page, "window_get_bounds")) == 1
    sent = _calls(page, "window_set_bounds")
    assert all(a[2] >= 1100 and a[3] >= 720 for a in sent)
    page.wait_for_function("() => window.__tvWin.inflight === 0")
    assert page.evaluate("window.__tvWin.maxInflight") == 1

    # W edge: the right edge stays put, x moves by what the width lost (clamped).
    page.evaluate("Object.assign(window.__tvWin.state, {x: 100, y: 80, width: 1440, height: 900})")
    _drag(page, (3, h / 2), (500, 40))
    _wait_bounds(page, [440, 80, 1100, 900])
    assert len(_calls(page, "window_get_bounds")) == 2
    # Growing works as well.
    page.evaluate("Object.assign(window.__tvWin.state, {x: 100, y: 80, width: 1440, height: 900})")
    _drag(page, (3, h / 2), (-60, 0))
    _wait_bounds(page, [40, 80, 1500, 900])
    page.wait_for_function("() => window.__tvWin.inflight === 0")
    assert page.evaluate("window.__tvWin.maxInflight") == 1

    # Escape cancels the drag and restores the original bounds.
    page.evaluate("Object.assign(window.__tvWin.state, {x: 100, y: 80, width: 1440, height: 900})")
    page.mouse.move(w - 4, h - 4)
    page.mouse.down()
    page.mouse.move(w + 100, h + 50, steps=6)
    page.keyboard.press("Escape")
    page.mouse.up()
    _wait_bounds(page, [100, 80, 1440, 900])
    assert page.tv_errors == []


def test_malformed_state_push_is_ignored(open_shell: Any) -> None:
    page = _open_win(open_shell)
    _push(page, maximized=True)
    for bad in ("'not json'", "'[1,2]'", "null", "42", "{maximized: 'yes', fullscreen: false}",
                "{fullscreen: true}", "'{\"maximized\": 1, \"fullscreen\": 0}'"):
        page.evaluate(f"tv.onWindowState({bad})")
    assert "window--maximized" in _root_classes(page)["html"]
    assert "window--fullscreen" not in _root_classes(page)["html"]
    assert _max_button(page).get_attribute("aria-label") == "Khôi phục"
    state = page.evaluate("import('./js/winstate.js').then(m => m.getState())")
    assert state["maximized"] is True and state["fullscreen"] is False
    assert page.tv_errors == []


def test_window_api_absent_does_not_throw(open_shell: Any) -> None:
    # Stub without any window_* method except min/close (an old bridge).
    page = open_shell("#/home")
    page.wait_for_selector("tv-titlebar [data-action='max']")
    _max_button(page).click()
    toast = page.locator(".tv-toast")
    toast.wait_for(state="visible")
    assert toast.inner_text() == "Chỉ có trong ứng dụng"
    page.locator("tv-titlebar .tv-titlebar__title").dblclick()
    page.keyboard.press("F11")
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    # Defaults: normal window, handles drawn, min size 1100x720.
    state = page.evaluate("import('./js/winstate.js').then(m => m.getState())")
    assert state["maximized"] is False and state["min_width"] == 1100
    assert page.tv_errors == []

    # Plain browser (http fallback, no pywebview at all): same, no exception.
    plain = open_shell("#/home", pywebview=False)
    plain.wait_for_selector("tv-titlebar [data-action='max']")
    _max_button(plain).click()
    plain.locator(".tv-toast").wait_for(state="visible")
    plain.keyboard.press("F11")
    plain.wait_for_timeout(150)
    assert plain.tv_errors == []
