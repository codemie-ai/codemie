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

"""Tests for leaderboard read endpoint 503 guard when LEADERBOARD_ENABLED=False."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.routers.analytics import router
from codemie.rest_api.security.authentication import (
    admin_access_only,
    admin_or_maintainer_or_auditor_access,
    authenticate,
)

_MOCK_USER = MagicMock()
_MOCK_USER.id = "user@example.com"
_MOCK_USER.is_admin = True
_MOCK_USER.is_auditor = False

app = FastAPI()
app.include_router(router)
app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
app.dependency_overrides[authenticate] = lambda: _MOCK_USER
app.dependency_overrides[admin_or_maintainer_or_auditor_access] = lambda: None
app.dependency_overrides[admin_access_only] = lambda: None

_LEADERBOARD_READ_ENDPOINTS = [
    "/v1/analytics/leaderboard/summary",
    "/v1/analytics/leaderboard/entries",
    "/v1/analytics/leaderboard/tiers",
    "/v1/analytics/leaderboard/scores",
    "/v1/analytics/leaderboard/dimensions",
    "/v1/analytics/leaderboard/top-performers",
    "/v1/analytics/leaderboard/snapshots",
    "/v1/analytics/leaderboard/seasons?view=monthly",
    "/v1/analytics/leaderboard/framework",
    "/v1/analytics/leaderboard/user/test-user-id",
]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", _LEADERBOARD_READ_ENDPOINTS)
@patch("codemie.rest_api.routers.analytics.config")
async def test_leaderboard_read_returns_503_when_disabled(mock_config, endpoint):
    """Every leaderboard GET endpoint must return 503 when LEADERBOARD_ENABLED=False."""
    mock_config.LEADERBOARD_ENABLED = False
    mock_config.ANALYTICS_DEFAULT_PAGE_SIZE = 10

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(endpoint)

    assert response.status_code == 503


@pytest.mark.anyio
@patch("codemie.rest_api.routers.analytics.AnalyticsService")
@patch("codemie.rest_api.routers.analytics.config")
async def test_leaderboard_compute_returns_503_when_disabled(mock_config, mock_service_class):
    """POST /leaderboard/compute must return 503 and make no service call when LEADERBOARD_ENABLED=False."""
    mock_config.LEADERBOARD_ENABLED = False
    mock_config.ANALYTICS_DEFAULT_PAGE_SIZE = 10

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/analytics/leaderboard/compute")

    assert response.status_code == 503
    mock_service_class.assert_not_called()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.analytics.AnalyticsService")
@patch("codemie.rest_api.routers.analytics.config")
async def test_leaderboard_summary_200_when_enabled(mock_config, mock_service_class):
    """GET /leaderboard/summary returns 200 when LEADERBOARD_ENABLED=True."""
    mock_config.LEADERBOARD_ENABLED = True
    mock_config.ANALYTICS_DEFAULT_PAGE_SIZE = 10
    mock_config.ENV = "test"

    _meta = {
        "timestamp": "2026-01-01T00:00:00",
        "data_as_of": "2026-01-01T00:00:00",
        "filters_applied": {},
        "execution_time_ms": 1.0,
    }
    mock_service = AsyncMock()
    mock_service.get_leaderboard_summary.return_value = {"data": {"metrics": []}, "metadata": _meta}
    mock_service_class.return_value = mock_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/analytics/leaderboard/summary")

    assert response.status_code == 200
    mock_service.get_leaderboard_summary.assert_called_once()


# ================================================================================
# CR-004: auth must be evaluated before the feature flag on every leaderboard route,
# so an unauthorized/unauthenticated caller's response never reveals flag state.
# ================================================================================


def _denied_access():
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized")


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", _LEADERBOARD_READ_ENDPOINTS)
@pytest.mark.parametrize("flag_enabled", [True, False])
@patch("codemie.rest_api.routers.analytics.config")
async def test_leaderboard_read_denies_unauthorized_caller_regardless_of_flag(mock_config, flag_enabled, endpoint):
    """An unauthorized caller must get the same 403 whether LEADERBOARD_ENABLED is True or False —
    the flag check must never run (and thus never leak) before the access check fails."""
    mock_config.LEADERBOARD_ENABLED = flag_enabled
    mock_config.ANALYTICS_DEFAULT_PAGE_SIZE = 10

    denied_app = FastAPI()
    denied_app.include_router(router)
    denied_app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    denied_app.dependency_overrides[authenticate] = lambda: _MOCK_USER
    denied_app.dependency_overrides[admin_or_maintainer_or_auditor_access] = _denied_access

    transport = ASGITransport(app=denied_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(endpoint)

    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize("flag_enabled", [True, False])
@patch("codemie.rest_api.routers.analytics.AnalyticsService")
@patch("codemie.rest_api.routers.analytics.config")
async def test_leaderboard_compute_denies_unauthorized_caller_regardless_of_flag(
    mock_config, mock_service_class, flag_enabled
):
    """POST /leaderboard/compute must also deny before it can leak flag state (matches the read
    endpoints' now-consistent auth-before-flag ordering)."""
    mock_config.LEADERBOARD_ENABLED = flag_enabled
    mock_config.ANALYTICS_DEFAULT_PAGE_SIZE = 10

    denied_app = FastAPI()
    denied_app.include_router(router)
    denied_app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    denied_app.dependency_overrides[authenticate] = lambda: _MOCK_USER
    denied_app.dependency_overrides[admin_access_only] = _denied_access

    transport = ASGITransport(app=denied_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/analytics/leaderboard/compute")

    assert response.status_code == 403
    mock_service_class.assert_not_called()
