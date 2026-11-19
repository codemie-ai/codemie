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

"""GET /branches: required repository, the project scope through FilterParams.resolve, no rows for a non-super-admin."""

from __future__ import annotations

from contextlib import asynccontextmanager
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


def _user(*, is_admin: bool = False, admin_projects: list[str] | None = None) -> User:
    return User.model_construct(
        id="u1",
        email="u@example.com",
        is_admin=is_admin,
        is_maintainer=False,
        admin_project_names=admin_projects or [],
        roles=[],
    )


@pytest.fixture()
def env():
    repo = MagicMock()

    @asynccontextmanager
    async def bounded():
        yield repo

    async def ranking(f, rates, page, per_page):
        if f.deny_all:
            return [{"part": "count", "n": 0}]
        return [
            {"part": "group", "key": "1", "branch": "main", "n": 2, "cost_usd": 1.0, "turns": 1, "tool_calls": 0},
            {"part": "count", "n": 1},
        ]

    repo.bounded = bounded
    repo.get_cost_models = AsyncMock(return_value=[])
    repo.get_branch_ranking = AsyncMock(side_effect=ranking)
    handler = LocalAnalyticsHandler(repo)
    storage = SimpleNamespace(raw_retention_days=90, reader=MagicMock())
    patches = [
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "_handler", return_value=handler),
        patch.object(router_module, "get_cli_analytics_storage", return_value=storage),
    ]
    for p in patches:
        p.start()

    def client(user: User) -> TestClient:
        app = FastAPI()
        app.include_router(router_module.router)

        def _auth(request: Request) -> User:
            request.state.user = user
            return user

        app.dependency_overrides[authenticate] = _auth

        @app.exception_handler(ExtendedHTTPException)
        async def _extended(request, exc: ExtendedHTTPException):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=exc.code, content={"message": exc.message})

        return TestClient(app, raise_server_exceptions=False)

    yield SimpleNamespace(client=client, repo=repo)
    for p in patches:
        p.stop()


def test_a_page_of_branches_is_served_with_the_repository_and_project_scope(env):
    response = env.client(_user(is_admin=True)).get(
        f"{BASE}/branches", params={"repository": "org/repo", "projects": "P", "page": 0, "per_page": 10}
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert [r["branch"] for r in body["rows"]] == ["main"]
    assert body["pagination"] == {"page": 0, "per_page": 10, "total_count": 1, "has_more": False}
    f = env.repo.get_branch_ranking.await_args.args[0]
    assert (f.repositories, f.projects) == (["org/repo"], ["P"])


def test_repository_is_anded_with_the_repositories_filter(env):
    response = env.client(_user(is_admin=True)).get(
        f"{BASE}/branches", params={"repository": "a", "repositories": "b,c"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["rows"] == []
    env.repo.get_branch_ranking.assert_not_called()


def test_missing_repository_is_a_validation_error(env):
    assert env.client(_user(is_admin=True)).get(f"{BASE}/branches").status_code == 422


def test_super_admin_no_project_scope_reads_the_unattributed_projects(env):
    env.client(_user(is_admin=True)).get(
        f"{BASE}/branches", params={"repository": "r", "is_project_unattributed": True}
    )

    f = env.repo.get_branch_ranking.await_args.args[0]
    assert (f.project_unattributed, f.deny_all) == (True, False)


def test_non_super_admin_no_project_scope_gets_no_rows_with_200(env):
    response = env.client(_user(admin_projects=["p1"])).get(
        f"{BASE}/branches", params={"repository": "r", "is_project_unattributed": True}
    )

    assert response.status_code == 200
    assert response.json()["data"]["rows"] == []
    assert response.json()["data"]["pagination"]["total_count"] == 0
