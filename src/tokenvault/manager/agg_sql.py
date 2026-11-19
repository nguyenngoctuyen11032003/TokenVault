"""Query core shared by the aggregate modules: SQL fragments, accumulators,
time/filter helpers, all bound in :class:`QueryContext`.

Time model: a fixed UTC offset (``tz_offset_minutes``), no DST. A local day is
``floor((hour * 60 + offset) / 1440)`` of a UTC hour: offsets that are not a
whole number of hours are applied to whole UTC hours (an hour is counted in
the local day its start falls in).

Speed: when the store's local rollups use the same offset, a time range is
split into whole local months (``rollup_month``), whole local days
(``rollup_day``) and leftover hours (``rollup_hour``); the answer is identical
to an all-hourly scan, just cheaper. Otherwise everything reads hourly rollups.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from tokenvault.common.pricing import Pricing
from tokenvault.manager.agg_models import Filter
from tokenvault.manager.store import TOKEN_COLS, DeviceRow, Store
from tokenvault.manager.store_core import (
    TOOL_TABLES,
    USAGE_TABLES,
    day_first_hour,
    local_day,
    month_first_day,
    month_of_day,
)

NO_MODEL = "(no model)"
RANGES = {"today": 1, "7d": 7, "30d": 30, "90d": 90, "all": 0}
EPOCH = date(1970, 1, 1)
SUMS = ", ".join(f"SUM(r.{c})" for c in TOKEN_COLS)
# Employee key of a rollup row (alias r); identity is frozen at ingest.
KEY = "CASE WHEN r.email <> '' THEN r.email ELSE 'device:' || r.device_id END"
JOIN = "LEFT JOIN devices d ON d.device_id = r.device_id"
# (grain, lo, hi): grain "hour" | "day" | "month"; bounds half-open, None = open.
Segment = tuple[str, int | None, int | None]


def model_label(model: str | None) -> str:
    return model if model else NO_MODEL


def tok(vals: Sequence[int]) -> dict[str, int]:
    """Five token sums -> keyword arguments for :class:`Tokens` subclasses."""
    i, o, cr, c5, c1 = (int(v or 0) for v in vals)
    return {
        "input_tokens": i,
        "output_tokens": o,
        "cache_read_tokens": cr,
        "cache_create_5m_tokens": c5,
        "cache_create_1h_tokens": c1,
        "total_tokens": i + o + cr + c5 + c1,
    }


class Acc:
    """Token + cost accumulator for one group of rows."""

    def __init__(self) -> None:
        self.t = [0, 0, 0, 0, 0]
        self.events = 0
        self.known = Decimal(0)
        self.unknown: set[str] = set()
        self.has_known = False

    def add(self, pricing: Pricing, model: str, toks: Sequence[int], events: int = 0) -> None:
        vals = [int(v or 0) for v in toks]
        for i, v in enumerate(vals):
            self.t[i] += v
        self.events += int(events or 0)
        if not any(vals):
            return
        c = pricing.cost(model, *vals)
        if c is None:
            self.unknown.add(model_label(model))
        else:
            self.known += c
            self.has_known = True

    @property
    def cost(self) -> float | None:
        """Known cost; None when tokens exist but none from a priced model."""
        if not self.has_known:
            return None if self.unknown else 0.0
        return round(float(self.known), 6)

    @property
    def partial(self) -> bool:
        return bool(self.unknown)

    @property
    def unknown_models(self) -> list[str]:
        return sorted(self.unknown)

    @property
    def total(self) -> int:
        return sum(self.t)


class QueryContext:
    """Store + pricing + clock + fixed tz offset, with SQL/time helpers."""

    def __init__(
        self,
        store: Store,
        pricing: Pricing,
        tz_offset_minutes: int = 0,
        now: Callable[[], float] = time.time,
    ):
        self.store = store
        self.pricing = pricing
        self.tz_offset_minutes = tz_offset_minutes
        self.tz = timezone(timedelta(minutes=tz_offset_minutes))
        self.now = now

    # ---------------------------------------------------------------- queries
    @contextmanager
    def snapshot(self) -> Iterator[None]:
        """Run every query inside one read transaction (consistent view)."""
        with self.store.read_snapshot():
            yield

    def q(self, sql: str, params: Sequence[Any] = ()) -> list[Any]:
        with self.store.read_snapshot() as snap:
            return snap.fetchall(sql, params)

    def devices(self) -> list[DeviceRow]:
        rows = self.q(
            "SELECT device_id, team, email, windows_user, hostname, last_seen "
            "FROM devices ORDER BY device_id"
        )
        return [DeviceRow(*tuple(r)) for r in rows]

    def online(self, window_s: float = 30) -> set[str]:
        rows = self.q("SELECT device_id FROM devices WHERE last_seen >= ?", [self.now() - window_s])
        return {str(r[0]) for r in rows}

    def scope(
        self,
        flt: Filter,
        table: str,
        *,
        tcol: str | None = None,
        lo: int | None = None,
        hi: int | None = None,
        session: bool = False,
        joins: str = "",
        extra: Sequence[str] = (),
        extra_params: Sequence[Any] = (),
    ) -> tuple[str, list[Any]]:
        """``FROM`` source (``<table> r`` + joins + ``WHERE``) and its params.

        Time bounds are explicit (``tcol`` in [``lo``, ``hi``)), except for
        ``session=True`` where the filter's range is matched by overlap of
        ``[first_ts, last_ts]``. Team/e-mail/project/model come from ``flt``.
        """
        conds: list[str] = []
        params: list[Any] = []
        src = f"{table} r"
        if flt.team is not None:
            src += f" {JOIN}"
            conds.append("COALESCE(d.team, '') = ?")
            params.append(flt.team)
        if joins:
            src += f" {joins}"
        if flt.email is not None:
            if flt.email.startswith("device:"):
                conds.append("r.email = '' AND r.device_id = ?")
                params.append(flt.email[len("device:") :])
            else:
                conds.append("r.email = ?")
                params.append(flt.email)
        if flt.project is not None:
            conds.append("r.project = ?")
            params.append(flt.project)
        if flt.model is not None:
            conds.append("r.model = ?")
            params.append("" if flt.model == NO_MODEL else flt.model)
        if session:
            sh, eh = self.hours(flt)
            if sh is not None:
                conds.append("r.last_ts >= ?")
                params.append(sh * 3600)
            if eh is not None:
                conds.append("r.first_ts < ?")
                params.append(eh * 3600)
        elif tcol is not None:
            if lo is not None:
                conds.append(f"r.{tcol} >= ?")
                params.append(lo)
            if hi is not None:
                conds.append(f"r.{tcol} < ?")
                params.append(hi)
        conds.extend(extra)
        params.extend(extra_params)
        if conds:
            src += " WHERE " + " AND ".join(conds)
        return src, params

    # ------------------------------------------------------------ segments
    @property
    def local_tables(self) -> bool:
        """Local day/month rollups are usable when they use our offset."""
        return self.store.tz_offset_minutes == self.tz_offset_minutes

    def segments(self, sh: int | None, eh: int | None, months: bool = True) -> list[Segment]:
        """Split [sh, eh) (UTC hours, None = open) into the coarsest exact pieces:
        whole local months, then whole local days, then leftover hours."""
        if not self.local_tables:
            return [("hour", sh, eh)]
        off = self.tz_offset_minutes
        d0 = None
        if sh is not None:
            d0 = local_day(sh, off)
            if day_first_hour(d0, off) != sh:
                d0 += 1
        d1 = None if eh is None else local_day(eh, off)
        if d0 is not None and d1 is not None and d0 >= d1:
            return [("hour", sh, eh)]
        out: list[Segment] = []
        if sh is not None and d0 is not None and sh < day_first_hour(d0, off):
            out.append(("hour", sh, day_first_hour(d0, off)))
        if eh is not None and d1 is not None and day_first_hour(d1, off) < eh:
            out.append(("hour", day_first_hour(d1, off), eh))
        m0 = m1 = None
        if months:
            if d0 is not None:
                m0 = month_of_day(d0)
                m0 += 1 if month_first_day(m0) != d0 else 0
            m1 = None if d1 is None else month_of_day(d1)
        if not months or (m0 is not None and m1 is not None and m0 >= m1):
            return [*out, ("day", d0, d1)]
        if d0 is not None and m0 is not None and d0 < month_first_day(m0):
            out.append(("day", d0, month_first_day(m0)))
        if d1 is not None and m1 is not None and month_first_day(m1) < d1:
            out.append(("day", month_first_day(m1), d1))
        return [*out, ("month", m0, m1)]

    def seg_query(
        self,
        flt: Filter,
        family: str,
        select: str,
        group: str,
        *,
        months: bool = True,
        bounds: tuple[int | None, int | None] | None = None,
        extra: Sequence[str] = (),
        extra_params: Sequence[Any] = (),
    ) -> list[tuple[str, Any]]:
        """Run ``SELECT select FROM <rollup> WHERE ... GROUP BY group`` on each
        time segment of the filter (or ``bounds``) and return ``(grain, row)``.

        ``family`` is ``"usage"`` or ``"tool"``; ``{t}`` in ``select``/``group``
        is replaced by the segment's time column.
        """
        sh, eh = bounds if bounds is not None else self.hours(flt)
        tables = USAGE_TABLES if family == "usage" else TOOL_TABLES
        by_grain = {tcol: table for table, tcol in tables}
        out: list[tuple[str, Any]] = []
        for grain, lo, hi in self.segments(sh, eh, months):
            src, params = self.scope(
                flt,
                by_grain[grain],
                tcol=grain,
                lo=lo,
                hi=hi,
                extra=extra,
                extra_params=extra_params,
            )
            sel = select.replace("{t}", f"r.{grain}")
            grp = group.replace("{t}", f"r.{grain}")
            rows = self.q(f"SELECT {sel} FROM {src} GROUP BY {grp}", params)
            out.extend((grain, r) for r in rows)
        return out

    def day_of(self, grain: str, value: int) -> int:
        """Local day of an hour/day bucket value."""
        return local_day(value, self.tz_offset_minutes) if grain == "hour" else value

    def month_of(self, grain: str, value: int) -> int:
        return value if grain == "month" else month_of_day(self.day_of(grain, value))

    def data_hours(self) -> tuple[int | None, int | None]:
        """First and last UTC hour that has any usage (unfiltered, index-only)."""
        row = self.q(
            "SELECT (SELECT MIN(hour) FROM rollup_hour), (SELECT MAX(hour) FROM rollup_hour)"
        )[0]
        return row[0], row[1]

    # ------------------------------------------------------------------- time
    def ts(self, v: str | datetime, is_end: bool) -> float:
        if isinstance(v, str):
            if len(v) == 10:  # date only = local day
                d = date.fromisoformat(v) + timedelta(days=1 if is_end else 0)
                return datetime(d.year, d.month, d.day, tzinfo=self.tz).timestamp()
            v = datetime.fromisoformat(v)
        if v.tzinfo is None:
            v = v.replace(tzinfo=self.tz)
        return v.timestamp()

    def hours(self, flt: Filter) -> tuple[int | None, int | None]:
        """Filter bounds as [start_hour, end_hour) UTC hour numbers."""
        s = None if flt.start is None else int(self.ts(flt.start, False) // 3600)
        e = None if flt.end is None else -int(-self.ts(flt.end, True) // 3600)
        return s, e

    def local_day(self, hour: int) -> int:
        return local_day(hour, self.tz_offset_minutes)

    def day_first_hour(self, day: int) -> int:
        return day_first_hour(day, self.tz_offset_minutes)

    @staticmethod
    def day_str(day_number: int) -> str:
        return (EPOCH + timedelta(days=day_number)).isoformat()

    def iso(self, ts: float | None) -> str | None:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, self.tz).isoformat(timespec="seconds")

    def range_filter(self, rng: str, base: Filter | None = None) -> Filter:
        if rng not in RANGES:
            raise ValueError(f"range must be one of {sorted(RANGES)}")
        b = base or Filter()
        if rng == "all":
            return Filter(b.team, b.email, b.project, b.model, None, None)
        today = datetime.fromtimestamp(self.now(), self.tz).date()
        first = today - timedelta(days=RANGES[rng] - 1)
        return Filter(b.team, b.email, b.project, b.model, first.isoformat(), today.isoformat())

    # ------------------------------------------------------------------ names
    def names(self) -> dict[str, str]:
        """device_id -> 'Unknown account (user@host)' label."""
        return {
            d.device_id: f"Unknown account ({d.windows_user}@{d.hostname})"
            if d.windows_user or d.hostname
            else f"Unknown account ({d.device_id})"
            for d in self.devices()
        }

    @staticmethod
    def display(key: str, names: dict[str, str]) -> str:
        if key.startswith("device:"):
            dev = key[len("device:") :]
            return names.get(dev, f"Unknown account ({dev})")
        return key
