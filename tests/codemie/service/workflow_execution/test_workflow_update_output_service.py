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
from unittest.mock import MagicMock, patch

from codemie.service.workflow_execution.workflow_update_output_service import WorkflowUpdateOutputService
from codemie.core.workflow_models import WorkflowState, WorkflowNextState
from codemie.core.workflow_models.workflow_config import WorkflowConfig


@patch("codemie.workflows.checkpoint_saver.CheckpointSaver.update_last_checkpoint")
@patch("codemie.core.workflow_models.WorkflowExecutionStateThought.get_by_fields")
@patch("codemie.core.workflow_models.WorkflowExecutionState.get_by_id")
def test_run_update_output(mock_get_state, mock_get_thought, mock_update_checkpoint):
    mock_state = MagicMock()
    mock_get_state.return_value = mock_state

    mock_thought = MagicMock()
    mock_get_thought.return_value = mock_thought

    WorkflowUpdateOutputService.run(execution_id="execution_id", state_id="state_id", new_output="new_output")

    mock_state.save.assert_called_once()
    assert mock_state.output == "new_output"

    mock_get_thought.assert_called_once_with({"execution_state_id": "state_id", "parent_id": None})
    mock_thought.save.assert_called_once()
    assert mock_thought.content == "new_output"

    # Update assertion to include output_key parameter (will be None if not found in config)
    mock_update_checkpoint.assert_called_once()
    call_args = mock_update_checkpoint.call_args
    assert call_args[0][0] == "execution_id"
    assert call_args[1]["output"] == "new_output"
    assert "output_key" in call_args[1]


