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

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.router import ClassifierCall, RoutingDecision
from codemie.core.routing_info import RoutingInfo
from codemie.enterprise.switchyard.engine import RoutingTier
from codemie.enterprise.switchyard.proxy import _routing_info_to_headers, apply_router_routing


@pytest.mark.asyncio
async def test_apply_router_routing_rewrites_body_when_router_decides():
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        decision_source="llm_classifier",
        routing_family="switchyard",
        classifier=ClassifierCall(cached_tokens=30, cache_creation_tokens=10),
    )
    router = MagicMock()
    router.decide = AsyncMock(return_value=decision)
    request_body = {"model": "claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal", "messages": []}
    body_bytes = json.dumps(request_body).encode("utf-8")

    with patch("codemie.service.llm_service.router_factory.create_router", return_value=router):
        # Exercise the REAL SwitchyardRouter.routing_info() formula via a real engine double,
        # rather than a hand-built expected RoutingInfo — build_switchyard_routing_meta no
        # longer exists, so there's no shortcut object to construct the expectation from.
        from types import SimpleNamespace

        real_engine = SimpleNamespace(
            capable_model="claude-4-6-sonnet",
            efficient_model="claude-4-5-haiku",
            routing_mode="signal",
        )
        from codemie.enterprise.switchyard.router import SwitchyardRouter

        router.routing_info = MagicMock(side_effect=SwitchyardRouter(real_engine).routing_info)

        new_bytes, new_body, returned_decision, routing_info = await apply_router_routing(
            endpoint="v1/messages",
            router_name="claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal",
            request_body=request_body,
            body_bytes=body_bytes,
        )

    assert json.loads(new_bytes)["model"] == "claude-4-5-haiku"
    assert new_body is not None
    assert new_body["model"] == "claude-4-5-haiku"
    assert returned_decision is decision
    assert routing_info is not None
    assert routing_info.routed_model == "claude-4-5-haiku"
    assert routing_info.tier == "simple"
    assert routing_info.requested_model == "claude-4-6-sonnet"
    assert routing_info.classifier_cached_tokens == 30
    assert routing_info.classifier_cache_creation_tokens == 10
    assert routing_info.decision_source == "llm-classifier"
    assert routing_info.router_type == "stage"


def test_routing_info_to_headers_serialises_typed_fields():
    info = RoutingInfo(
        routed_model="claude-4-5-haiku",
        requested_model="claude-4-6-sonnet",
        tier="simple",
        decision_source="llm-classifier",
        routing_source="judge",
        router_type="composite",
        routing_family="switchyard",
        classifier_model="gpt-5.6-luna",
        classifier_input_tokens=120,
        classifier_output_tokens=40,
        classifier_cached_tokens=15,
        classifier_cache_creation_tokens=5,
        classifier_cost_usd=0.0009,
    )
    headers = _routing_info_to_headers(info)
    assert headers["x-codemie-routed-model"] == "claude-4-5-haiku"
    assert headers["x-codemie-requested-model"] == "claude-4-6-sonnet"
    assert headers["x-codemie-routing-tier"] == "simple"
    assert headers["x-codemie-routing-decision-source"] == "llm-classifier"
    assert headers["x-codemie-routing-source"] == "judge"
    assert headers["x-codemie-routing-router-type"] == "composite"
    assert headers["x-codemie-routing-family"] == "switchyard"
    assert headers["x-codemie-routing-classifier-model"] == "gpt-5.6-luna"
    assert headers["x-codemie-routing-classifier-input-tokens"] == "120"
    assert headers["x-codemie-routing-classifier-output-tokens"] == "40"
    assert headers["x-codemie-routing-classifier-cached-tokens"] == "15"
    assert headers["x-codemie-routing-classifier-cache-creation-tokens"] == "5"
    assert headers["x-codemie-routing-classifier-cost-usd"] == "0.0009"


def test_routing_info_to_headers_omits_none_fields():
    headers = _routing_info_to_headers(RoutingInfo())
    assert headers == {}


def test_routing_info_to_headers_works_for_a_litellm_shaped_routing_info():
    """The same codec must serialise a RoutingInfo built from LiteLLM's own headers
    identically to one built from a Switchyard decision — no field is Switchyard-specific."""
    info = RoutingInfo(
        routed_model="claude-sonnet-5",
        tier="complex",
        decision_source="llm-classifier",
        routing_family="litellm",
        router_type="complexity",
    )
    headers = _routing_info_to_headers(info)
    assert headers["x-codemie-routed-model"] == "claude-sonnet-5"
    assert headers["x-codemie-routing-tier"] == "complex"
    assert headers["x-codemie-routing-decision-source"] == "llm-classifier"
    assert headers["x-codemie-routing-family"] == "litellm"
    assert headers["x-codemie-routing-router-type"] == "complexity"


@pytest.mark.asyncio
async def test_apply_router_routing_noop_when_router_does_not_decide():
    router = MagicMock()
    router.decide = AsyncMock(return_value=None)
    request_body = {"model": "gpt-4.1", "messages": []}
    body_bytes = json.dumps(request_body).encode("utf-8")

    with patch("codemie.service.llm_service.router_factory.create_router", return_value=router):
        new_bytes, new_body, returned_decision, routing_info = await apply_router_routing(
            endpoint="v1/messages",
            router_name="gpt-4.1",
            request_body=request_body,
            body_bytes=body_bytes,
        )

    assert new_bytes == body_bytes
    assert new_body == request_body
    assert returned_decision is None
    assert routing_info is None


@pytest.mark.asyncio
async def test_apply_router_routing_noop_for_ineligible_endpoint():
    request_body = {"model": "claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal", "messages": []}
    body_bytes = json.dumps(request_body).encode("utf-8")

    new_bytes, new_body, decision, routing_info = await apply_router_routing(
        endpoint="v1/embeddings",
        router_name="claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal",
        request_body=request_body,
        body_bytes=body_bytes,
    )

    assert new_bytes == body_bytes
    assert new_body == request_body
    assert decision is None
    assert routing_info is None
