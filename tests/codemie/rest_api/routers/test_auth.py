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

"""Tests for the auth router.

Covers the external redirect endpoint and preserves coverage for the
existing localhost callback endpoint.
"""

import base64
import json
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.routers import auth as auth_router
from codemie.core.exceptions import ExtendedHTTPException

app = FastAPI()
app.include_router(auth_router.router)


@app.exception_handler(ExtendedHTTPException)
async def extended_http_exception_handler(request, exc: ExtendedHTTPException):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=exc.code,
        content={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        },
    )


@pytest.fixture(autouse=True)
def _local_idp(monkeypatch):
    """Use local IDP so no oauth2-proxy cookies are required."""
    monkeypatch.setattr(auth_router.config, "IDP_PROVIDER", "local")


@pytest.fixture
def allowed_origins():
    return '["https://fa1.dev"]'


@pytest.mark.anyio
@patch("codemie.rest_api.routers.auth.DynamicConfigService")
async def test_login_redirect_success(mock_service, allowed_origins):
    mock_service.aget = AsyncMock(return_value=allowed_origins)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/auth/login/redirect",
            params={"callback_url": "https://fa1.dev/auth"},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "fa1.dev"
    assert parsed.path == "/auth"

    token = json.loads(base64.b64decode(parsed.query.split("=", 1)[1]).decode("ascii"))
    assert token["provider"] == "local"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.auth.DynamicConfigService")
async def test_login_redirect_preserves_existing_query_params(mock_service, allowed_origins):
    mock_service.aget = AsyncMock(return_value=allowed_origins)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/auth/login/redirect",
            params={"callback_url": "https://fa1.dev/auth?source=login"},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    location = response.headers["location"]
    assert "source=login" in location
    assert "token=" in location


@pytest.mark.anyio
@patch("codemie.rest_api.routers.auth.DynamicConfigService")
async def test_login_redirect_rejects_http_in_non_local_env(mock_service, allowed_origins, monkeypatch):
    mock_service.aget = AsyncMock(return_value=allowed_origins)
    monkeypatch.setattr(auth_router.config, "ENV", "prod")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/auth/login/redirect",
            params={"callback_url": "http://fa1.dev/auth"},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "HTTPS" in response.json()["details"]


@pytest.mark.anyio
@patch("codemie.rest_api.routers.auth.DynamicConfigService")
async def test_login_redirect_rejects_not_allowed_domain(mock_service):
    mock_service.aget = AsyncMock(return_value='["https://other.dev"]')

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/auth/login/redirect",
            params={"callback_url": "https://fa1.dev/auth"},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "not in the administrator allow-list" in response.json()["details"]


@pytest.mark.anyio
@patch("codemie.rest_api.routers.auth.DynamicConfigService")
async def test_login_redirect_rejects_url_with_fragment(mock_service, allowed_origins):
    mock_service.aget = AsyncMock(return_value=allowed_origins)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/auth/login/redirect",
            params={"callback_url": "https://fa1.dev/auth#section"},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_login_localhost_callback_unchanged():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/auth/login/12345",
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    assert response.headers["location"].startswith("http://localhost:12345/auth?token=")
