# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the “License”);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import MagicMock, patch

import pytest

from codemie.configs.llm_config import LiteLLMRouterConfig, LLMModel
from codemie.enterprise.litellm.router import _LITELLM_COMPLEXITY_ROUTER
from codemie.enterprise.switchyard.router import SwitchyardRouter
from codemie.core.router import (
    CallContext,
    ClassifierCall,
    NULL_ROUTER,
    NullRouter,
    Router,
    RoutingDecision,
    is_routable_endpoint,
)
from codemie.core.routing_info import RoutingInfo
from codemie.service.llm_service.router_factory import create_router


def test_call_context_defaults():
    ctx = CallContext(run_id="abc")
    assert ctx.run_id == "abc"
    assert ctx.decision is None
    assert ctx.headers is None


class _StubRouter(Router):
    """Minimal concrete Router exercising only the abstract methods, to test the
    non-abstract defaults (extract_classifier_usage, candidate_models, routing_info) in
    isolation. build_chat_model's default is exercised separately in
    test_router_chat_model.py — it needs RouterChatModel."""

    name = "stub"
    routing_family = "stub"

    async def decide(self, messages):
        return None

    def extract(self, response) -> RoutingInfo:
        return RoutingInfo()


def test_router_default_candidate_models_is_empty():
    assert _StubRouter().candidate_models() == ()


def test_router_default_extract_classifier_usage_is_none():
    ctx = CallContext(run_id="r1")
    assert _StubRouter().extract_classifier_usage(ctx) is None


def test_router_default_routing_info_combines_canonical_fields():
    """routing_info() is the decision-only counterpart to extract() (no response needed) —
    used wherever a RoutingDecision is already in hand (RouterChatModel, apply_router_routing,
    TokensCalculationCallback)."""
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier="capable",
        decision_source="llm_classifier",
        routing_family="stub",
        classifier=ClassifierCall(cost_usd=0.001),
    )
    info = _StubRouter().routing_info(decision)
    assert info.routed_model == "claude-4-5-haiku"
    assert info.classifier_cost_usd == 0.001
    assert info.routing_family == "stub"


def test_router_default_routing_info_classifier_cost_none_when_no_classifier():
    decision = RoutingDecision(
        model="claude-4-5-haiku", tier="capable", decision_source="heuristic", routing_family="stub"
    )
    info = _StubRouter().routing_info(decision)
    assert info.classifier_cost_usd is None
    assert info.routing_family == "stub"


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("v1/messages", True),
        ("/v1/messages", True),
        ("v1/chat/completions", True),
        ("v1/embeddings", False),
        ("v1/models", False),
    ],
)
def test_is_routable_endpoint(endpoint, expected):
    """Mechanism-agnostic: which HTTP endpoints carry a routable `messages` body — true for
    any decide()-capable Router, not owned by Switchyard (moved out of
    enterprise/switchyard/engine.py, see that module's history)."""
    assert is_routable_endpoint(endpoint) is expected


class TestNullRouter:
    """NullRouter is the genuinely LiteLLM-agnostic 'no routing at all' identity — replaces
    the old PassiveRouter(name='none') singleton, which incorrectly shared LiteLLM
    header-reading logic with the real LiteLLM auto-router identity."""

    @pytest.mark.asyncio
    async def test_decide_is_always_none(self):
        assert await NULL_ROUTER.decide([{"role": "user", "content": "hi"}]) is None

    def test_extract_is_always_empty_regardless_of_headers(self):
        message = MagicMock()
        message.response_metadata = {"headers": {"x-litellm-router-routed-model": "claude-haiku-4-5"}}
        assert NULL_ROUTER.extract(message).is_empty()

    def test_candidate_models_is_empty(self):
        assert NullRouter().candidate_models() == ()

    def test_routing_family_is_none(self):
        assert NullRouter().routing_family == "none"

    def test_build_chat_model_returns_raw_client(self, monkeypatch):
        """Empty candidate_models() routes through Router.build_chat_model's raw-client
        branch — the non-empty/wrap-in-RouterChatModel branch is covered separately in
        test_router_chat_model.py, which needs RouterChatModel itself."""
        sentinel = object()
        called_with = {}

        def fake_get_llm_by_credentials(**kwargs):
            called_with.update(kwargs)
            return sentinel

        monkeypatch.setattr("codemie.core.dependecies.get_llm_by_credentials", fake_get_llm_by_credentials)
        from codemie.core.router_chat_model import LLMParams

        result = NULL_ROUTER.build_chat_model(
            model_name="gpt-4.1", request_id="req-1", llm_params=LLMParams(temperature=0.2, top_p=None)
        )
        assert result is sentinel
        assert called_with == {
            "llm_model": "gpt-4.1",
            "request_id": "req-1",
            "temperature": 0.2,
            "top_p": None,
        }


def test_create_router_returns_null_router_for_undeclared_model():
    with patch("codemie.service.llm_service.llm_service.llm_service") as mock_service:
        mock_service.is_router_model.return_value = False
        mock_service.get_model_details.return_value = LLMModel(
            base_name="gpt-4.1", deployment_name="gpt-4.1", enabled=True
        )
        router = create_router("gpt-4.1")
    assert router is NULL_ROUTER


def test_create_router_returns_litellm_complexity_router_when_declared():
    with patch("codemie.service.llm_service.llm_service.llm_service") as mock_service:
        mock_service.is_router_model.return_value = False
        mock_service.get_model_details.return_value = LLMModel(
            base_name="smart-router",
            deployment_name="smart-router",
            enabled=True,
            litellm_router=LiteLLMRouterConfig(),
        )
        router = create_router("smart-router")
    assert router is _LITELLM_COMPLEXITY_ROUTER


def test_create_router_returns_switchyard_router_when_eligible():
    with (
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
        patch("codemie.enterprise.switchyard.engine.get_proxy_switchyard_router") as mock_get_engine,
    ):
        mock_service.is_router_model.return_value = True
        mock_get_engine.return_value = MagicMock(capable_model="claude-4-6-sonnet", efficient_model="claude-4-5-haiku")
        router = create_router("claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal")
    assert isinstance(router, SwitchyardRouter)
