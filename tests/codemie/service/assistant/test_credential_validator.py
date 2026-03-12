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

"""Unit tests for CredentialValidator service."""

from unittest.mock import Mock, patch

import pytest
from codemie_tools.base.models import ToolSet
from pydantic import BaseModel

from codemie.rest_api.models.settings import SettingsBase
from codemie.rest_api.security.user import User
from codemie.service.assistant.credential_validator import CredentialValidator, ValidationResult


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_user():
    """Fixture for mocking User."""
    user = Mock(spec=User)
    user.id = "test-user-id"
    user.name = "Test User"
    return user


@pytest.fixture
def mock_tool_settings_with_credentials():
    """Fixture for tool settings with inline credentials."""
    settings = Mock(spec=SettingsBase)
    settings.credential_values = {"api_key": "test-key"}
    return settings


@pytest.fixture
def mock_tool_settings_without_credentials():
    """Fixture for tool settings without credentials."""
    settings = Mock(spec=SettingsBase)
    settings.credential_values = None
    return settings


@pytest.fixture
def mock_config_class():
    """Fixture for mock config class."""

    class TestConfig(BaseModel):
        credential_type: str = "AWS"
        api_key: str = ""

    return TestConfig


# ============================================================================
# Parametrized Tests for Main Validation Flow
# ============================================================================


@pytest.mark.parametrize(
    "requires_creds,has_inline_creds,stored_config,expected_valid,expected_cred_type",
    [
        # No credentials required
        (False, False, None, True, None),
        # Has inline credentials
        (True, True, None, True, None),
        # Has stored credentials
        (True, False, Mock(api_key="stored"), True, "AWS"),
        # Missing stored credentials
        (True, False, None, False, "AWS"),
    ],
    ids=[
        "no_credentials_required",
        "inline_credentials_provided",
        "stored_credentials_found",
        "stored_credentials_missing",
    ],
)
@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_validate_tool_credentials_main_flow(
    mock_metadata_service,
    mock_user,
    mock_config_class,
    requires_creds,
    has_inline_creds,
    stored_config,
    expected_valid,
    expected_cred_type,
):
    """Test main validation flow with different credential scenarios."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = False
    mock_metadata_service.requires_credentials.return_value = requires_creds

    if requires_creds:
        mock_metadata_service.get_config_class.return_value = mock_config_class
        mock_metadata_service.resolve_config.return_value = stored_config
        mock_metadata_service.get_credential_type.return_value = "AWS"

    tool_settings = Mock(spec=SettingsBase) if has_inline_creds else None
    if has_inline_creds:
        tool_settings.credential_values = {"api_key": "inline-key"}

    # Execute
    result = CredentialValidator.validate_tool_credentials(
        toolkit_name="test_toolkit",
        tool_name="test_tool",
        user=mock_user,
        project_name="test-project",
        tool_settings=tool_settings,
    )

    # Assert
    assert result.is_valid == expected_valid
    assert result.credential_type == expected_cred_type


@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_validate_tool_credentials_no_config_class(mock_metadata_service, mock_user):
    """Test validation when config_class is not found."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = False
    mock_metadata_service.requires_credentials.return_value = True
    mock_metadata_service.get_config_class.return_value = None

    # Execute
    result = CredentialValidator.validate_tool_credentials(
        toolkit_name="test_toolkit",
        tool_name="test_tool",
        user=mock_user,
        project_name="test-project",
        tool_settings=None,
    )

    # Assert - Should return valid if can't validate (defensive programming)
    assert result.is_valid is True
    assert result.credential_type is None


# ============================================================================
# Parametrized Tests for Internal Toolkits (Git, Plugin)
# ============================================================================


