"""Run the REAL Manager back end (no GUI) in its own process for the E2E harness.

Same components and start order as the app (``lifecycle.Manager``: settings, TLS
certificate, pricing, Store, Enrollment, IngestServer, DiscoveryResponder, presence
ticker) plus the app's loopback ``UiServer`` dispatching ``POST /api/<method>`` to the
same ``Api`` class the window uses, WITH the per-launch token (sent as ``X-TV-Token``).

Differences from the installed app, all for isolation: temp data dir, ingest bound to
127.0.0.1 on a non-default port, discovery on a non-default UDP port, installers point
at 127.0.0.1, fixed UTC+7 offset, reports into the data dir, no firewall check.

Prints ONE JSON line on stdout when ready, then runs until stdin reaches EOF or a
``stop`` line arrives (clean stop). The harness may also kill it (crash test).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--ingest-port", type=int, required=True)
    ap.add_argument("--discovery-port", type=int, required=True)
    ap.add_argument("--tz", type=int, default=420)
    a = ap.parse_args()

    import tokenvault
    from tokenvault.manager.api import Api, dispatch
    from tokenvault.manager.lifecycle import Manager, ManagerConfig
    from tokenvault.manager.paths import pricing_path, ui_dir
    from tokenvault.manager.webserver import UiServer

    data = Path(a.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(data / "manager-host.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = ManagerConfig(
        data_dir=data, template=Path(a.template), pricing=pricing_path(),
        ingest_host="127.0.0.1", ingest_port=a.ingest_port,
        discovery=True, discovery_port=a.discovery_port,
        tz_offset_minutes=a.tz, firewall_check=None,
        hosts_provider=lambda: ["127.0.0.1"], reports_dir=data / "reports",
    )
    m = Manager(cfg)
    m.start()
    api = Api(m)
    ui = UiServer(ui_dir(), api_call=lambda meth, args: dispatch(api, meth, args),
                  require_token=True)
    ui.start()
    print(json.dumps({
        "url": ui.url, "token": ui.token, "ingest_port": m.bound_port(),
        "fingerprint": m.cert.fingerprint_sha256, "manager_id": m.manager_id,
        "tokenvault": tokenvault.__file__, "ingest_error": m.ingest_error,
    }), flush=True)
    try:
        for line in sys.stdin:
            if line.strip() == "stop":
                break
    finally:
        ui.stop()
        m.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
