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

"""Tests for ValidateToolsNode - tools validation with RAG and LLM decision-making."""

from unittest.mock import Mock, patch

import pytest

from codemie.rest_api.models.assistant import ToolDetails, ToolKitDetails
from codemie.workflows.assistant_generator.models.validation_models import ToolsDecisionResult
from codemie.workflows.assistant_generator.nodes.validation.validate_tools_node import ValidateToolsNode


@pytest.fixture
def validate_tools_node(mock_llm):
    """Create ValidateToolsNode instance with mocked LLM."""
    node = ValidateToolsNode(llm_model="gpt-4o-mini", request_id="test-request-123")
    node._llm = mock_llm
    return node


class TestValidateToolsNodeDecisionMaking:
    """Test LLM-based tools decision making."""

    def test_make_tools_decision_with_llm_success(
        self, validate_tools_node, sample_assistant, sample_toolkits, sample_state
    ):
        """Test successful tools decision making using LLM."""
        # Arrange
        expected_decision = ToolsDecisionResult(
            tools_to_include=["python_execute", "python_format"],
            tools_to_exclude=["analyze_code", "find_bugs"],
            reasoning="Python execution and formatting tools are essential for a Python code assistant.",
        )
        validate_tools_node.invoke_llm_with_retry = Mock(return_value=expected_decision)

        # Act
        result = validate_tools_node._make_tools_decision_with_llm(sample_assistant, sample_toolkits, sample_state)

        # Assert
        assert isinstance(result, ToolsDecisionResult)
        assert result.tools_to_include == ["python_execute", "python_format"]
        assert result.tools_to_exclude == ["analyze_code", "find_bugs"]
        assert result.reasoning
        validate_tools_node.invoke_llm_with_retry.assert_called_once()

    def test_make_tools_decision_includes_all_context(
        self, validate_tools_node, sample_assistant, sample_toolkits, sample_state
    ):
        """Test that decision prompt includes all necessary context."""
        # Arrange
        mock_decision = ToolsDecisionResult(
            tools_to_include=["python_execute"],
            tools_to_exclude=[],
            reasoning="Test reasoning",
        )
        validate_tools_node.invoke_llm_with_retry = Mock(return_value=mock_decision)

        # Act
        validate_tools_node._make_tools_decision_with_llm(sample_assistant, sample_toolkits, sample_state)
        call_args = validate_tools_node.invoke_llm_with_retry.call_args

        # Assert - verify prompt contains all necessary information
        prompt = call_args[0][0]
        assert sample_assistant.name in prompt
        assert sample_assistant.description in prompt
        assert sample_assistant.system_prompt in prompt
        # Verify RAG toolkits are formatted in prompt
        assert "python_toolkit" in prompt or "python_execute" in prompt


