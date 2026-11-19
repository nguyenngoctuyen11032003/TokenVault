from __future__ import annotations

import re
from dataclasses import fields

from tokenvault.adapter import consent
from tokenvault.common.models import UsageEvent

WIRE = [f.name for f in fields(UsageEvent)]
BULLET = re.compile(r"^  - ([a-z0-9_]+):", re.MULTILINE)


def test_consent_text_lists_every_wire_field_and_nothing_else() -> None:
    assert list(consent.WIRE_FIELDS) == WIRE
    text = consent.render_text(consent.WIRE_FIELDS, ["10.0.0.5"])
    listed = BULLET.findall(text)
    assert listed == WIRE  # every field, in order, once
    # every bulleted line is a wire field (nothing else is presented as "sent")
    bullets = [ln for ln in text.splitlines() if ln.startswith("  - ")]
    assert len(bullets) == len(WIRE)


def test_render_text_follows_the_given_fields() -> None:
    text = consent.render_text(["uuid", "project"], [])
    assert BULLET.findall(text) == ["uuid", "project"]


def test_consent_discloses_title_hosts_not_sent_and_removal() -> None:
    text = consent.render_text(consent.WIRE_FIELDS, ["10.0.0.5", "mgr-pc"])
    low = text.lower()
    assert "ai-title" in low and "session_title" in text
    assert "is sent" in low
    assert "10.0.0.5" in text and "mgr-pc" in text
    assert "not sent" in low
    for word in ("prompt", "repl", "file contents", "tool inputs", "full folder path"):
        assert word in low, word
    assert "apps & features" in low
