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

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.configs.provisioning_clients_config import ProvisioningPermission
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler, validation_exception_handler
from codemie.rest_api.routers.provisioning import mount_provisioning_api, router
from codemie.rest_api.security import provisioning_access
from codemie.rest_api.security.authentication import ACCESS_DENIED_MESSAGE, authenticate
from codemie.rest_api.security.provisioning_access import RequirePermission, provisioning_clients_only
from codemie.rest_api.security.user import User
from tests.codemie.configs.provisioning_clients_support import CLIENT_USER_ID, make_client, registered_clients

PROJECTS_URL = "/v1/provisioning/projects"
OTHER_URL = "/v1/other"
SERVICE_PATH = "codemie.rest_api.routers.provisioning.projects.provisioning_service"
LOGGER_PATH = "codemie.rest_api.routers.provisioning.logger"


@pytest.fixture(autouse=True)
def api_switched_on(monkeypatch):
    monkeypatch.setattr(provisioning_access, "_provisioning_routes", frozenset())
    monkeypatch.setattr(config, "PROVISIONING_API_ENABLED", True)
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)


@pytest.fixture
def mock_service():
    with patch(SERVICE_PATH) as service:
        service.list_member_projects = AsyncMock(return_value=[])
        yield service


@pytest.fixture
def act_as():
    """Mount the API on a fresh app whose real authenticate() resolves every request to `user`."""
    provider = MagicMock()
    with (
        patch("codemie.rest_api.security.authentication.get_user_provider", return_value=provider),
        patch("codemie.rest_api.security.authentication.IdpFactory"),
        patch("codemie.rest_api.security.teams_authentication_resolver.is_teams_bot_request", return_value=False),
    ):

        def build(user: User) -> TestClient:
            provider.authenticate_and_load_user = AsyncMock(return_value=user)
            return TestClient(_build_app())

        yield build


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.get(OTHER_URL)
    async def other(user: User = Depends(authenticate)) -> dict[str, str]:
        return {"user_id": user.id}

    mount_provisioning_api(app)
    return app


def _client_user() -> User:
    return User(id=CLIENT_USER_ID, email="client@svc.example.com")


def _regular_user() -> User:
    return User(id="6a5b4c3d-2e1f-4a0b-9c8d-7e6f5a4b3c2d", email="user@epam.com")


def _admin_user() -> User:
    return User(id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", email="admin@epam.com", is_admin=True)


def _lookup(client: TestClient, emails: list[str]):
    return client.get(PROJECTS_URL, params=[("member_email", email) for email in emails])


def _assert_access_denied(response) -> None:
    assert response.status_code == 403
    assert response.json()["error"]["message"] == ACCESS_DENIED_MESSAGE


class TestMemberLookup:
    def test_client_gets_sorted_union_for_repeated_member_email(self, act_as, mock_service):
        mock_service.list_member_projects.return_value = [
            SimpleNamespace(name="a-proj", display_name="Project A"),
            SimpleNamespace(name="b-proj", display_name=None),
        ]

        with registered_clients(make_client()):
            response = _lookup(act_as(_client_user()), ["A@epam.com", "b@epam.com"])

        assert response.status_code == 200
        assert response.json() == [
            {"project_name": "a-proj", "display_name": "Project A"},
            {"project_name": "b-proj", "display_name": None},
        ]
        # The route passes addresses on as parsed: case folding belongs to the repository lookup.
        mock_service.list_member_projects.assert_awaited_once_with(["A@epam.com", "b@epam.com"])

    @pytest.mark.parametrize("emails", [[], ["not-an-email"]], ids=["missing", "invalid"])
    def test_missing_or_invalid_member_email_is_422(self, act_as, mock_service, emails):
        with registered_clients(make_client()):
            response = _lookup(act_as(_client_user()), emails)

        assert response.status_code == 422
        mock_service.list_member_projects.assert_not_called()


class TestAccess:
    def test_client_without_read_projects_is_403(self, act_as, mock_service):
        with registered_clients(make_client(permissions=frozenset())):
            response = _lookup(act_as(_client_user()), ["a@epam.com"])

        _assert_access_denied(response)
        mock_service.list_member_projects.assert_not_called()

    @pytest.mark.parametrize("user_factory", [_regular_user, _admin_user], ids=["regular", "admin"])
    def test_non_client_and_admin_are_403(self, act_as, mock_service, user_factory):
        with registered_clients(make_client()):
            response = _lookup(act_as(user_factory()), ["a@epam.com"])

        _assert_access_denied(response)
        mock_service.list_member_projects.assert_not_called()

    def test_client_reaches_lookup_but_not_other_routes(self, act_as, mock_service):
        with registered_clients(make_client()):
            client = act_as(_client_user())
            lookup = _lookup(client, ["a@epam.com"])
            other = client.get(OTHER_URL)

        assert lookup.status_code == 200
        _assert_access_denied(other)


class TestMounting:
    @pytest.mark.parametrize(
        "api_enabled,user_management,warning",
        [(False, True, None), (True, False, "provisioning_api_disabled")],
        ids=["api_off", "user_management_off"],
    )
    def test_api_off_or_user_management_off_gives_404(
        self, act_as, mock_service, monkeypatch, api_enabled, user_management, warning
    ):
        monkeypatch.setattr(config, "PROVISIONING_API_ENABLED", api_enabled)
        monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", user_management)

        with registered_clients(make_client()), patch(LOGGER_PATH) as logger:
            client = act_as(_client_user())
            lookup = _lookup(client, ["a@epam.com"])
            other = client.get(OTHER_URL)

        assert lookup.status_code == 404
        _assert_access_denied(other)
        warnings = [call.args[0] for call in logger.warning.call_args_list]
        assert [message.split(":")[0] for message in warnings] == ([warning] if warning else [])

    def test_empty_registry_logs_warning(self, act_as, mock_service):
        with registered_clients(), patch(LOGGER_PATH) as logger:
            response = _lookup(act_as(_regular_user()), ["a@epam.com"])

        _assert_access_denied(response)
        logger.warning.assert_called_once()
        assert logger.warning.call_args.args[0].startswith("provisioning_api_no_clients:")


class TestRouteContract:
    def test_every_provisioning_route_declares_one_permission_after_guards(self):
        routes = [route for route in router.routes if isinstance(route, APIRoute)]
        assert routes

        for route in routes:
            calls = [dependency.call for dependency in route.dependant.dependencies]
            permissions = [call for call in calls if isinstance(call, RequirePermission)]

            assert len(permissions) == 1, route.path
            assert permissions[0].permission in ProvisioningPermission
            assert calls.index(authenticate) < calls.index(provisioning_clients_only) < calls.index(permissions[0])

    def test_lookup_openapi_is_vendor_free(self):
        with registered_clients(make_client()):
            openapi = _build_app().openapi()

        operation = openapi["paths"][PROJECTS_URL]["get"]
        response_schema = openapi["components"]["schemas"]["ProvisioningProjectResponse"]

        assert operation["tags"] == ["Provisioning"]
        assert "servicenow" not in json.dumps([operation, response_schema]).lower()

    def test_lookup_422_is_documented_in_the_platform_shape(self):
        with registered_clients(make_client()):
            openapi = _build_app().openapi()

        schema = openapi["paths"][PROJECTS_URL]["get"]["responses"]["422"]["content"]["application/json"]["schema"]

        assert schema == {"$ref": "#/components/schemas/ProvisioningValidationErrorResponse"}
