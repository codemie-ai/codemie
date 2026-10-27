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
from dataclasses import dataclass

from codemie.configs.llm_config import CostConfig
from codemie.service.llm_service.llm_service import llm_service

logger = logging.getLogger(__name__)


def normalize_model(model: str) -> str:
    """Strip AWS Bedrock prefixes to get a bare model id."""
    for prefix in ("us.anthropic.", "anthropic."):
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model


def lookup_cost_config(model: str) -> CostConfig | None:
    """Return the CostConfig for the model from the runtime catalog, or None if not found.

    The catalog is ``llm_service.get_all_llm_model_info()``: the LiteLLM proxy's models when the proxy is on, the
    YAML config otherwise. Matches by both ``base_name`` (after Bedrock-prefix stripping) and ``deployment_name`` so
    Bedrock variants stored in analytics (e.g. ``us.anthropic.claude-sonnet-4-6``) resolve without extra
    normalisation. No fallback to another price list or to the default model: a price is exact or absent.
    """
    normalized = normalize_model(model)
    for llm_model in llm_service.get_all_llm_model_info():
        if (
            llm_model.base_name == normalized or llm_model.deployment_name == normalized
        ) and llm_model.cost is not None:
            return llm_model.cost
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

    def price(self, model: str, counts: TokenCounts) -> float:
        # The split never prices more than the cache write it splits.
        cache_1h = min(counts.cache_1h or 0, counts.cache_creation)
        cache_5m = min(counts.cache_5m or 0, counts.cache_creation - cache_1h)
        unsplit = counts.cache_creation - cache_1h - cache_5m
        cost = self._cost(model)
        if cost is None:
            if counts.input or counts.output or counts.cache_read or counts.cache_creation or cache_5m or cache_1h:
                self._unpriced.add(model)
            return 0.0
        total = 0.0
        for tokens, rate in (
            (counts.input, cost.input),
            (counts.output, cost.output),
            (counts.cache_read, cost.cache_read_input_token_cost),
            (cache_5m + unsplit, cost.cache_creation_input_token_cost),
            (cache_1h, cost.cache_creation_input_token_cost_above_1hr),
        ):
            if tokens <= 0:
                continue
            if rate is None:
                self._unpriced.add(model)
                continue
            total += tokens * rate
        return total