@pytest.mark.parametrize(
    "toolkit,creds_exist,creds_complete,expected_valid",
    [
        # Git toolkit
        (ToolSet.GIT, True, True, True),
        (ToolSet.GIT, True, False, False),  # Missing token
        (ToolSet.GIT, False, False, False),
        # Plugin toolkit
        (ToolSet.PLUGIN, True, True, True),
        (ToolSet.PLUGIN, False, False, False),
    ],
    ids=[
        "git_with_valid_credentials",
        "git_with_incomplete_credentials",
        "git_without_credentials",
        "plugin_with_valid_credentials",
        "plugin_without_credentials",
    ],
)
@patch("codemie.service.assistant.credential_validator.SettingsService")
@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_internal_toolkit_validation(
    mock_metadata_service,
    mock_settings_service,
    mock_user,
    toolkit,
    creds_exist,
    creds_complete,
    expected_valid,
):
    """Test validation for internal toolkits (Git, Plugin)."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = True

    if toolkit == ToolSet.GIT:
        if creds_exist:
            mock_creds = Mock()
            mock_creds.url = "https://github.com/test/repo"
            mock_creds.token = "test-token" if creds_complete else None
            mock_settings_service.get_git_creds.return_value = mock_creds
        else:
            mock_settings_service.get_git_creds.return_value = None
    elif toolkit == ToolSet.PLUGIN:
        if creds_exist:
            mock_creds = Mock()
            mock_creds.plugin_key = "test-key"
            mock_settings_service.get_plugin_creds.return_value = mock_creds
        else:
            mock_settings_service.get_plugin_creds.return_value = None

    # Execute
    result = CredentialValidator._check_internal_toolkit_credentials(
        toolkit_name=toolkit,
        tool_name="test_tool",
        user=mock_user,
        project_name="test-project",
    )

    # Assert
    assert result.is_valid == expected_valid
    assert result.credential_type == toolkit.value


@patch("codemie.service.assistant.credential_validator.SettingsService")
@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_internal_toolkit_unknown(mock_metadata_service, mock_settings_service, mock_user):
    """Test validation for unknown internal toolkit."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = True

    # Execute
    result = CredentialValidator._check_internal_toolkit_credentials(
        toolkit_name="UnknownToolkit",
        tool_name="unknown_tool",
        user=mock_user,
        project_name="test-project",
    )

    # Assert - Should return valid for unknown toolkit (defensive)
    assert result.is_valid is True


@patch("codemie.service.assistant.credential_validator.SettingsService")
@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_internal_toolkit_exception_handling(mock_metadata_service, mock_settings_service, mock_user):
    """Test exception handling for internal toolkit validation."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = True
    mock_settings_service.get_git_creds.side_effect = Exception("Database error")

    # Execute
    result = CredentialValidator._check_internal_toolkit_credentials(
        toolkit_name=ToolSet.GIT,
        tool_name="git_clone",
        user=mock_user,
        project_name="test-project",
    )

    # Assert - Should return valid on error (defensive) but with credential_type
    assert result.is_valid is True
    assert result.credential_type == "Git"


# ============================================================================
# Edge Case Tests
# ============================================================================


@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_validate_tool_credentials_with_empty_inline_credentials(
    mock_metadata_service, mock_user, mock_tool_settings_without_credentials, mock_config_class
):
    """Test validation when inline credentials object exists but is empty."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = False
    mock_metadata_service.requires_credentials.return_value = True
    mock_metadata_service.get_config_class.return_value = mock_config_class
    mock_metadata_service.resolve_config.return_value = None
    mock_metadata_service.get_credential_type.return_value = "AWS"

    # Execute
    result = CredentialValidator.validate_tool_credentials(
        toolkit_name="test_toolkit",
        tool_name="test_tool",
        user=mock_user,
        project_name="test-project",
        tool_settings=mock_tool_settings_without_credentials,
    )

    # Assert - Should try to resolve stored credentials
    assert result.is_valid is False
    assert result.credential_type == "AWS"
    mock_metadata_service.resolve_config.assert_called_once()


