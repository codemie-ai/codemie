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

"""NeMo Switchyard routing for the LangGraph agent path.

Reuses the proxy-side routing engine (``ProxySwitchyardRouter``) one-to-one,
including both ``signal`` and ``classifier`` routing modes.  The selected model
is then invoked directly through LangChain so the rest of the agent stack
(callbacks, token tracking, history) works unchanged.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
from collections.abc import Callable, Sequence
from typing import Any, cast

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.messages import convert_to_openai_messages
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool

from codemie.configs.logger import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.enterprise.switchyard.decision import RoutingDecision, RoutingTier
from codemie.enterprise.switchyard.engine import ProxySwitchyardRouter, get_proxy_switchyard_router
from codemie.enterprise.switchyard.proxy import build_switchyard_routing_meta
from codemie.enterprise.switchyard.routing_meta import (
    _SWITCHYARD_DECISION_METADATA_KEY,
    _SWITCHYARD_RESPONSE_META_KEY,
)


class SwitchyardRoutingChatModel(BaseChatModel):
    """LangChain chat model that routes each call through ProxySwitchyardRouter.

    The proxy router is stateless and is created fresh per request, exactly as in
    the HTTP proxy path.  Capable and efficient LangChain models are provided up
    front; the router only decides which one to call.
    """

    # BaseChatModel before bind_tools() is called on this instance, Runnable[...] after —
    # bind_tools() returns a RunnableBinding wrapping the bound model, which no longer
    # satisfies BaseChatModel but still supports ainvoke() and the duck-typed attribute
    # lookups _model_name relies on.
    capable_model: BaseChatModel | Runnable[LanguageModelInput, AIMessage]
    efficient_model: BaseChatModel | Runnable[LanguageModelInput, AIMessage]
    router: ProxySwitchyardRouter

    @property
    def _llm_type(self) -> str:
        return "codemie-switchyard-routing"

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
    ) -> BaseChatModel:
        """Return a new routing model with tools bound to both candidate models."""
        capable_model, efficient_model = self.capable_model, self.efficient_model
        if not isinstance(capable_model, BaseChatModel) or not isinstance(efficient_model, BaseChatModel):
            raise TypeError(
                "SwitchyardRoutingChatModel.bind_tools() can only be called once, on the "
                "model returned by build_switchyard_routing_model() — tools are already "
                "bound on this instance."
            )
        # BaseChatModel.bind_tools declares tool_choice as str | None, but concrete
        # providers (ChatAnthropic, ChatOpenAI, ...) accept dict/bool too; forward
        # whatever the caller passed through unchanged rather than narrowing it away.
        provider_tool_choice = cast(Any, tool_choice)
        return SwitchyardRoutingChatModel(
            capable_model=capable_model.bind_tools(tools, tool_choice=provider_tool_choice, **kwargs),
            efficient_model=efficient_model.bind_tools(tools, tool_choice=provider_tool_choice, **kwargs),
            router=self.router,
        )

    def _select_model(
        self, decision: RoutingDecision | None
    ) -> tuple[BaseChatModel | Runnable[LanguageModelInput, AIMessage], RoutingTier, str]:
        """Return (selected_llm, tier, routed_model_name) for a RoutingDecision."""
        if decision is None or decision.tier == RoutingTier.CAPABLE:
            return self.capable_model, RoutingTier.CAPABLE, self._model_name(self.capable_model)
        return self.efficient_model, RoutingTier.EFFICIENT, self._model_name(self.efficient_model)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        openai_messages = convert_to_openai_messages(messages)
        decision = await self.router.pick_model(openai_messages)

        selected_model, tier, routed_model_name = self._select_model(decision)
        logger.info(
            f"[SWITCHYARD-AGENT] Routed: tier={tier} actual={routed_model_name!r} "
            f"capable={self.router.capable_model!r} efficient={self.router.efficient_model!r}"
        )

        # Bind tools on the selected model when requested.
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)
        if tools:
            if not isinstance(selected_model, BaseChatModel):
                raise TypeError("Switchyard target already has tools bound; cannot bind tools again at call time.")
            selected_model = selected_model.bind_tools(
                tools,
                tool_choice=cast(Any, tool_choice),
            )

        # Pass the decision through the call's own RunnableConfig metadata, not via
        # response_metadata (that's set below and only exists *after* ainvoke returns).
        # TokensCalculationCallback is attached to the concretely selected model itself
        # (get_llm_by_credentials attaches it automatically), so its on_llm_end for THIS
        # call fires before this function ever gets a chance to touch the response object —
        # the classifier usage on `decision` would otherwise never reach it. `run_id` is
        # generated once by LangChain per call and is identical between on_chat_model_start
        # (where the callback reads this metadata) and on_llm_end (where it's consumed), so
        # this is safe to pass even when the same model instance handles calls concurrently.
        if decision is not None:
            existing_config = cast("RunnableConfig", kwargs.pop("config", None) or {})
            existing_metadata: dict[str, Any] = dict(existing_config.get("metadata") or {})
            existing_metadata[_SWITCHYARD_DECISION_METADATA_KEY] = decision
            kwargs["config"] = {**existing_config, "metadata": existing_metadata}

        response = await selected_model.ainvoke(messages, stop=stop, **kwargs)
        if not isinstance(response, AIMessage):
            raise ValueError(f"Switchyard target returned {type(response).__name__} instead of AIMessage")

        # Attach the same routing metadata the proxy path injects.
        if decision is not None:
            meta = build_switchyard_routing_meta(decision)
            meta.routed_model = routed_model_name
            response.response_metadata = {
                **(response.response_metadata or {}),
                _SWITCHYARD_RESPONSE_META_KEY: dataclasses.asdict(meta),
            }
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
            "synchronous Switchyard routing cannot run inside an active event loop; "
            "use await agent.ainvoke(...) instead"
        )


@dataclasses.dataclass(frozen=True, slots=True)
class LLMParams:
    """Generation parameters forwarded to get_llm_by_credentials for both models."""

    temperature: float | None
    top_p: float | None


def build_switchyard_routing_model(
    *,
    router_name: str,
    request_id: str,
    llm_params: LLMParams,
) -> SwitchyardRoutingChatModel | None:
    """Build the per-call Switchyard routing model for the agent path.

    Returns None when ``router_name`` has no Switchyard configuration — the
    caller should fall back to a plain ``get_llm_by_credentials`` call in that
    case, exactly as it would without Switchyard.
    """
    router = get_proxy_switchyard_router(router_name=router_name)
    if router is None:
        return None

    capable_llm = get_llm_by_credentials(
        llm_model=router.capable_model,
        temperature=llm_params.temperature,
        top_p=llm_params.top_p,
        request_id=request_id,
    )
    efficient_llm = get_llm_by_credentials(
        llm_model=router.efficient_model,
        temperature=llm_params.temperature,
        top_p=llm_params.top_p,
        request_id=request_id,
    )
    return SwitchyardRoutingChatModel(
        capable_model=capable_llm,
        efficient_model=efficient_llm,
        router=router,
    )
