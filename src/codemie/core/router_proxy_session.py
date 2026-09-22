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

"""RouterProxySession — the proxy-path analog of RouterChatModel (core/router_chat_model.py):
owns the entire Router-eligible lifecycle of one HTTP proxy request — resolving the router,
running decide() and rewriting the request body if it decided, and (once the downstream
response exists) reporting the final RoutingInfo. enterprise/litellm/proxy_router.py interacts
only with this session object, never with Router or create_router() directly — mirroring how
agent-path code only ever talks to RouterChatModel. Turning the RoutingInfo this session
reports into wire-visible HTTP headers or SSE metadata is core/proxy_routing_headers.py's job,
not this class's.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from codemie.configs import logger
from codemie.core.router import Router, RoutingDecision, is_routable_endpoint
from codemie.core.routing_info import RoutingInfo


class RouterProxySession:
    def __init__(self, router: Router) -> None:
        self._router = router
        self._decision: RoutingDecision | None = None

    @classmethod
    def for_request(cls, router_name: str) -> RouterProxySession:
        """Always resolves create_router(router_name) and always succeeds — even for a
        NullRouter, or for a request whose endpoint CodeMie itself never synchronously
        routes. Unlike a router that resolves to nothing routable, there is no None return
        here: routing_info() must still get a chance to surface a LiteLLM auto-router's own
        signal from response headers even when decide_and_rewrite() is never called at all
        (see that method's own endpoint/body guard) — a NullRouter's routing_info() simply
        returns empty regardless, so a caller that skips the endpoint check gets a harmless
        empty result, not a crash."""
        from codemie.service.llm_service.router_factory import create_router

        return cls(create_router(router_name))

    @property
    def decision(self) -> RoutingDecision | None:
        return self._decision

    async def decide_and_rewrite(
        self, endpoint: str, request_body: dict[str, object] | None, body_bytes: bytes
    ) -> tuple[bytes, dict[str, object] | None]:
        """Run decide() and rewrite request_body["model"] if a different concrete model was
        chosen. No-ops (decision stays None, body untouched) when the endpoint doesn't carry
        a routable `messages` body or there is no request body at all — deciding needs both,
        even though resolving the router itself (see for_request()) does not.

        When the model changes, *request_body* is mutated in place (matching the pre-existing
        apply_router_routing behavior this replaces, since removed) — callers must treat the
        dict they passed in as no longer reliable afterward and use the returned one instead."""
        if not is_routable_endpoint(endpoint) or not request_body:
            return body_bytes, request_body

        raw_messages = request_body.get("messages", [])
        messages: list[dict[str, object]] = (
            [m for m in raw_messages if isinstance(m, dict)] if isinstance(raw_messages, list) else []
        )
        self._decision = await self._router.decide(messages)
        if self._decision is None:
            return body_bytes, request_body

        requested_model = request_body.get("model")
        chosen_model = self._decision.model
        if chosen_model != requested_model:
            request_body["model"] = chosen_model
            body_bytes = json.dumps(request_body).encode("utf-8")
            logger.debug("[ROUTING-PROXY] Override %r -> %r", requested_model, chosen_model)
        else:
            logger.debug("[ROUTING-PROXY] Keeping %r", chosen_model)
        return body_bytes, request_body

    def routing_info(self, response_headers: Mapping[str, object]) -> RoutingInfo:
        """Call once the downstream response exists. Reuses whatever decide_and_rewrite()
        already stored (None if it was never called, or if the router itself couldn't
        decide) — the router's own routing_info() picks the right input internally, exactly
        as RouterChatModel's own call does on the agent path."""
        return self._router.routing_info(self._decision, [response_headers])
