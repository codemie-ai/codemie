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

from __future__ import annotations

import dataclasses

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from codemie.core.router_chat_model import _iter_header_maps
from codemie.enterprise.litellm.litellm_router_headers import (
    LITELLM_ROUTER_FIELD_TO_HEADER,
    LiteLLMRouterHeaders,
)
from codemie.enterprise.litellm.router import LiteLLMRouter, normalize_litellm_tier, routing_info_from_headers


def _llm_result_with_headers(headers: dict) -> LLMResult:
    msg = AIMessage(content="", response_metadata={"headers": headers})
    gen = ChatGeneration(message=msg, generation_info={"headers": headers})
    return LLMResult(generations=[[gen]])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("simple", "simple"),
        ("efficient", "efficient"),
        ("medium", "medium"),
        ("middle", "middle"),
        ("complex", "complex"),
        ("capable", "capable"),
        ("reasoning", "reasoning"),
        (" NewTier ", "newtier"),
        (None, None),
    ],
)
def test_normalize_litellm_tier_uses_litellm_vocabulary(raw, expected):
    assert normalize_litellm_tier(raw) == expected


def test_extracts_routed_model_and_classifier_cost_from_headers():
    # _llm_result_with_headers places the SAME dict in both generation_info and
    # response_metadata, but _iter_header_maps only ever yields ONE of them per
    # generation: generation_info wins when present, and response_metadata is never
    # even inspected for that generation. So this dict is only merged once, not twice
    # deduplicated afterward — classifier_cost_usd should be 0.0009, not 0.0018.
    result = _llm_result_with_headers(
        {
            "x-litellm-router-routed-model": "claude-4-5-haiku",
            "x-litellm-classifier-cost": "0.0009",
        }
    )
    router = LiteLLMRouter(name="test")
    info = router.routing_info(None, _iter_header_maps(result))
    assert info.routed_model == "claude-4-5-haiku"
    assert info.classifier_cost_usd == pytest.approx(0.0009)


def test_routing_info_prefers_generation_info_over_corrupted_response_metadata():
    """Assistant responses must not merge duplicated/corrupted header copies."""
    clean_headers = {
        "x-litellm-router-routed-model": "gpt-5.6-luna-2026-07-09",
        "x-litellm-classifier-prompt-tokens": "700",
        "x-litellm-classifier-completion-tokens": "14",
        "x-litellm-classifier-total-tokens": "714",
    }
    corrupted_headers = {
        "x-litellm-router-routed-model": "gpt-5.6-luna-2026-07-09gpt-5.6-luna-2026-07-09",
        "x-litellm-classifier-prompt-tokens": "700700",
        "x-litellm-classifier-completion-tokens": "1414",
        "x-litellm-classifier-total-tokens": "714714",
    }
    result = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(content="", response_metadata={"headers": corrupted_headers}),
                    generation_info={"headers": clean_headers},
                )
            ]
        ]
    )

    info = LiteLLMRouter(name="test").routing_info(None, _iter_header_maps(result))

    assert info.routed_model == "gpt-5.6-luna-2026-07-09"
    assert info.classifier_input_tokens == 700
    assert info.classifier_output_tokens == 14
    assert info.classifier_total_tokens == 714


def test_routing_info_from_headers_empty_when_no_router_headers():
    """The pure header-parsing layer stays empty on an empty header map — unlike
    LiteLLMRouter.routing_info() itself, which always reports requested_model=self.router_name
    (see that method's own docstring) and is therefore never empty."""
    assert routing_info_from_headers({}).is_empty()


def test_routing_info_never_empty_even_with_no_router_headers():
    """Every call to a declared LiteLLM auto-router alias is routed by LiteLLM and comes back
    with routing headers — so requested_model=self.router_name is reported unconditionally,
    unlike the pure header parser (routing_info_from_headers) it wraps."""
    router = LiteLLMRouter(name="test")
    result = _llm_result_with_headers({})
    info = router.routing_info(None, _iter_header_maps(result))
    assert not info.is_empty()
    assert info.requested_model == "test"


