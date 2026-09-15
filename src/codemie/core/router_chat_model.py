# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""RouterChatModel — the one generic LangChain wrapper for any decide-capable Router.

Replaces enterprise/switchyard/agent.py::SwitchyardRoutingChatModel. No router-specific
LangChain plumbing is written per mechanism: any future Router that implements decide() and
candidate_models() gets this wrapper for free.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
from collections.abc import Callable, Sequence
from typing import Annotated, Any, cast

from langchain_core.callbacks import AsyncCallbackHandler, AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from pydantic import SkipValidation
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool

from codemie.configs.logger import logger
from codemie.core.router import Router, RoutingDecision
from codemie.core.routing_info import stamp_routing_info

# Key under which (router, decision) is stashed in a call's RunnableConfig.metadata — read by
# TokensCalculationCallback.on_chat_model_start. Not serialized: this is all in-process Python,
# RunnableConfig.metadata is a plain dict passed by reference through the LangChain callback
# chain, so the live Router instance and its RoutingDecision are stashed directly. Prefer
# stash_routing_ctx()/read_routing_ctx() over touching this key directly.
_ROUTING_CTX_KEY = "_routing_ctx"


def stash_routing_ctx(
    config: "RunnableConfig | dict[str, Any] | None", router: Router, decision: "RoutingDecision | None"
) -> dict[str, Any]:
    """Return a new call config with (router, decision) stashed under _ROUTING_CTX_KEY,
    merged with any metadata already on *config*. Read back with read_routing_ctx()."""
    existing_config = cast("RunnableConfig", config or {})
    existing_metadata: dict[str, Any] = dict(existing_config.get("metadata") or {})
    existing_metadata[_ROUTING_CTX_KEY] = (router, decision)
    return {**existing_config, "metadata": existing_metadata}


def read_routing_ctx(metadata: dict[str, Any] | None) -> "tuple[Router, RoutingDecision | None] | None":
    """Read back a (router, decision) pair stashed by stash_routing_ctx(), if present."""
    if not metadata:
        return None
    return metadata.get(_ROUTING_CTX_KEY)


class _CleanGenerationInfoCapture(AsyncCallbackHandler):
    """Captures generation_info exactly as the candidate model's own on_llm_end reports it —
    read by _agenerate to build Router.extract()'s input for decide()-less routers (LiteLLM).

    Why this exists: for a streamed response, the AIMessage.response_metadata that
    selected.ainvoke() hands back can end up with a custom header value duplicated
    string-wise (observed: "modelmodel" instead of "model") by the time _agenerate sees it —
    an artifact of how the underlying chunk-to-message assembly re-derives response_metadata
    from generation_info more than once. generation_info itself, read fresh off the LLMResult
    at on_llm_end (the same object TokensCalculationCallback already reads correctly, attached
    the same way), does not go through that reassembly and stays intact. This callback is the
    only way to reach that clean copy, since ainvoke() itself only returns the bare AIMessage,
    discarding the LLMResult/generation_info it was carried on.
    """

    def __init__(self) -> None:
        self.generation_info: dict[str, Any] | None = None

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        if response.generations and response.generations[0]:
            self.generation_info = response.generations[0][0].generation_info


@dataclasses.dataclass(frozen=True, slots=True)
class LLMParams:
    """Generation parameters forwarded to get_llm_by_credentials for every candidate model."""

    temperature: float | None
    top_p: float | None


