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

"""
Unit tests for user_settings CRUD endpoints.

Covers the following endpoints from user_settings.py:
- GET /v1/settings/user - paginated list with filters
- GET /v1/settings/user/available - all available settings
- POST /v1/settings/user - create setting
- PUT /v1/settings/user/{setting_id} - update setting
- DELETE /v1/settings/user/{setting_id} - delete setting
- POST /v1/settings/test/ - test credentials

Target >= 80% coverage for user_settings.py router.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialTypes, Settings, SettingType
from codemie.rest_api.routers.user_settings import router
from codemie.rest_api.security.authentication import User


# Create a FastAPI app and include the router
app = FastAPI()
app.include_router(router)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def mock_user():
    """Standard non-admin user for testing."""
    return User(
        id="user123",
        username="testuser",
        email="test@example.com",
        project_names=["test_project"],
    )


@pytest.fixture
def mock_admin_user():
    """Admin user for testing admin scenarios."""
    return User(
        id="admin123",
        username="adminuser",
        email="admin@example.com",
        roles=["admin"],
    )


@pytest.fixture
def mock_app_admin_user():
    """Application admin user (project admin) for testing."""
    return User(
        id="appadmin123",
        username="appadminuser",
        email="appadmin@example.com",
        admin_project_names=["admin_project"],
    )


@pytest.fixture
def sample_user_setting():
    """Sample user setting for testing."""
    return Settings(
        id="setting123",
        user_id="user123",
        project_name="test_project",
        alias="test_user_alias",
        credential_type=CredentialTypes.AWS,
        credential_values=[{"key": "aws_access_key", "value": "test_key"}],
    )


@pytest.fixture
def sample_project_setting():
    """Sample project setting for testing."""
    return Settings(
        id="project_setting123",
        user_id="user123",
        project_name="test_project",
        alias="test_project_alias",
        credential_type=CredentialTypes.JIRA,
        credential_values=[{"key": "jira_url", "value": "https://jira.example.com"}],
    )


# ----------------------------
# Test: GET /v1/settings/user (index_user_settings)
# ----------------------------


@pytest.mark.anyio
@patch('codemie.service.settings.settings_index_service.SettingsIndexService.run')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_index_user_settings_paginated_list(mock_authenticate, mock_index_service, mock_user):
    """Test index_user_settings returns paginated list with filters."""
    # Arrange
    mock_authenticate.return_value = mock_user
    expected_result = {
        "items": [{"id": "setting123", "alias": "test_alias"}],
        "total": 1,
        "page": 0,
        "per_page": 10,
    }
    mock_index_service.return_value = expected_result

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/settings/user",
            headers={"user-id": "user123"},
            params={"page": 0, "per_page": 10, "filters": '{"credential_type": "AWS"}'},
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == expected_result
    mock_index_service.assert_called_once_with(
        settings_type=SettingType.USER,
        user=mock_user,
        page=0,
        per_page=10,
        filters={"credential_type": "AWS"},
    )


@pytest.mark.anyio
@patch('codemie.service.settings.settings_index_service.SettingsIndexService.run')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_index_user_settings_no_filters(mock_authenticate, mock_index_service, mock_user):
    """Test index_user_settings works without filters parameter."""
    # Arrange
    mock_authenticate.return_value = mock_user
    expected_result = {"items": [], "total": 0, "page": 0, "per_page": 10}
    mock_index_service.return_value = expected_result

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/settings/user",
            headers={"user-id": "user123"},
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    mock_index_service.assert_called_once_with(
        settings_type=SettingType.USER,
        user=mock_user,
        page=0,
        per_page=10,
        filters={},
    )


@pytest.mark.anyio
@patch('codemie.service.settings.settings_index_service.SettingsIndexService.run')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_index_user_settings_custom_pagination(mock_authenticate, mock_index_service, mock_user):
    """Test index_user_settings respects custom page and per_page parameters."""
    # Arrange
    mock_authenticate.return_value = mock_user
    expected_result = {"items": [], "total": 0, "page": 2, "per_page": 25}
    mock_index_service.return_value = expected_result

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/settings/user",
            headers={"user-id": "user123"},
            params={"page": 2, "per_page": 25},
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    mock_index_service.assert_called_once_with(
        settings_type=SettingType.USER,
        user=mock_user,
        page=2,
        per_page=25,
        filters={},
    )


# ----------------------------
# Test: GET /v1/settings/user/available (index_settings)
# ----------------------------


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.get_settings')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_available_settings_regular_user(
    mock_authenticate, mock_get_settings, mock_user, sample_user_setting, sample_project_setting
):
    """Test index_settings calls get_settings for both user and project settings for regular user."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_get_settings.return_value = [sample_user_setting, sample_project_setting]

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/user/available", headers={"user-id": "user123"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    # Verify get_settings was called for project settings with user's project_names
    assert mock_get_settings.call_count >= 1


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.get_all_settings')
@patch('codemie.service.settings.settings.SettingsService.get_settings')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_available_settings_admin_user(
    mock_authenticate, mock_get_settings, mock_get_all_settings, mock_admin_user, sample_user_setting
):
    """Test index_settings returns all project settings for admin."""
    # Arrange
    mock_authenticate.return_value = mock_admin_user
    admin_project_setting = Settings(
        id="admin_proj_setting",
        user_id="other_user",
        project_name="other_project",
        alias="admin_sees_all",
        credential_type=CredentialTypes.GCP,
        credential_values=[{"key": "gcp_key", "value": "test"}],
    )
    mock_get_settings.return_value = [sample_user_setting]
    mock_get_all_settings.return_value = [admin_project_setting]

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/user/available", headers={"user-id": "admin123"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    result = response.json()
    assert len(result) == 2
    assert result[0]["alias"] == "test_user_alias"
    assert result[1]["alias"] == "admin_sees_all"
    mock_get_all_settings.assert_called_once_with(settings_type=SettingType.PROJECT)


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.get_settings')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_available_settings_app_admin_user(
    mock_authenticate, mock_get_settings, mock_app_admin_user, sample_user_setting
):
    """Test index_settings calls get_settings for application admin's projects."""
    # Arrange
    mock_authenticate.return_value = mock_app_admin_user
    mock_get_settings.return_value = [sample_user_setting]

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/user/available", headers={"user-id": "appadmin123"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    # Verify get_settings was called at least once
    assert mock_get_settings.call_count >= 1


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.get_settings')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_available_settings_empty_list(mock_authenticate, mock_get_settings, mock_user):
    """Test index_settings returns empty list when no settings exist."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_get_settings.return_value = []

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/settings/user/available", headers={"user-id": "user123"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


# ----------------------------
# Test: POST /v1/settings/user (create_user_setting)
# ----------------------------


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_user_setting_success(mock_authenticate, mock_create_setting, mock_user):
    """Test create_user_setting successfully creates setting."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_create_setting.return_value = None

    request_data = {
        "alias": "new_aws_creds",
        "credential_type": "AWS",
        "credential_values": [{"key": "aws_access_key", "value": "AKIA123"}],
    }

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/settings/user", headers={"user-id": "user123"}, json=request_data)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": "Specified credentials saved"}
    mock_create_setting.assert_called_once()
    call_kwargs = mock_create_setting.call_args[1]
    assert call_kwargs["user_id"] == "user123"
    assert call_kwargs["user"] == mock_user


