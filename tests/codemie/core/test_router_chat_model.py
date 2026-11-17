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

from codemie.core.router_chat_model import (
    RouterChatModel,
    _CleanGenerationInfoCapture,
    _headers_of,
    _iter_header_maps,
    _ROUTING_CTX_KEY,
    build_chat_model_for,
)
from codemie.core.router import ClassifierCall, RoutingDecision
from codemie.core.routing_info import RoutingInfo, _ROUTING_INFO_KEY


def _make_router(decision):
    router = MagicMock()
    router.name = "switchyard"
    router.decide = AsyncMock(return_value=decision)
    # Mirrors Router.routing_info's real default (see core/router.py) so RouterChatModel's own
    # stamping behavior can be asserted without re-testing that formula here — it's covered on
    # its own in tests/codemie/core/test_router.py. Accepts (and ignores) header_maps, matching
    # the real interface's unconditional-both-args call shape.
    router.routing_info = MagicMock(
        side_effect=lambda d, header_maps: RoutingInfo(
            routed_model=d.model,
            classifier_cost_usd=d.classifier.cost_usd if d.classifier else None,
            routing_family=d.routing_family,
        )
        if d is not None
        else RoutingInfo()
    )
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
async def test_agenerate_does_not_stamp_routing_info_when_no_decision_and_headers_empty():
    router = _make_router(decision=None)
    sonnet = _make_candidate("claude-4-6-sonnet", "default reply")
    chat_model = RouterChatModel(
        router=router,
        candidates={"claude-4-6-sonnet": sonnet},
        default_model="claude-4-6-sonnet",
    )

    result = await chat_model._agenerate([AIMessage(content="hi")])

    router.routing_info.assert_called_once()
    call_args = router.routing_info.call_args.args
    assert call_args[0] is None
    assert list(call_args[1]) == []  # response_metadata has no "headers" key
    assert _ROUTING_INFO_KEY not in result.generations[0].message.response_metadata


@pytest.mark.asyncio
async def test_agenerate_stamps_routing_info_read_from_response_headers_when_no_decision():
    """decide()-less routers (LiteLLMRouter) never produce a RoutingDecision — their only
    channel for the routed model is reading it back out of the response itself, via
    header_maps built from the response. RouterChatModel must stamp whatever routing_info()
    returns from those headers, the same as it would stamp a decision-based routing_info()."""
    router = MagicMock()
    router.name = "litellm_complexity"
    router.decide = AsyncMock(return_value=None)
    router.routing_info = MagicMock(return_value=RoutingInfo(routed_model="claude-haiku-4-5"))
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
    """The capture callback exists purely to feed header_maps for decide()-less routers — a
    decision-bearing call (Switchyard) never needs it, so attaching it would be pure
    overhead; SwitchyardRouter.routing_info() never even iterates header_maps."""
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
async def test_agenerate_reads_header_maps_from_clean_generation_info_when_response_metadata_corrupted():
    """Regression test: a streamed response's response_metadata['headers'] can end up with a
    custom header value duplicated string-wise (e.g. "modelmodel" instead of "model") by the
    time selected.ainvoke() returns, while the LLMResult.generation_info the same call reports
    at on_llm_end (captured via the callback attached in config) stays clean — see
    _CleanGenerationInfoCapture's docstring. router.routing_info() must see the clean copy via
    header_maps."""
    router = MagicMock()
    router.name = "litellm_complexity"
    router.decide = AsyncMock(return_value=None)
    router.routing_info = MagicMock(return_value=RoutingInfo(routed_model="claude-haiku-4-5"))

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

    header_maps_arg = list(router.routing_info.call_args.args[1])
    assert header_maps_arg == [clean_generation_info["headers"]]


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


class TestHeaderMapExtraction:
    """_headers_of/_iter_header_maps unwrap a LangChain response's various shapes into flat
    header mappings — used by RouterChatModel._agenerate and directly by
    TokensCalculationCallback.on_llm_end (a separate callback that fires earlier in the
    LangChain callback chain and can't reuse _agenerate's own code path)."""

    def test_iter_header_maps_reads_ai_message_response_metadata(self):
        message = AIMessage(content="hi", response_metadata={"headers": {"x-litellm-router-routed-model": "haiku"}})
        assert list(_iter_header_maps(message)) == [{"x-litellm-router-routed-model": "haiku"}]

    def test_iter_header_maps_empty_when_ai_message_has_no_headers(self):
        message = AIMessage(content="hi", response_metadata={})
        assert list(_iter_header_maps(message)) == []

    def test_iter_header_maps_prefers_generation_info_over_response_metadata(self):
        """generation_info is captured directly from the LLM's own LLMResult before LangChain
        assembles response_metadata, which may contain a corrupted duplicate — never merge
        the two, always prefer the generation_info copy when both are present."""
        message = AIMessage(content="hi", response_metadata={"headers": {"a": "corrupted-a"}})
        result = LLMResult(
            generations=[[ChatGeneration(message=message, generation_info={"headers": {"a": "clean-a"}})]]
        )
        assert list(_iter_header_maps(result)) == [{"a": "clean-a"}]

    def test_iter_header_maps_falls_back_to_response_metadata_when_no_generation_info(self):
        message = AIMessage(content="hi", response_metadata={"headers": {"a": "b"}})
        result = LLMResult(generations=[[ChatGeneration(message=message, generation_info=None)]])
        assert list(_iter_header_maps(result)) == [{"a": "b"}]

    def test_headers_of_returns_none_for_non_mapping_container(self):
        assert _headers_of(None) is None
        assert _headers_of("not a mapping") is None
        assert _headers_of({"headers": "not a mapping either"}) is None


