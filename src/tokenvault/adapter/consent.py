"""Consent text (generated from the wire fields, so it cannot drift) and dialog."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence

from tokenvault.common.models import UsageEvent

log = logging.getLogger("tokenvault.adapter.consent")

WIRE_FIELDS: tuple[str, ...] = tuple(f.name for f in dataclasses.fields(UsageEvent))

_NOTES = {
    "uuid": "id of the log line",
    "session_id": "id of the Claude Code session",
    "message_id": "id of the Claude response",
    "timestamp": "when the response was written",
    "model": "model name, e.g. claude-sonnet",
    "reasoning_effort": "effort setting (low / medium / high)",
    "is_sidechain": "whether a sub-agent produced it",
    "input_tokens": "token count",
    "output_tokens": "token count",
    "cache_read_tokens": "token count",
    "cache_create_5m_tokens": "token count",
    "cache_create_1h_tokens": "token count",
    "project": "LAST folder name of the working folder only (not the full path)",
    "session_title": "the short session title Claude Code generates (see note below)",
    "tool_names": "names of the tools used (e.g. Read, Bash), never their inputs or outputs",
    "claude_account_email": "your Claude account e-mail (from ~/.claude.json)",
    "account_uuid": "your Claude account id",
    "windows_user": "your Windows user name",
    "hostname": "this computer's name",
    "team": "team chosen by your manager for this installer",
    "device_id": "computer name + Windows user",
}


def render_text(fields: Sequence[str], hosts: Sequence[str] = ()) -> str:
    """Plain-text consent / "What is sent?" statement listing exactly ``fields``."""
    who = ", ".join(hosts) if hosts else "your company's Manager PC"
    lines = [
        "TokenVault Adapter - what is collected and sent",
        "",
        "For every response Claude Code writes on this computer, exactly these",
        "fields are sent:",
    ]
    for name in fields:
        note = _NOTES.get(name)
        lines.append(f"  - {name}: {note}" if note else f"  - {name}:")
    lines += [
        "",
        "NOT sent: your prompts, Claude's replies, tool inputs or outputs,",
        "file contents, commands, or any full folder path (only the last folder name).",
        "",
        "Note: session_title is the short title Claude Code itself writes for each",
        "session (the \"ai-title\" record). It is derived from your conversation",
        "text, and it IS sent.",
        "",
        f"Who sees it: only the TokenVault Manager at {who}, over the company",
        "network (encrypted). Nothing goes to the internet or any cloud.",
        "",
        "The Adapter runs in the background with a tray icon. 'What is sent?' in",
        "the tray menu shows a real queued event.",
        "",
        "To remove it: Windows Settings > Apps (Apps & Features) > TokenVault",
        "Adapter > Uninstall.",
    ]
    return "\n".join(lines)


def ask_consent(text: str) -> bool:  # pragma: no cover - interactive
    """Show the consent dialog; True only on an explicit Accept.

    Keyboard: Tab moves between buttons, Enter/Space presses the focused one,
    Alt+A accepts, Alt+D or Esc declines; closing the window declines.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        log.error("tkinter is not available; cannot show the consent screen")
        return False
    result = {"ok": False}
    try:
        root = tk.Tk()
    except tk.TclError as e:
        log.error("cannot open the consent window: %s", e)
        return False

    def done(ok: bool) -> None:
        result["ok"] = ok
        root.destroy()

    root.title("TokenVault Adapter - consent")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)
    box = tk.Text(frame, width=80, height=34, wrap="word", relief="flat")
    box.insert("1.0", text)
    box.configure(state="disabled", takefocus=0)
    box.pack(fill="both", expand=True)
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(8, 0))
    accept = ttk.Button(buttons, text="Accept", underline=0, command=lambda: done(True))
    decline = ttk.Button(buttons, text="Decline", underline=0, command=lambda: done(False))
    decline.pack(side="right")
    accept.pack(side="right", padx=(0, 8))
    for b in (accept, decline):
        b.bind("<Return>", lambda e: e.widget.invoke())
    root.bind("<Alt-a>", lambda e: done(True))
    root.bind("<Alt-d>", lambda e: done(False))
    root.bind("<Escape>", lambda e: done(False))
    root.protocol("WM_DELETE_WINDOW", lambda: done(False))
    accept.focus_set()
    root.attributes("-topmost", True)
    root.mainloop()
    return result["ok"]