class TestGetOutputKey:
    """Test suite for _get_output_key method."""

    @pytest.fixture
    def service(self):
        """Create service instance for testing."""
        return WorkflowUpdateOutputService(
            execution_id="test-execution-id", state_id="test-state-id", new_output="test-output"
        )

    @pytest.fixture
    def mock_state(self):
        """Create mock execution state."""
        state = MagicMock()
        state.name = "test-state-name"
        return state

    @pytest.fixture
    def mock_execution(self):
        """Create mock workflow execution."""
        execution = MagicMock()
        execution.workflow_id = "test-workflow-id"
        return execution

    @pytest.fixture
    def mock_workflow_config(self):
        """Create mock workflow configuration with output_key."""
        return WorkflowConfig(
            id="test-workflow-id",
            name="Test Workflow",
            description="Test workflow description",
            states=[
                WorkflowState(
                    id="test-state-name",
                    assistant_id="test-assistant",
                    task="test-task",
                    next=WorkflowNextState(state_id="next-state", output_key="test_output_key"),
                )
            ],
        )

    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowService")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecution.get_by_execution_id")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecutionState.get_by_id")
    def test_get_output_key_success(
        self,
        mock_get_state,
        mock_get_execution,
        mock_workflow_service,
        service,
        mock_state,
        mock_execution,
        mock_workflow_config,
    ):
        """Test successful retrieval of output_key from workflow configuration."""
        # Arrange
        mock_get_state.return_value = mock_state
        mock_get_execution.return_value = [mock_execution]
        mock_workflow_service_instance = MagicMock()
        mock_workflow_service_instance.get_workflow.return_value = mock_workflow_config
        mock_workflow_service.return_value = mock_workflow_service_instance

        # Act
        result = service._get_output_key()

        # Assert
        assert result == "test_output_key"
        mock_get_state.assert_called_once_with("test-state-id")
        mock_get_execution.assert_called_once_with("test-execution-id")
        mock_workflow_service_instance.get_workflow.assert_called_once_with("test-workflow-id")

    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecutionState.get_by_id")
    def test_get_output_key_state_not_found(self, mock_get_state, service):
        """Test that None is returned when state is not found."""
        # Arrange
        mock_get_state.return_value = None

        # Act
        result = service._get_output_key()

        # Assert
        assert result is None
        mock_get_state.assert_called_once_with("test-state-id")

    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecution.get_by_execution_id")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecutionState.get_by_id")
    def test_get_output_key_execution_not_found(self, mock_get_state, mock_get_execution, service, mock_state):
        """Test that None is returned when execution is not found."""
        # Arrange
        mock_get_state.return_value = mock_state
        mock_get_execution.return_value = []

        # Act
        result = service._get_output_key()

        # Assert
        assert result is None
        mock_get_execution.assert_called_once_with("test-execution-id")

    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowService")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecution.get_by_execution_id")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecutionState.get_by_id")
    def test_get_output_key_workflow_config_not_found(
        self, mock_get_state, mock_get_execution, mock_workflow_service, service, mock_state, mock_execution
    ):
        """Test that None is returned when workflow configuration is not found."""
        # Arrange
        mock_get_state.return_value = mock_state
        mock_get_execution.return_value = [mock_execution]
        mock_workflow_service_instance = MagicMock()
        mock_workflow_service_instance.get_workflow.return_value = None
        mock_workflow_service.return_value = mock_workflow_service_instance

        # Act
        result = service._get_output_key()

        # Assert
        assert result is None
        mock_workflow_service_instance.get_workflow.assert_called_once_with("test-workflow-id")

    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowService")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecution.get_by_execution_id")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecutionState.get_by_id")
    def test_get_output_key_workflow_config_no_states(
        self, mock_get_state, mock_get_execution, mock_workflow_service, service, mock_state, mock_execution
    ):
        """Test that None is returned when workflow configuration has no states."""
        # Arrange
        mock_get_state.return_value = mock_state
        mock_get_execution.return_value = [mock_execution]
        mock_workflow_service_instance = MagicMock()
        workflow_config = MagicMock()
        workflow_config.states = None
        mock_workflow_service_instance.get_workflow.return_value = workflow_config
        mock_workflow_service.return_value = mock_workflow_service_instance

        # Act
        result = service._get_output_key()

        # Assert
        assert result is None

    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowService")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecution.get_by_execution_id")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecutionState.get_by_id")
    def test_get_output_key_state_not_in_workflow_config(
        self, mock_get_state, mock_get_execution, mock_workflow_service, service, mock_state, mock_execution
    ):
        """Test that None is returned when state name is not found in workflow configuration."""
        # Arrange
        mock_get_state.return_value = mock_state
        mock_get_execution.return_value = [mock_execution]
        mock_workflow_service_instance = MagicMock()
        workflow_config = WorkflowConfig(
            id="test-workflow-id",
            name="Test Workflow",
            description="Test workflow description",
            states=[
                WorkflowState(
                    id="different-state-name",
                    assistant_id="test-assistant",
                    task="test-task",
                    next=WorkflowNextState(state_id="next-state", output_key="some_output_key"),
                )
            ],
        )
        mock_workflow_service_instance.get_workflow.return_value = workflow_config
        mock_workflow_service.return_value = mock_workflow_service_instance

        # Act
        result = service._get_output_key()

        # Assert
        assert result is None

    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowService")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecution.get_by_execution_id")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecutionState.get_by_id")
    def test_get_output_key_no_next_config(
        self, mock_get_state, mock_get_execution, mock_workflow_service, service, mock_state, mock_execution
    ):
        """Test that None is returned when state has no next configuration."""
        # Arrange
        mock_get_state.return_value = mock_state
        mock_get_execution.return_value = [mock_execution]
        mock_workflow_service_instance = MagicMock()
        # Use MagicMock for workflow_config to allow next=None
        workflow_config = MagicMock()
        mock_workflow_state = MagicMock()
        mock_workflow_state.id = "test-state-name"
        mock_workflow_state.next = None
        workflow_config.states = [mock_workflow_state]
        mock_workflow_service_instance.get_workflow.return_value = workflow_config
        mock_workflow_service.return_value = mock_workflow_service_instance

        # Act
        result = service._get_output_key()

        # Assert
        assert result is None

    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowService")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecution.get_by_execution_id")
    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecutionState.get_by_id")
    def test_get_output_key_no_output_key_in_next(
        self, mock_get_state, mock_get_execution, mock_workflow_service, service, mock_state, mock_execution
    ):
        """Test that None is returned when next configuration has no output_key."""
        # Arrange
        mock_get_state.return_value = mock_state
        mock_get_execution.return_value = [mock_execution]
        mock_workflow_service_instance = MagicMock()
        workflow_config = WorkflowConfig(
            id="test-workflow-id",
            name="Test Workflow",
            description="Test workflow description",
            states=[
                WorkflowState(
                    id="test-state-name",
                    assistant_id="test-assistant",
                    task="test-task",
                    next=WorkflowNextState(state_id="next-state", output_key=None),
                )
            ],
        )
        mock_workflow_service_instance.get_workflow.return_value = workflow_config
        mock_workflow_service.return_value = mock_workflow_service_instance

        # Act
        result = service._get_output_key()

        # Assert
        assert result is None

    @patch("codemie.service.workflow_execution.workflow_update_output_service.WorkflowExecutionState.get_by_id")
    def test_get_output_key_exception_handling(self, mock_get_state, service):
        """Test that exceptions are caught and None is returned."""
        # Arrange
        mock_get_state.side_effect = Exception("Database error")

        # Act
        result = service._get_output_key()

        # Assert
        assert result is None
        mock_get_state.assert_called_once_with("test-state-id")
