"""``status`` data, the "What is sent?" text, state.json, and a small text window.

Nothing here ever reads or prints secrets or keys: only whether a device key
exists (``enrolled``).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from tokenvault.adapter import consent
from tokenvault.adapter.config import atomic_write, load_config
from tokenvault.adapter.identity import Identity, display_account
from tokenvault.adapter.outbox import Outbox
from tokenvault.adapter.paths import DataFiles
from tokenvault.common.protocol import event_to_dict

log = logging.getLogger("tokenvault.adapter.status")


# ------------------------------------------------------------------ state.json
def write_state(files: DataFiles, state: str, last_error: str | None = None) -> None:
    d = {"state": state, "updated": time.time(), "pid": os.getpid(), "last_error": last_error}
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


# ---------------------------------------------------------------- outbox views
def sample_event(outbox: Outbox) -> dict[str, Any] | None:
    """One queued (undelivered) event exactly as it will be sent, or None."""
    rows = outbox.pending(1)
    return event_to_dict(rows[0].event) if rows else None


def what_is_sent_text(outbox: Outbox, hosts: list[str] | tuple[str, ...]) -> str:
    text = consent.render_text(consent.WIRE_FIELDS, list(hosts))
    ev = sample_event(outbox)
    if ev is None:
        tail = ("No events are queued right now (everything was delivered, or Claude Code "
                "has not been used yet). Run 'status --json' later to see one.")
    else:
        tail = ("One queued event, exactly as it will be sent:\n\n"
                + json.dumps(ev, ensure_ascii=False, indent=2))
    return f"{text}\n\n{tail}\n"


# ---------------------------------------------------------------------- status
def gather(files: DataFiles, identity: Identity, running: bool) -> dict[str, Any]:
    cfg = load_config(files.config)
    stats = {"queued": 0, "delivered": 0, "waiting": 0}
    ev: dict[str, Any] | None = None
    if files.outbox.exists():  # never create the outbox just to report on it
        with Outbox(files.outbox) as ob:
            s = ob.stats()
            stats = {"queued": s.queued, "delivered": s.delivered, "waiting": s.waiting}
            ev = sample_event(ob)
    st = read_state(files) if running else None
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
        "enrolled": files.device_key.exists(),
        "agent_running": running,
        "connection": st.get("state") if st else None,
        "last_error": st.get("last_error") if st else None,
        "outbox": stats,
        "sample_event": ev,
    }


def format_text(d: dict[str, Any]) -> str:
    mgr = d["manager"]
    conn = d["connection"] or ("unknown" if d["agent_running"] else "agent not running")
    lines = [
        f"Device id:   {d['device_id']}",
        f"Account:     {d['account']}",
        f"Data dir:    {d['data_dir']}",
        f"Team:        {d['team'] if d['configured'] else '(not set up - run the installer)'}",
        f"Manager:     {', '.join(mgr['hosts']) + ':' + str(mgr['port']) if mgr else '-'}",
        f"Enrolled:    {'yes' if d['enrolled'] else 'not yet'}",
        f"Agent:       {'running' if d['agent_running'] else 'not running'}",
        f"Connection:  {conn}",
        f"Outbox:      {d['outbox']['queued']} queued, {d['outbox']['delivered']} delivered, "
        f"{d['outbox']['waiting']} waiting",
        "",
        "Run 'status --json' to see one queued event exactly as it is sent.",
    ]
    return "\n".join(lines)


def version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _v

        return _v("tokenvault")
    except (ImportError, PackageNotFoundError):
        return "dev"


def about_text(identity: Identity | None = None, files: DataFiles | None = None) -> str:
    lines = [
        f"TokenVault Adapter {version()}",
        "Reports Claude Code token usage (counts and metadata only) to your company's",
        "TokenVault Manager. Use 'What is sent?' to see exactly what leaves this PC.",
    ]
    if identity is not None:
        lines += ["", f"Device id: {identity.device_id}", f"Account:   {display_account(identity)}"]
    if files is not None:
        lines.append(f"Data dir:  {files.root}")
    lines += ["", "To remove: Windows Settings > Apps (Apps & Features) > TokenVault Adapter."]
    return "\n".join(lines)


# ---------------------------------------------------------------- text window
def show_text_window(title: str, text: str) -> None:  # pragma: no cover - interactive
    """Blocking read-only text window (call from its own thread)."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        log.error("tkinter is not available")
        return
    try:
        root = tk.Tk()
    except tk.TclError as e:
        log.error("cannot open a window: %s", e)
        return
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
    close = ttk.Button(root, text="Close", command=root.destroy)
    close.pack(pady=(0, 10))
    close.focus_set()
    root.bind("<Escape>", lambda e: root.destroy())
    root.bind("<Return>", lambda e: root.destroy())
    root.mainloop()


def open_folder(path: Path) -> None:  # pragma: no cover - interactive
    try:
        os.startfile(str(path))
    except (OSError, AttributeError) as e:
        log.warning("cannot open %s: %s", path, e)
