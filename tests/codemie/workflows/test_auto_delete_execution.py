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
Test suite for WorkflowExecutor._auto_delete_execution().

Tests the auto-delete logic including terminal state checks,
conversation guard, error isolation, and execution not found.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from codemie.core.workflow_models import WorkflowConfig, WorkflowExecutionStatusEnum
from codemie.workflows.workflow import WorkflowExecutor


EXECUTION_ID = "exec_123"


@pytest.fixture
def mock_user():
    """Create mock User."""
    user = Mock()
    user.id = "user_123"
    user.username = "test_user"
    return user


@pytest.fixture
def mock_thought_queue():
    """Create mock ThoughtQueue."""
    queue = Mock()
    queue.set_context = Mock()
    return queue


@pytest.fixture
def basic_workflow_config():
    """Create basic WorkflowConfig."""
    config = Mock(spec=WorkflowConfig)
    config.id = "wf_001"
    config.name = "Test Workflow"
    config.project = "test_project"
    config.states = []
    config.assistants = []
    config.tools = []
    config.enable_summarization_node = False
    return config


@pytest.fixture
def executor(basic_workflow_config, mock_user, mock_thought_queue):
    """Create WorkflowExecutor with delete_on_completion=True."""
    with patch('codemie.workflows.workflow.WorkflowExecutionService'):
        return WorkflowExecutor(
            workflow_config=basic_workflow_config,
            user_input="test",
            user=mock_user,
            thought_queue=mock_thought_queue,
            execution_id=EXECUTION_ID,
            delete_on_completion=True,
        )


@pytest.fixture
def executor_no_delete(basic_workflow_config, mock_user, mock_thought_queue):
    """Create WorkflowExecutor with delete_on_completion=False (default)."""
    with patch('codemie.workflows.workflow.WorkflowExecutionService'):
        return WorkflowExecutor(
            workflow_config=basic_workflow_config,
            user_input="test",
            user=mock_user,
            thought_queue=mock_thought_queue,
            execution_id=EXECUTION_ID,
        )


class TestAutoDeleteExecution:
    """Test cases for WorkflowExecutor._auto_delete_execution()."""

    @patch('codemie.workflows.workflow.WorkflowExecution.delete', return_value={"status": "deleted"})
    @patch('codemie.workflows.workflow.WorkflowService.find_workflow_execution_by_id')
    def test_auto_delete_on_succeeded(self, mock_find, mock_delete, executor):
        """Auto-delete triggers when execution status is SUCCEEDED."""
        execution = MagicMock()
        execution.id = "pk-id"
        execution.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED
        execution.conversation_id = None
        mock_find.return_value = execution

        executor._auto_delete_execution()

        mock_find.assert_called_once_with(EXECUTION_ID)
        mock_delete.assert_called_once_with("pk-id")

    @patch('codemie.workflows.workflow.WorkflowExecution.delete', return_value={"status": "deleted"})
    @patch('codemie.workflows.workflow.WorkflowService.find_workflow_execution_by_id')
    def test_auto_delete_on_failed(self, mock_find, mock_delete, executor):
        """Auto-delete triggers when execution status is FAILED."""
        execution = MagicMock()
        execution.id = "pk-id"
        execution.overall_status = WorkflowExecutionStatusEnum.FAILED
        execution.conversation_id = None
        mock_find.return_value = execution

        executor._auto_delete_execution()

        mock_delete.assert_called_once_with("pk-id")

    @patch('codemie.workflows.workflow.WorkflowExecution.delete')
    @patch('codemie.workflows.workflow.WorkflowService.find_workflow_execution_by_id')
    def test_skip_auto_delete_on_interrupted(self, mock_find, mock_delete, executor):
        """Auto-delete does NOT trigger for INTERRUPTED status."""
        execution = MagicMock()
        execution.id = "pk-id"
        execution.overall_status = WorkflowExecutionStatusEnum.INTERRUPTED
        execution.conversation_id = None
        mock_find.return_value = execution

        executor._auto_delete_execution()

        mock_delete.assert_not_called()

    @patch('codemie.workflows.workflow.WorkflowExecution.delete')
    @patch('codemie.workflows.workflow.WorkflowService.find_workflow_execution_by_id')
    def test_skip_auto_delete_on_aborted(self, mock_find, mock_delete, executor):
        """Auto-delete does NOT trigger for ABORTED status."""
        execution = MagicMock()
        execution.id = "pk-id"
        execution.overall_status = WorkflowExecutionStatusEnum.ABORTED
        execution.conversation_id = None
        mock_find.return_value = execution

        executor._auto_delete_execution()

        mock_delete.assert_not_called()

    @patch('codemie.workflows.workflow.WorkflowExecution.delete')
    @patch('codemie.workflows.workflow.WorkflowService.find_workflow_execution_by_id')
    def test_skip_auto_delete_on_in_progress(self, mock_find, mock_delete, executor):
        """Auto-delete does NOT trigger for IN_PROGRESS status."""
        execution = MagicMock()
        execution.id = "pk-id"
        execution.overall_status = WorkflowExecutionStatusEnum.IN_PROGRESS
        execution.conversation_id = None
        mock_find.return_value = execution

        executor._auto_delete_execution()

        mock_delete.assert_not_called()

    @patch('codemie.workflows.workflow.WorkflowExecution.delete')
    @patch('codemie.workflows.workflow.WorkflowService.find_workflow_execution_by_id')
    def test_skip_auto_delete_with_conversation_id(self, mock_find, mock_delete, executor):
        """Auto-delete does NOT trigger when execution has a conversation_id."""
        execution = MagicMock()
        execution.id = "pk-id"
        execution.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED
        execution.conversation_id = "conv-456"
        mock_find.return_value = execution

        executor._auto_delete_execution()

        mock_delete.assert_not_called()

    @patch('codemie.workflows.workflow.WorkflowExecution.delete')
    @patch('codemie.workflows.workflow.WorkflowService.find_workflow_execution_by_id')
    def test_skip_auto_delete_execution_not_found(self, mock_find, mock_delete, executor):
        """Auto-delete does nothing when execution is not found."""
        mock_find.return_value = None

        executor._auto_delete_execution()

        mock_delete.assert_not_called()

    @patch('codemie.workflows.workflow.WorkflowService.find_workflow_execution_by_id')
    def test_auto_delete_exception_does_not_propagate(self, mock_find, executor):
        """Auto-delete errors are caught and do not propagate."""
        mock_find.side_effect = Exception("DB connection error")

        # Should NOT raise - errors are isolated
        executor._auto_delete_execution()

    def test_delete_on_completion_stored_on_executor(self, executor, executor_no_delete):
        """Verify delete_on_completion is correctly stored on executor."""
        assert executor.delete_on_completion is True
        assert executor_no_delete.delete_on_completion is False