@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_validate_tool_credentials_enum_toolkit_name(mock_metadata_service, mock_user):
    """Test validation handles enum toolkit names correctly."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = False
    mock_metadata_service.requires_credentials.return_value = False

    # Execute - Pass enum instead of string
    result = CredentialValidator.validate_tool_credentials(
        toolkit_name=ToolSet.RESEARCH,  # Enum value
        tool_name="test_tool",
        user=mock_user,
        project_name="test-project",
        tool_settings=None,
    )

    # Assert
    assert result.is_valid is True
    mock_metadata_service.requires_credentials.assert_called_once_with("test_tool", ToolSet.RESEARCH)


def test_validation_result_model():
    """Test ValidationResult model can be created correctly."""
    # Execute - Create directly
    result1 = ValidationResult(is_valid=True, credential_type="AWS")
    result2 = ValidationResult(is_valid=False)

    # Assert
    assert result1.is_valid is True
    assert result1.credential_type == "AWS"
    assert result2.is_valid is False
    assert result2.credential_type is None


# ============================================================================
# Tests for Internal Toolkit Credentials with tool_settings (Bug Fix)
# ============================================================================


@patch("codemie.service.assistant.credential_validator.SettingsService")
@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_internal_toolkit_with_settings_id(mock_metadata_service, mock_settings_service, mock_user):
    """Test internal toolkit validation with tool_settings containing id."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = True

    tool_settings = Mock(spec=SettingsBase)
    tool_settings.id = "c3684988-cbeb-4fc9-818b-6da1d9b8a50d"
    tool_settings.alias = "github"
    tool_settings.credential_values = [{"key": "token", "value": "test-token"}]

    # Mock the conversion
    from codemie.core.models import ToolConfig

    mock_tool_config = ToolConfig(name="", integration_id="c3684988-cbeb-4fc9-818b-6da1d9b8a50d")
    mock_metadata_service._convert_settings_to_config.return_value = mock_tool_config

    # Mock git credentials found
    mock_creds = Mock()
    mock_creds.url = "https://github.com"
    mock_creds.token = "valid-token"
    mock_settings_service.get_git_creds.return_value = mock_creds

    # Execute
    result = CredentialValidator._check_internal_toolkit_credentials(
        toolkit_name=ToolSet.GIT,
        tool_name="create_branch",
        user=mock_user,
        project_name="demo",
        tool_settings=tool_settings,
        assistant_id=None,
    )

    # Assert
    assert result.is_valid is True
    assert result.credential_type == "Git"

    # Verify tool_config was passed to get_git_creds
    mock_settings_service.get_git_creds.assert_called_once_with(
        user_id=mock_user.id,
        project_name="demo",
        repo_link=None,
        tool_config=mock_tool_config,
        assistant_id=None,
    )


@patch("codemie.service.assistant.credential_validator.SettingsService")
@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_internal_toolkit_with_settings_inline_credentials(mock_metadata_service, mock_settings_service, mock_user):
    """Test internal toolkit validation with tool_settings containing inline credentials."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = True

    tool_settings = Mock(spec=SettingsBase)
    tool_settings.id = None
    tool_settings.credential_values = {"token": "inline-token", "url": "https://gitlab.com"}

    # Mock the conversion to inline credentials
    from codemie.core.models import ToolConfig

    mock_tool_config = ToolConfig(name="", tool_creds={"token": "inline-token", "url": "https://gitlab.com"})
    mock_metadata_service._convert_settings_to_config.return_value = mock_tool_config

    # Mock git credentials found
    mock_creds = Mock()
    mock_creds.url = "https://gitlab.com"
    mock_creds.token = "inline-token"
    mock_settings_service.get_git_creds.return_value = mock_creds

    # Execute
    result = CredentialValidator._check_internal_toolkit_credentials(
        toolkit_name=ToolSet.GIT,
        tool_name="create_file",
        user=mock_user,
        project_name="test-project",
        tool_settings=tool_settings,
        assistant_id=None,
    )

    # Assert
    assert result.is_valid is True
    assert result.credential_type == "Git"

    # Verify tool_config with inline credentials was passed
    mock_settings_service.get_git_creds.assert_called_once_with(
        user_id=mock_user.id,
        project_name="test-project",
        repo_link=None,
        tool_config=mock_tool_config,
        assistant_id=None,
    )


@patch("codemie.service.assistant.credential_validator.SettingsService")
@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_internal_toolkit_plugin_with_settings_id(mock_metadata_service, mock_settings_service, mock_user):
    """Test Plugin toolkit validation with tool_settings containing id."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = True

    tool_settings = Mock(spec=SettingsBase)
    tool_settings.id = "plugin-settings-uuid"
    tool_settings.alias = "my-plugin"

    # Mock the conversion
    from codemie.core.models import ToolConfig

    mock_tool_config = ToolConfig(name="", integration_id="plugin-settings-uuid")
    mock_metadata_service._convert_settings_to_config.return_value = mock_tool_config

    # Mock plugin credentials found
    mock_creds = Mock()
    mock_creds.plugin_key = "valid-plugin-key"
    mock_settings_service.get_plugin_creds.return_value = mock_creds

    # Execute
    result = CredentialValidator._check_internal_toolkit_credentials(
        toolkit_name=ToolSet.PLUGIN,
        tool_name="execute_plugin",
        user=mock_user,
        project_name="demo",
        tool_settings=tool_settings,
        assistant_id=None,
    )

    # Assert
    assert result.is_valid is True
    assert result.credential_type == "Plugin"

    # Verify tool_config was passed to get_plugin_creds
    mock_settings_service.get_plugin_creds.assert_called_once_with(
        user_id=mock_user.id,
        project_name="demo",
        tool_config=mock_tool_config,
        assistant_id=None,
    )