@pytest.mark.anyio
@patch('codemie.rest_api.routers.user_settings.project_access_check')
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_user_setting_with_project_access_check(
    mock_authenticate, mock_create_setting, mock_project_access_check, mock_user
):
    """Test create_user_setting checks project access when project_name is provided."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_create_setting.return_value = None

    request_data = {
        "project_name": "test_project",
        "alias": "project_scoped_creds",
        "credential_type": "AWS",
        "credential_values": [{"key": "aws_key", "value": "test"}],
    }

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/settings/user", headers={"user-id": "user123"}, json=request_data)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    mock_project_access_check.assert_called_once_with(mock_user, "test_project")


@pytest.mark.anyio
@patch('codemie.rest_api.routers.user_settings.project_access_check')
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_user_setting_project_access_denied(
    mock_authenticate, mock_create_setting, mock_project_access_check, mock_user
):
    """Test create_user_setting raises 403 when project access denied."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_project_access_check.side_effect = ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details="You do not have permission to access the project 'forbidden_project'.",
    )

    request_data = {
        "project_name": "forbidden_project",
        "alias": "forbidden_creds",
        "credential_type": "AWS",
        "credential_values": [{"key": "key", "value": "value"}],
    }

    # Act & Assert
    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post("/v1/settings/user", headers={"user-id": "user123"}, json=request_data)

    assert exc_info.value.code == status.HTTP_403_FORBIDDEN
    assert "Access denied" in exc_info.value.message
    mock_create_setting.assert_not_called()


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_user_setting_database_error(mock_authenticate, mock_create_setting, mock_user):
    """Test create_user_setting returns 422 on database errors."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_create_setting.side_effect = Exception("Database constraint violation")

    request_data = {
        "alias": "duplicate_alias",
        "credential_type": "AWS",
        "credential_values": [{"key": "key", "value": "value"}],
    }

    # Act & Assert
    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post("/v1/settings/user", headers={"user-id": "user123"}, json=request_data)

    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Cannot create specified setting" in exc_info.value.message


# ----------------------------
# Test: PUT /v1/settings/user/{setting_id} (update_user_setting)
# ----------------------------


@pytest.mark.anyio
@patch('codemie.core.ability.Ability.can')
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_user_setting_success(
    mock_authenticate, mock_get_setting_ability, mock_update_settings, mock_can, mock_user
):
    """Test update_user_setting successfully updates setting."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_ability = MagicMock()
    mock_ability.user_id = "user123"
    mock_get_setting_ability.return_value = mock_ability
    mock_can.return_value = True  # User has write permission
    mock_update_settings.return_value = None

    request_data = {
        "alias": "updated_alias",
        "credential_type": "AWS",
        "credential_values": [{"key": "aws_key", "value": "updated_value"}],
    }

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put("/v1/settings/user/setting123", headers={"user-id": "user123"}, json=request_data)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": "Specified credentials updated"}
    mock_get_setting_ability.assert_called_once_with(credential_id="setting123", settings_type=SettingType.USER)
    mock_update_settings.assert_called_once()


