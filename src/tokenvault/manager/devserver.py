"""``--devserver``: the real UI in a normal browser against a seeded demo Store.

No pywebview, no ingest server, no discovery, no firewall. The UI's ``api.js``
falls back to ``POST /api/<method>`` (JSON argument array), which this server
dispatches to the same :class:`Api` class the window uses. Data lives in a fresh
temp folder (or ``--data-dir``; seeded only when empty) with the demo dataset of
:mod:`demo_data` and a fake adapter template, so every page, including "Create
installer", works. The demo's online devices are kept online by refreshing their
``last_seen`` every few seconds.

Prints one JSON line ``{"devserver": "<url>", "data_dir": "..."}`` to stdout.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from tokenvault.manager.api import Api, dispatch
from tokenvault.manager.demo_data import seed_now
from tokenvault.manager.lifecycle import Manager, ManagerConfig
from tokenvault.manager.paths import pricing_path, ui_dir
from tokenvault.manager.webserver import UiServer

log = logging.getLogger(__name__)
DEFAULT_PORT = 8765


def fake_template(path: Path) -> Path:
    if not path.exists():
        path.write_bytes(b"MZ" + b"TokenVault fake adapter template\0" * 64)
    return path


def build(data_dir: Path | None = None, template: Path | None = None,
          port: int = DEFAULT_PORT) -> tuple[Manager, UiServer]:
    root = Path(data_dir) if data_dir else Path(tempfile.mkdtemp(prefix="tokenvault-dev-"))
    root.mkdir(parents=True, exist_ok=True)
    cfg = ManagerConfig(
        data_dir=root,
        template=template or fake_template(root / "adapter-setup-template.exe"),
        pricing=pricing_path(),
        ingest=False,
        discovery=False,
        firewall_check=None,
        presence_interval=5.0,
    )
    m = Manager(cfg)
    m.start()
    with m.store.read_snapshot() as snap:
        empty = snap.fetchall("SELECT COUNT(*) FROM devices")[0][0] == 0
    if empty:
        info = seed_now(m.store, m.enrollment, m.tz_offset_minutes)
        online = set(info.online_ids)
    else:
        online = {d.device_id for d in m.store.list_devices()}
    m.presence_source = lambda: online
    m.touch_presence()
    api = Api(m)

    def call(method: str, args: list[Any]) -> Any:
        return dispatch(api, method, args)

    ui = UiServer(ui_dir(), api_call=call, require_token=False, port=port)
    try:
        ui.start()
    except Exception:
        m.stop()
        raise
    return m, ui


def run(port: int = DEFAULT_PORT, data_dir: Path | None = None,
        template: Path | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        m, ui = build(data_dir, template, port)
    except OSError as e:
        print(json.dumps({"error": f"cannot start the dev server: {e}"}), flush=True)
        return 2
    print(json.dumps({"devserver": f"{ui.url}/", "data_dir": str(m.paths.root)}), flush=True)
    print(f"TokenVault dev server: open {ui.url}/#/home  (Ctrl+C to stop)", file=sys.stderr)
    stop = threading.Event()
    try:
        while not stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        ui.stop()
        m.stop()
    return 0
