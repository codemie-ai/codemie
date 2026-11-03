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
    model.deployment_name = None
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


class TestNormalizeBedrockForms:
    @pytest.mark.parametrize(
        "raw",
        [
            "anthropic.claude-haiku-4-5-20251001-v1:0",
            "converse/global.anthropic.claude-haiku-4-5-20251001-v1:0",
            "converse/us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "converse/eu.anthropic.claude-haiku-4-5-20251001-v1:0",
            "bedrock/invoke/us-gov-west-1.anthropic.claude-haiku-4-5-20251001-v1:0",
        ],
    )
    def test_ticket_ids_reduce_to_catalog_id(self, raw):
        from codemie.service.analytics.handlers.coding_agent_pricing import normalize_model

        assert normalize_model(raw) == "claude-haiku-4-5-20251001"

    def test_canonical_and_dated_ids_unchanged(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import normalize_model

        for m in ("claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-4-5-haiku", ""):
            assert normalize_model(m) == m


def _alias_model(base: str, deployment: str | None, inp: float, out: float) -> MagicMock:
    model = MagicMock()
    model.base_name = base
    model.deployment_name = deployment
    model.cost = CostConfig(input=inp, output=out, cache_read_input_token_cost=inp / 10)
    return model


class TestBedrockAliasPricing:
    _BEDROCK_IDS = (
        "anthropic.claude-haiku-4-5-20251001-v1:0",
        "converse/global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "converse/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "converse/eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    )

    @pytest.mark.parametrize("raw", _BEDROCK_IDS)
    def test_ticket_ids_price_at_catalog_rates(self, raw):
        from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

        with _patch_config([_alias_model("claude-haiku-4-5-20251001", None, 1e-6, 5e-6)]):
            pricer = CatalogPricer()
            assert pricer.price(raw, TokenCounts(input=796, output=23)) == pytest.approx(0.000911, abs=1e-12)
            assert pricer.unpriced == []

    def test_raw_deployment_name_matches_exactly(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        deployment = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        haiku = _alias_model("claude-4-5-haiku", deployment, 1e-6, 5e-6)
        with _patch_config([haiku]):
            assert lookup_cost_config(deployment) is haiku.cost
            assert lookup_cost_config("converse/us.anthropic.claude-haiku-4-5-20251001-v1:0") is haiku.cost

    def test_exact_match_wins_over_alias(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        exact = _alias_model("claude-haiku-4-5-20251001", None, 1e-6, 5e-6)
        other = _alias_model("other", "anthropic.claude-haiku-4-5-20251001-v1:0", 9e-6, 9e-6)
        with _patch_config([other, exact]):
            assert lookup_cost_config("claude-haiku-4-5-20251001") is exact.cost

    def test_date_and_version_distinctions_are_kept(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        with _patch_config([_alias_model("claude-haiku-4-5-20251001", None, 1e-6, 5e-6)]):
            assert lookup_cost_config("anthropic.claude-haiku-4-5-20260101-v1:0") is None
            assert lookup_cost_config("anthropic.claude-haiku-4-5-v1:0") is None

    @pytest.mark.parametrize(
        "raw",
        [
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "converse/us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "bedrock/converse/us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "invoke/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        ],
    )
    def test_routing_wrapper_keeps_the_exact_deployment_price(self, raw):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        canonical = _alias_model("claude-haiku-4-5-20251001", None, 1e-6, 5e-6)
        us = _alias_model("claude-4-5-haiku", "us.anthropic.claude-haiku-4-5-20251001-v1:0", 1.1e-6, 5.5e-6)
        with _patch_config([canonical, us]):
            assert lookup_cost_config(raw) is us.cost

    @pytest.mark.parametrize(
        "raw", ["us.anthropic.claude-haiku-4-5-20251001-v1:0", "converse/us.anthropic.claude-haiku-4-5-20251001-v1:0"]
    )
    def test_deployment_without_tariff_does_not_fall_back_to_another_entry(self, raw):
        from codemie.service.analytics.handlers.coding_agent_pricing import (
            CatalogPricer,
            TokenCounts,
            lookup_cost_config,
        )

        canonical = _alias_model("claude-haiku-4-5-20251001", None, 1e-6, 5e-6)
        us = _alias_model("claude-4-5-haiku", "us.anthropic.claude-haiku-4-5-20251001-v1:0", 1.1e-6, 5.5e-6)
        us.cost = None
        with _patch_config([canonical, us]):
            assert lookup_cost_config(raw) is None
            pricer = CatalogPricer()
            assert pricer.price(raw, TokenCounts(input=10)) == 0.0
            assert pricer.unpriced == [raw]

    def test_deployment_with_partial_rates_is_unpriced_for_the_missing_kind_only(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

        raw = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        canonical = _alias_model("claude-haiku-4-5-20251001", None, 1e-6, 5e-6)
        us = _alias_model("claude-4-5-haiku", raw, 1.1e-6, 5.5e-6)
        us.cost = CostConfig(input=1.1e-6, output=5.5e-6)
        with _patch_config([canonical, us]):
            pricer = CatalogPricer()
            assert pricer.price(raw, TokenCounts(input=10, cache_read=1000)) == pytest.approx(11e-6)
            assert pricer.unpriced == [raw]

    def test_same_deployment_uses_its_priced_duplicate_when_another_has_no_tariff(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        raw = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        a = _alias_model("a", raw, 1e-6, 5e-6)
        b = _alias_model("b", raw, 1e-6, 5e-6)
        c = _alias_model("c", raw, 1e-6, 5e-6)
        c.cost = None
        with _patch_config([a, b]):
            assert lookup_cost_config(raw) == a.cost
        with _patch_config([a, b, c]):
            assert lookup_cost_config(raw) == a.cost

    @pytest.mark.parametrize("missing_first", [True, False])
    def test_shared_canonical_base_name_skips_missing_tariffs(self, missing_first):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        name = "claude-haiku-4-5-20251001"
        priced = _alias_model(name, "haiku-primary", 1e-6, 5e-6)
        missing = _alias_model(name, "haiku-without-price", 2e-6, 10e-6)
        missing.cost = None
        models = [missing, priced] if missing_first else [priced, missing]
        with _patch_config(models):
            assert lookup_cost_config(name) is priced.cost

    def test_shared_canonical_base_name_keeps_first_priced_catalog_entry_and_logs_the_tier(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        name = "claude-haiku-4-5-20251001"
        first = _alias_model(name, "haiku-primary", 1e-6, 5e-6)
        second = _alias_model(name, "haiku-secondary", 2e-6, 10e-6)
        with _patch_config([first, second]), patch(f"{_MODULE}.logger.warning") as warning:
            assert lookup_cost_config(name) is first.cost
        assert warning.call_args.args[1:] == (name, "raw", "base_name")

    def test_exact_deployment_wins_over_a_shared_base_name_regardless_of_catalog_order(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        name = "claude-haiku-4-5-20251001"
        shared_base = _alias_model(name, "haiku-other", 2e-6, 10e-6)
        deployment = _alias_model("haiku-exact", name, 1e-6, 5e-6)
        with _patch_config([shared_base, deployment]):
            assert lookup_cost_config(name) is deployment.cost

    def test_conflicting_exact_deployment_tariffs_are_unpriced_and_log_the_tier(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        raw = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        first = _alias_model("haiku-one", raw, 1e-6, 5e-6)
        second = _alias_model("haiku-two", raw, 2e-6, 10e-6)
        with _patch_config([first, second]), patch(f"{_MODULE}.logger.warning") as warning:
            assert lookup_cost_config("converse/" + raw) is None
        assert warning.call_args.args[1:] == ("converse/" + raw, "unwrapped", "deployment_name")

    def test_historical_session_cache_read_cost_matches_the_ticket(self):
        """Ticket trace f61ea7aa: 80,579 Haiku (Bedrock id) at 1e-7 + 212,878 Opus at 5.5e-7 = 0.1251408."""
        from codemie.service.analytics.handlers.session_money import price_session_models

        haiku = _alias_model("claude-haiku-4-5-20251001", None, 1e-6, 5e-6)
        haiku.cost = CostConfig(input=1e-6, output=5e-6, cache_read_input_token_cost=1e-7)
        opus = _alias_model("claude-opus-5", None, 5e-6, 25e-6)
        opus.cost = CostConfig(input=5e-6, output=25e-6, cache_read_input_token_cost=5.5e-7)
        rows = [
            {
                "session_id": "f61ea7aa",
                "model_name": "anthropic.claude-haiku-4-5-20251001-v1:0",
                "cache_read_tokens": 80_579,
            },
            {"session_id": "f61ea7aa", "model_name": "claude-opus-5", "cache_read_tokens": 212_878},
        ]
        with _patch_config([haiku, opus]):
            money = price_session_models(rows)
        assert money.cache_read == pytest.approx(0.1251408, abs=1e-12)
        assert money.cache_read_by_session["f61ea7aa"] == pytest.approx(0.1251408, abs=1e-12)
        assert money.pricer.unpriced == []

    def test_ambiguous_aliases_with_different_prices_yield_none(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import lookup_cost_config

        a = _alias_model("a", "us.anthropic.claude-haiku-4-5-20251001-v1:0", 1e-6, 5e-6)
        b = _alias_model("b", "eu.anthropic.claude-haiku-4-5-20251001-v1:0", 2e-6, 5e-6)
        with _patch_config([a, b]), patch(f"{_MODULE}.logger.warning") as warning:
            assert lookup_cost_config("converse/global.anthropic.claude-haiku-4-5-20251001-v1:0") is None
        assert "normalized_alias" in warning.call_args.args[0]

    def test_unknown_bedrock_model_stays_unpriced(self):
        from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

        raw = "anthropic.claude-unknown-9-20251001-v1:0"
        with _patch_config([_alias_model("claude-haiku-4-5-20251001", None, 1e-6, 5e-6)]):
            pricer = CatalogPricer()
            assert pricer.price(raw, TokenCounts(input=10)) == 0.0
            assert pricer.unpriced == [raw]

    def test_mixed_canonical_and_raw_sessions_total_and_cache_read_agree(self):
        from codemie.service.analytics.handlers.session_money import price_session_models

        rows = [
            {
                "session_id": "s1",
                "model_name": "claude-haiku-4-5-20251001",
                "input_tokens": 10,
                "cache_read_tokens": 1000,
            },
            {"session_id": "s1", "model_name": self._BEDROCK_IDS[0], "input_tokens": 10, "cache_read_tokens": 1000},
            {"session_id": "s2", "model_name": self._BEDROCK_IDS[2], "output_tokens": 2},
        ]
        with _patch_config([_alias_model("claude-haiku-4-5-20251001", None, 1e-6, 5e-6)]):
            money = price_session_models(rows)
        assert money.by_session["s1"] == pytest.approx(2 * (10e-6 + 1000e-7))
        assert money.cache_read_by_session["s1"] == pytest.approx(2 * 1000e-7)
        assert money.by_session["s2"] == pytest.approx(10e-6)
        assert money.total == pytest.approx(money.by_session["s1"] + money.by_session["s2"])
        assert money.pricer.unpriced == []


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
    model.deployment_name = None
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
