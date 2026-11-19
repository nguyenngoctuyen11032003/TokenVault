"""TokenVault Manager entry point.

CLI (``TokenVault-Manager.exe`` / ``python -m tokenvault.manager.app``)::

    (no args)                    run the Manager: tray + window + ingest server
      --minimized                start hidden in the tray (used by start-with-Windows)
      --data-dir DIR             data folder (default %APPDATA%\\TokenVault\\Manager)
      --template EXE             adapter template (env TOKENVAULT_TEMPLATE; default
                                 <install dir>\\adapter-setup-template.exe)
    --devserver [--port N]       UI in a normal browser against demo data (default port 8765)
    --selftest [--require-template]
                                 headless start/exercise/stop of every component; exit 0 = ok
    --selftest-window [--screenshot PNG] [--seconds S]
                                 open the real window on demo data, screenshot the desktop, exit
    firewall --ensure|--check [--port N] [--program EXE]
                                 add (elevated) / check the inbound firewall rules; prints JSON
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from tokenvault.manager import firewall
from tokenvault.manager.app_support import install_crash_hooks


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "firewall":
        return firewall.main(args[1:])
    ap = argparse.ArgumentParser(prog="TokenVault-Manager", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--devserver", action="store_true")
    mode.add_argument("--selftest", action="store_true")
    mode.add_argument("--selftest-window", action="store_true")
    ap.add_argument("--port", type=int, default=None, help="dev server port (default 8765)")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--template", default=None)
    ap.add_argument("--minimized", action="store_true")
    ap.add_argument("--screenshot", default=None)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--require-template", action="store_true",
                    help="--selftest: a missing real adapter template is a failure")
    a = ap.parse_args(args)
    if a.devserver:
        from tokenvault.manager import devserver

        return devserver.run(port=devserver.DEFAULT_PORT if a.port is None else a.port,
                             data_dir=Path(a.data_dir) if a.data_dir else None,
                             template=Path(a.template) if a.template else None)
    if a.selftest:
        from tokenvault.manager import selftest

        logging.basicConfig(level=logging.WARNING)
        return selftest.run(Path(a.template) if a.template else None,
                            require_template=a.require_template)
    from tokenvault.manager.gui import run_gui

    install_crash_hooks()
    if a.selftest_window:
        shot = Path(a.screenshot) if a.screenshot else Path(tempfile.gettempdir()) / "tv-window.png"
        return run_gui(a, demo=True, screenshot=shot, seconds=a.seconds)
    return run_gui(a)


if __name__ == "__main__":
    sys.exit(main())
