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

"""Proxy-side Switchyard model-routing integration.

This module sits between the LiteLLM HTTP proxy (`proxy_router.py`) and the
Switchyard routing engine (`engine.py`). It runs the router
when the requested capable model has a Switchyard configuration, mutates the
request body when a cheaper model is chosen, and builds the routing metadata
injected into responses.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from codemie.configs import logger

from .decision import RoutingDecision
from .engine import get_proxy_switchyard_router, is_switchyard_eligible_endpoint
from codemie.enterprise.switchyard.routing_meta import SwitchyardMeta


_MODELS_WITHOUT_ADAPTIVE_THINKING: frozenset[str] = frozenset(
    {
        "claude-haiku-4-5-20251001",
    }
)


def _strip_adaptive_thinking(request_body: dict[str, object], model: str) -> bool:
    """Remove adaptive thinking params from request_body when model doesn't support them.

    Mutates request_body in place. Returns True if anything was stripped.
    """
    if model not in _MODELS_WITHOUT_ADAPTIVE_THINKING:
        return False
    stripped = False
    if "thinking" in request_body:
        del request_body["thinking"]
        stripped = True
    return stripped


def _inject_routing_into_message_start(
    event_bytes: bytes,
    routing_meta: dict[str, str],
) -> bytes:
    """Inject routing_meta into the `message` object of an SSE `message_start` event."""
    try:
        text = event_bytes.decode("utf-8", errors="replace")
        lines = text.splitlines()
        event_line = next((ln for ln in lines if ln.startswith("event: ")), None)
        if event_line is None or event_line[7:].strip() != "message_start":
            return event_bytes
        data_idx = next((i for i, ln in enumerate(lines) if ln.startswith("data: ")), None)
        if data_idx is None:
            return event_bytes
        event_data: dict = json.loads(lines[data_idx][6:])
        if not isinstance(event_data.get("message"), dict):
            return event_bytes
        event_data["message"].update(routing_meta)
        lines[data_idx] = "data: " + json.dumps(event_data, separators=(",", ":"))
        return "\n".join(lines).encode("utf-8")
    except Exception:
        return event_bytes


def _process_buffered_sse_events(
    buf: bytearray,
    routing_meta: dict[str, str],
) -> tuple[list[bytes], bool]:
    """Consume complete SSE events currently in buf, injecting routing_meta into message_start.

    Mutates buf in place (removing consumed events). Returns (chunks_to_yield, injected).
    """
    chunks: list[bytes] = []
    while b"\n\n" in buf:
        pos = buf.find(b"\n\n")
        event_bytes = bytes(buf[:pos])
        del buf[: pos + 2]
        if b"event: message_start" in event_bytes:
            chunks.append(_inject_routing_into_message_start(event_bytes, routing_meta) + b"\n\n")
            if buf:
                chunks.append(bytes(buf))
                buf.clear()
            return chunks, True
        chunks.append(event_bytes + b"\n\n")
    return chunks, False


async def with_routing_metadata_stream(
    source: AsyncIterator[bytes],
    routing_meta: dict[str, str],
) -> AsyncIterator[bytes]:
    """Yield chunks from source, injecting routing_meta into the first SSE message_start event.

    Scans past blank lines and non-message_start events (keep-alive comments, etc.)
    that some LiteLLM backends send before the Anthropic message_start event.
    """
    buf = bytearray()
    injected = False
    async for chunk in source:
        if injected:
            yield chunk
            continue
        buf.extend(chunk)
        chunks, just_injected = _process_buffered_sse_events(buf, routing_meta)
        for c in chunks:
            yield c
        injected = injected or just_injected
        if not injected and buf:
            yield bytes(buf)
            buf = bytearray()
    if not injected and buf:
        yield bytes(buf)


def build_switchyard_routing_meta(routing_decision: RoutingDecision) -> SwitchyardMeta:
    """Build SwitchyardMeta from a RoutingDecision."""
    return SwitchyardMeta(
        requested_model=routing_decision.capable_model,
        tier=routing_decision.tier,
        decision_source=routing_decision.decision_source or None,
        confidence=routing_decision.confidence,
        classifier_model=routing_decision.classifier_model,
        classifier_input_tokens=routing_decision.classifier_input_tokens,
        classifier_output_tokens=routing_decision.classifier_output_tokens,
        classifier_cached_tokens=routing_decision.classifier_cached_tokens,
        classifier_cache_creation_tokens=routing_decision.classifier_cache_creation_tokens,
        classifier_cost_usd=routing_decision.classifier_cost_usd,
        classifier_p_solve=routing_decision.classifier_p_solve,
        classifier_crux=routing_decision.classifier_crux or None,
        classifier_primary_rule=routing_decision.classifier_primary_rule or None,
        classifier_capability_boundary=routing_decision.classifier_capability_boundary or None,
        signal_score=routing_decision.signal_score,
        signal_confidence=routing_decision.signal_confidence,
        signal_severity=routing_decision.signal_severity,
        signal_spinning=routing_decision.signal_spinning,
        signal_exploring=routing_decision.signal_exploring,
        signal_production=routing_decision.signal_production_intensity,
    )


async def apply_switchyard_proxy_routing(
    endpoint: str,
    router_name: str,
    request_body: dict[str, object] | None,
    body_bytes: bytes,
) -> tuple[bytes, dict[str, object] | None, RoutingDecision | None, SwitchyardMeta | None]:
    """Apply Switchyard model routing to a proxy request.

    Runs the Switchyard router when the requested model is a configured router,
    rewrites the request body model if a different tier is chosen, strips
    unsupported parameters, and builds the routing metadata dict.

    Returns:
        (updated_body_bytes, updated_request_body, routing_decision, routing_meta).
        routing_decision and routing_meta are None when routing was not performed.
    """
    if not is_switchyard_eligible_endpoint(endpoint) or not request_body:
        return body_bytes, request_body, None, None

    router = get_proxy_switchyard_router(router_name=router_name)
    if router is None:
        return body_bytes, request_body, None, None

    raw_messages = request_body.get("messages", [])
    messages: list[dict[str, object]] = (
        [m for m in raw_messages if isinstance(m, dict)] if isinstance(raw_messages, list) else []
    )
    routing_decision = await router.pick_model(messages)
    if routing_decision is None:
        return body_bytes, request_body, None, None

    chosen_model = routing_decision.model
    body_dirty = False
    if chosen_model != router_name:
        request_body["model"] = chosen_model
        body_dirty = True
        logger.debug("[SWITCHYARD-PROXY] Override %r -> %r", router_name, chosen_model)
    else:
        logger.debug("[SWITCHYARD-PROXY] Keeping %r", chosen_model)

    if _strip_adaptive_thinking(request_body, chosen_model):
        body_dirty = True
        logger.debug("[SWITCHYARD-PROXY] Stripped adaptive thinking for model=%r", chosen_model)

    if body_dirty:
        body_bytes = json.dumps(request_body).encode("utf-8")

    routing_meta = build_switchyard_routing_meta(routing_decision)
    routing_meta.routed_model = routing_decision.model  # actual chosen model
    return body_bytes, request_body, routing_decision, routing_meta
