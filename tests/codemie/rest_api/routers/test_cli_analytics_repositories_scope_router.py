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


"""GET /repositories: the project scope reaches the reader (a repository shared by two projects counts only the
selected project's sessions), and a non-super-admin asking for the unattributed projects gets an empty 200."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

BASE = "/v1/analytics/cli-analytics"
# One repository, two sessions of project P and three of project Q.
SESSIONS = {"P": 2, "Q": 3}


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

    async def ranking(f, include_branches, search, rates, page, per_page, unattributed_last=False):
        if f.deny_all:
            return [{"part": "count", "n": 0}]
        # the reader counts only the sessions of the projects in scope
        n = sum(count for project, count in SESSIONS.items() if not f.projects or project in f.projects)
        return [
            {"part": "group", "key": "1", "repository": "org/shared", "n": n, "cost_usd": 1.0},
            {"part": "count", "n": 1},
        ]

    repo.bounded = bounded
    repo.get_cost_models = AsyncMock(return_value=[])
    repo.get_repository_ranking = AsyncMock(side_effect=ranking)
    repo.get_repository_facts = AsyncMock(return_value=[])
    repo.get_repository_buckets = AsyncMock(return_value=[])
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
        return TestClient(app, raise_server_exceptions=False)

    yield SimpleNamespace(client=client, repo=repo)
    for p in patches:
        p.stop()


def _sessions_of_shared_repository(response) -> int:
    rows = response.json()["data"]["rows"]
    assert [r["repository"] for r in rows] == ["org/shared"]
    return rows[0]["session_count"]


def test_a_repository_shared_by_two_projects_counts_only_the_selected_projects_sessions(env):
    admin = env.client(_user(is_admin=True))

    scoped = admin.get(
        f"{BASE}/repositories", params={"projects": "P", "page": 0, "per_page": 10, "unattributed_last": True}
    )
    whole = admin.get(f"{BASE}/repositories", params={"page": 0, "per_page": 10, "unattributed_last": True})

    assert scoped.status_code == whole.status_code == 200
    assert env.repo.get_repository_ranking.await_args_list[0].args[0].projects == ["P"]
    assert _sessions_of_shared_repository(scoped) == SESSIONS["P"]
    assert _sessions_of_shared_repository(whole) == sum(SESSIONS.values())


def test_non_super_admin_asking_for_the_unattributed_projects_gets_an_empty_200(env):
    response = env.client(_user(admin_projects=["P"])).get(
        f"{BASE}/repositories",
        params={"is_project_unattributed": True, "page": 0, "per_page": 10, "unattributed_last": True},
    )

    assert response.status_code == 200
    assert response.json()["data"]["rows"] == []
    assert response.json()["data"]["pagination"]["total_count"] == 0
    assert env.repo.get_repository_ranking.await_args.args[0].deny_all is True
