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

"""End-to-end tests for ProxySwitchyardRouter.pick_model() against the REAL, installed
switchyard.libsy native package (nemo-switchyard) — no mocking of libsy.algorithms.stage_router
or libsy.Algorithm.run().

test_engine.py's pick_model tests mock the stage_router seam by design (Service Isolation
testing policy), so they never actually call into the native package. That is exactly why
EPMCDME-14083 (stage_router()'s positional-argument signature change, and the removal of the
Step/ModelCall/LlmResponse/RoutingOutcome streaming API, in nemo-switchyard 0.2.0) shipped to
main undetected. These tests exist specifically to close that gap — the only thing mocked here
is litellm.acompletion, the external LLM network boundary; libsy itself runs for real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from codemie.configs.llm_config import SwitchyardTuning
from codemie.enterprise.switchyard.engine import ProxySwitchyardRouter, RoutingTier


@pytest.mark.asyncio
async def test_pick_model_signal_mode_end_to_end_against_real_stage_router():
    """A single plain user turn, no tool calls, has no ToolSignals for the algorithm to
    escalate on — with picker="efficient_first" and the default signal_threshold=0.0, the
    FallThrough cascade always settles on efficient for this shape of request."""
    router = ProxySwitchyardRouter(
        capable_model="capable-model",
        efficient_model="efficient-model",
        routing_mode="signal",
        router_name="switchyard-auto",
        capable_model_deployment_name="capable-dep",
        efficient_model_deployment_name="efficient-dep",
        tuning=SwitchyardTuning(),
    )

    decision = await router.pick_model([{"role": "user", "content": "hi, just chatting"}])

    assert decision is not None
    assert decision.model == "efficient-dep"
    assert decision.tier == RoutingTier.EFFICIENT
    assert decision.decision_source == "heuristic"
    assert decision.routing_family == "switchyard"
    assert decision.classifier is None


@pytest.mark.asyncio
async def test_pick_model_classifier_mode_end_to_end_against_real_stage_router():
    """A confidence_threshold of 0.99 makes signal-only scoring inconclusive, forcing the real
    stage_router algorithm to fall through to the LlmFallback judge target — exercising
    _build_classifier's LlmFallback/TaskClassifierConfig/LlmTarget wiring for real. Only the
    external LLM call (litellm.acompletion) is stubbed; libsy drives the whole decision."""
    router = ProxySwitchyardRouter(
        capable_model="capable-model",
        efficient_model="efficient-model",
        routing_mode="classifier",
        router_name="switchyard-auto",
        capable_model_deployment_name="capable-dep",
        efficient_model_deployment_name="efficient-dep",
        tuning=SwitchyardTuning(
            classifier_model="judge-model",
            classifier_threshold=0.99,
            classifier_base_threshold=0.5,
        ),
    )

    fake_message = AsyncMock(content='{"target":"efficient"}')
    fake_response = AsyncMock(choices=[AsyncMock(message=fake_message)], usage=None)

    with patch("litellm.acompletion", AsyncMock(return_value=fake_response)) as mock_acompletion:
        decision = await router.pick_model([{"role": "user", "content": "classify this for me please"}])

    mock_acompletion.assert_awaited_once()
    assert mock_acompletion.await_args is not None
    assert mock_acompletion.await_args.kwargs["model"] == "openai/judge-model"

    assert decision is not None
    assert decision.model == "efficient-dep"
    assert decision.tier == RoutingTier.EFFICIENT
    assert decision.decision_source == "llm_classifier"
    assert decision.routing_family == "switchyard"
    assert decision.classifier is not None
    assert decision.classifier.model == "judge-model"
