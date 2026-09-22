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

from codemie.core.router import CallContext, ClassifierCall, RoutingDecision
from codemie.enterprise.switchyard.engine import RoutingTier
from codemie.enterprise.switchyard.router import SwitchyardRouter, normalize_switchyard_tier


def _make_engine(*, routing_mode="signal", **decide_kwargs):
    engine = MagicMock()
    engine.router_name = "switchyard-auto"
    engine.capable_model = "claude-4-6-sonnet"
    engine.efficient_model = "claude-4-5-haiku"
    engine.capable_model_deployment_name = "us.anthropic.claude-4-6-sonnet"
    engine.efficient_model_deployment_name = "us.anthropic.claude-4-5-haiku"
    engine.routing_mode = routing_mode
    engine.pick_model = AsyncMock(
        return_value=RoutingDecision(
            model="us.anthropic.claude-4-5-haiku",
            tier=RoutingTier.EFFICIENT,
            decision_source="heuristic",
            routing_family="switchyard",
            **decide_kwargs,
        )
    )
    return engine


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("efficient", "simple"), ("capable", "complex"), (" NewTier ", "newtier"), (None, None)],
)
def test_normalize_switchyard_tier_uses_switchyard_vocabulary(raw, expected):
    assert normalize_switchyard_tier(raw) == expected


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


def test_counterfactual_model_is_capable_model_set_at_construction():
    router = SwitchyardRouter(_make_engine())
    assert router.counterfactual_model == "claude-4-6-sonnet"


def test_routing_info_returns_empty_when_no_decision():
    """The decide()-failure edge case: nothing is ever reflected in LiteLLM's response
    headers for Switchyard, so header_maps is correctly never even inspected."""
    router = SwitchyardRouter(_make_engine())
    header_maps_spy = MagicMock()
    header_maps_spy.__iter__ = MagicMock(side_effect=AssertionError("header_maps must not be iterated"))
    assert router.routing_info(None, header_maps_spy).is_empty()


def test_extract_classifier_usage_reads_decision_from_context():
    router = SwitchyardRouter(_make_engine())
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        decision_source="llm_classifier",
        routing_family="switchyard",
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
        decision_source="heuristic",
        routing_family="switchyard",
        classifier=None,
    )
    ctx = CallContext(run_id="r1", decision=decision)
    assert router.extract_classifier_usage(ctx) is None


def test_extract_classifier_usage_none_when_no_decision_in_context():
    router = SwitchyardRouter(_make_engine())
    assert router.extract_classifier_usage(CallContext(run_id="r1")) is None


def test_router_routing_family_is_switchyard():
    assert SwitchyardRouter(_make_engine()).routing_family == "switchyard"


def test_routing_info_populates_typed_routing_dimensions():
    """The typed routing dimensions consumed by routing analytics (requested_model, tier,
    classifier token counts) must be on RoutingInfo itself — routing_call_usage reads the
    typed fields directly, and the client-facing headers (see core/proxy_routing_headers.py)
    are built from these same typed fields, not a separate passthrough bag.

    requested_model reports the router's own alias (router_name) — the same kind of value
    LiteLLMRouter reports — not the capable-model name, which is a distinct pricing-baseline
    concept surfaced separately as counterfactual_model."""
    router = SwitchyardRouter(_make_engine())
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        decision_source="llm_classifier",
        routing_family="switchyard",
        classifier=ClassifierCall(
            model="gpt-5.6-luna", input_tokens=120, output_tokens=40, cached_tokens=15, cost_usd=0.0009
        ),
    )

    info = router.routing_info(decision, ())

    assert info.routed_model == "claude-4-5-haiku"
    assert info.requested_model == "switchyard-auto"
    assert info.counterfactual_model == "claude-4-6-sonnet"
    assert info.tier == "simple"
    assert info.routing_tier_raw == RoutingTier.EFFICIENT
    assert info.classifier_input_tokens == 120
    assert info.classifier_output_tokens == 40
    assert info.classifier_cached_tokens == 15
    assert info.classifier_cost_usd == 0.0009
    assert info.decision_source == "llm-classifier"
    assert info.routing_family == "switchyard"
    assert info.router_type == "stage"  # _make_engine defaults to signal mode
    assert info.classifier_model == "gpt-5.6-luna"


def test_routing_info_router_type_is_composite_in_classifier_mode():
    """router_type reflects the engine's own routing_mode config ("stage" vs "composite"),
    independent of decision_source (which reflects whether the classifier fired for THIS
    call) — a classifier-mode engine reports "composite" even on a decision it settled via
    signals alone."""
    router = SwitchyardRouter(_make_engine(routing_mode="classifier"))
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        decision_source="heuristic",
        routing_family="switchyard",
        classifier=None,
    )

    info = router.routing_info(decision, ())

    assert info.router_type == "composite"


def test_routing_info_without_classifier_leaves_token_fields_none():
    router = SwitchyardRouter(_make_engine())
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier=RoutingTier.EFFICIENT,
        decision_source="heuristic",
        routing_family="switchyard",
        classifier=None,
    )

    info = router.routing_info(decision, ())

    assert info.tier == "simple"
    assert info.routing_tier_raw == RoutingTier.EFFICIENT
    assert info.counterfactual_model == "claude-4-6-sonnet"
    assert info.classifier_input_tokens is None
    assert info.classifier_output_tokens is None
    assert info.classifier_cached_tokens is None
    assert info.classifier_cost_usd is None
    assert info.decision_source == "heuristic"
    assert info.routing_family == "switchyard"
