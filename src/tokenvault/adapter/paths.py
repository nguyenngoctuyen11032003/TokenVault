"""Where the Adapter keeps its files and where Claude Code writes its logs.

Data dir (per Windows user, no admin): ``%LOCALAPPDATA%\\TokenVault\\Adapter``::

    config.json    manager_id, hosts, port, fingerprint, team (NO secrets)
    outbox.db      durable SQLite outbox (+ -wal / -shm)
    device.key     DPAPI-protected per-device key (after enrollment)
    enroll.secret  DPAPI-protected enrollment secret, only until the first Welcome
    adapter.log    rotating log (1 MB x 3); never contains secrets or keys
    state.json     connection state written by the running agent (for ``status``)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    e = os.environ if env is None else env
    base = e.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "TokenVault" / "Adapter"


def claude_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """``$CLAUDE_CONFIG_DIR`` if set, else ``~/.claude``."""
    e = os.environ if env is None else env
    override = e.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".claude"


def claude_projects_dir(env: Mapping[str, str] | None = None) -> Path:
    return claude_config_dir(env) / "projects"


@dataclass(frozen=True)
class DataFiles:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def outbox(self) -> Path:
        return self.root / "outbox.db"

    @property
    def device_key(self) -> Path:
        return self.root / "device.key"

    @property
    def enroll_secret(self) -> Path:
        return self.root / "enroll.secret"

    @property
    def log(self) -> Path:
        return self.root / "adapter.log"

    @property
    def state(self) -> Path:
        return self.root / "state.json"
