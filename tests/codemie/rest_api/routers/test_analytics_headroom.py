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

"""Headroom analytics endpoints are gated by the headroom customer feature, not routingAnalytics."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.routers.analytics import router
from codemie.rest_api.security.authentication import authenticate

_MOCK_USER = MagicMock()
_MOCK_USER.id = "user@example.com"

app = FastAPI()
app.include_router(router)
app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
app.dependency_overrides[authenticate] = lambda: _MOCK_USER

_ENDPOINTS = [
    "/v1/analytics/headroom/summary",
    "/v1/analytics/headroom/by-strategy",
    "/v1/analytics/headroom/timeseries",
]
CUSTOMER_CONFIG = "codemie.rest_api.routers.analytics.headroom.customer_config"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _feature_flags(enabled: set[str]):
    return lambda feature: feature in enabled


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_disabled_when_headroom_feature_is_off_even_if_routing_analytics_is_on(endpoint):
    with patch(CUSTOMER_CONFIG) as customer:
        customer.is_feature_enabled.side_effect = _feature_flags({"routingAnalytics"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(endpoint)

    assert response.status_code == 404
    customer.is_feature_enabled.assert_called_with("headroom")


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", _ENDPOINTS)
async def test_enabled_when_headroom_feature_is_on_without_routing_analytics(endpoint):
    empty = {"data": {"metrics": [], "columns": [], "rows": []}, "metadata": {}, "pagination": {}}
    with (
        patch(CUSTOMER_CONFIG) as customer,
        patch("codemie.rest_api.routers.analytics.headroom.AnalyticsService") as service_cls,
        patch("codemie.rest_api.routers.analytics.headroom._create_response") as create_response,
    ):
        customer.is_feature_enabled.side_effect = _feature_flags({"headroom"})
        service = service_cls.return_value
        for name in (
            "get_headroom_summary",
            "get_headroom_by_strategy",
            "get_headroom_timeseries",
        ):
            setattr(service, name, AsyncMock(return_value=empty))
        create_response.return_value = JSONResponse(content={})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(endpoint)

    assert response.status_code == 200


def test_by_mode_route_is_not_registered():
    paths = {getattr(route, "path", None) for route in app.routes}

    assert "/v1/analytics/headroom/by-mode" not in paths
    assert {
        "/v1/analytics/headroom/summary",
        "/v1/analytics/headroom/by-strategy",
        "/v1/analytics/headroom/timeseries",
    } <= paths


@pytest.mark.anyio
async def test_by_mode_request_is_not_found_even_when_headroom_feature_is_on():
    with (
        patch(CUSTOMER_CONFIG) as customer,
        patch("codemie.rest_api.routers.analytics.headroom.AnalyticsService") as service_cls,
    ):
        customer.is_feature_enabled.side_effect = _feature_flags({"headroom"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/analytics/headroom/by-mode")

    assert response.status_code in (404, 405)
    service_cls.assert_not_called()
