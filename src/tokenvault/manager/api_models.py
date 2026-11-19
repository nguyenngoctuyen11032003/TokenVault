"""Argument parsing, validation and JSON conversion for :mod:`tokenvault.manager.api`.

JSON policy: dataclasses go through ``dataclasses.asdict``; ``Decimal`` becomes a
JSON number (``float``); non-finite numbers, and UNKNOWN costs, are ``null``;
``Path`` -> string; tuples/sets -> lists. Instants are ISO-8601 UTC (``...Z``).
"""

from __future__ import annotations

import dataclasses
import math
import re
import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tokenvault.manager.agg_models import Filter
from tokenvault.manager.agg_sql import RANGES

FILTER_KEYS = ("team", "email", "project", "model", "start", "end")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
MAX_TEAM = 40
MAX_LABEL = 80
_BAD_TEAM_CHARS = set('/\\<>:"|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
NO_TEAM = "(no team)"


class ApiError(Exception):
    """An error whose message is safe to show (may start with a ``[code]`` prefix)."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(f"[{code}] {message}" if code else message)


class TemplateMissingError(ApiError):
    """The adapter template file is missing or empty (``[template_missing]``)."""

    def __init__(self, path: object) -> None:
        super().__init__(f"adapter template missing: {path}", "template_missing")


def parse_filter(obj: Any) -> Filter:
    """``{team, email, project, model, start, end}`` (all optional) -> :class:`Filter`."""
    if obj is None:
        return Filter()
    if not isinstance(obj, dict):
        raise ApiError("filter must be an object or null", "invalid_filter")
    unknown = sorted(str(k) for k in obj if k not in FILTER_KEYS)
    if unknown:
        raise ApiError(f"unknown filter key(s): {', '.join(unknown)}", "invalid_filter")
    vals: dict[str, str | None] = {}
    for k in FILTER_KEYS:
        v = obj.get(k)
        if v is not None and not isinstance(v, str):
            raise ApiError(f"filter.{k} must be text or null", "invalid_filter")
        if k in ("start", "end") and v is not None:
            if not _DAY.fullmatch(v):
                raise ApiError(f"filter.{k} must be a YYYY-MM-DD day", "invalid_filter")
            try:
                date.fromisoformat(v)
            except ValueError:
                raise ApiError(f"filter.{k} is not a valid day", "invalid_filter") from None
        vals[k] = v
    if vals["start"] and vals["end"] and vals["start"] > vals["end"]:
        raise ApiError("filter.start is after filter.end", "invalid_filter")
    return Filter(**vals)


def parse_range(rng: Any) -> str:
    if rng is None:
        return "today"
    if not isinstance(rng, str) or rng not in RANGES:
        raise ApiError(f"range must be one of {', '.join(RANGES)}", "invalid_range")
    return rng


def require_text(value: Any, name: str, max_len: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise ApiError(f"{name} must be non-empty text (<= {max_len} chars)", "invalid_args")
    return value


def clean_team(team: Any, *, allow_empty: bool) -> str:
    """Team name rules shared with the UI wizard: NFC, trimmed, <= 40 chars, file-name safe."""
    if not isinstance(team, str):
        raise ApiError("team must be text", "invalid_team")
    t = unicodedata.normalize("NFC", team).strip()
    if t == NO_TEAM:
        t = ""
    if not t:
        if allow_empty:
            return ""
        raise ApiError("team name must not be empty", "invalid_team")
    if len(t) > MAX_TEAM:
        raise ApiError(f"team name is longer than {MAX_TEAM} characters", "invalid_team")
    if any(ord(c) < 32 or ord(c) == 127 or c in _BAD_TEAM_CHARS for c in t):
        raise ApiError('team name must not contain / \\ < > : " | ? * or control characters',
                       "invalid_team")
    if t.endswith(".") or t.split(".")[0].upper() in _RESERVED:
        raise ApiError("team name is reserved by Windows", "invalid_team")
    return t


def clean_label(label: Any) -> str | None:
    if label is None:
        return None
    if not isinstance(label, str):
        raise ApiError("label must be text or null", "invalid_args")
    s = unicodedata.normalize("NFC", label).strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in s) or len(s) > MAX_LABEL:
        raise ApiError(f"label must be <= {MAX_LABEL} printable characters", "invalid_args")
    return s or None


def iso_utc(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def jsonable(obj: Any) -> Any:
    """Convert API results to plain JSON values (see module doc)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(jsonable(v) for v in obj)
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, (float, Decimal)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"not JSON-serialisable: {type(obj).__name__}")
