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
from itertools import product

from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.ports import ModelRates
from codemie.service.llm_service.llm_service import llm_service

logger = logging.getLogger(__name__)


_BEDROCK_CLAUDE_VERSION = re.compile(r"(anthropic\.claude-[a-z0-9.-]*?)-v1$", re.IGNORECASE)
_ROUTE_PREFIX = re.compile(r"^(bedrock/([a-z0-9-]+\.)?anthropic\.|vertex_ai/|anthropic/(anthropic\.)?)", re.IGNORECASE)
_ROUTING_PREFIX = re.compile(r"^(bedrock/)?(converse|invoke)/", re.IGNORECASE)
_PROVIDER_PREFIX = re.compile(r"^([a-z0-9-]+\.)?anthropic\.", re.IGNORECASE)
_VERSION_TAG = re.compile(r"(\d{8})-v\d+:\d+$")
_VERTEX_DATE = re.compile(r"@(\d{8})$")
_DATE_STAMP = re.compile(r"\d{8}")
_CATALOG_VARIANT_SUFFIX = re.compile(r"(-\d{8})?(-vertex)?$")


def normalize_model(model: str) -> str:
    """Canonical model id: same rules as its SQL twin `normalized_model_name` (first a trailing '-v1' with no colon
    after a Bedrock id `anthropic.claude-…`, so `bedrock/eu.anthropic.claude-opus-4-6-v1` becomes `claude-opus-4-6`;
    then LiteLLM route prefixes `bedrock/[<region>.]anthropic.`, `vertex_ai/`, `anthropic/anthropic.`, `anthropic/`, the
    Bedrock wrapper, routing and region/provider prefixes, a '-v1:0' tag that follows an 8-digit date stamp, and last
    Vertex's `@YYYYMMDD` becomes `-YYYYMMDD`)."""
    model = _BEDROCK_CLAUDE_VERSION.sub(r"\g<1>", model)
    model = _ROUTE_PREFIX.sub("", model)
    model = _ROUTING_PREFIX.sub("", model)
    model = _PROVIDER_PREFIX.sub("", model)
    model = _VERSION_TAG.sub(r"\g<1>", model)
    return _VERTEX_DATE.sub(r"-\g<1>", model)


def unpriced_display_names(models: Iterable[str]) -> list[str]:
    """Unpriced model names as the API reports them: normalised (a route becomes its plain name), deduplicated,
    sorted; an empty name stays empty."""
    return sorted({normalize_model(model) for model in models})


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

    A name with `/` is first matched exactly against the LiteLLM route map (each deployment's own price); a route
    outside it prices at its plain name below, as does a route whose price in the map has no usable rate. The catalog
    is ``llm_service.get_all_llm_model_info()``: the LiteLLM proxy's models when the proxy is on, the YAML config
    otherwise. Resolve the raw, unwrapped, then normalised name, preferring exact deployment names over shared base
    names. A shared base name retains the catalog's first-priced-entry order; missing tariffs for that same identity
    do not hide a priced duplicate. A matched catalog identity with no tariff never falls through to another catalog
    identity. Only the normalised-alias tiers compare
    different deployments, so all their tariffs must agree (including missing tariffs). The last tier, reached only
    when no alias matched and the name carries no 8-digit date, also matches catalog names with their trailing date
    or `-vertex` removed (the catalog side only, so a dated name never prices at another date). Conflicting exact
    deployment tariffs are also left unpriced. No fallback to another price list or to the default model: a price
    is exact or absent.
    """
    route_cost = _route_cost(model)
    if route_cost is not None:
        return route_cost
    catalog = llm_service.get_all_llm_model_info()
    unwrapped = _ROUTING_PREFIX.sub("", model)
    normalized = normalize_model(model)
    names = (("raw", model), ("unwrapped", unwrapped), ("normalized", normalized))
    for (tier, name), field in product(names, ("deployment_name", "base_name")):
        matches = [m for m in catalog if getattr(m, field) == name]
        if matches:
            return _identity_cost([m.cost for m in matches], model, tier, field)
    costs = _alias_costs(catalog, normalized)
    if not costs:
        return None
    if any(cost != costs[0] for cost in costs):
        logger.warning("cli_analytics: ambiguous catalog tariffs for model %s at normalized_alias", model)
        return None
    return costs[0]


def _route_cost(model: str) -> CostConfig | None:
    """The route's own price from the LiteLLM route map; None for a plain name, a route outside the map and a
    route whose price there has no usable rate."""
    if "/" not in model:
        return None
    route_cost = llm_service.get_route_cost(model)
    if route_cost is None:
        return None
    if _has_usable_rate(route_cost):
        return route_cost
    logger.debug("cli_analytics: route %s has no usable rate in the route map, priced by its plain name", model)
    return None


def _alias_costs(catalog: list, normalized: str) -> list[CostConfig | None]:
    """Tariffs of the catalog entries whose normalised name is `normalized`; when there is none and the name
    carries no 8-digit date, of the entries that match with their trailing date or `-vertex` removed."""
    costs = [
        m.cost
        for m in catalog
        if normalized in (normalize_model(m.base_name or ""), normalize_model(m.deployment_name or ""))
    ]
    if costs or _DATE_STAMP.search(normalized):
        return costs
    return [
        m.cost
        for m in catalog
        if normalized
        in (
            _CATALOG_VARIANT_SUFFIX.sub("", normalize_model(m.base_name or "")),
            _CATALOG_VARIANT_SUFFIX.sub("", normalize_model(m.deployment_name or "")),
        )
    ]


def _has_usable_rate(cost: CostConfig) -> bool:
    """Zero input and output with no positive cache-read rate is no price, not a free model."""
    return bool(cost.input or cost.output or (cost.cache_read_input_token_cost or 0) > 0)


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
    with no catalog entry at all, lands in ``unpriced``; those tokens contribute 0. An entry with zero input and
    output rates and no positive cache-read rate has no usable rate and counts as a missing entry.
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
            cost = lookup_cost_config(model) if model else None
            self._costs[model] = cost if cost is not None and _has_usable_rate(cost) else None
        return self._costs[model]

    def is_priced(self, model: str) -> bool:
        """True when the catalog has a usable price entry for the model, whatever the token counts; a zero-rate entry
        counts as missing. It records nothing."""
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
