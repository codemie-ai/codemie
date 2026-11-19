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

"""Request-level checks of the IDP lock: what the locked routes answer, and which mutating routes stay open.

The app is built from the real user-management and project routers. Every other dependency of a route is
replaced by a stub, so the answer comes from the lock alone.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers import user_management_router
from codemie.rest_api.routers.projects import router as projects_router
from codemie.rest_api.security.idp_lock import require_no_idp_lock

_LOCK_MESSAGE = "User management is controlled by the identity provider."
_MUTATING = {"POST", "PUT", "DELETE", "PATCH"}

LOCKED = {
    ("DELETE", "/v1/admin/users/{user_id}"),
    ("DELETE", "/v1/admin/users/{user_id}/knowledge-bases/{kb_name}"),
    ("DELETE", "/v1/admin/users/{user_id}/projects/{project_name}"),
    ("DELETE", "/v1/projects/{projectName}"),
    ("DELETE", "/v1/projects/{projectName}/assignment/{userId}"),
    ("DELETE", "/v1/projects/{projectName}/assignments"),
    ("POST", "/v1/admin/users"),
    ("POST", "/v1/admin/users/{user_id}/knowledge-bases"),
    ("POST", "/v1/admin/users/{user_id}/projects"),
    ("POST", "/v1/admin/users/{user_id}/reactivate"),
    ("POST", "/v1/projects"),
    ("POST", "/v1/projects/{projectName}/assignment"),
    ("POST", "/v1/projects/{projectName}/assignments"),
    ("POST", "/v1/projects/{projectName}/import-users"),
    ("POST", "/v1/projects/{projectName}/import-users/validate"),
    ("PUT", "/v1/admin/users/{user_id}/password"),
    ("PUT", "/v1/admin/users/{user_id}/projects/{project_name}"),
    ("PUT", "/v1/projects/{projectName}/assignment/{userId}"),
    ("PUT", "/v1/projects/{projectName}/ownership"),
}

# Platform-managed even in IDP mode: profile and platform roles, default project, budgets, project settings,
# and the id remap, which is how a pre-Keycloak user is moved onto the Keycloak-issued id.
OPEN_BY_DESIGN = {
    ("POST", "/v1/admin/users/remap"),
    ("PUT", "/v1/projects/{projectName}/repositories"),
    ("DELETE", "/v1/projects/{projectName}/repositories"),
    ("DELETE", "/v1/projects/{projectName}/allowed-models"),
    ("PUT", "/v1/admin/users/{user_id}"),
    ("PUT", "/v1/admin/users/{user_id}/projects/{project_name}/default"),
    ("DELETE", "/v1/admin/users/{user_id}/projects/{project_name}/default"),
    ("PUT", "/v1/admin/users/budgets/bulk"),
    ("POST", "/v1/admin/users/{user_id}/budgets/reset"),
    ("PUT", "/v1/admin/users/{user_id}/budgets"),
    ("PATCH", "/v1/projects/{projectName}"),
    ("PATCH", "/v1/projects/{projectName}/allowed-models"),
}


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ExtendedHTTPException)
    async def _extended(_request: Request, exc: ExtendedHTTPException):  # same shape as the application's handler
        return JSONResponse(
            status_code=exc.code, content={"error": {"message": exc.message, "details": exc.details, "help": exc.help}}
        )

    app.include_router(user_management_router.router)
    app.include_router(projects_router)
    return app


def _dependency_calls(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependency_calls(sub)


def _mutating_routes(app: FastAPI) -> dict[tuple[str, str], APIRoute]:
    return {
        (method, route.path): route
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods & _MUTATING
    }


APP = _build_app()
ROUTES = _mutating_routes(APP)
LOCKED_ROUTES = {key for key, route in ROUTES.items() if require_no_idp_lock in _dependency_calls(route.dependant)}


def test_the_locked_routes_are_exactly_the_expected_ones():
    assert LOCKED_ROUTES == LOCKED


def test_every_other_mutating_route_is_open_on_purpose():
    """A new mutating route must be locked or listed in OPEN_BY_DESIGN: it cannot skip the decision."""
    assert set(ROUTES) - LOCKED_ROUTES == OPEN_BY_DESIGN


def _stub_other_dependencies(app: FastAPI, route: APIRoute) -> None:
    for call in set(_dependency_calls(route.dependant)) - {require_no_idp_lock}:
        app.dependency_overrides[call] = lambda: None


def _path(route_path: str) -> str:
    return route_path.replace("{", "x-").replace("}", "")


async def _send(method: str, route: APIRoute) -> tuple[int, dict]:
    app = _build_app()
    _stub_other_dependencies(app, route)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, _path(route.path), json={})
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {}


@pytest.mark.asyncio
@pytest.mark.parametrize("method, path", sorted(LOCKED), ids=[f"{m} {p}" for m, p in sorted(LOCKED)])
async def test_locked_route_answers_403_in_idp_managed_mode(method, path):
    with patch("codemie.rest_api.security.idp_lock.config") as config:
        config.IDP_MANAGED_ENABLED = True

        status, body = await _send(method, ROUTES[(method, path)])

    assert status == 403
    assert body["error"]["message"] == _LOCK_MESSAGE


@pytest.mark.asyncio
@pytest.mark.parametrize("method, path", sorted(LOCKED), ids=[f"{m} {p}" for m, p in sorted(LOCKED)])
async def test_locked_route_passes_the_lock_when_the_mode_is_off(method, path):
    with patch("codemie.rest_api.security.idp_lock.config") as config:
        config.IDP_MANAGED_ENABLED = False

        status, body = await _send(method, ROUTES[(method, path)])

    assert not (status == 403 and body.get("error", {}).get("message") == _LOCK_MESSAGE)
