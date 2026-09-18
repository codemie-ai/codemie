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

"""Compute counterfactual routing costs — the single source of truth for the savings formula."""

from codemie.core.routing_info import RoutingInfo


def with_counterfactual_costs(
    routing: RoutingInfo,
    *,
    actual_cost_usd: float,
    estimated_max_cost_usd: float | None,
) -> RoutingInfo:
    """Return a copy of `routing` with the three cost fields filled in.

    Returns `routing` unchanged when the counterfactual is not computable
    (estimated_max_cost_usd is None).

    Args:
        routing: The RoutingInfo to augment. Must have routed_model and counterfactual_model set.
        actual_cost_usd: The real spend for this LLM run (routed model + classifier).
        estimated_max_cost_usd: The same tokens priced at counterfactual_model
                                (no classifier sub-cost).

    Returns:
        A new RoutingInfo with original_cost_usd, estimated_max_cost_usd, and
        potential_savings_usd populated. If estimated_max_cost_usd is None,
        returns `routing` unchanged.
    """
    if estimated_max_cost_usd is None:
        return routing
    return routing.model_copy(
        update={
            "original_cost_usd": actual_cost_usd,
            "estimated_max_cost_usd": estimated_max_cost_usd,
            "potential_savings_usd": estimated_max_cost_usd - actual_cost_usd,
        }
    )
