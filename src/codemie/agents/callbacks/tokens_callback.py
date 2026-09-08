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
from typing import TYPE_CHECKING, Any, NamedTuple, Optional
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import LLMResult

from codemie.configs import config, logger
from codemie.core.llm_cache import is_litellm_proxy_cache_hit, is_llm_cache_hit
from codemie.core.routing_info import RoutingInfo, compose_routing_info, default_routing_extractors
from codemie.core.utils import calculate_token_cost
from codemie.service.request_summary_manager import request_summary_manager, LLMRun
from codemie.service.llm_service.llm_service import llm_service

if TYPE_CHECKING:
    from codemie.enterprise.switchyard.decision import RoutingDecision


class TokensCalculationCallback(AsyncCallbackHandler):
    def __init__(self, request_id: str, llm_model: str) -> None:
        super().__init__()
        self.internal_run_id = str(uuid.uuid4())
        self.request_id = request_id
        self.llm_model = llm_model
        self.input_tokens = 0
        self.output_tokens = 0
        # Switchyard RoutingDecision objects passed in via a call's config={"metadata": {...}}
        # (see switchyard/agent.py::_agenerate), keyed by that call's run_id. This callback is
        # attached directly to the concretely selected capable/efficient model (get_llm_by_credentials
        # attaches it automatically), so on_llm_end for a given run fires before _agenerate has any
        # chance to attach routing data to the response — the config-metadata channel is the only
        # way the classifier sub-call's usage reaches this callback. Stashed in on_chat_model_start,
        # consumed and popped in on_llm_end/on_llm_error.
        self._pending_decisions: dict[UUID, "RoutingDecision"] = {}

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
    def _extract_classifier_usage(
        generation_info: dict,
    ) -> tuple[int, int, Optional[float], Optional[str], Optional[str]]:
        """Return (prompt_tokens, completion_tokens, cost, classifier_model, routed_model) from response headers.

        Headers are emitted by AutorouterCallback (x-litellm-classifier-* and
        x-litellm-router-classifier-model / x-litellm-router-routed-model) when the
        complexity autorouter runs a classifier sub-call.  Returns zeros/None/None when absent.
        """
        headers = generation_info.get("headers") or {}
        prompt_tokens = 0
        completion_tokens = 0
        cost: Optional[float] = None
        with contextlib.suppress(ValueError, TypeError):
            val = headers.get("x-litellm-classifier-prompt-tokens")
            if val is not None:
                prompt_tokens = int(val)
        with contextlib.suppress(ValueError, TypeError):
            val = headers.get("x-litellm-classifier-completion-tokens")
            if val is not None:
                completion_tokens = int(val)
        with contextlib.suppress(ValueError, TypeError):
            val = headers.get("x-litellm-classifier-cost")
            if val is not None:
                cost = float(val)
        classifier_model: Optional[str] = headers.get("x-litellm-router-classifier-model") or None
        routed_model: Optional[str] = headers.get("x-litellm-router-routed-model") or None
        return prompt_tokens, completion_tokens, cost, classifier_model, routed_model

    @staticmethod
    def _extract_routed_model_from_response_metadata(response_metadata: dict[str, object]) -> str | None:
        """Return the actual served model name, when the provider/proxy embeds it directly
        in response metadata (plain proxy responses; no router involved)."""
        proxy_model = response_metadata.get("model") or response_metadata.get("model_name")
        if proxy_model:
            return str(proxy_model)
        return None

    @staticmethod
    def _iter_gen_results(response: LLMResult):
        for gen in response.generations:
            yield from gen

    class _PerGenUsage(NamedTuple):
        billed_model: str | None
        proxy_cost: float | None
        clf_input_tokens: int
        clf_output_tokens: int
        clf_cost: float | None
        clf_model: str | None

    def _extract_proxy_and_classifier_usage(
        self,
        generation_info: dict,
        proxy_cost: Optional[float],
        clf_cost: Optional[float],
        clf_input_tokens: int,
        clf_output_tokens: int,
        clf_model: Optional[str],
    ) -> tuple[Optional[float], int, int, Optional[float], Optional[str], Optional[str]]:
        """Extract proxy cost and classifier usage from one generation_info dict.

        Returns (proxy_cost, clf_input_tokens, clf_output_tokens, clf_cost, clf_model, clf_routed_model).
        A no-op (values passed through unchanged, clf_routed_model=None) when proxy usage
        tracking is disabled. Split out of ``_process_gen_result_usage`` purely to keep that
        function's cognitive complexity within the project's Sonar threshold (S3776).
        """
        if not (config.LLM_PROXY_ENABLED and config.LLM_PROXY_TRACK_USAGE):
            return proxy_cost, clf_input_tokens, clf_output_tokens, clf_cost, clf_model, None
        if proxy_cost is None:
            proxy_cost = self._extract_proxy_cost(generation_info)
        clf_routed: Optional[str] = None
        # Classifier headers are present only on the first gen_result that carries them.
        if clf_cost is None and not clf_input_tokens and not clf_output_tokens:
            clf_input_tokens, clf_output_tokens, clf_cost, clf_model, clf_routed = self._extract_classifier_usage(
                generation_info
            )
        return proxy_cost, clf_input_tokens, clf_output_tokens, clf_cost, clf_model, clf_routed

    def _process_gen_result_usage(
        self,
        gen_result: Any,
        billed_model: str | None,
        proxy_cost: Optional[float],
        clf_cost: Optional[float],
        clf_input_tokens: int,
        clf_output_tokens: int,
        clf_model: Optional[str],
    ) -> "_PerGenUsage":
        """Extract billed-model, proxy cost, and classifier usage from one generation result."""
        if gen_result.generation_info:
            billed_model = gen_result.generation_info.get("model") or billed_model
            proxy_cost, clf_input_tokens, clf_output_tokens, clf_cost, clf_model, clf_routed = (
                self._extract_proxy_and_classifier_usage(
                    gen_result.generation_info, proxy_cost, clf_cost, clf_input_tokens, clf_output_tokens, clf_model
                )
            )
            billed_model = billed_model or clf_routed
        rm: dict[str, object] = getattr(getattr(gen_result, "message", None), "response_metadata", None) or {}
        if isinstance(rm, dict) and billed_model is None:
            billed_model = self._extract_routed_model_from_response_metadata(rm)
        return self._PerGenUsage(billed_model, proxy_cost, clf_input_tokens, clf_output_tokens, clf_cost, clf_model)

    @staticmethod
    def _apply_switchyard_classifier_fallback(
        decision: Optional["RoutingDecision"],
        clf_cost: Optional[float],
        clf_input_tokens: int,
        clf_output_tokens: int,
    ) -> tuple[int, int, Optional[float], Optional[str]]:
        """Fill classifier usage from a Switchyard decision when LiteLLM headers had nothing."""
        if decision is None or clf_cost is not None or clf_input_tokens or clf_output_tokens:
            return clf_input_tokens, clf_output_tokens, clf_cost, None
        sy_input = decision.classifier_input_tokens or 0
        sy_output = decision.classifier_output_tokens or 0
        sy_cost = decision.classifier_cost_usd
        if sy_input or sy_output or sy_cost is not None:
            return sy_input, sy_output, sy_cost, decision.classifier_model
        return clf_input_tokens, clf_output_tokens, clf_cost, None

    def _register_classifier_run(
        self,
        run_id: UUID,
        clf_input_tokens: int,
        clf_output_tokens: int,
        clf_cost: Optional[float],
        clf_model: Optional[str],
    ) -> None:
        """Record the classifier sub-call as its own billable LLMRun, when one was made.

        Applies to both routing mechanisms: LiteLLM's native complexity router (usage read
        from x-litellm-classifier-* / x-litellm-router-classifier-model headers) and
        Switchyard's classifier mode (usage read from the RoutingDecision passed in via
        on_chat_model_start's config metadata). Either source funnels into the same
        (clf_input_tokens, clf_output_tokens, clf_cost, clf_model) tuple before reaching here,
        so this method itself is routing-mechanism-agnostic.
        """
        if not (clf_input_tokens or clf_output_tokens or clf_cost is not None):
            return
        clf_money_spent, _, _ = self._calculate_cost(clf_cost, clf_input_tokens, clf_output_tokens, 0, 0)
        request_summary_manager.update_llm_run(
            request_id=self.request_id,
            llm_run=LLMRun(
                run_id=str(run_id) + "-classifier",
                input_tokens=clf_input_tokens,
                output_tokens=clf_output_tokens,
                money_spent=clf_money_spent,
                # Real classifier model name (e.g. "claude-4-5-haiku"); fall back to the request alias.
                llm_model=clf_model or self.llm_model,
            ),
        )

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
            # Pop first (even on the cache-hit early-return below) so a Switchyard decision
            # stashed in on_chat_model_start never lingers in self._pending_decisions.
            decision = self._pending_decisions.pop(run_id, None)

            if is_llm_cache_hit(response):
                logger.debug(
                    "Skipping LangGraph usage tracking for LiteLLM cache hit: "
                    f"request_id={self.request_id} model={self.llm_model} estimated_spend_skipped=unknown"
                )
                return
            input_tokens = 0
            output_tokens = 0
            cached_tokens = 0
            cache_creation_tokens = 0
            proxy_cost: Optional[float] = None

            billed_model: str | None = None
            clf_input_tokens = 0
            clf_output_tokens = 0
            clf_cost: Optional[float] = None
            clf_model: Optional[str] = None

            any_processed = False
            for gen_result in self._iter_gen_results(response):
                if gen_result.generation_info and is_litellm_proxy_cache_hit(gen_result.generation_info):
                    logger.debug(
                        "Skipping LangGraph usage tracking for LiteLLM proxy cache hit (x-litellm-cache-key): "
                        f"request_id={self.request_id} model={self.llm_model}"
                    )
                    continue
                any_processed = True
                if gen_result.message and gen_result.message.usage_metadata:  # type: ignore[union-attr]
                    usage_metadata: UsageMetadata = gen_result.message.usage_metadata  # type: ignore[union-attr]
                    input_tokens += usage_metadata.get("input_tokens", 0)
                    output_tokens += usage_metadata.get("output_tokens", 0)
                    cached_tokens += usage_metadata.get("input_token_details", {}).get("cache_read", 0)
                    cache_creation_tokens += usage_metadata.get("input_token_details", {}).get("cache_creation", 0)
                    logger.debug(f"On LLM End. Usage metadata: {usage_metadata}")
                # Classifier headers are present only on the first gen_result that carries them.
                billed_model, proxy_cost, clf_input_tokens, clf_output_tokens, clf_cost, clf_model = (
                    self._process_gen_result_usage(
                        gen_result, billed_model, proxy_cost, clf_cost, clf_input_tokens, clf_output_tokens, clf_model
                    )
                )

            # Switchyard's classifier sub-call usage: passed in via config metadata (see
            # switchyard/agent.py), not response_metadata — only consulted when the LiteLLM-router
            # header path above found nothing. The two routing mechanisms are mutually exclusive
            # per request, so at most one of them ever has data.
            clf_input_tokens, clf_output_tokens, clf_cost, sy_clf_model = self._apply_switchyard_classifier_fallback(
                decision, clf_cost, clf_input_tokens, clf_output_tokens
            )
            clf_model = clf_model or sy_clf_model

            if not any_processed:
                return

            # self.llm_model is already the concretely invoked model (this callback is attached
            # directly to it), so billed_model is only needed as an override for cases where the
            # proxy/router actually served a *different* model than the one this callback is
            # attached to (e.g. LiteLLM's own complexity router re-routing further downstream).
            money_spent, cached_tokens_money_spent, cached_tokens_creation_cost = self._calculate_cost(
                proxy_cost,
                input_tokens,
                output_tokens,
                cached_tokens,
                cache_creation_tokens,
                cost_model=billed_model or self.llm_model,
            )

            # DISPLAY routing (UI badge) is derived from the canonical composer, which
            # intentionally excludes a plain proxy `model` — unlike `billed_model` above,
            # which feeds the COST path and must reflect the actual billed model.
            display_routing = compose_routing_info(response, default_routing_extractors())

            llm_run = LLMRun(
                run_id=str(run_id),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                money_spent=money_spent,
                cached_tokens_money_spent=cached_tokens_money_spent,
                cached_tokens_creation_cost=cached_tokens_creation_cost,
                llm_model=self.llm_model,
                routing=RoutingInfo(
                    routed_model=display_routing.routed_model,
                    classifier_cost_usd=(
                        display_routing.classifier_cost_usd
                        if display_routing.classifier_cost_usd is not None
                        else clf_cost
                    ),
                ),
            )

            request_summary_manager.update_llm_run(request_id=self.request_id, llm_run=llm_run)

            self._register_classifier_run(run_id, clf_input_tokens, clf_output_tokens, clf_cost, clf_model)
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
        # Drop any pending decision for this run — it errored before on_llm_end could consume it.
        self._pending_decisions.pop(run_id, None)
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

        Stashes a Switchyard RoutingDecision passed through the call's config metadata (see
        switchyard/agent.py::_agenerate), keyed by run_id, so on_llm_end can pick up the
        classifier sub-call's usage — the only channel available, since response_metadata for
        this call doesn't exist yet at on_llm_end time (this callback fires on the concretely
        selected model itself, before _agenerate gets a chance to touch the response).
        """
        if not metadata:
            return
        from codemie.enterprise.switchyard.routing_meta import _SWITCHYARD_DECISION_METADATA_KEY

        decision = metadata.get(_SWITCHYARD_DECISION_METADATA_KEY)
        if decision is not None:
            self._pending_decisions[run_id] = decision
