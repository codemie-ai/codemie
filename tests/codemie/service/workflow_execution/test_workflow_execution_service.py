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

import pytest
from unittest.mock import MagicMock, patch

from codemie.core.workflow_models import WorkflowConfig, WorkflowState, WorkflowExecutionStatusEnum
from codemie.core.workflow_models.workflow_models import WorkflowNextState
from codemie.service.workflow_execution.workflow_execution_service import WorkflowExecutionService

EXECUTION_ID = "exec-123"


@pytest.fixture
def workflow_config():
    return WorkflowConfig(
        id="wf-1",
        name="Test Workflow",
        description="desc",
        states=[
            WorkflowState(id="state_a", assistant_id="asst-1", task="", next=WorkflowNextState(state_id="state_b")),
            WorkflowState(
                id="state_b",
                assistant_id="asst-2",
                task="",
                next=WorkflowNextState(state_id="end"),
                interrupt_before=True,
            ),
        ],
    )


@pytest.fixture
def service(workflow_config):
    with (
        patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecution.get_by_execution_id"),
        patch("codemie.service.workflow_execution.workflow_execution_service.request_summary_manager"),
    ):
        svc = WorkflowExecutionService(
            workflow_config=workflow_config,
            workflow_execution_id=EXECUTION_ID,
            user=MagicMock(),
        )
        svc.workflow_execution = MagicMock()
        return svc


def _make_state(name, status):
    s = MagicMock()
    s.name = name
    s.status = status
    return s


class TestInterruptPredecessorState:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_marks_predecessor_as_interrupted(self, mock_get_states, service):
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        mock_get_states.return_value = [state_a]

        service._interrupt_predecessor_state("state_b")

        assert state_a.status == WorkflowExecutionStatusEnum.INTERRUPTED
        state_a.save.assert_called_once()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_does_not_mark_non_predecessor(self, mock_get_states, service):
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        mock_get_states.return_value = [state_a]

        service._interrupt_predecessor_state("end")  # state_a does not lead to "end" via interrupt_before

        assert state_a.status == WorkflowExecutionStatusEnum.SUCCEEDED
        state_a.save.assert_not_called()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_skips_non_succeeded_predecessor(self, mock_get_states, service):
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.IN_PROGRESS)
        mock_get_states.return_value = [state_a]

        service._interrupt_predecessor_state("state_b")

        assert state_a.status == WorkflowExecutionStatusEnum.IN_PROGRESS
        state_a.save.assert_not_called()


class TestResumeStates:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_resets_interrupted_states_to_succeeded(self, mock_get_states, service):
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.INTERRUPTED)
        state_b = _make_state("state_b", WorkflowExecutionStatusEnum.SUCCEEDED)
        mock_get_states.return_value = [state_a, state_b]

        service.resume_states()

        assert state_a.status == WorkflowExecutionStatusEnum.SUCCEEDED
        state_a.save.assert_called_once()
        state_b.save.assert_not_called()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_no_interrupted_states_is_noop(self, mock_get_states, service):
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        mock_get_states.return_value = [state_a]

        service.resume_states()

        state_a.save.assert_not_called()


class TestStartState:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState")
    def test_saves_preceding_state_id_when_provided(self, mock_state_cls, service):
        mock_instance = MagicMock()
        mock_state_cls.return_value = mock_instance

        service.start_state(workflow_state_id="node_b", task="do something", preceding_state_id="node_a")

        _, kwargs = mock_state_cls.call_args
        assert kwargs["preceding_state_id"] == "node_a"

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState")
    def test_saves_none_when_preceding_state_id_omitted(self, mock_state_cls, service):
        mock_instance = MagicMock()
        mock_state_cls.return_value = mock_instance

        service.start_state(workflow_state_id="node_a", task="do something")

        _, kwargs = mock_state_cls.call_args
        assert kwargs["preceding_state_id"] is None

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState")
    def test_saves_explicit_state_id_when_provided(self, mock_state_cls, service):
        mock_instance = MagicMock()
        mock_state_cls.return_value = mock_instance

        service.start_state(workflow_state_id="assistant_2 1 of 5", task="do something", state_id="assistant_2")

        _, kwargs = mock_state_cls.call_args
        assert kwargs["state_id"] == "assistant_2"

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState")
    def test_falls_back_to_workflow_state_id_when_state_id_omitted(self, mock_state_cls, service):
        mock_instance = MagicMock()
        mock_state_cls.return_value = mock_instance

        service.start_state(workflow_state_id="node_a", task="do something")

        _, kwargs = mock_state_cls.call_args
        assert kwargs["state_id"] == "node_a"


class TestInterruptGuard:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    @patch("codemie.service.workflow_execution.workflow_execution_service.request_summary_manager")
    def test_empty_interrupted_state_skips_predecessor_update(self, mock_summary, mock_get_states, service):
        service.workflow_execution = MagicMock()
        mock_summary.get_summary.return_value = MagicMock(tokens_usage=None)

        service.interrupt("")

        mock_get_states.assert_not_called()
