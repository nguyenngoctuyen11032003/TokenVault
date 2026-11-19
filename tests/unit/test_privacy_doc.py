"""docs/privacy.md must list exactly the UsageEvent wire fields and the never-collected list."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from tokenvault.common.models import UsageEvent

DOC = Path(__file__).resolve().parents[2] / "docs" / "privacy.md"


def _section(text: str, name: str) -> str:
    m = re.search(rf"<!-- PRIVACY-{name}:BEGIN -->(.*?)<!-- PRIVACY-{name}:END -->", text, re.S)
    assert m, f"privacy.md is missing the PRIVACY-{name} markers"
    return m.group(1)


def test_privacy_doc_lists_exactly_the_wire_fields() -> None:
    table = _section(DOC.read_text(encoding="utf-8"), "FIELDS")
    documented = re.findall(r"^\|\s*`([a-z0-9_]+)`\s*\|", table, re.M)
    assert len(documented) == len(set(documented)), "duplicate field rows"
    assert set(documented) == {f.name for f in fields(UsageEvent)}


def test_privacy_doc_has_never_collected_list() -> None:
    items = [ln for ln in _section(DOC.read_text(encoding="utf-8"), "NEVER").splitlines()
             if ln.startswith("- ")]
    assert len(items) >= 6
    text = " ".join(items).lower()
    for must in ("prompt", "replies", "file contents", "full directory paths", "passwords"):
        assert must in text
