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
        routing_mode: str,
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

        *reason* is logged (not stored — RoutingDecision carries no decision-source field)
        so the "no_decision" case, which has no other log line at its call site, still shows
        up in observability.
        """
        logger.info("[SWITCHYARD-PROXY] Fallback to capable: reason=%s", reason)
        return RoutingDecision(
            model=self.capable_model,
            tier=RoutingTier.CAPABLE,
        )

    def _build_classifier(self) -> tuple[_ClassifierLlmClient | None, libsy.LlmFallback | None]:
        if self.routing_mode != "classifier" or not self.tuning.classifier_model:
            return None, None
        classifier_client = _ClassifierLlmClient(self.tuning.classifier_model)
        classifier = libsy.LlmFallback(
            config=libsy.TaskClassifierConfig(
                base_threshold=self.tuning.classifier_base_threshold,
                threshold_step=self.tuning.classifier_threshold_step,
                response_format_type="json_object",
            ),
        )
        return classifier_client, classifier

    async def _dispatch_call(self, call: libsy.ModelCall, classifier_client: _ClassifierLlmClient | None) -> None:
        """Answer one algorithm-issued model call. call.models carries the literal model ID(s)
        this call targets (drawn from the `models` mapping passed to run_stream) — the judge's
        classifier_model when the algorithm is invoking the classifier, or capable_model/
        efficient_model when it's probing a routing candidate (e.g. for context-window fit)."""
        target = call.models[0] if call.models else self.efficient_model
        try:
            if classifier_client is not None and target == self.tuning.classifier_model:
                response = await classifier_client.call(call.request)
            else:
                response = await self._routing_client.call(call.request)
            # The native bindings require an LlmResponse wrapper, not a raw dict.
            llm_response = libsy.LlmResponse.Agg(response)
            try:
                call.respond(llm_response)
            except Exception as respond_exc:
                logger.warning("[SWITCHYARD-PROXY] call.respond failed for target=%s: %s", target, respond_exc)
                call.fail(respond_exc)
        except Exception as call_exc:
            logger.warning("[SWITCHYARD-PROXY] call failed for target=%s: %s", target, call_exc)
            call.fail(call_exc)

    async def _consume_algorithm(
        self,
        algorithm: libsy.Algorithm,
        routing_request: dict[str, object],
        models: dict[str, list[str]],
        classifier_client: _ClassifierLlmClient | None,
    ) -> libsy.RoutingOutcome | None:
        """Drive the algorithm's step stream to completion, returning its final outcome."""
        outcome: libsy.RoutingOutcome | None = None
        async for step in algorithm.run_stream(routing_request, models):
            match step:
                case libsy.Step.CallModel(call=call):
                    await self._dispatch_call(call, classifier_client)
                case libsy.Step.Done(outcome=done_outcome):
                    outcome = done_outcome
        return outcome

    def _resolve_tier_and_model(self, selected_model_id: str) -> tuple[RoutingTier, str]:
        # Trust the algorithm's selection — it already applied the configured
        # confidence_threshold internally.  The FallThrough cascade always terminates
        # with a DefaultTarget (FallOpen) so outcome.selected_model_ids always carries a
        # selection. selected_model_id is one of the literal capable_model/efficient_model
        # strings passed via the `models` mapping in run_stream — not a role name.
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

        classifier_client, classifier = self._build_classifier()
        algorithm: libsy.Algorithm = libsy.algorithms.stage_router(
            picker="efficient_first",
            confidence_threshold=threshold,
            recent_window=self.tuning.recent_window,
            classifier=classifier,
        )
        models: dict[str, list[str]] = {
            "capable": [self.capable_model],
            "efficient": [self.efficient_model],
            "any": [self.capable_model, self.efficient_model],
        }
        if classifier_client is not None and self.tuning.classifier_model:
            models["judge"] = [self.tuning.classifier_model]

        try:
            outcome = await self._consume_algorithm(algorithm, routing_request, models, classifier_client)
        except Exception as exc:
            logger.warning("[SWITCHYARD-PROXY] Algorithm routing failed: %s", exc)
            return self._fallback_decision("router_error")

        usage, classifier_used = self._resolve_classifier_usage(classifier_client)
        classifier_call = self._build_classifier_call(usage) if classifier_used else None

        if outcome is None or not outcome.selected_model_ids:
            return self._fallback_decision("no_decision")

        tier, chosen = self._resolve_tier_and_model(outcome.selected_model_ids[0])
        self._log_decision(tier, chosen, threshold, classifier_call)
        return RoutingDecision(
            model=chosen,
            tier=tier,
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
