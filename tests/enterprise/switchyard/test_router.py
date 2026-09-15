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

import pytest
from unittest.mock import AsyncMock, MagicMock

from codemie.core.routing_info import _ROUTING_INFO_KEY
from codemie.core.router import CallContext, ClassifierCall, RoutingDecision
from codemie.enterprise.switchyard.engine import RoutingTier
from codemie.enterprise.switchyard.router import SwitchyardRouter, build_switchyard_routing_meta


def _make_engine(**decide_kwargs):
    engine = MagicMock()
    engine.capable_model = "claude-4-6-sonnet"
    engine.efficient_model = "claude-4-5-haiku"
    engine.capable_model_deployment_name = "us.anthropic.claude-4-6-sonnet"
    engine.efficient_model_deployment_name = "us.anthropic.claude-4-5-haiku"
    engine.pick_model = AsyncMock(
        return_value=RoutingDecision(
            model="us.anthropic.claude-4-5-haiku",
            tier=RoutingTier.EFFICIENT,
            **decide_kwargs,
        )
    )
    return engine


@pytest.mark.asyncio
async def test_decide_delegates_to_engine_pick_model():
    engine = _make_engine()
    router = SwitchyardRouter(engine)
    decision = await router.decide([{"role": "user", "content": "hi"}])
    engine.pick_model.assert_awaited_once_with([{"role": "user", "content": "hi"}])
    assert decision is not None
    assert decision.model == "us.anthropic.claude-4-5-haiku"


def test_candidate_models_returns_deployment_names():
    router = SwitchyardRouter(_make_engine())
    assert set(router.candidate_models()) == {"us.anthropic.claude-4-6-sonnet", "us.anthropic.claude-4-5-haiku"}


def test_extract_reads_stamped_response_metadata():
    router = SwitchyardRouter(_make_engine())
    message = MagicMock()
    message.response_metadata = {_ROUTING_INFO_KEY: {"routed_model": "claude-4-5-haiku", "classifier_cost_usd": 0.002}}
    info = router.extract(message)
    assert info.routed_model == "claude-4-5-haiku"
    assert info.classifier_cost_usd == 0.002


def test_extract_empty_when_not_stamped():
    router = SwitchyardRouter(_make_engine())
    message = MagicMock()
    message.response_metadata = {}
    assert router.extract(message).is_empty()


def test_extract_reads_stamped_response_metadata_from_llm_result():
    """TokensCalculationCallback.on_llm_end (a real LangChain AsyncCallbackHandler hook) is
    always invoked with the LLMResult wrapper, not a bare message — extract() must unwrap it,
    not just handle the agent path's bare-message shape."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    router = SwitchyardRouter(_make_engine())
    message = AIMessage(
        content="hi",
        response_metadata={_ROUTING_INFO_KEY: {"routed_model": "claude-4-5-haiku", "classifier_cost_usd": 0.002}},
    )
    result = LLMResult(generations=[[ChatGeneration(message=message)]])

    info = router.extract(result)

    assert info.routed_model == "claude-4-5-haiku"
    assert info.classifier_cost_usd == 0.002


def test_extract_empty_when_llm_result_not_stamped():
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    router = SwitchyardRouter(_make_engine())
    result = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="hi", response_metadata={}))]])

    assert router.extract(result).is_empty()


def test_extract_classifier_usage_reads_decision_from_context():
    router = SwitchyardRouter(_make_engine())
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        classifier=ClassifierCall(model="gpt-5.6-luna", input_tokens=120, output_tokens=40, cost_usd=0.0009),
    )
    ctx = CallContext(run_id="r1", decision=decision)
    usage = router.extract_classifier_usage(ctx)
    assert usage is not None
    assert usage.provider == "switchyard"
    assert usage.input_tokens == 120
    assert usage.output_tokens == 40
    assert usage.cost_usd == 0.0009
    assert usage.model == "gpt-5.6-luna"


def test_extract_classifier_usage_none_when_classifier_not_used():
    router = SwitchyardRouter(_make_engine())
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        classifier=None,
    )
    ctx = CallContext(run_id="r1", decision=decision)
    assert router.extract_classifier_usage(ctx) is None


def test_extract_classifier_usage_none_when_no_decision_in_context():
    router = SwitchyardRouter(_make_engine())
    assert router.extract_classifier_usage(CallContext(run_id="r1")) is None


def test_build_switchyard_routing_meta_maps_decision_fields_to_headers():
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        classifier=ClassifierCall(model="gpt-5.6-luna", cost_usd=0.0009),
    )
    meta = build_switchyard_routing_meta(decision, capable_model="claude-4-6-sonnet")
    headers = meta.to_headers()
    assert headers["x-codemie-routed-model"] == "claude-4-5-haiku"
    assert headers["x-codemie-requested-model"] == "claude-4-6-sonnet"
    assert headers["x-codemie-routing-tier"] == "efficient"
    assert headers["x-codemie-routing-classifier-model"] == "gpt-5.6-luna"
    assert headers["x-codemie-routing-classifier-cost-usd"] == "0.0009"


def test_build_routing_meta_returns_full_header_set():
    """SwitchyardRouter.build_routing_meta reads capable_model off its own engine — the
    proxy path never needs to know that field exists on the engine, not on RoutingDecision."""
    router = SwitchyardRouter(_make_engine())
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        classifier=ClassifierCall(cost_usd=0.0009),
    )
    headers = router.build_routing_meta(decision)
    assert headers["x-codemie-requested-model"] == "claude-4-6-sonnet"
    assert headers["x-codemie-routing-tier"] == "efficient"
