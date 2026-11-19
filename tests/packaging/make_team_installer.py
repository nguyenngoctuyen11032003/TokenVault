"""Smoke helper: build a team installer through the Manager's packager.

Usage: python make_team_installer.py <template exe> <output dir> <team>
Starts a headless Manager in a throw-away data folder (never the real one), calls the
same ``Api.create_installer`` the UI uses, copies the result to <output dir> and prints
one JSON line: {"path", "team", "port", "fingerprint"}.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from tokenvault.common.config_payload import read_payload
from tokenvault.manager.api import Api
from tokenvault.manager.lifecycle import Manager, ManagerConfig
from tokenvault.manager.paths import pricing_path


def main(argv: list[str]) -> int:
    template, out_dir, team = Path(argv[0]), Path(argv[1]), argv[2]
    root = Path(tempfile.mkdtemp(prefix="tv-pack-"))
    m = Manager(ManagerConfig(
        data_dir=root / "data", template=template, pricing=pricing_path(),
        ingest_host="127.0.0.1", ingest_port=0, discovery_port=0, firewall_check=None,
        hosts_provider=lambda: ["127.0.0.1"],
    ))
    try:
        m.start()
        res = Api(m).create_installer(team, None)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / Path(res["path"]).name
        shutil.copyfile(res["path"], dest)
        cfg = read_payload(dest)
        assert cfg is not None, "payload missing in the built installer"
        print(json.dumps({"path": str(dest), "team": cfg.team, "port": cfg.port,
                          "fingerprint": cfg.fingerprint}))
        return 0
    finally:
        m.stop()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
