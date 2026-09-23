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

"""Routing analytics monitoring service — emits routing_call_usage ES events."""

from __future__ import annotations

from typing import TYPE_CHECKING

from codemie.core.routing_info import RoutingInfo
from codemie.service.analytics.metric_names import MetricName
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService

if TYPE_CHECKING:
    from codemie.rest_api.security.user import User

_LITELLM_ROUTING_FAMILY = "litellm"
_CLASSIFIER_TOKEN_ATTRIBUTES = (
    "classifier_input_tokens",
    "classifier_output_tokens",
    "classifier_cached_tokens",
    "classifier_cache_creation_tokens",
    "classifier_total_tokens",
)


class RoutingMonitoringService(BaseMonitoringService):
    """Emit routing_call_usage events to Elasticsearch via OpenTelemetry log exporter."""

    @classmethod
    def send_routing_metric(
        cls,
        *,
        user: "User",
        routing: RoutingInfo,
        conversation_id: str | None,
        assistant_id: str | None,
        project: str,
        request_id: str | None = None,
        endpoint: str | None = None,
        llm_run_id: str | None = None,
    ) -> None:
        """Emit a routing_call_usage event if routing data is present.

        No-op when routing.is_empty() to avoid emitting empty documents.

        ``llm_run_id`` correlates this event back to the specific LLM run within a
        conversation turn that produced it. A single /model generation may involve
        multiple LLM runs (tool-calling loop, fallback, etc.), each with its own routing
        decision — callers emit one event per run rather than collapsing them, so
        ``llm_run_id`` distinguishes those events sharing the same ``request_id``/
        ``conversation_id``.
        """
        if routing.is_empty():
            return
        attributes = {
            "user_id": str(user.id),
            "user_email": getattr(user, "email", None) or getattr(user, "username", None),
            "project": project,
            "conversation_id": conversation_id,
            "assistant_id": str(assistant_id) if assistant_id else None,
            "request_id": request_id,
            "llm_run_id": llm_run_id,
            "endpoint": endpoint,
            # routed_model_label intentionally omitted: it's a UI display label derived
            # from routed_model for chat rendering, not an analytics dimension. routed_model
            # itself is already emitted below and is what every routing query groups by.
            "routed_model": routing.routed_model,
            "requested_model": routing.requested_model,
            "tier": routing.tier,
            "routing_tier_raw": routing.routing_tier_raw,
            "decision_source": routing.decision_source,
            "routing_source": routing.routing_source,
            "confidence": routing.confidence,
            "routing_family": routing.routing_family,
            "routing_cost_known": routing.routing_cost_known,
            "classifier_model": routing.classifier_model,
            "router_type": routing.router_type,
            "router_score": routing.router_score,
            "classifier_cost_usd": routing.classifier_cost_usd,
            "classifier_input_tokens": routing.classifier_input_tokens,
            "classifier_output_tokens": routing.classifier_output_tokens,
            "classifier_cached_tokens": routing.classifier_cached_tokens,
            "classifier_cache_creation_tokens": routing.classifier_cache_creation_tokens,
            "classifier_total_tokens": routing.classifier_total_tokens,
            "routed_input_tokens": routing.routed_input_tokens,
            "routed_output_tokens": routing.routed_output_tokens,
            "routed_cached_tokens": routing.routed_cached_tokens,
            "routed_cache_creation_tokens": routing.routed_cache_creation_tokens,
            "routed_total_tokens": routing.routed_total_tokens,
            "routed_cache_hit": routing.routed_cache_hit,
            # Savings fields (NEW)
            "counterfactual_model": routing.counterfactual_model,
            "original_cost_usd": routing.original_cost_usd,
            "estimated_max_cost_usd": routing.estimated_max_cost_usd,
            "potential_savings_usd": routing.potential_savings_usd,
        }
        if routing.routing_family == _LITELLM_ROUTING_FAMILY:
            # LiteLLM reports only the classifier's cost, never its tokens.
            for key in _CLASSIFIER_TOKEN_ATTRIBUTES:
                attributes.pop(key)
        cls.send_count_metric(name=MetricName.ROUTING_CALL_USAGE.value, attributes=attributes)