@patch("codemie.service.assistant.credential_validator.SettingsService")
@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_internal_toolkit_without_settings(mock_metadata_service, mock_settings_service, mock_user):
    """Test internal toolkit validation without tool_settings (fallback to default behavior)."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = True

    # No credentials found
    mock_settings_service.get_git_creds.return_value = None

    # Execute
    result = CredentialValidator._check_internal_toolkit_credentials(
        toolkit_name=ToolSet.GIT,
        tool_name="create_branch",
        user=mock_user,
        project_name="demo",
        tool_settings=None,  # No settings provided
        assistant_id=None,
    )

    # Assert
    assert result.is_valid is False
    assert result.credential_type == "Git"

    # Verify get_git_creds was called with None tool_config
    mock_settings_service.get_git_creds.assert_called_once_with(
        user_id=mock_user.id,
        project_name="demo",
        repo_link=None,
        tool_config=None,
        assistant_id=None,
    )


@patch("codemie.service.assistant.credential_validator.SettingsService")
@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_internal_toolkit_settings_conversion_returns_none(mock_metadata_service, mock_settings_service, mock_user):
    """Test internal toolkit when settings conversion returns None."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = True

    tool_settings = Mock(spec=SettingsBase)
    tool_settings.id = None
    tool_settings.credential_values = None

    # Mock conversion returns None (invalid settings)
    mock_metadata_service._convert_settings_to_config.return_value = None

    # No credentials found
    mock_settings_service.get_git_creds.return_value = None

    # Execute
    result = CredentialValidator._check_internal_toolkit_credentials(
        toolkit_name=ToolSet.GIT,
        tool_name="delete_file",
        user=mock_user,
        project_name="test-project",
        tool_settings=tool_settings,
        assistant_id=None,
    )

    # Assert
    assert result.is_valid is False
    assert result.credential_type == "Git"

    # Verify get_git_creds was called with None tool_config
    mock_settings_service.get_git_creds.assert_called_once_with(
        user_id=mock_user.id,
        project_name="test-project",
        repo_link=None,
        tool_config=None,
        assistant_id=None,
    )


@patch("codemie.service.assistant.credential_validator.SettingsService")
@patch("codemie.service.assistant.credential_validator.ToolMetadataService")
def test_internal_toolkit_credentials_not_found_with_settings(mock_metadata_service, mock_settings_service, mock_user):
    """Test internal toolkit when credentials not found despite having settings."""
    # Setup
    mock_metadata_service.is_internal_toolkit.return_value = True

    tool_settings = Mock(spec=SettingsBase)
    tool_settings.id = "non-existent-id"
    tool_settings.alias = "missing-integration"

    # Mock the conversion
    from codemie.core.models import ToolConfig

    mock_tool_config = ToolConfig(name="", integration_id="non-existent-id")
    mock_metadata_service._convert_settings_to_config.return_value = mock_tool_config

    # Mock credentials not found (wrong integration_id)
    mock_settings_service.get_git_creds.return_value = None

    # Execute
    result = CredentialValidator._check_internal_toolkit_credentials(
        toolkit_name=ToolSet.GIT,
        tool_name="create_pull_request",
        user=mock_user,
        project_name="demo",
        tool_settings=tool_settings,
    )

    # Assert - Should be invalid because credentials not found
    assert result.is_valid is False
    assert result.credential_type == "Git"
