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

"""Tests for validation utility functions."""

from unittest.mock import Mock, patch


from codemie.rest_api.models.assistant import Assistant, ToolDetails, ToolKitDetails
from codemie.rest_api.models.assistant_generator import RecommendationAction, RecommendationSeverity
from codemie.workflows.assistant_generator.nodes.validation.utils import (
    create_context_recommendation,
    find_existing_toolkit_for_tool,
    find_toolkit_for_tool,
    format_configured_context_for_prompt,
    format_context_for_prompt,
    format_existing_tools_for_prompt,
    format_metadata_validation_prompt,
    format_rag_toolkits_for_prompt,
    format_system_prompt_validation_prompt,
    get_assistant_tool_names,
    get_configured_context_names,
    get_toolkit_name,
    get_validated_context_info,
)
from codemie_tools.base.models import Tool, ToolKit


class TestGetAssistantToolNames:
    """Test get_assistant_tool_names utility."""

    def test_get_tool_names_from_assistant_with_tools(self, sample_assistant):
        """Test extracting tool names from assistant with toolkits."""
        # Act
        tool_names = get_assistant_tool_names(sample_assistant)

        # Assert
        assert isinstance(tool_names, set)
        assert len(tool_names) == 2
        assert "test_tool_1" in tool_names
        assert "test_tool_2" in tool_names

    def test_get_tool_names_from_assistant_without_toolkits(self):
        """Test extracting tool names from assistant without toolkits."""
        # Arrange
        assistant = Assistant(
            id="test-id",
            name="Empty Assistant",
            description="No tools",
            system_prompt="Test prompt",
            toolkits=[],
        )

        # Act
        tool_names = get_assistant_tool_names(assistant)

        # Assert
        assert isinstance(tool_names, set)
        assert len(tool_names) == 0

    def test_get_tool_names_from_assistant_with_none_toolkits(self):
        """Test extracting tool names when toolkits attribute is None."""
        # Arrange
        assistant = Assistant(
            id="test-id",
            name="No Toolkits",
            description="Toolkits is None",
            system_prompt="Test prompt",
        )
        assistant.toolkits = None

        # Act
        tool_names = get_assistant_tool_names(assistant)

        # Assert
        assert isinstance(tool_names, set)
        assert len(tool_names) == 0

    def test_get_tool_names_from_multiple_toolkits(self):
        """Test extracting tool names from assistant with multiple toolkits."""
        # Arrange
        assistant = Assistant(
            id="test-id",
            name="Multi-toolkit Assistant",
            description="Has multiple toolkits",
            system_prompt="Test prompt",
            toolkits=[
                ToolKitDetails(
                    toolkit="toolkit_1",
                    tools=[ToolDetails(name="tool_a", label="Tool A"), ToolDetails(name="tool_b", label="Tool B")],
                ),
                ToolKitDetails(
                    toolkit="toolkit_2",
                    tools=[ToolDetails(name="tool_c", label="Tool C"), ToolDetails(name="tool_d", label="Tool D")],
                ),
            ],
        )

        # Act
        tool_names = get_assistant_tool_names(assistant)

        # Assert
        assert len(tool_names) == 4
        assert {"tool_a", "tool_b", "tool_c", "tool_d"} == tool_names


class TestGetConfiguredContextNames:
    """Test get_configured_context_names utility."""

    def test_get_context_names_from_assistant(self, sample_assistant):
        """Test extracting context names from assistant."""
        # Act
        context_names = get_configured_context_names(sample_assistant)

        # Assert
        assert isinstance(context_names, list)
        assert len(context_names) == 2
        assert "test_repo_1" in context_names
        assert "test_repo_2" in context_names

    def test_get_context_names_from_assistant_with_no_context(self):
        """Test extracting context names when context is empty."""
        # Arrange
        assistant = Assistant(
            id="test-id",
            name="No Context",
            description="No context configured",
            system_prompt="Test prompt",
            context=[],
        )

        # Act
        context_names = get_configured_context_names(assistant)

        # Assert
        assert isinstance(context_names, list)
        assert len(context_names) == 0

    def test_get_context_names_from_assistant_with_none_context(self):
        """Test extracting context names when context is empty."""
        # Arrange
        assistant = Assistant(
            id="test-id",
            name="Empty Context",
            description="Context is empty",
            system_prompt="Test prompt",
            context=[],  # Empty list instead of None
        )

        # Act
        context_names = get_configured_context_names(assistant)

        # Assert
        assert isinstance(context_names, list)
        assert len(context_names) == 0


