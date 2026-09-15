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

"""SwitchyardRouter — the Router-protocol adapter over ProxySwitchyardRouter.

Wraps the unchanged engine one-to-one: this file adds no routing logic of its own, it only
translates between the Router interface (core/router.py) and the engine's existing
pick_model()/capable_model/efficient_model surface. See engine.py for the actual algorithm.

Deliberately does not override Router.routing_info()'s billed_model handling — a Switchyard
capable/efficient target being itself a router (e.g. a LiteLLM auto-router) is rejected at
config/resolution time (see build_switchyard_routers in configs/llm_config.py,
get_proxy_switchyard_router in engine.py), so this router's own decision can never have been
re-routed further downstream; the inherited default (billed_model == routed_model) is
correct, not a gap.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from codemie.core.router import CallContext, Router
from codemie.core.routing_info import ClassifierUsage, RoutingInfo
from codemie.enterprise.switchyard.routing_meta import SwitchyardMeta

if TYPE_CHECKING:
    from codemie.core.router import RoutingDecision
    from codemie.enterprise.switchyard.engine import ProxySwitchyardRouter


def build_switchyard_routing_meta(decision: "RoutingDecision", *, capable_model: str) -> SwitchyardMeta:
    """Build the SwitchyardMeta (tier, requested/routed model, classifier cache-token detail)
    from a RoutingDecision plus the capable-model name this router's engine was configured
    with. ``capable_model`` is a separate parameter, not a RoutingDecision field: it's
    Switchyard's own "what tier would have run absent downgrade" bookkeeping, not part of the
    shared decision contract (see core/router.py's module docstring). Used only to populate
    RoutingInfo.meta — nothing outside this package needs to know these field/header names."""
    c = decision.classifier
    return SwitchyardMeta(
        requested_model=capable_model,
        tier=decision.tier,
        classifier_model=c.model if c else None,
        classifier_input_tokens=c.input_tokens if c else None,
        classifier_output_tokens=c.output_tokens if c else None,
        classifier_cached_tokens=c.cached_tokens if c else None,
        classifier_cache_creation_tokens=c.cache_creation_tokens if c else None,
        classifier_cost_usd=c.cost_usd if c else None,
        routed_model=decision.model,
    )


class SwitchyardRouter(Router):
    name = "switchyard"

    def __init__(self, engine: "ProxySwitchyardRouter") -> None:
        self._engine = engine

    async def decide(self, messages: list[dict[str, object]]) -> "RoutingDecision | None":
        return await self._engine.pick_model(messages)

    def candidate_models(self) -> Sequence[str]:
        return (self._engine.capable_model_deployment_name, self._engine.efficient_model_deployment_name)

    def build_routing_meta(self, decision: "RoutingDecision") -> Mapping[str, str]:
        """Proxy-path only: the full x-codemie-routing-* header set (tier, requested/routed
        model, classifier cache-token detail) — see build_switchyard_routing_meta. Overrides
        the Router default ({}) because Switchyard's RoutingInfo has more per-call detail than
        the two canonical fields (routed_model, classifier_cost_usd) carry on their own."""
        return build_switchyard_routing_meta(decision, capable_model=self._engine.capable_model).to_headers()

    def extract(self, response: LLMResult | AIMessage) -> RoutingInfo:
        """Read the canonical RoutingInfo that RouterChatModel._agenerate stamped onto the
        response after this router's own decide() ran — the only channel that's actually
        populated for the agent path (SwitchyardRouter.build_chat_model is the Router
        default, which always wraps in RouterChatModel)."""
        return RoutingInfo.from_response(response)

    def extract_classifier_usage(self, ctx: CallContext) -> ClassifierUsage | None:
        decision = ctx.decision
        if decision is None or decision.classifier is None:
            return None
        c = decision.classifier
        return ClassifierUsage(
            provider=self.name,
            input_tokens=max(0, c.input_tokens),
            output_tokens=max(0, c.output_tokens),
            cost_usd=c.cost_usd,
            model=c.model,
        )
