"""``status`` data, the "What is sent?" / About texts, state.json and a text window.

Readers here open the outbox READ-ONLY (``mode=ro``), so ``status`` and the
``show`` child process never write to the agent's database. Nothing here ever
reads out or prints secrets or keys: only whether a usable key exists.
The text window (tkinter) is shown only from the short-lived ``show`` child
process, never inside the agent.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tokenvault.adapter import consent
from tokenvault.adapter.config import atomic_write, load_config, secret_state
from tokenvault.adapter.identity import Identity, display_account
from tokenvault.adapter.paths import DataFiles
from tokenvault.adapter.strings import t
from tokenvault.common.protocol import ProtocolError, event_from_dict, event_to_dict

log = logging.getLogger("tokenvault.adapter.status")
MAX_REASON = 200


# ------------------------------------------------------------------ state.json
def sanitize(reason: str | None) -> str | None:
    if reason is None:
        return None
    flat = " ".join(str(reason).split())
    return flat[:MAX_REASON]


def write_state(files: DataFiles, state: str, last_error: str | None = None,
                **extra: Any) -> None:
    d = {"state": state, "updated": time.time(), "pid": os.getpid(),
         "last_error": sanitize(last_error), **extra}
    try:
        atomic_write(files.state, json.dumps(d, ensure_ascii=False).encode("utf-8"))
    except OSError as e:
        log.debug("cannot write state.json: %s", e)


def read_state(files: DataFiles) -> dict[str, Any] | None:
    try:
        d = json.loads(files.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


# ------------------------------------------------------- read-only outbox view
@dataclass(frozen=True)
class OutboxView:
    queued: int = 0
    delivered: int = 0
    waiting: int = 0
    sample: dict[str, Any] | None = None


def read_outbox(path: Path) -> OutboxView:
    """Stats + one undelivered event, read-only. Missing/unreadable -> empty view."""
    if not path.exists():
        return OutboxView()
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=2)
    except sqlite3.Error as e:
        log.warning("cannot open outbox read-only: %s", e)
        return OutboxView()
    try:
        total, delivered = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(delivered), 0) FROM events").fetchone()
        sample: dict[str, Any] | None = None
        for (payload,) in db.execute(
            "SELECT payload FROM events WHERE delivered=0 ORDER BY enqueued_at, rowid LIMIT 5"
        ):
            try:
                sample = event_to_dict(event_from_dict(json.loads(payload)))
                break
            except (ValueError, ProtocolError):
                continue
        return OutboxView(int(total), int(delivered), int(total) - int(delivered), sample)
    except sqlite3.Error as e:
        log.warning("cannot read outbox: %s", e)
        return OutboxView()
    finally:
        db.close()


def what_is_sent_text(sample: dict[str, Any] | None, hosts: list[str] | tuple[str, ...]) -> str:
    text = consent.render_text(consent.WIRE_FIELDS, list(hosts))
    if sample is None:
        tail = t("wis.none")
    else:
        tail = t("wis.one") + "\n\n" + json.dumps(sample, ensure_ascii=False, indent=2)
    return f"{text}\n\n{tail}\n"


# ---------------------------------------------------------------------- status
def gather(files: DataFiles, identity: Identity, running: bool) -> dict[str, Any]:
    cfg = load_config(files.config)
    ob = read_outbox(files.outbox)
    st = read_state(files) if running else None
    key = secret_state(files.device_key)
    return {
        "device_id": identity.device_id,
        "account": display_account(identity),
        "windows_user": identity.windows_user,
        "hostname": identity.hostname,
        "data_dir": str(files.root),
        "configured": cfg is not None,
        "team": cfg.team if cfg else None,
        "manager": (
            {"hosts": list(cfg.hosts), "port": cfg.port, "fingerprint": cfg.fingerprint}
            if cfg else None
        ),
        "enrolled": key == "ok",
        "device_key": key,  # "ok" | "missing" | "unreadable" (never the key itself)
        "agent_running": running,
        "connection": st.get("state") if st else None,
        "last_error": st.get("last_error") if st else None,
        "outbox": {"queued": ob.queued, "delivered": ob.delivered, "waiting": ob.waiting},
        "sample_event": ob.sample,
    }


def format_text(d: dict[str, Any]) -> str:
    mgr = d["manager"]
    if d["connection"]:
        key = f"state.{d['connection']}"
        conn = t(key) if key in _STATE_KEYS else str(d["connection"])
    else:
        conn = t("status.unknown") if d["agent_running"] else t("status.not_running")
    enrolled = t({"ok": "status.key_ok", "missing": "status.key_missing"}.get(
        d["device_key"], "status.key_unreadable"))
    rows = [
        ("status.device", d["device_id"]),
        ("status.account", d["account"]),
        ("status.data_dir", d["data_dir"]),
        ("status.team", d["team"] if d["configured"] else t("status.not_setup")),
        ("status.manager", f"{', '.join(mgr['hosts'])}:{mgr['port']}" if mgr else "-"),
        ("status.enrolled", enrolled),
        ("status.agent", t("status.running") if d["agent_running"] else t("status.not_running")),
        ("status.connection", conn),
        ("status.outbox", t("status.outbox_line", **d["outbox"])),
    ]
    labels = [t(k) for k, _ in rows]
    width = max(len(s) for s in labels) + 1
    lines = [f"{lbl:<{width}} {val}" for lbl, (_, val) in zip(labels, rows, strict=True)]
    extra = [t("status.reset_needed"), ""] if _needs_reset(d) else []
    return "\n".join([*lines, "", *extra, t("status.hint")])


def _needs_reset(d: dict[str, Any]) -> bool:
    """The Manager refused to re-enroll this device (it already holds a key there)."""
    return d.get("connection") == "rejected" and "already enrolled" in str(d.get("last_error"))


_STATE_KEYS = {f"state.{s}" for s in (
    "connected", "offline", "rejected", "connecting", "not_configured", "stopped", "crashed",
    "no_indicator")}


def version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _v

        return _v("tokenvault")
    except (ImportError, PackageNotFoundError):
        return "dev"


def about_text(identity: Identity | None = None, files: DataFiles | None = None) -> str:
    lines = [t("about.title", version=version()), "", t("about.desc")]
    if identity is not None:
        lines += ["", f"{t('status.device')} {identity.device_id}",
                  f"{t('status.account')} {display_account(identity)}"]
    if files is not None:
        lines.append(f"{t('status.data_dir')} {files.root}")
    lines += ["", t("consent.remove")]
    return "\n".join(lines)


# ---------------------------------------------------------------- text window
def show_text_window(title: str, text: str) -> int:  # pragma: no cover - interactive
    """Blocking read-only text window. Only ever called in the ``show`` child process."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        log.error("tkinter is not available")
        return 4
    try:
        root = tk.Tk()
    except tk.TclError as e:
        log.error("cannot open a window: %s", e)
        return 4
    root.title(title)
    frame = ttk.Frame(root, padding=10)
    frame.pack(fill="both", expand=True)
    box = tk.Text(frame, width=90, height=36, wrap="word")
    scroll = ttk.Scrollbar(frame, command=box.yview)
    box.configure(yscrollcommand=scroll.set)
    box.insert("1.0", text)
    box.configure(state="disabled")
    scroll.pack(side="right", fill="y")
    box.pack(side="left", fill="both", expand=True)
    close = ttk.Button(root, text=t("common.close"), command=root.destroy)
    close.pack(pady=(0, 10))
    close.focus_set()
    root.bind("<Escape>", lambda e: root.destroy())
    root.bind("<Return>", lambda e: root.destroy())
    root.mainloop()
    return 0


def open_folder(path: Path) -> None:  # pragma: no cover - interactive
    try:
        os.startfile(str(path))
    except (OSError, AttributeError) as e:
        log.warning("cannot open %s: %s", path, e)
