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

"""Switchyard proxy-level LLM routing via libsy.algorithms.stage_router.

Routing is computed **before** each LLM call from the incoming request's
conversation history.  The algorithm's stage_router extracts ToolSignals
(tool call names, tool result error patterns, turn depth) from the request
messages, scores them, and returns the tier decision synchronously.

No per-session state is accumulated: stage_router's ToolSignalProcessor
reads its signals entirely from the request's message history, so each
decision is fully determined by the current conversation context.  A fresh
ProxySwitchyardRouter is created for every request because the router is
stateless (the classifier client is per-call, and the routing stub client
is a dummy).

Configuration:
    config.SWITCHYARD_ENABLED: master switch (default False); False disables all routing
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

import switchyard.libsy as libsy

from codemie.configs import config, logger
from codemie.core.router import ClassifierCall, RoutingDecision
from codemie.enterprise.switchyard.llm_clients import _BASE_REQUEST, _ClassifierLlmClient, _RoutingLlmClient
from codemie.enterprise.switchyard.message_format import (
    _has_recent_compaction_marker,
    _strip_old_compaction_markers,
    _to_neutral_messages,
)

if TYPE_CHECKING:
    from codemie.configs.llm_config import SwitchyardConfig, SwitchyardTuning
    from codemie.enterprise.switchyard.llm_clients import _ClassifierUsage


class RoutingTier(StrEnum):
    """Which candidate model a Switchyard routing decision selected. Switchyard-owned, not
    part of the shared RoutingDecision contract (core/router.py) — RoutingDecision.tier is a
    plain str precisely because tier naming/meaning is a per-router concept, and today
    Switchyard is the only router with tiers at all. StrEnum members are themselves str
    instances, so they satisfy RoutingDecision.tier's str type directly, with no cast."""

    CAPABLE = "capable"
    EFFICIENT = "efficient"


def _decision_source_for(classifier_call: ClassifierCall | None) -> str:
    """Which mechanism decided a non-fallback RoutingDecision: the LLM classifier sub-call, or
    signal-based heuristics alone. Single source of truth for this, set at decide()-time —
    SwitchyardRouter.routing_info() used to recompute the same thing locally from
    decision.classifier; now it just reads decision.decision_source instead."""
    return "llm_classifier" if classifier_call is not None else "heuristic"