class RouterChatModel(BaseChatModel):
    """LangChain chat model that routes each call through a Router.decide() call.

    `router` is resolved ONCE, at construction (see Router.build_chat_model), and held for the
    object's entire lifetime — not re-resolved by name on every call. RouterChatModel lives
    across an agent's whole multi-turn session; re-resolving by name on every _agenerate call
    would let the object silently start talking to a different kind of router mid-session if
    config changed underneath it, and its pre-built `candidates` dict (built from the
    *original* resolution's candidate_models()) would then be inconsistent with a decision
    produced by a newly-resolved, different router. Holding the concrete instance removes this
    drift risk entirely.
    """

    # BaseChatModel before bind_tools() is called on this instance, Runnable[...] after —
    # bind_tools() returns a RunnableBinding wrapping the bound model, which no longer
    # satisfies BaseChatModel but still supports ainvoke() and the duck-typed attribute
    # lookups _model_name relies on.
    router: Annotated[Router, SkipValidation]
    candidates: dict[str, Annotated[BaseChatModel | Runnable[LanguageModelInput, AIMessage], SkipValidation]]
    default_model: str

    @property
    def _llm_type(self) -> str:
        return "codemie-router-routing"

    def _model_name(self, model: BaseChatModel | Runnable[LanguageModelInput, AIMessage]) -> str:
        for attribute in ("model_name", "model"):
            value = getattr(model, attribute, None)
            if isinstance(value, str) and value:
                return value
        llm_type = getattr(model, "_llm_type", None)
        return llm_type if isinstance(llm_type, str) else "unknown"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RouterChatModel:
        """Return a new routing model with tools bound to every candidate model."""
        bind_kwargs: dict[str, Any] = {"tools": tools, **kwargs}
        if tool_choice is not None:
            bind_kwargs["tool_choice"] = cast(Any, tool_choice)
        bound = {name: model.bind_tools(**bind_kwargs) for name, model in self.candidates.items()}
        return RouterChatModel(router=self.router, candidates=bound, default_model=self.default_model)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        openai_messages = convert_to_openai_messages(messages)
        decision = await self.router.decide(openai_messages)

        selected_name = decision.model if decision is not None else self.default_model
        selected = self.candidates[selected_name]
        routed_model_name = self._model_name(selected)
        logger.info(
            f"[ROUTING] router={self.router.name!r} selected={routed_model_name!r} "
            f"candidates={sorted(self.candidates)!r}"
        )

        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)
        if tools:
            if not isinstance(selected, BaseChatModel):
                raise TypeError("Router target already has tools bound; cannot bind tools again at call time.")
            selected = selected.bind_tools(tools, tool_choice=cast(Any, tool_choice))

        # Pre-call: always stash the router (and decision, which may be None if this router's
        # decide() returned None — e.g. routing_mode=None). The router is unconditionally real
        # here: RouterChatModel is only ever built when candidate_models() is non-empty (see
        # Router.build_chat_model), so self.router is never NullRouter inside _agenerate.
        config = stash_routing_ctx(kwargs.pop("config", None), self.router, decision)

        # Only decide()-less routers (LiteLLM) need extract() at all — Switchyard already
        # knows what it picked (routing_info(decision) below). Attach the capture callback
        # only then: it's how extract() gets an uncorrupted generation_info to read (see
        # _CleanGenerationInfoCapture's docstring), and there's no reason to pay for it
        # otherwise.
        capture = _CleanGenerationInfoCapture() if decision is None else None
        if capture is not None:
            config = {**config, "callbacks": [*(config.get("callbacks") or []), capture]}
        kwargs["config"] = config

        response = await selected.ainvoke(messages, stop=stop, **kwargs)
        if not isinstance(response, AIMessage):
            raise ValueError(f"Router target returned {type(response).__name__} instead of AIMessage")

        # Post-call: stamp the CANONICAL RoutingInfo (not a router-specific dataclass) so
        # response-level consumers (AgentInvokeCallback/AgentStreamingCallback) can read it
        # self-describingly, without needing a Router reference at all. decision-based routers
        # (Switchyard) already know what they picked; decide-less routers (LiteLLM's own
        # auto-router, always None here — see Router.decide()'s docstring) only learn it from
        # the response itself, via this same router's own extract() — the one method every
        # Router implements for exactly this "no decision was ever in hand" case.
        if decision is not None:
            stamp_routing_info(response, self.router.routing_info(decision))
        else:
            # Prefer the clean generation_info the capture callback caught over the bare
            # response: response.response_metadata can end up with a corrupted copy of the
            # same data by this point (see _CleanGenerationInfoCapture's docstring) — wrapping
            # response in a minimal LLMResult carrying the clean generation_info lets
            # Router.extract() read the good copy via the exact same code path it already
            # uses for a real LLMResult (TokensCalculationCallback.on_llm_end's call).
            extract_source: LLMResult | AIMessage = response
            if capture is not None and capture.generation_info:
                extract_source = LLMResult(
                    generations=[[ChatGeneration(message=response, generation_info=capture.generation_info)]]
                )
            extracted = self.router.extract(extract_source)
            if not extracted.is_empty():
                stamp_routing_info(response, extracted)
        return ChatResult(generations=[ChatGeneration(message=response)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            with asyncio.Runner() as runner:
                return runner.run(
                    self._agenerate(messages, stop=stop, **kwargs),
                    context=contextvars.copy_context(),
                )
        raise RuntimeError(
            "synchronous Router routing cannot run inside an active event loop; use await agent.ainvoke(...) instead"
        )
