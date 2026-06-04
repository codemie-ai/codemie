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
import json
from unittest.mock import MagicMock, patch

from codemie.core.workflow_models import WorkflowConfig, WorkflowState, WorkflowExecutionStatusEnum
from codemie.core.workflow_models.workflow_models import WorkflowNextState
from codemie.service.workflow_execution.workflow_execution_service import WorkflowExecutionService, EXECUTION_ID_KEYWORD

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
                task="Review and approve the output",
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


def _make_state(state_id, status):
    s = MagicMock()
    s.state_id = state_id
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

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_marks_only_last_iteration_in_loop(self, mock_get_states, service):
        # Simulate 3 iterations of state_a; results returned newest-first (order_by update_date desc)
        iter_3 = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)  # most recent
        iter_2 = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        iter_1 = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        mock_get_states.return_value = [iter_3, iter_2, iter_1]

        service._interrupt_predecessor_state("state_b")

        assert iter_3.status == WorkflowExecutionStatusEnum.INTERRUPTED
        iter_3.save.assert_called_once()
        assert iter_2.status == WorkflowExecutionStatusEnum.SUCCEEDED
        iter_2.save.assert_not_called()
        assert iter_1.status == WorkflowExecutionStatusEnum.SUCCEEDED
        iter_1.save.assert_not_called()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_queries_with_order_by_update_date_desc(self, mock_get_states, service):
        mock_get_states.return_value = []

        service._interrupt_predecessor_state("state_b")

        mock_get_states.assert_called_once_with(
            fields={EXECUTION_ID_KEYWORD: service.workflow_execution_id},
            order_by="update_date",
            order_desc=True,
        )


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

        service.start_state(workflow_state_id="node_b", task="do something", preceding_state_ids="node_a")

        _, kwargs = mock_state_cls.call_args
        assert kwargs["preceding_state_ids"] == "node_a"

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState")
    def test_saves_none_when_preceding_state_id_omitted(self, mock_state_cls, service):
        mock_instance = MagicMock()
        mock_state_cls.return_value = mock_instance

        service.start_state(workflow_state_id="node_a", task="do something")

        _, kwargs = mock_state_cls.call_args
        assert kwargs["preceding_state_ids"] is None

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

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState")
    def test_saves_iteration_number_when_provided(self, mock_state_cls, service):
        mock_instance = MagicMock()
        mock_state_cls.return_value = mock_instance

        service.start_state(workflow_state_id="agent_node 3 of 10", task="do something", iteration_number=3)

        _, kwargs = mock_state_cls.call_args
        assert kwargs["iteration_number"] == 3

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState")
    def test_saves_none_when_iteration_number_omitted(self, mock_state_cls, service):
        mock_instance = MagicMock()
        mock_state_cls.return_value = mock_instance

        service.start_state(workflow_state_id="node_a", task="do something")

        _, kwargs = mock_state_cls.call_args
        assert kwargs["iteration_number"] is None