class ProxySwitchyardRouter:
    """Stateless Switchyard routing backed by libsy.algorithms.stage_router.

    Each call to `pick_model` scores the current conversation history and
    returns a RoutingDecision.  All routing metadata is returned inline — no
    mutable instance state — so concurrent requests for the same session
    cannot corrupt each other's confidence or reason fields.
    """

    def __init__(
        self,
        capable_model: str,
        efficient_model: str,
        routing_mode: str | None,
        *,
        capable_model_deployment_name: str,
        efficient_model_deployment_name: str,
        tuning: SwitchyardTuning,
    ) -> None:
        self.capable_model = capable_model
        self.efficient_model = efficient_model
        self.routing_mode = routing_mode
        self.capable_model_deployment_name = capable_model_deployment_name
        self.efficient_model_deployment_name = efficient_model_deployment_name
        self.tuning = tuning
        # Routing stub used by libsy stage_router when it asks for a dummy completion.
        self._routing_client = _RoutingLlmClient()

    def _fallback_decision(self, reason: str) -> RoutingDecision:
        """Escalate to capable when routing can't run to completion (compaction/error/no-decision).

        *reason* becomes RoutingDecision.decision_source, so the "no_decision" case — which has
        no other log line at its call site — now shows up in routing metadata, not just logs.
        """
        logger.info("[SWITCHYARD-PROXY] Fallback to capable: reason=%s", reason)
        return RoutingDecision(
            model=self.capable_model,
            tier=RoutingTier.CAPABLE,
            decision_source=reason,
            routing_family="switchyard",
        )

    def _build_classifier(self) -> tuple[_ClassifierLlmClient | None, libsy.LlmFallback | None]:
        if self.routing_mode != "classifier" or not self.tuning.classifier_model:
            return None, None
        classifier_client = _ClassifierLlmClient(self.tuning.classifier_model)
        judge_target = libsy.LlmTarget(self.tuning.classifier_model, classifier_client)
        classifier = libsy.LlmFallback(
            judge_target,
            config=libsy.TaskClassifierConfig(
                base_threshold=self.tuning.classifier_base_threshold,
                threshold_step=self.tuning.classifier_threshold_step,
            ),
        )
        return classifier_client, classifier

    @staticmethod
    def _extract_selected_model(trace: list[dict[str, object]]) -> str | None:
        """The FallThrough cascade always terminates with a DefaultTarget (FallOpen), so
        trace[-1]['selected_model'] carries the final decision whenever algorithm.run()
        returns without raising — mirrors the old outcome.selected_model_ids[0] guarantee.
        Guards against a non-dict trace element or an empty selection, either of which
        should be treated as "no decision" rather than crashing or silently escalating."""
        if not trace:
            return None
        last = trace[-1]
        selected = last.get("selected_model") if isinstance(last, dict) else None
        return selected if isinstance(selected, str) and selected else None

    def _resolve_tier_and_model(self, selected_model_id: str) -> tuple[RoutingTier, str]:
        # Trust the algorithm's selection — it already applied the configured
        # confidence_threshold internally.  The FallThrough cascade always terminates
        # with a DefaultTarget (FallOpen) so the run() trace always carries a selection.
        # selected_model_id is one of the literal capable_model/efficient_model strings
        # passed as the `name` of the corresponding LlmTarget — not a role name.
        if selected_model_id == self.efficient_model:
            return RoutingTier.EFFICIENT, self.efficient_model_deployment_name
        return RoutingTier.CAPABLE, self.capable_model_deployment_name

    def _resolve_classifier_usage(
        self, classifier_client: "_ClassifierLlmClient | None"
    ) -> tuple["_ClassifierUsage | None", bool]:
        """Read usage directly from the per-call instance — no shared state involved."""
        usage = classifier_client.usage if (classifier_client and classifier_client.usage.called) else None
        classifier_used = classifier_client.usage.classifier_used if classifier_client else False
        return usage, classifier_used

    def _build_classifier_call(self, usage: "_ClassifierUsage | None") -> ClassifierCall:
        input_tokens = usage.input_tokens if usage else 0
        output_tokens = usage.output_tokens if usage else 0
        cached_tokens = usage.cached_tokens if usage else None
        cache_creation_tokens = usage.cache_creation_tokens if usage else None
        cost_usd = usage.cost_usd if usage else None
        return ClassifierCall(
            model=self.tuning.classifier_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cost_usd=cost_usd,
        )

    def _log_decision(
        self,
        tier: RoutingTier,
        chosen: str,
        threshold: float,
        classifier_call: ClassifierCall | None,
    ) -> None:
        cost = classifier_call.cost_usd if classifier_call else None
        logger.info(
            "[SWITCHYARD-PROXY] decision: tier=%s model=%r mode=%s threshold=%s "
            "classifier_tokens=%s/%s cached=%s creation=%s classifier_cost=%s",
            tier,
            chosen,
            self.routing_mode,
            threshold,
            classifier_call.input_tokens if classifier_call else None,
            classifier_call.output_tokens if classifier_call else None,
            classifier_call.cached_tokens if classifier_call else None,
            classifier_call.cache_creation_tokens if classifier_call else None,
            f"{cost:.6f}" if cost is not None else "n/a",
        )

    async def pick_model(
        self,
        messages: list[dict[str, object]],
    ) -> RoutingDecision | None:
        """Score the current conversation and return a routing decision.

        Args:
            messages: The full message history from the incoming request.
                      The algorithm's ToolSignalProcessor scans these for
                      tool call names and tool result error patterns.

        Returns:
            RoutingDecision with the chosen model, scorer reason, confidence,
            and classifier token/cost metadata when the classifier was invoked.
            Returns None when routing_mode is None.
        """
        if self.routing_mode is None:
            return None

        threshold = self.tuning.signal_threshold if self.routing_mode == "signal" else self.tuning.classifier_threshold

        # Post-compaction: the algorithm has no ToolSignals to work with (the
        # session history was summarized into a text block), so escalate to
        # capable immediately rather than letting the algorithm return no
        # decisions and fall back to efficient.
        if _has_recent_compaction_marker(messages):
            logger.info("[SWITCHYARD-PROXY] Post-compaction detected; escalating to capable")
            return self._fallback_decision("compaction")

        neutral = _to_neutral_messages(_strip_old_compaction_markers(messages))
        routing_request: dict[str, object] = {**_BASE_REQUEST, "messages": neutral}

        logger.info(
            "[SWITCHYARD-PROXY] pick_model input: turns=%d mode=%s threshold=%s",
            len(neutral),
            self.routing_mode,
            threshold,
        )

        classifier_client: _ClassifierLlmClient | None = None
        try:
            classifier_client, classifier = self._build_classifier()
            capable_target = libsy.LlmTarget(self.capable_model, self._routing_client)
            efficient_target = libsy.LlmTarget(self.efficient_model, self._routing_client)
            algorithm: libsy.Algorithm = libsy.algorithms.stage_router(
                capable_target,
                efficient_target,
                picker="efficient_first",
                confidence_threshold=threshold,
                recent_window=self.tuning.recent_window,
                classifier=classifier,
            )
            trace, _response = await algorithm.run(routing_request)
        except Exception as exc:
            logger.warning("[SWITCHYARD-PROXY] Algorithm routing failed: %s", exc)
            return self._fallback_decision("router_error")

        usage, classifier_used = self._resolve_classifier_usage(classifier_client)
        classifier_call = self._build_classifier_call(usage) if classifier_used else None

        selected_model_id = self._extract_selected_model(trace)
        if selected_model_id is None:
            return self._fallback_decision("no_decision")

        tier, chosen = self._resolve_tier_and_model(selected_model_id)
        self._log_decision(tier, chosen, threshold, classifier_call)
        return RoutingDecision(
            model=chosen,
            tier=tier,
            decision_source=_decision_source_for(classifier_call),
            routing_family="switchyard",
            classifier=classifier_call,
        )


