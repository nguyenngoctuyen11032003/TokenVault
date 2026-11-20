"""Manager half of the performance bench.

  dataset  ``--events`` (default 2M) events for ``--employees`` x ``--days`` written with
           ``Store.upsert_events`` in 1000-event batches (= store ingest throughput);
           cached in ``--db-cache`` and copied for each run
  queries  every Aggregates read (overview, employees, employee, projects, sessions,
           skills, orchestration, session_turns) x range (today/7d/30d/90d/all):
           p50/p95 over ``--reps`` runs, cold first run separately; every distinct SQL
           statement timed on its own + EXPLAIN QUERY PLAN for the slowest ones
  backend  ``tests/e2e/manager_host.py`` on the big database: idle 60 s, then 60 s with
           ``--clients`` connected adapters (bench_clients.py), ingest of 2000 events
           per client, HTTP API p95 (incl. JSON), PDF export of all employees
"""

from __future__ import annotations

import json
import random
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

import bench_util as bu
import procs
from bench_adapter import Manager

from tokenvault.common.models import UsageEvent
from tokenvault.common.pricing import Pricing
from tokenvault.manager.aggregates import Aggregates
from tokenvault.manager.paths import pricing_path
from tokenvault.manager.store import Store

TZ = 420
HERE = Path(__file__).resolve().parent
MODELS = ("claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-sonnet-4-5")
TOOLS = ("Read", "Edit", "Bash", "Grep", "Skill", "Task", "Write", "WebFetch")
RANGES = ("today", "7d", "30d", "90d", "all")


