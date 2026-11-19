"""Consent text (generated from the wire fields, so it cannot drift) and dialog.

All wording lives in :mod:`tokenvault.adapter.strings` (Vietnamese by default).
"""

from __future__ import annotations

import dataclasses
import logging
import sys
import textwrap
from collections.abc import Sequence

from tokenvault.adapter import strings
from tokenvault.adapter.strings import t
from tokenvault.common.models import UsageEvent

log = logging.getLogger("tokenvault.adapter.consent")

WIRE_FIELDS: tuple[str, ...] = tuple(f.name for f in dataclasses.fields(UsageEvent))
NOTES = strings.FIELD_NOTES  # one note per wire field (tested)
_WIDTH = 78


def _para(text: str) -> list[str]:
    return textwrap.wrap(text, _WIDTH, break_long_words=False, break_on_hyphens=False)


def render_text(fields: Sequence[str], hosts: Sequence[str] = ()) -> str:
    """Plain-text consent / "What is sent?" statement listing exactly ``fields``."""
    who = ", ".join(hosts) if hosts else t("consent.who_default")
    lines = [t("consent.title"), "", *_para(t("consent.intro"))]
    for name in fields:
        note = strings.field_note(name)
        lines.append(f"  - {name}: {note}" if note else f"  - {name}:")
    for key in ("consent.not_sent", "consent.title_note"):
        lines += ["", *_para(t(key))]
    lines += ["", *_para(t("consent.who", who=who))]
    for key in ("consent.tray_note", "consent.remove"):
        lines += ["", *_para(t(key))]
    return "\n".join(lines)


def ask_consent(text: str) -> bool | None:  # pragma: no cover - interactive
    """Show the consent dialog.

    True = explicit Accept, False = Decline (button, Esc, Alt+D or closing the
    window), None = the dialog could not be shown at all (no tkinter / no
    display) - the caller must NOT treat that as a decline.
    Keyboard: Tab moves between buttons, Enter/Space presses the focused one.
    """
    if sys.platform == "darwin":  # osascript dialog (tkinter is unreliable in a bundle)
        from tokenvault.adapter.platform import mac

        return mac.ask_consent(text, t("consent.window_title"), t("consent.accept"),
                               t("consent.decline"))
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        log.error("tkinter is not available; cannot show the consent screen")
        return None
    result = {"ok": False}
    try:
        root = tk.Tk()
    except tk.TclError as e:
        log.error("cannot open the consent window: %s", e)
        return None

    def done(ok: bool) -> None:
        result["ok"] = ok
        root.destroy()

    root.title(t("consent.window_title"))
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)
    box = tk.Text(frame, width=82, height=36, wrap="word", relief="flat")
    box.insert("1.0", text)
    box.configure(state="disabled", takefocus=0)
    box.pack(fill="both", expand=True)
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(8, 0))
    accept = ttk.Button(buttons, text=t("consent.accept"), command=lambda: done(True))
    decline = ttk.Button(buttons, text=t("consent.decline"), command=lambda: done(False))
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
