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

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import Request, status
from httpx import AsyncClient, ASGITransport

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import app
from codemie.rest_api.models.mcp_config import MCPConfigTestResponse, MCPTestStatus
from codemie.rest_api.routers import mcp_config as mcp_config_router
from codemie.rest_api.security.authentication import admin_access_only
from codemie.rest_api.security.user import User

SERVICE_TEST = "codemie.rest_api.routers.mcp_config.MCPConfigService.test"


def _set_user(user: User):
    def _authenticate(request: Request):
        request.state.user = user
        return user

    async def _admin_access_only_override(request: Request):
        if not request.state.user.is_admin_or_maintainer:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Access denied",
                details="This action requires administrator or maintainer privileges.",
            )

    app.dependency_overrides[mcp_config_router.authenticate] = _authenticate
    app.dependency_overrides[admin_access_only] = _admin_access_only_override


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides = {}


def _response() -> MCPConfigTestResponse:
    return MCPConfigTestResponse(status=MCPTestStatus.SUCCESS, message="ok", tools_count=3)


async def _post(config_id: str = "cfg-1", json=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        return await ac.post(f"/v1/mcp-configs/{config_id}/test", headers={"Authorization": "Bearer t"}, json=json)


@pytest.mark.asyncio
async def test_admin_test_returns_test_result():
    admin = User(id="a1", username="admin", name="Admin", roles=["admin"], is_admin=True)
    _set_user(admin)
    with patch(SERVICE_TEST, return_value=_response()) as mock_test:
        response = await _post()

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["status"] == "success"
    assert body["message"] == "ok"
    assert body["tools_count"] == 3
    assert "last_test_at" not in body
    mock_test.assert_called_once_with("cfg-1", admin, None)


@pytest.mark.asyncio
async def test_non_admin_gets_403():
    # Plain object: User.resolve_is_admin forces is_admin=True when ENV=local
    _set_user(SimpleNamespace(id="u1", is_admin=False, is_maintainer=False, is_admin_or_maintainer=False))
    with patch(SERVICE_TEST) as mock_test:
        response = await _post()

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_test.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_id_gets_404():
    _set_user(User(id="a1", username="admin", name="Admin", roles=["admin"], is_admin=True))
    with patch(SERVICE_TEST, side_effect=ExtendedHTTPException(code=404, message="MCP configuration not found")):
        response = await _post("missing")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_settings_id_body_is_forwarded():
    admin = User(id="a1", username="admin", name="Admin", roles=["admin"], is_admin=True)
    _set_user(admin)
    with patch(SERVICE_TEST, return_value=_response()) as mock_test:
        response = await _post(json={"settings_id": "set-1"})

    assert response.status_code == status.HTTP_200_OK
    mock_test.assert_called_once_with("cfg-1", admin, "set-1")


@pytest.mark.asyncio
async def test_invalid_body_gets_422():
    _set_user(User(id="a1", username="admin", name="Admin", roles=["admin"], is_admin=True))
    with patch(SERVICE_TEST) as mock_test:
        response = await _post(json={"settings_id": ["x"]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_test.assert_not_called()
