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
from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer
from codemie.service.analytics.handlers.session_money import has_priced_model, priced_session_ids

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


def test_has_priced_model_true_when_one_model_has_a_catalog_price():
    with patch(_CATALOG, return_value=_catalog()):
        assert has_priced_model(["no-such-model", "priced-model"], CatalogPricer()) is True


def test_has_priced_model_false_when_no_model_has_a_catalog_price():
    with patch(_CATALOG, return_value=_catalog()):
        assert has_priced_model(["no-such-model"], CatalogPricer()) is False


def test_has_priced_model_false_for_an_empty_model_name():
    with patch(_CATALOG, return_value=_catalog()):
        assert has_priced_model([""], CatalogPricer()) is False


def test_has_priced_model_false_for_a_zero_rate_catalog_entry():
    """A zero-rate entry is no price, not a free model (EPMCDME-16522)."""
    with patch(_CATALOG, return_value=_catalog()):
        assert has_priced_model(["free-model"], CatalogPricer()) is False


def test_has_priced_model_false_for_an_empty_iterable():
    with patch(_CATALOG, return_value=_catalog()):
        assert has_priced_model([], CatalogPricer()) is False


def test_priced_session_ids_keeps_a_session_with_any_priced_model_only():
    rows = [
        {"session_id": "s1", "model_name": "no-such-model"},
        {"session_id": "s1", "model_name": "priced-model"},
        {"session_id": "s2", "model_name": "no-such-model"},
    ]
    with patch(_CATALOG, return_value=_catalog()):
        assert priced_session_ids(rows, CatalogPricer()) == {"s1"}
