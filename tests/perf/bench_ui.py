"""UI half of the performance bench.

  boot     the dev server (real Api on demo data) in headless Chromium: time to first
           render, scripts loaded at boot (is the 1 MB echarts bundle fetched before a
           chart page is shown?)
  nav      ``--navigations`` hash-route changes over every menu page: render time per
           route, JS heap after a forced GC and DOM counters (documents, nodes, event
           listeners) before/after, live ECharts instances at the end
  live     20 ``tv.onLive`` pushes in 10 s (the Manager's maximum rate) on the home
           page: API calls they cause
  gui      (``--gui``) the real WebView2 window on demo data via ``--selftest-window``:
           process tree (python + msedgewebview2) CPU / memory over ``--idle-seconds``,
           once shown and once started hidden (``--minimized``)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import median
from typing import Any

import bench_util as bu
import procs

ROUTES = ("home", "employees", "projects", "sessions", "skills", "orchestration", "installer",
          "settings", "diagnostics")
_WAIT_ROUTE = """async (name) => {
  const v = document.getElementById('tv-view');
  const t0 = performance.now();
  location.hash = '#/' + name;
  for (let i = 0; i < 400; i++) {
    await new Promise(r => setTimeout(r, 10));
    if (v.dataset.route === name && v.dataset.routeState && !v.hasAttribute('aria-busy')) break;
  }
  await new Promise(r => setTimeout(r, 60));
  return performance.now() - t0;
}"""


def _devserver(work: Path) -> tuple[Any, str]:
    port = procs.free_port()
    p = procs.spawn(["-m", "tokenvault.manager.app", "--devserver", "--port", str(port),
                     "--data-dir", str(work / "ui-data")], log=work / "devserver.log",
                    pipe_stdout=True)
    info = procs.read_json_line(p, 90)
    return p, info["devserver"].rstrip("/")


def _heap(cdp: Any) -> dict[str, float]:
    cdp.send("HeapProfiler.collectGarbage")
    cdp.send("HeapProfiler.collectGarbage")
    h = cdp.send("Runtime.getHeapUsage")
    d = cdp.send("Memory.getDOMCounters")
    return {"heap_mb": round(h["usedSize"] / 2**20, 2), "nodes": d["nodes"],
            "listeners": d["jsEventListeners"], "documents": d["documents"]}


def browser(work: Path, a: Any) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    srv, url = _devserver(work)
    res: dict[str, Any] = {}
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True)
            # the real UI server sends CSP script-src 'self': let the probes evaluate
            page = br.new_context(bypass_csp=True,
                                  viewport={"width": 1440, "height": 900}).new_page()
            calls: list[str] = []
            page.on("request", lambda r: calls.append(r.url))
            page.goto(f"{url}/#/settings")
            page.wait_for_function("document.documentElement.dataset.tvReady === '1'",
                                   timeout=60000)
            page.wait_for_timeout(500)
            boot_scripts = [c.rsplit("/", 1)[-1] for c in calls if c.endswith(".js")]
            res["boot"] = {
                "first_route": "settings",
                "boot_ms": page.evaluate("globalThis.tv.bootMs"),
                "echarts_loaded_on_non_chart_page": any("echarts" in s for s in boot_scripts),
                "scripts": len(boot_scripts),
            }
            cdp = page.context.new_cdp_session(page)
            for r in ROUTES:  # warm-up: every module + echarts loaded once
                page.evaluate(_WAIT_ROUTE, r)
            before = _heap(cdp)
            per: dict[str, list[float]] = {r: [] for r in ROUTES}
            for i in range(a.navigations):
                r = ROUTES[i % len(ROUTES)]
                per[r].append(page.evaluate(_WAIT_ROUTE, r))
            page.evaluate(_WAIT_ROUTE, "settings")
            after = _heap(cdp)
            charts = page.evaluate("import('./js/charts-core.js').then(m => m.liveCount())")
            res["navigation"] = {
                "navigations": a.navigations, "before": before, "after": after,
                "heap_growth_mb": round(after["heap_mb"] - before["heap_mb"], 2),
                "node_growth": after["nodes"] - before["nodes"],
                "listener_growth": after["listeners"] - before["listeners"],
                "live_charts_on_settings": charts,
                "render_ms_median": {r: round(median(v), 1) for r, v in per.items() if v},
            }
            page.evaluate(_WAIT_ROUTE, "home")
            page.wait_for_timeout(1500)
            n0 = len([c for c in calls if "/api/" in c])
            for _ in range(20):  # the Manager's LivePusher maximum: 2 pushes/s, for 10 s
                page.evaluate("tv.emitLive({online: 1, total: 2})")
                page.wait_for_timeout(500)
            page.wait_for_timeout(1500)
            res["live_20_pushes_10s"] = {"api_calls": len([c for c in calls if "/api/" in c])
                                         - n0}
            br.close()
    finally:
        procs.stop(srv)
    return res


def gui(work: Path, a: Any, hidden: bool) -> dict[str, Any]:
    secs = a.idle_seconds
    args = ["-m", "tokenvault.manager.app", "--selftest-window", "--seconds", str(secs + 25),
            "--screenshot", str(work / f"gui-{int(hidden)}.png")]
    if hidden:
        args.append("--minimized")
    p = procs.spawn(args, log=work / f"gui-{int(hidden)}.log")  # report JSON -> log
    try:
        time.sleep(20)  # boot + first render + transparency probe
        pid = p.pid
        w = bu.measure(lambda s: bu.tree(pid, s), secs)
        snap = bu.snapshot()
        py = snap.get(pid)
        webview = [snap[q] for q in bu.tree(pid, snap) if q != pid]
        out = w.summary()
        out.update(python_ws_mb=round(py.ws / 2**20, 1) if py else None,
                   python_private_mb=round(py.private / 2**20, 1) if py else None,
                   webview_processes=len(webview),
                   webview_ws_mb=round(sum(x.ws for x in webview) / 2**20, 1),
                   webview_private_mb=round(sum(x.private for x in webview) / 2**20, 1))
        return out
    finally:
        try:
            p.wait(120)
        except Exception:  # noqa: BLE001
            procs.stop(p)


def run(work: Path, a: Any) -> dict[str, Any]:
    res: dict[str, Any] = {"browser": browser(work, a)}
    print(json.dumps(res["browser"], indent=1), flush=True)
    if a.gui:
        res["gui_shown"] = gui(work, a, hidden=False)
        print("gui shown:", res["gui_shown"], flush=True)
        res["gui_hidden"] = gui(work, a, hidden=True)
        print("gui hidden:", res["gui_hidden"], flush=True)
    return res
