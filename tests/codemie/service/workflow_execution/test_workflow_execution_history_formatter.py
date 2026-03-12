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

"""
Unit tests for workflow execution history formatter.

Tests the functionality of formatting workflow execution history for chat context,
including execution ID extraction, filtering, output retrieval, and markdown formatting.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch


from codemie.core.workflow_models import (
    WorkflowExecution,
    WorkflowExecutionState,
    WorkflowExecutionStatusEnum,
)
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.service.workflow_execution.workflow_execution_history_formatter import (
    _get_execution_ids_from_conversation,
    _get_completed_executions,
    _get_workflow_output,
    _format_executions_as_markdown,
    format_execution_history,
)


class TestGetExecutionIdsFromConversation:
    """Tests for _get_execution_ids_from_conversation function."""

    def test_no_conversation_found(self):
        """Test when conversation does not exist."""
        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.Conversation.find_by_id',
            return_value=None,
        ):
            result = _get_execution_ids_from_conversation("conv-123", "workflow-456")

            assert result == []

    def test_conversation_with_no_history(self):
        """Test when conversation exists but has no history."""
        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.history = None

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.Conversation.find_by_id',
            return_value=mock_conversation,
        ):
            result = _get_execution_ids_from_conversation("conv-123", "workflow-456")

            assert result == []

    def test_conversation_with_empty_history(self):
        """Test when conversation has empty history list."""
        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.history = []

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.Conversation.find_by_id',
            return_value=mock_conversation,
        ):
            result = _get_execution_ids_from_conversation("conv-123", "workflow-456")

            assert result == []

    def test_conversation_with_no_execution_ids(self):
        """Test when conversation has messages but no execution IDs."""
        mock_message1 = MagicMock(spec=GeneratedMessage)
        mock_message1.execution_id = None

        mock_message2 = MagicMock(spec=GeneratedMessage)
        mock_message2.execution_id = None

        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.history = [mock_message1, mock_message2]

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.Conversation.find_by_id',
            return_value=mock_conversation,
        ):
            result = _get_execution_ids_from_conversation("conv-123", "workflow-456")

            assert result == []

    def test_conversation_with_execution_ids(self):
        """Test when conversation has messages with execution IDs."""
        mock_message1 = MagicMock(spec=GeneratedMessage)
        mock_message1.execution_id = "exec-1"

        mock_message2 = MagicMock(spec=GeneratedMessage)
        mock_message2.execution_id = None

        mock_message3 = MagicMock(spec=GeneratedMessage)
        mock_message3.execution_id = "exec-2"

        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.history = [mock_message1, mock_message2, mock_message3]

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.Conversation.find_by_id',
            return_value=mock_conversation,
        ):
            result = _get_execution_ids_from_conversation("conv-123", "workflow-456")

            assert result == ["exec-1", "exec-2"]


class TestGetCompletedExecutions:
    """Tests for _get_completed_executions function."""

    def test_empty_execution_ids(self):
        """Test when no execution IDs are provided."""
        result = _get_completed_executions([], "workflow-123")

        assert result == []

    def test_no_executions_for_workflow(self):
        """Test when workflow has no executions."""
        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.WorkflowExecution.get_by_workflow_id',
            return_value=[],
        ):
            result = _get_completed_executions(["exec-1", "exec-2"], "workflow-123")

            assert result == []

    def test_filters_by_execution_ids(self):
        """Test that only executions in the provided IDs are returned."""
        mock_exec1 = MagicMock(spec=WorkflowExecution)
        mock_exec1.execution_id = "exec-1"
        mock_exec1.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED
        mock_exec1.date = datetime(2024, 1, 1, 10, 0, 0)

        mock_exec2 = MagicMock(spec=WorkflowExecution)
        mock_exec2.execution_id = "exec-2"
        mock_exec2.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED
        mock_exec2.date = datetime(2024, 1, 1, 11, 0, 0)

        mock_exec3 = MagicMock(spec=WorkflowExecution)
        mock_exec3.execution_id = "exec-3"
        mock_exec3.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED
        mock_exec3.date = datetime(2024, 1, 1, 12, 0, 0)

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.WorkflowExecution.get_by_workflow_id',
            return_value=[mock_exec1, mock_exec2, mock_exec3],
        ):
            result = _get_completed_executions(["exec-1", "exec-3"], "workflow-123")

            assert len(result) == 2
            assert result[0].execution_id == "exec-1"
            assert result[1].execution_id == "exec-3"

    def test_filters_out_in_progress_executions(self):
        """Test that in-progress executions are excluded."""
        mock_exec1 = MagicMock(spec=WorkflowExecution)
        mock_exec1.execution_id = "exec-1"
        mock_exec1.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED
        mock_exec1.date = datetime(2024, 1, 1, 10, 0, 0)

        mock_exec2 = MagicMock(spec=WorkflowExecution)
        mock_exec2.execution_id = "exec-2"
        mock_exec2.overall_status = WorkflowExecutionStatusEnum.IN_PROGRESS
        mock_exec2.date = datetime(2024, 1, 1, 11, 0, 0)

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.WorkflowExecution.get_by_workflow_id',
            return_value=[mock_exec1, mock_exec2],
        ):
            result = _get_completed_executions(["exec-1", "exec-2"], "workflow-123")

            assert len(result) == 1
            assert result[0].execution_id == "exec-1"

    def test_filters_out_not_started_executions(self):
        """Test that not-started executions are excluded."""
        mock_exec1 = MagicMock(spec=WorkflowExecution)
        mock_exec1.execution_id = "exec-1"
        mock_exec1.overall_status = WorkflowExecutionStatusEnum.FAILED
        mock_exec1.date = datetime(2024, 1, 1, 10, 0, 0)

        mock_exec2 = MagicMock(spec=WorkflowExecution)
        mock_exec2.execution_id = "exec-2"
        mock_exec2.overall_status = WorkflowExecutionStatusEnum.NOT_STARTED
        mock_exec2.date = datetime(2024, 1, 1, 11, 0, 0)

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.WorkflowExecution.get_by_workflow_id',
            return_value=[mock_exec1, mock_exec2],
        ):
            result = _get_completed_executions(["exec-1", "exec-2"], "workflow-123")

            assert len(result) == 1
            assert result[0].execution_id == "exec-1"

    def test_includes_failed_executions(self):
        """Test that failed executions are included (per requirements)."""
        mock_exec = MagicMock(spec=WorkflowExecution)
        mock_exec.execution_id = "exec-1"
        mock_exec.overall_status = WorkflowExecutionStatusEnum.FAILED
        mock_exec.date = datetime(2024, 1, 1, 10, 0, 0)

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.WorkflowExecution.get_by_workflow_id',
            return_value=[mock_exec],
        ):
            result = _get_completed_executions(["exec-1"], "workflow-123")

            assert len(result) == 1
            assert result[0].overall_status == WorkflowExecutionStatusEnum.FAILED

    def test_includes_aborted_executions(self):
        """Test that aborted executions are included (per requirements)."""
        mock_exec = MagicMock(spec=WorkflowExecution)
        mock_exec.execution_id = "exec-1"
        mock_exec.overall_status = WorkflowExecutionStatusEnum.ABORTED
        mock_exec.date = datetime(2024, 1, 1, 10, 0, 0)

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.WorkflowExecution.get_by_workflow_id',
            return_value=[mock_exec],
        ):
            result = _get_completed_executions(["exec-1"], "workflow-123")

            assert len(result) == 1
            assert result[0].overall_status == WorkflowExecutionStatusEnum.ABORTED

    def test_sorts_by_date_oldest_first(self):
        """Test that executions are sorted by date (oldest first)."""
        mock_exec1 = MagicMock(spec=WorkflowExecution)
        mock_exec1.execution_id = "exec-1"
        mock_exec1.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED
        mock_exec1.date = datetime(2024, 1, 1, 12, 0, 0)

        mock_exec2 = MagicMock(spec=WorkflowExecution)
        mock_exec2.execution_id = "exec-2"
        mock_exec2.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED
        mock_exec2.date = datetime(2024, 1, 1, 10, 0, 0)

        mock_exec3 = MagicMock(spec=WorkflowExecution)
        mock_exec3.execution_id = "exec-3"
        mock_exec3.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED
        mock_exec3.date = datetime(2024, 1, 1, 11, 0, 0)

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.WorkflowExecution.get_by_workflow_id',
            return_value=[mock_exec1, mock_exec2, mock_exec3],
        ):
            result = _get_completed_executions(["exec-1", "exec-2", "exec-3"], "workflow-123")

            assert len(result) == 3
            assert result[0].execution_id == "exec-2"  # Oldest
            assert result[1].execution_id == "exec-3"  # Middle
            assert result[2].execution_id == "exec-1"  # Newest


class TestGetWorkflowOutput:
    """Tests for _get_workflow_output function."""

    def test_returns_execution_output_when_available(self):
        """Test that execution output is returned when available."""
        mock_execution = MagicMock(spec=WorkflowExecution)
        mock_execution.output = "This is the workflow output"
        mock_execution.execution_id = "exec-1"

        result = _get_workflow_output(mock_execution)

        assert result == "This is the workflow output"

    def test_falls_back_to_last_state_when_no_execution_output(self):
        """Test fallback to last execution state output when execution output is empty."""
        mock_execution = MagicMock(spec=WorkflowExecution)
        mock_execution.output = None
        mock_execution.execution_id = "exec-1"

        mock_state1 = MagicMock(spec=WorkflowExecutionState)
        mock_state1.output = "State 1 output"
        mock_state1.date = datetime(2024, 1, 1, 10, 0, 0)

        mock_state2 = MagicMock(spec=WorkflowExecutionState)
        mock_state2.output = "State 2 output (last)"
        mock_state2.date = datetime(2024, 1, 1, 11, 0, 0)

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.WorkflowExecutionState.get_all_by_fields',
            return_value=[mock_state1, mock_state2],
        ):
            result = _get_workflow_output(mock_execution)

            assert result == "State 2 output (last)"

    def test_returns_na_when_no_output_and_no_states(self):
        """Test that 'N/A' is returned when no output and no states exist."""
        mock_execution = MagicMock(spec=WorkflowExecution)
        mock_execution.output = None
        mock_execution.execution_id = "exec-1"

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.WorkflowExecutionState.get_all_by_fields',
            return_value=[],
        ):
            result = _get_workflow_output(mock_execution)

            assert result == "N/A"

    def test_returns_na_when_last_state_has_no_output(self):
        """Test that 'N/A' is returned when last state has no output."""
        mock_execution = MagicMock(spec=WorkflowExecution)
        mock_execution.output = None
        mock_execution.execution_id = "exec-1"

        mock_state = MagicMock(spec=WorkflowExecutionState)
        mock_state.output = None
        mock_state.date = datetime(2024, 1, 1, 10, 0, 0)

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter.WorkflowExecutionState.get_all_by_fields',
            return_value=[mock_state],
        ):
            result = _get_workflow_output(mock_execution)

            assert result == "N/A"


class TestFormatExecutionsAsMarkdown:
    """Tests for _format_executions_as_markdown function."""

    def test_formats_single_execution(self):
        """Test formatting a single execution."""
        mock_execution = MagicMock(spec=WorkflowExecution)
        mock_execution.prompt = "What is the weather?"
        mock_execution.output = "It is sunny"
        mock_execution.execution_id = "exec-1"

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter._get_workflow_output',
            return_value="It is sunny",
        ):
            result = _format_executions_as_markdown([mock_execution])

            assert "# Previous Workflow Executions" in result
            assert "## Execution 1" in result
            assert "**User Input:** What is the weather?" in result
            assert "**Workflow Output:** It is sunny" in result
            assert "## Current Request" in result

    def test_formats_multiple_executions(self):
        """Test formatting multiple executions."""
        mock_exec1 = MagicMock(spec=WorkflowExecution)
        mock_exec1.prompt = "First question"
        mock_exec1.output = "First answer"
        mock_exec1.execution_id = "exec-1"

        mock_exec2 = MagicMock(spec=WorkflowExecution)
        mock_exec2.prompt = "Second question"
        mock_exec2.output = "Second answer"
        mock_exec2.execution_id = "exec-2"

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter._get_workflow_output',
            side_effect=["First answer", "Second answer"],
        ):
            result = _format_executions_as_markdown([mock_exec1, mock_exec2])

            assert "## Execution 1" in result
            assert "**User Input:** First question" in result
            assert "**Workflow Output:** First answer" in result
            assert "## Execution 2" in result
            assert "**User Input:** Second question" in result
            assert "**Workflow Output:** Second answer" in result

    def test_handles_none_prompt(self):
        """Test handling of None prompt."""
        mock_execution = MagicMock(spec=WorkflowExecution)
        mock_execution.prompt = None
        mock_execution.output = "Some output"
        mock_execution.execution_id = "exec-1"

        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter._get_workflow_output',
            return_value="Some output",
        ):
            result = _format_executions_as_markdown([mock_execution])

            assert "**User Input:** N/A" in result


class TestFormatExecutionHistory:
    """Integration tests for format_execution_history function."""

    def test_returns_empty_string_when_no_conversation(self):
        """Test that empty string is returned when conversation doesn't exist."""
        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter._get_execution_ids_from_conversation',
            return_value=[],
        ):
            result = format_execution_history("conv-123", "workflow-456")

            assert result == ""

    def test_returns_empty_string_when_no_completed_executions(self):
        """Test that empty string is returned when no completed executions exist."""
        with (
            patch(
                'codemie.service.workflow_execution.workflow_execution_history_formatter._get_execution_ids_from_conversation',
                return_value=["exec-1", "exec-2"],
            ),
            patch(
                'codemie.service.workflow_execution.workflow_execution_history_formatter._get_completed_executions',
                return_value=[],
            ),
        ):
            result = format_execution_history("conv-123", "workflow-456")

            assert result == ""

    def test_formats_execution_history_successfully(self):
        """Test successful formatting of execution history."""
        mock_exec = MagicMock(spec=WorkflowExecution)
        mock_exec.prompt = "Test question"
        mock_exec.output = "Test answer"
        mock_exec.execution_id = "exec-1"

        with (
            patch(
                'codemie.service.workflow_execution.workflow_execution_history_formatter._get_execution_ids_from_conversation',
                return_value=["exec-1"],
            ),
            patch(
                'codemie.service.workflow_execution.workflow_execution_history_formatter._get_completed_executions',
                return_value=[mock_exec],
            ),
            patch(
                'codemie.service.workflow_execution.workflow_execution_history_formatter._format_executions_as_markdown',
                return_value="# Formatted History",
            ),
        ):
            result = format_execution_history("conv-123", "workflow-456")

            assert result == "# Formatted History"

    def test_returns_empty_string_on_exception(self):
        """Test that empty string is returned when an exception occurs."""
        with patch(
            'codemie.service.workflow_execution.workflow_execution_history_formatter._get_execution_ids_from_conversation',
            side_effect=Exception("Database error"),
        ):
            result = format_execution_history("conv-123", "workflow-456")

            assert result == ""
