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
import threading
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
    def test_marks_succeeded_predecessor_as_interrupted(self, mock_get_states, service):
        """Predecessor is marked INTERRUPTED, and a pending transition row is also written."""
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        state_a.id = "state_a_exec_1"
        mock_get_states.return_value = [state_a]

        with patch.object(service, "record_transition") as mock_record:
            service._interrupt_predecessor_state("state_b", checkpoint_state={"next": ["state_b"]})

        assert state_a.status == WorkflowExecutionStatusEnum.INTERRUPTED
        state_a.save.assert_called_once()
        mock_record.assert_called_once_with(
            from_state_id="state_a_exec_1",
            to_state_id=None,
            workflow_context={"next": ["state_b"]},
        )

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_does_not_mark_non_predecessor(self, mock_get_states, service):
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        mock_get_states.return_value = [state_a]

        service._interrupt_predecessor_state("end")  # state_a does not lead to "end" via interrupt_before

        assert state_a.status == WorkflowExecutionStatusEnum.SUCCEEDED
        state_a.save.assert_not_called()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_marks_in_progress_predecessor_as_interrupted(self, mock_get_states, service):
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.IN_PROGRESS)
        mock_get_states.return_value = [state_a]

        service._interrupt_predecessor_state("state_b")

        assert state_a.status == WorkflowExecutionStatusEnum.INTERRUPTED
        state_a.save.assert_called_once()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_records_transition_only_for_last_iteration(self, mock_get_states, service):
        iter_3 = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        iter_3.id = "state_a_iter_3"
        iter_2 = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        iter_2.id = "state_a_iter_2"
        mock_get_states.return_value = [iter_3, iter_2]  # newest-first

        with patch.object(service, "record_transition") as mock_record:
            service._interrupt_predecessor_state("state_b", checkpoint_state={})

        mock_record.assert_called_once_with(
            from_state_id="state_a_iter_3",
            to_state_id=None,
            workflow_context={},
        )
        assert iter_2.status == WorkflowExecutionStatusEnum.SUCCEEDED
        iter_2.save.assert_not_called()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_queries_with_order_by_update_date_desc(self, mock_get_states, service):
        mock_get_states.return_value = []

        service._interrupt_predecessor_state("state_b")

        mock_get_states.assert_called_once_with(
            fields={EXECUTION_ID_KEYWORD: service.workflow_execution_id},
            order_by="update_date",
            order_desc=True,
        )

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_save_failure_is_reraised(self, mock_get_states, service):
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.IN_PROGRESS)
        state_a.save.side_effect = OSError("db down")
        mock_get_states.return_value = [state_a]

        with pytest.raises(OSError, match="db down"):
            service._interrupt_predecessor_state("state_b")

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_query_failure_returns_none(self, mock_get_states, service):
        mock_get_states.side_effect = OSError("index unavailable")

        assert service._interrupt_predecessor_state("state_b") is None

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_records_empty_context_when_checkpoint_state_is_none(self, mock_get_states, service):
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        state_a.id = "state_a_exec_1"
        mock_get_states.return_value = [state_a]

        with patch.object(service, "record_transition") as mock_record:
            service._interrupt_predecessor_state("state_b", checkpoint_state=None)

        mock_record.assert_called_once_with(
            from_state_id="state_a_exec_1",
            to_state_id=None,
            workflow_context={},
        )


