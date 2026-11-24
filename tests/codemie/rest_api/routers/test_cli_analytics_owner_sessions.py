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
from codemie.repository.cli_analytics.ports import StatementTimeout
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
        self.handler.authorize_session = AsyncMock(return_value=True)

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
    for p in reversed(created):
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


def test_non_admin_gets_200_on_sessions_and_on_overview(make):
    client, _ = make(ORDINARY())

    assert client.get(f"{BASE}/sessions").status_code == 200
    # EPMCDME-17657: every role reaches the dashboard. The fake overview payload is empty, so a body error may follow.
    assert client.get(f"{BASE}/overview").status_code != 403


def test_non_admin_can_reach_session_detail_route_past_the_gate(make):
    client, env = make(ORDINARY())
    env.handler.get_session_detail = AsyncMock(return_value=({"trace_id": "s1", "start_time": ""}, [], "yaml"))

    resp = client.get(f"{BASE}/sessions/s1")

    assert resp.status_code != 403


def test_non_admin_default_list_is_their_own_shared_sessions_and_selectors_cannot_widen_it(make):
    client, env = make(ORDINARY())

    resp = client.get(
        f"{BASE}/sessions",
        params={"projects": "p1,p2", "users": "other@example.com", "is_project_unattributed": "true"},
    )

    f = env.sessions_filter()
    assert resp.status_code == 200
    scope = f.scope
    assert (scope.mode, scope.viewer_email, scope.own_shared) == ("exclude", "dev@example.com", True)
    # The scope is the restriction: no admin project, not unrestricted, so only the viewer's own rows match.
    assert (scope.unrestricted, scope.admin_projects, scope.sees_private) == (False, frozenset(), False)
    assert f.deny_all is True  # sessions without a project are for unrestricted viewers only


@pytest.mark.parametrize("personal_only", ["true", "false"])
def test_non_admin_personal_only_is_own_only_and_supplied_filters_cannot_widen_it(make, personal_only):
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
    if personal_only == "true":
        assert f.scope.mode == "own_only"
        assert (f.users, f.projects, f.deny_all, f.project_unattributed) == (None, None, False, False)
    else:
        assert f.scope.mode == "exclude"
    assert f.scope.viewer_email == "dev@example.com"
    assert f.scope.sees_private is False


def test_non_admin_without_email_gets_an_empty_list_and_a_scope_that_matches_nothing(make):
    from codemie.repository.cli_analytics.scope import sql_list_condition

    client, env = make(ORDINARY(email=""))

    resp = client.get(f"{BASE}/sessions")

    f = env.sessions_filter()
    assert resp.status_code == 200
    assert resp.json()["data"]["sessions"] == []
    assert f.scope.viewer_email is None
    assert sql_list_condition(f.scope) == ("false", {})


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


def test_admin_default_stays_exclude_and_the_scope_keeps_the_admin_projects(make):
    client, env = make(PROJECT_ADMIN())

    resp = client.get(f"{BASE}/sessions", params={"projects": "p1,p2"})

    f = env.sessions_filter()
    assert resp.status_code == 200
    assert f.scope.mode == "exclude"
    assert f.scope.admin_projects == frozenset({"p1"})
    assert f.projects == ["p1", "p2"]  # a selector only narrows; the scope decides what is readable


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
        (_role(), False, [], "exclude"),  # ordinary: their own shared sessions through the scope
        (_role(is_auditor=True), True, [], "exclude"),  # auditor: unrestricted read-only (EPMCDME-17657)
        (_role(admin_project_names=["p1"]), False, ["p1"], "exclude"),  # CR-003: real property keeps the list
        (_role(is_auditor=True, admin_project_names=["p1"]), True, ["p1"], "exclude"),  # auditor is unrestricted
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

    sees_private = bool(user.is_admin or user.is_auditor)  # admin, maintainer and auditor (EPMCDME-17657)
    assert default.sees_private is sees_private
    assert default.own_shared is True

    client.get(f"{BASE}/sessions", params={"personal_only": "true"})
    personal = env.sessions_filter().scope
    assert (personal.mode, personal.sees_private) == ("own_only", sees_private)

    client.get(f"{BASE}/sessions/s1")
    detail = env.handler.get_session_detail.await_args.kwargs["scope"]
    assert (detail.mode, detail.viewer_email, detail.unrestricted) == ("detail", "r@example.com", admin)
    assert detail.admin_projects == frozenset(projects)
    assert (detail.sees_private, detail.own_shared) == (sees_private, True)