class TestBuildChatModelFor:
    """The inverted Router.build_chat_model(): consumes a Router via candidate_models()
    instead of the router constructing its own LangChain wrapper."""

    def test_returns_raw_client_when_no_candidates(self, monkeypatch):
        sentinel = object()
        called_with = {}

        def fake_get_llm_by_credentials(**kwargs):
            called_with.update(kwargs)
            return sentinel

        monkeypatch.setattr("codemie.core.dependecies.get_llm_by_credentials", fake_get_llm_by_credentials)
        from codemie.core.router import NULL_ROUTER
        from codemie.core.router_chat_model import LLMParams

        result = build_chat_model_for(
            NULL_ROUTER,
            model_name="gpt-4.1",
            request_id="req-1",
            llm_params=LLMParams(temperature=0.2, top_p=None, effort="high"),
        )

        assert result is sentinel
        assert called_with == {
            "llm_model": "gpt-4.1",
            "request_id": "req-1",
            "temperature": 0.2,
            "top_p": None,
            "effort": "high",
        }
        assert called_with["effort"] == "high"

    def test_wraps_multiple_candidates_in_router_chat_model(self, monkeypatch):
        router = MagicMock()
        router.candidate_models.return_value = ("claude-4-6-sonnet", "claude-4-5-haiku")

        def fake_get_llm_by_credentials(*, llm_model, **kwargs):
            return f"client-{llm_model}"

        monkeypatch.setattr("codemie.core.dependecies.get_llm_by_credentials", fake_get_llm_by_credentials)
        from codemie.core.router_chat_model import LLMParams

        result = build_chat_model_for(
            router,
            model_name="claude-4-6-sonnet-switchyard-claude-4-5-haiku-signal",
            request_id="req-1",
            llm_params=LLMParams(temperature=None, top_p=None),
        )

        assert isinstance(result, RouterChatModel)
        assert result.router is router
        assert result.candidates == {
            "claude-4-6-sonnet": "client-claude-4-6-sonnet",
            "claude-4-5-haiku": "client-claude-4-5-haiku",
        }
        assert result.default_model == "claude-4-6-sonnet"

    def test_wraps_self_referential_single_candidate_for_litellm_router(self, monkeypatch):
        """LiteLLMRouter.candidate_models() returns (self.name,) — its own alias, the one
        deployable model. build_chat_model_for must still wrap it in RouterChatModel (not
        return a raw client) so the post-call routing_info() stamp runs."""
        from codemie.enterprise.litellm.router import LiteLLMRouter

        router = LiteLLMRouter("gpt-smart-router", counterfactual_model="gpt-5.6-terra-2026-07-09")

        def fake_get_llm_by_credentials(*, llm_model, **kwargs):
            return f"client-{llm_model}"

        monkeypatch.setattr("codemie.core.dependecies.get_llm_by_credentials", fake_get_llm_by_credentials)
        from codemie.core.router_chat_model import LLMParams

        result = build_chat_model_for(
            router, model_name="gpt-smart-router", request_id="req-1", llm_params=LLMParams(temperature=0.2, top_p=None)
        )

        assert isinstance(result, RouterChatModel)
        assert result.router is router
        assert result.candidates == {"gpt-smart-router": "client-gpt-smart-router"}
        assert result.default_model == "gpt-smart-router"


