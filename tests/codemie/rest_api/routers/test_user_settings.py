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
from fastapi import FastAPI, status
from httpx import AsyncClient, ASGITransport

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import Settings, CredentialTypes
from codemie.rest_api.routers.user_settings import router
from codemie.rest_api.security.authentication import User

# Create a FastAPI app and include the router
app = FastAPI()
app.include_router(router)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


user_settings = Settings(
    user_id="user123",
    project_name="test_project",
    alias="test_alias",
    credential_type=CredentialTypes.AWS,
    credential_values=[{"key": "test_key", "value": "test_value"}],
)

project_settings = Settings(
    user_id="user123",
    project_name="test_project",
    alias="test_project_alias",
    credential_type=CredentialTypes.JIRA,
    credential_values=[{"key": "test_key", "value": "test_value"}],
)

project_admin_settings = Settings(
    user_id="user123",
    project_name="test_other_project",
    alias="test_other_project_alias",
    credential_type=CredentialTypes.GCP,
    credential_values=[{"key": "test_key", "value": "test_value"}],
)

project_app_admin_settings = Settings(
    user_id="user123",
    project_name="test_app_admin_project",
    alias="test_app_admin_alias",
    credential_type=CredentialTypes.GIT,
    credential_values=[{"key": "test_key", "value": "test_value"}],
)


@pytest.mark.anyio
@patch('codemie.service.settings.settings_index_service.SettingsIndexService.run')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_index_user_settings(mock_authenticate, mock_index_service):
    mock_authenticate.return_value = User(id="user123", username="testuser")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/user", headers={"user-id": "user123"})
    mock_index_service.assert_called_once()

    assert response.status_code == 200


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.get_settings')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_index_available_settings(mock_authenticate, mock_get_settings):
    if config.ENV == "local":
        config.ENV = "dev"
    mock_authenticate.return_value = User(id="user123", username="testuser")
    mock_get_settings.side_effect = [[project_settings], [user_settings]]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/user/available", headers={"user-id": "user123"})

    assert response.status_code == 200
    assert response.json() == [user_settings.model_dump(), project_settings.model_dump()]


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.get_settings')
@patch('codemie.service.settings.settings.SettingsService.get_all_settings')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_index_settings_admin(mock_authenticate, mock_get_all_settings, mock_get_settings):
    if config.ENV == "local":
        config.ENV = "dev"
    mock_authenticate.return_value = User(id="user123", username="testuser", roles=["admin"])
    mock_get_all_settings.return_value = [project_settings, project_admin_settings]
    mock_get_settings.side_effect = [[user_settings]]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/user/available", headers={"user-id": "user123"})

    assert response.status_code == 200
    assert response.json() == [
        user_settings.model_dump(),
        project_settings.model_dump(),
        project_admin_settings.model_dump(),
    ]


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.get_settings')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_index_settings_app_admin(mock_authenticate, mock_get_settings):
    if config.ENV == "local":
        config.ENV = "dev"
    mock_authenticate.return_value = User(
        id="user123", username="testuser", admin_project_names=["test_app_admin_project"]
    )
    mock_get_settings.side_effect = [[project_app_admin_settings], [user_settings]]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/user/available", headers={"user-id": "user123"})

    assert response.status_code == 200
    assert response.json() == [user_settings.model_dump(), project_app_admin_settings.model_dump()]


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.get_settings')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_index_no_user_settings(mock_authenticate, mock_get_settings):
    mock_authenticate.return_value = User(id="user123", username="testuser")
    mock_get_settings.return_value = []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/user/available", headers={"user-id": "user123"})

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
@patch('codemie.service.settings.settings_tester.SettingsTester.test')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_test_setting(mock_authenticate, mock_test_setting):
    mock_authenticate.return_value = User(id="user123", username="testuser")
    mock_test_setting.return_value = (True, "Test Passed")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/settings/test/",
            headers={"user-id": "user123"},
            json={"credential_type": "Jira", "credential_values": [{"key": "test_key", "value": "test_value"}]},
        )

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "Test Passed"}


@pytest.mark.anyio
@patch('codemie.service.settings.settings_tester.SettingsTester.test')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_test_setting_exception(mock_authenticate, mock_test_setting):
    mock_authenticate.return_value = User(id="user123", username="testuser")
    mock_test_setting.side_effect = Exception("Test Failed")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        with pytest.raises(ExtendedHTTPException):
            await ac.post(
                "/v1/settings/test/",
                headers={"user-id": "user123"},
                json={"credential_type": "Jira", "credential_values": [{"key": "test_key", "value": "test_value"}]},
            )


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch('codemie.rest_api.routers.user_settings.project_access_check')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_user_setting_project_access_denied(
    mock_authenticate,
    mock_project_access_check,
    mock_create_setting,
):
    user = User(id="user123", username="testuser", project_names=["other_project"])
    mock_authenticate.return_value = user
    mock_project_access_check.side_effect = ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details="You do not have permission to access the project 'forbidden_project'.",
    )
    mock_create_setting.return_value = None

    request_data = {
        "project_name": "forbidden_project",
        "alias": "test_alias",
        "credential_type": "AWS",
        "credential_values": [{"key": "test_key", "value": "test_value"}],
    }
    transport = ASGITransport(app=app)

    with pytest.raises(ExtendedHTTPException) as excinfo:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post("/v1/settings/user", headers={"user-id": "user123"}, json=request_data)

    assert excinfo.value.code == status.HTTP_403_FORBIDDEN
    assert "Access denied" in excinfo.value.message
    mock_project_access_check.assert_called_once_with(user, "forbidden_project")
    mock_create_setting.assert_not_called()


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.rest_api.routers.user_settings.project_access_check')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_user_setting_project_access_denied(
    mock_authenticate, mock_project_access_check, mock_update_setting, mock_get_setting_ability
):
    user = User(id="user123", username="testuser", project_names=["other_project"])
    mock_authenticate.return_value = user
    mock_project_access_check.side_effect = ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details="You do not have permission to access the project 'forbidden_project'.",
    )
    mock_get_setting_ability.return_value = True
    mock_update_setting.return_value = None

    request_data = {
        "project_name": "forbidden_project",
        "alias": "test_alias",
        "credential_type": "AWS",
        "credential_values": [{"key": "test_key", "value": "test_value"}],
    }
    transport = ASGITransport(app=app)

    with pytest.raises(ExtendedHTTPException) as excinfo:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.put("/v1/settings/user/setting_123", headers={"user-id": "user123"}, json=request_data)

    assert excinfo.value.code == status.HTTP_403_FORBIDDEN
    assert "Access denied" in excinfo.value.message
    mock_project_access_check.assert_called_once_with(user, "forbidden_project")
    mock_update_setting.assert_not_called()
