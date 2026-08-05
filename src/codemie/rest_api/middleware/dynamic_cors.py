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

"""Runtime-configurable CORS middleware.

Reads additional allowed origins from dynamic configuration so that external
sites (e.g. harnesses) can call CodeMie APIs from a browser without a backend
redeploy. Base origins (FRONTEND_URL and localhost in dev) are always included.
"""

from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from codemie.configs import config, logger
from codemie.core.constants import Environment
from codemie.service.dynamic_config_service import DynamicConfigService

CLI_AUTH_ALLOWED_EXTERNAL_ORIGINS_KEY = "CLI_AUTH_ALLOWED_EXTERNAL_ORIGINS"
_CORS_CACHE_TTL_SECONDS = 60


class DynamicCORSOriginsProvider:
    """Provides the merged set of allowed CORS origins with a short TTL cache."""

    def __init__(self, static_origins: set[str]) -> None:
        self._static = frozenset(static_origins)
        self._cache: tuple[float, frozenset[str]] | None = None
        self._lock = asyncio.Lock()

    async def get_origins(self) -> frozenset[str]:
        """Return static + dynamic allowed origins, cached for a short window."""
        now = time.monotonic()

        cached = self._cache
        if cached is not None and now - cached[0] < _CORS_CACHE_TTL_SECONDS:
            return cached[1]

        async with self._lock:
            cached = self._cache
            if cached is not None and now - cached[0] < _CORS_CACHE_TTL_SECONDS:
                return cached[1]

            raw = await DynamicConfigService.aget(CLI_AUTH_ALLOWED_EXTERNAL_ORIGINS_KEY, default="[]")
            dynamic = self._parse_raw_origins(raw)
            merged = self._static | dynamic
            self._cache = (time.monotonic(), frozenset(merged))
            logger.debug(f"Refreshed dynamic CORS origins: static={self._static}, dynamic={dynamic}")
            return self._cache[1]

    def invalidate(self) -> None:
        """Clear the cache; call after dynamic config changes."""
        self._cache = None

    @staticmethod
    def _parse_raw_origins(raw: str) -> set[str]:
        try:
            origins = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON for {CLI_AUTH_ALLOWED_EXTERNAL_ORIGINS_KEY}: {raw!r}")
            origins = []

        if not isinstance(origins, list):
            logger.warning(f"Expected list for {CLI_AUTH_ALLOWED_EXTERNAL_ORIGINS_KEY}, got {type(origins)}")
            origins = []

        return {origin.strip().lower() for origin in origins if isinstance(origin, str) and origin.strip()}


def _get_origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_static_origins() -> set[str]:
    origins = {_get_origin(config.FRONTEND_URL)}
    if Environment.LOCAL.value != config.ENV:
        origins.add("http://localhost:3000")
    return origins


_origins_provider = DynamicCORSOriginsProvider(_build_static_origins())


def get_dynamic_cors_origins_provider() -> DynamicCORSOriginsProvider:
    return _origins_provider


def invalidate_dynamic_cors_cache() -> None:
    """Public hook used by the dynamic-config router after origin changes."""
    _origins_provider.invalidate()


class DynamicCORSMiddleware:
    """ASGI middleware that applies CORS headers based on runtime origin allow-list."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        origin = request.headers.get("origin")
        method = request.method

        allowed_origins = await _origins_provider.get_origins()

        # Preflight request
        if method == "OPTIONS" and "access-control-request-method" in request.headers:
            response = self._build_preflight_response(request, origin, allowed_origins)
            await response(scope, receive, send)
            return

        # Actual cross-origin request
        if origin and origin.lower() in allowed_origins:
            await self._handle_cors_request(scope, receive, send, origin)
            return

        await self.app(scope, receive, send)

    def _build_preflight_response(
        self,
        request: Request,
        origin: str | None,
        allowed_origins: frozenset[str],
    ) -> Response:
        headers: dict[str, str] = {"vary": "Origin"}

        if origin and origin.lower() in allowed_origins:
            headers["access-control-allow-origin"] = origin
            headers["access-control-allow-credentials"] = "true"
            headers["access-control-allow-methods"] = request.headers.get("access-control-request-method", "*")
            headers["access-control-allow-headers"] = request.headers.get("access-control-request-headers", "*")
            headers["access-control-max-age"] = "86400"

        return Response(status_code=200, headers=headers)

    async def _handle_cors_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        origin: str,
    ) -> None:
        async def send_with_cors_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["access-control-allow-origin"] = origin
                headers["access-control-allow-credentials"] = "true"
                headers["access-control-expose-headers"] = "Content-Disposition"
                headers["vary"] = "Origin"
            await send(message)

        await self.app(scope, receive, send_with_cors_headers)
