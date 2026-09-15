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
LiteLLMRouterMeta/_iter_header_maps — this is enterprise/litellm logic, not core. Mirrors
SwitchyardRouter's placement in enterprise/switchyard/router.py: core/router.py hosts only the
Router interface, its provider-neutral defaults, and NullRouter (which needs none of this);
create_router() lazily imports this module the same way it lazily imports SwitchyardRouter, so
core keeps its documented zero import-time (or runtime) dependency on either concrete package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from codemie.core.router import CallContext, Router
from codemie.core.routing_info import ClassifierUsage, RoutingInfo

if TYPE_CHECKING:
    from codemie.core.router import RoutingDecision
    from codemie.core.router_chat_model import LLMParams


class LiteLLMRouter(Router):
    """Never decides synchronously. Recognizes LiteLLM's own routing signals whenever they
    appear in a response, which happens identically whether or not this model_name carries a
    litellm_router declaration — LiteLLM's server-side behavior doesn't care about CodeMie's
    catalog. `name` exists only to distinguish future catalog-display purposes, not to change
    runtime behavior."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def decide(self, messages: list[dict[str, object]]) -> RoutingDecision | None:
        return None

    def extract(self, response: LLMResult | AIMessage) -> RoutingInfo:
        from codemie.enterprise.litellm.litellm_router_meta import LiteLLMRouterMeta
        from codemie.enterprise.litellm.routing_headers import _iter_header_maps

        info = RoutingInfo()
        for headers in _iter_header_maps(response):
            meta = LiteLLMRouterMeta.from_headers(headers)
            merged = RoutingInfo(routed_model=meta.routed_model, classifier_cost_usd=meta.classifier_cost_usd)
            info = info.merged_over(merged)
        return info

    def extract_classifier_usage(self, ctx: CallContext) -> ClassifierUsage | None:
        """Read LiteLLM's own classifier sub-call usage off response headers (ctx.headers) —
        the proxy-path/agent-path-agnostic counterpart to SwitchyardRouter's decision-based
        override. LiteLLM's classifier headers (x-litellm-classifier-*) appear identically
        whether or not this model_name carries a litellm_router declaration, matching this
        class's own extract() behavior above."""
        if not ctx.headers:
            return None
        from codemie.enterprise.litellm.litellm_router_meta import LiteLLMRouterMeta

        meta = LiteLLMRouterMeta.from_headers(ctx.headers)
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
