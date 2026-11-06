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

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import APIRouter

from codemie.configs.provisioning_clients_config import ProvisioningPermission
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security import provisioning_access
from codemie.rest_api.security.provisioning_access import (
    RequirePermission,
    enforce_provisioning_scope,
    provisioning_clients_only,
    require_permission,
    set_provisioning_routes,
)
from codemie.rest_api.security.user import User
from tests.codemie.configs.provisioning_clients_support import CLIENT_USER_ID, make_client, registered_clients

PROJECTS_PATH = "/v1/provisioning/projects"
OTHER_USER_ID = "1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9"
SECOND_CLIENT_USER_ID = "8e7d6c5b-4a39-4281-b7c6-d5e4f3a2b1c0"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def no_provisioning_routes(monkeypatch):
    monkeypatch.setattr(provisioning_access, "_provisioning_routes", frozenset())


@pytest.fixture
def provisioning_routes():
    router = APIRouter(prefix="/v1/provisioning")

    @router.get("/projects")
    async def projects() -> list[str]:
        return []

    set_provisioning_routes(router)


def _client_user(user_type="regular", **extra) -> User:
    return User(id=CLIENT_USER_ID, user_type=user_type, project_names=[], **extra)


def _other_user(**extra) -> User:
    return User(id=OTHER_USER_ID, project_names=[], **extra)


def _scoped_request(method, path):
    request = MagicMock()
    request.method = method
    request.scope = {"route": SimpleNamespace(path=path)} if path else {}
    return request


def _guard_request(user: User):
    request = MagicMock()
    request.state.user = user
    return request


class TestEnforceProvisioningScope:
    def test_unregistered_user_passes(self, provisioning_routes):
        with registered_clients(make_client()):
            enforce_provisioning_scope(_other_user(), _scoped_request("GET", "/v1/other"))

    def test_client_passes_on_a_provisioning_route(self, provisioning_routes):
        with registered_clients(make_client()):
            enforce_provisioning_scope(_client_user(), _scoped_request("GET", PROJECTS_PATH))

    @pytest.mark.parametrize(
        "method,path",
        [("GET", "/v1/other"), ("POST", PROJECTS_PATH), ("GET", None)],
        ids=["other_route", "other_method", "no_route_in_scope"],
    )
    def test_client_is_403_off_the_provisioning_routes(self, provisioning_routes, method, path):
        with registered_clients(make_client()), pytest.raises(ExtendedHTTPException) as exc_info:
            enforce_provisioning_scope(_client_user(), _scoped_request(method, path))

        assert exc_info.value.code == 403
        assert "servicenow" not in str(exc_info.value.details).lower()

    def test_second_registered_client_is_403_off_the_provisioning_routes(self, provisioning_routes):
        second = make_client(
            name="budget-tool",
            user_id=SECOND_CLIENT_USER_ID,
            permissions=frozenset({ProvisioningPermission.MANAGE_BUDGETS}),
        )
        second_user = User(id=SECOND_CLIENT_USER_ID, project_names=[])

        with registered_clients(make_client(), second), pytest.raises(ExtendedHTTPException) as exc_info:
            enforce_provisioning_scope(second_user, _scoped_request("GET", "/v1/other"))

        assert exc_info.value.code == 403

    def test_client_is_403_everywhere_when_no_router_is_set(self):
        with registered_clients(make_client()), pytest.raises(ExtendedHTTPException) as exc_info:
            enforce_provisioning_scope(_client_user(), _scoped_request("GET", PROJECTS_PATH))

        assert exc_info.value.code == 403


class TestProvisioningClientsOnly:
    @pytest.mark.anyio
    @pytest.mark.parametrize("user_type", ["regular", "external", "service_account"])
    async def test_client_of_any_type_passes(self, user_type):
        with registered_clients(make_client()):
            assert await provisioning_clients_only(_guard_request(_client_user(user_type))) is None

    @pytest.mark.anyio
    @pytest.mark.parametrize("user", [_other_user(), _other_user(is_admin=True)], ids=["regular", "admin"])
    async def test_non_client_is_403(self, user):
        with registered_clients(make_client()), pytest.raises(ExtendedHTTPException) as exc_info:
            await provisioning_clients_only(_guard_request(user))

        assert exc_info.value.code == 403


class TestRequirePermission:
    def test_exposes_its_permission(self):
        assert (
            RequirePermission(ProvisioningPermission.READ_PROJECTS).permission == ProvisioningPermission.READ_PROJECTS
        )

    @pytest.mark.anyio
    async def test_holder_gets_its_client_back(self):
        client = make_client()

        with registered_clients(client):
            result = await RequirePermission(ProvisioningPermission.READ_PROJECTS)(_guard_request(_client_user()))

        assert result == client

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "client,user",
        [(make_client(permissions=frozenset()), _client_user()), (make_client(), _other_user())],
        ids=["empty_permissions", "unregistered_caller"],
    )
    async def test_caller_without_the_permission_is_403(self, client, user):
        with registered_clients(client), pytest.raises(ExtendedHTTPException) as exc_info:
            await RequirePermission(ProvisioningPermission.READ_PROJECTS)(_guard_request(user))

        assert exc_info.value.code == 403


class TestRequirePermissionFunction:
    def test_holder_gets_its_client_back(self):
        client = make_client()

        assert require_permission(client, ProvisioningPermission.READ_PROJECTS, CLIENT_USER_ID) == client

    @pytest.mark.parametrize(
        "client,actor_user_id,expected_log",
        [
            (
                make_client(),
                CLIENT_USER_ID,
                "access_denied_provisioning_permission: client=support-portal, actor_user_id=" + CLIENT_USER_ID + ","
                " permission=provisioning:manage-projects, domain=provisioning",
            ),
            (
                None,
                OTHER_USER_ID,
                "access_denied_provisioning_permission: client=None, actor_user_id=" + OTHER_USER_ID + ","
                " permission=provisioning:manage-projects, domain=provisioning",
            ),
        ],
        ids=["client_without_the_permission", "unregistered_caller"],
    )
    def test_caller_without_the_permission_is_403_and_logged(self, client, actor_user_id, expected_log):
        with (
            patch.object(provisioning_access, "logger") as mock_logger,
            pytest.raises(ExtendedHTTPException) as exc_info,
        ):
            require_permission(client, ProvisioningPermission.MANAGE_PROJECTS, actor_user_id)

        assert exc_info.value.code == 403
        assert (
            exc_info.value.details
            == "This provisioning client does not hold the permission provisioning:manage-projects."
        )
        mock_logger.warning.assert_called_once_with(expected_log)
