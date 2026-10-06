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

"""Unit tests for POST /v1/logs endpoint, covering the ADMIN_LOG_LOOKUP_ENABLED guard."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from codemie.rest_api.main import extended_http_exception_handler
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.logs import router
from codemie.rest_api.security.authentication import admin_access_only, authenticate

app = FastAPI()
app.include_router(router)
app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)

# Bypass router-level auth for all tests in this module.
app.dependency_overrides[authenticate] = lambda: MagicMock()
app.dependency_overrides[admin_access_only] = lambda: None

_VALID_PAYLOAD = {"field": "conversation_id", "value": "some-uuid"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.logs.config")
async def test_post_logs_503_when_disabled(mock_config):
    """POST /logs must return 503 when ADMIN_LOG_LOOKUP_ENABLED is False."""
    mock_config.ADMIN_LOG_LOOKUP_ENABLED = False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/logs", json=_VALID_PAYLOAD)

    assert response.status_code == 503
    assert "Admin Log Lookup is not enabled" in response.text


@pytest.mark.anyio
@patch("codemie.rest_api.routers.logs.LogService")
@patch("codemie.rest_api.routers.logs.config")
async def test_post_logs_reachable_when_enabled(mock_config, mock_log_service):
    """POST /logs must reach the service layer and return 200 when ADMIN_LOG_LOOKUP_ENABLED is True."""
    mock_config.ADMIN_LOG_LOOKUP_ENABLED = True
    mock_log_service.get_logs_by_target_field.return_value = []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/logs", json=_VALID_PAYLOAD)

    assert response.status_code == 200
    mock_log_service.get_logs_by_target_field.assert_called_once()
