# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""The RoutingDecision result type, shared by both the proxy and agent paths.

Kept in its own module (no litellm/libsy imports) so callers that only need
the type — e.g. TokensCalculationCallback — don't have to pull in the engine's
heavier dependencies just to type-check against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RoutingTier(StrEnum):
    """Which candidate model a routing decision selected."""

    CAPABLE = "capable"
    EFFICIENT = "efficient"


@dataclass(frozen=True)
class RoutingDecision:
    """Result of a single pick_model call — immutable, safe to pass across async boundaries."""

    model: str
    tier: RoutingTier
    capable_model: str  # original capable model name for metadata
    confidence: float | None
    classifier_used: bool = False
    classifier_model: str | None = None  # name of the model used for the classifier sub-call
    classifier_confidence: float | None = None  # P(capable needed) — classifier's P(efficient) inverted at extraction
    classifier_input_tokens: int | None = None
    classifier_output_tokens: int | None = None
    classifier_cached_tokens: int | None = None
    classifier_cache_creation_tokens: int | None = None
    classifier_cost_usd: float | None = None
    # Signal dimensions from state.extra (populated when built from Yana's Switchyard fork)
    signal_score: float | None = None
    signal_confidence: float | None = None
    signal_severity: float | None = None
    signal_spinning: float | None = None
    signal_exploring: float | None = None
    signal_production_intensity: float | None = None
    classifier_p_solve: float | None = None
    # String metadata from classifier state.extra (requires get_str — available after .so rebuild)
    classifier_crux: str | None = None
    classifier_primary_rule: str | None = None
    classifier_capability_boundary: str | None = None
    # Routing decision source from Switchyard state.extra
    # Values: override | tests_passed | dimensions | ambiguous | llm-classifier | fall_open
    decision_source: str | None = None
