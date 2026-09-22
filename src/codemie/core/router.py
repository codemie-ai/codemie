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

"""The ``Router`` interface — the shared abstraction that decides and declares routing
metadata across routing mechanisms — plus the value types its contract is built on:
``RoutingDecision`` (the result of a single decide() call, genuinely shared by both the proxy
and agent paths — not owned by any one mechanism) and ``CallContext`` (per-call correlation
state passed to extract_classifier_usage()).

``Router`` knows nothing about HTTP or LangChain. ``routing_info(decision, header_maps)`` is
its one and only way to report what it knows: each concrete implementation decides internally
which of the two inputs it actually needs, deterministically, based on its own ``decide()``
contract — LiteLLM's ``decide()`` always returns ``None`` (see its own docstring), so its
``routing_info()`` never reads ``decision``; Switchyard's ``decide()`` returns a real decision
except on failure, and LiteLLM never echoes anything Switchyard-specific back in response
headers, so its ``routing_info()`` never reads ``header_maps``. There is no dispatcher outside
the router deciding which input "wins" — callers always pass both, unconditionally and lazily
(``header_maps`` is an ``Iterable``, typically a generator — a router implementation that never
consumes it costs its caller nothing).

Building a LangChain chat model for a resolved Router is not this module's job either — see
``build_chat_model_for()`` in ``core/router_chat_model.py``, which consumes a ``Router`` via
``candidate_models()`` instead of the router constructing its own LangChain wrapper.

``RoutingDecision.tier`` is a plain ``str``, not an enum owned by this module: "tier" naming
(e.g. Switchyard's "capable"/"efficient") is meaningful only to the router that produced a
given decision — see ``enterprise/switchyard/engine.py::RoutingTier`` for the one router that
currently has tiers at all. For the same reason ``RoutingDecision`` carries no
``capable_model`` field: that was Switchyard's own "what tier would have run absent
downgrade" bookkeeping, not something every decide-capable router has an equivalent of.
Switchyard forwards its tier into ``RoutingInfo``'s own typed ``tier`` field instead —
see ``SwitchyardRouter.routing_info``. ``RoutingInfo.requested_model`` itself always comes
from ``Router.router_name`` (see that attribute's own docstring), not from tier bookkeeping.

Also hosts ``NullRouter``/``NULL_ROUTER`` — the trivial "no routing at all" identity. It
lives here rather than in an enterprise package because it is genuinely mechanism-agnostic:
zero LiteLLM or Switchyard knowledge, just the Null Object for this interface.

The other concrete routers (``SwitchyardRouter``, ``LiteLLMRouter``) live in their owning
enterprise packages and are resolved via ``create_router()``
(``codemie.service.llm_service.router_factory``) — that factory, not this module, is where
catalog/service-layer lookups happen. This module stays a leaf: nothing here imports anything
under ``codemie.enterprise``, and — since ``build_chat_model()`` moved to
``core/router_chat_model.py`` — nothing here imports LangChain at all.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from codemie.core.routing_info import ClassifierUsage, RoutingInfo


# Endpoints whose request body carries a routable `messages` array — true for any
# decide()-capable Router, not just Switchyard (moved out of enterprise/switchyard/engine.py,
# which never had anything mechanism-specific in this set to begin with). Consulted by
# RouterProxySession (core/router_proxy_session.py) before running decide() at all, so a
# request to an unrelated endpoint never pays for a decide() call it can't use.
_ROUTABLE_ENDPOINTS: frozenset[str] = frozenset(
    {
        "v1/messages",
        "v1/chat/completions",
    }
)


def is_routable_endpoint(endpoint: str) -> bool:
    """Return True for endpoints whose request body a Router can inspect/rewrite."""
    return endpoint.lstrip("/") in _ROUTABLE_ENDPOINTS


@dataclass(frozen=True)
class ClassifierCall:
    """One router's own classifier sub-call, as observed at decide()-time — a separate,
    Optional nested value, not flat fields on RoutingDecision. Unlike ``model``/``tier``, not
    every decide()-capable router makes a classifier sub-call at all (today only Switchyard
    does; LiteLLM's own auto-router never returns a RoutingDecision in the first place — see
    LiteLLMRouter.decide()), so this cluster belongs behind an Optional, router-owned value,
    the same treatment ``tier`` already gets. Also replaces the previous
    ``classifier_used: bool`` field, which had to be kept in sync by hand with six separate
    Optional fields — presence of a ``ClassifierCall`` now *is* "classifier was used"."""

    model: str | None = None  # name of the model used for the classifier sub-call
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class RoutingDecision:
    """Result of a single Router.decide() call — immutable, safe to pass across async
    boundaries. Shared by both the proxy and agent paths; not specific to any one Router
    implementation, even though today SwitchyardRouter is the only one that populates it.

    ``tier`` is a bare ``str`` deliberately, not an enum owned by this module: which tier
    names exist (and what they mean) is entirely up to the producing router — see the module
    docstring. ``decision_source``/``routing_family`` are required for the same reason
    ``tier`` isn't optional: every producer of a RoutingDecision knows both by construction
    time (which mechanism it is, and why this particular decision was reached), so allowing
    either to silently default to None would only reintroduce the data loss this field exists
    to prevent — see enterprise/switchyard/engine.py's ``_fallback_decision``, which used to
    drop its own fallback reason for exactly this reason."""

    model: str
    tier: str
    decision_source: str
    routing_family: str
    classifier: ClassifierCall | None = None


@dataclass
class CallContext:
    """Whatever correlatable state is available for one LLM call, passed uniformly to
    Router.extract_classifier_usage(). Not every field is populated by every caller: the
    agent path fills `decision` (stashed via RunnableConfig.metadata, see tokens_callback.py);
    the proxy path fills `headers` (real HTTP response headers)."""

    run_id: str
    decision: RoutingDecision | None = None
    headers: Mapping[str, str] | None = None


class Router(ABC):
    """One instance per routing context (once per proxy request; once per agent LLM
    construction) for the routers that actually carry per-call state — threaded through the
    whole flow by two dedicated per-path orchestrators, ``RouterChatModel`` (agent path,
    ``core/router_chat_model.py``) and ``RouterProxySession`` (proxy path,
    ``core/router_proxy_session.py``). Neither orchestrator is this module's concern: ``Router``
    itself never imports either. The ONLY concrete implementations are SwitchyardRouter
    (enterprise/switchyard/router.py), LiteLLMRouter (enterprise/litellm/router.py), and
    NullRouter (below) — never subclassed anywhere else. Resolve one via create_router()
    (codemie.service.llm_service.router_factory), never construct directly."""

    name: str
    routing_family: str
    # The catalog alias this router was resolved for (the model_name create_router() was
    # called with) — every router's own identity, independent of decide()/routing_info(). The
    # single source for RoutingInfo.requested_model, so every mechanism reports the same kind
    # of "what did the caller ask for" value instead of each inventing its own stand-in.
    router_name: str
    counterfactual_model: str | None = None

    @abstractmethod
    async def decide(self, messages: list[dict[str, object]]) -> RoutingDecision | None:
        """Return a decision if this router can decide synchronously, before the call.
        None means "I cannot decide up front" — always the case for LiteLLM's auto-router,
        whose algorithm runs inside the external LiteLLM process, after the request is sent."""

    def routing_info(
        self, decision: RoutingDecision | None, header_maps: Iterable[Mapping[str, object]]
    ) -> RoutingInfo:
        """Report whatever this router knows about one call's routing, from whichever of the
        two inputs it actually needs — a synchronous decide()-time decision, post-call
        response headers, or (for every router today) only ever one of the two, never both.
        Each concrete override decides internally which input to read, deterministically,
        based on its own ``decide()`` contract (see the module docstring); callers always pass
        both, unconditionally.

        This default — available to any future minimal decide()-capable router that doesn't
        need bespoke field population (NullRouter overrides it instead, since it must stay
        empty even when a decision is present) — builds a RoutingInfo directly from
        ``decision`` when one exists (no response needed, so it's usable synchronously the
        moment decide() returns), and never reads ``header_maps``.
        ``routing_family`` is read from the decision, not from ``self.routing_family`` — the
        decision is the single source of truth once one exists (see RoutingDecision's own
        docstring)."""
        if decision is None:
            return RoutingInfo()
        return RoutingInfo(
            routed_model=decision.model,
            requested_model=self.router_name,
            classifier_cost_usd=decision.classifier.cost_usd if decision.classifier else None,
            routing_family=decision.routing_family,
        )

    def extract_classifier_usage(self, ctx: CallContext) -> ClassifierUsage | None:
        """Read this router's own classifier sub-call usage, tagged with this router's own
        identity (ClassifierUsage.provider). Default: no classifier sub-call, so None."""
        return None

    def candidate_models(self) -> Sequence[str]:
        """Concrete models this router might select between (agent path pre-builds a
        LangChain client per candidate via build_chat_model_for). Empty for routers whose
        decide() is always None and which have no self-referential single candidate either
        (e.g. NullRouter). See LiteLLMRouter.candidate_models() for the one exception where a
        decide()-less router still reports a (single) candidate: itself."""
        return ()


class NullRouter(Router):
    """The "no routing at all" identity — used whenever create_router() finds no Switchyard
    or LiteLLM-auto-router declaration for a model. Genuinely mechanism-agnostic: unlike the
    old PassiveRouter this replaces, it carries zero enterprise knowledge — decide() never has
    anything to decide, and routing_info() never has anything to report, regardless of what
    is passed in: overridden (rather than inheriting the base default) so that even a
    RoutingDecision-shaped value handed to it by a confused caller is still reported as
    empty — "no routing at all" is an identity, not merely a consequence of decide() always
    returning None."""

    name = "none"
    routing_family = "none"
    router_name = "none"

    async def decide(self, messages: list[dict[str, object]]) -> RoutingDecision | None:
        return None

    def routing_info(
        self, decision: RoutingDecision | None, header_maps: Iterable[Mapping[str, object]]
    ) -> RoutingInfo:
        return RoutingInfo()


NULL_ROUTER = NullRouter()
