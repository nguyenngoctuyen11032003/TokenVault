"""Manager settings (``settings.json``): validated, atomic, thread-safe.

* User-editable keys (``save_patch``, i.e. ``Api.save_settings``):
  ``transparent_window``, ``start_with_windows``, ``hosts``.
* Internal keys (``update_internal``, written by the app itself): ``port`` (the
  LAST BOUND ingest port, requested again on the next start), ``manager_id``,
  ``static_ip_hint``, ``transparent_pending`` (a transparent window was created
  but never finished loading: the next start falls back to opaque), ``window``
  (``{x, y, width, height, maximized}`` normal geometry in logical px, or null),
  ``window_shape`` (``"region"`` once WebView2 transparency was found broken).
* ``hosts``: empty list = automatic (primary LAN IPv4s + host name); otherwise
  the addresses put into new installers. Same rules as the installer payload:
  at most 8, each a valid IP or host name, no case-insensitive duplicates.
* Writes go to a temp file in the same folder, are fsynced and then
  ``os.replace``d over ``settings.json`` (never half-written). A file that is
  not valid JSON is renamed to ``settings.json.corrupt-<stamp>`` and defaults
  are used; single invalid values fall back to their default.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from tokenvault.common.config_payload import MAX_HOSTS, valid_host

log = logging.getLogger(__name__)

DEFAULT_PORT = 47800
DEFAULTS: dict[str, Any] = {
    "transparent_window": True,
    "start_with_windows": False,
    "hosts": [],
    "port": DEFAULT_PORT,
    "manager_id": "",
    "static_ip_hint": "",
    "transparent_pending": False,
    "window": None,
    "window_shape": None,
}
USER_KEYS = frozenset({"transparent_window", "start_with_windows", "hosts"})
INTERNAL_KEYS = frozenset(DEFAULTS) - USER_KEYS
_MANAGER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class SettingsError(ValueError):
    """A settings value or key was refused (message is user-presentable)."""


def clean_hosts(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise SettingsError("hosts must be a list of IP addresses or host names")
    out: list[str] = []
    seen: set[str] = set()
    for h in value:
        if not isinstance(h, str):
            raise SettingsError("hosts must contain only text")
        h = h.strip()
        if not valid_host(h):
            raise SettingsError(f"invalid host {h!r}: use an IPv4/IPv6 address or a host name")
        if h.lower() in seen:
            raise SettingsError(f"duplicate host {h!r}")
        seen.add(h.lower())
        out.append(h)
    if len(out) > MAX_HOSTS:
        raise SettingsError(f"at most {MAX_HOSTS} hosts")
    return out


def _validate(key: str, value: Any) -> Any:
    if key in ("transparent_window", "start_with_windows", "transparent_pending"):
        if type(value) is not bool:
            raise SettingsError(f"{key} must be true or false")
        return value
    if key == "hosts":
        return clean_hosts(value)
    if key == "port":
        if type(value) is not int or not 1 <= value <= 65535:
            raise SettingsError("port must be 1..65535")
        return value
    if key == "manager_id":
        if not isinstance(value, str) or not (value == "" or _MANAGER_ID.fullmatch(value)):
            raise SettingsError("manager_id must be 1..64 of A-Z a-z 0-9 . _ -")
        return value
    if key == "window":
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"x", "y", "width", "height", "maximized"}:
            raise SettingsError("window must be {x, y, width, height, maximized} or null")
        if type(value["maximized"]) is not bool or not all(
            type(value[k]) is int and -100_000 <= value[k] <= 100_000
            for k in ("x", "y", "width", "height")
        ) or value["width"] < 100 or value["height"] < 100:
            raise SettingsError("window geometry out of range")
        return dict(value)
    if key == "window_shape":
        if value not in (None, "region"):
            raise SettingsError("window_shape must be null or 'region'")
        return value
    if key == "static_ip_hint":
        if not isinstance(value, str) or len(value) > 500:
            raise SettingsError("static_ip_hint must be text (<= 500 chars)")
        return value
    raise SettingsError(f"unknown setting {key!r}")


class SettingsStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._values = self._load()

    def get(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._values)

    def save_patch(self, patch: Any) -> dict[str, Any]:
        """Apply a user patch (allowed keys only); all-or-nothing. Returns all settings."""
        if not isinstance(patch, dict):
            raise SettingsError("settings patch must be an object")
        bad = sorted(str(k) for k in patch if k not in USER_KEYS)
        if bad:
            raise SettingsError(f"setting(s) not editable: {', '.join(bad)}")
        return self._apply(patch)

    def update_internal(self, **values: Any) -> dict[str, Any]:
        return self._apply(values)

    def _apply(self, patch: dict[str, Any]) -> dict[str, Any]:
        clean = {k: _validate(k, v) for k, v in patch.items()}
        with self._lock:
            new = {**self._values, **clean}
            if new != self._values or not self.path.exists():
                self._write(new)
            self._values = new
            return copy.deepcopy(new)

    # ------------------------------------------------------------------ disk
    def _load(self) -> dict[str, Any]:
        values = copy.deepcopy(DEFAULTS)
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return values
        except OSError as e:
            log.warning("cannot read %s (%s); using defaults", self.path, e)
            return values
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError) as e:
            backup = self.path.with_name(
                f"{self.path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
            )
            with contextlib.suppress(OSError):
                os.replace(self.path, backup)
            log.warning("settings file was corrupt (%s); backed up to %s", e, backup.name)
            return values
        for key in DEFAULTS:
            if key not in data:
                continue
            try:
                values[key] = _validate(key, data[key])
            except SettingsError as e:
                log.warning("ignoring invalid setting %s: %s", key, e)
        return values

    def _write(self, values: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(4)}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(values, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as e:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise SettingsError(f"cannot save settings: {e}") from e
