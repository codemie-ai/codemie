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

import pytest

from codemie.core.routing_costs import with_counterfactual_costs
from codemie.core.routing_info import RoutingInfo


def test_with_counterfactual_costs_populates_all_three_cost_fields():
    """The function must populate original_cost, estimated_max, and savings."""
    routing = RoutingInfo(
        routed_model="claude-sonnet-5",
        counterfactual_model="claude-opus-5",
    )

    result = with_counterfactual_costs(
        routing,
        actual_cost_usd=0.05,
        estimated_max_cost_usd=0.12,
    )

    assert result.routed_model == "claude-sonnet-5"
    assert result.counterfactual_model == "claude-opus-5"
    assert result.original_cost_usd == 0.05
    assert result.estimated_max_cost_usd == 0.12
    assert result.potential_savings_usd == pytest.approx(0.07)  # 0.12 - 0.05


def test_with_counterfactual_costs_returns_unchanged_when_estimated_max_is_none():
    """When estimated_max is None, return routing unchanged."""
    routing = RoutingInfo(routed_model="claude-sonnet-5")

    result = with_counterfactual_costs(
        routing,
        actual_cost_usd=0.05,
        estimated_max_cost_usd=None,
    )

    assert result is routing  # Same object, unchanged
    assert result.original_cost_usd is None
    assert result.estimated_max_cost_usd is None
    assert result.potential_savings_usd is None


def test_with_counterfactual_costs_savings_can_be_negative():
    """Savings can be negative when routing chose an expensive model."""
    routing = RoutingInfo(
        routed_model="claude-opus-5",
        counterfactual_model="claude-sonnet-5",
    )

    result = with_counterfactual_costs(
        routing,
        actual_cost_usd=0.12,
        estimated_max_cost_usd=0.05,
    )

    assert result.potential_savings_usd == pytest.approx(-0.07)  # 0.05 - 0.12