class TestValidateToolsNodeFullWorkflow:
    """Test complete tools validation workflow."""

    def test_validate_tools_skips_validation_for_orchestrator_assistant(self, validate_tools_node, sample_state):
        """Test that tools validation is skipped for orchestrator assistants with sub-assistants."""
        # Arrange - mark state as having sub-assistants (orchestrator pattern)
        sample_state["has_sub_assistants"] = True

        # Act
        result_state = validate_tools_node(sample_state)

        # Assert
        assert result_state["tools_result"] is not None
        assert result_state["tools_result"].is_valid is True
        assert (
            result_state["tools_result"].rag_query
            == "N/A - Orchestrator assistant detected, skipping tools validation."
        )
        assert "orchestrator assistant" in result_state["tools_result"].reasoning.lower()
        assert "delegates tasks to sub-assistants" in result_state["tools_result"].reasoning
        assert "sub-assistants handle all tool-based capabilities" in result_state["tools_result"].reasoning

    @patch('codemie.workflows.assistant_generator.nodes.validation.validate_tools_node.ToolkitLookupService')
    def test_validate_tools_complete_workflow_no_changes_needed(
        self, mock_toolkit_service, validate_tools_node, sample_state, sample_toolkits
    ):
        """Test complete validation workflow when tools are already correct."""
        # Arrange - assistant already has correct tools (convert to ToolKitDetails)
        sample_state["assistant"].toolkits = [
            ToolKitDetails(
                toolkit=tk.toolkit,
                tools=[ToolDetails(name=tool.name, label=tool.label) for tool in tk.tools],
            )
            for tk in sample_toolkits
        ]

        # Mock tools decision
        validate_tools_node.invoke_llm_with_retry = Mock(
            return_value=ToolsDecisionResult(
                tools_to_include=["python_execute", "python_format", "sql_query", "db_migrate"],
                tools_to_exclude=[],
                reasoning="Current tools are appropriate",
            )
        )

        # Mock RAG lookup
        mock_toolkit_service.get_tools_by_query.return_value = sample_toolkits

        # Act
        result_state = validate_tools_node(sample_state)

        # Assert - node returns dict with only updated fields
        assert result_state["tools_result"] is not None
        assert result_state["tools_result"].is_valid is True
        assert len(result_state["tools_result"].recommended_additions) == 0
        assert len(result_state["tools_result"].recommended_deletions) == 0

    @patch('codemie.workflows.assistant_generator.nodes.validation.validate_tools_node.ToolkitLookupService')
    def test_validate_tools_recommends_additions(
        self, mock_toolkit_service, validate_tools_node, sample_state, sample_toolkits
    ):
        """Test validation workflow recommends adding missing tools."""
        # Arrange - assistant has no tools
        sample_state["assistant"].toolkits = []

        # Mock tools decision
        validate_tools_node.invoke_llm_with_retry = Mock(
            return_value=ToolsDecisionResult(
                tools_to_include=["python_execute", "python_format"],
                tools_to_exclude=[],
                reasoning="These tools are essential for Python coding assistance",
            )
        )

        # Mock RAG lookup
        mock_toolkit_service.get_tools_by_query.return_value = sample_toolkits

        # Act
        result_state = validate_tools_node(sample_state)

        # Assert
        assert result_state["tools_result"] is not None
        assert result_state["tools_result"].is_valid is False
        assert len(result_state["tools_result"].recommended_additions) == 2
        assert "python_execute" in result_state["tools_result"].recommended_additions
        assert "python_format" in result_state["tools_result"].recommended_additions
        assert len(result_state["tools_result"].recommended_deletions) == 0

    @patch('codemie.workflows.assistant_generator.nodes.validation.validate_tools_node.ToolkitLookupService')
    def test_validate_tools_recommends_deletions(
        self, mock_toolkit_service, validate_tools_node, sample_state, sample_toolkits
    ):
        """Test validation workflow recommends removing unnecessary tools."""
        # Arrange - assistant has tools that should be removed
        sample_state["assistant"].toolkits = [
            ToolKitDetails(
                toolkit="database_toolkit",
                tools=[
                    ToolDetails(name="sql_query", label="Execute SQL query"),
                    ToolDetails(name="db_migrate", label="Run database migration"),
                ],
            )
        ]

        # Mock tools decision
        validate_tools_node.invoke_llm_with_retry = Mock(
            return_value=ToolsDecisionResult(
                tools_to_include=["python_execute"],
                tools_to_exclude=["sql_query", "db_migrate"],
                reasoning="Database tools are not relevant for Python code assistance",
            )
        )

        # Mock RAG lookup
        mock_toolkit_service.get_tools_by_query.return_value = sample_toolkits

        # Act
        result_state = validate_tools_node(sample_state)

        # Assert
        assert result_state["tools_result"] is not None
        assert result_state["tools_result"].is_valid is False
        assert len(result_state["tools_result"].recommended_deletions) == 2
        assert "sql_query" in result_state["tools_result"].recommended_deletions
        assert "db_migrate" in result_state["tools_result"].recommended_deletions

    @patch('codemie.workflows.assistant_generator.nodes.validation.validate_tools_node.ToolkitLookupService')
    def test_validate_tools_handles_mixed_changes(
        self, mock_toolkit_service, validate_tools_node, sample_state, sample_toolkits
    ):
        """Test validation handles both additions and deletions."""
        # Arrange - assistant has some correct tools and some incorrect ones
        sample_state["assistant"].toolkits = [
            ToolKitDetails(
                toolkit="mixed_toolkit",
                tools=[
                    ToolDetails(name="python_execute", label="Execute Python"),
                    ToolDetails(name="sql_query", label="SQL Query"),
                ],
            )
        ]

        # Mock tools decision
        validate_tools_node.invoke_llm_with_retry = Mock(
            return_value=ToolsDecisionResult(
                tools_to_include=["python_execute", "python_format"],
                tools_to_exclude=["sql_query"],
                reasoning="Keep Python tools, remove SQL tools, add Python formatting",
            )
        )

        # Mock RAG lookup
        mock_toolkit_service.get_tools_by_query.return_value = sample_toolkits

        # Act
        result_state = validate_tools_node(sample_state)

        # Assert
        assert result_state["tools_result"] is not None
        assert result_state["tools_result"].is_valid is False
        assert "python_format" in result_state["tools_result"].recommended_additions
        assert "sql_query" in result_state["tools_result"].recommended_deletions
        assert "python_execute" in result_state["tools_result"].tools_to_keep

    @patch('codemie.workflows.assistant_generator.nodes.validation.validate_tools_node.ToolkitLookupService')
    def test_validate_tools_stores_rag_query_and_reasoning(
        self, mock_toolkit_service, validate_tools_node, sample_state, sample_toolkits
    ):
        """Test that RAG query and reasoning are stored in result."""
        # Arrange
        expected_reasoning = "These tools match the assistant's capabilities"

        validate_tools_node.invoke_llm_with_retry = Mock(
            return_value=ToolsDecisionResult(
                tools_to_include=["python_execute"],
                tools_to_exclude=[],
                reasoning=expected_reasoning,
            )
        )
        mock_toolkit_service.get_tools_by_query.return_value = sample_toolkits

        # Act
        result_state = validate_tools_node(sample_state)

        # Assert
        assert result_state["tools_result"].reasoning == expected_reasoning

    @patch('codemie.workflows.assistant_generator.nodes.validation.validate_tools_node.ToolkitLookupService')
    def test_validate_tools_handles_empty_rag_results(self, mock_toolkit_service, validate_tools_node, sample_state):
        """Test validation handles empty RAG results gracefully."""
        # Arrange - RAG returns no toolkits
        validate_tools_node.invoke_llm_with_retry = Mock(
            return_value=ToolsDecisionResult(
                tools_to_include=[],
                tools_to_exclude=[],
                reasoning="No relevant tools found",
            )
        )
        mock_toolkit_service.get_tools_by_query.return_value = []

        # Act
        result_state = validate_tools_node(sample_state)

        # Assert
        assert result_state["tools_result"] is not None
        assert len(result_state["tools_result"].recommended_toolkits) == 0

    @patch('codemie.workflows.assistant_generator.nodes.validation.validate_tools_node.ToolkitLookupService')
    def test_validate_tools_respects_rag_limit(
        self, mock_toolkit_service, validate_tools_node, sample_state, sample_toolkits
    ):
        """Test that RAG query respects the limit parameter."""
        # Arrange
        validate_tools_node.invoke_llm_with_retry = Mock(
            return_value=ToolsDecisionResult(
                tools_to_include=["python_execute"],
                tools_to_exclude=[],
                reasoning="Test reasoning",
            )
        )
        mock_toolkit_service.get_tools_by_query.return_value = sample_toolkits

        # Act
        validate_tools_node(sample_state)

        # Assert - verify RAG was called with limit=10
        mock_toolkit_service.get_tools_by_query.assert_called_once()
        call_kwargs = mock_toolkit_service.get_tools_by_query.call_args[1]
        assert call_kwargs['limit'] == 10


