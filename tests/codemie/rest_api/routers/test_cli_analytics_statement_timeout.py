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

"""Overview, Repositories, Projects, Branches and Efficiency answer 504, naming the statement time limit, when a statement exceeds it
(server side: QueryCanceledError; client side: the command timeout) instead of a 503 (EPMCDME-17052)."""

from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.cli_analytics.ports import RequestBudgetExceeded, StatementTimeout
from codemie.rest_api.security.authentication import authenticate
from tests.codemie.rest_api.routers.test_cli_analytics_storage_wiring import _authenticate

_PATHS = {
    "get_overview": "/v1/analytics/cli-analytics/overview",
    "get_repositories": "/v1/analytics/cli-analytics/repositories",
    "get_efficiency": "/v1/analytics/cli-analytics/efficiency",
    "get_tools": "/v1/analytics/cli-analytics/tools",
    "get_projects": "/v1/analytics/cli-analytics/projects",
    "get_activity": "/v1/analytics/cli-analytics/activity",
    "get_branches": "/v1/analytics/cli-analytics/branches?repository=r",
}


@pytest.mark.parametrize("error", [asyncpg.QueryCanceledError("canceling statement"), StatementTimeout()])
@pytest.mark.parametrize("method", _PATHS)
def test_a_statement_past_the_time_limit_answers_504_naming_the_limit(method, error):
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[authenticate] = _authenticate

    @app.exception_handler(ExtendedHTTPException)
    async def _extended(request, exc: ExtendedHTTPException):  # as the application registers it
        return JSONResponse(status_code=exc.code, content={"message": exc.message, "details": exc.details})

    handler = AsyncMock()
    getattr(handler, method).side_effect = error
    with (
        patch.object(router_module, "_handler", return_value=handler),
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module.config, "CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS", 45_000),
        patch.object(
            router_module.FilterParams, "resolve", new=AsyncMock(return_value=router_module.LocalAnalyticsFilter)
        ),
    ):
        resp = TestClient(app, raise_server_exceptions=False).get(_PATHS[method])

    assert resp.status_code == 504
    assert "45 seconds" in resp.json()["details"]


@pytest.mark.parametrize(
    ("error", "code", "text"),
    [
        (RequestBudgetExceeded(), 504, "budget of 540 seconds"),  # the whole request, the wait for a slot included
        (TimeoutError(), 503, "cannot be reached"),  # a pool or connect timeout: the store, not a slow statement
    ],
)
@pytest.mark.parametrize("method", _PATHS)
def test_a_request_past_its_budget_is_a_504_and_a_pool_timeout_stays_a_503(method, error, code, text):
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[authenticate] = _authenticate

    @app.exception_handler(ExtendedHTTPException)
    async def _extended(request, exc: ExtendedHTTPException):  # as the application registers it
        return JSONResponse(status_code=exc.code, content={"message": exc.message, "details": exc.details})

    handler = AsyncMock()
    getattr(handler, method).side_effect = error
    with (
        patch.object(router_module, "_handler", return_value=handler),
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "logger"),
        patch.object(router_module.config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 540),
        patch.object(
            router_module.FilterParams, "resolve", new=AsyncMock(return_value=router_module.LocalAnalyticsFilter)
        ),
    ):
        resp = TestClient(app, raise_server_exceptions=False).get(_PATHS[method])

    assert resp.status_code == code
    assert text in resp.json()["details"]
