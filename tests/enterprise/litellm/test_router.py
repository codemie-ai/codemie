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

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from codemie.core.router import CallContext, NULL_ROUTER
from codemie.enterprise.litellm.router import _LITELLM_COMPLEXITY_ROUTER


def test_litellm_complexity_router_name_distinct_from_null_router():
    assert _LITELLM_COMPLEXITY_ROUTER.name == "litellm_complexity"
    assert NULL_ROUTER.name == "none"
    assert not isinstance(_LITELLM_COMPLEXITY_ROUTER, type(NULL_ROUTER))


@pytest.mark.asyncio
async def test_litellm_router_decide_is_always_none():
    assert await _LITELLM_COMPLEXITY_ROUTER.decide([{"role": "user", "content": "hi"}]) is None


def test_litellm_router_extract_reads_litellm_headers():
    message = MagicMock(spec=AIMessage)
    message.response_metadata = {"headers": {"x-litellm-router-routed-model": "claude-haiku-4-5"}}
    info = _LITELLM_COMPLEXITY_ROUTER.extract(message)
    assert info.routed_model == "claude-haiku-4-5"


def test_litellm_router_extract_empty_when_no_headers():
    message = MagicMock(spec=AIMessage)
    message.response_metadata = {}
    assert _LITELLM_COMPLEXITY_ROUTER.extract(message).is_empty()


def test_litellm_router_extract_classifier_usage_reads_litellm_headers():
    ctx = CallContext(
        run_id="r1",
        headers={
            "x-litellm-classifier-prompt-tokens": "120",
            "x-litellm-classifier-completion-tokens": "40",
            "x-litellm-classifier-cost": "0.0009",
            "x-litellm-router-classifier-model": "gpt-5.6-luna",
        },
    )
    usage = _LITELLM_COMPLEXITY_ROUTER.extract_classifier_usage(ctx)
    assert usage is not None
    assert usage.provider == "litellm_complexity"
    assert usage.input_tokens == 120
    assert usage.output_tokens == 40
    assert usage.cost_usd == 0.0009
    assert usage.model == "gpt-5.6-luna"


def test_litellm_router_extract_classifier_usage_none_when_no_headers():
    assert _LITELLM_COMPLEXITY_ROUTER.extract_classifier_usage(CallContext(run_id="r1")) is None
    assert _LITELLM_COMPLEXITY_ROUTER.extract_classifier_usage(CallContext(run_id="r1", headers={})) is None


def test_build_chat_model_wraps_single_candidate_in_router_chat_model(monkeypatch):
    """Unlike the Router default (empty candidates -> raw client), LiteLLMRouter always wraps
    in RouterChatModel — with exactly one candidate, model_name itself — so the post-call
    extract() stamp (RouterChatModel._agenerate) runs through the one canonical path every
    router uses, instead of a bespoke metadata channel just for this mechanism."""
    from codemie.core.router_chat_model import LLMParams, RouterChatModel

    sentinel = object()
    called_with = {}

    def fake_get_llm_by_credentials(**kwargs):
        called_with.update(kwargs)
        return sentinel

    monkeypatch.setattr("codemie.core.dependecies.get_llm_by_credentials", fake_get_llm_by_credentials)

    result = _LITELLM_COMPLEXITY_ROUTER.build_chat_model(
        model_name="auto-router-1", request_id="req-1", llm_params=LLMParams(temperature=0.2, top_p=None)
    )

    assert isinstance(result, RouterChatModel)
    assert result.router is _LITELLM_COMPLEXITY_ROUTER
    assert result.candidates == {"auto-router-1": sentinel}
    assert result.default_model == "auto-router-1"
    assert called_with == {
        "llm_model": "auto-router-1",
        "request_id": "req-1",
        "temperature": 0.2,
        "top_p": None,
    }
