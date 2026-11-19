from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import fields

import pytest

from tokenvault.adapter import consent, strings
from tokenvault.common.models import UsageEvent

WIRE = [f.name for f in fields(UsageEvent)]
BULLET = re.compile(r"^  - ([a-z0-9_]+):", re.MULTILINE)


@pytest.fixture(autouse=True)
def _reset_lang(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("TOKENVAULT_LANG", raising=False)
    strings.set_lang(None)
    yield
    strings.set_lang(None)


def flat(text: str) -> str:
    return " ".join(text.split())


def test_consent_text_lists_every_wire_field_and_nothing_else() -> None:
    assert list(consent.WIRE_FIELDS) == WIRE
    for lang in strings.LANGS:
        strings.set_lang(lang)
        text = consent.render_text(consent.WIRE_FIELDS, ["10.0.0.5"])
        assert BULLET.findall(text) == WIRE  # every field, in order, once
        bullets = [ln for ln in text.splitlines() if ln.startswith("  - ")]
        assert len(bullets) == len(WIRE)  # nothing else is presented as "sent"


def test_notes_exist_for_every_wire_field_in_both_languages() -> None:
    assert set(consent.NOTES) == set(consent.WIRE_FIELDS)
    for note in consent.NOTES.values():
        assert set(note) == set(strings.LANGS) and all(note.values())


def test_every_string_key_has_both_languages() -> None:
    for key, val in strings.STRINGS.items():
        assert set(val) == set(strings.LANGS), key
        assert all(v.strip() for v in val.values()), key


def test_render_text_follows_the_given_fields() -> None:
    text = consent.render_text(["uuid", "project"], [])
    assert BULLET.findall(text) == ["uuid", "project"]


def test_consent_is_vietnamese_by_default() -> None:
    text = consent.render_text(consent.WIRE_FIELDS, ["10.0.0.5", "mgr-pc"])
    assert text.splitlines()[0] == "TokenVault Adapter – dữ liệu được thu thập và gửi đi"
    for name in consent.WIRE_FIELDS:
        assert f"  - {name}: {consent.NOTES[name]['vi']}" in text
    f = flat(text)
    assert "KHÔNG gửi" in f and "ai-title" in f and "CÓ được gửi đi" in f
    assert "10.0.0.5, mgr-pc" in f
    assert "Apps & Features" in f and "Gỡ cài đặt" in f


def test_english_fallback_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKENVAULT_LANG", "en")
    text = consent.render_text(consent.WIRE_FIELDS, ["10.0.0.5", "mgr-pc"])
    low = flat(text).lower()
    assert "ai-title" in low and "session_title" in text and "is sent" in low
    assert "10.0.0.5" in text and "mgr-pc" in text
    assert "not sent" in low
    for word in ("prompt", "repl", "file contents", "tool inputs", "full folder path"):
        assert word in low, word
    assert "apps & features" in low
