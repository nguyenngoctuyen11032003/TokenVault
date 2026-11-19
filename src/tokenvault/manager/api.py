"""The Python API the UI calls (``window.pywebview.api.<name>`` or ``POST /api/<name>``).

Implements ``docs/superpowers/specs/manager-api-contract.md`` exactly. Every public
method is exposed to JS (pywebview exposes all public callables of ``js_api``), so
this class has NO other public attributes; state lives in ``_``-prefixed fields.

* Arguments: validated (filters -> :class:`Filter`, date-only days); results are
  plain JSON (see :mod:`api_models`): costs are JSON numbers, UNKNOWN cost = null.
* Errors: every method catches everything; known problems raise
  ``Exception("[code] message")`` (``[template_missing]``, ``[invalid_team]``,
  ``[cert_error]``, ``[revoked]``, ...), anything unexpected is logged with its
  traceback and re-raised as a short clean message.
* Threads: pywebview runs each JS call on its own worker thread, so slow calls
  (``create_installer``: one template copy) never block the UI thread. All
  components used here are thread-safe.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, cast

from tokenvault.manager import api_manage as mg
from tokenvault.manager import api_reports as rp
from tokenvault.manager.api_models import (
    ApiError,
    jsonable,
    parse_filter,
    parse_range,
    require_text,
)
from tokenvault.manager.lifecycle import Manager
from tokenvault.manager.window_control import VirtualNative, WindowGeometry

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

EXPOSED = (
    "overview", "employees", "employee", "projects", "sessions", "session", "session_turns",
    "skills", "orchestration", "teams",
    "devices", "set_device_team", "revoke_device", "packages", "revoke_package",
    "create_installer", "reveal_in_explorer", "settings", "save_settings", "diagnostics",
    "connectivity_test", "regenerate_certificate",
    "report_options", "export_report", "report_progress", "open_path",
    "window_min", "window_close", "window_ready",
    "window_toggle_maximize", "window_maximize", "window_restore", "window_set_fullscreen",
    "window_toggle_fullscreen", "window_state", "window_get_bounds", "window_set_bounds",
)


class WindowControl(Protocol):
    """The app window (``geometry``, a :class:`WindowGeometry`, is read if present)."""

    def minimize(self) -> None: ...
    def hide(self) -> None: ...
    def ready(self) -> None: ...


def _int_arg(v: Any, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 100_000:
        raise ApiError(f"{name} must be a number", "invalid_args")
    return int(round(v))


def _api(fn: F) -> F:
    sig = inspect.signature(fn)
    name = fn.__name__

    @functools.wraps(fn)
    def wrapper(self: Api, *args: Any, **kw: Any) -> Any:
        try:
            sig.bind(self, *args, **kw)
        except TypeError as e:
            raise Exception(f"[invalid_args] {name}: {e}") from None
        try:
            return jsonable(fn(self, *args, **kw))
        except ApiError as e:
            log.info("api %s refused: %s", name, e)
            msg = str(e)
        except Exception:  # noqa: BLE001 - never leak internals to the UI
            log.exception("api %s failed", name)
            msg = f"{name} failed: internal error (details in manager.log)"
        raise Exception(msg)

    return cast(F, wrapper)


class Api:
    def __init__(self, manager: Manager, window: WindowControl | None = None) -> None:
        self._m = manager
        self._window = window
        self._reports = rp.ReportState()
        self._headless = WindowGeometry(VirtualNative())  # dev server / no window yet

    def _set_window(self, window: WindowControl | None) -> None:
        self._window = window

    def _geo(self) -> WindowGeometry:
        w = self._window
        geo = getattr(w, "geometry", None) if w is not None else None
        return geo if isinstance(geo, WindowGeometry) else self._headless

    # ------------------------------------------------------------- reads
    @_api
    def overview(self, filter: Any = None) -> Any:  # noqa: A002 - contract name
        return self._m.aggregates.overview(parse_filter(filter))

    @_api
    def employees(self, filter: Any = None) -> Any:  # noqa: A002
        return self._m.aggregates.employees(parse_filter(filter))

    @_api
    def employee(self, key: Any, range: Any = "today") -> Any:  # noqa: A002
        return self._m.aggregates.employee(require_text(key, "key"), parse_range(range))

    @_api
    def projects(self, filter: Any = None) -> Any:  # noqa: A002
        return self._m.aggregates.projects(parse_filter(filter))

    @_api
    def sessions(self, filter: Any = None) -> Any:  # noqa: A002
        return self._m.aggregates.sessions(parse_filter(filter))

    @_api
    def session(self, session_id: Any) -> Any:
        return self._m.aggregates.session(require_text(session_id, "session_id"))

    @_api
    def session_turns(self, session_id: Any) -> Any:
        return self._m.aggregates.session_turns(require_text(session_id, "session_id"))

    @_api
    def skills(self, range: Any = "7d") -> Any:  # noqa: A002
        return self._m.aggregates.skills(parse_range(range))

    @_api
    def orchestration(self, range: Any = "7d") -> Any:  # noqa: A002
        return self._m.aggregates.orchestration(parse_range(range))

    @_api
    def teams(self) -> Any:
        return mg.teams(self._m)

    # -------------------------------------------------------- management
    @_api
    def devices(self) -> Any:
        return mg.devices(self._m)

    @_api
    def set_device_team(self, device_id: Any, team: Any) -> Any:
        return mg.set_device_team(self._m, device_id, team)

    @_api
    def revoke_device(self, device_id: Any) -> Any:
        return mg.revoke_device(self._m, device_id)

    @_api
    def packages(self) -> Any:
        return mg.packages(self._m)

    @_api
    def revoke_package(self, package_id: Any) -> Any:
        return mg.revoke_package(self._m, package_id)

    @_api
    def create_installer(self, team: Any, label: Any = None) -> Any:
        return mg.create_installer(self._m, team, label)

    @_api
    def reveal_in_explorer(self, path: Any) -> Any:
        return mg.reveal_in_explorer(self._m, path)

    @_api
    def settings(self) -> Any:
        return mg.settings(self._m)

    @_api
    def save_settings(self, patch: Any) -> Any:
        return mg.save_settings(self._m, patch)

    @_api
    def diagnostics(self) -> Any:
        return mg.diagnostics(self._m)

    @_api
    def connectivity_test(self) -> Any:
        return mg.connectivity_test(self._m)

    @_api
    def regenerate_certificate(self) -> Any:
        return mg.regenerate_certificate(self._m)

    # ----------------------------------------------------------- reports
    @_api
    def report_options(self) -> Any:
        return rp.report_options(self._m)

    @_api
    def export_report(self, spec: Any) -> Any:
        return rp.export_report(self._m, self._reports, spec)

    @_api
    def report_progress(self) -> Any:
        return rp.report_progress(self._reports)

    @_api
    def open_path(self, path: Any, reveal: Any = False) -> Any:
        return rp.open_path(self._m, self._reports, path, reveal)

    # ------------------------------------------------------------ window
    @_api
    def window_min(self) -> Any:
        if self._window is not None:
            self._window.minimize()
        return {"ok": True}

    @_api
    def window_close(self) -> Any:
        """Hide to the tray; the ingest server keeps running."""
        if self._window is not None:
            self._window.hide()
        return {"ok": True}

    @_api
    def window_ready(self) -> Any:
        if self._window is not None:
            self._window.ready()
        return {"ok": True}

    @_api
    def window_toggle_maximize(self) -> Any:
        return self._geo().toggle_maximize()

    @_api
    def window_maximize(self) -> Any:
        return self._geo().maximize()

    @_api
    def window_restore(self) -> Any:
        return self._geo().restore()

    @_api
    def window_set_fullscreen(self, on: Any) -> Any:
        if not isinstance(on, bool):
            raise ApiError("on must be true or false", "invalid_args")
        return self._geo().set_fullscreen(on)

    @_api
    def window_toggle_fullscreen(self) -> Any:
        return self._geo().toggle_fullscreen()

    @_api
    def window_state(self) -> Any:
        return self._geo().state()

    @_api
    def window_get_bounds(self) -> Any:
        return self._geo().get_bounds()

    @_api
    def window_set_bounds(self, x: Any, y: Any, width: Any, height: Any) -> Any:
        return self._geo().set_bounds(_int_arg(x, "x"), _int_arg(y, "y"),
                                      _int_arg(width, "width"), _int_arg(height, "height"))


def dispatch(api: Api, method: str, args: list[Any]) -> Any:
    """``POST /api/<method>`` entry: only contract methods; LookupError if unknown."""
    if method not in EXPOSED:
        raise LookupError(method)
    return getattr(api, method)(*args)
