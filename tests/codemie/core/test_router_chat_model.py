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

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from codemie.core.router_chat_model import RouterChatModel, _CleanGenerationInfoCapture, _ROUTING_CTX_KEY
from codemie.core.router import ClassifierCall, RoutingDecision
from codemie.core.routing_info import RoutingInfo, _ROUTING_INFO_KEY


def _make_router(decision):
    router = MagicMock()
    router.name = "switchyard"
    router.decide = AsyncMock(return_value=decision)
    # Mirrors Router.routing_info's real default (see core/router.py) so RouterChatModel's own
    # stamping behavior can be asserted without re-testing that formula here — it's covered on
    # its own in tests/codemie/core/test_router.py.
    router.routing_info = MagicMock(
        side_effect=lambda d: RoutingInfo(
            routed_model=d.model,
            classifier_cost_usd=d.classifier.cost_usd if d.classifier else None,
            routing_family=d.routing_family,
        )
    )
    # Default: no post-hoc signal in the response either (the decide()-capable-router case —
    # a real LiteLLMRouter-style router that overrides this is exercised in its own test below).
    router.extract = MagicMock(return_value=RoutingInfo())
    return router


def _make_candidate(name: str, reply: str):
    model = MagicMock()
    model.model_name = name

    async def ainvoke(messages, stop=None, **kwargs):
        return AIMessage(content=reply, response_metadata={})

    model.ainvoke = AsyncMock(side_effect=ainvoke)
    return model


@pytest.mark.asyncio
async def test_agenerate_dispatches_to_decided_model():
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier="efficient",
        decision_source="llm_classifier",
        routing_family="switchyard",
    )
    router = _make_router(decision)
    haiku = _make_candidate("claude-4-5-haiku", "efficient reply")
    sonnet = _make_candidate("claude-4-6-sonnet", "capable reply")
    chat_model = RouterChatModel(
        router=router,
        candidates={"claude-4-5-haiku": haiku, "claude-4-6-sonnet": sonnet},
        default_model="claude-4-6-sonnet",
    )

    result = await chat_model._agenerate([AIMessage(content="hi")])

    haiku.ainvoke.assert_awaited_once()
    sonnet.ainvoke.assert_not_awaited()
    assert result.generations[0].message.content == "efficient reply"


@pytest.mark.asyncio
async def test_agenerate_falls_back_to_default_model_when_no_decision():
    router = _make_router(decision=None)
    sonnet = _make_candidate("claude-4-6-sonnet", "default reply")
    chat_model = RouterChatModel(
        router=router,
        candidates={"claude-4-6-sonnet": sonnet},
        default_model="claude-4-6-sonnet",
    )

    result = await chat_model._agenerate([AIMessage(content="hi")])

    sonnet.ainvoke.assert_awaited_once()
    assert result.generations[0].message.content == "default reply"


@pytest.mark.asyncio
async def test_agenerate_stashes_router_and_decision_in_config_metadata():
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier="efficient",
        decision_source="llm_classifier",
        routing_family="switchyard",
    )
    router = _make_router(decision)
    haiku = _make_candidate("claude-4-5-haiku", "reply")
    chat_model = RouterChatModel(
        router=router, candidates={"claude-4-5-haiku": haiku}, default_model="claude-4-5-haiku"
    )

    await chat_model._agenerate([AIMessage(content="hi")])

    call_kwargs = haiku.ainvoke.await_args.kwargs
    stashed = call_kwargs["config"]["metadata"][_ROUTING_CTX_KEY]
    assert stashed == (router, decision)


@pytest.mark.asyncio
async def test_agenerate_stamps_canonical_routing_info_on_response():
    decision = RoutingDecision(
        model="claude-4-5-haiku",
        tier="efficient",
        decision_source="llm_classifier",
        routing_family="switchyard",
        classifier=ClassifierCall(cost_usd=0.001),
    )
    router = _make_router(decision)
    haiku = _make_candidate("claude-4-5-haiku", "reply")
    chat_model = RouterChatModel(
        router=router, candidates={"claude-4-5-haiku": haiku}, default_model="claude-4-5-haiku"
    )

    result = await chat_model._agenerate([AIMessage(content="hi")])

    stamped = result.generations[0].message.response_metadata[_ROUTING_INFO_KEY]
    assert (
        stamped
        == RoutingInfo(
            routed_model="claude-4-5-haiku", classifier_cost_usd=0.001, routing_family="switchyard"
        ).model_dump()
    )


@pytest.mark.asyncio
async def test_agenerate_stashes_router_even_when_decide_returns_none():
    """Router is always stashed — not gated on decision being non-None."""
    router = _make_router(decision=None)
    sonnet = _make_candidate("claude-4-6-sonnet", "default reply")
    chat_model = RouterChatModel(
        router=router,
        candidates={"claude-4-6-sonnet": sonnet},
        default_model="claude-4-6-sonnet",
    )

    await chat_model._agenerate([AIMessage(content="hi")])

    call_kwargs = sonnet.ainvoke.await_args.kwargs
    stashed = call_kwargs["config"]["metadata"][_ROUTING_CTX_KEY]
    assert stashed == (router, None)


