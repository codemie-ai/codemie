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
    """Return the CostConfig for the model, or None if not found.

    Resolution order (first hit wins):

    1. ``llm_service.get_all_llm_model_info()`` — the authoritative runtime source.
       When ``LLM_PROXY_ENABLED`` is True this list is populated from the running
       LiteLLM proxy (enterprise adapter, uses ``litellm.get_model_info`` live data);
       when False it falls back to the YAML config.  Matches by both ``base_name``
       (after Bedrock-prefix stripping) and ``deployment_name`` so Bedrock variants
       stored in ClickHouse (e.g. ``us.anthropic.claude-sonnet-4-6``) are resolved
       without extra normalisation.

    2. ``litellm.get_model_info(normalized)`` — direct litellm lookup for any model
       that is not yet present in the app config (e.g. newly-released Claude models).
       This is the same source the ``epic-local-analytics`` branch used exclusively.
       Silently skipped when litellm is not importable or does not know the model.

    No fallback to the platform default model — analytics pricing must be exact
    or 0.0 (never silently wrong).
    """
    normalized = normalize_model(model)

    # 1 — runtime model list (YAML or LiteLLM proxy)
    for llm_model in llm_service.get_all_llm_model_info():
        if (
            llm_model.base_name == normalized or llm_model.deployment_name == normalized
        ) and llm_model.cost is not None:
            return llm_model.cost

    # 2 — litellm library fallback (covers new models not yet in app config)
    try:
        import litellm as _litellm  # lazy import — not always available

        info = _litellm.get_model_info(normalized)
        inp = info.get("input_cost_per_token")
        out = info.get("output_cost_per_token")
        if inp is not None and out is not None:
            return CostConfig(
                input=inp,
                output=out,
                cache_read_input_token_cost=info.get("cache_read_input_token_cost"),
                cache_creation_input_token_cost=info.get("cache_creation_input_token_cost"),
            )
    except Exception:
        pass

    return None


def cache_read_cost(model: str, cache_read_tokens: int) -> float:
    """Return USD cost for cache_read_tokens at this model's cache read rate.

    CostConfig.cache_read_input_token_cost is USD per token; multiply directly.
    Returns 0.0 when the model is not in the LLM config or has no cache read rate.
    """
    cost_config = lookup_cost_config(model)
    if cost_config is None or cost_config.cache_read_input_token_cost is None:
        return 0.0
    return cost_config.cache_read_input_token_cost * cache_read_tokens
