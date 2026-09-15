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
from codemie.enterprise.switchyard.router import build_switchyard_routing_meta


@pytest.mark.asyncio
async def test_apply_router_routing_rewrites_body_when_router_decides():
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        classifier=ClassifierCall(cached_tokens=30, cache_creation_tokens=10),
    )
    # apply_router_routing() no longer builds RoutingInfo itself (see Router.routing_info) —
    # it just forwards whatever the router returns, so the double stubs that method directly
    # with the same construction Router.routing_info's real default would produce.
    expected_routing_info = RoutingInfo(
        routed_model=decision.model,
        classifier_cost_usd=None,
        meta=build_switchyard_routing_meta(decision, capable_model="claude-4-6-sonnet").to_headers(),
    )
    router = MagicMock()
    router.decide = AsyncMock(return_value=decision)
    router.routing_info = MagicMock(return_value=expected_routing_info)
    request_body = {"model": "claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal", "messages": []}
    body_bytes = json.dumps(request_body).encode("utf-8")

    with patch("codemie.service.llm_service.router_factory.create_router", return_value=router):
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
    # P0 regression coverage: the decision detail RoutingInfo's canonical fields don't carry
    # (tier, requested model, classifier cache-token detail) must still reach the proxy
    # response headers via RoutingInfo.meta.
    assert routing_info.meta["x-codemie-routing-tier"] == "efficient"
    assert routing_info.meta["x-codemie-requested-model"] == "claude-4-6-sonnet"
    assert routing_info.meta["x-codemie-routing-classifier-cached-tokens"] == "30"
    assert routing_info.meta["x-codemie-routing-classifier-cache-creation-tokens"] == "10"


def test_routing_info_to_headers_forwards_meta_and_canonical_fields_win_on_collision():
    info = RoutingInfo(
        routed_model="claude-4-5-haiku",
        classifier_cost_usd=0.001,
        meta={
            "x-codemie-routing-tier": "efficient",
            # Deliberately stale/conflicting values for the two canonical headers — the
            # canonical RoutingInfo fields must win, not whatever happens to be in meta.
            "x-codemie-routed-model": "stale-value",
            "x-codemie-routing-classifier-cost-usd": "stale-value",
        },
    )
    headers = _routing_info_to_headers(info)
    assert headers["x-codemie-routing-tier"] == "efficient"
    assert headers["x-codemie-routed-model"] == "claude-4-5-haiku"
    assert headers["x-codemie-routing-classifier-cost-usd"] == "0.001"


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
