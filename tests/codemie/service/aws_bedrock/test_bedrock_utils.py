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
from botocore.exceptions import ClientError

from codemie.service.aws_bedrock.utils import (
    get_setting_for_user,
    get_all_settings_for_user,
    get_setting_aws_credentials,
    get_aws_client_for_service,
    handle_aws_call,
)
from codemie.service.aws_bedrock.exceptions import (
    SettingNotFoundException,
    SettingAWSCredentialTypeRequired,
    SettingAccessDeniedException,
    SettingIdRequiredException,
    AwsCredentialsNotFoundException,
)
from codemie.rest_api.models.settings import AWSCredentials, CredentialTypes, SettingType
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    user = MagicMock(spec=User)
    user.id = "user-123"
    user.is_admin = False
    user.project_names = ["project1", "project2"]
    user.admin_project_names = ["admin_project1"]
    return user


@pytest.fixture
def mock_admin_user():
    """Create a mock admin user for testing."""
    user = MagicMock(spec=User)
    user.id = "admin-123"
    user.is_admin = True
    user.project_names = ["project1"]
    user.admin_project_names = []
    return user


@pytest.fixture
def mock_user_setting():
    """Create a mock user setting."""
    setting = MagicMock()
    setting.id = "setting-123"
    setting.credential_type = CredentialTypes.AWS
    setting.setting_type = SettingType.USER
    setting.user_id = "user-123"
    setting.project_name = "user_project"
    return setting


@pytest.fixture
def mock_project_setting():
    """Create a mock project setting."""
    setting = MagicMock()
    setting.id = "setting-456"
    setting.credential_type = CredentialTypes.AWS
    setting.setting_type = SettingType.PROJECT
    setting.user_id = "other-user"
    setting.project_name = "project1"
    return setting


@pytest.fixture
def mock_aws_credentials():
    """Create mock AWS credentials."""
    return AWSCredentials(
        region="us-east-1",
        access_key_id="test-access-key",
        secret_access_key="test-secret-key",
    )


# Tests for get_setting_for_user
class TestGetSettingForUser:
    @patch("codemie.service.aws_bedrock.utils.Settings.get_by_id")
    def test_get_setting_for_user_success_user_setting(self, mock_get_by_id, mock_user, mock_user_setting):
        """Test successful retrieval of user setting."""
        mock_get_by_id.return_value = mock_user_setting

        result = get_setting_for_user(mock_user, "setting-123")

        assert result == mock_user_setting
        mock_get_by_id.assert_called_once_with(id_="setting-123")

    @patch("codemie.service.aws_bedrock.utils.Settings.get_by_id")
    def test_get_setting_for_user_success_project_setting(self, mock_get_by_id, mock_user, mock_project_setting):
        """Test successful retrieval of project setting for accessible project."""
        mock_get_by_id.return_value = mock_project_setting

        result = get_setting_for_user(mock_user, "setting-456")

        assert result == mock_project_setting
        mock_get_by_id.assert_called_once_with(id_="setting-456")

    @patch("codemie.service.aws_bedrock.utils.Settings.get_by_id")
    def test_get_setting_for_user_success_admin_user(self, mock_get_by_id, mock_admin_user, mock_project_setting):
        """Test successful retrieval for admin user."""
        mock_get_by_id.return_value = mock_project_setting

        result = get_setting_for_user(mock_admin_user, "setting-456")

        assert result == mock_project_setting
        mock_get_by_id.assert_called_once_with(id_="setting-456")

    @patch("codemie.service.aws_bedrock.utils.Settings.get_by_id")
    def test_get_setting_for_user_not_found(self, mock_get_by_id, mock_user):
        """Test setting not found exception."""
        mock_get_by_id.side_effect = KeyError()

        with pytest.raises(SettingNotFoundException) as exc_info:
            get_setting_for_user(mock_user, "nonexistent-setting")

        assert exc_info.value.setting_id == "nonexistent-setting"

    @patch("codemie.service.aws_bedrock.utils.Settings.get_by_id")
    def test_get_setting_for_user_key_error(self, mock_get_by_id, mock_user):
        """Test setting not found via KeyError."""
        mock_get_by_id.side_effect = KeyError("Setting not found")

        with pytest.raises(SettingNotFoundException) as exc_info:
            get_setting_for_user(mock_user, "nonexistent-setting")

        assert exc_info.value.setting_id == "nonexistent-setting"

    @patch("codemie.service.aws_bedrock.utils.Settings.get_by_id")
    def test_get_setting_for_user_wrong_credential_type(self, mock_get_by_id, mock_user):
        """Test setting with wrong credential type."""
        setting = MagicMock()
        setting.credential_type = CredentialTypes.AZURE  # Not AWS
        mock_get_by_id.return_value = setting

        with pytest.raises(SettingAWSCredentialTypeRequired) as exc_info:
            get_setting_for_user(mock_user, "setting-123")

        assert exc_info.value.setting_id == "setting-123"

    @patch("codemie.service.aws_bedrock.utils.Settings.get_by_id")
    def test_get_setting_for_user_access_denied_wrong_user(self, mock_get_by_id, mock_user):
        """Test access denied for user setting owned by different user."""
        setting = MagicMock()
        setting.credential_type = CredentialTypes.AWS
        setting.setting_type = SettingType.USER
        setting.user_id = "other-user"  # Different user
        setting.project_name = "user_project"
        mock_get_by_id.return_value = setting

        with pytest.raises(SettingAccessDeniedException) as exc_info:
            get_setting_for_user(mock_user, "setting-123")

        assert exc_info.value.user_id == "user-123"
        assert exc_info.value.setting_id == "setting-123"
        assert exc_info.value.project_name == "user_project"

    @patch("codemie.service.aws_bedrock.utils.Settings.get_by_id")
    def test_get_setting_for_user_access_denied_wrong_project(self, mock_get_by_id, mock_user):
        """Test access denied for project setting user doesn't have access to."""
        setting = MagicMock()
        setting.credential_type = CredentialTypes.AWS
        setting.setting_type = SettingType.PROJECT
        setting.user_id = "other-user"
        setting.project_name = "inaccessible_project"  # User doesn't have access
        mock_get_by_id.return_value = setting

        with pytest.raises(SettingAccessDeniedException) as exc_info:
            get_setting_for_user(mock_user, "setting-123")

        assert exc_info.value.user_id == "user-123"
        assert exc_info.value.setting_id == "setting-123"
        assert exc_info.value.project_name == "inaccessible_project"


