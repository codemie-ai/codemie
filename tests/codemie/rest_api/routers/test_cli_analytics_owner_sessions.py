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

"""`/sessions` owner self-view: the relaxed gate, `personal_only` and the forced own-Personal scope."""

from __future__ import annotations

import inspect
import typing
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

BASE = "/v1/analytics/cli-analytics"


class _Env:
    def __init__(self, user: User) -> None:
        self.user = user
        self.handler = MagicMock()
        pricer = MagicMock(unpriced=[], catalog_source="yaml")
        self.handler.get_sessions = AsyncMock(
            return_value=({"sessions": [], "page": 0, "per_page": 20, "total": 0}, pricer, None)
        )
        self.handler.get_overview = AsyncMock(return_value=({}, pricer, None))

    def sessions_filter(self):
        return self.handler.get_sessions.await_args.args[0]


def _client(user: User):
    env = _Env(user)
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

    storage = SimpleNamespace(raw_retention_days=90, reader=MagicMock())
    storage.reader.authorize_session = AsyncMock(return_value=True)
    patches = [
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "_handler", return_value=env.handler),
        patch.object(router_module, "get_cli_analytics_storage", return_value=storage),
    ]
    for p in patches:
        p.start()
    return TestClient(app, raise_server_exceptions=False), env, patches


@pytest.fixture()
def make():
    created = []

    def factory(user: User):
        client, env, patches = _client(user)
        created.extend(patches)
        return client, env

    yield factory
    for p in created:
        p.stop()


def _user(email: str, *, is_admin: bool = False, admin_projects: list[str] | None = None) -> User:
    # model_construct skips the validator that makes every user an admin under ENV=local.
    return User.model_construct(
        id="u1",
        email=email,
        is_admin=is_admin,
        is_maintainer=False,
        admin_project_names=admin_projects or [],
        roles=[],
    )


ORDINARY = lambda email="dev@example.com": _user(email)  # noqa: E731
PROJECT_ADMIN = lambda: _user("pa@example.com", admin_projects=["p1"])  # noqa: E731
SUPER = lambda: _user("root@example.com", is_admin=True)  # noqa: E731


def test_non_admin_gets_200_on_sessions_and_403_on_overview(make):
    client, _ = make(ORDINARY())

    assert client.get(f"{BASE}/sessions").status_code == 200
    assert client.get(f"{BASE}/overview").status_code == 403


def test_non_admin_can_reach_session_detail_route_past_the_gate(make):
    client, env = make(ORDINARY())
    env.handler.get_session_detail = AsyncMock(return_value=({"trace_id": "s1", "start_time": ""}, [], "yaml"))

    resp = client.get(f"{BASE}/sessions/s1")

    assert resp.status_code != 403


@pytest.mark.parametrize("personal_only", ["true", "false"])
def test_non_admin_is_always_own_only_and_supplied_filters_cannot_widen_it(make, personal_only):
    client, env = make(ORDINARY())

    resp = client.get(
        f"{BASE}/sessions",
        params={
            "personal_only": personal_only,
            "projects": "p1,p2",
            "users": "other@example.com",
            "is_project_unattributed": "true",
        },
    )

    f = env.sessions_filter()
    assert resp.status_code == 200
    assert f.scope.mode == "own_only"
    assert f.scope.viewer_email == "dev@example.com"
    assert (f.users, f.projects, f.deny_all, f.project_unattributed) == (None, None, False, False)


def test_non_admin_without_email_gets_an_empty_list_and_an_own_only_scope_that_matches_nothing(make):
    client, env = make(ORDINARY(email=""))

    resp = client.get(f"{BASE}/sessions")

    f = env.sessions_filter()
    assert resp.status_code == 200
    assert resp.json()["data"]["sessions"] == []
    assert (f.scope.mode, f.scope.viewer_email) == ("own_only", None)


@pytest.mark.parametrize("make_user", [SUPER, PROJECT_ADMIN])
def test_admin_personal_only_is_own_only_and_ignores_projects_users_and_unattributed(make, make_user):
    client, env = make(make_user())

    resp = client.get(
        f"{BASE}/sessions",
        params={
            "personal_only": "true",
            "projects": "p9",
            "users": "x@example.com",
            "is_project_unattributed": "true",
            "repositories": "repo",
            "subscription_type": "personal",
        },
    )

    f = env.sessions_filter()
    assert resp.status_code == 200
    assert f.scope.mode == "own_only"
    assert f.scope.viewer_email == make_user().email
    assert (f.users, f.projects, f.deny_all, f.project_unattributed) == (None, None, False, False)
    assert f.repositories == ["repo"]  # other filters still narrow
    assert f.subscription_types == ["personal"]


def test_admin_default_stays_exclude_with_todays_project_scope(make):
    client, env = make(PROJECT_ADMIN())

    resp = client.get(f"{BASE}/sessions", params={"projects": "p1,p2"})

    f = env.sessions_filter()
    assert resp.status_code == 200
    assert f.scope.mode == "exclude"
    assert f.projects == ["p1"]


def test_personal_only_on_an_aggregate_route_is_ignored(make):
    client, env = make(SUPER())

    client.get(f"{BASE}/overview", params={"personal_only": "true"})

    assert env.handler.get_overview.await_args.args[0].scope.mode == "exclude"


def test_personal_only_is_declared_on_sessions_only():
    app = FastAPI()
    app.include_router(router_module.router)
    paths = app.openapi()["paths"]

    declared = {
        path: any(p["name"] == "personal_only" for p in op["get"].get("parameters", []))
        for path, op in paths.items()
        if "get" in op
    }
    assert [p for p, has in declared.items() if has] == [f"{BASE}/sessions"]


