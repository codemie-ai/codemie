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

from codemie.core.router import CallContext
from codemie.enterprise.litellm.router import LiteLLMRouter


def _router(name: str = "gpt-smart-router", counterfactual_model: str | None = None) -> LiteLLMRouter:
    return LiteLLMRouter(name, counterfactual_model=counterfactual_model)


def test_litellm_router_name_is_its_own_resolved_alias():
    router = _router(name="gpt-smart-router")
    assert router.name == "gpt-smart-router"
    assert router.routing_family == "litellm"


@pytest.mark.asyncio
async def test_litellm_router_decide_is_always_none():
    assert await _router().decide([{"role": "user", "content": "hi"}]) is None


def test_candidate_models_returns_its_own_alias_as_sole_candidate():
    """LiteLLM's auto-router alias IS the deployable model — there are no other concrete
    deployments to choose between (unlike Switchyard's capable/efficient pair), so
    build_chat_model_for's generic candidate-building logic must still wrap it in
    RouterChatModel with exactly one candidate: itself."""
    router = _router(name="gpt-smart-router")
    assert router.candidate_models() == ("gpt-smart-router",)


def test_counterfactual_model_set_at_construction():
    router = _router(counterfactual_model="gpt-5.6-terra-2026-07-09")
    assert router.counterfactual_model == "gpt-5.6-terra-2026-07-09"


def test_routing_info_ignores_decision_and_reads_header_maps():
    """decision is never consulted (this router's decide() always returns None — the
    generic caller passes whatever it has anyway, unconditionally)."""
    router = _router(counterfactual_model="gpt-5.6-terra-2026-07-09")
    header_maps = [{"x-litellm-router-routed-model": "claude-haiku-4-5"}]
    info = router.routing_info(object(), header_maps)  # a decision-shaped sentinel, never read
    assert info.routed_model == "claude-haiku-4-5"
    assert info.counterfactual_model == "gpt-5.6-terra-2026-07-09"


def test_routing_info_merges_multiple_header_maps():
    router = _router()
    header_maps = [
        {"x-litellm-router-routed-model": "claude-haiku-4-5"},
        {"x-litellm-classifier-cost": "0.0009"},
    ]
    info = router.routing_info(None, header_maps)
    assert info.routed_model == "claude-haiku-4-5"
    assert info.classifier_cost_usd == 0.0009


def test_routing_info_never_empty_even_with_no_header_maps():
    """requested_model=self.router_name is reported unconditionally (see routing_info()'s own
    docstring) — every call to a declared LiteLLM auto-router alias is routed by LiteLLM and
    comes back with routing headers, so there is nothing to guard for here."""
    info = _router(name="gpt-smart-router").routing_info(None, [])
    assert not info.is_empty()
    assert info.requested_model == "gpt-smart-router"


def test_routing_info_counterfactual_present_even_with_no_header_maps():
    """counterfactual_model is router-owned state, not parsed from headers — it survives
    even when there is nothing else to report."""
    router = _router(counterfactual_model="gpt-5.6-terra-2026-07-09")
    info = router.routing_info(None, [])
    assert info.counterfactual_model == "gpt-5.6-terra-2026-07-09"
    assert info.routed_model is None


def test_extract_classifier_usage_reads_litellm_headers():
    router = _router()
    ctx = CallContext(
        run_id="r1",
        headers={
            "x-litellm-classifier-prompt-tokens": "120",
            "x-litellm-classifier-completion-tokens": "40",
            "x-litellm-classifier-cost": "0.0009",
            "x-litellm-router-classifier-model": "gpt-5.6-luna",
        },
    )
    usage = router.extract_classifier_usage(ctx)
    assert usage is not None
    assert usage.provider == "gpt-smart-router"
    assert usage.input_tokens == 120
    assert usage.output_tokens == 40
    assert usage.cost_usd == 0.0009
    assert usage.model == "gpt-5.6-luna"


def test_extract_classifier_usage_none_when_no_headers():
    router = _router()
    assert router.extract_classifier_usage(CallContext(run_id="r1")) is None
    assert router.extract_classifier_usage(CallContext(run_id="r1", headers={})) is None
