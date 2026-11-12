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

"""Closed static-content ASGI tree. Management authentication never runs on content hosts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from html import escape
from time import monotonic
from urllib.parse import parse_qs
from uuid import uuid4

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from codemie.configs.logger import logger
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.manifest import resolve_asset
from codemie.core.hosted_applications.paths import decode_content_path, strip_root_path
from codemie.core.hosted_applications.protocol import etag_matches, parse_range, parse_session_cookies
from codemie.hosted_apps_gateway.admission import ContentCapacity, ContentLimits
from codemie.hosted_apps_gateway.cache import ImmutableContentCache
from codemie.hosted_apps_gateway.management import route_path
from codemie.hosted_apps_gateway.navigation import (
    WRAPPER_SCRIPT,
    WRAPPER_SCRIPT_HASH,
    navigation_suffix,
    representation_range,
)
from codemie.service.hosted_applications.observability import hosted_request_monitoring

CONTENT_CSP = (
    "default-src 'none'; base-uri 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
    "media-src 'self' blob:; connect-src 'self'; frame-src 'none'; object-src 'none'; "
    "worker-src 'none'; manifest-src 'none'; form-action 'none'; frame-ancestors 'none'; "
    "sandbox allow-scripts allow-same-origin allow-popups"
)
# No `webrtc 'block'`: Chromium and Firefox do not implement it and log an error on every response.
# WebRTC egress remains a documented residual risk (docs/hosted-applications/architecture.md).
# Immutable version assets are revalidated on every use: each reuse re-runs live authorization
# before the browser may keep its copy (304). Everything else is never stored.
NO_STORE = "private, no-store"
REVALIDATE = "private, no-cache"
REVALIDATED_STATUSES = frozenset({200, 206, 304})
# Content may present itself: fullscreen on its own origin only, and pop-ups (a presenter window)
# that inherit the same sandbox (no allow-popups-to-escape-sandbox). Powerful features stay denied.
DENIED_FEATURES = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_CSP,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-DNS-Prefetch-Control": "off",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": DENIED_FEATURES + ", fullscreen=(self)",
    "Cache-Control": NO_STORE,
}
UNAVAILABLE = "Application unavailable"
RETURN_TO_CODEMIE = "Return to CodeMie to open this application"
FRAME_ANCESTORS = "frame-ancestors "
# Platform-owned paths on every version host; uploaded files can never use them (validation refuses them).
RESERVED = ("_platform",)


def navigation(request):
    return (
        request.headers.get("sec-fetch-mode") == "navigate"
        and request.headers.get("sec-fetch-dest") in ("document", "iframe")
        and "text/html" in request.headers.get("accept", "")
    )


class ContentGateway:
    def __init__(self, launch, storage, *, policy: ContentLimits | None = None):
        self.launch, self.storage, self.settings = launch, storage, launch.settings
        self.cache = ImmutableContentCache.shared(storage)
        self.policy = policy = policy or ContentLimits()
        self.capacity = ContentCapacity(policy.total_concurrency, policy.viewer_concurrency, policy.queue_seconds)
        self.cookie_name = "codemie-app-session-local" if self.settings.local else "__Host-codemie-app-session"
        self.embedded_cookie_name = (
            "codemie-app-embedded-local" if self.settings.local else "__Host-codemie-app-embedded"
        )
        self.cookie_names = (self.embedded_cookie_name, self.cookie_name)
        self.headers = SECURITY_HEADERS | {
            "Content-Security-Policy": CONTENT_CSP.replace(
                "frame-ancestors 'none';", FRAME_ANCESTORS + " ".join(self.settings.ui_origins) + ";"
            )
        }

    def response_headers(self, request):
        return self.scope_headers(request.scope)

    def scope_headers(self, scope):
        state = scope.get("state", {})
        version_id = state.get("hosted_version_id")
        if state.get("hosted_viewer_host") and version_id is not None:
            # No uploaded markup on this origin. A different origin prevents
            # the child from changing its frame permissions, including in WebKit where the
            # top-level Permissions-Policy response header is not enforced.
            origin = self.settings.version_origin(version_id)
            return SECURITY_HEADERS | {
                # Fullscreen is delegated to the framed version origin only, never to other origins.
                "Permissions-Policy": f'{DENIED_FEATURES}, fullscreen=(self "{origin}")',
                "Content-Security-Policy": (
                    "default-src 'none'; base-uri 'none'; "
                    f"script-src 'sha256-{WRAPPER_SCRIPT_HASH}'; style-src 'unsafe-inline'; "
                    f"frame-src {origin}; frame-ancestors 'none'; form-action 'none'; "
                    "sandbox allow-scripts allow-same-origin allow-popups"
                ),
            }
        headers = self.headers
        if version_id is None:
            return headers
        viewer = self.settings.version_origin(version_id, viewer=True)
        return headers | {
            "Content-Security-Policy": headers["Content-Security-Policy"].replace(
                FRAME_ANCESTORS + " ".join(self.settings.ui_origins) + ";",
                FRAME_ANCESTORS + " ".join((*self.settings.ui_origins, viewer)) + ";",
            )
        }

    @staticmethod
    def request_target(request):
        # Preserve a framework's path/query without credentials, host inference or HTML injection.
        raw = strip_root_path(request.scope.get("raw_path", b"/"), request.scope.get("root_path", ""))
        decode_content_path(raw)
        query = request.scope.get("query_string", b"")
        if len(raw) > 4096 or len(query) > 8192 or any(c < 32 or c == 127 for c in query):
            raise HostedApplicationError("CONTENT_UNAVAILABLE", UNAVAILABLE, 400)
        try:
            return raw.decode("ascii") + ("?" + query.decode("ascii") if query else "")
        except UnicodeError as exc:
            raise HostedApplicationError("CONTENT_UNAVAILABLE", UNAVAILABLE, 400) from exc

    async def standalone(self, request):
        """Trusted wrapper: viewing credentials stay on the separate version child origin."""
        target = self.request_target(request)
        raw = strip_root_path(request.scope.get("raw_path", b"/"), request.scope.get("root_path", ""))
        if decode_content_path(raw).partition("/")[0].casefold() in RESERVED:
            raise HostedApplicationError("FILE_NOT_FOUND", "File unavailable", 404)
        origin = self.settings.version_origin(request.state.hosted_version_id)
        source = escape(origin + target, quote=True)
        document = (
            '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>Application viewer</title>'
            '<style>html,body,iframe{margin:0;width:100%;height:100%;border:0;overflow:hidden}'
            'html,body{background:Canvas;color-scheme:light dark}</style></head><body>'
            '<iframe title="Application content" sandbox="allow-scripts allow-same-origin allow-popups" '
            'allow="camera \'none\'; microphone \'none\'; geolocation \'none\'; payment \'none\'; '
            f'usb \'none\'; fullscreen {escape(origin, quote=True)}" referrerpolicy="no-referrer" '
            f'data-src="{source}"></iframe>'
            f'<script>{WRAPPER_SCRIPT}</script>'
            '</body></html>'
        )
        return HTMLResponse(document, headers=self.response_headers(request))

    @staticmethod
    async def admit_viewer(request, state):
        """Account the rest of the request to the authenticated viewer's share of process capacity."""
        slot = getattr(request.state, "hosted_capacity", None)
        if slot is not None:
            await slot.serve(state["viewer_subject"])

    @staticmethod
    def unique_headers(request):
        critical = ("cookie", "origin", "content-type", "range", "if-none-match", "if-range")
        for name in critical:
            if len(request.headers.getlist(name)) > 1:
                raise HostedApplicationError("CONTENT_UNAVAILABLE", UNAVAILABLE, 400)

    def launch_request_allowed(self, request) -> bool:
        """A launch is a browser navigation posting a form from a CodeMie UI origin."""
        return (
            request.headers.get("origin") in self.settings.ui_origins
            and request.headers.get("content-type") == "application/x-www-form-urlencoded"
            and request.headers.get("sec-fetch-mode") == "navigate"
            and request.headers.get("sec-fetch-dest") in ("document", "iframe")
            and request.headers.get("sec-fetch-site") in ("cross-site", "same-site", "same-origin")
        )

    @staticmethod
    async def launch_body(request) -> bytearray:
        """The small launch form, read within its size and time bounds."""
        data = bytearray()
        try:
            async with asyncio.timeout(10):
                iterator = request.stream().__aiter__()
                while True:
                    try:
                        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=5)
                    except StopAsyncIteration:
                        break
                    data.extend(chunk)
                    if len(data) > 256:
                        raise HostedApplicationError("LAUNCH_EXPIRED", "Return to CodeMie", 401)
        except TimeoutError as exc:
            raise HostedApplicationError("LAUNCH_EXPIRED", "Return to CodeMie", 408) from exc
        return data

    async def bootstrap(self, request):
        self.unique_headers(request)
        if not self.launch_request_allowed(request):
            raise HostedApplicationError("LAUNCH_EXPIRED", RETURN_TO_CODEMIE, 401)
        data = await self.launch_body(request)
        try:
            fields = parse_qs(data.decode("ascii"), strict_parsing=True, max_num_fields=2)
            if set(fields) != {"ticket"} or len(fields["ticket"]) != 1 or request.url.query:
                raise ValueError
        except ValueError as exc:
            raise HostedApplicationError("LAUNCH_EXPIRED", "Invalid application launch", 401) from exc
        token, target = await self.launch.consume(fields["ticket"][0], request.headers["host"])
        embedded = request.headers.get("sec-fetch-dest") == "iframe"
        destination = "/_platform/view"
        if not embedded:
            destination = self.settings.version_origin(request.state.hosted_version_id, viewer=True) + target
        response = RedirectResponse(destination, status_code=303, headers=self.response_headers(request))
        response.set_cookie(
            self.embedded_cookie_name if embedded else self.cookie_name,
            token,
            max_age=900,
            secure=embedded or not self.settings.local,
            httponly=True,
            samesite="none" if embedded else "lax",
            path="/",
        )
        if embedded:
            # Starlette's partitioned=True requires Python 3.14. Append only this fixed
            # attribute to our freshly generated synthetic cookie; never alter Morsel globally.
            response.headers["set-cookie"] += "; Partitioned"
        return response

    def frame_message(self, kind, *, ready=False):
        message = json.dumps({"type": kind})
        scripts = "".join(
            "parent.postMessage(" + message + "," + json.dumps(origin) + ");" for origin in self.settings.ui_origins
        )
        if ready:
            scripts += 'location.replace("/");'
        return "<script>" + scripts + "</script>"

    async def view(self, request):
        self.unique_headers(request)
        token = self.session_cookie(request)
        async with asyncio.timeout(10):
            state = await self.launch.authorize(token, request.state.hosted_version_id)
        await self.admit_viewer(request, state)
        headers = self.response_headers(request)
        # No uploaded markup in this platform-owned readiness document. The parent only
        # uses the bounded message as a loading hint, never as an authority or navigation URL.
        return HTMLResponse(
            '<!doctype html><html><meta charset="utf-8"><title>Opening application</title>'
            '<body>Opening application…' + self.frame_message("codemie-hosted-ready", ready=True) + '</body></html>',
            headers=headers,
        )

    def session_cookie(self, request):
        # One strict tokenizer: a sibling version origin can set Domain cookies for the shared
        # content domain, so only exact platform names with exact token values are honored.
        try:
            cookies = parse_session_cookies(request.headers.get("cookie", ""), self.cookie_names)
        except ValueError as exc:
            raise HostedApplicationError("LAUNCH_EXPIRED", RETURN_TO_CODEMIE, 401) from exc
        name = next((candidate for candidate in self.cookie_names if candidate in cookies), self.cookie_name)
        if name not in cookies:
            raise HostedApplicationError("LAUNCH_EXPIRED", RETURN_TO_CODEMIE, 401)
        return cookies[name]

    async def authorize_content(self, request, token):
        async with asyncio.timeout(10):
            return await self.launch.authorize(token, request.state.hosted_version_id)

    @staticmethod
    def content_request_allowed(request) -> bool:
        """Only plain reads; no worker may be registered from uploaded content."""
        return (
            request.method in ("GET", "HEAD")
            and request.headers.get("sec-fetch-dest") not in ("serviceworker", "sharedworker", "worker")
            and "service-worker" not in request.headers
        )

    async def content(self, request):
        self.unique_headers(request)
        if not self.content_request_allowed(request):
            raise HostedApplicationError("CONTENT_UNAVAILABLE", UNAVAILABLE, 404)
        raw_path = strip_root_path(request.scope.get("raw_path", b"/"), request.scope.get("root_path", ""))
        path = decode_content_path(raw_path)
        if path.partition("/")[0].casefold() in RESERVED:
            raise HostedApplicationError("FILE_NOT_FOUND", "File unavailable", 404)
        token = self.session_cookie(request)
        state = await self.authorize_content(request, token)
        version, attempt = state["version"], state["attempt"]
        session_headers = self.response_headers(request)
        await self.admit_viewer(request, state)
        if request.headers.get("sec-fetch-dest") == "document":
            # A direct version URL must not bypass the standalone frame after ticket redemption.
            destination = self.settings.version_origin(request.state.hosted_version_id, viewer=True)
            return RedirectResponse(
                destination + self.request_target(request), status_code=303, headers=session_headers
            )
        manifest = await self.cache.manifest(version, attempt)
        asset = resolve_asset(manifest, path, html_navigation=navigation(request))
        suffix = (
            navigation_suffix(self.settings.version_origin(request.state.hosted_version_id, viewer=True))
            if navigation(request) and asset["mime"].startswith("text/html")
            else b""
        )
        tag = asset["sha256"] + ("." + hashlib.sha256(suffix).hexdigest() if suffix else "")
        headers = session_headers | {
            "Content-Type": asset["mime"],
            "Accept-Ranges": "bytes",
            "ETag": '"' + tag + '"',
            "Cache-Control": REVALIDATE,
        }
        # Authorization above always precedes conditional/range/HEAD responses.
        if etag_matches(request.headers.get("if-none-match"), headers["ETag"]) and not request.headers.get("range"):
            return Response(status_code=304, headers=headers)
        return self.asset_response(request, asset, suffix, headers)

    def asset_response(self, request, asset, suffix, headers):
        """The asset, or the requested byte range of it, with its suffix appended to the representation."""
        size = asset["size"] + len(suffix)
        ranged = request.headers.get("range")
        if ranged and request.headers.get("if-range") not in (None, headers["ETag"]):
            ranged = None
        start, end, _ = parse_range(ranged, size)
        length = max(0, end - start + 1)
        headers["Content-Length"] = str(length)
        if ranged:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if request.method == "HEAD" or length == 0:
            return Response(status_code=206 if ranged else 200, headers=headers)
        body = representation_range(self.storage, asset["key"], asset["size"], suffix, start, end)
        return StreamingResponse(body, status_code=206 if ranged else 200, headers=headers)

    async def failure(self, request, exc):
        headers = dict(self.response_headers(request))
        if exc.status in (429, 503):
            headers["Retry-After"] = "5"
        if exc.status == 416:
            headers["Accept-Ranges"] = "bytes"
        # Capacity refusals are expected under load and counted by the content metrics; other 5xx are logged.
        if exc.status >= 500 and exc.code not in ("CONTENT_UNAVAILABLE", "SERVICE_BUSY"):
            logger.error(
                f"Hosted content unavailable: error_id={uuid4().hex}, code={exc.code}, status={exc.status}",
                exc_info=exc,
            )
        # The viewer waits for a hint from its frame: a busy content host says so instead of timing out.
        if navigation(request) and exc.status in (429, 503):
            html = (
                "<!doctype html><html><meta charset=utf-8><title>Application busy</title><body>"
                "<h1>This application is busy</h1><p>Wait a few seconds, then reload it in CodeMie.</p>"
                + self.frame_message("codemie-hosted-unavailable")
                + "</body></html>"
            )
            return HTMLResponse(html, status_code=exc.status, headers=headers)
        # No platform login inference, private metadata, external redirects, or credential-bearing URLs.
        if navigation(request) and request.method == "GET" and exc.status == 401:
            html = (
                "<!doctype html><html><meta charset=utf-8><title>Open in CodeMie</title><body>"
                "<h1>Open this application from CodeMie</h1><p>Your viewing session is unavailable.</p>"
                "<p>Return to the CodeMie application page and select Open application.</p>"
                + '<p><a href="'
                + escape(sorted(self.settings.ui_origins)[0] + '/apps', quote=True)
                + '">Return to Applications</a></p>'
                + self.frame_message("codemie-hosted-unavailable")
                + '</body></html>'
            )
            return HTMLResponse(html, status_code=401, headers=headers)
        return Response(UNAVAILABLE if request.method != "HEAD" else b"", status_code=exc.status, headers=headers)

    async def timed_out(self, request, exc):
        return await self.failure(
            request, HostedApplicationError("CONTENT_UNAVAILABLE", "Application busy; retry later", 503)
        )

    async def unexpected(self, request, exc):
        logger.error(
            f"Hosted content request failed unexpectedly: error_id={uuid4().hex}, error={type(exc).__name__}",
            exc_info=exc,
        )
        return Response(
            UNAVAILABLE if request.method != "HEAD" else b"",
            status_code=503,
            headers=dict(self.response_headers(request)) | {"Retry-After": "5"},
        )

    def app(self):
        errors = {
            HostedApplicationError: self.failure,
            TimeoutError: self.timed_out,
            Exception: self.unexpected,
        }
        uploaded = Starlette(
            routes=[
                Route("/_platform/launch", self.bootstrap, methods=["POST"]),
                Route("/_platform/view", self.view, methods=["GET"]),
                Route(
                    "/{path:path}",
                    self.content,
                    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
                ),
            ],
            exception_handlers=errors,
        )
        viewer = Starlette(
            routes=[Route("/{path:path}", self.standalone, methods=["GET", "HEAD"])], exception_handlers=errors
        )

        async def dispatch(scope, receive, send):
            tree = viewer if scope.get("state", {}).get("hosted_viewer_host") else uploaded
            await tree(scope, receive, send)

        return ContentResponseBoundary(
            dispatch,
            headers=self.headers,
            capacity=self.capacity,
            request_seconds=self.policy.request_seconds,
            headers_factory=self.scope_headers,
        )


