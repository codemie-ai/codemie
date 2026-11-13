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

from decimal import Decimal
from unittest.mock import MagicMock, patch

from codemie.configs.llm_config import CostConfig
from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer
from codemie.service.analytics.handlers.session_money import cost_available, token_counts, unpriced_session_ids

_CATALOG = "codemie.service.analytics.handlers.coding_agent_pricing.llm_service.get_all_llm_model_info"


def _catalog_model(name: str, cost: CostConfig) -> MagicMock:
    model = MagicMock()
    model.base_name = name
    model.deployment_name = name
    model.cost = cost
    return model


def _catalog() -> list[MagicMock]:
    return [
        _catalog_model("priced-model", CostConfig(input=0.000003, output=0.000015)),
        _catalog_model("free-model", CostConfig(input=0.0, output=0.0)),
    ]


def test_cost_available_true_when_one_usage_model_has_a_catalog_price():
    rows = [{"model_name": "no-such-model", "input_tokens": 1}, {"model_name": "priced-model", "input_tokens": 1}]
    with patch(_CATALOG, return_value=_catalog()):
        assert cost_available(rows, CatalogPricer()) is True


def test_cost_available_false_when_no_usage_model_has_a_catalog_price():
    with patch(_CATALOG, return_value=_catalog()):
        assert cost_available([{"model_name": "no-such-model", "input_tokens": 1}], CatalogPricer()) is False


def test_cost_available_false_for_usage_under_an_empty_model_name():
    with patch(_CATALOG, return_value=_catalog()):
        assert cost_available([{"model_name": "", "output_tokens": 3}], CatalogPricer()) is False


def test_cost_available_false_for_a_zero_rate_catalog_entry():
    """A zero-rate entry is no price, not a free model (EPMCDME-16522)."""
    with patch(_CATALOG, return_value=_catalog()):
        assert cost_available([{"model_name": "free-model", "input_tokens": 1}], CatalogPricer()) is False


def test_cost_available_stays_true_for_a_model_priced_for_only_some_token_kinds():
    """A catalogued model with no cache-read rate is priced: the session keeps `cost_available: true`, the cache-read
    tokens cost 0 and the model is flagged unpriced, so the cost is understated and marked, not unknown
    (EPMCDME-17138 review CR-002: current behaviour kept and pinned)."""
    row = {"session_id": "s1", "model_name": "no-cache-rate-model", "input_tokens": 1000, "cache_read_tokens": 5000}
    catalog = [_catalog_model("no-cache-rate-model", CostConfig(input=0.000003, output=0.000015))]
    with patch(_CATALOG, return_value=catalog):
        pricer = CatalogPricer()
        assert cost_available([row], pricer) is True
        assert unpriced_session_ids([row], pricer) == set()
        cost, missing = pricer.price_flagged("no-cache-rate-model", token_counts(row))
    assert missing is True
    assert cost == 1000 * 0.000003  # the cache-read tokens add nothing
    assert pricer.unpriced == ["no-cache-rate-model"]


def test_cost_available_true_without_rows():
    """No model usage is a known $0.00 cost (EPMCDME-17138)."""
    with patch(_CATALOG, return_value=_catalog()):
        assert cost_available([], CatalogPricer()) is True


def test_cost_available_ignores_zero_and_negative_token_rows():
    rows = [
        {"model_name": "no-such-model"},
        {"model_name": "no-such-model", "input_tokens": -5},
        {"model_name": "no-such-model", "cache_read_tokens": 0.4},
    ]
    with patch(_CATALOG, return_value=_catalog()):
        assert cost_available(rows, CatalogPricer()) is True


def test_cost_available_counts_a_cache_creation_only_row_as_usage():
    rows = [{"model_name": "no-such-model", "cache_creation_tokens": 10}]
    with patch(_CATALOG, return_value=_catalog()):
        assert cost_available(rows, CatalogPricer()) is False


def test_unpriced_session_ids_names_only_sessions_with_unpriced_usage():
    rows = [
        {"session_id": "s1", "model_name": "no-such-model", "input_tokens": 1},
        {"session_id": "s1", "model_name": "priced-model", "input_tokens": 1},
        {"session_id": "s2", "model_name": "no-such-model", "input_tokens": 1},
        {"session_id": "s3", "model_name": "no-such-model"},
    ]
    with patch(_CATALOG, return_value=_catalog()):
        assert unpriced_session_ids(rows, CatalogPricer()) == {"s2"}


def test_token_counts_rounds_a_fractional_split_share_instead_of_truncating():
    """A fallback dispatch's token sums are request_share fractions (reader.py's request_shares CTE), delivered
    as numeric (Decimal) or float values; truncating them toward zero (the old int()) would drop up to one token
    per field. round() keeps the nearest whole token instead."""
    counts = token_counts(
        {
            "input_tokens": 2.9,
            "output_tokens": Decimal("1.6"),
            "cache_read_tokens": Decimal("3.8"),
            "cache_creation_tokens": 5.9,
            "cache_creation_5m_tokens": 2.9,
            "cache_creation_1h_tokens": None,
        }
    )
    assert (counts.input, counts.output, counts.cache_read, counts.cache_creation) == (3, 2, 4, 6)
    assert (counts.cache_5m, counts.cache_1h) == (3, None)