# ------------------------------------------------------------------ dataset
def build_db(path: Path, events: int, employees: int, days: int) -> dict[str, Any]:
    rng = random.Random(1)
    store = Store(path, tz_offset_minutes=TZ)
    per_emp_day = max(1, events // (employees * days))
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    n = 0
    t0 = time.perf_counter()
    for e in range(employees):
        dev = f"host{e:02d}:emp{e:02d}"
        email = f"emp{e:02d}@bench.local"
        team = ("Dev", "QA", "Ops", "Data")[e % 4]
        store.upsert_device(dev, team, email, time.time(), f"emp{e:02d}", f"host{e:02d}")
        projects = [f"proj{(e * 3 + k) % 30}" for k in range(6)]
        batch: list[UsageEvent] = []
        for d in range(days):
            day0 = now - timedelta(days=days - 1 - d)
            sessions = [f"{e:02d}{d:03d}{s:02d}" + "0" * 25 for s in range(8)]
            for k in range(per_emp_day):
                ts = day0 - timedelta(seconds=rng.randint(0, 36000))
                sid = sessions[k * 8 // per_emp_day]
                tools = tuple(rng.sample(TOOLS, rng.randint(0, 2)))
                batch.append(UsageEvent(
                    uuid=f"u{e}-{d}-{k}", session_id=sid, message_id=f"m{e}-{d}-{k}",
                    timestamp=ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"), model=rng.choice(MODELS),
                    reasoning_effort=None, is_sidechain=rng.random() < 0.2,
                    input_tokens=rng.randint(1, 60), output_tokens=rng.randint(50, 4000),
                    cache_read_tokens=rng.randint(0, 90000), cache_create_5m_tokens=rng.randint(
                        0, 6000), cache_create_1h_tokens=rng.randint(0, 3000),
                    project=projects[hash(sid) % 6], session_title=f"title {sid[:6]}",
                    tool_names=tools, claude_account_email=email, account_uuid=None,
                    windows_user=f"emp{e:02d}", hostname=f"host{e:02d}", team=team,
                    device_id=dev))
                if len(batch) >= 1000:
                    n += store.upsert_events(dev, batch)
                    batch = []
        if batch:
            n += store.upsert_events(dev, batch)
    el = time.perf_counter() - t0
    store.close()
    return {"events": n, "seconds": round(el, 1), "store_events_per_s": round(n / el),
            "db_mb": round(path.stat().st_size / 2**20, 1)}


def ensure_db(a: Any) -> tuple[Path, dict[str, Any]]:
    cache = Path(a.db_cache)
    cache.mkdir(parents=True, exist_ok=True)
    db = cache / f"bench-{a.events}-{a.employees}-{a.days}.db"
    meta = db.with_suffix(".json")
    if not db.exists() or not meta.exists():
        tmp = db.with_suffix(".tmp")
        for suf in ("", "-wal", "-shm"):
            Path(str(tmp) + suf).unlink(missing_ok=True)
        info = build_db(tmp, a.events, a.employees, a.days)
        tmp.replace(db)
        meta.write_text(json.dumps(info), encoding="utf-8")
    return db, json.loads(meta.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ queries
def _calls(agg: Aggregates) -> dict[str, Any]:
    key = "emp07@bench.local"
    sid = agg.ctx.q("SELECT session_id FROM rollup_session ORDER BY last_ts DESC LIMIT 1")[0][0]
    out: dict[str, Any] = {}
    for r in RANGES:
        f = agg.ctx.range_filter(r)
        out[f"overview/{r}"] = lambda f=f: agg.overview(f)
        out[f"employees/{r}"] = lambda f=f: agg.employees(f)
        out[f"projects/{r}"] = lambda f=f: agg.projects(f)
        out[f"sessions/{r}"] = lambda f=f: agg.sessions(f)
        out[f"employee/{r}"] = lambda r=r: agg.employee(key, r)
        if r != "all":
            out[f"skills/{r}"] = lambda r=r: agg.skills(r)
            out[f"orchestration/{r}"] = lambda r=r: agg.orchestration(r)
    out["session_turns"] = lambda: agg.session_turns(sid)
    return out


def time_queries(db: Path, reps: int) -> dict[str, Any]:
    store = Store(db, tz_offset_minutes=TZ)
    agg = Aggregates(store, Pricing.load(pricing_path()), TZ)
    stmts: dict[str, int] = {}
    store._rcon.set_trace_callback(lambda s: stmts.__setitem__(s, stmts.get(s, 0) + 1))
    res: dict[str, Any] = {}
    for name, fn in _calls(agg).items():
        t0 = time.perf_counter()
        fn()
        cold = time.perf_counter() - t0
        times = []
        for _ in range(reps):
            t0 = time.perf_counter()
            fn()
            times.append(time.perf_counter() - t0)
        res[name] = {"cold_ms": round(cold * 1000, 1), "p50_ms": round(median(times) * 1000, 1),
                     "p95_ms": round(bu.pct(times, 95) * 1000, 1)}
    store._rcon.set_trace_callback(None)
    slow = []
    for sql in stmts:
        if not sql.lstrip().upper().startswith("SELECT"):
            continue
        t0 = time.perf_counter()
        store._rcon.execute(sql).fetchall()
        slow.append((time.perf_counter() - t0, sql))
    slow.sort(reverse=True)
    plans = []
    for el, sql in slow[:12]:
        plan = [r[3] for r in store._rcon.execute("EXPLAIN QUERY PLAN " + sql).fetchall()]
        plans.append({"ms": round(el * 1000, 1), "sql": sql[:400], "plan": plan})
    store.close()
    worst = max(res.items(), key=lambda kv: kv[1]["p95_ms"])
    return {"calls": res, "distinct_statements": len(stmts), "slowest_statements": plans,
            "worst_call": {"name": worst[0], **worst[1]}}


# ------------------------------------------------------------------ backend
def backend(work: Path, db: Path, a: Any) -> dict[str, Any]:
    data = work / "manager-data"
    data.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(db, data / "manager.db")
    mgr = Manager(work)
    t0 = time.monotonic()
    mgr.start()
    res: dict[str, Any] = {"startup_s": round(time.monotonic() - t0, 2)}
    api = mgr.api
    assert api is not None
    pid = mgr.proc.pid
    mine = lambda s: [pid] if pid in s else []  # noqa: E731
    try:
        time.sleep(3)
        res["idle_no_clients"] = bu.measure(mine, a.idle_seconds).summary()
        print("manager idle:", res["idle_no_clients"], flush=True)
        res["http"] = _http(api, a.reps)
        print("http:", res["http"], flush=True)
        inst = api.create_installer("Bench", "bench")["path"]
        cl = procs.spawn([str(HERE / "bench_clients.py"), "--installer", inst, "--clients",
                          str(a.clients)], log=work / "clients.log", pipe_stdout=True,
                         stdin_pipe=True)
        try:
            t0 = time.monotonic()
            got = procs.read_json_line(cl, 180)
            res["clients_connect"] = {**got, "seconds": round(time.monotonic() - t0, 2)}
            time.sleep(3)
            res["idle_with_clients"] = bu.measure(mine, a.idle_seconds).summary()
            print("manager idle with clients:", res["idle_with_clients"], flush=True)
            box: dict[str, Any] = {}

            def send() -> None:
                cl.stdin.write(b"send 2000\n")
                cl.stdin.flush()
                box.update(procs.read_json_line(cl, 600))

            import threading

            th = threading.Thread(target=send, daemon=True)
            th.start()
            w = bu.measure(mine, 600, interval=0.5, until=lambda: not th.is_alive())
            ing = w.summary()
            ing.update(box, events_per_s=round(box.get("sent", 0) / max(box.get("seconds", 1),
                                                                         1e-9)))
            res["ingest"] = ing
            print("ingest:", ing, flush=True)
        finally:
            try:
                cl.stdin.write(b"stop\n")
                cl.stdin.flush()
                cl.wait(20)
            except Exception:  # noqa: BLE001
                procs.stop(cl)
        box2: dict[str, Any] = {}

        def pdf() -> None:
            t1 = time.monotonic()
            r = api.export_report({"scope": "all", "range": "30d"}, timeout=600)
            box2.update(seconds=round(time.monotonic() - t1, 2), pages=r.get("pages"))

        import threading

        th2 = threading.Thread(target=pdf, daemon=True)
        th2.start()
        w = bu.measure(mine, 600, interval=0.5, until=lambda: not th2.is_alive())
        res["pdf_all_30d"] = {**box2, **{k: v for k, v in w.summary().items()
                                         if k in ("cpu_pct_of_one_core", "ws_mb_max",
                                                  "private_mb_max")}}
        print("pdf:", res["pdf_all_30d"], flush=True)
    finally:
        mgr.stop()
    return res


def _http(api: procs.ManagerApi, reps: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, args in (("overview", [None]), ("employees", [None]),
                       ("overview", [{"start": None, "end": None}]), ("projects", [None]),
                       ("sessions", [None]), ("skills", ["30d"]), ("orchestration", ["30d"])):
        times = []
        for _ in range(reps):
            t0 = time.perf_counter()
            api(name, *args)
            times.append(time.perf_counter() - t0)
        out[name] = {"p50_ms": round(median(times) * 1000, 1),
                     "p95_ms": round(bu.pct(times, 95) * 1000, 1)}
    return out


def run(work: Path, a: Any) -> dict[str, Any]:
    db, info = ensure_db(a)
    print("dataset:", info, flush=True)
    res: dict[str, Any] = {"dataset": info}
    copy = work / "q.db"
    shutil.copyfile(db, copy)
    res["queries"] = time_queries(copy, a.reps)
    print("worst query:", res["queries"]["worst_call"], flush=True)
    res["backend"] = backend(work, db, a)
    return res
