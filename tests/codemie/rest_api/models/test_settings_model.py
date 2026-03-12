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

from codemie.rest_api.models.settings import Settings, SettingType, Credentials, GitAuthType


@patch.object(Settings, "get_by_fields", return_value=None)
def test_settings_check_alias_empty(mock_get_by_fields):
    with pytest.raises(ValueError, match="Alias is required"):
        Settings.check_alias_unique(
            project_name="test_project",
            alias="",
            user_id="user_123",
            setting_type=SettingType.USER,
        )
    mock_get_by_fields.assert_not_called()


@patch.object(Settings, "get_by_fields")
def test_settings_check_alias_non_unique(mock_get_by_fields):
    mock_settings = MagicMock(id="existing_setting_id")
    mock_get_by_fields.return_value = mock_settings

    with pytest.raises(ValueError, match="There are more than one settings with the alias named"):
        Settings.check_alias_unique(
            project_name="test_project",
            alias="duplicate_alias",
            user_id="user_123",
            setting_id="different_setting_id",
            setting_type=SettingType.USER,
        )
    mock_get_by_fields.assert_called_once_with(
        {"project_name.keyword": "test_project", "user_id.keyword": "user_123", "alias.keyword": "duplicate_alias"}
    )


@patch.object(Settings, "get_by_fields", return_value=None)
def test_settings_check_alias_ok(mock_get_by_fields):
    result = Settings.check_alias_unique(
        project_name="test_project",
        alias="unique_alias",
        user_id="user_123",
        setting_id="unique_setting_id",
        setting_type=SettingType.USER,
    )
    assert result is True
    mock_get_by_fields.assert_called_once_with(
        {"project_name.keyword": "test_project", "user_id.keyword": "user_123", "alias.keyword": "unique_alias"}
    )


@patch.object(Settings, "get_by_fields", return_value=None)
def test_settings_check_alias_ok_project(mock_get_by_fields):
    result = Settings.check_alias_unique(
        project_name="test_project",
        alias="unique_alias",
        user_id="user_123",
        setting_id="unique_setting_id",
        setting_type=SettingType.PROJECT,
    )
    assert result is True
    mock_get_by_fields.assert_called_once_with(
        {"project_name.keyword": "test_project", "alias.keyword": "unique_alias"}
    )


# Credentials class tests


@pytest.mark.parametrize(
    "auth_type,token,token_name,app_id,private_key,installation_id,expected_is_github_app",
    [
        # Valid PAT authentication
        (
            GitAuthType.PAT,
            "ghp_test_token_123",
            "test_token",
            None,
            None,
            None,
            False,
        ),
        # Valid GitHub App authentication with installation_id
        (
            GitAuthType.GITHUB_APP,
            None,
            None,
            12345,
            "-----BEGIN RSA PRIVATE KEY-----\ntest_key\n-----END RSA PRIVATE KEY-----",
            67890,
            True,
        ),
        # Valid GitHub App authentication without installation_id
        (
            GitAuthType.GITHUB_APP,
            None,
            None,
            12345,
            "test_key",
            None,
            True,
        ),
    ],
)
def test_credentials_valid_authentication(
    auth_type, token, token_name, app_id, private_key, installation_id, expected_is_github_app
):
    # Arrange & Act - Create valid credentials
    credentials = Credentials(
        url="https://github.com/test/repo",
        auth_type=auth_type,
        token=token,
        token_name=token_name,
        app_id=app_id,
        private_key=private_key,
        installation_id=installation_id,
    )

    # Assert - Verify all fields are set correctly
    assert credentials.url == "https://github.com/test/repo"
    assert credentials.auth_type == auth_type
    assert credentials.is_github_app == expected_is_github_app
    if token:
        assert credentials.token == token
    if app_id:
        assert credentials.app_id == app_id
    if private_key:
        assert credentials.private_key == private_key


