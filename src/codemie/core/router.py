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

"""The ``Router`` interface — the shared abstraction that decides, extracts, and (agent
path) constructs chat models across routing mechanisms — plus the value types its contract
is built on: ``RoutingDecision`` (the result of a single decide() call, genuinely shared by
both the proxy and agent paths — not owned by any one mechanism) and ``CallContext``
(per-call correlation state passed to extract_classifier_usage()).

``RoutingDecision.tier`` is a plain ``str``, not an enum: "tier" naming (e.g. Switchyard's
"capable"/"efficient") is meaningful only to the router that produced a given decision, not
to this shared contract — see ``enterprise/switchyard/engine.py::RoutingTier`` for the one
router that currently has tiers at all. For the same reason ``RoutingDecision`` carries no
``capable_model`` field: that was Switchyard's own "what tier would have run absent
downgrade" bookkeeping, not something every decide-capable router has an equivalent of.
Switchyard forwards it (and its tier) into ``RoutingInfo``'s own typed ``requested_model``/
``tier`` fields instead — see ``SwitchyardRouter.routing_info``.

Also hosts ``NullRouter``/``NULL_ROUTER`` — the trivial "no routing at all" identity. It
lives here rather than in an enterprise package because it is genuinely mechanism-agnostic:
zero LiteLLM or Switchyard knowledge, just the Null Object for this interface.

The other concrete routers (``SwitchyardRouter``, ``LiteLLMRouter``) live in their owning
enterprise packages and are resolved via ``create_router()``
(``codemie.service.llm_service.router_factory``) — that factory, not this module, is where
catalog/service-layer lookups happen. This module stays a leaf: the only same-package
reference is a single lazy import of ``RouterChatModel`` inside ``build_chat_model``'s
default, needed because ``core/router_chat_model.py`` itself imports ``Router`` from here.
Unlike the ``PassiveRouter`` this replaces, nothing here ever imports anything under
``codemie.enterprise`` — not even lazily.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from codemie.core.routing_info import ClassifierUsage, RoutingInfo

if TYPE_CHECKING:
    from langchain_core.language_models import LanguageModelInput
    from langchain_core.runnables import Runnable

    from codemie.core.router_chat_model import LLMParams


# Endpoints whose request body carries a routable `messages` array — true for any
# decide()-capable Router, not just Switchyard (moved out of enterprise/switchyard/engine.py,
# which never had anything mechanism-specific in this set to begin with). Consulted by
# apply_router_routing() (enterprise/switchyard/proxy.py) before running create_router() at
# all, so a request to an unrelated endpoint never pays for a router resolution it can't use.
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
    whole flow: decide, extract, classifier usage, billed model, and (agent path) chat-model
    construction. The ONLY concrete implementations are SwitchyardRouter
    (enterprise/switchyard/router.py), LiteLLMRouter (enterprise/litellm/router.py), and
    NullRouter (below) — never subclassed anywhere else. NullRouter and LiteLLMRouter are
    stateless, so their instances (NULL_ROUTER / the LiteLLM singletons) are shared across
    requests rather than freshly constructed — safe only because they hold no per-call state.
    Resolve one via create_router() (codemie.service.llm_service.router_factory), never
    construct directly."""

    name: str
    routing_family: str

    @abstractmethod
    async def decide(self, messages: list[dict[str, object]]) -> RoutingDecision | None:
        """Return a decision if this router can decide synchronously, before the call.
        None means "I cannot decide up front" — always the case for LiteLLM's auto-router,
        whose algorithm runs inside the external LiteLLM process, after the request is sent."""

    @abstractmethod
    def extract(self, response: LLMResult | AIMessage) -> RoutingInfo:
        """The no-synchronous-decision-available path: read this router's own routing
        fingerprint back out of a LangChain response, after the fact. Used only where no
        RoutingDecision was ever in hand at the call site — AgentInvokeCallback/
        AgentStreamingCallback (which only ever see the final response, never a decision) and
        LiteLLMRouter (whose decide() always returns None, see its own docstring, so this is
        its only channel). NOT a general-purpose "get routing info from anywhere a response is
        available" call: SwitchyardRouter.extract() only returns non-empty once
        RouterChatModel._agenerate has stamped the canonical RoutingInfo onto the response
        (core/routing_info.py::stamp_routing_info) — a callback attached directly to the
        underlying candidate model (e.g. TokensCalculationCallback, see
        core/dependecies.py:get_llm_by_credentials) fires *before* that stamp exists, so it
        must prefer routing_info(decision) below whenever it already has a decision in hand,
        and fall back to this method only when it doesn't. The proxy path never calls this
        either — it already has the decision object and calls routing_info() directly."""

    def extract_classifier_usage(self, ctx: CallContext) -> ClassifierUsage | None:
        """Read this router's own classifier sub-call usage, tagged with this router's own
        identity (ClassifierUsage.provider). Default: no classifier sub-call, so None."""
        return None

    def candidate_models(self) -> Sequence[str]:
        """Concrete models this router might select between (agent path pre-builds a
        LangChain client per candidate). Empty for routers whose decide() is always None."""
        return ()

    def routing_info(self, decision: RoutingDecision) -> RoutingInfo:
        """Canonical RoutingInfo built directly from a decision — no response needed, so it's
        usable synchronously the moment decide() returns, unlike extract() (which needs a
        response object and, for SwitchyardRouter, needs RouterChatModel to have already
        stamped it — see extract()'s docstring). Prefer this over extract() at every call site
        that already has a RoutingDecision in hand (RouterChatModel._agenerate,
        apply_router_routing, TokensCalculationCallback when a decision was stashed); fall back
        to extract() only where no decision was ever available synchronously.

        ``routing_family`` is read from the decision, not from ``self.routing_family`` —
        the decision is the single source of truth once one exists (see RoutingDecision's own
        docstring); ``self.routing_family`` exists only for routers whose decide() can return
        None (LiteLLMRouter), which never reach this method with a real decision anyway."""
        return RoutingInfo(
            routed_model=decision.model,
            classifier_cost_usd=decision.classifier.cost_usd if decision.classifier else None,
            routing_family=decision.routing_family,
        )

    def build_chat_model(self, *, model_name: str, request_id: str, llm_params: "LLMParams") -> BaseChatModel:
        """Agent-path entry point.

        candidate_models() is the signal for whether this router's decide() can ever return
        something other than None (see that method's docstring): empty means it can't, so
        wrapping in RouterChatModel would be pure overhead and would break
        with_structured_output() for the raw provider client (used across a wide swath of the
        codebase — assistant_agent.py, structured_tool_agent.py, every workflow_generator
        node). Non-empty means decide() can genuinely pick between candidates, so every
        candidate is pre-built and wrapped in RouterChatModel.

        LiteLLMRouter overrides this default entirely (see its own build_chat_model): its
        decide() is always None, but it still wraps in RouterChatModel — with exactly one
        candidate, model_name itself — purely so the post-call extract() stamp in
        RouterChatModel._agenerate runs, instead of needing a bespoke metadata channel.

        ``model_name`` itself is never one of the built candidates: whenever candidate_models()
        is non-empty, ``model_name`` is the router's own alias (e.g. a Switchyard
        "<capable>-switchyard-<efficient>-<mode>" name — see create_router()/is_router_model()),
        not a deployable model. Building/exposing a client for it would only add a redundant,
        self-referential entry to RouterChatModel.candidates (and to its "[ROUTING] ...
        candidates=" log line) that decide() can never actually select — its RoutingDecision.model
        is always one of candidate_models()'s own entries. default_model is set to the first
        candidate instead, so the decide()-returned-None fallback path picks a real target
        (by convention the higher-quality/fail-open one — see SwitchyardRouter.candidate_models(),
        which orders capable before efficient).
        """
        from codemie.core.dependecies import get_llm_by_credentials

        candidates = self.candidate_models()
        if not candidates:
            return get_llm_by_credentials(
                llm_model=model_name, request_id=request_id, temperature=llm_params.temperature, top_p=llm_params.top_p
            )

        from codemie.core.router_chat_model import RouterChatModel

        llms: dict[str, BaseChatModel | Runnable[LanguageModelInput, AIMessage]] = {
            m: get_llm_by_credentials(
                llm_model=m, request_id=request_id, temperature=llm_params.temperature, top_p=llm_params.top_p
            )
            for m in candidates
        }
        return RouterChatModel(router=self, candidates=llms, default_model=candidates[0])


class NullRouter(Router):
    """The "no routing at all" identity — used whenever create_router() finds no Switchyard
    or LiteLLM-auto-router declaration for a model. Genuinely mechanism-agnostic: unlike the
    old PassiveRouter this replaces, it carries zero enterprise knowledge, not even a lazy
    import — decide() never has anything to decide, and extract() never has any
    router-specific signal to read back out of a response.

    Stateless, so shared as a module-level singleton (NULL_ROUTER) rather than constructed
    per request — see the Router docstring."""

    name = "none"
    routing_family = "none"

    async def decide(self, messages: list[dict[str, object]]) -> RoutingDecision | None:
        return None

    def extract(self, response: LLMResult | AIMessage) -> RoutingInfo:
        return RoutingInfo()


NULL_ROUTER = NullRouter()
