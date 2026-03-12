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

"""Unit tests for AssistantIntegrationValidator service."""

from unittest.mock import Mock, patch

import pytest
from codemie_tools.base.models import Tool

from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantRequest,
    IntegrationValidationResult,
    MissingIntegration,
    MissingIntegrationsByCredentialType,
    SettingsConfigLevel,
    ToolKitDetails,
)
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_integration_validator import AssistantIntegrationValidator
from codemie.service.assistant.credential_validator import ValidationResult


class TestAssistantIntegrationValidator:
    """Test suite for the AssistantIntegrationValidator class."""

    @pytest.fixture
    def mock_user(self):
        """Fixture for mocking User."""
        user = Mock(spec=User)
        user.id = "test-user-id"
        user.name = "Test User"
        return user

    @pytest.fixture
    def mock_assistant_request_minimal(self):
        """Fixture for minimal assistant request without toolkits."""
        return AssistantRequest(
            name="Test Assistant",
            description="Test description",
            project="test-project",
            system_prompt="Test prompt",
            llm_model_type="gpt-4",
            toolkits=[],
            assistant_ids=[],
        )

    @pytest.fixture
    def mock_assistant_request_with_toolkits(self):
        """Fixture for assistant request with toolkits."""
        # Use Mock instead of ToolKitDetails to avoid validation issues
        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "AWS"
        toolkit.label = "AWS Toolkit"
        toolkit.is_external = False

        tool1 = Mock(spec=Tool)
        tool1.name = "s3_upload"
        tool1.settings = None
        tool1.settings_config = False  # Tool uses toolkit-level settings
        tool1.label = "S3 Upload"

        tool2 = Mock(spec=Tool)
        tool2.name = "lambda_invoke"
        tool2.settings = None
        tool2.settings_config = False  # Tool uses toolkit-level settings
        tool2.label = "Lambda Invoke"

        toolkit.tools = [tool1, tool2]

        return AssistantRequest(
            name="Test Assistant",
            description="Test description",
            project="test-project",
            system_prompt="Test prompt",
            llm_model_type="gpt-4",
            toolkits=[toolkit],
            assistant_ids=[],
        )

    @pytest.fixture
    def mock_assistant_request_with_sub_assistants(self):
        """Fixture for assistant request with sub-assistants."""
        return AssistantRequest(
            name="Orchestrator Assistant",
            description="Orchestrator description",
            project="test-project",
            system_prompt="Test prompt",
            llm_model_type="gpt-4",
            toolkits=[],
            assistant_ids=["sub-assistant-1", "sub-assistant-2"],
        )

    @pytest.fixture
    def mock_sub_assistant(self):
        """Fixture for mock sub-assistant."""
        assistant = Mock(spec=Assistant)
        assistant.id = "sub-assistant-1"
        assistant.name = "Sub Assistant 1"
        assistant.icon_url = "https://example.com/icon.png"
        assistant.is_global = False  # Not a global assistant

        # Mock created_by for ownership checking
        created_by = Mock()
        created_by.id = "different-user-id"  # Different from mock_user
        assistant.created_by = created_by

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "Jira"
        toolkit.label = "Jira Toolkit"
        toolkit.is_external = False

        tool = Mock(spec=Tool)
        tool.name = "jira_search"
        tool.settings = None
        tool.settings_config = False  # Tool uses toolkit-level settings
        tool.label = "Jira Search"

        toolkit.tools = [tool]
        assistant.toolkits = [toolkit]
        return assistant

    # ============================================================================
    # Tests for validate_integrations - Main Orchestration
    # ============================================================================

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_integrations_no_toolkits(self, mock_validator, mock_user, mock_assistant_request_minimal):
        """Test validation with no toolkits configured."""
        # Execute
        result = AssistantIntegrationValidator.validate_integrations(
            assistant_request=mock_assistant_request_minimal,
            user=mock_user,
            project_name="test-project",
        )

        # Assert
        assert isinstance(result, IntegrationValidationResult)
        assert result.has_missing_integrations is False
        assert result.missing_by_credential_type == []
        assert result.sub_assistants_missing == []
        assert result.message is None

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_integrations_all_valid(self, mock_validator, mock_user, mock_assistant_request_with_toolkits):
        """Test validation when all credentials are valid."""
        # Setup
        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=True)

        # Execute
        result = AssistantIntegrationValidator.validate_integrations(
            assistant_request=mock_assistant_request_with_toolkits,
            user=mock_user,
            project_name="test-project",
        )

        # Assert
        assert result.has_missing_integrations is False
        assert result.missing_by_credential_type == []
        assert result.message is None
        assert mock_validator.validate_tool_credentials.call_count == 2  # 2 tools

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_integrations_missing_credentials(
        self, mock_validator, mock_user, mock_assistant_request_with_toolkits
    ):
        """Test validation when credentials are missing."""
        # Setup - First tool missing, second valid
        mock_validator.validate_tool_credentials.side_effect = [
            ValidationResult(is_valid=False, credential_type="AWS"),
            ValidationResult(is_valid=True),
        ]

        # Execute
        result = AssistantIntegrationValidator.validate_integrations(
            assistant_request=mock_assistant_request_with_toolkits,
            user=mock_user,
            project_name="test-project",
        )

        # Assert
        assert result.has_missing_integrations is True
        assert len(result.missing_by_credential_type) == 1
        assert result.missing_by_credential_type[0].credential_type == "AWS"
        assert len(result.missing_by_credential_type[0].missing_tools) == 1
        assert result.missing_by_credential_type[0].missing_tools[0].tool == "s3_upload"
        assert (
            result.missing_by_credential_type[0].missing_tools[0].settings_config_level == SettingsConfigLevel.TOOLKIT
        )
        assert "1 tool(s) which require integrations" in result.message

    @patch("codemie.service.assistant.assistant_integration_validator.Assistant")
    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_integrations_with_sub_assistants(
        self,
        mock_validator,
        mock_assistant_class,
        mock_user,
        mock_assistant_request_with_sub_assistants,
        mock_sub_assistant,
    ):
        """Test validation includes sub-assistant toolkits."""
        # Setup
        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=False, credential_type="Jira")
        mock_assistant_class.get_by_ids.return_value = [mock_sub_assistant]

        # Execute
        result = AssistantIntegrationValidator.validate_integrations(
            assistant_request=mock_assistant_request_with_sub_assistants,
            user=mock_user,
            project_name="test-project",
        )

        # Assert
        assert result.has_missing_integrations is True
        assert len(result.sub_assistants_missing) == 1
        assert result.sub_assistants_missing[0].credential_type == "Jira"
        assert result.sub_assistants_missing[0].assistant_name == "Sub Assistant 1"
        assert result.sub_assistants_missing[0].assistant_id == "sub-assistant-1"
        assert result.sub_assistants_missing[0].icon_url == "https://example.com/icon.png"
        assert result.sub_assistants_missing[0].missing_tools[0].settings_config_level == SettingsConfigLevel.TOOLKIT

    # ============================================================================
    # Tests for _validate_toolkits
    # ============================================================================

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_empty_list(self, mock_validator, mock_user):
        """Test validating empty toolkit list."""
        # Execute
        result = AssistantIntegrationValidator._validate_toolkits(
            toolkits=[],
            user=mock_user,
            project_name="test-project",
        )

        # Assert
        assert result == []
        mock_validator.validate_tool_credentials.assert_not_called()

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_external_toolkit_skipped(self, mock_validator, mock_user):
        """Test that external toolkits (provider tools) are skipped."""
        # Setup
        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "ProviderToolkit"
        toolkit.label = "Provider"
        toolkit.is_external = True  # External toolkit

        tool = Mock(spec=Tool)
        tool.name = "provider_tool"
        tool.settings_config = False
        toolkit.tools = [tool]

        # Execute
        result = AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="test-project",
        )

        # Assert
        assert result == []
        mock_validator.validate_tool_credentials.assert_not_called()

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_multiple_missing(self, mock_validator, mock_user):
        """Test validation with multiple missing credentials."""
        # Setup
        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "AWS"
        toolkit.label = "AWS Toolkit"
        toolkit.is_external = False

        tool1 = Mock(spec=Tool)
        tool1.name = "s3_upload"
        tool1.settings = None
        tool1.settings_config = False  # Tool uses toolkit-level settings
        tool1.label = "S3 Upload"

        tool2 = Mock(spec=Tool)
        tool2.name = "lambda_invoke"
        tool2.settings = None
        tool2.settings_config = False  # Tool uses toolkit-level settings
        tool2.label = "Lambda Invoke"

        tool3 = Mock(spec=Tool)
        tool3.name = "ec2_list"
        tool3.settings = None
        tool3.settings_config = False  # Tool uses toolkit-level settings
        tool3.label = "EC2 List"

        toolkit.tools = [tool1, tool2, tool3]

        # All tools missing credentials
        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=False, credential_type="AWS")

        # Execute
        result = AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="test-project",
        )

        # Assert
        assert len(result) == 3
        assert all(isinstance(item, MissingIntegration) for item in result)
        assert all(item.toolkit == "AWS" for item in result)
        assert all(item.credential_type == "AWS" for item in result)
        assert all(item.settings_config_level == SettingsConfigLevel.TOOLKIT for item in result)

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_with_toolkit_settings_only(self, mock_validator, mock_user):
        """Test that toolkit settings are passed when tool has no settings."""
        # Setup
        toolkit_settings = Mock()
        tool = Mock(spec=Tool)
        tool.name = "test_tool"
        tool.settings = None  # Tool has no settings
        tool.settings_config = False

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "TestToolkit"
        toolkit.tools = [tool]
        toolkit.is_external = False
        toolkit.settings = toolkit_settings

        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=True)

        # Execute
        AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="test-project",
        )

        # Assert - Should use toolkit settings
        mock_validator.validate_tool_credentials.assert_called_once_with(
            toolkit_name="TestToolkit",
            tool_name="test_tool",
            user=mock_user,
            project_name="test-project",
            tool_settings=toolkit_settings,
            assistant_id=None,
        )

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_with_tool_settings_priority(self, mock_validator, mock_user):
        """Test that tool settings take priority over toolkit settings."""
        # Setup
        toolkit_settings = Mock()
        toolkit_settings.id = "toolkit-settings-id"
        toolkit_settings.alias = "toolkit-alias"

        tool_settings = Mock()
        tool_settings.id = "tool-settings-id"
        tool_settings.alias = "tool-alias"

        tool = Mock(spec=Tool)
        tool.name = "github"
        tool.settings = tool_settings  # Tool has its own settings
        tool.settings_config = True  # Tool has its own settings

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "VCS"
        toolkit.tools = [tool]
        toolkit.is_external = False
        toolkit.settings = toolkit_settings  # Toolkit also has settings

        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=True)

        # Execute
        AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="demo",
        )

        # Assert - Should use tool settings (priority), not toolkit settings
        mock_validator.validate_tool_credentials.assert_called_once_with(
            toolkit_name="VCS",
            tool_name="github",
            user=mock_user,
            project_name="demo",
            tool_settings=tool_settings,  # Tool settings, not toolkit settings
            assistant_id=None,
        )

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_mixed_tool_settings(self, mock_validator, mock_user):
        """Test mixed scenario: some tools have settings, others don't."""
        # Setup
        toolkit_settings = Mock()
        toolkit_settings.id = "toolkit-id"

        tool1_settings = Mock()
        tool1_settings.id = "tool1-id"

        tool1 = Mock(spec=Tool)
        tool1.name = "github"
        tool1.settings = tool1_settings  # Has own settings
        tool1.settings_config = True  # Tool has its own settings

        tool2 = Mock(spec=Tool)
        tool2.name = "gitlab"
        tool2.settings = None  # No settings, should use toolkit settings
        tool2.settings_config = False

        tool3 = Mock(spec=Tool)
        tool3.name = "azure_devops_git"
        tool3.settings = None  # No settings, should use toolkit settings
        tool3.settings_config = False

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "VCS"
        toolkit.tools = [tool1, tool2, tool3]
        toolkit.is_external = False
        toolkit.settings = toolkit_settings

        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=True)

        # Execute
        AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="demo",
        )

        # Assert - 3 calls total
        assert mock_validator.validate_tool_credentials.call_count == 3

        # Check first tool uses its own settings
        call1 = mock_validator.validate_tool_credentials.call_args_list[0]
        assert call1[1]["tool_name"] == "github"
        assert call1[1]["tool_settings"] == tool1_settings

        # Check second tool uses toolkit settings
        call2 = mock_validator.validate_tool_credentials.call_args_list[1]
        assert call2[1]["tool_name"] == "gitlab"
        assert call2[1]["tool_settings"] == toolkit_settings

        # Check third tool uses toolkit settings
        call3 = mock_validator.validate_tool_credentials.call_args_list[2]
        assert call3[1]["tool_name"] == "azure_devops_git"
        assert call3[1]["tool_settings"] == toolkit_settings

    # ============================================================================
    # Tests for _validate_sub_assistants
    # ============================================================================

    @patch("codemie.service.assistant.assistant_integration_validator.Assistant")
    def test_validate_sub_assistants_empty_list(self, mock_assistant_class, mock_user):
        """Test validating empty sub-assistant list."""
        # Execute
        result = AssistantIntegrationValidator._validate_sub_assistants(
            assistant_ids=[],
            user=mock_user,
            project_name="test-project",
        )

        # Assert
        assert result == []
        mock_assistant_class.get_by_ids.assert_not_called()

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    @patch("codemie.service.assistant.assistant_integration_validator.Assistant")
    def test_validate_sub_assistants_not_found(self, mock_assistant_class, mock_validator, mock_user):
        """Test handling of sub-assistant not found."""
        # Setup
        mock_assistant_class.get_by_ids.return_value = []  # Not found

        # Execute
        result = AssistantIntegrationValidator._validate_sub_assistants(
            assistant_ids=["missing-assistant"],
            user=mock_user,
            project_name="test-project",
        )

        # Assert
        assert result == []
        mock_validator.validate_tool_credentials.assert_not_called()

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    @patch("codemie.service.assistant.assistant_integration_validator.Assistant")
    def test_validate_sub_assistants_with_missing_credentials(
        self, mock_assistant_class, mock_validator, mock_user, mock_sub_assistant
    ):
        """Test sub-assistant validation with missing credentials."""
        # Setup
        mock_assistant_class.get_by_ids.return_value = [mock_sub_assistant]
        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=False, credential_type="Jira")

        # Execute
        result = AssistantIntegrationValidator._validate_sub_assistants(
            assistant_ids=["sub-assistant-1"],
            user=mock_user,
            project_name="test-project",
        )

        # Assert
        assert len(result) == 1
        missing_tool, assistant_id, assistant_name, icon_url = result[0]
        assert isinstance(missing_tool, MissingIntegration)
        assert assistant_id == "sub-assistant-1"
        assert assistant_name == "Sub Assistant 1"
        assert icon_url == "https://example.com/icon.png"

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    @patch("codemie.service.assistant.assistant_integration_validator.Assistant")
    def test_validate_sub_assistants_multiple_sub_assistants(self, mock_assistant_class, mock_validator, mock_user):
        """Test validation with multiple sub-assistants."""
        # Setup
        sub1 = Mock(spec=Assistant)
        sub1.id = "sub-1"
        sub1.name = "Sub 1"
        sub1.icon_url = "icon1.png"

        toolkit1 = Mock(spec=ToolKitDetails)
        toolkit1.toolkit = "AWS"
        toolkit1.is_external = False
        tool1 = Mock(spec=Tool)
        tool1.name = "s3_upload"
        tool1.settings = None
        tool1.settings_config = False
        tool1.label = "S3 Upload"
        toolkit1.tools = [tool1]
        sub1.toolkits = [toolkit1]

        sub2 = Mock(spec=Assistant)
        sub2.id = "sub-2"
        sub2.name = "Sub 2"
        sub2.icon_url = "icon2.png"

        toolkit2 = Mock(spec=ToolKitDetails)
        toolkit2.toolkit = "Jira"
        toolkit2.is_external = False
        tool2 = Mock(spec=Tool)
        tool2.name = "jira_search"
        tool2.settings = None
        tool2.settings_config = False
        tool2.label = "Jira Search"
        toolkit2.tools = [tool2]
        sub2.toolkits = [toolkit2]

        def get_by_ids_side_effect(user, ids, parent_assistant):
            if "sub-1" in ids:
                return [sub1]
            elif "sub-2" in ids:
                return [sub2]
            return []

        mock_assistant_class.get_by_ids.side_effect = get_by_ids_side_effect
        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=False, credential_type="AWS")

        # Execute
        result = AssistantIntegrationValidator._validate_sub_assistants(
            assistant_ids=["sub-1", "sub-2"],
            user=mock_user,
            project_name="test-project",
        )

        # Assert - Should have 2 missing tools (one from each sub-assistant)
        assert len(result) == 2

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    @patch("codemie.service.assistant.assistant_integration_validator.Assistant")
    def test_validate_sub_assistants_exception_handling(self, mock_assistant_class, mock_validator, mock_user):
        """Test exception handling during sub-assistant validation."""
        # Setup
        mock_assistant_class.get_by_ids.side_effect = Exception("Database error")

        # Execute - Should not raise exception
        result = AssistantIntegrationValidator._validate_sub_assistants(
            assistant_ids=["sub-assistant-1"],
            user=mock_user,
            project_name="test-project",
        )

        # Assert - Should return empty list on error
        assert result == []

    # ============================================================================
    # Tests for _group_by_credential_type
    # ============================================================================

    def test_group_by_credential_type_empty_list(self):
        """Test grouping empty list of missing tools."""
        # Execute
        result = AssistantIntegrationValidator._group_by_credential_type([])

        # Assert
        assert result == []

    def test_group_by_credential_type_single_credential_type(self):
        """Test grouping tools with single credential type."""
        # Setup
        missing_tools = [
            MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3 Upload", credential_type="AWS"),
            MissingIntegration(toolkit="AWS", tool="lambda_invoke", label="Lambda", credential_type="AWS"),
        ]

        # Execute
        result = AssistantIntegrationValidator._group_by_credential_type(missing_tools)

        # Assert
        assert len(result) == 1
        assert result[0].credential_type == "AWS"
        assert len(result[0].missing_tools) == 2

    def test_group_by_credential_type_multiple_credential_types(self):
        """Test grouping tools with multiple credential types."""
        # Setup
        missing_tools = [
            MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS"),
            MissingIntegration(toolkit="Jira", tool="jira_search", label="Jira", credential_type="Jira"),
            MissingIntegration(toolkit="AWS", tool="lambda_invoke", label="Lambda", credential_type="AWS"),
            MissingIntegration(
                toolkit="Confluence", tool="confluence_search", label="Confluence", credential_type="Confluence"
            ),
        ]

        # Execute
        result = AssistantIntegrationValidator._group_by_credential_type(missing_tools)

        # Assert
        assert len(result) == 3
        credential_types = {item.credential_type for item in result}
        assert credential_types == {"AWS", "Jira", "Confluence"}

        # Check AWS has 2 tools
        aws_group = next(item for item in result if item.credential_type == "AWS")
        assert len(aws_group.missing_tools) == 2

    def test_group_by_credential_type_deduplication(self):
        """Test that duplicate tools are deduplicated."""
        # Setup - Same tool appears twice
        missing_tools = [
            MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS"),
            MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS"),
        ]

        # Execute
        result = AssistantIntegrationValidator._group_by_credential_type(missing_tools)

        # Assert - Should only have 1 tool after deduplication
        assert len(result) == 1
        assert len(result[0].missing_tools) == 1

    def test_group_by_credential_type_sorted_output(self):
        """Test that results are sorted by credential type."""
        # Setup
        missing_tools = [
            MissingIntegration(toolkit="Jira", tool="jira_search", label="Jira", credential_type="Jira"),
            MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS"),
            MissingIntegration(
                toolkit="Confluence", tool="confluence_search", label="Confluence", credential_type="Confluence"
            ),
        ]

        # Execute
        result = AssistantIntegrationValidator._group_by_credential_type(missing_tools)

        # Assert - Should be sorted alphabetically
        credential_types = [item.credential_type for item in result]
        assert credential_types == sorted(credential_types)

    # ============================================================================
    # Tests for _group_by_credential_type_with_context
    # ============================================================================

    def test_group_by_credential_type_with_context_empty_list(self):
        """Test grouping empty list with context."""
        # Execute
        result = AssistantIntegrationValidator._group_by_credential_type_with_context([])

        # Assert
        assert result == []

    def test_group_by_credential_type_with_context_single_assistant(self):
        """Test grouping tools from single sub-assistant."""
        # Setup
        missing_tool = MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS")
        missing_tools_with_context = [
            (missing_tool, "sub-1", "Sub Assistant 1", "icon1.png"),
        ]

        # Execute
        result = AssistantIntegrationValidator._group_by_credential_type_with_context(missing_tools_with_context)

        # Assert
        assert len(result) == 1
        assert result[0].credential_type == "AWS"
        assert result[0].assistant_id == "sub-1"
        assert result[0].assistant_name == "Sub Assistant 1"
        assert result[0].icon_url == "icon1.png"
        assert len(result[0].missing_tools) == 1

    def test_group_by_credential_type_with_context_multiple_assistants(self):
        """Test grouping tools from multiple sub-assistants."""
        # Setup
        missing_tools_with_context = [
            (
                MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS"),
                "sub-1",
                "Sub 1",
                "icon1.png",
            ),
            (
                MissingIntegration(toolkit="Jira", tool="jira_search", label="Jira", credential_type="Jira"),
                "sub-2",
                "Sub 2",
                "icon2.png",
            ),
        ]

        # Execute
        result = AssistantIntegrationValidator._group_by_credential_type_with_context(missing_tools_with_context)

        # Assert
        assert len(result) == 2
        assert result[0].assistant_name == "Sub 1"
        assert result[1].assistant_name == "Sub 2"

    def test_group_by_credential_type_with_context_deduplication(self):
        """Test deduplication within same credential type and assistant."""
        # Setup - Same tool from same assistant twice
        missing_tools_with_context = [
            (
                MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS"),
                "sub-1",
                "Sub 1",
                "icon1.png",
            ),
            (
                MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS"),
                "sub-1",
                "Sub 1",
                "icon1.png",
            ),
        ]

        # Execute
        result = AssistantIntegrationValidator._group_by_credential_type_with_context(missing_tools_with_context)

        # Assert - Should only have 1 tool
        assert len(result) == 1
        assert len(result[0].missing_tools) == 1

    # ============================================================================
    # Tests for _build_validation_result
    # ============================================================================

    def test_build_validation_result_no_missing(self):
        """Test building result when no integrations are missing."""
        # Execute
        result = AssistantIntegrationValidator._build_validation_result(
            main_grouped=[],
            sub_grouped=[],
        )

        # Assert
        assert result.has_missing_integrations is False
        assert result.missing_by_credential_type == []
        assert result.sub_assistants_missing == []
        assert result.message is None

    def test_build_validation_result_with_main_missing(self):
        """Test building result with missing main assistant integrations."""
        # Setup
        main_grouped = [
            MissingIntegrationsByCredentialType(
                credential_type="AWS",
                missing_tools=[MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS")],
            )
        ]

        # Execute
        result = AssistantIntegrationValidator._build_validation_result(
            main_grouped=main_grouped,
            sub_grouped=[],
        )

        # Assert
        assert result.has_missing_integrations is True
        assert len(result.missing_by_credential_type) == 1
        assert "1 tool(s) which require integrations" in result.message

    def test_build_validation_result_with_sub_missing(self):
        """Test building result with missing sub-assistant integrations."""
        # Setup
        sub_grouped = [
            MissingIntegrationsByCredentialType(
                credential_type="Jira",
                missing_tools=[
                    MissingIntegration(toolkit="Jira", tool="jira_search", label="Jira", credential_type="Jira")
                ],
                assistant_id="sub-1",
                assistant_name="Sub 1",
            )
        ]

        # Execute
        result = AssistantIntegrationValidator._build_validation_result(
            main_grouped=[],
            sub_grouped=sub_grouped,
        )

        # Assert
        assert result.has_missing_integrations is True
        assert len(result.sub_assistants_missing) == 1
        assert "1 tool(s) which require integrations" in result.message

    def test_build_validation_result_with_both_missing(self):
        """Test building result with both main and sub-assistant missing integrations."""
        # Setup
        main_grouped = [
            MissingIntegrationsByCredentialType(
                credential_type="AWS",
                missing_tools=[MissingIntegration(toolkit="AWS", tool="s3_upload", label="S3", credential_type="AWS")],
            )
        ]
        sub_grouped = [
            MissingIntegrationsByCredentialType(
                credential_type="Jira",
                missing_tools=[
                    MissingIntegration(toolkit="Jira", tool="jira_search", label="Jira", credential_type="Jira"),
                    MissingIntegration(toolkit="Jira", tool="jira_create", label="Jira Create", credential_type="Jira"),
                ],
                assistant_id="sub-1",
                assistant_name="Sub 1",
            )
        ]

        # Execute
        result = AssistantIntegrationValidator._build_validation_result(
            main_grouped=main_grouped,
            sub_grouped=sub_grouped,
        )

        # Assert
        assert result.has_missing_integrations is True
        assert "3 tool(s) which require integrations" in result.message  # 1 main + 2 sub

    # ============================================================================
    # Tests for toolkit-level settings (Bug Fix)
    # ============================================================================

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_passes_toolkit_settings_not_tool_settings(self, mock_validator, mock_user):
        """Test that toolkit settings are passed, not tool settings."""
        # Setup
        toolkit_settings = Mock()
        toolkit_settings.id = "toolkit-settings-id"
        toolkit_settings.alias = "my-toolkit-integration"

        tool1 = Mock(spec=Tool)
        tool1.name = "tool1"

        tool2 = Mock(spec=Tool)
        tool2.name = "tool2"

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "Git"
        toolkit.tools = [tool1, tool2]
        toolkit.is_external = False
        toolkit.settings = toolkit_settings

        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=True)

        # Execute
        AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="demo",
        )

        # Assert - Should be called twice (once per tool) with same toolkit_settings
        assert mock_validator.validate_tool_credentials.call_count == 2

        # Check first call
        call1 = mock_validator.validate_tool_credentials.call_args_list[0]
        assert call1[1]["toolkit_name"] == "Git"
        assert call1[1]["tool_name"] == "tool1"
        assert call1[1]["tool_settings"] == toolkit_settings

        # Check second call
        call2 = mock_validator.validate_tool_credentials.call_args_list[1]
        assert call2[1]["toolkit_name"] == "Git"
        assert call2[1]["tool_name"] == "tool2"
        assert call2[1]["tool_settings"] == toolkit_settings

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_toolkit_without_settings(self, mock_validator, mock_user):
        """Test toolkit validation when toolkit has no settings."""
        # Setup
        tool = Mock(spec=Tool)
        tool.name = "test_tool"
        tool.settings_config = False

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "ResearchToolkit"
        toolkit.tools = [tool]
        toolkit.is_external = False
        toolkit.settings = None  # No settings at toolkit level

        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=True)

        # Execute
        AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="test-project",
        )

        # Assert - tool_settings should be None
        mock_validator.validate_tool_credentials.assert_called_once_with(
            toolkit_name="ResearchToolkit",
            tool_name="test_tool",
            user=mock_user,
            project_name="test-project",
            tool_settings=None,
            assistant_id=None,
        )

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_with_git_toolkit_and_settings(self, mock_validator, mock_user):
        """Test Git toolkit validation with settings containing id and alias."""
        # Setup
        toolkit_settings = Mock()
        toolkit_settings.id = "c3684988-cbeb-4fc9-818b-6da1d9b8a50d"
        toolkit_settings.alias = "github"
        toolkit_settings.credential_values = [
            {"key": "url", "value": "https://github.com"},
            {"key": "token", "value": "***"},
        ]

        tool = Mock(spec=Tool)
        tool.name = "create_branch"
        tool.label = "Create Branch"
        tool.settings_config = False

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "Git"
        toolkit.tools = [tool]
        toolkit.is_external = False
        toolkit.settings = toolkit_settings

        # Mock validation returns valid
        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=True, credential_type="Git")

        # Execute
        result = AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="demo",
        )

        # Assert - No missing tools
        assert len(result) == 0

        # Verify settings were passed
        mock_validator.validate_tool_credentials.assert_called_once_with(
            toolkit_name="Git",
            tool_name="create_branch",
            user=mock_user,
            project_name="demo",
            tool_settings=toolkit_settings,
            assistant_id=None,
        )

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_with_git_toolkit_missing_credentials(self, mock_validator, mock_user):
        """Test Git toolkit validation when credentials are missing."""
        # Setup
        toolkit_settings = Mock()
        toolkit_settings.id = "invalid-id-not-found"
        toolkit_settings.alias = "missing-integration"

        tool = Mock(spec=Tool)
        tool.name = "create_pull_request"
        tool.label = "Create Pull Request"
        tool.settings_config = False  # Tool uses toolkit-level settings

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "Git"
        toolkit.tools = [tool]
        toolkit.is_external = False
        toolkit.settings = toolkit_settings

        # Mock validation returns invalid
        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=False, credential_type="Git")

        # Execute
        result = AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="demo",
        )

        # Assert - Should have one missing tool
        assert len(result) == 1
        assert result[0].toolkit == "Git"
        assert result[0].tool == "create_pull_request"
        assert result[0].label == "Create Pull Request"
        assert result[0].credential_type == "Git"
        assert result[0].settings_config_level == SettingsConfigLevel.TOOLKIT

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_multiple_tools_same_settings(self, mock_validator, mock_user):
        """Test that all tools in a toolkit share the same toolkit settings."""
        # Setup
        toolkit_settings = Mock()
        toolkit_settings.id = "shared-settings-id"
        toolkit_settings.alias = "shared-integration"

        tools = []
        for i in range(5):
            tool = Mock(spec=Tool)
            tool.name = f"tool_{i}"
            tool.label = f"Tool {i}"
            tool.settings_config = False
            tools.append(tool)

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "Git"
        toolkit.tools = tools
        toolkit.is_external = False
        toolkit.settings = toolkit_settings

        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=True)

        # Execute
        AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="demo",
        )

        # Assert - All 5 tools should be validated with same toolkit_settings
        assert mock_validator.validate_tool_credentials.call_count == 5

        for i, call in enumerate(mock_validator.validate_tool_credentials.call_args_list):
            assert call[1]["tool_name"] == f"tool_{i}"
            assert call[1]["tool_settings"] == toolkit_settings
            assert call[1]["tool_settings"].id == "shared-settings-id"

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_tool_level_settings_missing(self, mock_validator, mock_user):
        """Test that settings_config_level is set to TOOL when tool metadata has settings_config=True."""
        # Setup - Tool has settings_config=True in its metadata
        tool = Mock(spec=Tool)
        tool.name = "sql"
        tool.label = "SQL"
        tool.settings_config = True  # Tool requires its own settings (from metadata)

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "Data Management"
        toolkit.tools = [tool]
        toolkit.is_external = False
        toolkit.settings = None  # No toolkit-level settings

        # Mock validation returns invalid (missing credentials)
        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=False, credential_type="SQL")

        # Execute
        result = AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="demo",
        )

        # Assert - Should indicate TOOL level
        assert len(result) == 1
        assert result[0].settings_config_level == SettingsConfigLevel.TOOL
        assert result[0].toolkit == "Data Management"
        assert result[0].tool == "sql"
        assert result[0].credential_type == "SQL"

    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_toolkits_mixed_settings_levels(self, mock_validator, mock_user):
        """Test mixed scenario: one tool has settings_config=True (TOOL level), another has False (TOOLKIT level)."""
        # Setup
        toolkit_settings = Mock()
        toolkit_settings.id = "toolkit-id"

        tool1 = Mock(spec=Tool)
        tool1.name = "sql"
        tool1.label = "SQL"
        tool1.settings_config = True  # Tool requires its own settings (from metadata) → TOOL level

        tool2 = Mock(spec=Tool)
        tool2.name = "search_elastic_index"
        tool2.label = "Search Elastic Index"
        tool2.settings_config = False  # Tool uses toolkit settings → TOOLKIT level

        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "Data Management"
        toolkit.tools = [tool1, tool2]
        toolkit.is_external = False
        toolkit.settings = toolkit_settings

        # Both tools missing credentials (using different credential types for clarity)
        def validation_side_effect(toolkit_name, tool_name, user, project_name, tool_settings, assistant_id=None):
            if tool_name == "sql":
                return ValidationResult(is_valid=False, credential_type="SQL")
            else:
                return ValidationResult(is_valid=False, credential_type="Elastic")

        mock_validator.validate_tool_credentials.side_effect = validation_side_effect

        # Execute
        result = AssistantIntegrationValidator._validate_toolkits(
            toolkits=[toolkit],
            user=mock_user,
            project_name="demo",
        )

        # Assert - Should have different levels
        assert len(result) == 2

        sql_missing = next(item for item in result if item.tool == "sql")
        assert sql_missing.settings_config_level == SettingsConfigLevel.TOOL
        assert sql_missing.credential_type == "SQL"

        elastic_missing = next(item for item in result if item.tool == "search_elastic_index")
        assert elastic_missing.settings_config_level == SettingsConfigLevel.TOOLKIT
        assert elastic_missing.credential_type == "Elastic"

    @patch("codemie.service.assistant.assistant_integration_validator.Assistant")
    @patch("codemie.service.assistant.assistant_integration_validator.CredentialValidator")
    def test_validate_sub_assistants_global_flag(self, mock_validator, mock_assistant_class, mock_user):
        """Test that sub-assistants with missing credentials are properly validated."""
        # Setup - Create two sub-assistants
        global_assistant = Mock(spec=Assistant)
        global_assistant.id = "global-assistant-1"
        global_assistant.name = "Global Assistant"
        global_assistant.icon_url = "https://example.com/global.png"
        global_assistant.is_global = True
        # Global assistant created by current user
        global_created_by = Mock()
        global_created_by.id = mock_user.id  # Same as test user
        global_assistant.created_by = global_created_by

        non_global_assistant = Mock(spec=Assistant)
        non_global_assistant.id = "non-global-assistant-1"
        non_global_assistant.name = "Non-Global Assistant"
        non_global_assistant.icon_url = "https://example.com/non-global.png"
        non_global_assistant.is_global = False
        # Non-global assistant created by different user
        non_global_created_by = Mock()
        non_global_created_by.id = "different-user-id"  # Different from test user
        non_global_assistant.created_by = non_global_created_by

        # Both have missing Jira tools
        toolkit = Mock(spec=ToolKitDetails)
        toolkit.toolkit = "Jira"
        toolkit.is_external = False

        tool = Mock(spec=Tool)
        tool.name = "jira_search"
        tool.settings_config = False

        toolkit.tools = [tool]

        global_assistant.toolkits = [toolkit]
        non_global_assistant.toolkits = [toolkit]

        # Mock Assistant.get_by_ids to return assistants based on ID
        def get_by_ids_side_effect(user, ids, parent_assistant=None):
            if ids[0] == "global-assistant-1":
                return [global_assistant]
            elif ids[0] == "non-global-assistant-1":
                return [non_global_assistant]
            return []

        mock_assistant_class.get_by_ids.side_effect = get_by_ids_side_effect

        # Mock validation to return missing credentials
        mock_validator.validate_tool_credentials.return_value = ValidationResult(is_valid=False, credential_type="Jira")

        # Create assistant request with both sub-assistants
        request = AssistantRequest(
            name="Parent Assistant",
            description="Test parent",
            project="test-project",
            system_prompt="Test prompt",
            llm_model_type="gpt-4",
            toolkits=[],
            assistant_ids=["global-assistant-1", "non-global-assistant-1"],
        )

        # Execute
        result = AssistantIntegrationValidator.validate_integrations(
            assistant_request=request,
            user=mock_user,
            project_name="test-project",
        )

        # Assert - Should have two sub-assistant groups
        assert result.has_missing_integrations is True
        assert len(result.sub_assistants_missing) == 2

        # Find the global assistant result
        global_result = next(
            item for item in result.sub_assistants_missing if item.assistant_id == "global-assistant-1"
        )
        assert global_result.assistant_name == "Global Assistant"

        # Find the non-global assistant result
        non_global_result = next(
            item for item in result.sub_assistants_missing if item.assistant_id == "non-global-assistant-1"
        )
        assert non_global_result.assistant_name == "Non-Global Assistant"
