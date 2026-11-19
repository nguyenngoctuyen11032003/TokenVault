"""TokenVault end-to-end harness: real Manager + real Adapters in separate processes.

    <venv>\\Scripts\\python tests\\e2e\\run_e2e.py [--work DIR] [--idle-seconds 60]
                                                 [--burst 5000] [--keep]

What runs (each a separate OS process, own data dir):
  * ``manager_host.py`` - the real Manager back end + its HTTP API (token protected),
  * ``wire_proxy.py``   - TLS proxy presenting the Manager's own cert, recording plaintext,
  * ``python -m tokenvault.adapter.main setup|run`` per fake employee, each with its own
    USERPROFILE/HOME (sample transcripts + ~/.claude.json), LOCALAPPDATA (default
    adapter data dir), USERNAME (-> device id) and singleton mutex name.

Steps (numbering = the Task 23 checklist; run order differs so revocation is last):
  1 Manager up   2 installers -> setup -> run   3 ground truth vs API   4 latency
  5 offline/online catch-up + adapter kill      9 resources (idle + burst), final GT check
  8 PDF reports  6 revoke / re-enroll / forged secret / pin mismatch   7 privacy grep

Exit code 0 when every step passed. Results: ``<work>/e2e-results.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import ground_truth as gt  # noqa: E402
import procs  # noqa: E402
import sample_data as sd  # noqa: E402

TZ = 420
HOST = socket.gethostname()
PRICING = HERE.parents[1] / "data" / "pricing.json"
DIRECT = {"carol"}  # these adapters use the installer endpoint unmodified (no proxy)


def redact(s: Any) -> Any:
    """Keep this machine's host name out of result files."""
    if isinstance(s, str):
        return s.replace(HOST, "<host>")
    if isinstance(s, dict):
        return {redact(k): redact(v) for k, v in s.items()}
    if isinstance(s, list):
        return [redact(v) for v in s]
    return s


# ------------------------------------------------------------------ results
@dataclass
class Step:
    name: str
    failures: list[str] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def check(self, ok: bool, what: str) -> bool:
        if not ok:
            self.failures.append(what)
            print(f"   FAIL {what}", flush=True)
        return ok

    @property
    def status(self) -> str:
        return "PASS" if not self.failures and not self.error else "FAIL"