class TestInterrupt:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_propagates_and_does_not_send_event_when_predecessor_save_fails(self, mock_get_states, service):
        """A partial predecessor update must not be reported as a successful interrupt."""
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.IN_PROGRESS)
        state_a.save.side_effect = OSError("db down")
        mock_get_states.return_value = [state_a]

        with (
            patch.object(service, "_refresh_workflow_execution"),
            patch.object(service, "_calculate_tokens_usage", return_value={}),
            patch.object(service, "_send_interrupted_event") as mock_event,
        ):
            with pytest.raises(OSError, match="db down"):
                service.interrupt("state_b")

        assert service.workflow_execution.overall_status == WorkflowExecutionStatusEnum.INTERRUPTED
        mock_event.assert_not_called()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_passes_predecessor_id_when_interrupt_succeeds(self, mock_get_states, service):
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        state_a.id = "pred-1"
        mock_get_states.return_value = [state_a]

        with (
            patch.object(service, "_refresh_workflow_execution"),
            patch.object(service, "_calculate_tokens_usage", return_value={}),
            patch.object(service, "_send_interrupted_event") as mock_event,
        ):
            service.interrupt("state_b")

        mock_event.assert_called_once_with("state_b", "pred-1")


class TestTransitionViewerReportsFalseTerminalOnInterrupt:
    """EPMCDME-13566: after the fix, _interrupt_predecessor_state records a pending
    transition row so the viewer can find it instead of returning 404."""

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_pending_transition_row_is_recorded_after_interrupt(self, mock_get_states, service, workflow_config):
        checkpoint = {"next": ["state_b"], "messages": []}

        state_a_execution_id = "state_a_exec_1"
        state_a = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        state_a.id = state_a_execution_id
        mock_get_states.return_value = [state_a]

        with patch.object(service, "record_transition") as mock_record:
            predecessor_id = service._interrupt_predecessor_state("state_b", checkpoint_state=checkpoint)

        assert predecessor_id == state_a_execution_id
        assert state_a.status == WorkflowExecutionStatusEnum.INTERRUPTED
        assert workflow_config.states[0].next.state_id == "state_b"
        mock_record.assert_called_once_with(
            from_state_id=state_a_execution_id,
            to_state_id=None,
            workflow_context=checkpoint,
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


class TestAbort:
    def _patch_abort_internals(self, service):
        return (
            patch.object(service, "_refresh_workflow_execution"),
            patch.object(service, "_calculate_tokens_usage", return_value={}),
            patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowMonitoringService"),
            patch("codemie.service.workflow_execution.workflow_execution_service.request_summary_manager"),
        )

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_db_failure_on_get_all_by_fields_does_not_propagate(self, mock_get_states, service):
        """abort() must swallow a DB failure when querying child states, just like fail() does."""
        mock_get_states.side_effect = OSError("ES unreachable")

        patches = self._patch_abort_internals(service)
        with patches[0], patches[1], patches[2], patches[3]:
            service.abort()  # must not raise

        assert service.workflow_execution.overall_status == WorkflowExecutionStatusEnum.ABORTED

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_per_state_save_failure_does_not_abort_remaining_states(self, mock_get_states, service):
        """A save() failure on one child state must not prevent remaining states from being marked ABORTED."""
        state_bad = _make_state("bad", WorkflowExecutionStatusEnum.IN_PROGRESS)
        state_bad.save.side_effect = OSError("constraint violation")
        state_good = _make_state("good", WorkflowExecutionStatusEnum.IN_PROGRESS)
        mock_get_states.return_value = [state_bad, state_good]

        patches = self._patch_abort_internals(service)
        with patches[0], patches[1], patches[2], patches[3]:
            service.abort()  # must not raise

        state_bad.save.assert_called_once()
        state_good.save.assert_called_once()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    def test_completed_at_set_on_aborted_states(self, mock_get_states, service):
        """abort() must set completed_at on child states, matching fail() behaviour."""
        state = _make_state("state_x", WorkflowExecutionStatusEnum.IN_PROGRESS)
        mock_get_states.return_value = [state]

        patches = self._patch_abort_internals(service)
        with patches[0], patches[1], patches[2], patches[3]:
            service.abort()

        assert state.completed_at is not None
        assert state.status == WorkflowExecutionStatusEnum.ABORTED


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
            patch.object(service, "record_transition"),
        ):
            service.interrupt("state_b")

        mock_queue.send.assert_called_once()
        payload = json.loads(mock_queue.send.call_args[0][0])
        assert payload["workflow_state"]["event_type"] == "state_interrupted"
        assert payload["workflow_state"]["id"] == "predecessor-uuid"


