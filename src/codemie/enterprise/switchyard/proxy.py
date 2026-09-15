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

"""Proxy-side Router entry point plus routing-metadata SSE injection.

This module sits between the LiteLLM HTTP proxy (`proxy_router.py`) and whichever concrete
Router `create_router()` resolves for a given request. It is Switchyard-owned only by
location, not by content: `apply_router_routing()` below runs any decide()-capable Router
(SwitchyardRouter today; any future one for free), mutates the request body when a different
model is chosen, and builds the RoutingInfo injected into response headers/SSE
`message_start`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from codemie.configs import logger
from codemie.core.router import RoutingDecision

if TYPE_CHECKING:
    from codemie.core.routing_info import RoutingInfo


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


def _routing_info_to_headers(info: RoutingInfo) -> dict[str, str]:
    """Canonical fields plus the opaque meta passthrough. Canonical fields are applied last
    so they stay authoritative if a meta key happens to collide with one of them."""
    headers: dict[str, str] = dict(info.meta)
    if info.routed_model is not None:
        headers["x-codemie-routed-model"] = info.routed_model
    if info.classifier_cost_usd is not None:
        headers["x-codemie-routing-classifier-cost-usd"] = f"{info.classifier_cost_usd:.6g}"
    return headers


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


async def apply_router_routing(
    endpoint: str,
    router_name: str,
    request_body: dict[str, object] | None,
    body_bytes: bytes,
) -> tuple[bytes, dict[str, object] | None, RoutingDecision | None, RoutingInfo | None]:
    """Apply Router-based model routing to a proxy request.

    Resolves a Router via create_router(), runs it when the requested endpoint is routable,
    rewrites the request body model if a different tier is chosen, and builds the RoutingInfo
    directly from the decision (NOT via Router.extract() — that method is agent-path-only; the
    proxy path already has the decision in hand and never needs to read it back out of a
    response).

    Returns:
        (updated_body_bytes, updated_request_body, routing_decision, routing_info).
        routing_decision and routing_info are None when routing was not performed.
    """
    from codemie.core.router import is_routable_endpoint
    from codemie.service.llm_service.router_factory import create_router

    if not is_routable_endpoint(endpoint) or not request_body:
        return body_bytes, request_body, None, None

    router = create_router(router_name)
    raw_messages = request_body.get("messages", [])
    messages: list[dict[str, object]] = (
        [m for m in raw_messages if isinstance(m, dict)] if isinstance(raw_messages, list) else []
    )
    decision = await router.decide(messages)
    if decision is None:
        return body_bytes, request_body, None, None

    chosen_model = decision.model
    if chosen_model != router_name:
        request_body["model"] = chosen_model
        body_bytes = json.dumps(request_body).encode("utf-8")
        logger.debug("[ROUTING-PROXY] Override %r -> %r", router_name, chosen_model)
    else:
        logger.debug("[ROUTING-PROXY] Keeping %r", chosen_model)

    routing_info = router.routing_info(decision)
    return body_bytes, request_body, decision, routing_info
