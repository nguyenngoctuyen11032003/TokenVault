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
MAC_TEMPLATE_NAME = "adapter-mac.zip"
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


def _embedded_root() -> Path | None:
    """Running from the signed embeddable runtime installed by the Manager installer
    (``<app>/runtime/app/tokenvault/manager``): ``<app>``, the folder holding ``runtime/``.
    In an extracted Manager ZIP that folder is ``<zip root>/_he-thong``."""
    rt = _PKG_DIR.parents[2]
    if rt.name.lower() == "runtime" and (rt / "python.exe").is_file():
        return rt.parent
    return None


def install_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _embedded_root() or _REPO_ROOT


def template_path(override: str | Path | None = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("TOKENVAULT_TEMPLATE")
    if env:
        return Path(env)
    return install_dir() / TEMPLATE_NAME


def mac_template_path(data_dir: Path) -> Path:
    """macOS adapter template (a zip built on a Mac by ``build/build-mac.sh``).

    ``TOKENVAULT_MAC_TEMPLATE`` wins; else ``<data dir>/templates/adapter-mac.zip``; else
    ``<install dir>/adapter-mac.zip``. When none exists the first (data dir) is returned,
    so the "missing" message tells the owner where to put it."""
    env = os.environ.get("TOKENVAULT_MAC_TEMPLATE")
    if env:
        return Path(env)
    primary = Path(data_dir) / "templates" / MAC_TEMPLATE_NAME
    alt = install_dir() / MAC_TEMPLATE_NAME
    if not primary.is_file() and alt.is_file():
        return alt
    return primary


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


DEMO_MARKER = ".tokenvault-demo"


class DemoDirError(ValueError):
    """A demo/dev command was pointed at a real Manager data folder."""


def prepare_demo_dir(path: Path | str) -> Path:
    """Refuse the real data dir or a folder holding non-demo databases; mark it as demo."""
    p = Path(path).resolve()
    if p == default_data_dir().resolve():
        raise DemoDirError(f"{p} is the real Manager data folder; use another folder")
    if not (p / DEMO_MARKER).exists():
        for name in ("manager.db", "enrollment.db"):
            f = p / name
            if f.exists() and f.stat().st_size > 0:
                raise DemoDirError(
                    f"{p} contains a real {name} (no {DEMO_MARKER} marker); refusing to "
                    "write demo data into it"
                )
    p.mkdir(parents=True, exist_ok=True)
    (p / DEMO_MARKER).write_text("TokenVault demo data - safe to delete\n", encoding="utf-8")
    return p
