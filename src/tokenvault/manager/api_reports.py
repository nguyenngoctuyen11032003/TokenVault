"""PDF report export half of the Manager API (``report_options``, ``export_report``,
``open_path``, ``report_progress``). Functions take the running :class:`Manager` and a
:class:`ReportState`; :mod:`api` wraps them.

Safety: the UI never sends a file path to write. The destination is either the default
folder (``Documents\\TokenVault Reports``, or ``ManagerConfig.reports_dir``) with a generated,
sanitised file name, or a path picked by the user in the native save dialog (Python side).
``open_path`` only opens/reveals ``.pdf`` files inside the reports folder or exported by this
process. Reports never include session titles (they may be derived from prompts).
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tokenvault.manager.api_manage import teams
from tokenvault.manager.api_models import ApiError, require_text
from tokenvault.manager.lifecycle import Manager
from tokenvault.manager.reports import (
    PAGE_CAP,
    ReportEmptyScopeError,
    ReportError,
    ReportFontError,
    ReportSpec,
    ReportSpecError,
    build_report,
    unique_report_path,
)
from tokenvault.manager.reports.spec import MAX_EMPLOYEE_KEYS, safe_file_part

REPORT_DIR_NAME = "TokenVault Reports"
SPEC_KEYS = {"scope", "employee_keys", "team", "range", "start", "end",
             "per_employee_pages", "destination"}
DESTINATIONS = ("default", "dialog")
CSIDL_PERSONAL = 5


class ReportState:
    """Per-Api state: progress of the running export and the files this process wrote."""

    def __init__(self) -> None:
        self.lock = threading.Lock()  # held while an export runs
        self.guard = threading.Lock()  # protects the fields below
        self.running = False
        self.fraction = 0.0
        self.message = ""
        self.exported: set[Path] = set()


def documents_dir() -> Path:
    if sys.platform == "win32":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(520)
            rc = ctypes.windll.shell32.SHGetFolderPathW(
                None, CSIDL_PERSONAL, None, 0, buf)
            if rc == 0 and buf.value:
                return Path(buf.value)
        except Exception:  # noqa: BLE001 - fall back to the profile folder
            pass
    return Path(os.path.expanduser("~")) / "Documents"


def reports_dir(m: Manager) -> Path:
    return m.cfg.reports_dir if m.cfg.reports_dir is not None else documents_dir() / REPORT_DIR_NAME


def report_options(m: Manager) -> dict[str, Any]:
    return {
        "default_dir": str(reports_dir(m)),
        "can_pick_path": m.cfg.pick_save_path is not None,
        "can_open": m.cfg.open_file is not None,
        "can_reveal": m.cfg.reveal is not None,
        "teams": [t["name"] for t in teams(m)],
        "max_employees": MAX_EMPLOYEE_KEYS,
        "page_cap": PAGE_CAP,
        "ranges": ["today", "7d", "30d", "custom"],
    }


def _parse_spec(raw: Any) -> tuple[ReportSpec, str]:
    if not isinstance(raw, dict):
        raise ApiError("spec must be an object", "invalid_args")
    unknown = sorted(str(k) for k in raw if k not in SPEC_KEYS)
    if unknown:
        raise ApiError(f"unknown spec key(s): {', '.join(unknown)}", "invalid_args")
    dest = raw.get("destination", "default")
    if dest not in DESTINATIONS:
        raise ApiError("destination must be 'default' or 'dialog'", "invalid_args")
    kw = {k: v for k, v in raw.items() if k != "destination" and v is not None}
    if not isinstance(kw.get("employee_keys", []), list):
        raise ApiError("employee_keys must be a list", "invalid_args")
    # titles can come from prompts: never exported
    try:
        spec = ReportSpec(**kw, include_session_titles=False).validate()
    except ReportSpecError as e:
        raise ApiError(str(e), "invalid_report_spec") from None
    return spec, dest


def _target(m: Manager, spec: ReportSpec, dest: str, now: datetime) -> Path:
    folder = reports_dir(m)
    default = unique_report_path(folder, spec, now)
    if dest == "default":
        return default
    if m.cfg.pick_save_path is None:
        raise ApiError("no save dialog available here", "invalid_args")
    chosen = m.cfg.pick_save_path(default.name, folder)
    if chosen is None:
        raise ApiError("export cancelled", "cancelled")
    p = Path(chosen)
    return p.with_name(f"{safe_file_part(p.stem, 'BaoCao')}.pdf")


def export_report(m: Manager, st: ReportState, raw: Any) -> dict[str, Any]:
    spec, dest = _parse_spec(raw)
    if not st.lock.acquire(blocking=False):
        raise ApiError("another report is being created", "busy")
    try:
        now = datetime.now(timezone(timedelta(minutes=m.tz_offset_minutes)))
        out = _target(m, spec, dest, now)
        with st.guard:
            st.running, st.fraction, st.message = True, 0.0, ""

        def progress(frac: float, msg: str) -> None:
            with st.guard:
                st.fraction, st.message = frac, msg

        try:
            res = build_report(m.aggregates, spec, out, now=now,
                               manager_name="TokenVault Manager", progress=progress)
        except ReportEmptyScopeError as e:
            raise ApiError(str(e), "empty_scope") from None
        except ReportFontError as e:
            raise ApiError(str(e), "font_missing") from None
        except ReportSpecError as e:
            raise ApiError(str(e), "invalid_report_spec") from None
        except ReportError as e:
            raise ApiError(str(e), "report_failed") from None
        except OSError as e:
            raise ApiError(f"cannot write the report: {e.strerror or e}", "write_failed") from None
        path = res.path.resolve()
        with st.guard:
            st.exported.add(path)
        return {"path": str(path), "file_name": path.name, "folder": str(path.parent),
                "pages": res.pages, "bytes": res.bytes, "scope_label": res.scope_label,
                "range_label": res.range_label}
    finally:
        with st.guard:
            st.running = False
        st.lock.release()


def report_progress(st: ReportState) -> dict[str, Any]:
    with st.guard:
        return {"running": st.running, "fraction": st.fraction, "message": st.message}


def open_path(m: Manager, st: ReportState, path: Any, reveal: Any = False) -> dict[str, Any]:
    if not isinstance(reveal, bool):
        raise ApiError("reveal must be true or false", "invalid_args")
    try:
        target = Path(require_text(path, "path", 1024)).resolve(strict=True)
    except OSError:
        raise ApiError("file not found", "not_found") from None
    with st.guard:
        known = target in st.exported
    root = reports_dir(m)
    inside = root.exists() and target.is_relative_to(root.resolve())
    if target.suffix.lower() != ".pdf" or not target.is_file() or not (inside or known):
        raise ApiError("only report files created by TokenVault can be opened", "invalid_args")
    fn = m.cfg.reveal if reveal else m.cfg.open_file
    if fn is None:
        raise ApiError("this action is not available here", "invalid_args")
    fn(target)
    return {"ok": True}
