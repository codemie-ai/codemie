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

from __future__ import annotations

import contextlib
import uuid
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import LLMResult

from codemie.configs import config, logger
from codemie.core.llm_cache import is_litellm_proxy_cache_hit, is_llm_cache_hit
from codemie.core.router import CallContext
from codemie.core.routing_costs import with_counterfactual_costs
from codemie.core.utils import calculate_token_cost
from codemie.service.request_summary_manager import request_summary_manager, LLMRun
from codemie.service.llm_service.llm_service import llm_service

if TYPE_CHECKING:
    from codemie.core.router import Router, RoutingDecision
    from codemie.core.routing_info import RoutingInfo


class TokensCalculationCallback(AsyncCallbackHandler):
    def __init__(self, request_id: str, llm_model: str) -> None:
        super().__init__()
        self.internal_run_id = str(uuid.uuid4())
        self.request_id = request_id
        self.llm_model = llm_model
        self.input_tokens = 0
        self.output_tokens = 0
        # (router, decision) pairs passed in via a call's config={"metadata": {...}} (see
        # core/router_chat_model.py::RouterChatModel._agenerate), keyed by that call's run_id.
        # This callback is attached directly to the concretely selected candidate model
        # (get_llm_by_credentials attaches it automatically), so on_llm_end for a given run
        # fires before _agenerate has any chance to attach routing data to the response — the
        # config-metadata channel is the only way a decide-time decision reaches this callback.
        # Stashed in on_chat_model_start, consumed and popped in on_llm_end/on_llm_error.
        self._pending: dict[UUID, tuple["Router", "RoutingDecision | None"]] = {}
        # Lazily-resolved, memoized fallback Router for calls that never went through
        # RouterChatModel (so _pending has no entry) — self.llm_model is fixed for this
        # callback's entire lifetime, so create_router(self.llm_model) always resolves to the
        # same instance; no need to re-resolve it on every on_llm_end call.
        self._fallback_router: "Router | None" = None

    def _resolve_fallback_router(self) -> "Router":
        """Memoized create_router(self.llm_model) — see self._fallback_router's docstring."""
        if self._fallback_router is None:
            from codemie.service.llm_service.router_factory import create_router

            self._fallback_router = create_router(self.llm_model)
        return self._fallback_router

    @staticmethod
    def _extract_proxy_cost(generation_info: dict) -> Optional[float]:
        """Return the pre-calculated cost from LiteLLM proxy generation_info, or None."""
        streaming_cost = generation_info.get("litellm_cost")
        if streaming_cost is not None:
            with contextlib.suppress(ValueError, TypeError):
                return float(streaming_cost)
        cost_str = generation_info.get("headers", {}).get("x-litellm-response-cost")
        if cost_str:
            with contextlib.suppress(ValueError, TypeError):
                return float(cost_str)
        return None

    @staticmethod
    def _iter_gen_results(response: LLMResult):
        for gen in response.generations:
            yield from gen

    def _calculate_cost(
        self,
        proxy_cost: Optional[float],
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cache_creation_tokens: int,
        cost_model: str | None = None,
    ) -> tuple[float, float, float]:
        """Return (money_spent, cached_tokens_money_spent, cached_tokens_creation_cost).

        ``cost_model`` defaults to ``self.llm_model`` — this callback is always attached to
        the concretely invoked model (proxy path, plain agent path, or the capable/efficient
        model actually selected by Switchyard), so ``self.llm_model`` is already the correct
        billed model with no alias to resolve.
        """
        if proxy_cost is not None:
            return proxy_cost, 0.0, 0.0
        model_costs = llm_service.get_model_cost(cost_model or self.llm_model)
        return calculate_token_cost(
            llm_model=cost_model or self.llm_model,
            cost_config=model_costs,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )

    @staticmethod
    def _usage_from_message(gen_result: Any) -> tuple[int, int, int, int]:
        """Token counts from the generation's own usage_metadata, or all zero when absent.

        Accesses gen_result.message directly (not getattr) so a plain Generation without a
        message attribute at all still raises — on_llm_end's outer try/except is the intended
        handler for that malformed-response case, matching the pre-extraction behavior."""
        if not (gen_result.message and gen_result.message.usage_metadata):
            return 0, 0, 0, 0
        usage_metadata: UsageMetadata = gen_result.message.usage_metadata
        logger.debug(f"On LLM End. Usage metadata: {usage_metadata}")
        details = usage_metadata.get("input_token_details", {})
        return (
            usage_metadata.get("input_tokens", 0),
            usage_metadata.get("output_tokens", 0),
            details.get("cache_read", 0),
            details.get("cache_creation", 0),
        )

    @staticmethod
    def _served_model_from_response_metadata(gen_result: Any) -> str | None:
        """Non-proxied providers (e.g. plain ChatOpenAI) embed the actually-served model
        directly in the message's response_metadata, not generation_info."""
        response_metadata = getattr(getattr(gen_result, "message", None), "response_metadata", None)
        if not isinstance(response_metadata, dict):
            return None
        served_model = response_metadata.get("model") or response_metadata.get("model_name")
        return str(served_model) if served_model else None

    def _billed_model_and_proxy_info(
        self,
        gen_result: Any,
        billed_model: str | None,
        proxy_cost: Optional[float],
        headers: dict[str, str],
    ) -> tuple[str | None, Optional[float], dict[str, str]]:
        """Update billed_model/proxy_cost/headers from one generation's generation_info,
        falling back to the served model embedded in response_metadata when generation_info
        never carried one."""
        if gen_result.generation_info:
            billed_model = gen_result.generation_info.get("model") or billed_model
            if config.LLM_PROXY_ENABLED and config.LLM_PROXY_TRACK_USAGE and proxy_cost is None:
                proxy_cost = self._extract_proxy_cost(gen_result.generation_info)
            if not headers:
                headers = gen_result.generation_info.get("headers") or {}
        if billed_model is None:
            billed_model = self._served_model_from_response_metadata(gen_result)
        return billed_model, proxy_cost, headers

    def _accumulate_generation_usage(
        self, response: LLMResult
    ) -> tuple[int, int, int, int, Optional[float], str | None, dict[str, str], bool]:
        """Aggregate token/cost/header data across every non-cache-hit generation in *response*.

        Returns (input_tokens, output_tokens, cached_tokens, cache_creation_tokens, proxy_cost,
        billed_model, headers, any_processed).
        """
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        cache_creation_tokens = 0
        proxy_cost: Optional[float] = None
        billed_model: str | None = None
        headers: dict[str, str] = {}
        any_processed = False

        for gen_result in self._iter_gen_results(response):
            if gen_result.generation_info and is_litellm_proxy_cache_hit(gen_result.generation_info):
                logger.debug(
                    "Skipping LangGraph usage tracking for LiteLLM proxy cache hit (x-litellm-cache-key): "
                    f"request_id={self.request_id} model={self.llm_model}"
                )
                continue
            any_processed = True

            in_tokens, out_tokens, cache_read, cache_creation = self._usage_from_message(gen_result)
            input_tokens += in_tokens
            output_tokens += out_tokens
            cached_tokens += cache_read
            cache_creation_tokens += cache_creation

            billed_model, proxy_cost, headers = self._billed_model_and_proxy_info(
                gen_result, billed_model, proxy_cost, headers
            )

        return (
            input_tokens,
            output_tokens,
            cached_tokens,
            cache_creation_tokens,
            proxy_cost,
            billed_model,
            headers,
            any_processed,
        )

    def _record_classifier_usage(
        self,
        router: "Router",
        decision: "RoutingDecision | None",
        run_id: UUID,
        headers: dict[str, str],
    ) -> None:
        ctx = CallContext(run_id=str(run_id), decision=decision, headers=headers or None)
        usage = router.extract_classifier_usage(ctx)
        if usage is None or not (usage.input_tokens or usage.output_tokens or usage.cost_usd is not None):
            return
        clf_money_spent, _, _ = self._calculate_cost(usage.cost_usd, usage.input_tokens, usage.output_tokens, 0, 0)
        request_summary_manager.update_llm_run(
            request_id=self.request_id,
            llm_run=LLMRun(
                run_id=str(run_id) + "-classifier",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                money_spent=clf_money_spent,
                llm_model=usage.model or self.llm_model,
            ),
        )

    @staticmethod
    def _apply_usage_to_routing(
        display_routing: "RoutingInfo",
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cache_creation_tokens: int,
        cache_hit: bool,
    ) -> "RoutingInfo":
        """Stamp actual token usage onto an already-decided routing record."""
        if display_routing.is_empty():
            return display_routing
        return display_routing.model_copy(
            update={
                "routed_input_tokens": input_tokens,
                "routed_output_tokens": output_tokens,
                "routed_cached_tokens": cached_tokens,
                "routed_cache_creation_tokens": cache_creation_tokens,
                "routed_total_tokens": input_tokens + output_tokens,
                "routed_cache_hit": cache_hit,
            }
        )

    def _resolve_counterfactual_model(self, display_routing: "RoutingInfo") -> "RoutingInfo":
        """Populate counterfactual_model if the router didn't already declare it."""
        if not display_routing.routed_model or display_routing.counterfactual_model:
            return display_routing
        from codemie.enterprise.litellm.routing_headers import resolve_counterfactual_model

        counterfactual_model = resolve_counterfactual_model(
            display_routing.requested_model or display_routing.routed_model or self.llm_model
        )
        if not counterfactual_model:
            return display_routing
        return display_routing.model_copy(update={"counterfactual_model": counterfactual_model})

    def _apply_counterfactual_costs(
        self,
        display_routing: "RoutingInfo",
        money_spent: float,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cache_creation_tokens: int,
    ) -> "RoutingInfo":
        """Re-price tokens at counterfactual_model and stamp actual-vs-counterfactual savings.

        On the agent path, actual_cost_usd includes the classifier fee (it books the
        classifier as its own LLMRun), unlike the proxy path.
        """
        if not (display_routing.counterfactual_model and display_routing.routed_model):
            return display_routing
        try:
            if display_routing.tier == "complex":
                # Already at top tier; use actual spend, not re-pricing, to avoid
                # catalog-vs-agent-path rounding showing up as fake savings.
                estimated_max_usd = money_spent
            else:
                estimated_max_usd, _, _ = self._calculate_cost(
                    proxy_cost=None,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_tokens=cached_tokens,
                    cache_creation_tokens=cache_creation_tokens,
                    cost_model=display_routing.counterfactual_model,
                )
            return with_counterfactual_costs(
                display_routing,
                actual_cost_usd=money_spent + (display_routing.classifier_cost_usd or 0.0),
                estimated_max_cost_usd=estimated_max_usd,
            )
        except Exception:
            logger.debug(f"Could not compute counterfactual costs for {display_routing.counterfactual_model}")
            return display_routing

    def _cost_for_usage(
        self,
        billed_model: str,
        proxy_cost: float | None,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cache_creation_tokens: int,
        cache_hit: bool,
    ) -> tuple[float, float, float]:
        """Compute (money_spent, cached_tokens_money_spent, cached_tokens_creation_cost).

        Cache hits are billed at zero: LiteLLM served the whole response from cache, so no
        upstream tokens were actually spent regardless of what usage the provider reported.
        """
        if cache_hit:
            return 0.0, 0.0, 0.0
        return self._calculate_cost(
            proxy_cost, input_tokens, output_tokens, cached_tokens, cache_creation_tokens, cost_model=billed_model
        )

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Run when LLM ends running."""
        try:
            router, decision = self._pending.pop(run_id, (None, None))
            if router is None:
                router = self._resolve_fallback_router()

            cache_hit = is_llm_cache_hit(response)
            if cache_hit:
                logger.debug(
                    "Skipping LangGraph usage tracking for LiteLLM cache hit: "
                    f"request_id={self.request_id} model={self.llm_model} estimated_spend_skipped=unknown"
                )

            (
                input_tokens,
                output_tokens,
                cached_tokens,
                cache_creation_tokens,
                proxy_cost,
                billed_model,
                headers,
                any_processed,
            ) = self._accumulate_generation_usage(response)
            if not any_processed:
                return

            # Prefer the decision already in hand over extract(response): for SwitchyardRouter,
            # extract() reads a stamp RouterChatModel._agenerate only applies *after* this
            # callback's own on_llm_end fires (it's attached directly to the candidate model —
            # see get_llm_by_credentials — so it runs inside selected.ainvoke(), strictly
            # before the post-call stamp). decision, when present, was already stashed
            # pre-call and is always correct; extract() is only the right channel when no
            # decision exists at all (LiteLLMRouter's decide() always returns None — see
            # Router.extract()'s docstring on core/router.py).
            display_routing = router.routing_info(decision) if decision is not None else router.extract(response)
            if cache_hit and display_routing.is_empty():
                return

            billed_model = billed_model or display_routing.routed_model or self.llm_model
            money_spent, cached_tokens_money_spent, cached_tokens_creation_cost = self._cost_for_usage(
                billed_model, proxy_cost, input_tokens, output_tokens, cached_tokens, cache_creation_tokens, cache_hit
            )
            display_routing = self._apply_usage_to_routing(
                display_routing, input_tokens, output_tokens, cached_tokens, cache_creation_tokens, cache_hit
            )
            display_routing = self._resolve_counterfactual_model(display_routing)
            display_routing = self._apply_counterfactual_costs(
                display_routing, money_spent, input_tokens, output_tokens, cached_tokens, cache_creation_tokens
            )

            llm_run = LLMRun(
                run_id=str(run_id),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                money_spent=money_spent,
                cached_tokens_money_spent=cached_tokens_money_spent,
                cached_tokens_creation_cost=cached_tokens_creation_cost,
                llm_model=self.llm_model,
                routing=display_routing if not display_routing.is_empty() else None,
            )
            request_summary_manager.update_llm_run(request_id=self.request_id, llm_run=llm_run)

            self._record_classifier_usage(router, decision, run_id, headers)
        except Exception as e:
            logger.error(f"Error while calculating tokens: {str(e)}")

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Run when LLM errors. Capture partial token usage if the provider returns it."""
        # Drop any pending (router, decision) pair for this run — it errored before on_llm_end
        # could consume it.
        self._pending.pop(run_id, None)
        try:
            logger.warning(f"LLM error for run {run_id}, model {self.llm_model}: {error}")
            response: Optional[LLMResult] = kwargs.get("response")
            if response is None:
                return
            if is_llm_cache_hit(response):
                logger.debug(
                    "Skipping LangGraph error usage tracking for LiteLLM cache hit: "
                    f"request_id={self.request_id} model={self.llm_model} estimated_spend_skipped=unknown"
                )
                return

            input_tokens = 0
            output_tokens = 0
            cached_tokens = 0
            cache_creation_tokens = 0
            for gen_result in self._iter_gen_results(response):
                if gen_result.generation_info and is_litellm_proxy_cache_hit(gen_result.generation_info):
                    logger.debug(
                        "Skipping LangGraph error usage tracking for LiteLLM proxy cache hit"
                        f" (x-litellm-cache-key): request_id={self.request_id} model={self.llm_model}"
                    )
                    continue
                if gen_result.message and gen_result.message.usage_metadata:
                    usage_metadata: UsageMetadata = gen_result.message.usage_metadata
                    input_tokens += usage_metadata.get("input_tokens", 0)
                    output_tokens += usage_metadata.get("output_tokens", 0)
                    cached_tokens += usage_metadata.get("input_token_details", {}).get("cache_read", 0)
                    cache_creation_tokens += usage_metadata.get("input_token_details", {}).get("cache_creation", 0)

            if not (input_tokens or output_tokens):
                return

            model_costs = llm_service.get_model_cost(self.llm_model)
            money_spent, cached_tokens_money_spent, cached_tokens_creation_cost = calculate_token_cost(
                llm_model=self.llm_model,
                cost_config=model_costs,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                cache_creation_tokens=cache_creation_tokens,
            )

            llm_run = LLMRun(
                run_id=str(run_id),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                money_spent=money_spent,
                cached_tokens_money_spent=cached_tokens_money_spent,
                cached_tokens_creation_cost=cached_tokens_creation_cost,
                llm_model=self.llm_model,
            )
            request_summary_manager.update_llm_run(request_id=self.request_id, llm_run=llm_run)
        except Exception as e:
            logger.error(f"Error while handling LLM error token calculation: {str(e)}")

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Run when LLM starts running.

        Stashes a (router, decision) pair passed through the call's config metadata (see
        core/router_chat_model.py::RouterChatModel._agenerate), keyed by run_id, so on_llm_end
        can call that same router's extract()/extract_classifier_usage() — the only channel
        available, since response_metadata for this call doesn't exist yet at on_llm_end time
        (this callback fires on the concretely selected model itself, before _agenerate gets a
        chance to touch the response).
        """
        from codemie.core.router_chat_model import read_routing_ctx

        stashed = read_routing_ctx(metadata)
        if stashed is not None:
            self._pending[run_id] = stashed
