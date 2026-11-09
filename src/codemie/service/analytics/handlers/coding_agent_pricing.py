# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.ports import ModelRates
from codemie.service.llm_service.llm_service import llm_service

logger = logging.getLogger(__name__)


_ROUTING_PREFIX = re.compile(r"^(bedrock/)?(converse|invoke)/", re.IGNORECASE)
_PROVIDER_PREFIX = re.compile(r"^([a-z0-9-]+\.)?anthropic\.", re.IGNORECASE)
_VERSION_TAG = re.compile(r"(\d{8})-v\d+:\d+$")


def normalize_model(model: str) -> str:
    """Canonical model id: same rules as the SQL reader's `_normalized_model_name` (Bedrock wrapper, routing and
    region/provider prefixes, and a '-v1:0' tag that follows an 8-digit date stamp)."""
    model = _ROUTING_PREFIX.sub("", model)
    model = _PROVIDER_PREFIX.sub("", model)
    return _VERSION_TAG.sub(r"\g<1>", model)


def _identity_cost(costs: list[CostConfig | None], model: str, tier: str, field: str) -> CostConfig | None:
    """Choose a tariff within one matched identity; shared bases keep first-priced catalog order."""
    priced = [cost for cost in costs if cost is not None]
    if not priced:
        return None
    if any(cost != priced[0] for cost in priced):
        logger.warning("cli_analytics: conflicting catalog tariffs for model %s at %s.%s", model, tier, field)
        if field == "deployment_name":
            return None
    return priced[0]


def lookup_cost_config(model: str) -> CostConfig | None:
    """Return the CostConfig for the model from the runtime catalog, or None if not priced.

    The catalog is ``llm_service.get_all_llm_model_info()``: the LiteLLM proxy's models when the proxy is on, the
    YAML config otherwise. Resolve the raw, unwrapped, then normalised name, preferring exact deployment names
    over shared base names. A shared base name retains the catalog's first-priced-entry order; missing tariffs
    for that same identity do not hide a priced duplicate. A matched identity with no tariff never falls through
    to another identity. Only the final normalised-alias tier compares different deployments, so all its tariffs
    must agree (including missing tariffs). Conflicting exact deployment tariffs are also left unpriced.
    No fallback to another price list or to the default model: a price is exact or absent.
    """
    catalog = llm_service.get_all_llm_model_info()
    unwrapped = _ROUTING_PREFIX.sub("", model)
    normalized = normalize_model(model)
    for tier, name in (("raw", model), ("unwrapped", unwrapped), ("normalized", normalized)):
        for field in ("deployment_name", "base_name"):
            matches = [m for m in catalog if getattr(m, field) == name]
            if not matches:
                continue
            return _identity_cost([m.cost for m in matches], model, tier, field)
    costs = [
        m.cost
        for m in catalog
        if normalized in (normalize_model(m.base_name or ""), normalize_model(m.deployment_name or ""))
    ]
    if costs:
        if any(cost != costs[0] for cost in costs):
            logger.warning("cli_analytics: ambiguous catalog tariffs for model %s at normalized_alias", model)
            return None
        return costs[0]
    return None


@dataclass(frozen=True)
class TokenCounts:
    """Token counts of one model group; ``cache_5m`` / ``cache_1h`` are None when the source has no split."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    cache_5m: int | None = None
    cache_1h: int | None = None


def kind_tokens(counts: TokenCounts) -> tuple[int, int, int, int, int]:
    """Tokens per priced kind: input, output, cache read, cache write (5m + unsplit), cache write 1h.
    The cache split never prices more than the cache write it splits."""
    cache_1h = min(counts.cache_1h or 0, counts.cache_creation)
    cache_5m = min(counts.cache_5m or 0, counts.cache_creation - cache_1h)
    unsplit = counts.cache_creation - cache_1h - cache_5m
    return counts.input, counts.output, counts.cache_read, cache_5m + unsplit, cache_1h


def _kind_rates(cost: CostConfig) -> tuple[float | None, ...]:
    """The kind-to-rate table, in `kind_tokens` order; the one place that maps a kind to its catalog rate."""
    return (
        cost.input,
        cost.output,
        cost.cache_read_input_token_cost,
        cost.cache_creation_input_token_cost,
        cost.cache_creation_input_token_cost_above_1hr,
    )


def model_rates_for(model: str) -> ModelRates:
    """Catalog rates of a raw model name (all None when it has no catalog entry)."""
    cost = lookup_cost_config(model) if model else None
    if cost is None:
        return ModelRates(model=model)
    return ModelRates(model, *_kind_rates(cost))


class CatalogPricer:
    """Prices token counts at the platform catalog; one instance per API request.

    Each distinct model name is resolved once. A model priced with a missing price for a token kind it used, or
    with no catalog entry at all, lands in ``unpriced``; those tokens contribute 0.
    """

    def __init__(self) -> None:
        self.catalog_source = llm_service.catalog_source()
        self._costs: dict[str, CostConfig | None] = {}
        self._unpriced: set[str] = set()

    @property
    def unpriced(self) -> list[str]:
        return sorted(self._unpriced)

    def _cost(self, model: str) -> CostConfig | None:
        if model not in self._costs:
            self._costs[model] = lookup_cost_config(model) if model else None
        return self._costs[model]

    def is_priced(self, model: str) -> bool:
        """True when the catalog has a price entry for the model, whatever the token counts; it records nothing."""
        return self._cost(model) is not None

    def price(self, model: str, counts: TokenCounts) -> float:
        return self.price_flagged(model, counts)[0]

    def price_flagged(self, model: str, counts: TokenCounts) -> tuple[float, bool]:
        """`price`, and whether some of these tokens had no price (the model is then in `unpriced`)."""
        tokens_by_kind = kind_tokens(counts)
        cost = self._cost(model)
        total, missing = 0.0, False
        if cost is None:
            # A kind is used when its tokens are positive, as for a catalogued model below and as in the SQL flags
            # (`bool_or(tokens > 0)`): a negative sum is not usage. `cache_creation` is the sum of the two write kinds.
            missing = any(tokens > 0 for tokens in tokens_by_kind)
        else:
            for tokens, rate in zip(tokens_by_kind, _kind_rates(cost), strict=True):
                if tokens <= 0:
                    continue
                if rate is None:
                    missing = True
                    continue
                total += tokens * rate
        if missing:
            self._unpriced.add(model)
        return total, missing

    def record_unpriced_from_flags(self, rows: Iterable[tuple[str, Sequence[bool]]]) -> None:
        """Record unpriced models from per-kind "used" flags (`kind_tokens` order, true = tokens > 0) instead of
        token counts: an uncatalogued model with any used kind, or a catalogued one missing the rate of a used kind."""
        for model, used in rows:
            cost = self._cost(model)
            if cost is None:
                if any(used):
                    self._unpriced.add(model)
            elif any(flag and rate is None for flag, rate in zip(used, _kind_rates(cost), strict=True)):
                self._unpriced.add(model)
