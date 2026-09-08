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
    config.SWITCHYARD_ENABLED: master switch (default True); False disables all routing
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import switchyard.libsy as libsy

from codemie.configs import config, logger
from codemie.enterprise.switchyard.decision import RoutingDecision, RoutingTier
from codemie.enterprise.switchyard.llm_clients import _BASE_REQUEST, _ClassifierLlmClient, _RoutingLlmClient
from codemie.enterprise.switchyard.message_format import (
    _has_recent_compaction_marker,
    _strip_old_compaction_markers,
    _to_neutral_messages,
)

if TYPE_CHECKING:
    from codemie.configs.llm_config import SwitchyardConfig, SwitchyardTuning


_SWITCHYARD_ELIGIBLE_ENDPOINTS: frozenset[str] = frozenset(
    {
        "v1/messages",
        "v1/chat/completions",
    }
)


class _PyDecision(Protocol):
    """Structural type for libsy's opaque native (PyO3) decision object.

    libsy ships no Python type stubs, so this documents the subset of its
    interface pick_model() actually relies on, in place of Any. ``get_str``
    is deliberately NOT part of this protocol: older .so builds don't have
    it, so callers probe for it with getattr(decision, "get_str", None).
    """

    def get(self, key: str) -> Any: ...
    @property
    def selected_model_id(self) -> str: ...


def _decision_get(decision: _PyDecision, key: str, fallback: str | None = None) -> Any:
    """Read a scalar value from a PyDecision with an optional legacy fallback key."""
    value = decision.get(key)
    if value is None and fallback:
        value = decision.get(fallback)
    return value


def _decision_get_str(decision: _PyDecision, key: str, fallback: str | None = None) -> str | None:
    """Read a string value from a PyDecision with an optional legacy fallback key."""
    getter = getattr(decision, "get_str", None)
    if getter is None:
        return None
    value = getter(key)
    if value is None and fallback:
        value = getter(fallback)
    return value


def _compute_routing_confidence(
    classifier_used: bool,
    classifier_p_solve: float | None,
    signal_score: float | None,
) -> float | None:
    """Return routing confidence in [0, 1]: P(capable model needed).

    Classifier path: 1 - classifier_p_solve  (classifier_p_solve = P(efficient sufficient)).
    Signal path: (signal_score + 1) / 2  (score in (-1,+1), +1=capable, -1=efficient).
    """
    if classifier_used and classifier_p_solve is not None:
        return 1.0 - classifier_p_solve
    if signal_score is not None:
        return (signal_score + 1.0) / 2.0
    return None


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

    def _fallback_decision(self, decision_source: str) -> RoutingDecision:
        """Escalate to capable when routing can't run to completion (compaction/error/no-decision)."""
        return RoutingDecision(
            model=self.capable_model,
            tier=RoutingTier.CAPABLE,
            capable_model=self.capable_model,
            confidence=None,
            decision_source=decision_source,
        )

    def _build_classifier(self) -> tuple[_ClassifierLlmClient | None, libsy.LlmFallback | None]:
        if self.routing_mode != "classifier" or not self.tuning.classifier_model:
            return None, None
        classifier_client = _ClassifierLlmClient(self.tuning.classifier_model)
        classifier = libsy.LlmFallback(
            "classifier",
            config=libsy.TaskClassifierConfig(
                base_threshold=self.tuning.classifier_base_threshold,
                threshold_step=self.tuning.classifier_threshold_step,
                response_format_type="json_object",
            ),
        )
        return classifier_client, classifier

    async def _dispatch_call(self, call: libsy.ModelCall, classifier_client: _ClassifierLlmClient | None) -> None:
        """Answer one algorithm-issued model call (classifier or routing stub)."""
        target = call.models[0] if call.models else "efficient"
        try:
            if target == "classifier" and classifier_client:
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
        classifier_client: _ClassifierLlmClient | None,
    ) -> _PyDecision | None:
        """Drive the algorithm's step stream to completion, returning its last decision."""
        last_decision: _PyDecision | None = None
        async for step in algorithm.run_stream(routing_request):
            match step:
                case libsy.Step.CallModel(call=call):
                    await self._dispatch_call(call, classifier_client)
                case libsy.Step.Decision(decision=decision):
                    last_decision = decision
        return last_decision

    def _resolve_tier_and_model(self, selected_model_id: str) -> tuple[RoutingTier, str]:
        # Trust the algorithm's selected_model_id — it already applied the configured
        # confidence_threshold internally.  The FallThrough cascade always terminates
        # with a DefaultTarget (FallOpen) so last_decision always carries a selection.
        # selected_model_id itself is a raw string from the libsy native binding — its
        # target-name convention ("capable"/"efficient"), not our RoutingTier.
        if selected_model_id == "efficient":
            return RoutingTier.EFFICIENT, self.efficient_model_deployment_name
        return RoutingTier.CAPABLE, self.capable_model_deployment_name

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
            "capable",
            "efficient",
            picker="efficient_first",
            confidence_threshold=threshold,
            recent_window=self.tuning.recent_window,
            classifier=classifier,
        )

        try:
            last_decision = await self._consume_algorithm(algorithm, routing_request, classifier_client)
        except Exception as exc:
            logger.warning("[SWITCHYARD-PROXY] Algorithm routing failed: %s", exc)
            return self._fallback_decision("router_error")

        # Read usage directly from the per-call instance — no shared state involved.
        _c = classifier_client.usage if (classifier_client and classifier_client.usage.called) else None
        classifier_used: bool = classifier_client.usage.classifier_used if classifier_client else False
        classifier_confidence: float | None = None
        classifier_input_tokens: int | None = _c.input_tokens if _c else None
        classifier_output_tokens: int | None = _c.output_tokens if _c else None
        classifier_cached_tokens: int | None = _c.cached_tokens if _c else None
        classifier_cache_creation_tokens: int | None = _c.cache_creation_tokens if _c else None
        classifier_cost_usd: float | None = _c.cost_usd if _c else None

        if last_decision is None:
            return self._fallback_decision("no_decision")

        # Read routing metadata from state.extra.
        # Scalars via PyDecision.get(); strings via PyDecision.get_str() (requires rebuilt .so).
        signal_score: float | None = last_decision.get("signal_score")
        signal_confidence: float | None = last_decision.get("signal_confidence")
        signal_severity: float | None = last_decision.get("signal_severity")
        signal_spinning: float | None = last_decision.get("signal_spinning")
        signal_exploring: float | None = last_decision.get("signal_exploring")
        signal_production_intensity: float | None = last_decision.get("signal_production_intensity")
        classifier_p_solve: float | None = _decision_get(last_decision, "classifier_p_solve", "judge_p_solve")
        classifier_crux: str | None = _decision_get_str(last_decision, "classifier_crux", "judge_crux")
        classifier_primary_rule: str | None = _decision_get_str(
            last_decision, "classifier_primary_rule", "judge_primary_rule"
        )
        classifier_capability_boundary: str | None = _decision_get_str(
            last_decision, "classifier_capability_boundary", "judge_capability_boundary"
        )
        decision_source: str | None = _decision_get_str(last_decision, "decision_source")

        confidence = _compute_routing_confidence(classifier_used, classifier_p_solve, signal_score)

        tier, chosen = self._resolve_tier_and_model(last_decision.selected_model_id)

        logger.info(
            "[SWITCHYARD-PROXY] decision: tier=%s model=%r mode=%s threshold=%s "
            "classifier_tokens=%s/%s cached=%s creation=%s classifier_cost=%s "
            "signal_score=%s signal_confidence=%s classifier_p_solve=%s decision_source=%s",
            tier,
            chosen,
            self.routing_mode,
            threshold,
            classifier_input_tokens,
            classifier_output_tokens,
            classifier_cached_tokens,
            classifier_cache_creation_tokens,
            f"{classifier_cost_usd:.6f}" if classifier_cost_usd is not None else "n/a",
            signal_score,
            signal_confidence,
            classifier_p_solve,
            decision_source,
        )
        return RoutingDecision(
            model=chosen,
            tier=tier,
            capable_model=self.capable_model,
            confidence=confidence,
            classifier_used=classifier_used,
            classifier_model=self.tuning.classifier_model if classifier_used else None,
            classifier_confidence=classifier_confidence,
            classifier_input_tokens=classifier_input_tokens,
            classifier_output_tokens=classifier_output_tokens,
            classifier_cached_tokens=classifier_cached_tokens,
            classifier_cache_creation_tokens=classifier_cache_creation_tokens,
            classifier_cost_usd=classifier_cost_usd,
            signal_score=signal_score,
            signal_confidence=signal_confidence,
            signal_severity=signal_severity,
            signal_spinning=signal_spinning,
            signal_exploring=signal_exploring,
            signal_production_intensity=signal_production_intensity,
            classifier_p_solve=classifier_p_solve,
            classifier_crux=classifier_crux,
            classifier_primary_rule=classifier_primary_rule,
            classifier_capability_boundary=classifier_capability_boundary,
            decision_source=decision_source,
        )


