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

import pytest
from unittest.mock import patch
from fastapi import FastAPI, status
from httpx import AsyncClient, ASGITransport

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.project_settings import router
from codemie.rest_api.security.authentication import User

app = FastAPI()
app.include_router(router)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
@patch('codemie.service.settings.settings_index_service.SettingsIndexService.run')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_index_user_settings(mock_authenticate, mock_index_service):
    mock_authenticate.return_value = User(id="user123", username="testuser")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/project", headers={"user-id": "user123"})

    mock_index_service.assert_called_once()
    assert response.status_code == 200


@pytest.mark.anyio
@patch('codemie.service.settings.settings_index_service.SettingsIndexService.get_users')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_project_settings_users(mock_authenticate, mock_get_users):
    """Test that /settings/project/users endpoint returns list of users who created project settings"""
    from codemie.core.models import CreatedByUser
    from codemie.rest_api.models.settings import SettingType

    mock_user = User(id="user123", username="testuser", name="Test User")
    mock_authenticate.return_value = mock_user

    mock_users = [
        CreatedByUser(id="user1", username="user1", name="User One"),
        CreatedByUser(id="user2", username="user2", name="User Two"),
    ]
    mock_get_users.return_value = mock_users

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/project/users", headers={"user-id": "user123"})

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["id"] == "user1"
    assert response.json()[0]["username"] == "user1"
    assert response.json()[0]["name"] == "User One"
    assert response.json()[1]["id"] == "user2"
    mock_get_users.assert_called_once_with(user=mock_user, settings_type=SettingType.PROJECT)


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.validate_litellm_request")
@patch("codemie.enterprise.litellm.require_litellm_enabled")
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_regular_user_cannot_create_project_litellm_when_personal_feature_exists(
    mock_authenticate,
    mock_create_setting,
    mock_require_litellm_enabled,
    mock_validate_litellm_request,
):
    user = User(id="user123", username="testuser", project_names=["test_project"])
    user.is_admin = False
    mock_authenticate.return_value = user
    request_data = {
        "project_name": "test_project",
        "alias": "project-litellm",
        "credential_type": "LiteLLM",
        "credential_values": [{"key": "api_key", "value": "sk-project"}],
    }
    transport = ASGITransport(app=app)

    with pytest.raises(ExtendedHTTPException) as excinfo:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post("/v1/settings/project", headers={"user-id": "user123"}, json=request_data)

    assert excinfo.value.code == status.HTTP_403_FORBIDDEN
    mock_require_litellm_enabled.assert_not_called()
    mock_validate_litellm_request.assert_not_called()
    mock_create_setting.assert_not_called()