# ------------------------------------------------------------------ agents
@dataclass
class Agent:
    emp: sd.Employee
    run_id: str
    proc: Any = None
    tag: str = ""  # suffix for re-installs (separate LOCALAPPDATA)

    @property
    def device_id(self) -> str:
        # the adapter picks host:user:<random> on first setup and keeps it in device.id
        try:
            return (self.data_dir / "device.id").read_text(encoding="utf-8").strip()
        except OSError:
            return f"{HOST}:{self.emp.user}"

    @property
    def key(self) -> str:
        return self.emp.email or f"device:{self.device_id}"

    @property
    def local(self) -> Path:
        return self.emp.home / ("AppData" + self.tag) / "Local"

    @property
    def data_dir(self) -> Path:  # the adapter's DEFAULT data dir under the fake LOCALAPPDATA
        return self.local / "TokenVault" / "Adapter"

    @property
    def log(self) -> Path:
        return self.emp.home / f"adapter-console{self.tag}.log"

    def env(self) -> dict[str, str]:
        e = procs.base_env()
        h = str(self.emp.home)
        e.update(USERPROFILE=h, HOME=h, USERNAME=self.emp.user, USER=self.emp.user,
                 LOGNAME=self.emp.user, LOCALAPPDATA=str(self.local),
                 APPDATA=str(self.emp.home / ("AppData" + self.tag) / "Roaming"),
                 TOKENVAULT_MUTEX_NAME=f"Local\\TokenVaultE2E-{self.run_id}-{self.emp.user}"
                 f"{self.tag}")
        return e

    def setup(self, installer: Path) -> Any:
        self.local.mkdir(parents=True, exist_ok=True)
        return procs.run(["-m", "tokenvault.adapter.main", "setup", "--from", str(installer),
                          "--silent", "--accept", "--no-start", "--no-autostart"],
                         env=self.env())

    def start(self) -> None:
        self.proc = procs.spawn(["-m", "tokenvault.adapter.main", "run", "--no-tray"],
                                env=self.env(), log=self.log)

    def kill(self) -> None:
        procs.stop(self.proc)

    def config(self) -> dict[str, Any]:
        return json.loads((self.data_dir / "config.json").read_text(encoding="utf-8"))

    def set_port(self, port: int) -> None:
        cfg = self.config()
        cfg["port"] = port
        (self.data_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    def state(self) -> dict[str, Any]:
        try:
            return json.loads((self.data_dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


# ------------------------------------------------------------------ Manager process
class ManagerProc:
    def __init__(self, work: Path, ingest_port: int, discovery_port: int) -> None:
        self.data = work / "manager-data"
        self.template = work / "adapter-setup-template.exe"
        if not self.template.exists():
            self.template.write_bytes(b"MZ" + bytes(8192))
        self.ingest_port, self.discovery_port = ingest_port, discovery_port
        self.log = work / "manager-console.log"
        self.proc: Any = None
        self.info: dict[str, Any] = {}
        self.api: procs.ManagerApi | None = None

    def start(self) -> dict[str, Any]:
        self.proc = procs.spawn(
            [str(HERE / "manager_host.py"), "--data-dir", str(self.data), "--template",
             str(self.template), "--ingest-port", str(self.ingest_port), "--discovery-port",
             str(self.discovery_port), "--tz", str(TZ)],
            log=self.log, pipe_stdout=True, stdin_pipe=True)
        self.info = procs.read_json_line(self.proc, 90)
        self.api = procs.ManagerApi(self.info["url"], self.info["token"])
        return self.info

    def stop_clean(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"stop\n")
                self.proc.stdin.flush()
                self.proc.wait(20)
            except Exception:  # noqa: BLE001
                procs.stop(self.proc)

    def kill(self) -> None:
        procs.stop(self.proc)


# ------------------------------------------------------------------ helpers
class Writer(threading.Thread):
    """Appends (user, assistant) line pairs to a transcript at a fixed rate."""

    def __init__(self, path: Path, user: str, session: str, cwd: str, count: int, rate: float,
                 prefix: str, split_lines: bool = False) -> None:
        super().__init__(daemon=True)
        self.path, self.user, self.session, self.cwd = path, user, session, cwd
        self.count, self.rate, self.prefix, self.split = count, rate, prefix, split_lines
        self.written = 0

    def run(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        gap = 1.0 / self.rate
        for i in range(self.count):
            text = sd.live_line_pair(self.user, self.session, self.cwd, datetime.now(UTC),
                                     f"{self.prefix}_{i}", output=100 + i)
            with open(self.path, "a", encoding="utf-8", newline="") as fh:
                if self.split:  # a line written in two pieces (still being written)
                    half = len(text) - 40
                    fh.write(text[:half])
                    fh.flush()
                    time.sleep(min(gap / 2, 0.05))
                    fh.write(text[half:])
                else:
                    fh.write(text)
            self.written += 1
            time.sleep(gap)


def devices_of(gen: sd.Generated, agents: dict[str, Agent]) -> list[gt.Device]:
    return [gt.Device(a.device_id, a.emp.email, a.emp.team, a.emp.projects_dir)
            for a in agents.values() if a.tag == ""]


def close(a: float | None, b: float | None, tol: float = 2e-6) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol + 1e-9 * abs(b)


TOK = (("input", "input_tokens"), ("output", "output_tokens"), ("cache_read", "cache_read_tokens"),
       ("c5", "cache_create_5m_tokens"), ("c1", "cache_create_1h_tokens"),
       ("total", "total_tokens"))


def cmp_tokens(step: Step, label: str, truth: dict[str, Any], row: dict[str, Any]) -> None:
    for tk, ak in TOK:
        step.check(truth[tk] == row.get(ak), f"{label} {ak}: manager {row.get(ak)} != truth "
                   f"{truth[tk]}")


def compare_all(step: Step, api: procs.ManagerApi, truth: gt.Truth, agents: dict[str, Agent],
                label: str, expect_online: bool = True) -> None:
    """Every Manager view that the owner looks at vs the independent ground truth."""
    ov = api.overview(None)
    t, o = truth.overall.summary(), ov["totals"]
    cmp_tokens(step, f"[{label}] overview", t, o)
    step.check(t["events"] == o["events"], f"[{label}] overview events {o['events']} != "
               f"{t['events']}")
    step.check(t["sessions"] == o["sessions"], f"[{label}] overview sessions {o['sessions']} != "
               f"{t['sessions']}")
    step.check(close(o["cost"], t["cost"]), f"[{label}] overview cost {o['cost']} != {t['cost']}")
    step.check(o["cost_partial"] == t["partial"], f"[{label}] cost_partial {o['cost_partial']}")
    step.check(sd.UNKNOWN_MODEL in ov["unknown_cost_models"],
               f"[{label}] unknown model not flagged: {ov['unknown_cost_models']}")
    # per local day (UTC+7)
    api_days = {d["day"]: d for d in ov["daily"]}
    for day, b in truth.by_day.items():
        d = api_days.get(day)
        if step.check(d is not None, f"[{label}] day {day} missing in Manager daily"):
            assert d is not None
            step.check(d["total_tokens"] == b.total,
                       f"[{label}] day {day} tokens {d['total_tokens']} != {b.total}")
            step.check(close(d["cost"], float(b.cost)),
                       f"[{label}] day {day} cost {d['cost']} != {float(b.cost)}")
    extra = [d for d, v in api_days.items() if d not in truth.by_day and v["total_tokens"]]
    step.check(not extra, f"[{label}] Manager has tokens on days the truth does not: {extra}")
    # by model / project
    api_models = {m["model"]: m for m in ov["by_model"]}
    for model, b in truth.by_model.items():
        m = api_models.get(model)
        step.check(m is not None and m["total_tokens"] == b.total and m["events"] == b.events,
                   f"[{label}] model {model}: {m and (m['total_tokens'], m['events'])} != "
                   f"{(b.total, b.events)}")
    step.check(set(api_models) == set(truth.by_model),
               f"[{label}] model set {sorted(api_models)} != {sorted(truth.by_model)}")
    api_proj = {p["project"]: p for p in ov["by_project"]}
    for proj, b in truth.by_project.items():
        p = api_proj.get(proj)
        step.check(p is not None and p["total_tokens"] == b.total
                   and p["sessions"] == len(b.sessions),
                   f"[{label}] project {proj}: {p and (p['total_tokens'], p['sessions'])} != "
                   f"{(b.total, len(b.sessions))}")
    step.check(set(api_proj) == set(truth.by_project),
               f"[{label}] project set {sorted(api_proj)} != {sorted(truth.by_project)}")
    # employees
    emps = {e["key"]: e for e in api.employees(None)}
    for key, b in truth.by_employee.items():
        e = emps.get(key)
        if not step.check(e is not None, f"[{label}] employee {key} missing"):
            continue
        assert e is not None
        s = b.summary()
        cmp_tokens(step, f"[{label}] employee {key}", s, e)
        step.check(e["events"] == s["events"] and e["sessions"] == s["sessions"],
                   f"[{label}] employee {key} events/sessions {(e['events'], e['sessions'])} != "
                   f"{(s['events'], s['sessions'])}")
        step.check(close(e["cost"], s["cost"]), f"[{label}] employee {key} cost {e['cost']} != "
                   f"{s['cost']}")
        step.check(e["cost_partial"] == s["partial"], f"[{label}] employee {key} partial flag")
        step.check(len(e["devices"]) == len(b.devices),
                   f"[{label}] employee {key} devices {len(e['devices'])} != {len(b.devices)}")
        if expect_online:
            step.check(e["online"] is True, f"[{label}] employee {key} not online")
        teams = {a.emp.team for a in agents.values() if a.key == key and a.tag == ""}
        step.check(set(e["teams"]) == teams, f"[{label}] employee {key} teams {e['teams']}")
        det = api.employee(key, "all")
        step.check({p["project"] for p in det["by_project"]} == set(s["projects"]),
                   f"[{label}] employee {key} projects {[p['project'] for p in det['by_project']]}"
                   f" != {s['projects']}")
        step.check({m["model"] for m in det["by_model"]} == set(s["models"]),
                   f"[{label}] employee {key} models differ")
        ddays = {d["day"]: d["total_tokens"] for d in det["daily"] if d["total_tokens"]}
        tdays = {day: bb.total for (k, day), bb in truth.by_employee_day.items() if k == key}
        step.check(ddays == tdays, f"[{label}] employee {key} daily {ddays} != {tdays}")
    step.check(set(emps) == set(truth.by_employee),
               f"[{label}] employee keys {sorted(emps)} != {sorted(truth.by_employee)}")
    # teams
    tb = {k: v.summary() for k, v in truth.by_team.items()}
    for team, s in tb.items():
        ot = api.overview({"team": team})["totals"]
        step.check(ot["total_tokens"] == s["total"] and ot["events"] == s["events"],
                   f"[{label}] team {team} {(ot['total_tokens'], ot['events'])} != "
                   f"{(s['total'], s['events'])}")
    # sessions + titles
    rows = api.sessions(None)
    step.check(len(rows) == truth.overall.summary()["sessions"],
               f"[{label}] sessions list {len(rows)} != {truth.overall.summary()['sessions']}")
    got = {(r["device_id"], r["session_id"]): r["title"] for r in rows}
    bad_titles = [(redact(k[0]), k[1][:8], got.get(k), v) for k, v in truth.titles.items()
                  if got.get(k) != v]
    step.check(not bad_titles, f"[{label}] session titles differ: {bad_titles[:5]}")
    orch = api.orchestration("all")
    step.check(orch["sidechain_tokens"] == truth.sidechain_tokens,
               f"[{label}] sidechain tokens {orch['sidechain_tokens']} != "
               f"{truth.sidechain_tokens}")
    step.numbers[f"{label}_compared"] = {
        "events": t["events"], "sessions": t["sessions"], "total_tokens": t["total"],
        "cost_usd": round(t["cost"], 4), "employees": len(truth.by_employee),
        "days": len(truth.by_day), "models": len(truth.by_model),
        "projects": len(truth.by_project), "titles": len(truth.titles)}


def wait_converged(api: procs.ManagerApi, truth: gt.Truth, timeout: float) -> float | None:
    t0 = time.monotonic()
    want = (truth.overall.events, truth.overall.total)

    def ok() -> bool:
        o = api.overview(None)["totals"]
        return (o["events"], o["total_tokens"]) == want

    return time.monotonic() - t0 if procs.wait_until(ok, timeout, 0.2) else None


def manager_events(api: procs.ManagerApi) -> int:
    return int(api.overview(None)["totals"]["events"])


# ------------------------------------------------------------------ the run
class Harness:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="tv-e2e-"))
        self.work.mkdir(parents=True, exist_ok=True)
        self.run_id = secrets.token_hex(3)
        self.steps: dict[str, Step] = {}
        self.agents: dict[str, Agent] = {}
        self.extra_agents: list[Agent] = []
        self.proxy: Any = None
        self.capture = self.work / "wire-capture"

    def step(self, key: str, name: str) -> Step:
        s = Step(name)
        self.steps[key] = s
        print(f"\n== {key}. {name}", flush=True)
        return s

    def truth(self) -> gt.Truth:
        return gt.compute(devices_of(self.gen, self.agents), PRICING, TZ)

    # 1 -------------------------------------------------------------------
    def s1_manager(self) -> None:
        s = self.step("1", "Manager back end up (real lifecycle, own process, temp dirs)")
        self.gen = sd.generate(self.work)
        self.mgr = ManagerProc(self.work, procs.free_port(), procs.free_port(socket.SOCK_DGRAM))
        t0 = time.monotonic()
        info = self.mgr.start()
        s.numbers["startup_s"] = round(time.monotonic() - t0, 2)
        s.check(Path(info["tokenvault"]).resolve().is_relative_to(procs.SRC),
                f"manager imports tokenvault from {info['tokenvault']}")
        s.check(info["ingest_port"] == self.mgr.ingest_port and not info["ingest_error"],
                f"ingest bound {info['ingest_port']} (wanted {self.mgr.ingest_port}) "
                f"error={info['ingest_error']}")
        api = self.mgr.api
        assert api is not None
        diag = api.diagnostics()
        s.numbers["diagnostics_keys"] = sorted(diag)[:12]
        # The token is enforced: a request without it is refused.
        try:
            procs.ManagerApi(info["url"], "wrong")("overview", None)
            s.check(False, "API accepted a request with a wrong token")
        except procs.ApiError as e:
            s.check("403" in str(e), f"wrong token -> {e}")

    # 2 -------------------------------------------------------------------
    def s2_install(self) -> None:
        s = self.step("2", "create_installer -> adapter setup --from <installer> -> adapter run")
        from tokenvault.common.config_payload import read_payload

        api = self.mgr.api
        assert api is not None
        self.installers: dict[str, Path] = {}
        self.package_ids: dict[str, str] = {}
        for team in ("Dev", "QA"):
            r = api.create_installer(team, f"E2E {team}")
            p = Path(r["path"])
            self.installers[team], self.package_ids[team] = p, r["package_id"]
            pl = read_payload(p)
            s.check(pl is not None and pl.port == self.mgr.ingest_port
                    and pl.fingerprint == self.mgr.info["fingerprint"] and pl.team == team
                    and pl.hosts == ["127.0.0.1"], f"payload of {team} installer: {pl!r}")
        # recording proxy (presents the Manager's own certificate)
        self.proxy = procs.spawn(
            [str(HERE / "wire_proxy.py"), "--cert-dir", str(self.mgr.data / "certs"),
             "--upstream", str(self.mgr.ingest_port), "--capture", str(self.capture)],
            log=self.work / "proxy-console.log", pipe_stdout=True)
        self.proxy_port = int(procs.read_json_line(self.proxy, 60)["port"])
        setup_times = []
        for emp in self.gen.employees:
            ag = Agent(emp, self.run_id)
            self.agents[emp.user] = ag
            t0 = time.monotonic()
            r = ag.setup(self.installers[emp.team])
            setup_times.append(time.monotonic() - t0)
            s.check(r.returncode == 0, f"setup {emp.user} exit {r.returncode}: {r.stderr[-400:]}")
            cfg = ag.config()
            s.check(cfg["port"] == self.mgr.ingest_port and cfg["team"] == emp.team,
                    f"{emp.user} config {cfg}")
            s.check(not (ag.data_dir / "device.key").exists()
                    and (ag.data_dir / "enroll.secret").exists(), f"{emp.user} secret files")
            raw_cfg = (ag.data_dir / "config.json").read_bytes()
            s.check(b"secret" not in raw_cfg.lower(), f"{emp.user} config.json mentions secret")
            if emp.user not in DIRECT:
                ag.set_port(self.proxy_port)
        s.numbers["setup_s_each"] = [round(x, 2) for x in setup_times]
        t0 = time.monotonic()
        for ag in self.agents.values():
            ag.start()
        want = {a.device_id: a.emp.team for a in self.agents.values()}

        def all_online() -> bool:
            devs = {d["device_id"]: d for d in api.devices()}
            return all(k in devs and devs[k]["online"] and devs[k]["team"] == v
                       for k, v in want.items())

        ok = procs.wait_until(all_online, 60, 0.2)
        s.numbers["all_devices_online_s"] = round(time.monotonic() - t0, 2)
        s.check(ok, f"devices online/teams: {redact(api.devices())}")
        for ag in self.agents.values():
            s.check(procs.wait_until(lambda ag=ag: (ag.data_dir / "device.key").exists()
                                     and not (ag.data_dir / "enroll.secret").exists(), 20),
                    f"{ag.emp.user}: device key stored and enroll secret deleted after Welcome")
        pk = {p["package_id"]: p["devices"] for p in api.packages()}
        s.check(pk.get(self.package_ids["Dev"]) == 3 and pk.get(self.package_ids["QA"]) == 2,
                f"package device counts {pk}")
        s.numbers["teams"] = api.teams()

    # 3 -------------------------------------------------------------------
    def s3_truth(self) -> None:
        s = self.step("3", "independent ground truth == Manager API (overview/employees/days/"
                      "sessions/titles/online)")
        api = self.mgr.api
        assert api is not None
        truth = self.truth()
        s.numbers["truth_stats"] = truth.stats
        conv = wait_converged(api, truth, 90)
        s.numbers["initial_backfill_converged_s"] = conv and round(conv, 2)
        s.check(conv is not None, f"Manager never converged: {api.overview(None)['totals']} vs "
                f"events={truth.overall.events} tokens={truth.overall.total}")
        compare_all(s, api, truth, self.agents, "initial")
        # employees of both teams online; dan (no account) appears as an unknown account
        emps = {e["key"]: e for e in api.employees(None)}
        dan = self.agents["dan"]
        s.check(dan.key in emps and "dan" in emps[dan.key]["display_name"],
                f"dan (no account) row: {redact(emps.get(dan.key))}")
        s.numbers["unknown_account_label"] = redact(emps.get(dan.key, {}).get("display_name"))
        s.check(len(emps["alice@congty.vn"]["devices"]) == 2, "alice's two devices not merged")

    # 4 -------------------------------------------------------------------
    def s4_latency(self) -> None:
        s = self.step("4", "latency: transcript append -> visible in Manager API")
        api = self.mgr.api
        assert api is not None
        g = self.gen
        cwd = f"C:\\Users\\bob\\{sd.MARK}-work\\live-proj"
        lat: list[float] = []
        for i in range(self.a.latency_samples):
            before = len(api.session_turns(g.live_session))
            text = sd.live_line_pair("bob", g.live_session, cwd, datetime.now(UTC),
                                     f"msg_lat_{self.run_id}_{i}")
            with open(g.live_file, "a", encoding="utf-8", newline="") as fh:
                fh.write(text)
            t0 = time.monotonic()
            ok = procs.wait_until(lambda b=before: len(api.session_turns(g.live_session)) > b,
                                  30, 0.02)
            if s.check(ok, f"latency sample {i} never arrived"):
                lat.append(time.monotonic() - t0)
            time.sleep(1.5)
        if lat:
            lat_sorted = sorted(lat)
            s.numbers["latency_s"] = {
                "n": len(lat), "median": round(median(lat), 3),
                "p90": round(lat_sorted[int(0.9 * (len(lat) - 1))], 3),
                "max": round(max(lat), 3), "min": round(min(lat), 3)}
            s.check(median(lat) <= 2.5, f"median latency {median(lat):.2f}s > 2.5s")
        # partial last line (carol, direct connection): completed now -> must arrive
        before = manager_events(api)
        with open(g.partial_file, "a", encoding="utf-8", newline="") as fh:
            fh.write(g.partial_completion)
        t0 = time.monotonic()
        ok = procs.wait_until(lambda: manager_events(api) == before + 1, 30, 0.02)
        s.check(ok, "completed partial line never arrived")
        if ok:
            s.numbers["partial_line_completion_latency_s"] = round(time.monotonic() - t0, 3)
        # title appended AFTER the session's events were delivered
        with open(g.live_file, "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps({"type": "ai-title", "sessionId": g.live_session,
                                 "aiTitle": "bob live renamed"}) + "\n")
        time.sleep(4)
        title_only = (api.session(g.live_session) or {}).get("title")
        s.numbers["title_after_delivery_without_new_event"] = title_only
        if title_only != "bob live renamed":
            s.notes.append("A title line appended after all of a session's events were "
                           "delivered is not shown until the session's NEXT response "
                           f"(Manager still shows {title_only!r}). By design the title rides on "
                           "events; see report.")
        text = sd.live_line_pair("bob", g.live_session, cwd, datetime.now(UTC),
                                 f"msg_title_{self.run_id}")
        with open(g.live_file, "a", encoding="utf-8", newline="") as fh:
            fh.write(text)
        ok = procs.wait_until(lambda: (api.session(g.live_session) or {}).get("title")
                              == "bob live renamed", 20, 0.05)
        s.check(ok, "renamed title not shown even after the next event")
        s.notes.append("Machine is shared with other agents' builds/tests: latency numbers are "
                       "pessimistic.")

    # 5 -------------------------------------------------------------------
    def s5_catchup(self) -> None:
        s = self.step("5", "offline/online catch-up (Manager killed + restarted; adapter killed "
                      "mid-write)")
        api_old = self.mgr.api
        assert api_old is not None
        g = self.gen
        bob_cwd = f"C:\\Users\\bob\\{sd.MARK}-work\\live-proj"
        al = self.agents["alice"]
        al_cwd = f"C:\\Users\\alice\\{sd.MARK}-work\\new-proj"
        al_file = (al.emp.projects_dir / sd.encode_cwd(al_cwd)
                   / "aaaaaaaa-0000-4000-8000-0000000000aa.jsonl")
        # a) Manager crash (TerminateProcess) while adapters keep writing
        self.mgr.kill()
        s.numbers["manager_killed"] = True
        w1 = Writer(g.live_file, "bob", g.live_session, bob_cwd, 150, 30, f"msg_off_{self.run_id}",
                    split_lines=True)
        w2 = Writer(al_file, "alice", al_file.stem, al_cwd, 80, 20, f"msg_offa_{self.run_id}")
        w1.start()
        w2.start()
        w1.join()
        w2.join()
        time.sleep(2)
        states = {u: a.state().get("state") for u, a in self.agents.items()}
        s.numbers["adapter_states_while_manager_down"] = states
        s.check(all(v in ("offline", "connecting") for v in states.values()),
                f"adapter states while Manager down: {states}")
        waiting = {}
        for u in ("bob", "alice"):
            st = procs.run(["-m", "tokenvault.adapter.main", "status", "--json"],
                           env=self.agents[u].env())
            try:
                waiting[u] = json.loads(st.stdout)["outbox"]["waiting"]
            except (ValueError, KeyError):
                waiting[u] = f"status failed: {st.stderr[-200:]}"
        s.numbers["outbox_waiting_while_down"] = waiting
        s.check(waiting.get("bob", 0) >= 150 and waiting.get("alice", 0) >= 80,
                f"backlog not queued in outboxes: {waiting}")
        t0 = time.monotonic()
        self.mgr.start()
        api = self.mgr.api
        assert api is not None
        truth = self.truth()
        conv = wait_converged(api, truth, 150)
        s.numbers["catchup_after_manager_restart_s"] = conv and round(time.monotonic() - t0, 2)
        s.check(conv is not None, f"backlog did not arrive: {api.overview(None)['totals']} vs "
                f"{truth.overall.events}")
        compare_all(s, api, truth, self.agents, "after_manager_restart")
        # b) adapter killed mid-write, restarted later
        bob = self.agents["bob"]
        w3 = Writer(g.live_file, "bob", g.live_session, bob_cwd, 300, 100,
                    f"msg_kill_{self.run_id}", split_lines=True)
        w3.start()
        time.sleep(1.0)
        bob.kill()
        s.numbers["bob_killed_after_lines"] = w3.written
        w3.join()
        time.sleep(1)
        bob.start()
        t0 = time.monotonic()
        truth = self.truth()
        conv = wait_converged(api, truth, 120)
        s.numbers["catchup_after_adapter_restart_s"] = conv and round(time.monotonic() - t0, 2)
        s.check(conv is not None, "bob's backlog after kill/restart did not arrive")
        compare_all(s, api, truth, self.agents, "after_adapter_kill")
        # duplicates: raw rows == unique keys (read-only look at the Manager DB)
        con = sqlite3.connect(f"file:{self.mgr.data / 'manager.db'}?mode=ro", uri=True)
        try:
            n, nk = con.execute("SELECT COUNT(*), COUNT(DISTINCT device_id || '|' || msg_key) "
                                "FROM events").fetchone()
            integ = con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()
        s.numbers["db_rows"] = n
        s.check(n == nk == truth.overall.events, f"db rows {n} distinct {nk} truth "
                f"{truth.overall.events}")
        s.check(integ == "ok", f"db quick_check {integ}")

    # 9 -------------------------------------------------------------------
    def s9_resources(self) -> None:
        s = self.step("9", "adapter resource use (idle + burst)")
        api = self.mgr.api
        assert api is not None
        bob = self.agents["bob"]
        g = self.gen
        time.sleep(3)
        secs = self.a.idle_seconds
        c0 = procs.cpu_seconds(bob.proc)
        rss: list[int] = []
        priv: list[int] = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < secs:
            w, p = procs.memory(bob.proc)
            rss.append(w)
            priv.append(p)
            time.sleep(1)
        c1 = procs.cpu_seconds(bob.proc)
        el = time.monotonic() - t0
        s.numbers["idle"] = {"seconds": round(el, 1), "cpu_s": round(c1 - c0, 3),
                             "cpu_pct_of_one_core": round(100 * (c1 - c0) / el, 3),
                             "rss_mb_avg": round(sum(rss) / len(rss) / 2**20, 1),
                             "rss_mb_max": round(max(rss) / 2**20, 1),
                             "private_mb_max": round(max(priv) / 2**20, 1)}
        s.check((c1 - c0) / el < 0.01, f"idle CPU {(c1 - c0) / el * 100:.2f}% of a core >= 1%")
        # burst: N assistant lines written as fast as possible
        n = self.a.burst
        before = manager_events(api)
        cwd = f"C:\\Users\\bob\\{sd.MARK}-work\\live-proj"
        chunks = []
        for i in range(n):
            pair = sd.live_line_pair("bob", g.live_session, cwd, datetime.now(UTC),
                                     f"msg_burst_{self.run_id}_{i}", output=10 + i % 50)
            chunks.append(pair.split("\n")[1] + "\n")  # assistant line only
        c0 = procs.cpu_seconds(bob.proc)
        t0 = time.monotonic()
        peak = 0
        with open(g.live_file, "a", encoding="utf-8", newline="") as fh:
            for i in range(0, n, 500):
                fh.write("".join(chunks[i:i + 500]))
                fh.flush()
        write_s = time.monotonic() - t0

        def done() -> bool:
            nonlocal peak
            peak = max(peak, procs.memory(bob.proc)[0])
            return manager_events(api) >= before + n

        ok = procs.wait_until(done, 300, 0.1)
        el = time.monotonic() - t0
        c1 = procs.cpu_seconds(bob.proc)
        s.check(ok, f"burst: only {manager_events(api) - before}/{n} arrived")
        s.numbers["burst"] = {"lines": n, "write_s": round(write_s, 3),
                              "all_in_manager_s": round(el, 2), "adapter_cpu_s": round(c1 - c0, 2),
                              "cpu_pct_of_one_core_during": round(100 * (c1 - c0) / el, 1),
                              "rss_mb_peak": round(peak / 2**20, 1),
                              "events_per_s": round(n / el, 1)}
        truth = self.truth()
        conv = wait_converged(api, truth, 60)
        s.check(conv is not None, "final ground truth not converged after burst")
        compare_all(s, api, truth, self.agents, "final")
        self.final_truth = truth

    # 8 -------------------------------------------------------------------
    def s8_pdf(self) -> None:
        s = self.step("8", "PDF reports (all + per person) via the Api, parsed with pypdf")
        from pypdf import PdfReader

        api = self.mgr.api
        assert api is not None
        truth = self.final_truth

        def fmt_int(n: int) -> str:
            return f"{n:,}".replace(",", ".")

        def pdf_text(path: str) -> tuple[str, bytes, int]:
            r = PdfReader(path)
            return "\n".join(p.extract_text() or "" for p in r.pages), Path(path).read_bytes(), \
                len(r.pages)

        def norm(t: str) -> str:
            return "".join(t.split())

        out: dict[str, Any] = {}
        specs = {
            "all": {"scope": "all", "range": "30d"},
            "alice": {"scope": "employee", "employee_keys": ["alice@congty.vn"], "range": "30d"},
            "team_QA": {"scope": "team", "team": "QA", "range": "30d"},
        }
        expect = {"all": truth.overall.summary(),
                  "alice": truth.by_employee["alice@congty.vn"].summary(),
                  "team_QA": truth.by_team["QA"].summary()}
        for name, spec in specs.items():
            try:
                res = api.export_report(spec, timeout=180)
            except procs.ApiError as e:
                s.check(False, f"export {name}: {e}")
                continue
            text, raw, pages = pdf_text(res["path"])
            e = expect[name]
            tot, cost = fmt_int(e["total"]), f"${e['cost']:.2f}"
            out[name] = {"pages": pages, "bytes": len(raw), "total_tokens": tot,
                         "cost": cost + ("+" if e["partial"] else "")}
            s.check(norm(tot) in norm(text), f"{name}: total tokens {tot} not in PDF text")
            s.check(norm(cost) in norm(text), f"{name}: cost {cost} not in PDF text")
            s.check(fmt_int(e["sessions"]) in text, f"{name}: sessions {e['sessions']} not in PDF")
            s.check(sd.MARK not in text and sd.MARK.encode() not in raw,
                    f"{name}: privacy marker in PDF")
            for title in truth.titles.values():
                if title in text:
                    s.check(False, f"{name}: session title {title!r} in PDF (titles must be "
                            "excluded)")
                    break
            if name == "all":
                for k in ("alice@congty.vn", "bob@congty.vn", "carol@congty.vn"):
                    s.check(k in text, f"all: employee {k} not in PDF")
        s.numbers["reports"] = out

    # 6 -------------------------------------------------------------------
    def s6_security(self) -> None:
        s = self.step("6", "revoke device/package, re-enroll, forged secret, wrong team, pin")
        from tokenvault.common.config_payload import PayloadConfig, append_payload, read_payload
        from tokenvault.manager.tls import ensure_certificate, load_server_context

        api = self.mgr.api
        assert api is not None

        def rejected(ag: Agent, timeout: float = 30) -> bool:
            return procs.wait_until(lambda: ag.state().get("state") == "rejected", timeout, 0.2)

        def extra(user: str, team_home: str, tag: str = "") -> Agent:
            emp = sd.Employee(user, f"{user}@congty.vn", team_home, 99,
                              self.work / "homes" / user)
            if not emp.home.exists():
                (emp.projects_dir / "C--x-proj").mkdir(parents=True)
                (emp.home / ".claude.json").write_text(json.dumps(
                    {"oauthAccount": {"emailAddress": emp.email, "accountUuid": "u-" + user}}))
                (emp.projects_dir / "C--x-proj" / "s.jsonl").write_text(sd.live_line_pair(
                    user, "ffffffff-0000-4000-8000-00000000000f", "C:\\x\\proj",
                    datetime.now(UTC) - timedelta(minutes=5), f"msg_{user}_1"), encoding="utf-8")
            ag = Agent(emp, self.run_id, tag=tag)
            self.extra_agents.append(ag)
            return ag

        # a) revoke one device: live session closed, later lines never land, device hidden
        lap = self.agents["alice-laptop"]
        s.check(wait_converged(api, self.truth(), 90) is not None, "not converged before revoke")
        before =api.employee("alice@congty.vn", "all")["totals"]["events"]
        api.revoke_device(lap.device_id)
        s.check(rejected(lap), f"alice-laptop adapter not rejected: {lap.state()}")
        lap_file = sorted(gt.jsonl_files(lap.emp.projects_dir))[0]
        sid = json.loads(next(gt.complete_lines(lap_file)))["sessionId"]
        with open(lap_file, "a", encoding="utf-8", newline="") as fh:
            fh.write(sd.live_line_pair("alice-laptop", sid, "C:\\x\\webapp", datetime.now(UTC),
                                       f"msg_after_revoke_{self.run_id}"))
        time.sleep(5)
        after = api.employee("alice@congty.vn", "all")["totals"]["events"]
        s.check(after == before, f"events after revoke landed: {before} -> {after}")
        s.check(all(d["device_id"] != lap.device_id for d in api.devices()),
                "revoked device still listed")
        s.numbers["revoked_device_state"] = redact(lap.state())
        # b) re-install on the revoked device (fresh data dir, same device id) -> refused
        re = Agent(lap.emp, self.run_id, tag="-reinstall")
        self.extra_agents.append(re)
        re.data_dir.mkdir(parents=True, exist_ok=True)  # same machine: same device id
        (re.data_dir / "device.id").write_text(lap.device_id, encoding="utf-8")
        r = re.setup(self.installers["Dev"])
        s.check(r.returncode == 0, f"re-install setup exit {r.returncode}")
        re.set_port(self.proxy_port)
        re.start()
        s.check(rejected(re), f"re-enroll of revoked device not rejected: {re.state()}")
        s.check(not (re.data_dir / "device.key").exists(), "revoked device got a new key")
        s.check(all(d["device_id"] != lap.device_id for d in api.devices()),
                "revoked device re-appeared")
        # c) forged secret (valid format, unknown) -> refused, nothing lands
        dev_pl = read_payload(self.installers["Dev"])
        assert dev_pl is not None
        forged = self.work / "forged-installer.exe"
        append_payload(self.mgr.template, forged, PayloadConfig(
            dev_pl.manager_id, dev_pl.hosts, dev_pl.port, dev_pl.fingerprint,
            secrets.token_urlsafe(32), "Dev"))
        fg = extra("mallory", "Dev")
        s.check(fg.setup(forged).returncode == 0, "forged setup failed")
        fg.start()
        s.check(rejected(fg), f"forged secret not rejected: {fg.state()}")
        # d) wrong team claimed with a valid Dev secret -> lands in the package's team (Dev)
        wt = self.work / "wrongteam-installer.exe"
        append_payload(self.mgr.template, wt, PayloadConfig(
            dev_pl.manager_id, dev_pl.hosts, dev_pl.port, dev_pl.fingerprint, dev_pl.secret, "QA"))
        wtag = extra("trudy", "QA")
        s.check(wtag.setup(wt).returncode == 0, "wrong-team setup failed")
        wtag.start()
        ok = procs.wait_until(lambda: {d["device_id"]: d["team"] for d in api.devices()}.get(
            wtag.device_id) == "Dev", 30, 0.2)
        s.check(ok, f"claimed team QA not overridden by package team Dev: "
                f"{[redact(d) for d in api.devices() if d['device_id'] == wtag.device_id]}")
        # e) device of the Dev package re-enrolling with the QA secret (key lost) -> refused
        wtag.kill()
        (wtag.data_dir / "device.key").unlink(missing_ok=True)
        r = wtag.setup(self.installers["QA"])
        s.check(r.returncode == 0, "QA setup on a Dev device failed")
        wtag.start()
        s.check(rejected(wtag), f"cross-package re-enroll not rejected: {wtag.state()}")
        s.check({d["device_id"]: d["team"] for d in api.devices()}.get(wtag.device_id) == "Dev",
                "device moved team by re-enrolling with another package")
        # f) revoke the QA package: carol and dan cut, new QA installs refused
        api.revoke_package(self.package_ids["QA"])
        carol, dan = self.agents["carol"], self.agents["dan"]
        s.check(rejected(carol) and rejected(dan), f"QA devices not cut: {carol.state()} "
                f"{dan.state()}")
        devs = {d["device_id"] for d in api.devices()}
        s.check(carol.device_id not in devs and dan.device_id not in devs,
                "revoked package devices still listed")
        new_qa = extra("erin", "QA")
        s.check(new_qa.setup(self.installers["QA"]).returncode == 0, "erin setup failed")
        new_qa.start()
        s.check(rejected(new_qa), f"enrollment with revoked package not rejected: "
                f"{new_qa.state()}")
        # g) certificate pin mismatch: an impostor with another cert gets ZERO app bytes
        imp_cert = ensure_certificate(self.work / "impostor-certs")
        ctx = load_server_context(imp_cert)
        lsock = socket.socket()
        lsock.bind(("127.0.0.1", 0))
        lsock.listen()
        imp_port = lsock.getsockname()[1]
        rec = {"accepted": 0, "handshakes": 0, "bytes": 0}
        stop = threading.Event()

        def serve() -> None:
            lsock.settimeout(0.2)
            while not stop.is_set():
                try:
                    c, _ = lsock.accept()
                except OSError:
                    continue
                rec["accepted"] += 1
                try:
                    c.settimeout(5)
                    with ctx.wrap_socket(c, server_side=True) as tl:
                        rec["handshakes"] += 1
                        while chunk := tl.recv(65536):
                            rec["bytes"] += len(chunk)
                except (OSError, ssl.SSLError):
                    pass
                finally:
                    c.close()

        th = threading.Thread(target=serve, daemon=True)
        th.start()
        pin = extra("gina", "Dev")
        s.check(pin.setup(self.installers["Dev"]).returncode == 0, "gina setup failed")
        pin.set_port(imp_port)
        pin.start()
        procs.wait_until(lambda: rec["accepted"] >= 2, 30, 0.2)
        # state.json is throttled (one write per 5 s): wait for the failure reason to land
        procs.wait_until(lambda: "mismatch" in (pin.state().get("last_error") or ""), 15, 0.2)
        stop.set()
        th.join(3)
        lsock.close()
        st = pin.state()
        s.numbers["pin_mismatch"] = {"impostor_tcp_accepts": rec["accepted"],
                                     "impostor_app_bytes_received": rec["bytes"],
                                     "adapter_state": st.get("state"),
                                     "adapter_last_error": st.get("last_error")}
        s.check(rec["accepted"] >= 1, "adapter never contacted the impostor")
        s.check(rec["bytes"] == 0, f"impostor received {rec['bytes']} application bytes")
        s.check("mismatch" in (st.get("last_error") or ""), f"adapter state {st}")
        s.check(pin.device_id not in {d["device_id"] for d in api.devices()},
                "pin-mismatch device reached the Manager")
        s.check(fg.device_id not in {d["device_id"] for d in api.devices()},
                "forged-secret device reached the Manager")
        s.check(new_qa.device_id not in {d["device_id"] for d in api.devices()},
                "revoked-package enrollment reached the Manager")

    # 7 -------------------------------------------------------------------
    def s7_privacy(self) -> None:
        s = self.step("7", "privacy: marker never in Manager files or captured wire frames")
        mark = sd.MARK.encode()
        needles = [mark, mark.lower(), sd.MARK.encode("utf-16-le")]
        hits: dict[str, int] = {}
        scanned = {"files": 0, "bytes": 0}

        def scan(root: Path, label: str) -> None:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                try:
                    b = p.read_bytes()
                except OSError:
                    continue
                scanned["files"] += 1
                scanned["bytes"] += len(b)
                n = sum(b.count(x) for x in needles)
                if n:
                    hits[f"{label}/{p.relative_to(root)}"] = n

        self.mgr.stop_clean()  # flush WAL into the DB file too, then scan everything
        scan(self.mgr.data, "manager-data")
        for ag in [*self.agents.values(), *self.extra_agents]:
            scan(ag.data_dir, f"adapter-{ag.emp.user}{ag.tag}")
        cap_raw = Path(str(self.capture) + ".raw").read_bytes()
        cap_msgs = Path(str(self.capture) + ".jsonl").read_text(encoding="utf-8")
        s.numbers["wire_capture_bytes"] = len(cap_raw)
        s.check(len(cap_raw) > 10_000, "wire capture too small to be meaningful")
        for x in needles:
            if x in cap_raw:
                hits["wire.raw"] = cap_raw.count(x)
        if sd.MARK in cap_msgs:
            hits["wire.frames"] = cap_msgs.count(sd.MARK)
        s.numbers["scanned"] = scanned
        s.numbers["marker_hits"] = redact(hits)
        s.check(not hits, f"privacy marker found: {redact(hits)}")
        # what the frames DO carry (sanity: capture really holds the decoded event stream)
        keys: set[str] = set()
        types: dict[str, int] = {}
        for line in cap_msgs.splitlines():
            rec = json.loads(line)
            if rec["op"] != 1:
                continue
            try:
                msg = json.loads(rec["text"])
            except ValueError:
                continue
            types[f"{rec['dir']}:{msg.get('type')}"] = types.get(
                f"{rec['dir']}:{msg.get('type')}", 0) + 1
            for ev in msg.get("events", []) if isinstance(msg, dict) else []:
                keys |= set(ev)
        s.numbers["frame_types"] = types
        s.numbers["event_fields_on_wire"] = sorted(keys)
        s.check(types.get("c2s:batch", 0) > 0, "no batch frames captured")
        # (client frames are masked in .raw; the decoded frames are the meaningful check)
        s.check("alice@congty.vn" in cap_msgs and "bob live session" in cap_msgs,
                "decoded frames lack the expected e-mail/title (capture broken?)")
        leaked = [t for t in ("thinking about", "Xin chào", "file content", "grep -r",
                              "Here is the reply", "-work", "login.py") if t in cap_msgs]
        s.check(not leaked, f"prompt/reply/path fragments on the wire: {leaked}")

    # ---------------------------------------------------------------------
    def teardown(self) -> None:
        for ag in [*self.agents.values(), *self.extra_agents]:
            ag.kill()
        procs.stop(self.proxy)
        if hasattr(self, "mgr"):
            self.mgr.stop_clean()

    def run(self) -> int:
        order: list[tuple[str, Callable[[], None]]] = [
            ("1", self.s1_manager), ("2", self.s2_install), ("3", self.s3_truth),
            ("4", self.s4_latency), ("5", self.s5_catchup), ("9", self.s9_resources),
            ("8", self.s8_pdf), ("6", self.s6_security), ("7", self.s7_privacy)]
        started = time.time()
        try:
            for key, fn in order:
                if self.a.only and key not in self.a.only.split(","):
                    continue
                try:
                    fn()
                except Exception as e:  # noqa: BLE001 - record and continue
                    st = self.steps.get(key) or self.step(key, fn.__name__)
                    st.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
                    print(f"   ERROR {st.error}", flush=True)
                    if key in ("1", "2"):
                        break
        finally:
            self.teardown()
        res = {
            "date": datetime.now().astimezone().isoformat(timespec="seconds"),
            "duration_s": round(time.time() - started, 1), "work": str(self.work),
            "python": sys.version.split()[0], "cpus": os.cpu_count(),
            "steps": {k: {"name": v.name, "status": v.status, "failures": v.failures,
                          "numbers": v.numbers, "notes": v.notes, "error": v.error}
                      for k, v in sorted(self.steps.items())},
        }
        res = redact(res)
        (self.work / "e2e-results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False),
                                                    encoding="utf-8")
        print("\n== SUMMARY")
        for k, v in sorted(self.steps.items()):
            print(f" {k}. {v.status}  {v.name}" + (f"  ({len(v.failures)} failures)"
                                                   if v.failures else ""))
        print(f"results: {self.work / 'e2e-results.json'}")
        want = len(self.a.only.split(",")) if self.a.only else 9
        ok = all(v.status == "PASS" for v in self.steps.values()) and len(self.steps) == want
        if ok and not self.a.keep:
            shutil.rmtree(self.work, ignore_errors=True)
        return 0 if ok else 1


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", help="work folder (default: new temp folder)")
    ap.add_argument("--idle-seconds", type=float, default=60)
    ap.add_argument("--burst", type=int, default=5000)
    ap.add_argument("--latency-samples", type=int, default=20)
    ap.add_argument("--keep", action="store_true", help="keep the work folder even on success")
    ap.add_argument("--only", help="comma list of steps to run (development; 1,2 always needed)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    return Harness(parse(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