def test_litellm_router_meta_from_headers_reads_known_fields():
    headers = {
        "x-litellm-router-routed-model": "claude-haiku-4-5",
        "x-litellm-router-tier": "efficient",
        "x-litellm-classifier-cost": "0.0009",
        "x-litellm-classifier-prompt-tokens": "120",
        "x-litellm-classifier-completion-tokens": "5",
    }
    meta = LiteLLMRouterHeaders.from_headers(headers)
    assert meta.routed_model == "claude-haiku-4-5"
    assert meta.tier == "efficient"
    assert meta.classifier_cost_usd == 0.0009
    assert meta.classifier_prompt_tokens == 120
    assert meta.classifier_completion_tokens == 5


def test_litellm_router_meta_from_headers_returns_empty_for_unknown():
    meta = LiteLLMRouterHeaders.from_headers({})
    assert meta.routed_model is None
    assert meta.classifier_cost_usd is None


def test_litellm_router_meta_from_headers_invalid_values_become_none():
    headers = {
        "x-litellm-classifier-prompt-tokens": "not-a-number",
        "x-litellm-classifier-cost": "invalid",
    }
    meta = LiteLLMRouterHeaders.from_headers(headers)
    assert meta.classifier_prompt_tokens is None
    assert meta.classifier_cost_usd is None


def test_field_to_header_keys_match_dataclass_fields():
    field_names = {f.name for f in dataclasses.fields(LiteLLMRouterHeaders)}
    assert set(LITELLM_ROUTER_FIELD_TO_HEADER.keys()) == field_names


def test_extracts_new_routing_fields_from_litellm_headers():
    """LiteLLMRouter.routing_info maps tier, cause→decision_source, score→confidence, tokens.

    Uses AIMessage (single header source) so additive fields are not doubled.
    """
    msg = AIMessage(
        content="",
        response_metadata={
            "headers": {
                "x-litellm-router-tier": "medium",
                "x-litellm-router-cause": "llm_classifier",
                "x-litellm-router-score": "0.87",
                "x-litellm-router-type": "complexity",
                "x-litellm-router-classifier-model": "claude-4-5-haiku",
                "x-litellm-classifier-prompt-tokens": "150",
                "x-litellm-classifier-completion-tokens": "45",
                "x-litellm-classifier-total-tokens": "195",
                "x-litellm-classifier-cost": "0.0",
            }
        },
    )
    info = LiteLLMRouter(name="test").routing_info(None, _iter_header_maps(msg))
    assert info.tier == "medium"
    assert info.routing_tier_raw == "medium"
    assert info.decision_source == "llm-classifier"
    assert info.routing_source == "judge"
    assert info.routing_family == "litellm"
    assert info.routing_cost_known is True
    assert info.confidence == pytest.approx(0.87)
    assert info.router_type == "complexity"
    assert info.classifier_model == "claude-4-5-haiku"
    assert info.classifier_input_tokens == 150
    assert info.classifier_output_tokens == 45
    assert info.classifier_total_tokens == 195


def test_litellm_routing_cost_is_unknown_when_classifier_cost_is_missing():
    msg = AIMessage(
        content="",
        response_metadata={"headers": {"x-litellm-router-tier": "capable"}},
    )

    info = LiteLLMRouter(name="test").routing_info(None, _iter_header_maps(msg))

    assert info.routing_family == "litellm"
    assert info.tier == "capable"
    assert info.routing_cost_known is False


def test_litellm_classifier_only_header_still_identifies_routing():
    msg = AIMessage(
        content="",
        response_metadata={"headers": {"x-litellm-classifier-cost": "0.001"}},
    )

    info = LiteLLMRouter(name="test").routing_info(None, _iter_header_maps(msg))

    assert info.routing_family == "litellm"
    assert info.routing_cost_known is True


def test_requested_model_is_router_owned_alias_not_the_wire_header():
    """requested_model always comes from self.router_name (the alias create_router() resolved
    this router for — router-owned state, same as counterfactual_model), never from LiteLLM's
    own x-litellm-router-model-name header — that header is read only as one of several
    has_routing_metadata presence signals (see routing_info_from_headers), not for its value."""
    msg = AIMessage(
        content="",
        response_metadata={
            "headers": {
                "x-litellm-router-routed-model": "gpt-5.6-luna-2026-07-09",
                "x-litellm-router-model-name": "a-different-name-litellm-reported",
            }
        },
    )

    info = LiteLLMRouter(name="gpt-smart-router").routing_info(None, _iter_header_maps(msg))

    assert info.routed_model == "gpt-5.6-luna-2026-07-09"
    assert info.requested_model == "gpt-smart-router"