class TestAbortSubWorkflowCascade:
    """_abort_active_sub_execution cascades abort to the active child execution."""

    def test_cascades_to_in_progress_child(self, service):
        service.workflow_execution.active_sub_execution_id = "child-exec-001"

        child_execution = MagicMock()
        child_execution.overall_status = WorkflowExecutionStatusEnum.IN_PROGRESS
        child_execution.workflow_id = "child-wf-id"

        child_svc = MagicMock()

        with (
            patch("codemie.service.workflow_service.WorkflowService") as mock_ws,
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionService",
                return_value=child_svc,
            ),
        ):
            mock_ws.find_workflow_execution_by_id.return_value = child_execution
            mock_ws.return_value.get_workflow.return_value = MagicMock()
            service._abort_active_sub_execution()

        child_svc.abort.assert_called_once()

    def test_no_cascade_when_no_active_sub_execution(self, service):
        service.workflow_execution.active_sub_execution_id = None

        with patch("codemie.service.workflow_service.WorkflowService") as mock_ws:
            service._abort_active_sub_execution()

        mock_ws.find_workflow_execution_by_id.assert_not_called()

    def test_no_cascade_when_child_already_aborted(self, service):
        service.workflow_execution.active_sub_execution_id = "child-exec-002"

        child_execution = MagicMock()
        child_execution.overall_status = WorkflowExecutionStatusEnum.ABORTED

        with (
            patch("codemie.service.workflow_service.WorkflowService") as mock_ws,
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionService",
            ) as mock_svc_cls,
        ):
            mock_ws.find_workflow_execution_by_id.return_value = child_execution
            service._abort_active_sub_execution()

        mock_svc_cls.assert_not_called()

    def test_no_cascade_when_child_succeeded(self, service):
        service.workflow_execution.active_sub_execution_id = "child-exec-003"

        child_execution = MagicMock()
        child_execution.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED

        with (
            patch("codemie.service.workflow_service.WorkflowService") as mock_ws,
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionService",
            ) as mock_svc_cls,
        ):
            mock_ws.find_workflow_execution_by_id.return_value = child_execution
            service._abort_active_sub_execution()

        mock_svc_cls.assert_not_called()

    def test_no_cascade_when_child_failed(self, service):
        service.workflow_execution.active_sub_execution_id = "child-exec-005"

        child_execution = MagicMock()
        child_execution.overall_status = WorkflowExecutionStatusEnum.FAILED

        with (
            patch("codemie.service.workflow_service.WorkflowService") as mock_ws,
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionService",
            ) as mock_svc_cls,
        ):
            mock_ws.find_workflow_execution_by_id.return_value = child_execution
            service._abort_active_sub_execution()

        mock_svc_cls.assert_not_called()

    def test_child_abort_error_does_not_propagate(self, service):
        service.workflow_execution.active_sub_execution_id = "child-exec-004"

        child_execution = MagicMock()
        child_execution.overall_status = WorkflowExecutionStatusEnum.IN_PROGRESS
        child_execution.workflow_id = "child-wf-id"

        with (
            patch("codemie.service.workflow_service.WorkflowService") as mock_ws,
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionService",
                side_effect=RuntimeError("db down"),
            ),
        ):
            mock_ws.find_workflow_execution_by_id.return_value = child_execution
            mock_ws.return_value.get_workflow.return_value = MagicMock()
            service._abort_active_sub_execution()  # must not raise


class TestSendInterruptedEventExtended:
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


