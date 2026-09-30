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
from unittest.mock import MagicMock, patch
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


@pytest.fixture(autouse=True)
def _teams_bot_integration_enabled():
    """test_update_ms_teams_project_setting_rejects_project_mismatch runs the real
    validate_ms_teams_request, which gates on the shared customer-config.yaml flag first.
    Decouple that from the shared config value so an unrelated change to that file
    (see CR-002) can't make it fail with a 403 instead of the mismatch it tests."""
    from codemie.configs.customer_config import CustomerConfig

    original = CustomerConfig.is_feature_enabled

    def _is_feature_enabled(self, feature_name, *args, **kwargs):
        if feature_name == "teamsBotIntegration":
            return True
        return original(self, feature_name, *args, **kwargs)

    with patch.object(CustomerConfig, "is_feature_enabled", _is_feature_enabled):
        yield


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


def _admin_user():
    user = User(id="admin1", username="admin", project_names=["test_project"])
    user.is_admin = True
    return user


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.logger")
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_project_setting_logs_on_success(mock_authenticate, mock_create_setting, mock_logger):
    """create_project_setting emits a logger.info containing 'project_setting_created'."""
    mock_authenticate.return_value = _admin_user()
    request_data = {
        "project_name": "test_project",
        "alias": "my-git",
        "credential_type": "Git",
        "credential_values": [{"key": "token", "value": "super-secret-token"}],
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/settings/project", headers={"user-id": "admin1"}, json=request_data)

    assert response.status_code == 200
    mock_logger.info.assert_called_once()
    message = mock_logger.info.call_args[0][0]
    assert "project_setting_created" in message
    assert "my-git" in message
    assert "super-secret-token" not in message


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.logger")
@patch("codemie.rest_api.routers.project_settings.Ability")
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_project_setting_logs_on_success(
    mock_authenticate, mock_get_ability, mock_update_settings, mock_ability, mock_logger
):
    """update_project_setting emits a logger.info containing 'project_setting_updated'."""
    mock_authenticate.return_value = _admin_user()
    mock_ability.return_value.can.return_value = True
    request_data = {
        "project_name": "test_project",
        "alias": "my-git",
        "credential_type": "Git",
        "credential_values": [{"key": "token", "value": "super-secret-token"}],
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put("/v1/settings/project/set-1", headers={"user-id": "admin1"}, json=request_data)

    assert response.status_code == 200
    mock_logger.info.assert_called_once()
    message = mock_logger.info.call_args[0][0]
    assert "project_setting_updated" in message
    assert "set-1" in message
    assert "super-secret-token" not in message


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.logger")
@patch("codemie.rest_api.routers.project_settings.Settings")
@patch("codemie.service.aws_bedrock.bedrock_orchestration_service.BedrockOrchestratorService.delete_all_entities")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_delete_project_setting_logs_on_success(
    mock_authenticate, mock_delete_entities, mock_settings, mock_logger
):
    """delete_project_setting emits a logger.info containing 'project_setting_deleted'."""
    mock_authenticate.return_value = _admin_user()
    setting = mock_settings.get_by_id.return_value
    setting.project_name = "test_project"
    setting.alias = "my-git"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.delete("/v1/settings/project/set-1", headers={"user-id": "admin1"})

    assert response.status_code == 200
    mock_logger.info.assert_called_once()
    message = mock_logger.info.call_args[0][0]
    assert "project_setting_deleted" in message
    assert "set-1" in message


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_ms_teams_project_setting_succeeds(mock_authenticate, mock_create_setting):
    user = User(id="user123", username="testuser", project_names=["proj1"])
    user.is_admin = True
    mock_authenticate.return_value = user

    payload = {
        "project_name": "proj1",
        "alias": "teams-integration",
        "credential_type": "MSTeams",
        "credential_values": [{"key": "assistant_ids", "value": ["a1"]}],
    }

    with patch("codemie.rest_api.routers.project_settings.validate_ms_teams_request") as mock_validate:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/settings/project", json=payload, headers={"user-id": "user123"})

    assert response.status_code == 200
    mock_validate.assert_called_once()
    mock_create_setting.assert_called_once()


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_ms_teams_project_setting_rejects_invalid_request(mock_authenticate, mock_create_setting):
    user = User(id="user123", username="testuser", project_names=["proj1"])
    user.is_admin = True
    mock_authenticate.return_value = user

    payload = {
        "project_name": "proj1",
        "alias": "teams-integration",
        "credential_type": "MSTeams",
        "credential_values": [{"key": "assistant_ids", "value": ["bad-id"]}],
    }

    with patch(
        "codemie.rest_api.routers.project_settings.validate_ms_teams_request",
        side_effect=ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST, message="Invalid assistant_ids", details="bad-id", help="fix it"
        ),
    ):
        transport = ASGITransport(app=app)
        with pytest.raises(ExtendedHTTPException) as excinfo:
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                await ac.post("/v1/settings/project", json=payload, headers={"user-id": "user123"})

    assert excinfo.value.code == status.HTTP_400_BAD_REQUEST
    mock_create_setting.assert_not_called()


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_ms_teams_project_setting_checks_permission_before_validation(
    mock_authenticate, mock_create_setting
):
    """An unauthorized caller must be rejected before validate_ms_teams_request runs, so
    ms_teams existence for a project the caller cannot access is never leaked."""
    user = User(id="user123", username="testuser", project_names=[])
    user.is_admin = False
    mock_authenticate.return_value = user

    payload = {
        "project_name": "other-proj",
        "alias": "teams-integration",
        "credential_type": "MSTeams",
        "credential_values": [{"key": "assistant_ids", "value": ["a1"]}],
    }

    with patch("codemie.rest_api.routers.project_settings.validate_ms_teams_request") as mock_validate:
        transport = ASGITransport(app=app)
        with pytest.raises(ExtendedHTTPException) as excinfo:
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                await ac.post("/v1/settings/project", json=payload, headers={"user-id": "user123"})

    assert excinfo.value.code == status.HTTP_403_FORBIDDEN
    mock_validate.assert_not_called()
    mock_create_setting.assert_not_called()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.Settings")
@patch("codemie.rest_api.routers.project_settings.Ability")
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_ms_teams_project_setting_revalidates(
    mock_authenticate, mock_get_setting_ability, mock_update_settings, mock_ability, mock_settings
):
    from codemie.rest_api.models.settings import SettingType

    user = User(id="user123", username="testuser", project_names=["proj1"])
    user.is_admin = True
    mock_authenticate.return_value = user
    mock_ability.return_value.can.return_value = True
    mock_get_setting_ability.return_value = MagicMock()
    mock_settings.get_by_id.return_value = MagicMock(project_name="proj1")

    payload = {
        "project_name": "proj1",
        "alias": "teams-integration",
        "credential_type": "MSTeams",
        "credential_values": [{"key": "assistant_ids", "value": ["a1", "a2"]}],
    }

    with patch("codemie.rest_api.routers.project_settings.validate_ms_teams_request") as mock_validate:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put("/v1/settings/project/setting-1", json=payload, headers={"user-id": "user123"})

    assert response.status_code == 200
    mock_validate.assert_called_once_with(
        mock_validate.call_args[0][0], setting_type=SettingType.PROJECT, setting_id="setting-1"
    )
    mock_update_settings.assert_called_once()


@pytest.mark.anyio
@patch("codemie.service.settings.settings_request_validator.Settings")
@patch("codemie.rest_api.routers.project_settings.Ability")
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_ms_teams_project_setting_rejects_project_mismatch(
    mock_authenticate, mock_get_setting_ability, mock_update_settings, mock_ability, mock_settings
):
    """The setting being updated actually belongs to 'proj1'; a caller may not smuggle
    a different project_name into the PUT body to have it validated/applied instead.

    validate_ms_teams_request (not the router) owns this check, so it must run for real
    here rather than being mocked out."""
    user = User(id="user123", username="testuser", project_names=["proj1"])
    user.is_admin = True
    mock_authenticate.return_value = user
    mock_ability.return_value.can.return_value = True
    mock_get_setting_ability.return_value = MagicMock()
    mock_settings.get_by_id.return_value = MagicMock(project_name="proj1")

    payload = {
        "project_name": "other-proj",
        "alias": "teams-integration",
        "credential_type": "MSTeams",
        "credential_values": [{"key": "assistant_ids", "value": ["a1"]}],
    }

    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as excinfo:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.put("/v1/settings/project/setting-1", json=payload, headers={"user-id": "user123"})

    assert excinfo.value.code == status.HTTP_400_BAD_REQUEST
    assert excinfo.value.message == "Project mismatch"
    mock_update_settings.assert_not_called()


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_ms_teams_integrity_error_maps_to_409(mock_authenticate, mock_create_setting):
    """The ms_teams-specific 409 must only fire for MS_TEAMS credential_type."""
    from sqlalchemy.exc import IntegrityError

    user = User(id="user123", username="testuser", project_names=["proj1"])
    user.is_admin = True
    mock_authenticate.return_value = user
    mock_create_setting.side_effect = IntegrityError("stmt", {}, Exception("dup"))

    payload = {
        "project_name": "proj1",
        "alias": "teams-integration",
        "credential_type": "MSTeams",
        "credential_values": [{"key": "assistant_ids", "value": ["a1"]}],
    }

    with patch("codemie.rest_api.routers.project_settings.validate_ms_teams_request"):
        transport = ASGITransport(app=app)
        with pytest.raises(ExtendedHTTPException) as excinfo:
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                await ac.post("/v1/settings/project", json=payload, headers={"user-id": "user123"})

    assert excinfo.value.code == status.HTTP_409_CONFLICT
    assert "ms_teams" in excinfo.value.message


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.logger")
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_git_integrity_error_maps_to_generic_422(mock_authenticate, mock_create_setting, mock_logger):
    """A non-ms_teams IntegrityError must not be reported as an ms_teams conflict."""
    from sqlalchemy.exc import IntegrityError

    mock_authenticate.return_value = _admin_user()
    mock_create_setting.side_effect = IntegrityError("stmt", {}, Exception("dup alias"))

    payload = {
        "project_name": "test_project",
        "alias": "my-git",
        "credential_type": "Git",
        "credential_values": [{"key": "token", "value": "super-secret-token"}],
    }

    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as excinfo:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post("/v1/settings/project", headers={"user-id": "admin1"}, json=payload)

    assert excinfo.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "ms_teams" not in excinfo.value.message


@pytest.mark.anyio
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_project_setting_rejects_unknown_credential_type(mock_authenticate):
    """An unrecognized credential_type must fail request validation (422), not reach the service."""
    mock_authenticate.return_value = _admin_user()

    request_data = {
        "project_name": "test_project",
        "alias": "bogus-alias",
        "credential_type": "NotARealCredentialType",
        "credential_values": [{"key": "api_key", "value": "x"}],
    }
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/settings/project", headers={"user-id": "admin1"}, json=request_data)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
