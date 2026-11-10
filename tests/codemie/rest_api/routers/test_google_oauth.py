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

"""Tests for the Google OAuth router's Standalone enable-gate.

Outside Standalone the three endpoints had no gate before this ticket and must stay
reachable regardless of GOOGLE_OAUTH_ENABLED/Redis. Inside Standalone they 503 unless
the flag is on and Redis is configured.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from codemie.clients import redis as redis_client_module
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.routers import google_oauth
from codemie.rest_api.routers.google_oauth import router
from codemie.rest_api.security.authentication import authenticate

_MOCK_USER = MagicMock()
_MOCK_USER.id = "user@example.com"

_APP = FastAPI()
_APP.include_router(router)
_APP.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
_APP.dependency_overrides[authenticate] = lambda: _MOCK_USER


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _mock_service():
    service = MagicMock()
    service.initiate_flow.return_value = {"auth_url": "https://accounts.google.com/x", "state": "s"}
    callback_result = MagicMock(success=True, message="ok", status_code=200)
    service.handle_callback.return_value = callback_result
    service.get_status.return_value = {"status": "pending"}
    return service


@pytest.mark.anyio
async def test_initiate_503_when_disabled():
    with (
        patch.object(redis_client_module.config, "STANDALONE", True),
        patch.object(redis_client_module.config, "GOOGLE_OAUTH_ENABLED", False),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/google-oauth/initiate")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Google OAuth is not enabled."


@pytest.mark.anyio
async def test_callback_503_when_disabled():
    """Unlike initiate/status, callback is a browser-rendered HTML page (see
    oauth_router_factory._register_callback's sibling pattern) and must stay HTML, not JSON,
    even when the gate is closed."""
    with (
        patch.object(redis_client_module.config, "STANDALONE", True),
        patch.object(redis_client_module.config, "GOOGLE_OAUTH_ENABLED", False),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/google-oauth/callback", params={"state": "s", "code": "c"})

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert "Google OAuth is not enabled." in response.text


@pytest.mark.anyio
async def test_status_503_when_disabled():
    with (
        patch.object(redis_client_module.config, "STANDALONE", True),
        patch.object(redis_client_module.config, "GOOGLE_OAUTH_ENABLED", False),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/google-oauth/status/some-state")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Google OAuth is not enabled."


@pytest.mark.anyio
async def test_endpoints_unaffected_outside_standalone():
    """GOOGLE_OAUTH_ENABLED left at its default False, non-Standalone: no 503 anywhere."""
    service = _mock_service()
    with (
        patch.object(redis_client_module.config, "STANDALONE", False),
        patch.object(redis_client_module.config, "GOOGLE_OAUTH_ENABLED", False),
        patch.object(google_oauth, "_get_oauth_service", return_value=service),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            initiate_response = await ac.post("/v1/google-oauth/initiate")
            callback_response = await ac.get("/v1/google-oauth/callback", params={"state": "s", "code": "c"})
            status_response = await ac.get("/v1/google-oauth/status/some-state")

    assert initiate_response.status_code == 200
    assert callback_response.status_code == 200
    assert status_response.status_code == 202
