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

"""GET /sessions?is_branch_unattributed: needs `repositories`, excludes `branch`, ANDs with the scope."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

BASE = "/v1/analytics/cli-analytics"


@pytest.fixture()
def env():
    handler = MagicMock(spec=LocalAnalyticsHandler)
    pricer = SimpleNamespace(unpriced=[], catalog_source=None)
    handler.get_sessions = AsyncMock(
        return_value=({"sessions": [], "page": 0, "per_page": 20, "total": 0}, pricer, None)
    )
    storage = SimpleNamespace(raw_retention_days=90, reader=MagicMock())
    patches = [
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "_handler", return_value=handler),
        patch.object(router_module, "get_cli_analytics_storage", return_value=storage),
    ]
    for p in patches:
        p.start()

    def client() -> TestClient:
        app = FastAPI()
        app.include_router(router_module.router)
        user = User.model_construct(
            id="u1", email="u@example.com", is_admin=True, is_maintainer=False, admin_project_names=[], roles=[]
        )

        def _auth(request: Request) -> User:
            request.state.user = user
            return user

        app.dependency_overrides[authenticate] = _auth

        @app.exception_handler(ExtendedHTTPException)
        async def _extended(request, exc: ExtendedHTTPException):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=exc.code, content={"message": exc.message})

        return TestClient(app, raise_server_exceptions=False)

    yield SimpleNamespace(client=client, handler=handler)
    for p in patches:
        p.stop()


def test_flag_with_repositories_and_projects_reaches_the_handler_with_the_scope(env):
    response = env.client().get(
        f"{BASE}/sessions", params={"repositories": "R", "projects": "P", "is_branch_unattributed": "true"}
    )

    assert response.status_code == 200
    args = env.handler.get_sessions.await_args
    f = args.args[0]
    assert (f.repositories, f.projects) == (["R"], ["P"])
    assert args.args[-1] is True
    assert f.branch_unattributed is False  # the handler sets it; the mock does not


def test_flag_without_repositories_is_a_validation_error(env):
    response = env.client().get(f"{BASE}/sessions", params={"is_branch_unattributed": "true"})

    assert response.status_code == 422
    env.handler.get_sessions.assert_not_called()


def test_flag_with_branch_is_a_validation_error(env):
    response = env.client().get(
        f"{BASE}/sessions", params={"repositories": "R", "branch": "main", "is_branch_unattributed": "true"}
    )

    assert response.status_code == 422
    env.handler.get_sessions.assert_not_called()


def test_omitting_the_flag_leaves_the_request_unchanged(env):
    response = env.client().get(f"{BASE}/sessions", params={"repositories": "R"})

    assert response.status_code == 200
    args = env.handler.get_sessions.await_args
    assert args.args[-1] is False
    assert args.args[0].branch_unattributed is False
