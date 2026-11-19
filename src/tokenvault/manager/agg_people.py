"""Employee views. Identity = the e-mail frozen on each event at ingest; rows
without one get the key ``"device:<id>"`` and the label
``"Unknown account (windows_user@hostname)"`` (never dropped).
"""

from __future__ import annotations

from collections import defaultdict

from tokenvault.manager.agg_models import DeviceInfo, EmployeeRow, EmployeeView, Filter
from tokenvault.manager.agg_series import (
    daily,
    hourly,
    project_rows,
    session_counts,
    session_total,
    sessions,
    summary,
    totals,
)
from tokenvault.manager.agg_sql import KEY, SUMS, Acc, QueryContext, tok


def _device_key(device_id: str, email: str | None) -> str:
    return email if email else f"device:{device_id}"


def employees(ctx: QueryContext, flt: Filter) -> list[EmployeeRow]:
    """One row per employee key; the same e-mail on several devices merges.

    Devices with no matching usage are listed too (zero tokens, keyed by the
    device's current e-mail) unless the filter names a project or model.
    """
    rows = ctx.seg_query(
        flt,
        "usage",
        f"r.device_id, {KEY}, r.model, {SUMS}, SUM(r.events)",
        f"r.device_id, {KEY}, r.model",
    )
    sess = session_counts(ctx, flt, KEY)
    devices = {d.device_id: d for d in ctx.devices()}
    online = ctx.online()
    names = ctx.names()

    accs: dict[str, Acc] = defaultdict(Acc)
    devs: dict[str, set[str]] = defaultdict(set)
    for _, (dev, key, model, *rest) in rows:
        accs[key].add(ctx.pricing, model, rest[:5], rest[5])
        devs[key].add(dev)
    seen = {d for ds in devs.values() for d in ds}
    if flt.project is None and flt.model is None:
        for d in devices.values():
            if d.device_id in seen or (flt.team is not None and d.team != flt.team):
                continue
            key = _device_key(d.device_id, d.email)
            if flt.email is not None and key != flt.email:
                continue
            accs.setdefault(key, Acc())
            devs[key].add(d.device_id)

    grand = sum(a.total for a in accs.values())
    out = []
    for key, a in accs.items():
        ds = sorted(devs[key])
        known = [devices[d] for d in ds if d in devices]
        last = max((d.last_seen for d in known if d.last_seen), default=None)
        out.append(
            EmployeeRow(
                **tok(a.t),
                key=key,
                email=None if key.startswith("device:") else key,
                display_name=ctx.display(key, names),
                devices=ds,
                teams=sorted({d.team for d in known if d.team}),
                online=any(d in online for d in ds),
                last_seen=ctx.iso(last),
                events=a.events,
                sessions=sess.get(key, 0),
                cost=a.cost,
                cost_partial=a.partial,
                share=round(a.total / grand, 6) if grand else 0.0,
            )
        )
    return sorted(out, key=lambda r: (-r.total_tokens, r.display_name, r.key))


def employee(ctx: QueryContext, key: str, rng: str) -> EmployeeView:
    """Detail for one employee key (e-mail or ``device:<id>``) over a range."""
    f = ctx.range_filter(rng, Filter(email=key))
    s = summary(ctx, f)
    days, _ = daily(ctx, f)
    n_sess = session_total(ctx, f)
    names = ctx.names()
    used = ctx.seg_query(Filter(email=key), "usage", "r.device_id", "r.device_id")
    dev_ids = {str(r[0]) for _, r in used}
    all_devs = ctx.devices()
    dev_ids |= {d.device_id for d in all_devs if _device_key(d.device_id, d.email) == key}
    online = ctx.online()
    infos = [
        DeviceInfo(
            d.device_id,
            d.team,
            d.windows_user,
            d.hostname,
            d.device_id in online,
            ctx.iso(d.last_seen) if d.last_seen else None,
        )
        for d in all_devs
        if d.device_id in dev_ids
    ]
    return EmployeeView(
        key=key,
        email=None if key.startswith("device:") else key,
        display_name=ctx.display(key, names),
        range=rng,
        totals=totals(s.total, n_sess),
        hourly=hourly(ctx, f),
        daily=days,
        monthly=s.monthly,
        by_project=project_rows(ctx, f, s.by_project),
        by_model=s.by_model,
        sessions=sessions(ctx, f, 200),
        devices=infos,
        unknown_cost_models=s.total.unknown_models,
    )