class TestFormatMetadataValidationPrompt:
    """Test format_metadata_validation_prompt utility."""

    def test_format_metadata_prompt_success(self, sample_state):
        """Test formatting metadata validation prompt variables."""
        # Act
        result = format_metadata_validation_prompt(sample_state)

        # Assert
        assert isinstance(result, dict)
        assert result["name"] == "Python Code Assistant"  # From conftest fixture
        assert result["description"] == "An assistant that helps with Python coding tasks"
        assert "helpful Python programming assistant" in result["system_prompt"]

    def test_format_metadata_prompt_with_categories(self, sample_state):
        """Test formatting metadata prompt includes all metadata fields."""
        # Act
        result = format_metadata_validation_prompt(sample_state)

        # Assert
        assert "name" in result
        assert "description" in result
        assert "system_prompt" in result
        assert result["name"] == "Python Code Assistant"

    def test_format_metadata_prompt_with_minimal_data(self, sample_user):
        """Test formatting metadata prompt with minimal assistant data."""
        # Arrange
        from codemie.workflows.assistant_generator.models.validation_state import AssistantValidationState

        assistant = Assistant(
            id="test-id",
            name="Minimal Assistant",
            description="Test description",
            system_prompt="Test prompt",
            categories=[],
        )
        state = AssistantValidationState(
            assistant=assistant, user=sample_user, request_id="test", current_phase="validate_metadata"
        )

        # Act
        result = format_metadata_validation_prompt(state)

        # Assert
        assert result["name"] == "Minimal Assistant"
        assert result["description"] == "Test description"
        assert result["system_prompt"] == "Test prompt"


class TestFormatSystemPromptValidationPrompt:
    """Test format_system_prompt_validation_prompt utility."""

    def test_format_system_prompt_variables(self, sample_state):
        """Test formatting system prompt validation variables."""
        # Act
        result = format_system_prompt_validation_prompt(sample_state)

        # Assert
        assert isinstance(result, dict)
        assert result["name"] == "Python Code Assistant"  # From conftest fixture
        assert result["description"] == "An assistant that helps with Python coding tasks"
        assert "programming, python" in result["categories"]
        assert "helpful Python programming assistant" in result["system_prompt"]
        assert len(result["conversation_starters"]) == 2

    def test_format_system_prompt_with_no_conversation_starters(self, sample_user):
        """Test formatting when conversation_starters is empty."""
        # Arrange
        from codemie.workflows.assistant_generator.models.validation_state import AssistantValidationState

        assistant = Assistant(
            id="test-id",
            name="Test",
            description="Test",
            system_prompt="Test prompt",
            conversation_starters=[],  # Empty list instead of None
        )
        state = AssistantValidationState(
            assistant=assistant, user=sample_user, request_id="test", current_phase="validate_system_prompt"
        )

        # Act
        result = format_system_prompt_validation_prompt(state)

        # Assert
        assert result["conversation_starters"] == []


class TestFormatRagToolkitsForPrompt:
    """Test format_rag_toolkits_for_prompt utility."""

    def test_format_rag_toolkits_with_data(self, sample_toolkits):
        """Test formatting RAG toolkits for prompt."""
        # Act
        result = format_rag_toolkits_for_prompt(sample_toolkits)

        # Assert
        assert isinstance(result, str)
        assert "python_toolkit" in result
        assert "python_execute" in result
        assert "Execute Python code" in result
        assert "database_toolkit" in result
        assert "sql_query" in result

    def test_format_rag_toolkits_empty_list(self):
        """Test formatting when RAG returns empty list."""
        # Act
        result = format_rag_toolkits_for_prompt([])

        # Assert
        assert result == "No RAG candidates found"

    def test_format_rag_toolkits_handles_missing_label(self):
        """Test formatting when tools don't have label attribute."""
        # Arrange
        # Create a tool with no label
        tool_without_label = Tool(name="test_tool")  # Tool with only name, no label
        toolkits = [
            ToolKit(
                toolkit="test_toolkit",
                tools=[tool_without_label],
            )
        ]

        # Act
        result = format_rag_toolkits_for_prompt(toolkits)

        # Assert
        assert "test_tool" in result
        # Tool may have auto-generated label or show as None


class TestFormatExistingToolsForPrompt:
    """Test format_existing_tools_for_prompt utility."""

    def test_format_existing_tools_with_tools(self, sample_assistant):
        """Test formatting existing tools for prompt."""
        # Act
        result = format_existing_tools_for_prompt(sample_assistant)

        # Assert
        assert isinstance(result, str)
        assert "test_tool_1" in result
        assert "test_tool_2" in result
        assert ", " in result  # Comma-separated

    def test_format_existing_tools_without_tools(self):
        """Test formatting when assistant has no tools."""
        # Arrange
        assistant = Assistant(
            id="test-id",
            name="No Tools",
            description="Test",
            system_prompt="Test prompt",
            toolkits=[],
        )

        # Act
        result = format_existing_tools_for_prompt(assistant)

        # Assert
        assert result == "None"