class ContentResponseBoundary:
    """Apply isolation headers to framework errors; never emit private exception details."""

    def __init__(self, app, *, headers=None, capacity=None, request_seconds=120.0, headers_factory=None):
        self.inner = app
        self.headers = headers if headers is not None else SECURITY_HEADERS
        self.capacity = capacity if capacity is not None else ContentCapacity(64, 16, 5.0)
        self.request_seconds = request_seconds
        self.headers_factory = headers_factory

    def response_headers(self, scope):
        if self.headers_factory is not None:
            return self.headers_factory(scope)
        return self.headers

    @staticmethod
    def cache_control(status, headers):
        # Only an authorized version asset response may opt into revalidated caching; any other
        # status or value, including framework errors, is never stored.
        declared = [value for key, value in headers if key.lower() == b"cache-control"]
        return REVALIDATE if status in REVALIDATED_STATUSES and declared == [REVALIDATE.encode()] else NO_STORE

    async def __call__(self, scope, receive, send):
        scope.setdefault("state", {})
        began = monotonic()
        status, size = 503, 0

        async def measured_send(message):
            nonlocal status, size
            if message['type'] == 'http.response.start':
                status = message['status']
            if message['type'] == 'http.response.body':
                size += len(message.get('body', b''))
            await send(message)

        try:
            await self.respond(scope, receive, measured_send)
        finally:
            hosted_request_monitoring().content(
                status, size, monotonic() - began, bootstrap=route_path(scope) == '/_platform/launch'
            )

    async def respond(self, scope, receive, send):
        started = False

        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                inner = message.get("headers", [])
                protected = {
                    key.lower().encode(): value.encode()
                    for key, value in (self.response_headers(scope) | {"Cache-Control": NO_STORE}).items()
                }
                protected[b"cache-control"] = self.cache_control(message["status"], inner).encode()
                headers = [(key, value) for key, value in inner if key.lower() not in protected]
                message = {**message, "headers": headers + list(protected.items())}
            await send(message)

        slot = self.capacity.slot()
        scope["state"]["hosted_capacity"] = slot
        try:
            if not await slot.admit():
                await Response(
                    "Application busy; retry later",
                    status_code=503,
                    headers={**self.response_headers(scope), "Retry-After": "5"},
                )(scope, receive, send)
                return
            try:
                async with asyncio.timeout(self.request_seconds):
                    await self.inner(scope, receive, safe_send)
            except asyncio.CancelledError:
                raise
            except Exception:
                if started:
                    return
                await Response(
                    UNAVAILABLE,
                    status_code=503,
                    headers={**self.response_headers(scope), "Retry-After": "5"},
                )(scope, receive, send)
        finally:
            slot.release()
