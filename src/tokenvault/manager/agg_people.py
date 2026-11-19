"""Employee views for :class:`~tokenvault.manager.aggregates.Aggregates`.

Split out of ``aggregates.py`` to keep files small. Employee identity is the
Claude account e-mail; devices without one get the key ``"device:<id>"`` and
the label ``"Unknown account (windows_user@hostname)"``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from tokenvault.manager.agg_models import DeviceInfo, EmployeeRow, EmployeeView, Filter

if TYPE_CHECKING:
    from tokenvault.manager.aggregates import Aggregates


def _device_key(device_id: str, email: str | None) -> str:
    return email if email else f"device:{device_id}"


def employees(agg: Aggregates, flt: Filter) -> list[EmployeeRow]:
    from tokenvault.manager.aggregates import _JOIN, _KEY, _SUMS, _Acc, _tok

    where, params = agg._where(flt)
    rows = agg._q(
        f"SELECT r.device_id, {_KEY} AS k, r.model, {_SUMS}, SUM(r.events) "
        f"FROM rollup_hour r {_JOIN}{where} GROUP BY r.device_id, k, r.model",
        params,
    )
    sessions = agg._session_counts(flt, _KEY)
    devices = {d.device_id: d for d in agg.store.list_devices()}
    online = set(agg.store.devices_online(agg._now()))
    names = agg._names()

    accs: dict[str, _Acc] = defaultdict(_Acc)
    devs: dict[str, set[str]] = defaultdict(set)
    for dev, key, model, *rest in rows:
        accs[key].add(agg.pricing, model, rest[:5], rest[5])
        devs[key].add(dev)
    seen = {d for ds in devs.values() for d in ds}
    if flt.project is None and flt.model is None:
        for d in devices.values():
            if d.device_id in seen or (flt.team is not None and d.team != flt.team):
                continue
            key = _device_key(d.device_id, d.email)
            if flt.email is not None and key != flt.email:
                continue
            accs[key]  # noqa: B018 - create the zero row
            devs[key].add(d.device_id)

    grand = sum(a.total for a in accs.values())
    out = []
    for key, a in accs.items():
        ds = sorted(devs[key])
        known = [devices[d] for d in ds if d in devices]
        last = max((d.last_seen for d in known if d.last_seen), default=None)
        out.append(
            EmployeeRow(
                **_tok(a.t),
                key=key,
                email=None if key.startswith("device:") else key,
                display_name=agg._display(key, names),
                devices=ds,
                teams=sorted({d.team for d in known if d.team}),
                online=any(d in online for d in ds),
                last_seen=agg._iso(last),
                events=a.events,
                sessions=sessions.get(key, 0),
                cost=a.cost,
                cost_partial=a.partial,
                share=round(a.total / grand, 6) if grand else 0.0,
            )
        )
    return sorted(out, key=lambda r: (-r.total_tokens, r.display_name))


def employee(agg: Aggregates, key: str, rng: str) -> EmployeeView:
    from tokenvault.manager.aggregates import _JOIN

    f = agg._range_filter(rng, Filter(email=key))
    s = agg._summary(f)
    total = s["total"]
    n_sess = sum(agg._session_counts(f, "1").values())
    names = agg._names()

    where, params = agg._where(Filter(email=key))
    dev_ids = {str(r[0]) for r in agg._q(
        f"SELECT DISTINCT r.device_id FROM rollup_hour r {_JOIN}{where}", params
    )}  # fmt: skip
    all_devs = agg.store.list_devices()
    dev_ids |= {d.device_id for d in all_devs if _device_key(d.device_id, d.email) == key}
    online = set(agg.store.devices_online(agg._now()))
    infos = [
        DeviceInfo(
            d.device_id,
            d.team,
            d.windows_user,
            d.hostname,
            d.device_id in online,
            agg._iso(d.last_seen) if d.last_seen else None,
        )
        for d in all_devs
        if d.device_id in dev_ids
    ]
    return EmployeeView(
        key=key,
        email=None if key.startswith("device:") else key,
        display_name=agg._display(key, names),
        range=rng,
        totals=agg._totals(total, n_sess),
        hourly=agg._hourly(f),
        daily=s["daily"],
        by_project=agg._project_rows(f, s["by_project"]),
        by_model=s["by_model"],
        sessions=agg._sessions(f, 200),
        devices=infos,
        unknown_cost_models=sorted(total.unknown),
    )