@pytest.mark.asyncio
async def test_agenerate_does_not_stamp_routing_info_when_no_decision_and_extract_empty():
    router = _make_router(decision=None)
    sonnet = _make_candidate("claude-4-6-sonnet", "default reply")
    chat_model = RouterChatModel(
        router=router,
        candidates={"claude-4-6-sonnet": sonnet},
        default_model="claude-4-6-sonnet",
    )

    result = await chat_model._agenerate([AIMessage(content="hi")])

    router.extract.assert_called_once_with(result.generations[0].message)
    assert _ROUTING_INFO_KEY not in result.generations[0].message.response_metadata


@pytest.mark.asyncio
async def test_agenerate_stamps_extracted_routing_info_when_no_decision():
    """decide()-less routers (LiteLLMRouter) never produce a RoutingDecision — their only
    channel for the routed model is reading it back out of the response itself, via their own
    extract(). RouterChatModel must stamp whatever extract() finds, the same as it would stamp
    a decision-based routing_info()."""
    router = _make_router(decision=None)
    router.extract = MagicMock(return_value=RoutingInfo(routed_model="claude-haiku-4-5"))
    sonnet = _make_candidate("claude-4-6-sonnet", "default reply")
    chat_model = RouterChatModel(
        router=router,
        candidates={"claude-4-6-sonnet": sonnet},
        default_model="claude-4-6-sonnet",
    )

    result = await chat_model._agenerate([AIMessage(content="hi")])

    stamped = result.generations[0].message.response_metadata[_ROUTING_INFO_KEY]
    assert stamped == RoutingInfo(routed_model="claude-haiku-4-5").model_dump()


@pytest.mark.asyncio
async def test_agenerate_does_not_attach_capture_callback_when_decision_present():
    """The capture callback exists purely to feed extract() for decide()-less routers — a
    decision-bearing call (Switchyard) never calls extract() at all, so attaching it would be
    pure overhead."""
    decision = RoutingDecision(
        model="claude-4-5-haiku", tier="efficient", decision_source="llm_classifier", routing_family="switchyard"
    )
    router = _make_router(decision)
    haiku = _make_candidate("claude-4-5-haiku", "reply")
    chat_model = RouterChatModel(
        router=router, candidates={"claude-4-5-haiku": haiku}, default_model="claude-4-5-haiku"
    )

    await chat_model._agenerate([AIMessage(content="hi")])

    call_kwargs = haiku.ainvoke.await_args.kwargs
    assert "callbacks" not in call_kwargs["config"]


@pytest.mark.asyncio
async def test_clean_generation_info_capture_reads_generation_info_from_llm_result():
    capture = _CleanGenerationInfoCapture()
    assert capture.generation_info is None

    result = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="x"), generation_info={"a": "b"})]])
    await capture.on_llm_end(result)

    assert capture.generation_info == {"a": "b"}


@pytest.mark.asyncio
async def test_agenerate_extracts_from_clean_generation_info_when_response_metadata_corrupted():
    """Regression test: a streamed response's response_metadata['headers'] can end up with a
    custom header value duplicated string-wise (e.g. "modelmodel" instead of "model") by the
    time selected.ainvoke() returns, while the LLMResult.generation_info the same call reports
    at on_llm_end (captured via the callback attached in config) stays clean — see
    _CleanGenerationInfoCapture's docstring. extract() must see the clean copy."""
    router = _make_router(decision=None)
    router.extract = MagicMock(return_value=RoutingInfo(routed_model="claude-haiku-4-5"))

    clean_generation_info = {"headers": {"x-litellm-router-routed-model": "claude-haiku-4-5"}}
    corrupted_response_metadata = {"headers": {"x-litellm-router-routed-model": "claude-haiku-4-5claude-haiku-4-5"}}

    async def ainvoke(messages, stop=None, **kwargs):
        callbacks = kwargs["config"]["callbacks"]
        clean_result = LLMResult(
            generations=[[ChatGeneration(message=AIMessage(content="reply"), generation_info=clean_generation_info)]]
        )
        for cb in callbacks:
            await cb.on_llm_end(clean_result)
        return AIMessage(content="reply", response_metadata=corrupted_response_metadata)

    sonnet = MagicMock()
    sonnet.model_name = "claude-4-6-sonnet"
    sonnet.ainvoke = AsyncMock(side_effect=ainvoke)
    chat_model = RouterChatModel(
        router=router, candidates={"claude-4-6-sonnet": sonnet}, default_model="claude-4-6-sonnet"
    )

    await chat_model._agenerate([AIMessage(content="hi")])

    extract_arg = router.extract.call_args.args[0]
    assert isinstance(extract_arg, LLMResult)
    assert extract_arg.generations[0][0].generation_info == clean_generation_info


def test_bind_tools_returns_new_router_chat_model_with_tools_bound_on_every_candidate():
    router = _make_router(decision=None)
    candidate = MagicMock()
    bound_candidate = MagicMock()
    candidate.bind_tools.return_value = bound_candidate
    chat_model = RouterChatModel(router=router, candidates={"m": candidate}, default_model="m")

    result = chat_model.bind_tools(tools=[])

    assert result.candidates["m"] is bound_candidate
    assert result.router is router
    assert result.default_model == "m"
    candidate.bind_tools.assert_called_once_with(tools=[])