class TestSendInterruptedEvent:
    def test_no_op_when_thought_queue_is_none(self, service):
        service.thought_queue = None
        service._send_interrupted_event("state_b")
        # No error raised — the method returns early

    def test_sends_event_with_task_and_id_from_state_config(self, service):
        mock_queue = MagicMock()
        service.thought_queue = mock_queue

        with patch(
            "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_by_id",
            return_value=None,
        ):
            service._send_interrupted_event("state_b", execution_state_id="exec-state-uuid")

        mock_queue.send.assert_called_once()
        payload = json.loads(mock_queue.send.call_args[0][0])
        assert payload["workflow_state"]["id"] == "exec-state-uuid"
        assert payload["workflow_state"]["name"] == "state_b"
        assert payload["workflow_state"]["task"] == "Review and approve the output"
        assert payload["workflow_state"]["status"] == WorkflowExecutionStatusEnum.INTERRUPTED.value
        assert payload["workflow_state"]["event_type"] == "state_interrupted"
        assert payload["last"] is True

    def test_sends_event_without_id_when_not_provided(self, service):
        mock_queue = MagicMock()
        service.thought_queue = mock_queue

        service._send_interrupted_event("state_b")

        payload = json.loads(mock_queue.send.call_args[0][0])
        assert payload["workflow_state"]["id"] is None

    def test_sends_none_task_when_state_config_not_found(self, service):
        mock_queue = MagicMock()
        service.thought_queue = mock_queue

        service._send_interrupted_event("unknown_state")

        payload = json.loads(mock_queue.send.call_args[0][0])
        assert payload["workflow_state"]["task"] is None

    def test_interrupt_propagates_predecessor_id_to_event(self, service):
        mock_queue = MagicMock()
        service.thought_queue = mock_queue
        service.workflow_execution = MagicMock()

        predecessor = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        predecessor.id = "predecessor-uuid"

        with (
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields",
                return_value=[predecessor],
            ),
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_by_id",
                return_value=None,
            ),
        ):
            service.interrupt("state_b")

        mock_queue.send.assert_called_once()
        payload = json.loads(mock_queue.send.call_args[0][0])
        assert payload["workflow_state"]["event_type"] == "state_interrupted"
        assert payload["workflow_state"]["id"] == "predecessor-uuid"

    def test_with_predecessor_output(self, service):
        mock_queue = MagicMock()
        service.thought_queue = mock_queue

        mock_state = MagicMock()
        mock_state.output = "some output"

        with patch(
            "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_by_id",
            return_value=mock_state,
        ):
            service._send_interrupted_event("state_b", execution_state_id="exec-state-456")

        payload = json.loads(mock_queue.send.call_args[0][0])
        assert payload["generated"] == "some output"
        assert payload["last"] is True

    def test_without_execution_state_id(self, service):
        mock_queue = MagicMock()
        service.thought_queue = mock_queue

        service._send_interrupted_event("state_a", execution_state_id=None)

        payload = json.loads(mock_queue.send.call_args[0][0])
        assert payload["generated"] is None
        assert payload["last"] is True

    def test_db_lookup_failure_degrades_gracefully(self, service):
        mock_queue = MagicMock()
        service.thought_queue = mock_queue

        with patch(
            "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_by_id",
            side_effect=Exception("DB unavailable"),
        ):
            service._send_interrupted_event("state_b", execution_state_id="exec-state-456")

        mock_queue.send.assert_called_once()
        payload = json.loads(mock_queue.send.call_args[0][0])
        assert payload["generated"] is None
        assert payload["last"] is True


class TestInterruptGuard:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    @patch("codemie.service.workflow_execution.workflow_execution_service.request_summary_manager")
    def test_empty_interrupted_state_skips_predecessor_update(self, mock_summary, mock_get_states, service):
        service.workflow_execution = MagicMock()
        mock_summary.get_summary.return_value = MagicMock(tokens_usage=None)

        service.interrupt("")

        mock_get_states.assert_not_called()


class TestAuthenticationRequired:
    def test_mark_authentication_required_preserves_serialized_payload(self, service):
        service.workflow_execution = MagicMock()
        output = json.dumps(
            {
                "auth_config_id": "auth-1",
                "mcp_config_id": "mcp-1",
                "mcp_server_name": "server-1",
                "status": "config_error",
                "auth_type": "saml",
                "error_context": "SAML is not supported for HTTP transport.",
            }
        )

        with patch.object(service, "_refresh_workflow_execution"):
            service.mark_authentication_required(output)

        assert service.workflow_execution.overall_status == WorkflowExecutionStatusEnum.AUTHENTICATION_REQUIRED
        assert service.workflow_execution.output == output
        service.workflow_execution.update.assert_called_with(refresh=True)

    def test_mark_authentication_required_aggregate_servers_payload(self, service):
        service.workflow_execution = MagicMock()
        output = json.dumps(
            {
                "error": "authentication_required",
                "servers": [
                    {
                        "auth_config_id": "auth-1",
                        "mcp_config_id": "mcp-1",
                        "mcp_config_name": "server-1",
                        "mcp_server_name": "server-1",
                        "status": "authentication_required",
                        "auth_type": "oauth2",
                        "as_hostname": "login.example.com",
                        "error_context": None,
                    },
                    {
                        "auth_config_id": "auth-2",
                        "mcp_config_id": "mcp-2",
                        "mcp_config_name": "server-2",
                        "mcp_server_name": "server-2",
                        "status": "session_expired",
                        "auth_type": "saml",
                        "as_hostname": "idp.example.com",
                        "error_context": "SAML session expired",
                    },
                ],
            }
        )

        with patch.object(service, "_refresh_workflow_execution"):
            service.mark_authentication_required(output)

        assert service.workflow_execution.overall_status == WorkflowExecutionStatusEnum.AUTHENTICATION_REQUIRED
        assert service.workflow_execution.output == output
        service.workflow_execution.update.assert_called_with(refresh=True)
