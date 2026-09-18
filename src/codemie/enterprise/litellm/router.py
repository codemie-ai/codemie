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

"""LiteLLMRouter — the Router-protocol adapter for LiteLLM's own (externally configured)
auto-routing signals.

Lives here, not in core/router.py, because every method that does real work (extract,
extract_classifier_usage) reads LiteLLM-specific x-litellm-* headers via
LiteLLMRouterHeaders/_iter_header_maps — this is enterprise/litellm logic, not core. Mirrors
SwitchyardRouter's placement in enterprise/switchyard/router.py: core/router.py hosts only the
Router interface, its provider-neutral defaults, and NullRouter (which needs none of this);
create_router() lazily imports this module the same way it lazily imports SwitchyardRouter, so
core keeps its documented zero import-time (or runtime) dependency on either concrete package.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from codemie.core.router import CallContext, Router
from codemie.core.routing_info import (
    ClassifierUsage,
    RoutingInfo,
    normalize_decision_source,
)

if TYPE_CHECKING:
    from codemie.core.router import RoutingDecision
    from codemie.core.router_chat_model import LLMParams


def normalize_litellm_tier(raw: str | None) -> str | None:
    """Translate LiteLLM's tier names into the canonical routing vocabulary."""
    if raw is None:
        return None
    return str(raw).strip().lower()


def _parse_signals(raw: str | None) -> tuple[str | None, str | None]:
    """Read LiteLLM's optional ``source:tier`` signal fallback."""
    if not raw:
        return None, None
    try:
        values = json.loads(raw)
        values = values if isinstance(values, list) else [values]
    except (TypeError, json.JSONDecodeError):
        values = [raw]

    for value in values:
        if not isinstance(value, str) or ":" not in value:
            continue
        source, tier = value.strip().split(":", 1)
        return source, tier
    return None, None


def routing_info_from_headers(headers: Mapping[str, object]) -> RoutingInfo:
    """Build the canonical LiteLLM routing value from one response-header map."""
    from codemie.enterprise.litellm.litellm_router_headers import LiteLLMRouterHeaders

    meta = LiteLLMRouterHeaders.from_headers(headers)
    signal_source, signal_tier = _parse_signals(meta.signals)
    raw_tier = meta.tier or signal_tier
    decision_source = normalize_decision_source(meta.cause or signal_source)
    has_routing_metadata = any(
        value is not None
        for value in (
            meta.tier,
            meta.cause,
            meta.score,
            meta.routed_model,
            meta.router_model_name,
            meta.router_type,
            meta.signals,
            meta.classifier_model,
            meta.classifier_prompt_tokens,
            meta.classifier_completion_tokens,
            meta.classifier_total_tokens,
            meta.classifier_cost_usd,
        )
    )
    if decision_source == "llm-classifier":
        routing_source = "judge"
    elif decision_source:
        routing_source = "stage_router"
    else:
        routing_source = None
    return RoutingInfo(
        routed_model=meta.routed_model,
        requested_model=meta.router_model_name,
        classifier_cost_usd=meta.classifier_cost_usd,
        tier=normalize_litellm_tier(raw_tier),
        routing_tier_raw=raw_tier,
        decision_source=decision_source,
        routing_source=routing_source,
        confidence=meta.score,
        routing_family="litellm" if has_routing_metadata else None,
        routing_cost_known=meta.classifier_cost_usd is not None if has_routing_metadata else None,
        classifier_model=meta.classifier_model,
        router_type=meta.router_type,
        router_score=meta.score,
        classifier_input_tokens=meta.classifier_prompt_tokens,
        classifier_output_tokens=meta.classifier_completion_tokens,
        classifier_total_tokens=meta.classifier_total_tokens,
    )


class LiteLLMRouter(Router):
    """Never decides synchronously. Recognizes LiteLLM's own routing signals whenever they
    appear in a response, which happens identically whether or not this model_name carries a
    litellm_router declaration — LiteLLM's server-side behavior doesn't care about CodeMie's
    catalog. `name` exists only to distinguish future catalog-display purposes, not to change
    runtime behavior."""

    routing_family = "litellm"

    def __init__(self, name: str) -> None:
        self.name = name

    async def decide(self, messages: list[dict[str, object]]) -> RoutingDecision | None:
        return None

    def extract(self, response: LLMResult | AIMessage) -> RoutingInfo:
        from codemie.enterprise.litellm.routing_headers import _iter_header_maps

        info = RoutingInfo()
        # _iter_header_maps already deduplicates by object identity, so a headers dict that
        # LangChain stashed in both generation_info and response_metadata is yielded once —
        # important because merged_over sums the additive classifier fields.
        for headers in _iter_header_maps(response):
            info = info.merged_over(routing_info_from_headers(headers))
        return info

    def extract_classifier_usage(self, ctx: CallContext) -> ClassifierUsage | None:
        """Read LiteLLM's own classifier sub-call usage off response headers (ctx.headers) —
        the proxy-path/agent-path-agnostic counterpart to SwitchyardRouter's decision-based
        override. LiteLLM's classifier headers (x-litellm-classifier-*) appear identically
        whether or not this model_name carries a litellm_router declaration, matching this
        class's own extract() behavior above."""
        if not ctx.headers:
            return None
        from codemie.enterprise.litellm.litellm_router_headers import LiteLLMRouterHeaders

        meta = LiteLLMRouterHeaders.from_headers(ctx.headers)
        input_tokens = max(0, meta.classifier_prompt_tokens or 0)
        output_tokens = max(0, meta.classifier_completion_tokens or 0)
        if not (input_tokens or output_tokens or meta.classifier_cost_usd is not None):
            return None
        return ClassifierUsage(
            provider=self.name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=meta.classifier_cost_usd,
            model=meta.classifier_model,
        )

    def build_chat_model(self, *, model_name: str, request_id: str, llm_params: "LLMParams") -> BaseChatModel:
        """Override the Router default: unlike Switchyard's synthetic base_name, LiteLLM's
        auto-router alias IS the deployable model_name — the proxy's own server-side fan-out is
        keyed on this exact name, so there is exactly one real candidate to build, and it's
        model_name itself. Still wrapped in RouterChatModel (not returned as a raw client) so
        the post-call extract() stamp (see RouterChatModel._agenerate) runs through the one
        canonical path every router uses, instead of a bespoke metadata channel for just this
        mechanism."""
        from codemie.core.dependecies import get_llm_by_credentials
        from codemie.core.router_chat_model import RouterChatModel

        llm = get_llm_by_credentials(
            llm_model=model_name, request_id=request_id, temperature=llm_params.temperature, top_p=llm_params.top_p
        )
        return RouterChatModel(router=self, candidates={model_name: llm}, default_model=model_name)


_LITELLM_COMPLEXITY_ROUTER = LiteLLMRouter(name="litellm_complexity")
