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

Lives here, not in core/router.py, because routing_info() and extract_classifier_usage() both
read LiteLLM-specific x-litellm-* headers via LiteLLMRouterHeaders — this is enterprise/litellm
logic, not core. Mirrors SwitchyardRouter's placement in enterprise/switchyard/router.py:
core/router.py hosts only the Router interface, its provider-neutral defaults, and NullRouter
(which needs none of this); create_router() lazily imports this module the same way it lazily
imports SwitchyardRouter, so core keeps its documented zero import-time (or runtime)
dependency on either concrete package.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from codemie.core.router import CallContext, Router
from codemie.core.routing_info import (
    ClassifierUsage,
    RoutingInfo,
    normalize_decision_source,
)

if TYPE_CHECKING:
    from codemie.core.router import RoutingDecision


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
        # LiteLLM's own savings baseline wins over the catalog counterfactual_model the
        # router was built with (None here keeps that one in LiteLLMRouter.routing_info()).
        counterfactual_model=meta.savings_baseline_model_group,
    )


class LiteLLMRouter(Router):
    """Never decides synchronously. Recognizes LiteLLM's own routing signals whenever they
    appear in a response headers map, which happens identically whether or not this
    model_name carries a litellm_router declaration — LiteLLM's server-side behavior doesn't
    care about CodeMie's catalog.

    Resolved fresh per alias by create_router() (never cached/shared — see router_factory.py):
    ``name``/``router_name`` and ``counterfactual_model`` all vary per resolved alias, so a
    single shared instance would leak one alias's identity/pricing baseline onto every other."""

    routing_family = "litellm"

    def __init__(self, name: str, *, counterfactual_model: str | None = None) -> None:
        self.name = name
        self.router_name = name
        self.counterfactual_model = counterfactual_model

    async def decide(self, messages: list[dict[str, object]]) -> "RoutingDecision | None":
        return None

    def candidate_models(self) -> tuple[str]:
        """LiteLLM's auto-router alias IS the deployable model_name — the proxy's own
        server-side fan-out is keyed on this exact name, so there is exactly one real
        candidate, and it's this router's own resolved alias (self.name). Unlike
        SwitchyardRouter, whose candidates are two OTHER concrete deployments, this router's
        sole "candidate" is itself — build_chat_model_for's generic logic (see
        core/router_chat_model.py) still wraps it in RouterChatModel with exactly this one
        candidate, so the post-call routing_info() stamp runs through the one canonical path
        every router uses."""
        return (self.name,)

    def routing_info(
        self, decision: "RoutingDecision | None", header_maps: Iterable[Mapping[str, object]]
    ) -> RoutingInfo:
        """``decision`` is never read — this router's decide() always returns None (see
        decide()'s own docstring), so a real decision is never in hand for it to consult; the
        caller passes it anyway, unconditionally, for interface uniformity with
        SwitchyardRouter. ``requested_model`` comes from ``self`` (router-owned state, set once
        at construction — see router_factory.py) unconditionally: every call to a declared
        LiteLLM auto-router alias is routed by LiteLLM and comes back with routing headers, so
        there is no "was this even routed" case to guard for here. ``counterfactual_model`` is
        LiteLLM's own savings baseline when the headers carry one, so CodeMie prices the same
        counterfactual LiteLLM's savings dashboard does; ``self.counterfactual_model`` (from the
        catalog) is only the fallback. Every other field comes from merging
        ``routing_info_from_headers()`` over each map in ``header_maps``."""
        info = RoutingInfo(requested_model=self.router_name)
        for headers in header_maps:
            info = info.merged_over(routing_info_from_headers(headers))
        if info.counterfactual_model is None:
            info = info.model_copy(update={"counterfactual_model": self.counterfactual_model})
        return info

    def extract_classifier_usage(self, ctx: CallContext) -> ClassifierUsage | None:
        """Read LiteLLM's own classifier sub-call cost off response headers (ctx.headers) —
        the proxy-path/agent-path-agnostic counterpart to SwitchyardRouter's decision-based
        override. Token counts are always 0: LiteLLM's routing_decision carries only the
        classifier's cost, and its tokens are not correlated back to the parent response."""
        if not ctx.headers:
            return None
        from codemie.enterprise.litellm.litellm_router_headers import LiteLLMRouterHeaders

        meta = LiteLLMRouterHeaders.from_headers(ctx.headers)
        if meta.classifier_cost_usd is None:
            return None
        return ClassifierUsage(
            provider=self.name,
            input_tokens=0,
            output_tokens=0,
            cost_usd=meta.classifier_cost_usd,
            model=meta.classifier_model,
        )
