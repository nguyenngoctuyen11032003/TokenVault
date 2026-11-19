from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from tokenvault.common.pricing import Pricing

REPO_PRICING = Path(__file__).resolve().parents[2] / "data" / "pricing.json"


def _write(tmp_path: Path, doc: dict[str, object]) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


@pytest.fixture
def pricing(tmp_path: Path) -> Pricing:
    return Pricing.load(
        _write(
            tmp_path,
            {
                "_comment": "test",
                "models": {
                    "m-1": {
                        "input": 3.0,
                        "output": 15.0,
                        "cache_read": 0.3,
                        "cache_create_5m": 3.75,
                        "cache_create_1h": 6.0,
                    }
                },
                "aliases": {"m-1-20250101": "m-1"},
            },
        )
    )


def test_unknown_model_cost_is_none_not_zero(pricing: Pricing) -> None:
    assert pricing.cost("nope", 1000, 1000, 0, 0, 0) is None
    assert pricing.cost("nope", 0, 0, 0, 0, 0) is None
    # never guess from a name that merely looks similar
    assert pricing.cost("m-1-preview", 1_000_000, 0, 0, 0, 0) is None
    assert pricing.cost("", 1, 0, 0, 0, 0) is None
    assert pricing.is_known("m-1") and not pricing.is_known("nope")


def test_cache_5m_and_1h_priced_separately(pricing: Pricing) -> None:
    m = 1_000_000
    assert pricing.cost("m-1", 0, 0, 0, m, 0) == Decimal("3.75")
    assert pricing.cost("m-1", 0, 0, 0, 0, m) == Decimal("6")
    assert pricing.cost("m-1", 0, 0, 0, m, m) == Decimal("9.75")


def test_cost_sums_all_components_exactly(pricing: Pricing) -> None:
    c = pricing.cost("m-1", 1_000_000, 2_000_000, 10_000_000, 0, 0)
    assert c == Decimal("3") + Decimal("30") + Decimal("3")
    assert isinstance(c, Decimal)
    assert pricing.cost("m-1", 1, 0, 0, 0, 0) == Decimal("0.000003")


def test_alias_maps_to_exact_model(pricing: Pricing) -> None:
    assert pricing.cost("m-1-20250101", 1_000_000, 0, 0, 0, 0) == Decimal("3")


def test_repo_pricing_file_loads_with_reference_rates() -> None:
    p = Pricing.load(REPO_PRICING)
    doc = json.loads(REPO_PRICING.read_text(encoding="utf-8"))
    assert "estimate" in doc["_comment"].lower()
    m = 1_000_000
    assert p.cost("claude-sonnet-4-6", m, 0, 0, 0, 0) == Decimal("3")
    assert p.cost("claude-sonnet-4-6", 0, m, 0, 0, 0) == Decimal("15")
    assert p.cost("claude-opus-4-7", 0, 0, 0, m, 0) == Decimal("18.75")
    assert p.cost("claude-haiku-4-5", 0, 0, 0, 0, m) == Decimal("2")
    assert p.cost("gpt-4o", m, 0, 0, 0, 0) is None


def test_alias_to_unknown_model_is_rejected(tmp_path: Path) -> None:
    doc = {"models": {}, "aliases": {"claude-x-2025": "claude-x"}}
    with pytest.raises(ValueError, match="claude-x-2025"):
        Pricing.load(_write(tmp_path, doc))