class TestFail:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowMonitoringService")
    def test_fail_marks_in_progress_states_as_failed(self, mock_monitoring, mock_get_states, service):
        """fail() must transition any IN_PROGRESS or INTERRUPTED step states to FAILED."""
        in_progress = _make_state("state_a", WorkflowExecutionStatusEnum.IN_PROGRESS)
        interrupted = _make_state("state_b", WorkflowExecutionStatusEnum.INTERRUPTED)
        succeeded = _make_state("state_c", WorkflowExecutionStatusEnum.SUCCEEDED)
        mock_get_states.return_value = [in_progress, interrupted, succeeded]

        with patch.object(service, "_refresh_workflow_execution"):
            with patch.object(service, "_calculate_tokens_usage", return_value={}):
                with patch.object(service, "_update_assistant_response_in_history"):
                    service.fail(error_class="RuntimeError", error_message="boom")

        assert in_progress.status == WorkflowExecutionStatusEnum.FAILED
        in_progress.save.assert_called_once()
        assert interrupted.status == WorkflowExecutionStatusEnum.FAILED
        interrupted.save.assert_called_once()
        assert succeeded.status == WorkflowExecutionStatusEnum.SUCCEEDED
        succeeded.save.assert_not_called()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowMonitoringService")
    def test_fail_does_not_overwrite_aborted_status(self, mock_monitoring, mock_get_states, service):
        """fail() must leave an ABORTED execution ABORTED and not persist anything."""
        service.workflow_execution.overall_status = WorkflowExecutionStatusEnum.ABORTED

        with patch.object(service, "_refresh_workflow_execution"):
            service.fail(error_class="RuntimeError", error_message="boom")

        assert service.workflow_execution.overall_status == WorkflowExecutionStatusEnum.ABORTED
        service.workflow_execution.update.assert_not_called()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowMonitoringService")
    def test_fail_sets_failed_when_in_progress(self, mock_monitoring, mock_get_states, service):
        mock_get_states.return_value = []
        service.workflow_execution.overall_status = WorkflowExecutionStatusEnum.IN_PROGRESS

        with patch.object(service, "_refresh_workflow_execution"):
            with patch.object(service, "_calculate_tokens_usage", return_value={}):
                with patch.object(service, "_update_assistant_response_in_history"):
                    service.fail(error_class="RuntimeError", error_message="boom")

        assert service.workflow_execution.overall_status == WorkflowExecutionStatusEnum.FAILED
        service.workflow_execution.update.assert_called_once()


