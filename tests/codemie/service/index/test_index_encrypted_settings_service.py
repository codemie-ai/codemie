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
from unittest.mock import patch, MagicMock

from codemie.service.index.index_encrypted_settings_service import (
    IndexEncryptedSettingsService,
    IndexEncryptedSettingsError,
)
from codemie.core.ability import Action
from codemie.rest_api.models.permission import PrincipalType, ResourceType


def test_init():
    index = MagicMock()
    user = MagicMock()

    service = IndexEncryptedSettingsService(index=index, user=user, x_request_id="test_request_id")

    assert service.index == index
    assert service.user == user


@patch("codemie.service.index.index_encrypted_settings_service.Permission")
def test_run_no_permissions(mock_permission):
    index = MagicMock()
    user = MagicMock()

    mock_permission.get_for.return_value = None

    service = IndexEncryptedSettingsService(index=index, user=user, x_request_id="test_request_id")

    with pytest.raises(IndexEncryptedSettingsError) as exc_info:
        service.run()

    assert "User does not have permission to read the index settings" in str(exc_info.value)
    mock_permission.get_for.assert_called_once_with(user=user, instance=index, action=Action.READ)


@patch("codemie.service.index.index_encrypted_settings_service.Permission")
def test_run_permission_principal_not_valid(mock_permission):
    index = MagicMock()
    user = MagicMock()

    mock_perm = MagicMock()
    mock_perm.principal_type = PrincipalType.USER  # Not an APPLICATION
    mock_permission.get_for.return_value = mock_perm

    service = IndexEncryptedSettingsService(index=index, user=user, x_request_id="test_request_id")

    with pytest.raises(IndexEncryptedSettingsError) as exc_info:
        service.run()

    assert "User is not an application, cannot access encrypted settings" in str(exc_info.value)


@patch("codemie.service.index.index_encrypted_settings_service.Permission")
@patch("codemie.service.index.index_encrypted_settings_service.authorized_applications_config")
def test_run_invalid_app_id(mock_app_config, mock_permission):
    index = MagicMock()
    user = MagicMock(username="test_app")

    mock_perm = MagicMock()
    mock_perm.principal_type = PrincipalType.APPLICATION
    mock_permission.get_for.return_value = mock_perm

    mock_app_config.find_by_name.return_value = None

    service = IndexEncryptedSettingsService(index=index, user=user, x_request_id="test_request_id")

    with pytest.raises(IndexEncryptedSettingsError) as exc_info:
        service.run()

    assert "Application test_app is not registered in the authorized applications" in str(exc_info.value)
    mock_app_config.find_by_name.assert_called_once_with(user.username)


@patch("codemie.service.index.index_encrypted_settings_service.Permission")
@patch("codemie.service.index.index_encrypted_settings_service.authorized_applications_config")
def test_run_invalid_resource_type(mock_app_config, mock_permission):
    index = MagicMock()
    user = MagicMock(username="test_app")

    mock_perm = MagicMock()
    mock_perm.principal_type = PrincipalType.APPLICATION
    mock_permission.get_for.return_value = mock_perm

    mock_app = MagicMock()
    mock_app.allowed_resources = ["workflow"]
    mock_app_config.find_by_name.return_value = mock_app

    service = IndexEncryptedSettingsService(index=index, user=user, x_request_id="test_request_id")

    with pytest.raises(IndexEncryptedSettingsError) as exc_info:
        service.run()

    assert "Application test_app is not allowed to access the datasource resource" in str(exc_info.value)


@patch("codemie.service.index.index_encrypted_settings_service.Permission")
@patch("codemie.service.index.index_encrypted_settings_service.authorized_applications_config")
def test_run_no_setting_id(mock_app_config, mock_permission):
    index = MagicMock()
    index.setting_id = None
    user = MagicMock(username="test_app")

    mock_perm = MagicMock()
    mock_perm.principal_type = PrincipalType.APPLICATION
    mock_permission.get_for.return_value = mock_perm

    mock_app = MagicMock()
    mock_app.allowed_resources = [ResourceType.DATASOURCE]
    mock_app_config.find_by_name.return_value = mock_app

    service = IndexEncryptedSettingsService(index=index, user=user, x_request_id="test_request_id")

    with pytest.raises(IndexEncryptedSettingsError) as exc_info:
        service.run()

    assert "Index does not have associated settings" in str(exc_info.value)


@patch("codemie.service.index.index_encrypted_settings_service.Permission")
@patch("codemie.service.index.index_encrypted_settings_service.authorized_applications_config")
@patch("codemie.service.index.index_encrypted_settings_service.Settings")
def test_run_no_settings_found(mock_settings, mock_app_config, mock_permission):
    index = MagicMock()
    index.setting_id = "setting123"
    user = MagicMock(username="test_app")
    mock_perm = MagicMock()
    mock_perm.principal_type = PrincipalType.APPLICATION
    mock_permission.get_for.return_value = mock_perm

    mock_app = MagicMock()
    mock_app.allowed_resources = [ResourceType.DATASOURCE]
    mock_app.get_public_key.return_value = "123"
    mock_app_config.find_by_name.return_value = mock_app

    mock_settings.get_by_id.side_effect = KeyError("Settings not found")

    service = IndexEncryptedSettingsService(index=index, user=user, x_request_id="test_request_id")

    with pytest.raises(IndexEncryptedSettingsError) as exc_info:
        service.run()

    assert "Settings with ID setting123 are not found" in str(exc_info.value)


@patch("codemie.service.index.index_encrypted_settings_service.Permission")
@patch("codemie.service.index.index_encrypted_settings_service.authorized_applications_config")
@patch("codemie.service.index.index_encrypted_settings_service.Settings")
@patch("codemie.service.index.index_encrypted_settings_service.SettingsIndexService")
def test_run_success(mock_settings_service, mock_settings, mock_app_config, mock_permission):
    index = MagicMock()
    index.setting_id = "setting123"
    index.id = "index123"
    user = MagicMock(username="test_app")
    key = b"-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA\n-----END PUBLIC KEY-----"

    mock_perm = MagicMock()
    mock_perm.principal_type = PrincipalType.APPLICATION
    mock_permission.get_for.return_value = mock_perm

    mock_app = MagicMock()
    mock_app.allowed_resources = [ResourceType.DATASOURCE]
    mock_app.get_public_key.return_value = key
    mock_app_config.find_by_name.return_value = mock_app

    mock_setting = MagicMock()
    mock_setting.credential_values = [MagicMock(value="secret_value")]
    mock_settings.get_by_id.return_value = mock_setting

    mock_settings_service._decrypt_fields.return_value = [MagicMock(value="decrypted_value")]

    service = IndexEncryptedSettingsService(index=index, user=user, x_request_id="test_request_id")
    service._encrypt_value = MagicMock(return_value="encrypted_base64_value")

    result = service.run()

    assert result == mock_setting
    mock_settings.get_by_id.assert_called_once_with(index.setting_id)
    service._encrypt_value.assert_called_with(value="decrypted_value", public_key=key)
    assert mock_setting.credential_values[0].value == "encrypted_base64_value"
