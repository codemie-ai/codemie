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

"""Per-role RBAC matrix of the CLI analytics read routes (EPMCDME-17657)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

BASE = "/v1/analytics/cli-analytics"
GATED = [
    "overview",
    "cost",
    "users",
    "user-charts",
    "repositories",
    "projects",
    "tools",
    "activity",
    "efficiency",
    "frameworks",
]
HANDLER_METHODS = [
    "get_overview",
    "get_cost",
    "get_users",
    "get_user_charts",
    "get_repositories",
    "get_projects",
    "get_tools",
    "get_activity",
    "get_efficiency",
    "get_sessions",
    "get_session_detail",
    "authorize_session",
]


def _role(**kw) -> User:
    # model_construct skips the validator that makes every user an admin under ENV=local.
    base = {"id": "u1", "email": "r@example.com", "roles": [], "is_admin": False, "is_maintainer": False}
    return User.model_construct(**{"admin_project_names": [], **base, **kw})


ROLES = {
    "super": lambda: _role(is_admin=True),
    # The User validator also sets is_admin for a maintainer; model_construct skips it, so set both.
    "maintainer": lambda: _role(is_admin=True, is_maintainer=True),
    "project-admin": lambda: _role(admin_project_names=["p1"]),
    "auditor": lambda: _role(is_auditor=True),
    "regular": lambda: _role(),
}
SEES_PRIVATE = ["super", "maintainer", "auditor"]


class _Env:
    def __init__(self) -> None:
        self.handler = MagicMock()
        pricer = MagicMock(unpriced=[], catalog_source="yaml")
        for name in HANDLER_METHODS:
            setattr(self.handler, name, AsyncMock(return_value=({}, pricer, None)))
        self.handler.get_sessions = AsyncMock(
            return_value=({"sessions": [], "page": 0, "per_page": 20, "total": 0}, pricer, None)
        )
        self.handler.get_session_detail = AsyncMock(return_value=({"trace_id": "s1", "start_time": ""}, [], "yaml"))

    def sessions_scope(self):
        return self.handler.get_sessions.await_args.args[0].scope

    def detail_scope(self):
        return self.handler.get_session_detail.await_args.kwargs["scope"]


@pytest.fixture()
def make():
    patches = []

    def factory(user: User):
        env = _Env()
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

        storage = MagicMock(raw_retention_days=90, reader=MagicMock())
        storage.reader.authorize_session = AsyncMock(return_value=True)
        for p in (
            patch.object(router_module, "_ensure_enabled", return_value=None),
            patch.object(router_module, "_handler", return_value=env.handler),
            patch.object(router_module, "get_cli_analytics_storage", return_value=storage),
        ):
            p.start()
            patches.append(p)
        return TestClient(app, raise_server_exceptions=False), env

    yield factory
    for p in reversed(patches):
        p.stop()


@pytest.mark.parametrize("path", GATED)
@pytest.mark.parametrize("role", ROLES)
def test_every_role_reaches_every_dashboard_route(make, role, path):
    client, _ = make(ROLES[role]())

    # The fake payloads are empty, so a body error may follow; the point is that no role is turned away.
    assert client.get(f"{BASE}/{path}").status_code != 403


@pytest.mark.parametrize(
    ("role", "unrestricted", "projects", "own_shared"),
    [
        ("super", True, frozenset(), True),
        ("maintainer", True, frozenset(), True),
        ("auditor", True, frozenset(), True),
        ("project-admin", False, frozenset({"p1"}), True),
        ("regular", False, frozenset(), True),
    ],
)
def test_scope_by_role(make, role, unrestricted, projects, own_shared):
    client, env = make(ROLES[role]())

    client.get(f"{BASE}/overview")

    f = env.handler.get_overview.await_args.args[0]
    assert (f.scope.unrestricted, f.scope.admin_projects, f.scope.mode) == (unrestricted, projects, "exclude")
    assert f.scope.own_shared is own_shared
    assert f.deny_all is False


def test_regular_user_scope_is_the_viewers_own_email_and_no_project(make):
    client, env = make(ROLES["regular"]())

    client.get(f"{BASE}/overview")

    scope = env.handler.get_overview.await_args.args[0].scope
    assert (scope.viewer_email, scope.admin_projects, scope.unrestricted) == ("r@example.com", frozenset(), False)


def test_regular_user_cannot_widen_the_scope_with_selectors(make):
    client, env = make(ROLES["regular"]())

    client.get(f"{BASE}/overview", params={"projects": "foreign", "users": "someone@example.com"})

    scope = env.handler.get_overview.await_args.args[0].scope
    assert (scope.unrestricted, scope.admin_projects, scope.sees_private) == (False, frozenset(), False)


@pytest.mark.parametrize("role", ROLES)
def test_project_selector_never_widens_the_scope(make, role):
    client, env = make(ROLES[role]())

    client.get(f"{BASE}/overview", params={"projects": "foreign"})

    f = env.handler.get_overview.await_args.args[0]
    assert f.projects == ["foreign"]  # a selector only narrows; the scope decides what is readable
    assert f.scope.admin_projects == (frozenset({"p1"}) if role == "project-admin" else frozenset())


@pytest.mark.parametrize(("role", "denied"), [(r, r not in ("super", "maintainer", "auditor")) for r in ROLES])
def test_project_unattributed_by_role(make, role, denied):
    client, env = make(ROLES[role]())

    client.get(f"{BASE}/sessions", params={"is_project_unattributed": "true"})

    f = env.handler.get_sessions.await_args.args[0]
    assert f.deny_all is denied


@pytest.mark.parametrize("role", ROLES)
def test_private_visibility_by_role(make, role):
    client, env = make(ROLES[role]())
    expected = role in SEES_PRIVATE

    client.get(f"{BASE}/sessions")
    assert env.sessions_scope().sees_private is expected
    client.get(f"{BASE}/sessions", params={"personal_only": "true"})
    assert env.sessions_scope().sees_private is expected
    client.get(f"{BASE}/sessions/s1")
    assert env.detail_scope().sees_private is expected
    client.get(f"{BASE}/overview")
    assert env.handler.get_overview.await_args.args[0].scope.sees_private is expected


@pytest.mark.parametrize("role", ROLES)
def test_sessions_by_role(make, role):
    client, env = make(ROLES[role]())

    client.get(f"{BASE}/sessions")
    assert env.sessions_scope().mode == "exclude"
    client.get(f"{BASE}/sessions", params={"personal_only": "true"})
    assert env.sessions_scope().mode == "own_only"
    client.get(f"{BASE}/sessions/s1")
    assert env.detail_scope().mode == "detail"


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
@pytest.mark.parametrize("path", GATED)
def test_no_write_methods(make, method, path):
    client, _ = make(ROLES["auditor"]())

    assert getattr(client, method)(f"{BASE}/{path}").status_code == 405


@pytest.mark.parametrize("role", ROLES)
def test_classification_policy_is_open_to_every_authenticated_role(make, role):
    client, _ = make(ROLES[role]())

    assert client.get(f"{BASE}/classification-policy").status_code != 403