class TestAbortByConversationId:
    SVC = "codemie.service.workflow_execution.workflow_execution_service"

    @staticmethod
    def _execution(execution_id, status=WorkflowExecutionStatusEnum.IN_PROGRESS):
        ex = MagicMock()
        ex.execution_id = execution_id
        ex.workflow_id = f"wf-{execution_id}"
        ex.overall_status = status
        return ex

    def test_aborts_each_in_progress_execution(self):
        executions = [self._execution("e1"), self._execution("e2")]
        user = MagicMock()
        with (
            patch(f"{self.SVC}.WorkflowExecution.get_in_progress_by_conversation_id", return_value=executions),
            patch("codemie.service.workflow_service.WorkflowService") as mock_wf_service,
            patch(f"{self.SVC}.WorkflowExecutionService") as mock_service_cls,
        ):
            count = WorkflowExecutionService.abort_by_conversation_id("conv-1", user)

        assert count == (2, 0)
        assert mock_service_cls.call_count == 2
        mock_service_cls.assert_any_call(
            workflow_config=mock_wf_service.return_value.get_workflow.return_value,
            workflow_execution_id="e1",
            user=user,
        )
        assert mock_service_cls.return_value.abort.call_count == 2

    def test_failure_of_one_does_not_skip_others(self):
        executions = [self._execution("e1"), self._execution("e2")]
        with (
            patch(f"{self.SVC}.WorkflowExecution.get_in_progress_by_conversation_id", return_value=executions),
            patch("codemie.service.workflow_service.WorkflowService"),
            patch(f"{self.SVC}.WorkflowExecutionService") as mock_service_cls,
        ):
            mock_service_cls.return_value.abort.side_effect = [RuntimeError("boom"), None]
            count = WorkflowExecutionService.abort_by_conversation_id("conv-1", MagicMock())

        assert count == (1, 1)
        assert mock_service_cls.return_value.abort.call_count == 2

    def test_missing_workflow_config_still_aborts_execution(self):
        with (
            patch(
                f"{self.SVC}.WorkflowExecution.get_in_progress_by_conversation_id", return_value=[self._execution("e1")]
            ),
            patch("codemie.service.workflow_service.WorkflowService") as mock_wf_service,
            patch(f"{self.SVC}.WorkflowExecutionService") as mock_service_cls,
        ):
            mock_wf_service.return_value.get_workflow.side_effect = RuntimeError("workflow deleted")
            count = WorkflowExecutionService.abort_by_conversation_id("conv-1", MagicMock())

        assert count == (1, 0)
        assert mock_service_cls.call_args.kwargs["workflow_config"] is None
        mock_service_cls.return_value.abort.assert_called_once()

    def test_no_executions_aborts_nothing(self):
        with (
            patch(f"{self.SVC}.WorkflowExecution.get_in_progress_by_conversation_id", return_value=[]),
            patch(f"{self.SVC}.WorkflowExecutionService") as mock_service_cls,
        ):
            count = WorkflowExecutionService.abort_by_conversation_id("conv-1", MagicMock())

        assert count == (0, 0)
        mock_service_cls.assert_not_called()

    def test_completed_executions_are_left_untouched(self):
        """The lookup filters IN_PROGRESS in the query, so terminal executions never reach abort() (AC3)."""
        with (
            patch(
                "codemie.core.workflow_models.workflow_execution.WorkflowExecution.get_all_by_fields",
                return_value=[],
            ) as mock_get_all,
            patch(f"{self.SVC}.WorkflowExecutionService") as mock_service_cls,
        ):
            count = WorkflowExecutionService.abort_by_conversation_id("conv-1", MagicMock())

        assert count == (0, 0)
        mock_service_cls.assert_not_called()
        mock_get_all.assert_called_once_with(
            {"conversation_id": "conv-1", "overall_status": WorkflowExecutionStatusEnum.IN_PROGRESS}
        )

    def test_stop_marks_in_progress_execution_aborted(self, workflow_config):
        """Stop with only the DB layer mocked: the real abort() flips the execution to ABORTED (AC1/AC7)."""
        execution = MagicMock()
        execution.execution_id = "e1"
        execution.workflow_id = "wf-1"
        execution.overall_status = WorkflowExecutionStatusEnum.IN_PROGRESS
        execution.active_sub_execution_id = None

        with (
            patch(f"{self.SVC}.WorkflowExecution.get_in_progress_by_conversation_id", return_value=[execution]),
            patch(f"{self.SVC}.WorkflowExecution.get_by_execution_id", return_value=[execution]),
            patch("codemie.service.workflow_service.WorkflowService") as mock_wf_service,
            patch(f"{self.SVC}.WorkflowExecutionState.get_all_by_fields", return_value=[]),
            patch(f"{self.SVC}.WorkflowMonitoringService"),
            patch(f"{self.SVC}.request_summary_manager") as mock_summary,
        ):
            mock_summary.get_summary.return_value = None
            mock_wf_service.return_value.get_workflow.return_value = workflow_config
            count = WorkflowExecutionService.abort_by_conversation_id("conv-1", MagicMock())

        assert count == (1, 0)
        assert execution.overall_status == WorkflowExecutionStatusEnum.ABORTED
        execution.update.assert_called()