class TestValidateToolsNodeErrorHandling:
    """Test error handling in tools validation."""

    @patch('codemie.workflows.assistant_generator.nodes.validation.validate_tools_node.ToolkitLookupService')
    def test_validate_tools_handles_llm_failure_with_retry(
        self, mock_toolkit_service, validate_tools_node, sample_state
    ):
        """Test that LLM failures are handled with retry mechanism."""
        # Arrange
        mock_toolkit_service.get_tools_by_query.return_value = []

        # Mock invoke_llm_with_retry to raise exception (simulating exhausted retries)
        validate_tools_node.invoke_llm_with_retry = Mock(side_effect=Exception("LLM service unavailable"))

        # Act & Assert
        with pytest.raises(Exception, match="LLM service unavailable"):
            validate_tools_node(sample_state)

    @patch('codemie.workflows.assistant_generator.nodes.validation.validate_tools_node.ToolkitLookupService')
    def test_validate_tools_handles_toolkit_lookup_failure(
        self, mock_toolkit_service, validate_tools_node, sample_state
    ):
        """Test handling of toolkit lookup service failures."""
        # Arrange
        # Mock ToolkitLookupService to raise exception
        mock_toolkit_service.get_tools_by_query.side_effect = Exception("RAG service unavailable")

        # Act & Assert
        with pytest.raises(Exception, match="RAG service unavailable"):
            validate_tools_node(sample_state)