def _get_model_deployment_name(base_name: str) -> str | None:
    """Return the deployment_name for a model base_name, preferring the live LiteLLM/DIAL
    catalog when available — matches the AGENT-path resolution in get_llm_by_credentials,
    so proxy-path and agent-path routing decisions target the same deployment."""
    from codemie.service.llm_service.llm_service import llm_service  # local import to avoid circular deps

    return llm_service.get_model_deployment_name(base_name)


def _get_switchyard_model_config(router_name: str) -> SwitchyardConfig | None:
    """Return the SwitchyardConfig for *router_name*, or None if not configured.

    Reads the effective router catalog (live LiteLLM/DIAL catalog when available,
    static YAML fallback otherwise — see LLMService.get_llm_routers).
    """
    if not config.SWITCHYARD_ENABLED:
        return None

    from codemie.service.llm_service.llm_service import llm_service  # local import to avoid circular deps

    for router in llm_service.get_llm_routers():
        if router.base_name == router_name and router.enabled:
            return router.switchyard
    return None


def get_proxy_switchyard_router(
    router_name: str,
) -> ProxySwitchyardRouter | None:
    """Return a fresh routing instance for the given router.

    Returns None if Switchyard is disabled or the router has no Switchyard
    configuration.  The router is stateless, so a new instance is created for
    every request.
    """
    sw_config = _get_switchyard_model_config(router_name)
    if sw_config is None:
        return None

    logger.debug(
        f"[SWITCHYARD-PROXY] New router: "
        f"capable={sw_config.capable_model!r} efficient={sw_config.efficient_model!r} mode={sw_config.mode}"
    )
    efficient_deployment = _get_model_deployment_name(sw_config.efficient_model)
    capable_deployment = _get_model_deployment_name(sw_config.capable_model)
    return ProxySwitchyardRouter(
        capable_model=sw_config.capable_model,
        efficient_model=sw_config.efficient_model,
        routing_mode=sw_config.mode,
        capable_model_deployment_name=capable_deployment or sw_config.capable_model,
        efficient_model_deployment_name=efficient_deployment or sw_config.efficient_model,
        tuning=sw_config.tuning,
    )


def is_switchyard_eligible_endpoint(endpoint: str) -> bool:
    """Return True for endpoints that support Switchyard model routing."""
    return endpoint.lstrip("/") in _SWITCHYARD_ELIGIBLE_ENDPOINTS