@pytest.mark.anyio
@patch('codemie.core.ability.Ability.can')
@patch('codemie.rest_api.routers.user_settings.project_access_check')
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_user_setting_with_project_access_check(
    mock_authenticate,
    mock_get_setting_ability,
    mock_update_settings,
    mock_project_access_check,
    mock_can,
    mock_user,
):
    """Test update_user_setting checks project access when project_name is provided."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_ability = MagicMock()
    mock_ability.user_id = "user123"
    mock_get_setting_ability.return_value = mock_ability
    mock_can.return_value = True

    request_data = {
        "project_name": "test_project",
        "alias": "updated_alias",
        "credential_type": "AWS",
        "credential_values": [{"key": "key", "value": "value"}],
    }

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put("/v1/settings/user/setting123", headers={"user-id": "user123"}, json=request_data)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    mock_project_access_check.assert_called_once_with(mock_user, "test_project")


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_user_setting_database_error(
    mock_authenticate, mock_get_setting_ability, mock_update_settings, mock_user
):
    """Test update_user_setting returns 422 on database errors."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_ability = MagicMock()
    mock_ability.user_id = "user123"
    mock_get_setting_ability.return_value = mock_ability
    mock_update_settings.side_effect = Exception("Database error")

    request_data = {
        "alias": "updated_alias",
        "credential_type": "AWS",
        "credential_values": [{"key": "key", "value": "value"}],
    }

    # Act & Assert
    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.put("/v1/settings/user/setting123", headers={"user-id": "user123"}, json=request_data)

    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Cannot update specified setting" in exc_info.value.message


