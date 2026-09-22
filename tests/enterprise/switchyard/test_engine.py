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

"""Live-catalog defense-in-depth check for router-on-router configurations — the request-time
counterpart to the static test_switchyard_capable_is_litellm_router_skipped /
test_switchyard_efficient_is_litellm_router_skipped tests in tests/codemie/configs/test_llm_config.py.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs.llm_config import (
    LiteLLMRouterConfig,
    LLMModel,
    LLMRouter,
    RoutingMode,
    SwitchyardConfig,
    SwitchyardTuning,
)
from codemie.enterprise.switchyard.engine import ProxySwitchyardRouter, RoutingTier, get_proxy_switchyard_router
from codemie.enterprise.switchyard.llm_clients import _ClassifierUsage

_ROUTER_NAME = "cap-switchyard-eff-signal"


def _llm_router(capable_model: str = "cap", efficient_model: str = "eff") -> LLMRouter:
    return LLMRouter(
        base_name=_ROUTER_NAME,
        switchyard=SwitchyardConfig(
            capable_model=capable_model,
            efficient_model=efficient_model,
            mode=RoutingMode.SIGNAL,
            tuning=SwitchyardTuning(),
        ),
    )


def test_get_proxy_switchyard_router_rejects_capable_declared_as_litellm_router():
    with (
        patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", True),
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
    ):
        mock_service.get_llm_routers.return_value = [_llm_router()]
        mock_service.is_router_model.return_value = False
        mock_service.get_model_details.return_value = LLMModel(
            base_name="cap", deployment_name="cap", enabled=True, litellm_router=LiteLLMRouterConfig()
        )
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is None


def test_get_proxy_switchyard_router_rejects_efficient_that_is_a_switchyard_alias():
    with (
        patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", True),
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
    ):
        mock_service.get_llm_routers.return_value = [_llm_router()]
        # "cap" resolves cleanly; "eff" is itself a configured Switchyard router base_name.
        mock_service.is_router_model.side_effect = lambda name: name == "eff"
        mock_service.get_model_details.return_value = None
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is None


def test_get_proxy_switchyard_router_proceeds_when_neither_target_is_a_router():
    with (
        patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", True),
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
    ):
        mock_service.get_llm_routers.return_value = [_llm_router()]
        mock_service.is_router_model.return_value = False
        mock_service.get_model_details.return_value = LLMModel(base_name="cap", deployment_name="cap", enabled=True)
        mock_service.get_model_deployment_name.side_effect = lambda name: {"cap": "cap-dep", "eff": "eff-dep"}[name]
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is not None
    assert result.capable_model == "cap"
    assert result.efficient_model == "eff"
    assert mock_service.get_model_deployment_name.call_count == 2


def test_get_proxy_switchyard_router_rejects_target_not_in_catalog():
    """A capable/efficient model that no longer resolves in the live catalog (renamed, removed,
    or a typo) must reject the setup outright instead of silently falling back to the literal
    base_name as a deployment name."""
    with (
        patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", True),
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
    ):
        mock_service.get_llm_routers.return_value = [_llm_router()]
        mock_service.is_router_model.return_value = False
        mock_service.get_model_details.return_value = LLMModel(base_name="cap", deployment_name="cap", enabled=True)
        mock_service.get_model_deployment_name.return_value = None
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is None


def test_get_proxy_switchyard_router_returns_none_when_disabled():
    with patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", False):
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is None


def test_get_proxy_switchyard_router_returns_none_when_not_configured():
    with (
        patch("codemie.enterprise.switchyard.engine.config.SWITCHYARD_ENABLED", True),
        patch("codemie.service.llm_service.llm_service.llm_service") as mock_service,
    ):
        mock_service.get_llm_routers.return_value = []
        result = get_proxy_switchyard_router(_ROUTER_NAME)
    assert result is None


def _make_switchyard_router() -> ProxySwitchyardRouter:
    return ProxySwitchyardRouter(
        capable_model="cap",
        efficient_model="eff",
        routing_mode="signal",
        router_name=_ROUTER_NAME,
        capable_model_deployment_name="cap-dep",
        efficient_model_deployment_name="eff-dep",
        tuning=SwitchyardTuning(),
    )


def test_decision_source_for_returns_llm_classifier_when_classifier_used():
    from codemie.core.router import ClassifierCall
    from codemie.enterprise.switchyard.engine import _decision_source_for

    assert _decision_source_for(ClassifierCall(model="gpt-5.6-luna")) == "llm_classifier"


def test_decision_source_for_returns_heuristic_when_no_classifier():
    from codemie.enterprise.switchyard.engine import _decision_source_for

    assert _decision_source_for(None) == "heuristic"


def test_fallback_decision_sets_decision_source_to_reason():
    router = _make_switchyard_router()
    decision = router._fallback_decision("compaction")
    assert decision.model == "cap"
    assert decision.tier == RoutingTier.CAPABLE
    assert decision.decision_source == "compaction"
    assert decision.routing_family == "switchyard"


def test_fallback_decision_router_error_reason():
    router = _make_switchyard_router()
    decision = router._fallback_decision("router_error")
    assert decision.decision_source == "router_error"


def test_fallback_decision_no_decision_reason():
    router = _make_switchyard_router()
    decision = router._fallback_decision("no_decision")
    assert decision.decision_source == "no_decision"


# --- pick_model ------------------------------------------------------------
#
# algorithm.run() (libsy.algorithms.stage_router's return value) is mocked at the
# `libsy.algorithms.stage_router` seam per Service Isolation testing policy: pick_model's own
# branch logic (compaction/error/no-decision fallbacks, tier resolution, classifier bookkeeping)
# is what's under test here, not the native routing heuristics themselves — those are exercised
# for real in ai-run/codemie's manual verification, not in this unit suite.


def _make_pick_model_router(
    *, routing_mode: str | None = "signal", classifier_model: str | None = None
) -> ProxySwitchyardRouter:
    return ProxySwitchyardRouter(
        capable_model="capable-model",
        efficient_model="efficient-model",
        routing_mode=routing_mode,
        router_name=_ROUTER_NAME,
        capable_model_deployment_name="capable-dep",
        efficient_model_deployment_name="efficient-dep",
        tuning=SwitchyardTuning(classifier_model=classifier_model),
    )


def _user_message(text: str = "hi") -> list[dict[str, object]]:
    return [{"role": "user", "content": text}]


def _mock_algorithm(trace: Sequence[Mapping[str, object]], response: Mapping[str, object] | None = None) -> MagicMock:
    """A stand-in for the libsy.Algorithm that stage_router() would normally return."""
    algorithm = MagicMock()
    algorithm.run = AsyncMock(return_value=(trace, response or {}))
    return algorithm


def _stage_router_patch():
    return patch("codemie.enterprise.switchyard.engine.libsy.algorithms.stage_router")


@pytest.mark.asyncio
async def test_pick_model_returns_none_when_routing_mode_is_none():
    router = _make_pick_model_router(routing_mode=None)
    with _stage_router_patch() as mock_stage_router:
        decision = await router.pick_model(_user_message())
    assert decision is None
    mock_stage_router.assert_not_called()


@pytest.mark.asyncio
async def test_pick_model_escalates_to_capable_on_recent_compaction():
    router = _make_pick_model_router()
    messages = _user_message("session is being continued from a previous conversation")
    with _stage_router_patch() as mock_stage_router:
        decision = await router.pick_model(messages)
    assert decision is not None
    assert decision.model == "capable-model"
    assert decision.tier == RoutingTier.CAPABLE
    assert decision.decision_source == "compaction"
    mock_stage_router.assert_not_called()


@pytest.mark.asyncio
async def test_pick_model_falls_back_to_router_error_when_algorithm_run_raises():
    router = _make_pick_model_router()
    algorithm = MagicMock()
    algorithm.run = AsyncMock(side_effect=RuntimeError("native routing failure"))
    with _stage_router_patch() as mock_stage_router:
        mock_stage_router.return_value = algorithm
        decision = await router.pick_model(_user_message())
    assert decision is not None
    assert decision.model == "capable-model"
    assert decision.tier == RoutingTier.CAPABLE
    assert decision.decision_source == "router_error"


@pytest.mark.parametrize(
    "trace",
    [
        pytest.param([], id="empty_trace"),
        pytest.param([{"reasoning": "no selection recorded"}], id="missing_selected_model_key"),
        pytest.param([{"selected_model": 123}], id="non_string_selected_model"),
    ],
)
@pytest.mark.asyncio
async def test_pick_model_falls_back_to_no_decision_when_trace_has_no_selection(
    trace: Sequence[Mapping[str, object]],
):
    router = _make_pick_model_router()
    with _stage_router_patch() as mock_stage_router:
        mock_stage_router.return_value = _mock_algorithm(trace)
        decision = await router.pick_model(_user_message())
    assert decision is not None
    assert decision.model == "capable-model"
    assert decision.tier == RoutingTier.CAPABLE
    assert decision.decision_source == "no_decision"


@pytest.mark.asyncio
async def test_pick_model_signal_mode_selects_efficient():
    router = _make_pick_model_router(routing_mode="signal")
    trace: list[dict[str, object]] = [
        {"reasoning": "fall-through selected efficient-model (confidence 0.000)", "selected_model": "efficient-model"}
    ]
    with _stage_router_patch() as mock_stage_router:
        mock_stage_router.return_value = _mock_algorithm(trace)
        decision = await router.pick_model(_user_message())
    assert decision is not None
    assert decision.model == "efficient-dep"
    assert decision.tier == RoutingTier.EFFICIENT
    assert decision.decision_source == "heuristic"
    assert decision.classifier is None


@pytest.mark.asyncio
async def test_pick_model_signal_mode_selects_capable():
    router = _make_pick_model_router(routing_mode="signal")
    trace: list[dict[str, object]] = [{"reasoning": "escalated to capable-model", "selected_model": "capable-model"}]
    with _stage_router_patch() as mock_stage_router:
        mock_stage_router.return_value = _mock_algorithm(trace)
        decision = await router.pick_model(_user_message())
    assert decision is not None
    assert decision.model == "capable-dep"
    assert decision.tier == RoutingTier.CAPABLE
    assert decision.decision_source == "heuristic"
    assert decision.classifier is None


@pytest.mark.asyncio
async def test_pick_model_invokes_stage_router_with_positional_llm_targets():
    """Regression test for EPMCDME-14083: nemo-switchyard 0.2.0's stage_router() requires
    capable_target/efficient_target as positional LlmTarget arguments (a TypeError otherwise)."""
    router = _make_pick_model_router(routing_mode="signal")
    trace: list[dict[str, object]] = [{"selected_model": "efficient-model"}]
    with _stage_router_patch() as mock_stage_router:
        mock_stage_router.return_value = _mock_algorithm(trace)
        await router.pick_model(_user_message())

    args, kwargs = mock_stage_router.call_args
    assert len(args) == 2, "capable_target/efficient_target must be positional, not keyword"
    assert args[0].name == "capable-model"
    assert args[1].name == "efficient-model"
    assert kwargs["picker"] == "efficient_first"
    assert kwargs["confidence_threshold"] == router.tuning.signal_threshold
    assert kwargs["recent_window"] == router.tuning.recent_window
    assert kwargs["classifier"] is None


@pytest.mark.asyncio
async def test_pick_model_classifier_mode_reports_llm_classifier_when_classifier_used():
    router = _make_pick_model_router(routing_mode="classifier", classifier_model="judge-model")
    trace: list[dict[str, object]] = [{"selected_model": "efficient-model"}]

    mock_classifier_client = MagicMock()
    mock_classifier_client.usage = _ClassifierUsage(
        called=True,
        classifier_used=True,
        input_tokens=120,
        output_tokens=40,
        cached_tokens=10,
        cache_creation_tokens=5,
        cost_usd=0.0021,
    )

    with (
        _stage_router_patch() as mock_stage_router,
        patch.object(router, "_build_classifier", return_value=(mock_classifier_client, MagicMock())),
    ):
        mock_stage_router.return_value = _mock_algorithm(trace)
        decision = await router.pick_model(_user_message())

    assert decision is not None
    assert decision.model == "efficient-dep"
    assert decision.decision_source == "llm_classifier"
    assert decision.classifier is not None
    assert decision.classifier.model == "judge-model"
    assert decision.classifier.input_tokens == 120
    assert decision.classifier.output_tokens == 40
    assert decision.classifier.cached_tokens == 10
    assert decision.classifier.cache_creation_tokens == 5
    assert decision.classifier.cost_usd == 0.0021


@pytest.mark.asyncio
async def test_pick_model_classifier_mode_reports_heuristic_when_classifier_not_invoked():
    """routing_mode == "classifier" attaches a classifier fallback, but stage_router's own
    signal scoring can still settle the decision without ever calling the judge — decision_source
    must reflect what actually happened for THIS call, not the router's static configuration."""
    router = _make_pick_model_router(routing_mode="classifier", classifier_model="judge-model")
    trace: list[dict[str, object]] = [{"selected_model": "capable-model"}]

    mock_classifier_client = MagicMock()
    mock_classifier_client.usage = _ClassifierUsage(called=False, classifier_used=False)

    with (
        _stage_router_patch() as mock_stage_router,
        patch.object(router, "_build_classifier", return_value=(mock_classifier_client, MagicMock())),
    ):
        mock_stage_router.return_value = _mock_algorithm(trace)
        decision = await router.pick_model(_user_message())

    assert decision is not None
    assert decision.model == "capable-dep"
    assert decision.decision_source == "heuristic"
    assert decision.classifier is None


@pytest.mark.asyncio
async def test_pick_model_classifier_mode_without_classifier_model_configured():
    """RoutingMode.CLASSIFIER with no tuning.classifier_model can never invoke a classifier
    (see test_llm_config.py's static counterpart) — _build_classifier must degrade to (None,
    None) so stage_router runs signal-only, not crash on a missing judge target."""
    router = _make_pick_model_router(routing_mode="classifier", classifier_model=None)
    trace: list[dict[str, object]] = [{"selected_model": "efficient-model"}]

    with _stage_router_patch() as mock_stage_router:
        mock_stage_router.return_value = _mock_algorithm(trace)
        decision = await router.pick_model(_user_message())

    _, kwargs = mock_stage_router.call_args
    assert kwargs["classifier"] is None
    assert decision is not None
    assert decision.decision_source == "heuristic"
    assert decision.classifier is None