def _resolve_switchyard_setup(router_name: str) -> tuple[SwitchyardConfig, str, str] | None:
    """Resolve everything get_proxy_switchyard_router needs from the live catalog in one pass:
    the router's SwitchyardConfig, plus its capable/efficient deployment names — or None if
    Switchyard is disabled, *router_name* has no Switchyard configuration, either target does
    not resolve to a real model in the live catalog, or either target is itself resolvable as a
    router (a Switchyard alias, via is_router_model, or a declared LiteLLM auto-router). Both
    checks run against the live catalog, not just the static YAML build_switchyard_routers
    already validates against — this is defense in depth against catalog drift, mirroring the
    `details is not None and ...` guard style already used in router_factory.create_router.
    """
    if not config.SWITCHYARD_ENABLED:
        return None

    from codemie.service.llm_service.llm_service import llm_service  # local import to avoid circular deps

    sw_config: SwitchyardConfig | None = None
    for router in llm_service.get_llm_routers():
        if router.base_name == router_name and router.enabled:
            sw_config = router.switchyard
            break
    if sw_config is None:
        return None

    deployments: dict[str, str] = {}
    for role, target in (("capable", sw_config.capable_model), ("efficient", sw_config.efficient_model)):
        deployment = llm_service.get_model_deployment_name(target)
        if deployment is None:
            logger.warning(
                "[SWITCHYARD-PROXY] %r's %s model %r not found in the current catalog; skipping Switchyard for %r.",
                router_name,
                role,
                target,
                router_name,
            )
            return None
        details = llm_service.get_model_details(target)
        if llm_service.is_router_model(target) or (details is not None and details.is_declared_litellm_router()):
            logger.error(
                "[SWITCHYARD-PROXY] Router-on-router not supported: %r's %s model is itself a "
                "router; skipping Switchyard for %r.",
                router_name,
                role,
                router_name,
            )
            return None
        deployments[role] = deployment

    return sw_config, deployments["capable"], deployments["efficient"]


def get_proxy_switchyard_router(
    router_name: str,
) -> ProxySwitchyardRouter | None:
    """Return a fresh routing instance for the given router.

    Returns None if Switchyard is disabled or the router has no Switchyard
    configuration.  The router is stateless, so a new instance is created for
    every request.
    """
    setup = _resolve_switchyard_setup(router_name)
    if setup is None:
        return None
    sw_config, capable_deployment, efficient_deployment = setup

    logger.debug(
        f"[SWITCHYARD-PROXY] New router: "
        f"capable={sw_config.capable_model!r} efficient={sw_config.efficient_model!r} mode={sw_config.mode}"
    )
    return ProxySwitchyardRouter(
        capable_model=sw_config.capable_model,
        efficient_model=sw_config.efficient_model,
        routing_mode=sw_config.mode,
        capable_model_deployment_name=capable_deployment,
        efficient_model_deployment_name=efficient_deployment,
        tuning=sw_config.tuning,
    )
