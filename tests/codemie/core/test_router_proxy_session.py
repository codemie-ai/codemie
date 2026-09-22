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

from codemie.core.router import RoutingDecision
from codemie.core.router_proxy_session import RouterProxySession
from codemie.core.routing_info import RoutingInfo


def test_for_request_resolves_router_via_create_router():
    router = MagicMock()
    with patch("codemie.service.llm_service.router_factory.create_router", return_value=router) as mock_create:
        session = RouterProxySession.for_request("gpt-4.1")

    mock_create.assert_called_once_with("gpt-4.1")
    assert session.decision is None


class TestDecideAndRewrite:
    @pytest.mark.asyncio
    async def test_rewrites_body_when_router_decides_a_different_model(self):
        decision = RoutingDecision(
            model="claude-4-5-haiku", tier="efficient", decision_source="llm_classifier", routing_family="switchyard"
        )
        router = MagicMock()
        router.decide = AsyncMock(return_value=decision)
        session = RouterProxySession(router)
        request_body = {"model": "claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal", "messages": []}
        body_bytes = json.dumps(request_body).encode("utf-8")

        new_bytes, new_body = await session.decide_and_rewrite("v1/messages", request_body, body_bytes)

        assert json.loads(new_bytes)["model"] == "claude-4-5-haiku"
        assert new_body is not None
        assert new_body["model"] == "claude-4-5-haiku"
        assert session.decision is decision

    @pytest.mark.asyncio
    async def test_keeps_body_unchanged_when_decided_model_matches_request(self):
        decision = RoutingDecision(
            model="claude-4-6-sonnet", tier="complex", decision_source="heuristic", routing_family="switchyard"
        )
        router = MagicMock()
        router.decide = AsyncMock(return_value=decision)
        session = RouterProxySession(router)
        request_body = {"model": "claude-4-6-sonnet", "messages": []}
        body_bytes = json.dumps(request_body).encode("utf-8")

        new_bytes, new_body = await session.decide_and_rewrite("v1/messages", request_body, body_bytes)

        assert new_bytes is body_bytes
        assert new_body is request_body
        assert session.decision is decision

    @pytest.mark.asyncio
    async def test_noop_when_router_does_not_decide(self):
        router = MagicMock()
        router.decide = AsyncMock(return_value=None)
        session = RouterProxySession(router)
        request_body = {"model": "gpt-4.1", "messages": []}
        body_bytes = json.dumps(request_body).encode("utf-8")

        new_bytes, new_body = await session.decide_and_rewrite("v1/messages", request_body, body_bytes)

        assert new_bytes == body_bytes
        assert new_body == request_body
        assert session.decision is None

    @pytest.mark.asyncio
    async def test_noop_for_ineligible_endpoint_without_calling_decide(self):
        router = MagicMock()
        router.decide = AsyncMock(
            return_value=RoutingDecision(model="x", tier="t", decision_source="s", routing_family="f")
        )
        session = RouterProxySession(router)
        request_body = {"model": "claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal", "messages": []}
        body_bytes = json.dumps(request_body).encode("utf-8")

        new_bytes, new_body = await session.decide_and_rewrite("v1/embeddings", request_body, body_bytes)

        assert new_bytes == body_bytes
        assert new_body == request_body
        assert session.decision is None
        router.decide.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_noop_for_empty_request_body_without_calling_decide(self):
        router = MagicMock()
        session = RouterProxySession(router)

        new_bytes, new_body = await session.decide_and_rewrite("v1/messages", None, b"")

        assert new_bytes == b""
        assert new_body is None
        router.decide.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_for_empty_dict_request_body_without_calling_decide(self):
        """`not request_body` also catches an empty dict (truthiness), not just None."""
        router = MagicMock()
        session = RouterProxySession(router)

        new_bytes, new_body = await session.decide_and_rewrite("v1/messages", {}, b"{}")

        assert new_bytes == b"{}"
        assert new_body == {}
        router.decide.assert_not_called()


class TestRoutingInfo:
    @pytest.mark.asyncio
    async def test_passes_stored_decision_and_response_headers_to_router(self):
        decision = RoutingDecision(
            model="claude-4-5-haiku", tier="efficient", decision_source="llm_classifier", routing_family="switchyard"
        )
        router = MagicMock()
        router.decide = AsyncMock(return_value=decision)
        router.routing_info.return_value = RoutingInfo(routed_model="claude-4-5-haiku")
        session = RouterProxySession(router)
        await session.decide_and_rewrite("v1/messages", {"model": "alias", "messages": []}, b"{}")

        result = session.routing_info({"some": "header"})

        assert result == RoutingInfo(routed_model="claude-4-5-haiku")
        router.routing_info.assert_called_once()
        assert router.routing_info.call_args.args[0] is decision
        assert list(router.routing_info.call_args.args[1]) == [{"some": "header"}]

    def test_passes_none_decision_when_decide_and_rewrite_never_ran(self):
        """Covers both the decide()-returned-None case and the never-attempted case (e.g. a
        non-routable endpoint, or an endpoint where decide_and_rewrite was simply never
        called) — routing_info() must still work, passing the response headers straight to
        the router so a LiteLLM-declared alias's own auto-router signal can still surface
        even when CodeMie's own decide() never ran for this request at all."""
        router = MagicMock()
        router.routing_info.return_value = RoutingInfo(routed_model="claude-haiku-4-5")
        session = RouterProxySession(router)

        result = session.routing_info({"x-litellm-router-routed-model": "claude-haiku-4-5"})

        assert result == RoutingInfo(routed_model="claude-haiku-4-5")
        router.routing_info.assert_called_once()
        assert router.routing_info.call_args.args[0] is None
