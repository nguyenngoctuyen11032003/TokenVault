"""``--selftest``: start every Manager component headless, exercise it, stop, exit 0.

Runs in a temporary data folder (never touches the real one): store,
enrollment, certificate, ingest server (127.0.0.1, ephemeral port), discovery
responder (ephemeral UDP port), the loopback UI server, the Api (every read
method, settings, diagnostics, connectivity test, create_installer with a fake
template, package revoke) and the live-push script. Used by the installer smoke
test (Task 18). Prints a JSON report; exit 0 = all steps passed.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import socket
import ssl
import tempfile
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tokenvault.common.config_payload import read_payload
from tokenvault.common.discovery_wire import build_query, parse_answer
from tokenvault.manager.api import Api, dispatch
from tokenvault.manager.api_manage import live_payload
from tokenvault.manager.demo_data import seed_now
from tokenvault.manager.devserver import fake_template
from tokenvault.manager.lifecycle import Manager, ManagerConfig
from tokenvault.manager.live import LivePusher
from tokenvault.manager.paths import pricing_path, template_path, ui_dir
from tokenvault.manager.webserver import UiServer


def _http(port: int, method: str, path: str,
          headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, headers={"Host": f"127.0.0.1:{port}", **headers})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, body


def _check(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError(what)


def run(template: Path | None = None) -> int:
    t0 = time.monotonic()
    steps: list[dict[str, Any]] = []
    root = Path(tempfile.mkdtemp(prefix="tokenvault-selftest-"))
    m: Manager | None = None
    ui: UiServer | None = None

    def step(name: str, fn: Callable[[], Any]) -> None:
        s = time.monotonic()
        try:
            detail = fn()
            steps.append({"step": name, "ok": True, "ms": int((time.monotonic() - s) * 1000),
                          **({"detail": detail} if detail is not None else {})})
        except Exception as e:  # noqa: BLE001 - reported, then the run fails
            steps.append({"step": name, "ok": False, "error": f"{type(e).__name__}: {e}",
                          "trace": traceback.format_exc(limit=3)})
            raise

    try:
        cfg = ManagerConfig(
            data_dir=root / "data", template=fake_template(root / "template.exe"),
            pricing=pricing_path(), ingest_host="127.0.0.1", ingest_port=0,
            discovery_port=0, firewall_check=None, presence_interval=0.5,
            hosts_provider=lambda: ["127.0.0.1"],
        )
        m = Manager(cfg)
        step("start", m.start)
        mgr = m
        api = Api(mgr)
        step("seed", lambda: seed_now(mgr.store, mgr.enrollment, mgr.tz_offset_minutes,
                                      days=7, scale=0.5).events)

        def reads() -> int:
            sess = api.sessions(None)
            emps = api.employees(None)
            out = [api.overview(None), emps, api.projects(None), sess, api.teams(),
                   api.skills("30d"), api.orchestration("30d"), api.devices(), api.packages(),
                   api.settings(), api.diagnostics()]
            if sess:
                out += [api.session(sess[0]["session_id"]),
                        api.session_turns(sess[0]["session_id"])]
            if emps:
                out.append(api.employee(emps[0]["key"], "7d"))
            return len(json.dumps(out, allow_nan=False))

        step("api_reads", reads)

        def tls_probe() -> str:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection(("127.0.0.1", mgr.bound_port()), timeout=5) as raw, \
                    ctx.wrap_socket(raw) as s:
                fp = hashlib.sha256(s.getpeercert(binary_form=True) or b"").hexdigest()
            _check(fp == mgr.cert.fingerprint_sha256, "ingest serves the Manager certificate")
            return f"port {mgr.bound_port()}"

        step("ingest_tls", tls_probe)

        def discovery() -> str:
            d = mgr.discovery
            _check(d is not None and d.bound_port > 0, "discovery responder bound")
            assert d is not None
            nonce = "0123456789abcdef"
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(3)
                s.sendto(build_query(mgr.manager_id, nonce), ("127.0.0.1", d.bound_port))
                ans = parse_answer(s.recvfrom(2048)[0], mgr.manager_id, nonce)
            _check(ans is not None and ans.port == mgr.bound_port(), "discovery answer")
            return f"udp {d.bound_port}"

        step("discovery", discovery)
        def connectivity() -> str:
            checks = api.connectivity_test()["checks"]
            _check(next(c for c in checks if c["name"] == "bind")["ok"], "ingest bind check")
            return "bind ok"

        step("connectivity", connectivity)

        def installer() -> str:
            res = api.create_installer("Selftest", None)
            cfgp = read_payload(Path(res["path"]))
            _check(cfgp is not None and cfgp.port == mgr.bound_port(), "installer payload")
            api.revoke_package(res["package_id"])
            return str(res["bytes"])

        step("create_installer", installer)

        def ui_server() -> str:
            nonlocal ui
            ui = UiServer(ui_dir(), api_call=lambda meth, a: dispatch(api, meth, a))
            ui.start()
            st, h, _ = _http(ui.port, "GET", f"/?tvk={ui.token}", {})
            _check(st == 303, "token redirect")
            cookie = {"Cookie": h["set-cookie"].split(";")[0]}
            st, h, body = _http(ui.port, "GET", "/js/app.js", cookie)
            _check(st == 200 and h["content-type"].startswith("text/javascript"), "js MIME")
            st, _, body = _http(ui.port, "GET", "/", cookie)
            _check(st == 200 and b"<html" in body.lower(), "index.html")
            return ui.url

        step("ui_server", ui_server)

        def live() -> int:
            sent: list[str] = []
            p = LivePusher(sent.append, lambda: live_payload(mgr))
            p.notify()
            p.pump()
            _check(len(sent) == 1 and "tv.onLive(" in sent[0], "live script")
            return len(sent[0])

        step("live_push", live)
        real = template or template_path()
        steps.append({"step": "real_template", "ok": True, "detail": f"{real} exists="
                      f"{real.is_file()}"})
        ok = True
    except Exception:  # noqa: BLE001 - already recorded in steps
        ok = False
    finally:
        if ui is not None:
            ui.stop()
        if m is not None:
            try:
                m.stop()
            except Exception as e:  # noqa: BLE001
                steps.append({"step": "stop", "ok": False, "error": str(e)})
                ok = False
        shutil.rmtree(root, ignore_errors=True)
    print(json.dumps({"ok": ok, "seconds": round(time.monotonic() - t0, 2), "steps": steps},
                     ensure_ascii=False, indent=1), flush=True)
    return 0 if ok else 1