def test_an_ordinary_auditor_detail_runs_the_executor_with_an_unrestricted_private_scope(make):
    """The scope the route builds is what the authorize executor receives: an auditor is unrestricted and sees
    private Personal sessions; a denied read is still the same 404 as a missing session."""
    client, env = make(_role(is_auditor=True))
    reader = env.handler
    reader.authorize_session = AsyncMock(return_value=False)
    env.handler.get_session_detail = AsyncMock()

    resp = client.get(f"{BASE}/sessions/foreign")

    scope = reader.authorize_session.await_args.args[1]
    assert (scope.mode, scope.unrestricted, scope.sees_private) == ("detail", True, True)
    assert resp.status_code == 404
    env.handler.get_session_detail.assert_not_awaited()


def test_an_auditor_with_a_project_admin_role_is_unrestricted_on_the_aggregates(make):
    """The aggregate `_resolve` path: the auditor-project-admin is unrestricted, so selected projects pass through."""
    client, env = make(_role(is_auditor=True, admin_project_names=["p1"]))

    client.get(f"{BASE}/overview", params={"projects": "p1,other"})

    f = env.handler.get_overview.await_args.args[0]
    assert (sorted(f.projects), f.deny_all, f.scope.unrestricted, f.scope.sees_private) == (
        ["other", "p1"],
        False,
        True,
        True,
    )


def test_an_ordinary_auditor_reads_the_aggregates_unrestricted(make):
    client, env = make(_role(is_auditor=True))

    # The fake overview payload is empty, so the response may be a 400; the gate and the scope are the point.
    assert client.get(f"{BASE}/overview").status_code != 403
    f = env.handler.get_overview.await_args.args[0]
    assert (f.scope.unrestricted, f.deny_all, f.scope.sees_private) == (True, False, True)


@pytest.mark.parametrize(
    ("make_user", "denied"),
    [(lambda: _role(is_auditor=True), False), (PROJECT_ADMIN, True)],
    ids=["auditor-allowed", "project-admin-denied"],
)
def test_project_unattributed_is_allowed_for_the_auditor_and_denied_for_a_project_admin(make, make_user, denied):
    client, env = make(make_user())

    client.get(f"{BASE}/sessions", params={"is_project_unattributed": "true"})

    assert env.sessions_filter().deny_all is denied


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


@pytest.mark.parametrize("error", [asyncpg.QueryCanceledError("canceling statement"), StatementTimeout()])
def test_a_session_detail_statement_timeout_is_a_504_naming_the_limit(make, error):
    client, env = make(ORDINARY())
    env.handler.get_session_detail = AsyncMock(side_effect=error)

    resp = client.get(f"{BASE}/sessions/s1")

    assert resp.status_code == 504
    assert resp.json()["message"] == "The session detail took too long to build"


def test_a_client_side_sessions_timeout_is_a_504_too(make):
    client, env = make(ORDINARY())
    env.handler.get_sessions = AsyncMock(side_effect=StatementTimeout())  # the client command timeout of a statement

    resp = client.get(f"{BASE}/sessions", params={"search": "x" * 500, "framework": "f" * 500})

    assert resp.status_code == 504
    assert resp.json()["message"] == "The sessions list took too long to build"


def test_a_pool_timeout_on_the_sessions_route_is_a_503(make):
    client, env = make(ORDINARY())
    env.handler.get_sessions = AsyncMock(side_effect=TimeoutError())

    resp = client.get(f"{BASE}/sessions")

    assert resp.status_code == 503


def test_other_storage_errors_on_the_sessions_route_keep_their_503(make):
    client, env = make(ORDINARY())
    env.handler.get_sessions = AsyncMock(side_effect=asyncpg.PostgresError("down"))

    assert client.get(f"{BASE}/sessions").status_code == 503
