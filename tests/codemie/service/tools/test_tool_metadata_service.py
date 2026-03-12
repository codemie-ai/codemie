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

"""Unit tests for ToolMetadataService."""

from unittest.mock import Mock, patch

import pytest
from pydantic import BaseModel

from codemie.core.models import ToolConfig
from codemie.rest_api.models.settings import SettingsBase
from codemie.service.tools.tool_metadata_service import ToolMetadataService, get_enum_value


class TestGetEnumValue:
    """Test suite for get_enum_value utility function."""

    def test_get_enum_value_with_enum(self):
        """Test extracting value from an enum."""
        from codemie_tools.base.models import ToolSet

        # Execute
        result = get_enum_value(ToolSet.GIT)

        # Assert
        assert result == "Git"

    def test_get_enum_value_with_string(self):
        """Test with string input returns same string."""
        # Execute
        result = get_enum_value("Git")

        # Assert
        assert result == "Git"

    def test_get_enum_value_with_none(self):
        """Test with None returns None."""
        # Execute
        result = get_enum_value(None)

        # Assert
        assert result is None

    def test_get_enum_value_with_custom_enum(self):
        """Test with custom enum class."""
        from enum import Enum

        class CustomEnum(Enum):
            VALUE = "custom_value"

        # Execute
        result = get_enum_value(CustomEnum.VALUE)

        # Assert
        assert result == "custom_value"