# Tests for get_all_settings_for_user
class TestGetAllSettingsForUser:
    @patch("codemie.service.aws_bedrock.utils.SettingsService.get_settings")
    def test_get_all_settings_for_user_success(self, mock_get_settings, mock_user):
        """Test successful retrieval of all settings for user."""
        user_settings = [MagicMock(), MagicMock()]
        project_settings = [MagicMock()]

        mock_get_settings.side_effect = [user_settings, project_settings]

        result = get_all_settings_for_user(mock_user)

        assert len(result) == 3
        assert result == user_settings + project_settings

        # Verify calls
        assert mock_get_settings.call_count == 2
        mock_get_settings.assert_any_call(
            user_id="user-123",
            settings_type=SettingType.USER,
            credential_type=CredentialTypes.AWS,
        )

        # Get the actual call arguments for project names to handle order-independent comparison
        project_call = None
        for call in mock_get_settings.call_args_list:
            if 'project_names' in call.kwargs:
                project_call = call
                break

        assert project_call is not None
        # Compare project names as sets since order doesn't matter
        expected_projects = {"admin_project1", "project1", "project2"}
        actual_projects = set(project_call.kwargs['project_names'])
        assert actual_projects == expected_projects
        assert project_call.kwargs['settings_type'] == SettingType.PROJECT
        assert project_call.kwargs['credential_type'] == CredentialTypes.AWS

    @patch("codemie.service.aws_bedrock.utils.SettingsService.get_settings")
    @patch("codemie.service.aws_bedrock.utils.SettingsService.get_all_settings")
    def test_get_all_settings_for_user_no_projects(self, mock_get_all_settings, mock_get_settings):
        """Test retrieval for user with no project access."""
        user = MagicMock(spec=User)
        user.id = "user-123"
        user.project_names = []
        user.admin_project_names = []

        single_setting = [MagicMock()]
        mock_get_all_settings.return_value = single_setting
        mock_get_settings.return_value = single_setting

        result = get_all_settings_for_user(user)

        assert len(result) == 2
        assert result == [single_setting[0], single_setting[0]]

        # Should only call once for user settings and once for the admin all project settings
        mock_get_settings.assert_called_once_with(
            user_id="user-123",
            settings_type=SettingType.USER,
            credential_type=CredentialTypes.AWS,
        )

        mock_get_all_settings.assert_called_once_with(
            settings_type=SettingType.PROJECT,
            credential_type=CredentialTypes.AWS,
        )

    @patch("codemie.service.aws_bedrock.utils.SettingsService.get_settings")
    @patch("codemie.service.aws_bedrock.utils.SettingsService.get_all_settings")
    def test_get_all_settings_for_user_none_values(self, mock_get_all_settings, mock_get_settings):
        """Test retrieval for user with None values in applications."""
        user = MagicMock(spec=User)
        user.id = "user-123"
        user.project_names = None
        user.admin_project_names = None

        single_setting = [MagicMock()]
        mock_get_all_settings.return_value = single_setting
        mock_get_settings.return_value = single_setting

        result = get_all_settings_for_user(user)

        assert len(result) == 2
        assert result == [single_setting[0], single_setting[0]]

        # Should only call once for user settings and once for the admin all project settings
        mock_get_settings.assert_called_once_with(
            user_id="user-123",
            settings_type=SettingType.USER,
            credential_type=CredentialTypes.AWS,
        )

        mock_get_all_settings.assert_called_once_with(
            settings_type=SettingType.PROJECT,
            credential_type=CredentialTypes.AWS,
        )


