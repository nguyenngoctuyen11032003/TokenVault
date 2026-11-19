"""Per-model token pricing (USD per million tokens). Pure lookup, no guessing.

The pricing file (``data/pricing.json``) has the shape::

    {"_comment": "...",
     "models": {"<exact model id>": {"input": 3.0, "output": 15.0, "cache_read": 0.3,
                                      "cache_create_5m": 3.75, "cache_create_1h": 6.0}},
     "aliases": {"<other id>": "<exact model id>"}}

Lookups are exact (after one alias hop). A model that is not listed costs
``None`` (UNKNOWN), never zero and never a guess based on the model name.
Costs are computed on demand and never stored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

_PER = Decimal(1_000_000)
_FIELDS = ("input", "output", "cache_read", "cache_create_5m", "cache_create_1h")


@dataclass(frozen=True)
class ModelRates:
    """USD per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_create_5m: Decimal
    cache_create_1h: Decimal


class Pricing:
    def __init__(self, rates: dict[str, ModelRates], aliases: dict[str, str] | None = None):
        self._rates = dict(rates)
        self._aliases = dict(aliases or {})

    @classmethod
    def load(cls, path: str | Path) -> Pricing:
        doc: Any = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal)
        if not isinstance(doc, dict):
            raise ValueError("pricing file must be a JSON object")
        rates: dict[str, ModelRates] = {}
        for name, r in (doc.get("models") or {}).items():
            if not isinstance(r, dict):
                raise ValueError(f"pricing for {name!r} must be an object")
            try:
                vals = [Decimal(str(r[f])) for f in _FIELDS]
            except KeyError as e:
                raise ValueError(f"pricing for {name!r} lacks {e.args[0]!r}") from None
            rates[str(name)] = ModelRates(*vals)
        aliases = {str(k): str(v) for k, v in (doc.get("aliases") or {}).items()}
        return cls(rates, aliases)

    def rates(self, model: str | None) -> ModelRates | None:
        if not model:
            return None
        r = self._rates.get(model)
        if r is None and model in self._aliases:
            r = self._rates.get(self._aliases[model])
        return r

    def is_known(self, model: str | None) -> bool:
        return self.rates(model) is not None

    def cost(
        self,
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_create_5m_tokens: int,
        cache_create_1h_tokens: int,
    ) -> Decimal | None:
        """USD cost, or ``None`` when the model is not in the price list."""
        r = self.rates(model)
        if r is None:
            return None
        total = (
            r.input * input_tokens
            + r.output * output_tokens
            + r.cache_read * cache_read_tokens
            + r.cache_create_5m * cache_create_5m_tokens
            + r.cache_create_1h * cache_create_1h_tokens
        )
        return (total / _PER).normalize() if total else Decimal(0)
