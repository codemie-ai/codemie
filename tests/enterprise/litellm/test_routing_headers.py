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

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from codemie.enterprise.litellm.litellm_router_meta import (
    LITELLM_ROUTER_FIELD_TO_HEADER,
    LiteLLMRouterMeta,
)
from codemie.enterprise.litellm.routing_headers import LiteLLMRouterExtractor


def _llm_result_with_headers(headers: dict) -> LLMResult:
    msg = AIMessage(content="", response_metadata={"headers": headers})
    gen = ChatGeneration(message=msg, generation_info={"headers": headers})
    return LLMResult(generations=[[gen]])


def test_extracts_routed_model_and_classifier_cost_from_headers():
    result = _llm_result_with_headers(
        {
            "x-litellm-router-routed-model": "claude-4-5-haiku",
            "x-litellm-classifier-cost": "0.0009",
        }
    )
    info = LiteLLMRouterExtractor().extract(result)
    assert info.routed_model == "claude-4-5-haiku"
    assert info.classifier_cost_usd == 0.0009


def test_empty_when_no_router_headers():
    assert LiteLLMRouterExtractor().extract(_llm_result_with_headers({})).is_empty()


def test_litellm_router_meta_from_headers_reads_known_fields():
    headers = {
        "x-litellm-router-routed-model": "claude-haiku-4-5",
        "x-litellm-router-tier": "efficient",
        "x-litellm-classifier-cost": "0.0009",
        "x-litellm-classifier-prompt-tokens": "120",
        "x-litellm-classifier-completion-tokens": "5",
    }
    meta = LiteLLMRouterMeta.from_headers(headers)
    assert meta.routed_model == "claude-haiku-4-5"
    assert meta.tier == "efficient"
    assert meta.classifier_cost_usd == 0.0009
    assert meta.classifier_prompt_tokens == 120
    assert meta.classifier_completion_tokens == 5


def test_litellm_router_meta_from_headers_returns_empty_for_unknown():
    meta = LiteLLMRouterMeta.from_headers({})
    assert meta.routed_model is None
    assert meta.classifier_cost_usd is None


def test_litellm_router_meta_from_headers_invalid_values_become_none():
    headers = {
        "x-litellm-classifier-prompt-tokens": "not-a-number",
        "x-litellm-classifier-cost": "invalid",
    }
    meta = LiteLLMRouterMeta.from_headers(headers)
    assert meta.classifier_prompt_tokens is None
    assert meta.classifier_cost_usd is None


def test_field_to_header_keys_match_dataclass_fields():
    field_names = {f.name for f in dataclasses.fields(LiteLLMRouterMeta)}
    assert set(LITELLM_ROUTER_FIELD_TO_HEADER.keys()) == field_names


def test_litellm_router_meta_to_headers_serialises_non_none_fields():
    meta = LiteLLMRouterMeta(tier="efficient", routed_model="claude-haiku-4-5", classifier_cost_usd=0.0009)
    headers = meta.to_headers()
    assert headers["x-litellm-router-tier"] == "efficient"
    assert headers["x-litellm-router-routed-model"] == "claude-haiku-4-5"
    assert headers["x-litellm-classifier-cost"] == "0.0009"
    assert "x-litellm-router-cause" not in headers  # None field omitted


def test_litellm_router_meta_from_routing_decision():
    decision = {"tier": "capable", "routed_model": "claude-sonnet-5", "cause": "high_complexity"}
    meta = LiteLLMRouterMeta.from_routing_decision(decision)
    assert meta.tier == "capable"
    assert meta.routed_model == "claude-sonnet-5"
    assert meta.cause == "high_complexity"
