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

import pytest

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

        library_info = {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000002}
        with _patch_config([]), patch("litellm.get_model_info", return_value=library_info):
            assert lookup_cost_config("definitely-not-a-real-model-xyz-9999") is None

    def test_returns_none_when_model_has_no_cost(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        model = _make_llm_model("no-cost-model-xyz-9999", cache_read_rate=None)
        with _patch_config([model]):
            assert lookup_cost_config("no-cost-model-xyz-9999") is None


def _priced_model(name: str, inp, out, read, write_5m, write_1h) -> MagicMock:
    model = MagicMock()
    model.base_name = name
    model.cost = CostConfig(
        input=inp,
        output=out,
        cache_read_input_token_cost=read,
        cache_creation_input_token_cost=write_5m,
        cache_creation_input_token_cost_above_1hr=write_1h,
    )
    return model


_OPUS = _priced_model("claude-opus-5-5", 4.4e-6, 22e-6, 0.22e-6, 5.5e-6, 8.8e-6)
_SONNET = _priced_model("claude-sonnet-4-6", 3.3e-6, 16.5e-6, 0.33e-6, 4.125e-6, 6.6e-6)
_HAIKU = _priced_model("claude-haiku-4-5", 1.0e-6, 5.0e-6, 0.10e-6, 1.25e-6, 2.0e-6)
_NO_1H = _priced_model("no-1h-model", 1.0e-6, 5.0e-6, 0.10e-6, 1.25e-6, None)


class TestCatalogPricer:
    @pytest.mark.parametrize(
        ("model", "counts", "expected"),
        [
            (
                "claude-opus-5-5",
                {
                    "input": 374,
                    "output": 66_483,
                    "cache_read": 40_356_538,
                    "cache_creation": 1_888_908,
                    "cache_1h": 1_888_908,
                },
                26.96510036,
            ),
            ("claude-opus-5-5", {"cache_read": 40_356_538}, 8.87843836),
            (
                "claude-sonnet-4-6",
                {"input": 6, "output": 708, "cache_read": 107_215, "cache_creation": 41_644, "cache_1h": 41_644},
                0.32193315,
            ),
            (
                "claude-haiku-4-5",
                {"input": 10, "output": 100, "cache_read": 68_589, "cache_creation": 1_000, "cache_5m": 1_000},
                0.0086189,
            ),
            ("claude-haiku-4-5", {"cache_creation": 1_000}, 0.00125),
            ("claude-haiku-4-5", {"cache_creation": 1_000, "cache_1h": 400}, 0.00155),
            ("claude-haiku-4-5", {"cache_creation": 1_000, "cache_5m": 1_000, "cache_1h": 1_000}, 0.002),
            ("claude-haiku-4-5", {"cache_creation": 0, "cache_5m": 300, "cache_1h": 700}, 0.0),
        ],
    )
    def test_prices_at_catalog(self, model, counts, expected):
        from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

        with _patch_config([_OPUS, _SONNET, _HAIKU]):
            pricer = CatalogPricer()
            assert pricer.price(model, TokenCounts(**counts)) == pytest.approx(expected, abs=1e-12)
            assert pricer.unpriced == []

    def test_missing_1h_price_marks_unpriced_only_when_1h_tokens_used(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

        with _patch_config([_NO_1H]):
            pricer = CatalogPricer()
            assert pricer.price("no-1h-model", TokenCounts(input=10, cache_creation=1_000)) == pytest.approx(0.00126)
            assert pricer.unpriced == []
            assert pricer.price("no-1h-model", TokenCounts(cache_creation=100, cache_1h=100)) == 0.0
            assert pricer.unpriced == ["no-1h-model"]

    def test_empty_and_unknown_models_are_unpriced(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

        with _patch_config([_OPUS]):
            pricer = CatalogPricer()
            assert pricer.price("", TokenCounts(output=5)) == 0.0
            assert pricer.unpriced == [""]
            assert pricer.price("unknown-model", TokenCounts(input=5)) == 0.0
            assert pricer.price("unknown-model", TokenCounts(input=5)) == 0.0
            assert pricer.unpriced == ["", "unknown-model"]

    def test_unknown_model_without_tokens_is_not_unpriced(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

        with _patch_config([]):
            pricer = CatalogPricer()
            assert pricer.price("unknown-model", TokenCounts()) == 0.0
            assert pricer.unpriced == []

    def test_resolves_each_distinct_model_once(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

        with _patch_config([_OPUS]):
            pricer = CatalogPricer()
            assert pricer.price("claude-opus-5-5", TokenCounts(input=1_000)) == pytest.approx(0.0044)
            assert pricer.price("claude-sonnet-4-6", TokenCounts(input=1_000)) == 0.0
        with _patch_config([_SONNET]):
            assert pricer.price("claude-opus-5-5", TokenCounts(input=1_000)) == pytest.approx(0.0044)
            assert pricer.price("claude-sonnet-4-6", TokenCounts(input=1_000)) == 0.0
        assert pricer.unpriced == ["claude-sonnet-4-6"]

    def test_exposes_catalog_source(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer

        with patch(f"{_MODULE}.llm_service.catalog_source", return_value="litellm"):
            assert CatalogPricer().catalog_source == "litellm"