class TestFormatConfiguredContextForPrompt:
    """Test format_configured_context_for_prompt utility."""

    def test_format_configured_context_with_context(self, sample_assistant):
        """Test formatting configured context for prompt."""
        # Act
        result = format_configured_context_for_prompt(sample_assistant)

        # Assert
        assert isinstance(result, str)
        assert "test_repo_1" in result
        assert "test_repo_2" in result
        assert "Configured datasources:" in result

    def test_format_configured_context_without_context(self):
        """Test formatting when no context configured."""
        # Arrange
        assistant = Assistant(
            id="test-id",
            name="No Context",
            description="Test",
            system_prompt="Test prompt",
            context=[],  # Empty list instead of None
        )

        # Act
        result = format_configured_context_for_prompt(assistant)

        # Assert
        assert result == "No context configured"


class TestGetValidatedContextInfo:
    """Test get_validated_context_info utility."""

    @patch('codemie.workflows.assistant_generator.nodes.validation.utils.IndexInfo')
    def test_get_validated_context_info_success(self, mock_index_info, sample_assistant, sample_user):
        """Test validating and retrieving context info."""
        # Arrange
        mock_index = Mock()
        mock_index.repo_name = "test_repo_1"
        mock_index.index_type = "code"
        mock_index.description = "Test repository"
        mock_index_info.filter_for_user_repo_names.return_value = [mock_index]

        configured_context = ["test_repo_1"]

        # Act
        context_info, validated_names = get_validated_context_info(sample_assistant, sample_user, configured_context)

        # Assert
        assert len(context_info) == 1
        assert context_info[0]["repo_name"] == "test_repo_1"
        assert context_info[0]["index_type"] == "code"
        assert context_info[0]["description"] == "Test repository"
        assert validated_names == {"test_repo_1"}

    @patch('codemie.workflows.assistant_generator.nodes.validation.utils.IndexInfo')
    def test_get_validated_context_info_empty_context(self, mock_index_info, sample_assistant, sample_user):
        """Test validation with empty configured context."""
        # Act
        context_info, validated_names = get_validated_context_info(sample_assistant, sample_user, [])

        # Assert
        assert context_info == []
        assert validated_names == set()
        mock_index_info.filter_for_user_repo_names.assert_not_called()

    @patch('codemie.workflows.assistant_generator.nodes.validation.utils.IndexInfo')
    def test_get_validated_context_info_filters_invalid_context(self, mock_index_info, sample_assistant, sample_user):
        """Test that invalid context is filtered out."""
        # Arrange - only one of two contexts exists in DB
        mock_index = Mock()
        mock_index.repo_name = "test_repo_1"
        mock_index.index_type = "code"
        mock_index.description = "Test repository"
        mock_index_info.filter_for_user_repo_names.return_value = [mock_index]

        configured_context = ["test_repo_1", "invalid_repo"]

        # Act
        context_info, validated_names = get_validated_context_info(sample_assistant, sample_user, configured_context)

        # Assert - only valid context returned
        assert len(context_info) == 1
        assert "test_repo_1" in validated_names
        assert "invalid_repo" not in validated_names


class TestFormatContextForPrompt:
    """Test format_context_for_prompt utility."""

    def test_format_context_with_descriptions(self):
        """Test formatting validated context with descriptions."""
        # Arrange
        validated_context = [
            {"repo_name": "repo1", "index_type": "code", "description": "Python code repository"},
            {"repo_name": "repo2", "index_type": "confluence", "description": "Documentation"},
        ]

        # Act
        result = format_context_for_prompt(validated_context)

        # Assert
        assert "repo1" in result
        assert "code" in result
        assert "Python code repository" in result
        assert "repo2" in result
        assert "confluence" in result
        assert "Documentation" in result

    def test_format_context_without_descriptions(self):
        """Test formatting context when descriptions are None."""
        # Arrange
        validated_context = [{"repo_name": "repo1", "index_type": "code", "description": None}]

        # Act
        result = format_context_for_prompt(validated_context)

        # Assert
        assert "No description" in result

    def test_format_context_empty_list(self):
        """Test formatting empty context list."""
        # Act
        result = format_context_for_prompt([])

        # Assert
        assert result == "No datasources available"


