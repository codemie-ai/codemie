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

"""Tests for the runtime CORS middleware."""

import time
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from codemie.rest_api.middleware.dynamic_cors import (
    DynamicCORSMiddleware,
    DynamicCORSOriginsProvider,
)


import codemie.rest_api.middleware.dynamic_cors as dynamic_cors_module


def _build_app(origins: frozenset[str]):
    """Build a Starlette app wrapped in DynamicCORSMiddleware with the given origins."""

    async def homepage(request):
        return PlainTextResponse("ok")

    provider = DynamicCORSOriginsProvider(set())
    provider._cache = (time.monotonic(), origins)
    dynamic_cors_module._origins_provider = provider

    starlette_app = Starlette(routes=[Route("/", homepage, methods=["GET", "POST", "OPTIONS"])])
    starlette_app.add_middleware(DynamicCORSMiddleware)
    return starlette_app, provider


@pytest.fixture
def cors_app():
    original_provider = dynamic_cors_module._origins_provider
    app, _ = _build_app(frozenset({"https://fa1.dev"}))
    yield app
    dynamic_cors_module._origins_provider = original_provider


@pytest.mark.anyio
async def test_adds_cors_headers_for_allowed_origin(cors_app):
    transport = ASGITransport(app=cors_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/", headers={"origin": "https://fa1.dev"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://fa1.dev"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["vary"] == "Origin"


@pytest.mark.anyio
async def test_no_cors_headers_for_disallowed_origin(cors_app):
    transport = ASGITransport(app=cors_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/", headers={"origin": "https://evil.dev"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.anyio
async def test_preflight_for_allowed_origin(cors_app):
    transport = ASGITransport(app=cors_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.options(
            "/",
            headers={
                "origin": "https://fa1.dev",
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://fa1.dev"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"]


@pytest.mark.anyio
async def test_preflight_for_disallowed_origin(cors_app):
    transport = ASGITransport(app=cors_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.options(
            "/",
            headers={
                "origin": "https://evil.dev",
                "access-control-request-method": "POST",
            },
        )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.anyio
@patch("codemie.rest_api.middleware.dynamic_cors.DynamicConfigService")
async def test_provider_refreshes_cache_after_ttl(mock_service):
    provider = DynamicCORSOriginsProvider(set())
    mock_service.aget = AsyncMock(return_value='["  https://refreshed.dev  "]')

    origins = await provider.get_origins()
    assert "https://refreshed.dev" in origins
    mock_service.aget.assert_awaited_once()

    # Second call within TTL should use cache
    origins2 = await provider.get_origins()
    assert origins2 == origins
    assert mock_service.aget.await_count == 1

    # Expire cache and call again
    provider._cache = (0.0, origins)
    origins3 = await provider.get_origins()
    assert mock_service.aget.await_count == 2
    assert "https://refreshed.dev" in origins3


@pytest.mark.anyio
async def test_invalidate_clears_cache():
    provider = DynamicCORSOriginsProvider(set())
    provider._cache = (time.monotonic(), frozenset({"https://fa1.dev"}))

    provider.invalidate()
    assert provider._cache is None
