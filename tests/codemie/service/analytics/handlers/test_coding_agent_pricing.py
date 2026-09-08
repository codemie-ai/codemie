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

from unittest.mock import MagicMock, patch

from codemie.configs.llm_config import CostConfig

_MODULE = "codemie.service.analytics.handlers.coding_agent_pricing"


def _make_llm_model(base_name: str, cache_read_rate: float | None) -> MagicMock:
    model = MagicMock()
    model.base_name = base_name
    if cache_read_rate is not None:
        model.cost = CostConfig(input=0.000003, output=0.000015, cache_read_input_token_cost=cache_read_rate)
    else:
        model.cost = None
    return model


def _patch_config(models: list) -> patch:
    return patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=models)


class TestNormalizeModel:
    def test_passthrough_bare_id(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import normalize_model

        assert normalize_model("claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_strips_anthropic_prefix(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import normalize_model

        assert normalize_model("anthropic.claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_strips_us_anthropic_prefix(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import normalize_model

        assert normalize_model("us.anthropic.claude-opus-4-8") == "claude-opus-4-8"


class TestLookupCostConfig:
    def test_returns_cost_config_for_known_model(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        model = _make_llm_model("claude-sonnet-4-6", cache_read_rate=0.00000033)
        with _patch_config([model]):
            result = lookup_cost_config("claude-sonnet-4-6")
        assert result is not None
        assert result.cache_read_input_token_cost == 0.00000033

    def test_normalizes_bedrock_prefix_before_lookup(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        model = _make_llm_model("claude-sonnet-4-6", cache_read_rate=0.00000033)
        with _patch_config([model]):
            result = lookup_cost_config("us.anthropic.claude-sonnet-4-6")
        assert result is not None

    def test_returns_none_for_unknown_model(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        with _patch_config([]):
            assert lookup_cost_config("definitely-not-a-real-model-xyz-9999") is None

    def test_returns_none_when_model_has_no_cost(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        model = _make_llm_model("no-cost-model-xyz-9999", cache_read_rate=None)
        with _patch_config([model]):
            assert lookup_cost_config("no-cost-model-xyz-9999") is None


class TestCacheReadCost:
    def test_known_model_correct_usd_no_million_division(self):
        # rate=0.00000033 USD/token (per-token, not per-1M); 1_000_000 tokens → 0.33 USD
        from codemie.service.analytics.handlers.coding_agent_pricing import cache_read_cost

        model = _make_llm_model("claude-sonnet-4-6", cache_read_rate=0.00000033)
        with _patch_config([model]):
            cost = cache_read_cost("claude-sonnet-4-6", 1_000_000)
        assert abs(cost - 0.33) < 1e-9

    def test_bedrock_prefixed_name_resolved(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import cache_read_cost

        model = _make_llm_model("claude-sonnet-4-6", cache_read_rate=0.00000033)
        with _patch_config([model]):
            cost = cache_read_cost("us.anthropic.claude-sonnet-4-6", 1_000_000)
        assert abs(cost - 0.33) < 1e-9

    def test_unknown_model_returns_zero(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import cache_read_cost

        with _patch_config([]):
            assert cache_read_cost("definitely-not-a-real-model-xyz-9999", 1_000_000) == 0.0

    def test_zero_tokens_returns_zero(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import cache_read_cost

        model = _make_llm_model("claude-sonnet-4-6", cache_read_rate=0.00000033)
        with _patch_config([model]):
            assert cache_read_cost("claude-sonnet-4-6", 0) == 0.0

    def test_model_without_cache_read_rate_returns_zero(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import cache_read_cost

        # Model in config but no cache_read_input_token_cost set
        model = MagicMock()
        model.base_name = "some-model"
        model.cost = CostConfig(input=0.001, output=0.002)  # cache_read_input_token_cost defaults to None
        with _patch_config([model]):
            assert cache_read_cost("some-model", 1_000_000) == 0.0
