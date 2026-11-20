"""Adapter half of the performance bench (real Manager + real Adapter processes).

Scenario (each number is recorded in the result dict):
  import   ``-X importtime`` of tokenvault.adapter.main, wall import time, heavy modules
  backfill generated history (default 60k messages, ~3 KB tool output per turn) ->
           time until the Manager has every message, CPU per 1 s sample (sustained =
           worst 5 s average), RSS, bytes written by the adapter, outbox/WAL size
  startup  spawn -> "adapter started" in its log, spawn -> state "connected"
  idle     60 s after backfill, no tray: CPU %, wake-ups/s (context switches), RSS
  latency  append one response -> visible in the Manager (session_turns), N samples
  burst    5k assistant lines appended at once -> all in the Manager, CPU, RSS
  tray     a second adapter WITH the tray icon, idle 60 s
"""

from __future__ import annotations

import json
import random
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

import bench_util as bu
import procs

HERE = Path(__file__).resolve().parent
E2E = HERE.parent / "e2e"
_PAD = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "


# ------------------------------------------------------------------ history
def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _lines(sid: str, cwd: str, ts: datetime, mid: str, rng: random.Random, pad: int,
           parts: int = 2) -> list[str]:
    base = {"parentUuid": None, "isSidechain": False, "userType": "external", "cwd": cwd,
            "sessionId": sid, "version": "2.1.0", "gitBranch": "main"}
    tool = (_PAD * (pad // len(_PAD) + 1))[: max(0, pad + rng.randint(-pad // 2, pad // 2))]
    out = [json.dumps({**base, "type": "user", "uuid": f"u{rng.getrandbits(64):x}",
                       "timestamp": _iso(ts), "message": {"role": "user", "content": [
                           {"type": "tool_result", "tool_use_id": "toolu_x", "content": tool}]}})]
    for p in range(parts):
        out.append(json.dumps({**base, "type": "assistant", "uuid": f"a{rng.getrandbits(64):x}",
                               "timestamp": _iso(ts + timedelta(seconds=1 + p)), "message": {
                                   "id": mid, "model": "claude-sonnet-4-6", "role": "assistant",
                                   "content": [{"type": "tool_use", "id": "t", "name": "Read",
                                                "input": {"file_path": "x"}}],
                                   "usage": {"input_tokens": 5, "output_tokens": 50 * (p + 1),
                                             "cache_read_input_tokens": 1000,
                                             "cache_creation_input_tokens": 20}}}))
    return out


def gen_history(projects: Path, messages: int, files: int, pad: int, days: int = 85
                ) -> dict[str, Any]:
    rng = random.Random(42)
    now = datetime.now(UTC)
    per = max(1, messages // files)
    total_bytes = written = 0
    for f in range(files):
        n = per if f < files - 1 else messages - per * (files - 1)
        cwd = f"C:\\Users\\bench\\work\\proj{f % 12}"
        folder = projects / ("C--Users-bench-work-proj" + str(f % 12))
        folder.mkdir(parents=True, exist_ok=True)
        sid = f"{rng.getrandbits(128):032x}"
        start = now - timedelta(days=days * (files - f) / files, hours=1)
        buf = []
        for i in range(n):
            buf.extend(_lines(sid, cwd, start + timedelta(seconds=20 * i), f"msg_b{f}_{i}",
                              rng, pad))
        text = "\n".join(buf) + "\n"
        path = folder / f"{sid}.jsonl"
        path.write_text(text, encoding="utf-8", newline="")
        total_bytes += len(text.encode("utf-8"))
        written += n
    return {"messages": written, "files": files, "mb": round(total_bytes / 2**20, 1)}


# ------------------------------------------------------------------ processes
class Manager:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.template = work / "adapter-setup-template.exe"
        self.template.write_bytes(b"MZ" + bytes(8192))
        self.proc: Any = None
        self.api: procs.ManagerApi | None = None
        self.info: dict[str, Any] = {}

    def start(self) -> None:
        self.proc = procs.spawn(
            [str(E2E / "manager_host.py"), "--data-dir", str(self.work / "manager-data"),
             "--template", str(self.template), "--ingest-port", str(procs.free_port()),
             "--discovery-port", str(procs.free_port(2))],
            log=self.work / "manager-console.log", pipe_stdout=True, stdin_pipe=True)
        self.info = procs.read_json_line(self.proc, 90)
        self.api = procs.ManagerApi(self.info["url"], self.info["token"])

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"stop\n")
                self.proc.stdin.flush()
                self.proc.wait(20)
            except Exception:  # noqa: BLE001
                procs.stop(self.proc)


class Adapter:
    def __init__(self, work: Path, user: str) -> None:
        self.user = user
        self.home = work / "homes" / user
        self.projects = self.home / ".claude" / "projects"
        self.local = self.home / "AppData" / "Local"
        self.data = self.local / "TokenVault" / "Adapter"
        self.proc: Any = None
        self.mutex = f"Local\\TokenVaultBench-{work.name}-{user}"

    def env(self) -> dict[str, str]:
        e = procs.base_env()
        h = str(self.home)
        e.update(USERPROFILE=h, HOME=h, USERNAME=self.user, USER=self.user,
                 LOCALAPPDATA=str(self.local), APPDATA=str(self.home / "AppData" / "Roaming"),
                 TOKENVAULT_MUTEX_NAME=self.mutex)
        return e

    def setup(self, installer: Path) -> None:
        self.local.mkdir(parents=True, exist_ok=True)
        (self.home / ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"emailAddress": f"{self.user}@bench.local"}}), encoding="utf-8")
        r = procs.run(["-m", "tokenvault.adapter.main", "setup", "--from", str(installer),
                       "--silent", "--accept", "--no-start", "--no-autostart"], env=self.env())
        if r.returncode != 0:
            raise RuntimeError(f"setup failed: {r.stderr[-500:]}")

    def start(self, tray: bool = False) -> float:
        args = ["-m", "tokenvault.adapter.main", "run"] + ([] if tray else ["--no-tray"])
        t0 = time.monotonic()
        self.proc = procs.spawn(args, env=self.env(), log=self.home / "console.log")
        return t0

    def log_has(self, text: str) -> bool:
        try:
            return text in (self.data / "adapter.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    def state(self) -> str:
        try:
            return json.loads((self.data / "state.json").read_text(encoding="utf-8"))["state"]
        except (OSError, ValueError, KeyError):
            return ""

    def stop(self) -> None:
        procs.stop(self.proc)


# ------------------------------------------------------------------ steps
def import_profile() -> dict[str, Any]:
    walls = []
    for _ in range(3):
        r = procs.run(["-c", "import time,sys;t=time.perf_counter();import tokenvault.adapter.main;"
                       "print(time.perf_counter()-t);print(','.join(sorted({m.split('.')[0] "
                       "for m in sys.modules})))"])
        lines = r.stdout.strip().splitlines()
        walls.append(float(lines[0]))
        mods = set(lines[1].split(","))
    r = procs.run(["-X", "importtime", "-c", "import tokenvault.adapter.main"])
    top = []
    for line in r.stderr.splitlines():
        m = re.match(r"import time:\s+(\d+) \|\s+(\d+) \|(\s*)(\S+)", line)
        if m and len(m.group(3)) <= 3:
            top.append((int(m.group(2)), m.group(4)))
    top.sort(reverse=True)
    heavy = ("PIL", "pystray", "tkinter", "cryptography", "websockets", "watchdog", "asyncio",
             "ssl", "sqlite3")
    return {"wall_ms_median": round(1000 * median(walls), 1),
            "top_cumulative_ms": [(n, round(us / 1000, 1)) for us, n in top[:8]],
            "loaded": {h: h in mods for h in heavy}, "modules": len(mods)}


def _events(api: procs.ManagerApi) -> int:
    return int(api.overview(None)["totals"]["events"])


def run(work: Path, a: Any) -> dict[str, Any]:
    res: dict[str, Any] = {"import": import_profile()}
    print("adapter import:", res["import"]["wall_ms_median"], "ms", flush=True)
    mgr = Manager(work)
    mgr.start()
    api = mgr.api
    assert api is not None
    inst = Path(api.create_installer("Bench", "bench")["path"])
    ad = Adapter(work, "bench")
    ad.setup(inst)
    res["history"] = gen_history(ad.projects, a.messages, a.files, a.pad)
    print("history:", res["history"], flush=True)
    try:
        t0 = ad.start()
        pid = ad.proc.pid
        mine = lambda s: [pid] if pid in s else []  # noqa: E731
        procs.wait_until(lambda: ad.log_has("adapter started"), 30, 0.01)
        started = time.monotonic() - t0
        procs.wait_until(lambda: ad.log_has("transport connected"), 60, 0.01)
        res["startup"] = {"to_started_s": round(started, 3),
                          "to_connected_s": round(time.monotonic() - t0, 3)}
        want = res["history"]["messages"]
        box = {"n": 0}

        def done() -> bool:
            box["n"] = _events(api)
            return box["n"] >= want

        # sampled from "connected" (~0.5 s after spawn) until the Manager has everything
        w = bu.measure(mine, a.backfill_timeout, until=done)
        res["backfill_wall_since_spawn_s"] = round(time.monotonic() - t0, 2)
        bf = w.summary()
        bf.update(delivered=box["n"], expected=want, complete=box["n"] >= want,
                  outbox_mb=round((ad.data / "outbox.db").stat().st_size / 2**20, 1),
                  wal_mb=round(_size(ad.data / "outbox.db-wal") / 2**20, 1),
                  msgs_per_s=round(box["n"] / max(w.seconds, 1e-9), 1))
        res["backfill"] = bf
        print("backfill:", bf, flush=True)
        time.sleep(5)
        res["idle_no_tray"] = bu.measure(mine, a.idle_seconds).summary()
        print("idle (no tray):", res["idle_no_tray"], flush=True)
        res["latency"] = _latency(ad, api, a.latency_samples)
        print("latency:", res["latency"], flush=True)
        res["burst"] = _burst(ad, api, a.burst, mine)
        print("burst:", res["burst"], flush=True)
    finally:
        ad.stop()
    tr = Adapter(work, "benchtray")
    tr.setup(inst)
    gen_history(tr.projects, 200, 2, 500)
    try:
        tr.start(tray=True)
        tpid = tr.proc.pid
        procs.wait_until(lambda: tr.state() == "connected", 60, 0.1)
        time.sleep(10)
        res["idle_tray"] = bu.measure(lambda s: [tpid] if tpid in s else [],
                                      a.idle_seconds).summary()
        print("idle (tray):", res["idle_tray"], flush=True)
    finally:
        tr.stop()
        mgr.stop()
    return res


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _live_file(ad: Adapter) -> tuple[Path, str, str]:
    sid = "f" * 32
    cwd = "C:\\Users\\bench\\work\\live"
    path = ad.projects / "C--Users-bench-work-live" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path, sid, cwd


def _latency(ad: Adapter, api: procs.ManagerApi, n: int) -> dict[str, Any]:
    path, sid, cwd = _live_file(ad)
    rng = random.Random(7)
    lat: list[float] = []
    for i in range(n):
        before = len(api.session_turns(sid))
        text = "\n".join(_lines(sid, cwd, datetime.now(UTC), f"msg_lat_{i}_{time.time()}",
                                rng, 800, parts=1)) + "\n"
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(text)
        t0 = time.monotonic()
        if procs.wait_until(lambda b=before: len(api.session_turns(sid)) > b, 30, 0.02):
            lat.append(time.monotonic() - t0)
        time.sleep(1.5)
    return {"n": len(lat), "median_s": round(median(lat), 3) if lat else None,
            "p90_s": round(bu.pct(lat, 90), 3), "max_s": round(max(lat), 3) if lat else None}


def _burst(ad: Adapter, api: procs.ManagerApi, n: int, mine: Any) -> dict[str, Any]:
    path, sid, cwd = _live_file(ad)
    rng = random.Random(9)
    before = _events(api)
    now = datetime.now(UTC)
    chunks = ["\n".join(_lines(sid, cwd, now, f"msg_burst_{i}_{time.time()}", rng, 300,
                               parts=1)[1:]) + "\n" for i in range(n)]
    t0 = time.monotonic()
    with open(path, "a", encoding="utf-8", newline="") as fh:
        for i in range(0, n, 500):
            fh.write("".join(chunks[i:i + 500]))
            fh.flush()
    w = bu.measure(mine, 300, interval=0.5, until=lambda: _events(api) >= before + n)
    s = w.summary()
    s.update(lines=n, all_in_manager_s=round(time.monotonic() - t0, 2),
             arrived=_events(api) - before)
    return s
