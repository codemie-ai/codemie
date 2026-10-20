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
Tests for EPMCDME-12586: Allow changing project for existing integration settings.

Validates that users can change the project field when editing an existing integration,
with proper permission checks on the destination project.
"""

import pytest
from unittest.mock import Mock, patch
from fastapi import FastAPI, status
from httpx import AsyncClient, ASGITransport

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.models.settings_transfer import TransferMode
from codemie.rest_api.routers.project_settings import router
from codemie.rest_api.security.authentication import User

app = FastAPI()
app.include_router(router)
app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def _admin_user():
    """Helper to create an admin user."""
    user = User(id="admin1", username="admin", project_names=["project-a", "project-b"])
    user.is_admin = True
    return user


def _regular_user():
    """Helper to create a regular user with project-a admin permission."""
    user = User(id="user1", username="regular", project_names=["project-a"])
    user.is_admin = False
    return user


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.SettingsTransferService")
@patch("codemie.rest_api.routers.project_settings.Settings")
@patch("codemie.rest_api.routers.project_settings.Ability")
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.routers.project_settings._check_permission")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_project_setting_validates_new_project_permission(
    mock_authenticate,
    mock_check_perm,
    mock_get_ability,
    mock_update_settings,
    mock_ability,
    mock_settings,
    mock_transfer_service,
):
    """
    When updating a setting with a different project_name, _check_permission
    should be called to validate user has admin access to the new project, and the
    move itself should go through SettingsTransferService.transfer_setting (not an
    ad-hoc project_name mutation) — and only *after* update_settings has applied the
    field updates, so a failure there never leaves the setting moved without them.
    """
    mock_authenticate.return_value = _admin_user()
    mock_ability.return_value.can.return_value = True

    # Mock existing setting with project-a
    existing_setting = Mock()
    existing_setting.project_name = "project-a"
    mock_settings.get_by_id.return_value = existing_setting

    call_order = []
    mock_update_settings.side_effect = lambda *a, **kw: call_order.append("update_settings")
    mock_transfer_service.transfer_setting.side_effect = lambda *a, **kw: call_order.append("transfer_setting")

    request_data = {
        "project_name": "project-b",  # Different project
        "alias": "my-git",
        "credential_type": "Git",
        "credential_values": [{"key": "token", "value": "secret"}],
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put("/v1/settings/project/set-1", headers={"user-id": "admin1"}, json=request_data)

    assert response.status_code == 200
    # Verify _check_permission was called with the NEW project name
    mock_check_perm.assert_called_once_with(mock_authenticate.return_value, "project-b")
    # Verify the move itself was delegated to the transfer service, not mutated inline
    mock_transfer_service.transfer_setting.assert_called_once_with("set-1", "project-b", mode=TransferMode.MOVE)
    # Verify field updates are applied before the move happens
    assert call_order == ["update_settings", "transfer_setting"]


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.SettingsTransferService")
@patch("codemie.rest_api.routers.project_settings.Settings")
@patch("codemie.rest_api.routers.project_settings.Ability")
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.routers.project_settings._check_permission")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_project_setting_does_not_move_when_update_settings_fails(
    mock_authenticate,
    mock_check_perm,
    mock_get_ability,
    mock_update_settings,
    mock_ability,
    mock_settings,
    mock_transfer_service,
):
    """
    Field updates (alias, credentials, ...) are applied before the move, not after: if
    update_settings fails for any reason (e.g. the new alias already exists in the target
    project, caught by update_settings' own check_alias_unique call), transfer_setting must
    never run. Otherwise the setting would end up moved to the new project without the
    field updates that were supposed to go with it, and a retry against the original
    project would 404.
    """
    mock_authenticate.return_value = _admin_user()
    mock_ability.return_value.can.return_value = True

    # Mock existing setting with project-a
    existing_setting = Mock()
    existing_setting.project_name = "project-a"
    mock_settings.get_by_id.return_value = existing_setting

    # update_settings fails, e.g. because the new alias already exists in the target project
    mock_update_settings.side_effect = ValueError("There are more than one settings with the alias named renamed-git")

    request_data = {
        "project_name": "project-b",  # Different project...
        "alias": "renamed-git",  # ...and a different alias, at the same time
        "credential_type": "Git",
        "credential_values": [{"key": "token", "value": "secret"}],
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put("/v1/settings/project/set-1", headers={"user-id": "admin1"}, json=request_data)

    assert response.status_code == 422
    mock_update_settings.assert_called_once()
    # The move must never happen once update_settings fails
    mock_transfer_service.transfer_setting.assert_not_called()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.Settings")
@patch("codemie.rest_api.routers.project_settings.Ability")
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.routers.project_settings._check_permission")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_project_setting_rejects_unauthorized_project_change(
    mock_authenticate, mock_check_perm, mock_get_ability, mock_ability, mock_settings
):
    """
    When a user without admin permission on the destination project tries to
    change project_name, the request should fail with 403.
    """
    mock_authenticate.return_value = _regular_user()
    mock_ability.return_value.can.return_value = True

    # Mock existing setting with project-a
    existing_setting = Mock()
    existing_setting.project_name = "project-a"
    mock_settings.get_by_id.return_value = existing_setting

    # _check_permission raises 403 when user lacks permission
    mock_check_perm.side_effect = ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details="You do not have sufficient permissions to access the project 'project-b'.",
        help="This action requires application admin privileges.",
    )

    request_data = {
        "project_name": "project-b",  # User doesn't have admin permission here
        "alias": "my-git",
        "credential_type": "Git",
        "credential_values": [{"key": "token", "value": "secret"}],
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put("/v1/settings/project/set-1", headers={"user-id": "user1"}, json=request_data)

    assert response.status_code == 403
    assert "Access denied" in response.json()["error"]["message"]
    mock_check_perm.assert_called_once_with(mock_authenticate.return_value, "project-b")


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.Settings")
@patch("codemie.rest_api.routers.project_settings.Ability")
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.routers.project_settings._check_permission")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_project_setting_skips_permission_check_when_project_unchanged(
    mock_authenticate,
    mock_check_perm,
    mock_get_ability,
    mock_update_settings,
    mock_ability,
    mock_settings,
):
    """
    When updating a setting WITHOUT changing project_name, _check_permission
    should NOT be called (no need to re-validate same project).
    """
    mock_authenticate.return_value = _admin_user()
    mock_ability.return_value.can.return_value = True

    # Mock existing setting with project-a
    existing_setting = Mock()
    existing_setting.project_name = "project-a"
    mock_settings.get_by_id.return_value = existing_setting

    request_data = {
        "project_name": "project-a",  # SAME project
        "alias": "updated-alias",
        "credential_type": "Git",
        "credential_values": [{"key": "token", "value": "secret"}],
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put("/v1/settings/project/set-1", headers={"user-id": "admin1"}, json=request_data)

    assert response.status_code == 200
    # _check_permission should NOT be called when project doesn't change
    mock_check_perm.assert_not_called()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.project_settings.SettingsTransferService")
@patch("codemie.rest_api.routers.project_settings.Settings")
@patch("codemie.rest_api.routers.project_settings.Ability")
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.routers.project_settings._check_permission")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_project_setting_allows_global_admin_to_change_any_project(
    mock_authenticate,
    mock_check_perm,
    mock_get_ability,
    mock_update_settings,
    mock_ability,
    mock_settings,
    mock_transfer_service,
):
    """
    Global admins (is_admin=True) should be able to change project to any project,
    as _check_permission returns True immediately for global admins.
    """
    mock_authenticate.return_value = _admin_user()
    mock_ability.return_value.can.return_value = True

    # Mock existing setting
    existing_setting = Mock()
    existing_setting.project_name = "project-a"
    mock_settings.get_by_id.return_value = existing_setting

    # _check_permission returns True for global admins
    mock_check_perm.return_value = True

    request_data = {
        "project_name": "project-xyz",  # Any project
        "alias": "my-git",
        "credential_type": "Git",
        "credential_values": [{"key": "token", "value": "secret"}],
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put("/v1/settings/project/set-1", headers={"user-id": "admin1"}, json=request_data)

    assert response.status_code == 200
    mock_check_perm.assert_called_once_with(mock_authenticate.return_value, "project-xyz")