class TestRoutedPayer:
    """A routed chat reports the payer of the candidate that runs, not of the one built last (EPMCDME-16625)."""

    @pytest.fixture
    def llm_context(self):
        from codemie.core.dependecies import litellm_context
        from codemie.rest_api.models.settings import LiteLLMContext

        context = LiteLLMContext(
            credentials=None,
            current_project="dept-a",
            principal_email="user@example.com",
            personal_project="user@example.com",
            routing_project="dept-a",
            budget_candidates=["dept-a", "dept-b"],
        )
        token = litellm_context.set(context)
        yield context
        litellm_context.reset(token)

    @staticmethod
    def _builds_charging(payers: dict):
        """A get_llm_by_credentials stand-in whose build records the payer, as _record_direct_budget_attribution does."""
        from codemie.core.dependecies import litellm_context

        def build(*, llm_model, **kwargs):
            payer = payers.get(llm_model)
            if payer is not None:
                context = litellm_context.get()
                context.current_project, context.budget_fallback_kind = payer
                context.budget_fallback_from = "dept-a" if payer[1] else None
                context.budget_outcome_built = True
            return _make_candidate(llm_model, f"reply from {llm_model}")

        return build

    def test_each_candidate_keeps_its_own_payer_and_the_context_names_the_default(self, monkeypatch, llm_context):
        from codemie.core.router_chat_model import LLMParams

        router = MagicMock()
        router.candidate_models.return_value = ("sonnet", "haiku")
        monkeypatch.setattr(
            "codemie.core.dependecies.get_llm_by_credentials",
            self._builds_charging({"sonnet": ("dept-a", None), "haiku": ("user@example.com", "personal")}),
        )

        result = build_chat_model_for(
            router, model_name="router", request_id="req-1", llm_params=LLMParams(temperature=None, top_p=None)
        )

        assert result.payers["sonnet"].current_project == "dept-a"
        assert result.payers["haiku"].current_project == "user@example.com"
        assert result.payers["haiku"].budget_fallback_kind == "personal"
        assert llm_context.current_project == "dept-a"
        assert llm_context.budget_fallback_kind is None
        assert llm_context.budget_outcome_built is True

    @pytest.mark.asyncio
    async def test_the_selected_candidate_payer_is_what_analytics_read(self, monkeypatch, llm_context):
        from codemie.core.dependecies import get_current_project
        from codemie.core.router_chat_model import LLMParams

        decision = RoutingDecision(
            model="haiku", tier="efficient", decision_source="llm_classifier", routing_family="switchyard"
        )
        router = _make_router(decision)
        # The selected candidate is neither the default nor the one built last.
        router.candidate_models.return_value = ("sonnet", "haiku", "opus")
        monkeypatch.setattr(
            "codemie.core.dependecies.get_llm_by_credentials",
            self._builds_charging(
                {"sonnet": ("dept-a", None), "haiku": ("user@example.com", "personal"), "opus": ("dept-b", None)}
            ),
        )
        chat_model = build_chat_model_for(
            router, model_name="router", request_id="req-1", llm_params=LLMParams(temperature=None, top_p=None)
        )

        await chat_model._agenerate([AIMessage(content="hi")])

        assert get_current_project() == "user@example.com"
        assert llm_context.budget_fallback_kind == "personal"
        assert llm_context.budget_fallback_from == "dept-a"

    def test_a_build_that_records_no_payer_is_not_credited_with_an_earlier_one(self, monkeypatch, llm_context):
        from codemie.core.router_chat_model import LLMParams

        llm_context.budget_outcome_built = True
        router = MagicMock()
        router.candidate_models.return_value = ("own-creds",)
        monkeypatch.setattr("codemie.core.dependecies.get_llm_by_credentials", self._builds_charging({}))

        result = build_chat_model_for(
            router, model_name="router", request_id="req-1", llm_params=LLMParams(temperature=None, top_p=None)
        )

        assert result.payers == {"own-creds": None}
        assert llm_context.current_project == "dept-a"

    @pytest.mark.asyncio
    async def test_a_payer_built_for_another_request_is_not_restored(self, monkeypatch, llm_context):
        from codemie.core.dependecies import BuiltPayer

        router = _make_router(decision=None)
        stale = BuiltPayer(
            request=("other@example.com", "dept-a", ("dept-a",)),
            current_project="other@example.com",
            budget_fallback_from="dept-a",
            budget_fallback_kind="personal",
            budget_funding_unknown=False,
        )
        chat_model = RouterChatModel(
            router=router,
            candidates={"sonnet": _make_candidate("sonnet", "reply")},
            default_model="sonnet",
            payers={"sonnet": stale},
        )

        await chat_model._agenerate([AIMessage(content="hi")])

        assert llm_context.current_project == "dept-a"
        assert llm_context.budget_fallback_kind is None

    def test_bind_tools_keeps_the_payers(self):
        payer = object()
        candidate = MagicMock()
        candidate.bind_tools.return_value = candidate
        chat_model = RouterChatModel(
            router=_make_router(None),
            candidates={"sonnet": candidate},
            default_model="sonnet",
            payers={"sonnet": payer},
        )

        assert chat_model.bind_tools([]).payers == {"sonnet": payer}
