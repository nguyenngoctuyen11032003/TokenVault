"""Where the Manager keeps its files.

Data dir (``%APPDATA%\\TokenVault\\Manager``, overridable with ``--data-dir`` or
``TOKENVAULT_DATA_DIR``)::

    manager.db        Store (events, devices, rollups)
    enrollment.db     packages and device keys
    certs/            manager-cert.pem, manager-key.pem, manager-key.pem.pass
    settings.json     SettingsStore
    installers/       default output folder of "Create installer"
    manager.log       rotating log (1 MiB x 3)
    manager.lock      single-instance lock
    webview/          WebView2 profile (localStorage: palette, filters)

Install dir = folder of the frozen exe (or the repo root in development); the
adapter template is ``<install dir>/adapter-setup-template.exe`` unless
``--template`` / ``TOKENVAULT_TEMPLATE`` says otherwise.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

TEMPLATE_NAME = "adapter-setup-template.exe"
_PKG_DIR = Path(__file__).resolve().parent  # src/tokenvault/manager
_REPO_ROOT = _PKG_DIR.parents[2]


@dataclass(frozen=True)
class ManagerPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "manager.db"

    @property
    def enrollment(self) -> Path:
        return self.root / "enrollment.db"

    @property
    def certs(self) -> Path:
        return self.root / "certs"

    @property
    def settings(self) -> Path:
        return self.root / "settings.json"

    @property
    def installers(self) -> Path:
        return self.root / "installers"

    @property
    def log(self) -> Path:
        return self.root / "manager.log"

    @property
    def lock(self) -> Path:
        return self.root / "manager.lock"

    @property
    def webview(self) -> Path:
        return self.root / "webview"

    def create(self) -> ManagerPaths:
        for d in (self.root, self.certs, self.installers, self.webview):
            d.mkdir(parents=True, exist_ok=True)
        return self


def default_data_dir() -> Path:
    env = os.environ.get("TOKENVAULT_DATA_DIR")
    if env:
        return Path(env)
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "TokenVault" / "Manager"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _REPO_ROOT


def template_path(override: str | Path | None = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("TOKENVAULT_TEMPLATE")
    if env:
        return Path(env)
    return install_dir() / TEMPLATE_NAME


def _bundle_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else _REPO_ROOT


def pricing_path() -> Path:
    env = os.environ.get("TOKENVAULT_PRICING")
    if env:
        return Path(env)
    return _bundle_dir() / "data" / "pricing.json"


def ui_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "tokenvault" / "ui"
    return _PKG_DIR.parent / "ui"
