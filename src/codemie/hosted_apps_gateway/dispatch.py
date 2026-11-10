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

"""Host-based ASGI dispatch: the content namespace is a closed tree, every other host is management."""

from __future__ import annotations

import re
from uuid import UUID

from starlette.responses import PlainTextResponse

_CANONICAL_AUTHORITY = re.compile(r"^[a-z\d](?:[a-z\d.-]{0,251}[a-z\d])?(?::\d{1,5})?$", re.ASCII)
_FORWARDED_AUTHORITY = frozenset({b"forwarded", b"x-forwarded-host", b"x-original-host"})
_CLOSED_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "X-DNS-Prefetch-Control": "off",
}

MANAGEMENT, CONTENT, AMBIGUOUS = "management", "content", "ambiguous"


def closed_response(message: str, status: int) -> PlainTextResponse:
    """A response that no tree produced: no cookies, no management headers, nothing a browser can run."""
    return PlainTextResponse(message, status_code=status, headers=dict(_CLOSED_HEADERS))


class HostDispatcher:
    """Select a closed ASGI tree before any management middleware executes.

    Only the request Host decides. A Host inside the content namespace (the content domain or any of
    its subdomains) reaches the closed content tree and never management: a version host on the
    configured content port is served, anything else in the namespace is a closed 404. Every other
    Host - kubelet probes addressed by pod IP, in-cluster service names, requests without a Host -
    reaches management unchanged.

    Forwarding headers (Forwarded, X-Forwarded-Host, X-Original-Host) never route. A request whose
    forwarding headers disagree with Host about the content namespace is ambiguous and is rejected
    closed, so a proxy that believes it is talking to one origin never receives the other tree. The
    content tree accepts at most one forwarding header: an X-Forwarded-Host that repeats Host exactly,
    as ingress controllers send it. Host values that are not canonical are matched against the content
    domain by substring, so malformed spellings of a content host fail closed instead of reaching
    management.
    """

    def __init__(self, management, content, *, content_domain: str, content_port: int = 443):
        domain = (content_domain or "").strip().rstrip(".").lower()
        if not domain:
            raise ValueError("A content domain is required for host dispatch")
        try:
            # Strict: a lossy encoding would make malformed content hosts route to management.
            domain_bytes = domain.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("The content domain must be ASCII (use its punycode form)") from exc
        self.management = management
        self.content = content
        self.content_domain = domain
        self.content_port = content_port
        self.version_host = re.compile(r"^[vw]-([0-9a-f]{32})\." + re.escape(domain) + r"$")
        self._domain_bytes = domain_bytes

    def __getattr__(self, name):
        # Preserve FastAPI's inspection/testing surface, never used for dispatch.
        return getattr(self.management, name)

    def _in_namespace(self, authority: str) -> bool:
        hostname = authority.partition(":")[0]
        return hostname == self.content_domain or hostname.endswith("." + self.content_domain)

    def _mentions_content(self, raw: bytes) -> bool:
        return self._domain_bytes in raw.lower()

    def classify(self, scope) -> tuple[str, str | None]:
        """Return the tree for this request and its canonical Host authority, when it has one."""
        headers = scope.get("headers") or []
        hosts = [value for name, value in headers if name.lower() == b"host"]
        forwarded = [(name.lower(), value) for name, value in headers if name.lower() in _FORWARDED_AUTHORITY]
        if len(hosts) > 1:
            return AMBIGUOUS, None
        raw = hosts[0] if hosts else b""
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            text = ""
        authority = text if _CANONICAL_AUTHORITY.fullmatch(text) else None
        content = self._in_namespace(authority) if authority is not None else self._mentions_content(raw)
        if not content:
            if any(self._mentions_content(value) for _, value in forwarded):
                return AMBIGUOUS, None
            return MANAGEMENT, authority
        if forwarded and (authority is None or len(forwarded) != 1 or forwarded[0] != (b"x-forwarded-host", raw)):
            return AMBIGUOUS, None
        return CONTENT, authority

    def version_id(self, authority: str | None) -> UUID | None:
        if authority is None:
            return None
        hostname, _, port = authority.partition(":")
        if port != str(self.content_port) and not (port == "" and self.content_port == 443):
            return None
        match = self.version_host.fullmatch(hostname)
        return UUID(hex=match.group(1)) if match else None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.management(scope, receive, send)
            return
        route, authority = self.classify(scope)
        if route == MANAGEMENT:
            await self.management(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            # Unknown protocols in the content namespace are never delivered to either tree.
            return
        version_id = self.version_id(authority) if route == CONTENT else None
        if version_id is not None:
            state = scope.setdefault("state", {})
            state["hosted_version_id"] = version_id
            state["hosted_viewer_host"] = authority.startswith("w-")
            await self.content(scope, receive, send)
            return
        if route == AMBIGUOUS:
            await closed_response("Bad request", 400)(scope, receive, send)
            return
        await closed_response("Application unavailable", 404)(scope, receive, send)


def protect_content_hosts(management, settings):
    """A configured content origin remains isolated even during disablement/outages."""
    if not settings.content_domain:
        return management

    async def closed_content(scope, receive, send):
        state = getattr(management, "state", None)
        runtime = getattr(state, "hosted_applications", None)
        if not settings.enabled or runtime is None or not runtime.ready:
            await closed_response("Application unavailable", 503)(scope, receive, send)
            return
        if not hasattr(runtime, "content_app"):
            from codemie.hosted_apps_gateway.content import ContentGateway

            runtime.content_app = ContentGateway(runtime.launch, runtime.storage).app()
        await runtime.content_app(scope, receive, send)

    return HostDispatcher(
        management, closed_content, content_domain=settings.content_domain, content_port=settings.content_port
    )