class TestToolMetadataService:
    """Test suite for the ToolMetadataService class."""

    @pytest.fixture
    def mock_tool_definition(self):
        """Fixture for mock tool definition."""
        tool_def = Mock()
        tool_def.name = "test_tool"
        tool_def.settings_config = True
        tool_def.config_class = Mock()
        return tool_def

    @pytest.fixture
    def mock_toolkit_definition(self):
        """Fixture for mock toolkit definition."""
        toolkit_def = Mock()
        toolkit_def.settings_config = False
        toolkit_def.config_class = None
        return toolkit_def

    @pytest.fixture
    def mock_config_class(self):
        """Fixture for mock config class."""

        class TestConfig(BaseModel):
            credential_type: str = "AWS"
            api_key: str = ""

        return TestConfig

    # ============================================================================
    # Tests for _get_tool_and_toolkit_definitions
    # ============================================================================

    @patch("codemie.service.tools.tool_metadata_service.toolkit_provider")
    def test_get_tool_and_toolkit_definitions_success(self, mock_provider):
        """Test successful retrieval of tool and toolkit definitions."""
        # Setup
        mock_tool_def = Mock()
        mock_toolkit_class = Mock()
        mock_toolkit_def = Mock()
        mock_toolkit_class.get_definition.return_value = mock_toolkit_def

        mock_provider.get_toolkit.return_value = mock_toolkit_class
        mock_provider.get_tool.return_value = mock_tool_def

        # Execute
        tool_def, toolkit_def = ToolMetadataService._get_tool_and_toolkit_definitions(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
        )

        # Assert
        assert tool_def == mock_tool_def
        assert toolkit_def == mock_toolkit_def
        mock_provider.get_toolkit.assert_called_once_with("test_toolkit")
        mock_provider.get_tool.assert_called_once_with("test_tool")

    @patch("codemie.service.tools.tool_metadata_service.toolkit_provider")
    def test_get_tool_and_toolkit_definitions_toolkit_not_found(self, mock_provider):
        """Test when toolkit is not found."""
        # Setup
        mock_provider.get_toolkit.return_value = None

        # Execute
        tool_def, toolkit_def = ToolMetadataService._get_tool_and_toolkit_definitions(
            tool_name="test_tool",
            toolkit_name="unknown_toolkit",
        )

        # Assert
        assert tool_def is None
        assert toolkit_def is None
        mock_provider.get_tool.assert_not_called()

    @patch("codemie.service.tools.tool_metadata_service.toolkit_provider")
    def test_get_tool_and_toolkit_definitions_tool_not_found(self, mock_provider):
        """Test when tool is not found."""
        # Setup
        mock_toolkit_class = Mock()
        mock_toolkit_def = Mock()
        mock_toolkit_class.get_definition.return_value = mock_toolkit_def

        mock_provider.get_toolkit.return_value = mock_toolkit_class
        mock_provider.get_tool.return_value = None

        # Execute
        tool_def, toolkit_def = ToolMetadataService._get_tool_and_toolkit_definitions(
            tool_name="unknown_tool",
            toolkit_name="test_toolkit",
        )

        # Assert
        assert tool_def is None
        assert toolkit_def is None

    @patch("codemie.service.tools.tool_metadata_service.toolkit_provider")
    def test_get_tool_and_toolkit_definitions_with_enum(self, mock_provider):
        """Test with enum toolkit name."""
        from codemie_tools.base.models import ToolSet

        # Setup
        mock_toolkit_class = Mock()
        mock_toolkit_def = Mock()
        mock_toolkit_class.get_definition.return_value = mock_toolkit_def
        mock_tool_def = Mock()

        mock_provider.get_toolkit.return_value = mock_toolkit_class
        mock_provider.get_tool.return_value = mock_tool_def

        # Execute
        tool_def, toolkit_def = ToolMetadataService._get_tool_and_toolkit_definitions(
            tool_name="test_tool",
            toolkit_name=ToolSet.GIT,
        )

        # Assert
        assert tool_def == mock_tool_def
        assert toolkit_def == mock_toolkit_def
        mock_provider.get_toolkit.assert_called_once_with("Git")

    # ============================================================================
    # Tests for requires_credentials
    # ============================================================================

    @patch.object(ToolMetadataService, "_get_tool_and_toolkit_definitions")
    def test_requires_credentials_tool_requires(self, mock_get_defs, mock_tool_definition, mock_toolkit_definition):
        """Test when tool requires credentials."""
        # Setup
        mock_tool_definition.settings_config = True
        mock_toolkit_definition.settings_config = False
        mock_get_defs.return_value = (mock_tool_definition, mock_toolkit_definition)

        # Execute
        result = ToolMetadataService.requires_credentials(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
        )

        # Assert
        assert result is True

    @patch.object(ToolMetadataService, "_get_tool_and_toolkit_definitions")
    def test_requires_credentials_toolkit_requires(self, mock_get_defs, mock_tool_definition, mock_toolkit_definition):
        """Test when toolkit requires credentials."""
        # Setup
        mock_tool_definition.settings_config = False
        mock_toolkit_definition.settings_config = True
        mock_get_defs.return_value = (mock_tool_definition, mock_toolkit_definition)

        # Execute
        result = ToolMetadataService.requires_credentials(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
        )

        # Assert
        assert result is True

    @patch.object(ToolMetadataService, "_get_tool_and_toolkit_definitions")
    def test_requires_credentials_both_require(self, mock_get_defs, mock_tool_definition, mock_toolkit_definition):
        """Test when both tool and toolkit require credentials."""
        # Setup
        mock_tool_definition.settings_config = True
        mock_toolkit_definition.settings_config = True
        mock_get_defs.return_value = (mock_tool_definition, mock_toolkit_definition)

        # Execute
        result = ToolMetadataService.requires_credentials(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
        )

        # Assert
        assert result is True

    @patch.object(ToolMetadataService, "_get_tool_and_toolkit_definitions")
    def test_requires_credentials_neither_requires(self, mock_get_defs, mock_tool_definition, mock_toolkit_definition):
        """Test when neither tool nor toolkit requires credentials."""
        # Setup
        mock_tool_definition.settings_config = False
        mock_toolkit_definition.settings_config = False
        mock_get_defs.return_value = (mock_tool_definition, mock_toolkit_definition)

        # Execute
        result = ToolMetadataService.requires_credentials(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
        )

        # Assert
        assert result is False

    @patch.object(ToolMetadataService, "_get_tool_and_toolkit_definitions")
    def test_requires_credentials_not_found(self, mock_get_defs):
        """Test when tool/toolkit not found."""
        # Setup
        mock_get_defs.return_value = (None, None)

        # Execute
        result = ToolMetadataService.requires_credentials(
            tool_name="unknown_tool",
            toolkit_name="unknown_toolkit",
        )

        # Assert
        assert result is False

    # ============================================================================
    # Tests for get_config_class
    # ============================================================================

    @patch.object(ToolMetadataService, "_get_tool_and_toolkit_definitions")
    def test_get_config_class_from_tool(self, mock_get_defs, mock_tool_definition, mock_toolkit_definition):
        """Test getting config_class from tool definition."""
        # Setup
        mock_config = Mock()
        mock_tool_definition.config_class = mock_config
        mock_toolkit_definition.config_class = None
        mock_get_defs.return_value = (mock_tool_definition, mock_toolkit_definition)

        # Execute
        result = ToolMetadataService.get_config_class(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
        )

        # Assert
        assert result == mock_config

    @patch.object(ToolMetadataService, "_get_tool_and_toolkit_definitions")
    def test_get_config_class_from_toolkit(self, mock_get_defs, mock_tool_definition, mock_toolkit_definition):
        """Test getting config_class from toolkit definition (fallback)."""
        # Setup
        mock_config = Mock()
        mock_tool_definition.config_class = None
        mock_toolkit_definition.config_class = mock_config
        mock_get_defs.return_value = (mock_tool_definition, mock_toolkit_definition)

        # Execute
        result = ToolMetadataService.get_config_class(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
        )

        # Assert
        assert result == mock_config

    @patch.object(ToolMetadataService, "_get_tool_and_toolkit_definitions")
    def test_get_config_class_not_found(self, mock_get_defs):
        """Test when config_class is not found."""
        # Setup
        mock_get_defs.return_value = (None, None)

        # Execute
        result = ToolMetadataService.get_config_class(
            tool_name="unknown_tool",
            toolkit_name="unknown_toolkit",
        )

        # Assert
        assert result is None

    # ============================================================================
    # Tests for get_credential_type
    # ============================================================================

    def test_get_credential_type_from_model_fields(self, mock_config_class):
        """Test extracting credential_type from model_fields."""
        # Execute
        result = ToolMetadataService.get_credential_type(mock_config_class)

        # Assert
        assert result == "AWS"

    def test_get_credential_type_from_instance(self):
        """Test extracting credential_type from instance."""

        # Setup
        class TestConfig:
            credential_type = "Jira"

        # Execute
        result = ToolMetadataService.get_credential_type(TestConfig)

        # Assert
        assert result == "Jira"

    def test_get_credential_type_with_enum(self):
        """Test extracting credential_type when it's an enum."""
        from enum import Enum

        class CredType(Enum):
            AWS = "AWS"

        class TestConfig(BaseModel):
            credential_type: CredType = CredType.AWS

        # Execute
        result = ToolMetadataService.get_credential_type(TestConfig)

        # Assert
        assert result == "AWS"

    def test_get_credential_type_none_config(self):
        """Test with None config_class."""
        # Execute
        result = ToolMetadataService.get_credential_type(None)

        # Assert
        assert result is None

    def test_get_credential_type_no_field(self):
        """Test when config_class has no credential_type field."""

        # Setup
        class TestConfig(BaseModel):
            api_key: str = ""

        # Execute
        result = ToolMetadataService.get_credential_type(TestConfig)

        # Assert
        assert result is None

    # ============================================================================
    # Tests for is_internal_toolkit
    # ============================================================================

    def test_is_internal_toolkit_git(self):
        """Test Git is recognized as internal toolkit."""
        from codemie_tools.base.models import ToolSet

        # Execute
        result = ToolMetadataService.is_internal_toolkit(ToolSet.GIT)

        # Assert
        assert result is True

    def test_is_internal_toolkit_plugin(self):
        """Test Plugin is recognized as internal toolkit."""
        from codemie_tools.base.models import ToolSet

        # Execute
        result = ToolMetadataService.is_internal_toolkit(ToolSet.PLUGIN)

        # Assert
        assert result is True

    def test_is_internal_toolkit_string_git(self):
        """Test string 'Git' is recognized as internal."""
        # Execute
        result = ToolMetadataService.is_internal_toolkit("Git")

        # Assert
        assert result is True

    def test_is_internal_toolkit_external(self):
        """Test external toolkit is not recognized as internal."""
        from codemie_tools.base.models import ToolSet

        # Execute
        result = ToolMetadataService.is_internal_toolkit(ToolSet.RESEARCH)

        # Assert
        assert result is False

    # ============================================================================
    # Tests for resolve_config
    # ============================================================================

    @patch("codemie.service.tools.tool_metadata_service.SettingsService")
    @patch.object(ToolMetadataService, "get_config_class")
    @patch.object(ToolMetadataService, "requires_credentials")
    def test_resolve_config_no_credentials_required(self, mock_requires, mock_get_config_class, mock_settings_service):
        """Test resolve_config when no credentials required."""
        # Setup
        mock_requires.return_value = False

        # Execute
        result = ToolMetadataService.resolve_config(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
            user_id="test-user",
            project_name="test-project",
        )

        # Assert
        assert result is None
        mock_get_config_class.assert_not_called()
        mock_settings_service.get_config.assert_not_called()

    @patch("codemie.service.tools.tool_metadata_service.SettingsService")
    @patch.object(ToolMetadataService, "get_config_class")
    @patch.object(ToolMetadataService, "requires_credentials")
    def test_resolve_config_no_config_class(self, mock_requires, mock_get_config_class, mock_settings_service):
        """Test resolve_config when config_class not found."""
        # Setup
        mock_requires.return_value = True
        mock_get_config_class.return_value = None

        # Execute
        result = ToolMetadataService.resolve_config(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
            user_id="test-user",
            project_name="test-project",
        )

        # Assert
        assert result is None
        mock_settings_service.get_config.assert_not_called()

    @patch("codemie.service.tools.tool_metadata_service.SettingsService")
    @patch.object(ToolMetadataService, "get_config_class")
    @patch.object(ToolMetadataService, "requires_credentials")
    def test_resolve_config_success(
        self, mock_requires, mock_get_config_class, mock_settings_service, mock_config_class
    ):
        """Test successful config resolution."""
        # Setup
        mock_requires.return_value = True
        mock_get_config_class.return_value = mock_config_class
        mock_config = mock_config_class(api_key="test-key")
        mock_settings_service.get_config.return_value = mock_config

        # Execute
        result = ToolMetadataService.resolve_config(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
            user_id="test-user",
            project_name="test-project",
        )

        # Assert
        assert result == mock_config
        mock_settings_service.get_config.assert_called_once_with(
            config_class=mock_config_class,
            user_id="test-user",
            project_name="test-project",
            assistant_id=None,
            tool_config=None,
        )

    @patch("codemie.service.tools.tool_metadata_service.SettingsService")
    @patch.object(ToolMetadataService, "get_config_class")
    @patch.object(ToolMetadataService, "requires_credentials")
    def test_resolve_config_with_tool_config(
        self, mock_requires, mock_get_config_class, mock_settings_service, mock_config_class
    ):
        """Test resolve_config with tool_config provided."""
        # Setup
        mock_requires.return_value = True
        mock_get_config_class.return_value = mock_config_class
        mock_config = mock_config_class(api_key="test-key")
        mock_settings_service.get_config.return_value = mock_config

        tool_config = ToolConfig(name="test_tool", tool_creds={"api_key": "inline-key"})

        # Execute
        result = ToolMetadataService.resolve_config(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
            user_id="test-user",
            project_name="test-project",
            tool_config=tool_config,
        )

        # Assert
        assert result == mock_config
        mock_settings_service.get_config.assert_called_once_with(
            config_class=mock_config_class,
            user_id="test-user",
            project_name="test-project",
            assistant_id=None,
            tool_config=tool_config,
        )

    @patch("codemie.service.tools.tool_metadata_service.SettingsService")
    @patch.object(ToolMetadataService, "get_config_class")
    @patch.object(ToolMetadataService, "requires_credentials")
    @patch.object(ToolMetadataService, "_convert_settings_to_config")
    def test_resolve_config_with_tool_settings(
        self, mock_convert, mock_requires, mock_get_config_class, mock_settings_service, mock_config_class
    ):
        """Test resolve_config with tool_settings provided."""
        # Setup
        mock_requires.return_value = True
        mock_get_config_class.return_value = mock_config_class

        tool_settings = Mock(spec=SettingsBase)
        tool_config = ToolConfig(name="test_tool", tool_creds={"api_key": "inline-key"})
        mock_convert.return_value = tool_config

        mock_config = mock_config_class(api_key="test-key")
        mock_settings_service.get_config.return_value = mock_config

        # Execute
        result = ToolMetadataService.resolve_config(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
            user_id="test-user",
            project_name="test-project",
            tool_settings=tool_settings,
        )

        # Assert
        assert result == mock_config
        mock_convert.assert_called_once_with(tool_settings)
        mock_settings_service.get_config.assert_called_once_with(
            config_class=mock_config_class,
            user_id="test-user",
            project_name="test-project",
            assistant_id=None,
            tool_config=tool_config,
        )

    @patch("codemie.service.tools.tool_metadata_service.SettingsService")
    @patch.object(ToolMetadataService, "get_config_class")
    @patch.object(ToolMetadataService, "requires_credentials")
    def test_resolve_config_exception_handling(
        self, mock_requires, mock_get_config_class, mock_settings_service, mock_config_class
    ):
        """Test exception handling in resolve_config."""
        # Setup
        mock_requires.return_value = True
        mock_get_config_class.return_value = mock_config_class
        mock_settings_service.get_config.side_effect = Exception("Database error")

        # Execute
        result = ToolMetadataService.resolve_config(
            tool_name="test_tool",
            toolkit_name="test_toolkit",
            user_id="test-user",
            project_name="test-project",
        )

        # Assert - Should return None on exception
        assert result is None

    # ============================================================================
    # Tests for _convert_settings_to_config
    # ============================================================================

    def test_convert_settings_to_config_success(self):
        """Test successful conversion of settings to config."""
        # Setup
        tool_settings = Mock(spec=SettingsBase)
        tool_settings.name = "test_tool"
        tool_settings.credential_values = {"api_key": "test-key"}

        # Execute
        result = ToolMetadataService._convert_settings_to_config(tool_settings)

        # Assert
        assert isinstance(result, ToolConfig)
        assert result.name == "test_tool"
        assert result.tool_creds == {"api_key": "test-key"}

    def test_convert_settings_to_config_none_settings(self):
        """Test with None settings."""
        # Execute
        result = ToolMetadataService._convert_settings_to_config(None)

        # Assert
        assert result is None

    def test_convert_settings_to_config_no_credential_values_attribute(self):
        """Test when settings has no credential_values attribute."""
        # Setup
        tool_settings = Mock(spec=SettingsBase)
        delattr(tool_settings, "credential_values")

        # Execute
        result = ToolMetadataService._convert_settings_to_config(tool_settings)

        # Assert
        assert result is None

    def test_convert_settings_to_config_empty_credential_values(self):
        """Test when credential_values is None."""
        # Setup
        tool_settings = Mock(spec=SettingsBase)
        tool_settings.name = "test_tool"
        tool_settings.credential_values = None

        # Execute
        result = ToolMetadataService._convert_settings_to_config(tool_settings)

        # Assert
        assert result is None

    def test_convert_settings_to_config_no_name(self):
        """Test when settings has no name attribute."""
        # Setup
        tool_settings = Mock(spec=SettingsBase)
        tool_settings.credential_values = {"api_key": "test-key"}
        if hasattr(tool_settings, "name"):
            delattr(tool_settings, "name")

        # Execute
        result = ToolMetadataService._convert_settings_to_config(tool_settings)

        # Assert
        assert isinstance(result, ToolConfig)
        assert result.name == ""  # Default empty string
        assert result.tool_creds == {"api_key": "test-key"}

    def test_convert_settings_to_config_with_id_and_alias(self):
        """Test converting settings with id to ToolConfig with integration_id."""
        # Setup
        tool_settings = Mock(spec=SettingsBase)
        tool_settings.id = "c3684988-cbeb-4fc9-818b-6da1d9b8a50d"
        tool_settings.alias = "github"
        tool_settings.name = "git_tool"
        tool_settings.credential_values = [{"key": "token", "value": "test-token"}]

        # Execute
        result = ToolMetadataService._convert_settings_to_config(tool_settings)

        # Assert - Should prioritize id over credential_values
        assert isinstance(result, ToolConfig)
        assert result.integration_id == "c3684988-cbeb-4fc9-818b-6da1d9b8a50d"
        assert result.tool_creds is None
        assert result.name == "git_tool"

    def test_convert_settings_to_config_with_id_no_credential_values(self):
        """Test converting settings with only id (no credential_values)."""
        # Setup
        tool_settings = Mock(spec=SettingsBase)
        tool_settings.id = "test-settings-id-123"
        tool_settings.alias = "my-integration"
        tool_settings.name = "test_tool"
        tool_settings.credential_values = None

        # Execute
        result = ToolMetadataService._convert_settings_to_config(tool_settings)

        # Assert - Should use id as integration_id
        assert isinstance(result, ToolConfig)
        assert result.integration_id == "test-settings-id-123"
        assert result.tool_creds is None

    def test_convert_settings_to_config_priority_id_over_values(self):
        """Test that id takes priority over credential_values when both exist."""
        # Setup
        tool_settings = Mock(spec=SettingsBase)
        tool_settings.id = "priority-test-id"
        tool_settings.alias = "priority-alias"
        tool_settings.credential_values = {"api_key": "should-not-use-this"}
        tool_settings.name = "priority_test"

        # Execute
        result = ToolMetadataService._convert_settings_to_config(tool_settings)

        # Assert - id should take priority
        assert result.integration_id == "priority-test-id"
        assert result.tool_creds is None  # Should not use credential_values

    def test_convert_settings_to_config_no_id_with_credential_values(self):
        """Test converting settings without id but with credential_values."""
        # Setup
        tool_settings = Mock(spec=SettingsBase)
        tool_settings.id = None
        tool_settings.credential_values = {"api_key": "inline-key", "api_secret": "inline-secret"}
        tool_settings.name = "inline_tool"

        # Execute
        result = ToolMetadataService._convert_settings_to_config(tool_settings)

        # Assert - Should use credential_values as tool_creds
        assert isinstance(result, ToolConfig)
        assert result.integration_id is None
        assert result.tool_creds == {"api_key": "inline-key", "api_secret": "inline-secret"}
        assert result.name == "inline_tool"