# ----------------------------
# Test: DELETE /v1/settings/user/{setting_id} (delete_user_setting)
# ----------------------------


@pytest.mark.anyio
@patch('codemie.core.ability.Ability.can')
@patch('codemie.rest_api.models.settings.Settings.delete_setting')
@patch('codemie.service.aws_bedrock.bedrock_orchestration_service.BedrockOrchestratorService.delete_all_entities')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_delete_user_setting_success(
    mock_authenticate, mock_get_setting_ability, mock_delete_bedrock, mock_delete_setting, mock_can, mock_user
):
    """Test delete_user_setting successfully removes setting."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_ability = MagicMock()
    mock_ability.user_id = "user123"
    mock_get_setting_ability.return_value = mock_ability
    mock_can.return_value = True  # User has delete permission

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.delete("/v1/settings/user/setting123", headers={"user-id": "user123"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": "Specified credential removed"}
    mock_delete_bedrock.assert_called_once_with("setting123")
    mock_delete_setting.assert_called_once_with("setting123")


@pytest.mark.anyio
@patch('codemie.core.ability.Ability.can')
@patch('codemie.rest_api.models.settings.Settings.delete_setting')
@patch('codemie.service.aws_bedrock.bedrock_orchestration_service.BedrockOrchestratorService.delete_all_entities')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_delete_user_setting_not_found(
    mock_authenticate, mock_get_setting_ability, mock_delete_bedrock, mock_delete_setting, mock_can, mock_user
):
    """Test delete_user_setting raises 404 when setting not found."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_ability = MagicMock()
    mock_ability.user_id = "user123"
    mock_get_setting_ability.return_value = mock_ability
    mock_can.return_value = True
    mock_delete_setting.side_effect = KeyError("Setting not found")

    # Act & Assert
    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.delete("/v1/settings/user/setting123", headers={"user-id": "user123"})

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND
    assert "Credential not found" in exc_info.value.message


# ----------------------------
# Test: POST /v1/settings/test/ (test_setting)
# ----------------------------


@pytest.mark.anyio
@patch('codemie.rest_api.routers.user_settings.SettingsTester')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_test_credentials_success(mock_authenticate, mock_tester_class, mock_user):
    """Test test_setting successfully validates credentials."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_tester = MagicMock()
    mock_tester.test.return_value = (True, "Connection successful")
    mock_tester_class.return_value = mock_tester

    request_data = {
        "credential_type": "AWS",
        "credential_values": [{"key": "aws_access_key", "value": "AKIA123"}],
    }

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/settings/test/", headers={"user-id": "user123"}, json=request_data)

    # Assert
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"success": True, "message": "Connection successful"}


@pytest.mark.anyio
@patch('codemie.rest_api.routers.user_settings.SettingsTester')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_test_credentials_failure(mock_authenticate, mock_tester_class, mock_user):
    """Test test_setting returns 400 when credentials are invalid."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_tester = MagicMock()
    mock_tester.test.return_value = (False, "Invalid credentials")
    mock_tester_class.return_value = mock_tester

    request_data = {
        "credential_type": "AWS",
        "credential_values": [{"key": "aws_access_key", "value": "INVALID"}],
    }

    # Act & Assert
    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post("/v1/settings/test/", headers={"user-id": "user123"}, json=request_data)

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.message == "Integration test failed"
    assert exc_info.value.details == "Invalid credentials"


@pytest.mark.anyio
@patch('codemie.service.settings.settings_tester.SettingsTester')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_test_credentials_exception(mock_authenticate, mock_tester_class, mock_user):
    """Test test_setting returns 422 when tester raises exception."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_tester_class.side_effect = Exception("Connection timeout")

    request_data = {
        "credential_type": "AWS",
        "credential_values": [{"key": "aws_access_key", "value": "AKIA123"}],
    }

    # Act & Assert
    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post("/v1/settings/test/", headers={"user-id": "user123"}, json=request_data)

    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Cannot test specified setting" in exc_info.value.message