class TestAbortedStatusIsNotOverwritten:
    def test_mark_authentication_required_keeps_aborted(self, service):
        execution = MagicMock()
        execution.overall_status = WorkflowExecutionStatusEnum.ABORTED
        service.workflow_execution = execution

        with patch.object(service, "_refresh_workflow_execution"):
            service.mark_authentication_required("auth payload")

        assert execution.overall_status == WorkflowExecutionStatusEnum.ABORTED
        execution.update.assert_not_called()

    def test_interrupt_keeps_aborted(self, service):
        execution = MagicMock()
        execution.overall_status = WorkflowExecutionStatusEnum.ABORTED
        service.workflow_execution = execution

        with (
            patch.object(service, "_refresh_workflow_execution"),
            patch.object(service, "_send_interrupted_event") as mock_send,
        ):
            service.interrupt("state_b")

        assert execution.overall_status == WorkflowExecutionStatusEnum.ABORTED
        execution.update.assert_not_called()
        mock_send.assert_not_called()


class TestInterruptPropagatesCheckpointState:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    @patch("codemie.service.workflow_execution.workflow_execution_service.request_summary_manager")
    def test_interrupt_passes_checkpoint_state_to_predecessor_method(self, mock_summary, mock_get_states, service):
        service.workflow_execution = MagicMock()
        mock_summary.get_summary.return_value = MagicMock(tokens_usage=None)
        predecessor = _make_state("state_a", WorkflowExecutionStatusEnum.SUCCEEDED)
        predecessor.id = "pred-uuid"
        mock_get_states.return_value = [predecessor]

        checkpoint = {"next": ["state_b"], "messages": []}
        service.thought_queue = None

        with (
            patch.object(service, "_interrupt_predecessor_state", wraps=service._interrupt_predecessor_state) as spy,
            patch.object(service, "record_transition"),
        ):
            service.interrupt("state_b", checkpoint_state=checkpoint)

        spy.assert_called_once_with("state_b", checkpoint)


class TestFinishState:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.find_by_id")
    def test_persists_truncated_on_state(self, mock_find_by_id, service):
        state = MagicMock()
        mock_find_by_id.return_value = state
        service.thought_queue = None

        service.finish_state(
            execution_state_id="exec-state-1",
            output="cut off output",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
            truncated=True,
        )

        assert state.output == "cut off output"
        assert state.truncated is True
        assert state.status == WorkflowExecutionStatusEnum.SUCCEEDED
        state.save.assert_called_once()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.find_by_id")
    def test_defaults_truncated_when_not_passed(self, mock_find_by_id, service):
        state = MagicMock()
        mock_find_by_id.return_value = state
        service.thought_queue = None

        service.finish_state(
            execution_state_id="exec-state-2",
            output="complete output",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
        )

        assert state.truncated is False

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.find_by_id")
    def test_streams_truncated_on_workflow_state_event(self, mock_find_by_id, service):
        state = MagicMock()
        state.id = "exec-state-3"
        state.name = "state_a"
        state.task = "do something"
        state.started_at = None
        mock_find_by_id.return_value = state
        mock_queue = MagicMock()
        service.thought_queue = mock_queue

        service.finish_state(
            execution_state_id="exec-state-3",
            output="cut off output",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
            truncated=True,
        )

        mock_queue.send.assert_called_once()
        payload = json.loads(mock_queue.send.call_args[0][0])
        assert payload["workflow_state"]["truncated"] is True

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.find_by_id")
    def test_returns_early_when_state_not_found(self, mock_find_by_id, service):
        mock_find_by_id.return_value = None
        service.thought_queue = MagicMock()

        service.finish_state(
            execution_state_id="missing-state",
            output="output",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
            truncated=True,
        )  # must not raise

        service.thought_queue.send.assert_not_called()


class TestWorkflowExecutionLockReentrant:
    def test_record_transition_does_not_deadlock_inside_lock(self, service):
        """record_transition must complete when called while workflow_execution_lock is already held."""
        completed = []

        def acquire_and_call():
            with service.workflow_execution_lock:
                service.record_transition(from_state_id=None, to_state_id=None, workflow_context={})
            completed.append(True)

        t = threading.Thread(target=acquire_and_call, daemon=True)
        t.start()
        t.join(timeout=2.0)
        assert len(completed) == 1, "record_transition deadlocked when called inside workflow_execution_lock"


