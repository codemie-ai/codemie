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

from codemie.core.routing_info import RoutingInfo
from codemie.rest_api.security.user import User
from codemie.service.monitoring.routing_monitoring_service import RoutingMonitoringService


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user-123"
    user.email = "test@example.com"
    return user


def test_noop_when_routing_is_empty(mock_user):
    """send_routing_metric is a no-op when routing.is_empty()."""
    with patch.object(RoutingMonitoringService, "send_count_metric") as m:
        RoutingMonitoringService.send_routing_metric(
            user=mock_user,
            routing=RoutingInfo(),
            conversation_id=None,
            assistant_id=None,
            project="p",
        )
        m.assert_not_called()


def test_emits_routing_dimensions(mock_user):
    """send_routing_metric calls send_count_metric with all routing dimensions."""
    ri = RoutingInfo(
        routed_model="haiku",
        tier="medium",
        routing_tier_raw="efficient",
        requested_model="opus",
        decision_source="llm-classifier",
        routing_source="judge",
        routing_family="switchyard",
        routing_cost_known=True,
        confidence=0.92,
        classifier_model="claude-4-5-haiku",
        router_type="complexity",
        router_score=0.92,
        classifier_cost_usd=0.0001,
        classifier_cache_creation_tokens=2,
        routed_input_tokens=100,
        routed_output_tokens=20,
        routed_cached_tokens=5,
        routed_cache_creation_tokens=3,
        routed_total_tokens=120,
        routed_cache_hit=True,
    )
    with patch.object(RoutingMonitoringService, "send_count_metric") as m:
        RoutingMonitoringService.send_routing_metric(
            user=mock_user,
            routing=ri,
            conversation_id="c1",
            assistant_id="a1",
            project="p1",
            request_id="r1",
        )
        m.assert_called_once()
        call_kwargs = m.call_args
        # accept either positional name or kwarg
        name_arg = call_kwargs.kwargs.get("name") or (call_kwargs.args[0] if call_kwargs.args else None)
        assert name_arg == "routing_call_usage"
        attrs = call_kwargs.kwargs.get("attributes", {})
        assert attrs.get("routed_model") == "haiku"
        assert attrs.get("tier") == "medium"
        assert attrs.get("routing_tier_raw") == "efficient"
        assert attrs.get("decision_source") == "llm-classifier"
        assert attrs.get("routing_source") == "judge"
        assert attrs.get("routing_family") == "switchyard"
        assert attrs.get("routing_cost_known") is True
        assert attrs.get("classifier_model") == "claude-4-5-haiku"
        assert attrs.get("router_type") == "complexity"
        assert attrs.get("router_score") == 0.92
        assert attrs.get("classifier_cache_creation_tokens") == 2
        assert attrs.get("request_id") == "r1"
        assert attrs.get("conversation_id") == "c1"
        assert attrs.get("routed_input_tokens") == 100
        assert attrs.get("routed_output_tokens") == 20
        assert attrs.get("routed_cached_tokens") == 5
        assert attrs.get("routed_cache_creation_tokens") == 3
        assert attrs.get("routed_total_tokens") == 120
        assert attrs.get("routed_cache_hit") is True


def test_emits_llm_run_id_for_correlation(mock_user):
    """send_routing_metric passes llm_run_id through so multiple routing events emitted for
    the same conversation/request (one per LLM run) can be correlated back to their run."""
    ri = RoutingInfo(routed_model="haiku", tier="simple")
    with patch.object(RoutingMonitoringService, "send_count_metric") as m:
        RoutingMonitoringService.send_routing_metric(
            user=mock_user,
            routing=ri,
            conversation_id="c1",
            assistant_id="a1",
            project="p1",
            request_id="r1",
            llm_run_id="run-42",
        )
        m.assert_called_once()
        attrs = m.call_args.kwargs.get("attributes", {})
        assert attrs.get("llm_run_id") == "run-42"


def test_llm_run_id_defaults_to_none(mock_user):
    """llm_run_id is optional and defaults to None when the caller doesn't pass it (legacy
    single-event-per-turn callers)."""
    ri = RoutingInfo(routed_model="sonnet")
    with patch.object(RoutingMonitoringService, "send_count_metric") as m:
        RoutingMonitoringService.send_routing_metric(
            user=mock_user,
            routing=ri,
            conversation_id=None,
            assistant_id=None,
            project="myproject",
        )
        attrs = m.call_args.kwargs.get("attributes", {})
        assert attrs.get("llm_run_id") is None


def test_emits_with_none_optional_fields(mock_user):
    """send_routing_metric works when optional fields are None."""
    ri = RoutingInfo(routed_model="sonnet")
    with patch.object(RoutingMonitoringService, "send_count_metric") as m:
        RoutingMonitoringService.send_routing_metric(
            user=mock_user,
            routing=ri,
            conversation_id=None,
            assistant_id=None,
            project="myproject",
        )
        m.assert_called_once()