class TestAutoDeleteInExecutionStream:
    """Test that auto-delete integrates correctly in _execute_workflow_stream."""

    @patch('codemie.workflows.workflow.clear_workflow_trace_context')
    @patch.object(WorkflowExecutor, '_auto_delete_execution')
    @patch.object(WorkflowExecutor, '_run_workflow_execution')
    @patch.object(WorkflowExecutor, '_build_graph_config')
    @patch.object(WorkflowExecutor, '_start_thought_consumer_if_enabled')
    def test_auto_delete_called_when_enabled(
        self,
        mock_consumer,
        mock_config,
        mock_run,
        mock_auto_delete,
        mock_clear_trace,
        executor,
    ):
        """_auto_delete_execution is called in finally block when delete_on_completion=True."""
        executor._execute_workflow_stream()

        mock_auto_delete.assert_called_once()

    @patch('codemie.workflows.workflow.clear_workflow_trace_context')
    @patch.object(WorkflowExecutor, '_auto_delete_execution')
    @patch.object(WorkflowExecutor, '_run_workflow_execution')
    @patch.object(WorkflowExecutor, '_build_graph_config')
    @patch.object(WorkflowExecutor, '_start_thought_consumer_if_enabled')
    def test_auto_delete_not_called_when_disabled(
        self,
        mock_consumer,
        mock_config,
        mock_run,
        mock_auto_delete,
        mock_clear_trace,
        executor_no_delete,
    ):
        """_auto_delete_execution is NOT called when delete_on_completion=False."""
        executor_no_delete._execute_workflow_stream()

        mock_auto_delete.assert_not_called()

    @patch('codemie.workflows.workflow.clear_workflow_trace_context')
    @patch.object(WorkflowExecutor, '_auto_delete_execution')
    @patch.object(WorkflowExecutor, '_run_workflow_execution', side_effect=Exception("workflow error"))
    @patch.object(WorkflowExecutor, '_build_graph_config')
    @patch.object(WorkflowExecutor, '_start_thought_consumer_if_enabled')
    @patch.object(WorkflowExecutor, '_handle_task_exception')
    def test_auto_delete_called_even_on_workflow_failure(
        self,
        mock_handle_exc,
        mock_consumer,
        mock_config,
        mock_run,
        mock_auto_delete,
        mock_clear_trace,
        executor,
    ):
        """_auto_delete_execution runs in finally block even when workflow fails."""
        executor._execute_workflow_stream()

        mock_auto_delete.assert_called_once()