class TestRunAttributesOnMetrics:
    MODULE = "codemie.service.workflow_execution.workflow_execution_service"

    def _service(self, workflow_config, executions):
        with patch(f"{self.MODULE}.WorkflowExecution.get_by_execution_id", side_effect=executions):
            return WorkflowExecutionService(
                workflow_config=workflow_config, workflow_execution_id=EXECUTION_ID, user=MagicMock()
            )

    @patch(f"{MODULE}.request_summary_manager")
    @patch(f"{MODULE}.WorkflowMonitoringService")
    def test_fail_labels_metric_with_execution_attributes(self, mock_monitoring, _summary, workflow_config):
        execution = MagicMock(attributes={"team": "core"}, overall_status=WorkflowExecutionStatusEnum.IN_PROGRESS)
        service = self._service(workflow_config, [[execution]])

        with (
            patch(f"{self.MODULE}.WorkflowExecution.get_by_execution_id", return_value=[execution]),
            patch(f"{self.MODULE}.WorkflowExecutionState.get_all_by_fields", return_value=[]),
            patch.object(service, "_update_assistant_response_in_history"),
        ):
            service.fail(error_class="RuntimeError", error_message="boom")

        assert mock_monitoring.send_workflow_execution_metric.call_args.kwargs["run_attributes"] == {"team": "core"}

    @patch(f"{MODULE}.request_summary_manager")
    @patch(f"{MODULE}.WorkflowMonitoringService")
    def test_finish_labels_metric_with_execution_attributes(self, mock_monitoring, _summary, workflow_config):
        execution = MagicMock(attributes={"team": "core"}, overall_status=WorkflowExecutionStatusEnum.IN_PROGRESS)
        service = self._service(workflow_config, [[execution]])

        with (
            patch(f"{self.MODULE}.WorkflowExecution.get_by_execution_id", return_value=[execution]),
            patch.object(service, "_update_assistant_response_in_history"),
        ):
            service.finish()

        assert mock_monitoring.send_workflow_execution_metric.call_args.kwargs["run_attributes"] == {"team": "core"}

    @patch(f"{MODULE}.request_summary_manager")
    @patch(f"{MODULE}.WorkflowMonitoringService")
    def test_abort_labels_metric_with_execution_attributes(self, mock_monitoring, _summary, workflow_config):
        execution = MagicMock(attributes={"team": "core"}, overall_status=WorkflowExecutionStatusEnum.IN_PROGRESS)
        service = self._service(workflow_config, [[execution]])

        with (
            patch(f"{self.MODULE}.WorkflowExecution.get_by_execution_id", return_value=[execution]),
            patch.object(service, "_cleanup_inflight_states"),
            patch.object(service, "_abort_active_sub_execution"),
        ):
            service.abort()

        assert mock_monitoring.send_workflow_execution_metric.call_args.kwargs["run_attributes"] == {"team": "core"}

    @patch(f"{MODULE}.request_summary_manager")
    @patch(f"{MODULE}.WorkflowMonitoringService")
    def test_metric_flushed_after_row_deletion_keeps_cached_attributes(
        self, mock_monitoring, mock_summary, workflow_config
    ):
        execution = MagicMock(attributes={"team": "core"})
        service = self._service(workflow_config, [[execution]])
        mock_summary.get_summary.return_value = MagicMock(llm_runs=[MagicMock()])

        with patch(f"{self.MODULE}.WorkflowExecution.get_by_execution_id", return_value=[]):
            service.fail(error_class="RuntimeError", error_message="boom")

        call = mock_monitoring.send_workflow_execution_metric.call_args.kwargs
        assert call["workflow_execution_config"] is None
        assert call["run_attributes"] == {"team": "core"}