def _role(**kw) -> User:
    return User.model_construct(
        id="u1",
        email="r@example.com",
        roles=kw.pop("roles", []),
        **{"is_admin": False, "is_maintainer": False, "admin_project_names": [], **kw},
    )


@pytest.mark.parametrize(
    ("user", "admin", "projects", "mode"),
    [
        (_role(), False, [], "own_only"),  # ordinary
        (_role(is_auditor=True), False, [], "own_only"),  # auditor: read-only elsewhere, no CLI shared-session right
        (_role(admin_project_names=["p1"]), False, ["p1"], "exclude"),  # CR-003: real property keeps the list
        (_role(is_auditor=True, admin_project_names=["p1"]), False, ["p1"], "exclude"),  # auditor does not erase them
        (_role(is_admin=True), True, [], "exclude"),
        (_role(is_auditor=True, is_admin=True), True, [], "exclude"),
        (_role(is_maintainer=True, is_admin=True), True, [], "exclude"),  # the User validator sets is_admin
    ],
    ids=["ordinary", "auditor", "project-admin", "auditor+project-admin", "admin", "auditor+admin", "maintainer"],
)
def test_sessions_role_matrix_on_the_real_user_for_list_and_detail(make, user, admin, projects, mode):
    """The REAL User drives list and detail through `viewer_access` (no hand-built access context): an auditor is
    never widened, a project admin keeps only their own projects, and personal_only and detail never widen any role
    to others' Personal."""
    client, env = make(user)
    env.handler.get_session_detail = AsyncMock(return_value=({"trace_id": "s1", "start_time": ""}, [], "yaml"))

    assert client.get(f"{BASE}/sessions").status_code == 200
    default = env.sessions_filter().scope
    assert (default.mode, default.viewer_email, default.unrestricted) == (mode, "r@example.com", admin)
    assert default.admin_projects == frozenset(projects)

    client.get(f"{BASE}/sessions", params={"personal_only": "true"})
    assert env.sessions_filter().scope.mode == "own_only"

    client.get(f"{BASE}/sessions/s1")
    detail = env.handler.get_session_detail.await_args.kwargs["scope"]
    assert (detail.mode, detail.viewer_email, detail.unrestricted) == ("detail", "r@example.com", admin)
    assert detail.admin_projects == frozenset(projects)


def test_an_ordinary_auditor_detail_runs_the_executor_with_own_private_only_and_a_denied_read_is_a_404(make):
    """The scope the route builds is what the authorize executor receives: an auditor is not unrestricted and has no
    admin project, so a shared or foreign session is denied; the response is the same 404 as a missing session."""
    client, env = make(_role(is_auditor=True))
    reader = router_module.get_cli_analytics_storage().reader
    reader.authorize_session = AsyncMock(return_value=False)
    env.handler.get_session_detail = AsyncMock()

    resp = client.get(f"{BASE}/sessions/foreign")

    scope = reader.authorize_session.await_args.args[1]
    assert (scope.mode, scope.unrestricted, scope.admin_projects) == ("detail", False, frozenset())
    assert resp.status_code == 404
    env.handler.get_session_detail.assert_not_awaited()


def test_an_auditor_with_a_project_admin_role_sees_only_the_administered_projects_on_the_aggregates(make):
    """The aggregate `_resolve` path: the gate admits the auditor-project-admin; the filter is limited to their own
    projects instead of being widened to unrestricted by the shared AccessFilter."""
    client, env = make(_role(is_auditor=True, admin_project_names=["p1"]))

    client.get(f"{BASE}/overview", params={"projects": "p1,other"})  # the empty fake payload is not the point

    f = env.handler.get_overview.await_args.args[0]
    assert (f.projects, f.deny_all, f.scope.unrestricted, f.scope.admin_projects) == (
        ["p1"],
        False,
        False,
        frozenset({"p1"}),
    )


def test_an_ordinary_auditor_stays_out_of_the_aggregates(make):
    client, env = make(_role(is_auditor=True))

    assert client.get(f"{BASE}/overview").status_code == 403
    env.handler.get_overview.assert_not_awaited()


def test_the_sessions_sort_choices_are_exactly_the_sorts_the_database_pages():
    """A sort the router accepts must have a database order: a fourth one cannot reach the router without SQL."""
    from codemie.repository.cli_analytics.ports import SESSION_PAGE_SORTS

    hints = typing.get_type_hints(inspect.unwrap(router_module.get_sessions))
    assert typing.get_args(hints["sort_by"]) == SESSION_PAGE_SORTS


def test_a_sessions_query_timeout_is_a_504_with_a_clear_message_on_the_sessions_route_only(make):
    client, env = make(ORDINARY())
    env.handler.get_sessions = AsyncMock(side_effect=asyncpg.QueryCanceledError("canceling statement"))

    resp = client.get(f"{BASE}/sessions", params={"sort_by": "cost_usd"})

    assert resp.status_code == 504
    assert resp.json()["message"] == "The sessions list took too long to build"


def test_a_client_side_sessions_timeout_is_a_504_too(make):
    client, env = make(ORDINARY())
    env.handler.get_sessions = AsyncMock(side_effect=TimeoutError())

    resp = client.get(f"{BASE}/sessions", params={"search": "x" * 500, "framework": "f" * 500})

    assert resp.status_code == 504
    assert resp.json()["message"] == "The sessions list took too long to build"


def test_other_storage_errors_on_the_sessions_route_keep_their_503(make):
    client, env = make(ORDINARY())
    env.handler.get_sessions = AsyncMock(side_effect=asyncpg.PostgresError("down"))

    assert client.get(f"{BASE}/sessions").status_code == 503