@pytest.mark.parametrize(
    "token,app_id,private_key,expected_error",
    [
        # PAT missing token with GitHub App field present
        (None, 12345, None, "PAT authentication requires 'token'"),
        # PAT with app_id
        ("ghp_test_token_123", 12345, None, "Cannot set GitHub App fields when using PAT authentication"),
        # PAT with private_key
        ("ghp_test_token_123", None, "test_key", "Cannot set GitHub App fields when using PAT authentication"),
        # PAT with both GitHub App fields
        ("ghp_test_token_123", 12345, "test_key", "Cannot set GitHub App fields when using PAT authentication"),
    ],
)
def test_credentials_invalid_pat_authentication(token, app_id, private_key, expected_error):
    # Arrange & Act & Assert - Invalid PAT authentication should raise ValueError
    with pytest.raises(ValueError, match=expected_error):
        Credentials(
            url="https://github.com/test/repo",
            auth_type=GitAuthType.PAT,
            token=token,
            app_id=app_id,
            private_key=private_key,
        )


@pytest.mark.parametrize(
    "app_id,private_key,token,expected_error",
    [
        # GitHub App missing app_id
        (None, "test_key", None, "GitHub App authentication requires 'app_id'"),
        # GitHub App missing private_key
        (12345, None, None, "GitHub App authentication requires 'private_key'"),
        # GitHub App with PAT token
        (12345, "test_key", "ghp_test_token_123", "Cannot set PAT token when using GitHub App authentication"),
    ],
)
def test_credentials_invalid_github_app_authentication(app_id, private_key, token, expected_error):
    # Arrange & Act & Assert - Invalid GitHub App authentication should raise ValueError
    with pytest.raises(ValueError, match=expected_error):
        Credentials(
            url="https://github.com/test/repo",
            auth_type=GitAuthType.GITHUB_APP,
            app_id=app_id,
            private_key=private_key,
            token=token,
        )


def test_credentials_empty_backward_compatibility():
    # Arrange & Act - Create credentials with no auth fields (backward compatibility)
    credentials = Credentials(url="https://github.com/test/repo", auth_type=GitAuthType.PAT)

    # Assert - Should not raise validation error when all auth fields are empty
    assert credentials.url == "https://github.com/test/repo"
    assert credentials.auth_type == GitAuthType.PAT
    assert credentials.token is None
    assert credentials.app_id is None
    assert credentials.private_key is None


@pytest.mark.parametrize(
    "auth_type,expected_is_github_app",
    [
        (GitAuthType.PAT, False),
        (GitAuthType.GITHUB_APP, True),
    ],
)
def test_credentials_is_github_app_property(auth_type, expected_is_github_app):
    # Arrange - Prepare credentials based on auth_type
    if auth_type == GitAuthType.PAT:
        credentials = Credentials(
            url="https://github.com/test/repo",
            auth_type=auth_type,
            token="ghp_test_token_123",
        )
    else:  # GitAuthType.GITHUB_APP
        credentials = Credentials(
            url="https://github.com/test/repo",
            auth_type=auth_type,
            app_id=12345,
            private_key="test_key",
        )

    # Act & Assert - is_github_app property should match auth_type
    assert credentials.is_github_app == expected_is_github_app


def test_credentials_validate_config_name_to_token_name():
    # Arrange & Act - Test backward compatibility: "name" field should be converted to "token_name"
    credentials = Credentials.model_validate(
        {
            "url": "https://github.com/test/repo",
            "auth_type": "pat",
            "token": "ghp_test_token_123",
            "name": "legacy_name",
        }
    )

    # Assert - "name" should be converted to "token_name"
    assert credentials.token_name == "legacy_name"
    assert credentials.token == "ghp_test_token_123"


def test_credentials_validate_config_preserves_token_name():
    # Arrange & Act - Ensure token_name field works correctly
    credentials = Credentials(
        url="https://github.com/test/repo",
        auth_type=GitAuthType.PAT,
        token="ghp_test_token_123",
        token_name="explicit_token_name",
    )

    # Assert - token_name should be preserved
    assert credentials.token_name == "explicit_token_name"


@pytest.mark.parametrize(
    "installation_id,expected_installation_id",
    [
        (67890, 67890),  # With installation_id
        (None, None),  # Without installation_id (optional field)
    ],
)
def test_credentials_github_app_installation_id(installation_id, expected_installation_id):
    # Arrange & Act - Test GitHub App with/without installation_id
    credentials = Credentials(
        url="https://github.com/test/repo",
        auth_type=GitAuthType.GITHUB_APP,
        app_id=12345,
        private_key="test_key",
        installation_id=installation_id,
    )

    # Assert - installation_id should match expected value
    assert credentials.installation_id == expected_installation_id
    assert credentials.is_github_app is True