# Tests for get_setting_aws_credentials
class TestGetSettingAwsCredentials:
    @patch("codemie.service.aws_bedrock.utils.SettingsService.get_aws_creds")
    def test_get_setting_aws_credentials_success(self, mock_get_aws_creds, mock_aws_credentials):
        """Test successful retrieval of AWS credentials."""
        mock_get_aws_creds.return_value = mock_aws_credentials

        result = get_setting_aws_credentials("setting-123")

        assert result == mock_aws_credentials
        mock_get_aws_creds.assert_called_once_with(integration_id="setting-123")

    def test_get_setting_aws_credentials_no_setting_id(self):
        """Test exception when no setting ID provided."""
        with pytest.raises(SettingIdRequiredException):
            get_setting_aws_credentials(None)

    @patch("codemie.service.aws_bedrock.utils.SettingsService.get_aws_creds")
    def test_get_setting_aws_credentials_not_found(self, mock_get_aws_creds):
        """Test exception when AWS credentials not found."""
        mock_get_aws_creds.return_value = None

        with pytest.raises(AwsCredentialsNotFoundException) as exc_info:
            get_setting_aws_credentials("setting-123")

        assert exc_info.value.setting_id == "setting-123"


# Tests for get_aws_client_for_service
class TestGetAwsClientForService:
    @patch("codemie.service.aws_bedrock.utils.boto3.client")
    @patch("codemie.service.aws_bedrock.utils.Config")
    def test_get_aws_client_for_service_success(self, mock_config, mock_boto_client):
        """Test successful creation of AWS client."""
        mock_client_instance = MagicMock()
        mock_boto_client.return_value = mock_client_instance
        mock_config_instance = MagicMock()
        mock_config.return_value = mock_config_instance

        result = get_aws_client_for_service(
            service="bedrock", region="us-east-1", access_key_id="test-key", secret_access_key="test-secret"
        )

        assert result == mock_client_instance
        mock_config.assert_called_once_with(region_name="us-east-1")
        mock_boto_client.assert_called_once_with(
            "bedrock",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret",
            aws_session_token=None,
            config=mock_config_instance,
        )


# Tests for handle_aws_call
class TestHandleAwsCall:
    def test_handle_aws_call_success(self):
        """Test successful AWS call."""

        def test_func(x, y):
            return x + y

        result = handle_aws_call(test_func, 5, 3)

        assert result == 8

    def test_handle_aws_call_with_kwargs(self):
        """Test successful AWS call with kwargs."""

        def test_func(x, y=10):
            return x * y

        result = handle_aws_call(test_func, 5, y=2)

        assert result == 10

    @patch("codemie.service.aws_bedrock.utils.logger")
    def test_handle_aws_call_client_error(self, mock_logger):
        """Test handling of ClientError."""

        def failing_func():
            raise ClientError(
                error_response={"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
                operation_name="TestOperation",
            )

        with pytest.raises(ClientError):
            handle_aws_call(failing_func)

        mock_logger.error.assert_called_once()
        assert "AWS ClientError" in mock_logger.error.call_args[0][0]

    @patch("codemie.service.aws_bedrock.utils.logger")
    def test_handle_aws_call_generic_exception(self, mock_logger):
        """Test handling of generic exception."""

        def failing_func():
            raise ValueError("Something went wrong")

        with pytest.raises(ValueError):
            handle_aws_call(failing_func)

        mock_logger.error.assert_called_once()
        assert "Unexpected error when calling AWS" in mock_logger.error.call_args[0][0]

    @patch("codemie.service.aws_bedrock.utils.logger")
    def test_handle_aws_call_no_arguments(self, mock_logger):
        """Test handling exception with no arguments passed."""

        def failing_func():
            raise KeyError("Generic error")

        with pytest.raises(KeyError):
            handle_aws_call(failing_func)

        mock_logger.error.assert_called_once()