class TestGetToolkitName:
    """Test get_toolkit_name utility."""

    def test_get_toolkit_name_from_enum(self):
        """Test extracting toolkit name from enum with value attribute."""
        # Arrange
        mock_toolkit = Mock()
        mock_toolkit.value = "python_toolkit"

        # Act
        result = get_toolkit_name(mock_toolkit)

        # Assert
        assert result == "python_toolkit"

    def test_get_toolkit_name_from_string(self):
        """Test extracting toolkit name from string."""
        # Act
        result = get_toolkit_name("database_toolkit")

        # Assert
        assert result == "database_toolkit"


class TestFindToolkitForTool:
    """Test find_toolkit_for_tool utility."""

    def test_find_toolkit_for_existing_tool(self, sample_toolkits):
        """Test finding toolkit that contains a specific tool."""
        # Act
        result = find_toolkit_for_tool("python_execute", sample_toolkits)

        # Assert
        assert result == "python_toolkit"

    def test_find_toolkit_for_tool_not_found(self, sample_toolkits):
        """Test finding toolkit when tool doesn't exist."""
        # Act
        result = find_toolkit_for_tool("nonexistent_tool", sample_toolkits)

        # Assert
        assert result is None

    def test_find_toolkit_for_tool_in_second_toolkit(self, sample_toolkits):
        """Test finding tool in second toolkit."""
        # Act
        result = find_toolkit_for_tool("sql_query", sample_toolkits)

        # Assert
        assert result == "database_toolkit"


class TestFindExistingToolkitForTool:
    """Test find_existing_toolkit_for_tool utility."""

    def test_find_existing_toolkit_success(self, sample_assistant):
        """Test finding toolkit in existing assistant toolkits."""
        # Act
        result = find_existing_toolkit_for_tool("test_tool_1", sample_assistant)

        # Assert
        assert result == "test_toolkit"

    def test_find_existing_toolkit_not_found(self, sample_assistant):
        """Test finding toolkit when tool doesn't exist."""
        # Act
        result = find_existing_toolkit_for_tool("nonexistent_tool", sample_assistant)

        # Assert
        assert result is None

    def test_find_existing_toolkit_no_toolkits(self):
        """Test finding toolkit when assistant has no toolkits."""
        # Arrange
        assistant = Assistant(
            id="test-id",
            name="No Toolkits",
            description="Test",
            system_prompt="Test prompt",
            toolkits=[],  # Empty list instead of None
        )

        # Act
        result = find_existing_toolkit_for_tool("test_tool", assistant)

        # Assert
        assert result is None


class TestCreateContextRecommendation:
    """Test create_context_recommendation utility."""

    def test_create_context_recommendation_with_description(self):
        """Test creating context recommendation with description."""
        # Arrange
        context_map = {
            "repo1": {
                "repo_name": "repo1",
                "index_type": "code",
                "description": "Python code repository",
            }
        }
        reason_template = "Context {name} should be added{desc}"

        # Act
        result = create_context_recommendation(
            "repo1", RecommendationAction.Change, context_map, reason_template, RecommendationSeverity.CRITICAL
        )

        # Assert
        assert result.name == "repo1"
        assert result.action == RecommendationAction.Change
        assert result.severity == RecommendationSeverity.CRITICAL
        assert "code" in result.reason
        assert "Python code repository" in result.reason

    def test_create_context_recommendation_without_description(self):
        """Test creating context recommendation when context has no description."""
        # Arrange
        context_map = {"repo1": {"repo_name": "repo1", "index_type": "code", "description": None}}
        reason_template = "Context {name} should be deleted{desc}"

        # Act
        result = create_context_recommendation(
            "repo1", RecommendationAction.Delete, context_map, reason_template, RecommendationSeverity.OPTIONAL
        )

        # Assert
        assert result.name == "repo1"
        assert result.action == RecommendationAction.Delete
        assert result.severity == RecommendationSeverity.OPTIONAL
        assert "No description" in result.reason

    def test_create_context_recommendation_not_in_map(self):
        """Test creating context recommendation when context not in map."""
        # Arrange
        reason_template = "Context {name} not found{desc}"

        # Act
        result = create_context_recommendation(
            "unknown_repo", RecommendationAction.Delete, {}, reason_template, RecommendationSeverity.CRITICAL
        )

        # Assert
        assert result.name == "unknown_repo"
        assert result.action == RecommendationAction.Delete
        assert result.severity == RecommendationSeverity.CRITICAL
        # desc should be empty when not in map
        assert result.reason == "Context unknown_repo not found"
