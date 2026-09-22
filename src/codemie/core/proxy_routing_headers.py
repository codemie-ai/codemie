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

"""Routing-metadata header codec plus SSE injection for the proxy path — the client-facing
counterpart to `RouterProxySession` (`core/router_proxy_session.py`). Shared by any
Router-routed proxy response (`enterprise/litellm/proxy_router.py`) regardless of which
mechanism produced the RoutingInfo: building the routing decision itself (resolving a Router,
running decide(), rewriting the request body) is `RouterProxySession`'s job, not this module's
— this module only ever turns an already-built RoutingInfo into wire-visible HTTP headers or
SSE metadata.

Lives in `core` rather than under either `enterprise/switchyard` or `enterprise/litellm`
because it is genuinely mechanism-agnostic: it has never imported anything from either
enterprise package, and moving it out of `enterprise/switchyard/proxy.py` (its original
location, a placement accident predating the Router mechanism-unification effort — see
docs/superpowers/specs/2026-09-21-router-path-decoupling-design.md) removes the last
proxy-path artifact that lived under a mechanism-specific package for no content reason.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from codemie.core.routing_info import encode_header_value

if TYPE_CHECKING:
    from codemie.core.routing_info import RoutingInfo

_ROUTING_HEADER_FIELDS: dict[str, str] = {
    "requested_model": "x-codemie-requested-model",
    "tier": "x-codemie-routing-tier",
    "decision_source": "x-codemie-routing-decision-source",
    "routing_source": "x-codemie-routing-source",
    "router_type": "x-codemie-routing-router-type",
    "routing_family": "x-codemie-routing-family",
    "classifier_model": "x-codemie-routing-classifier-model",
    "classifier_input_tokens": "x-codemie-routing-classifier-input-tokens",
    "classifier_output_tokens": "x-codemie-routing-classifier-output-tokens",
    "classifier_cached_tokens": "x-codemie-routing-classifier-cached-tokens",
    "classifier_cache_creation_tokens": "x-codemie-routing-classifier-cache-creation-tokens",
    "counterfactual_model": "x-codemie-routing-counterfactual-model",
}


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
        event_data: dict[str, object] = json.loads(lines[data_idx][6:])
        message = event_data.get("message")
        if not isinstance(message, dict):
            return event_bytes
        message.update(routing_meta)
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
    """Build the canonical x-codemie-routing-* header set directly from RoutingInfo's typed
    fields — one vocabulary regardless of which Router produced the info. routed_model/
    classifier_cost_usd get their own headers unconditionally when present — they're
    RoutingInfo's two always-canonical fields (see that field's docstring)."""
    headers: dict[str, str] = {}
    for field_name, header_name in _ROUTING_HEADER_FIELDS.items():
        value = getattr(info, field_name)
        if value is not None:
            headers[header_name] = encode_header_value(str(value))
    if info.routed_model is not None:
        headers["x-codemie-routed-model"] = encode_header_value(info.routed_model)
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
