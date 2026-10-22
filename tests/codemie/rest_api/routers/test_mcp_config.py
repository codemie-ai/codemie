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

from unittest.mock import patch

import pytest
from fastapi import Request, status
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import app
from codemie.rest_api.models.mcp_config import MCPConfigResponse, MCPServerConfigData
from codemie.rest_api.routers import mcp_config as mcp_config_router
from codemie.rest_api.security.user import User
from codemie.service.mcp_config_service import MCPConfigService

CLONE_URL = "/v1/mcp-configs/src-1/clone"

CLONE_PAYLOAD = {
    "name": "Clone 1",
    "config": {"url": "https://h.example.com/sse"},
}


@pytest.fixture
def admin() -> User:
    # is_maintainer keeps admin_access_only satisfied regardless of ENV / ENABLE_USER_MANAGEMENT leaked by other tests.
    return User(id="admin-1", username="admin", name="Admin", is_maintainer=True)


@pytest.fixture
def regular_user() -> User:
    user = User(id="user-1", username="user", name="User")
    # Under ENV=local the User validator grants admin at construction; force a non-admin afterwards.
    user.is_admin = False
    user.is_maintainer = False
    return user


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides = {}


def _authenticate_as(user: User) -> None:
    async def _override(request: Request) -> User:
        request.state.user = user
        return user

    app.dependency_overrides[mcp_config_router.authenticate] = _override


def _clone_response(**overrides) -> MCPConfigResponse:
    payload = {
        "id": "new-1",
        "name": "Copy 1 of Server",
        "description": None,
        "server_home_url": None,
        "source_url": None,
        "logo_url": None,
        "categories": [],
        "config": MCPServerConfigData(url="https://h.example.com/sse", headers={"Authorization": "Bearer abc"}),
        "required_env_vars": [],
        "user_id": "admin-1",
        "is_public": True,
        "is_system": False,
        "created_by": None,
        "usage_count": 0,
        "is_active": True,
        "date": None,
        "update_date": None,
    }
    payload.update(overrides)
    return MCPConfigResponse(**payload)


async def _post(url: str = CLONE_URL, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.post(url, headers={"Authorization": "Bearer testtoken"}, **kwargs)


@pytest.mark.asyncio
async def test_non_admin_gets_403_and_service_is_not_called(regular_user):
    _authenticate_as(regular_user)

    with patch.object(MCPConfigService, "clone_from_form") as clone:
        response = await _post(json=CLONE_PAYLOAD)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert {"message", "details", "help"} <= set(response.json()["error"])
    clone.assert_not_called()


@pytest.mark.asyncio
async def test_clone_calls_service_with_id_request_and_user_and_returns_flat_response(admin):
    _authenticate_as(admin)

    with patch.object(MCPConfigService, "clone_from_form", return_value=_clone_response()) as clone:
        response = await _post(json=CLONE_PAYLOAD)

    assert response.status_code == status.HTTP_201_CREATED
    clone.assert_called_once()
    args = clone.call_args.args
    assert args[0] == "src-1"
    assert args[1].name == "Clone 1"
    assert args[2] == admin
    assert "stripped_fields" not in response.json()


@pytest.mark.asyncio
async def test_response_is_a_flat_config_object_without_stripped_fields(admin):
    _authenticate_as(admin)

    with patch.object(MCPConfigService, "clone_from_form", return_value=_clone_response()):
        response = await _post(json=CLONE_PAYLOAD)

    body = response.json()
    assert body["id"] == "new-1" and body["name"] == "Copy 1 of Server"
    assert body["is_system"] is False
    assert "stripped_fields" not in body
    assert isinstance(body["config"], dict) and "data" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,message",
    [
        (404, "MCP configuration not found"),
        (400, "MCP configuration already exists"),
        (409, "auth_config.id already in use"),
        (422, "Invalid auth_config"),
    ],
)
async def test_service_errors_propagate_as_typed_bodies(admin, code, message):
    _authenticate_as(admin)
    error = ExtendedHTTPException(code=code, message=message, details="some details", help="some help")

    with patch.object(MCPConfigService, "clone_from_form", side_effect=error):
        response = await _post(json=CLONE_PAYLOAD)

    assert response.status_code == code
    assert response.json() == {"error": {"message": message, "details": "some details", "help": "some help"}}
