"""Process-level helpers: logging, native message box, crash hooks, Explorer."""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import TracebackType

log = logging.getLogger("tokenvault.manager")


def setup_logging(path: Path | None, verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")
    if path is not None:
        h = logging.handlers.RotatingFileHandler(path, maxBytes=1 << 20, backupCount=3,
                                                 encoding="utf-8")
        h.setFormatter(fmt)
        root.addHandler(h)
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        s = logging.StreamHandler()
        s.setFormatter(fmt)
        root.addHandler(s)


def message_box(text: str, error: bool = True) -> None:
    """Native message box (also printed to stderr; ``TOKENVAULT_NO_MSGBOX`` disables it)."""
    if sys.stderr is not None:
        print(text, file=sys.stderr)
    if sys.platform == "win32" and not os.environ.get("TOKENVAULT_NO_MSGBOX"):
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, "TokenVault Manager",
                                             0x10 if error else 0x40)


def reveal_explorer(path: Path) -> None:
    subprocess.Popen(["explorer", f"/select,{path}"])  # noqa: S603,S607 - fixed program


def open_file(path: Path) -> None:
    """Open a file with its default program (the report in the PDF viewer)."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - a vetted .pdf path


def pick_save_path(name: str, folder: Path) -> Path | None:
    """Native "save as" dialog (pywebview) for a PDF; None = cancelled."""
    import webview

    if not webview.windows:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    save_mode = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or webview.SAVE_DIALOG
    res = webview.windows[0].create_file_dialog(
        save_mode, directory=str(folder), save_filename=name,
        file_types=("PDF (*.pdf)",))
    if not res:
        return None
    return Path(res if isinstance(res, str) else res[0])


def install_crash_hooks() -> None:
    """Uncaught exceptions go to manager.log with a traceback (the windowed exe has no
    console); a fatal main-thread error also shows a short native message box."""

    def main_hook(tp: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        if issubclass(tp, KeyboardInterrupt):
            sys.__excepthook__(tp, exc, tb)
            return
        log.critical("uncaught exception", exc_info=(tp, exc, tb))
        message_box(f"TokenVault Manager stopped because of an unexpected error:\n\n"
                    f"{tp.__name__}: {exc}\n\nDetails are in manager.log.")

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "?"
        log.error("thread %s crashed", name,
                  exc_info=(args.exc_type, args.exc_value or args.exc_type(),
                            args.exc_traceback))

    sys.excepthook = main_hook
    threading.excepthook = thread_hook
