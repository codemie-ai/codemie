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

from typing import Any, Optional
from datetime import datetime
import threading

from codemie.configs import logger
from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowExecution,
    WorkflowExecutionStatusEnum,
    WorkflowExecutionState,
)
from codemie.core.thread import MessageQueue
from codemie.rest_api.security.user import User
from codemie.service.request_summary_manager import request_summary_manager
from codemie.service.monitoring.workflow_monitoring_service import WorkflowMonitoringService

REFRESH_WAIT_FOR = 'wait_for'
EXECUTION_ID_KEYWORD = 'execution_id.keyword'


class WorkflowExecutionService:
    def __init__(
        self,
        workflow_config: WorkflowConfig,
        workflow_execution_id: str,
        user: User,
        thought_queue: Optional[MessageQueue] = None,
    ):
        self.workflow_config = workflow_config
        self.user = user
        self.workflow_execution_id = workflow_execution_id
        self.workflow_execution_lock = threading.Lock()
        self.thought_queue = thought_queue  # For streaming state events
        self._refresh_workflow_execution()

    def fail(self, error_class: str, error_message: str):
        with self.workflow_execution_lock:
            self._refresh_workflow_execution()
            if not self.workflow_execution:
                logger.error(
                    f"Workflow execution not found for execution_id: {self.workflow_execution_id}. "
                    "Cannot mark workflow as failed."
                )
                return

            logger.warning(
                f"Overall status for Execution ID: {self.workflow_execution.execution_id} "
                f"set to {WorkflowExecutionStatusEnum.FAILED}, Reason = {error_message}"
            )

            self.workflow_execution.overall_status = WorkflowExecutionStatusEnum.FAILED
            self.workflow_execution.tokens_usage = self._calculate_tokens_usage(self.workflow_execution_id)
            self.workflow_execution.output = error_message

            # Update assistant response in history
            self._update_assistant_response_in_history(error_message)

            self.workflow_execution.update(refresh=True)

        WorkflowMonitoringService.send_workflow_execution_metric(
            workflow_config=self.workflow_config,
            workflow_execution_config=self.workflow_execution,
            user=self.user,
            additional_attributes={"error_class": error_class, "error_cause": error_message},
        )
        request_summary_manager.clear_summary(self.workflow_execution_id)

    def abort(self):
        with self.workflow_execution_lock:
            self._refresh_workflow_execution()
            if not self.workflow_execution:
                logger.error(
                    f"Workflow execution not found for execution_id: {self.workflow_execution_id}. "
                    "Cannot abort workflow."
                )
                return

            logger.warning(
                f"Overall status for Execution ID: {self.workflow_execution.execution_id} "
                f"set to {WorkflowExecutionStatusEnum.ABORTED}"
            )
            self.workflow_execution.overall_status = WorkflowExecutionStatusEnum.ABORTED
            self.workflow_execution.tokens_usage = self._calculate_tokens_usage(self.workflow_execution_id)
            self.workflow_execution.update(refresh=True)

            states = WorkflowExecutionState.get_all_by_fields(fields={EXECUTION_ID_KEYWORD: self.workflow_execution_id})
            for state in states:
                if state.status in (WorkflowExecutionStatusEnum.IN_PROGRESS, WorkflowExecutionStatusEnum.INTERRUPTED):
                    state.status = WorkflowExecutionStatusEnum.ABORTED
                    state.save()

        WorkflowMonitoringService.send_workflow_execution_metric(
            workflow_config=self.workflow_config, workflow_execution_config=self.workflow_execution, user=self.user
        )

    def interrupt(self, interrupted_state: str):
        with self.workflow_execution_lock:
            self._refresh_workflow_execution()
            if not self.workflow_execution:
                logger.error(
                    f"Workflow execution not found for execution_id: {self.workflow_execution_id}. "
                    "Cannot interrupt workflow."
                )
                return

            self.workflow_execution.overall_status = WorkflowExecutionStatusEnum.INTERRUPTED
            self.workflow_execution.tokens_usage = self._calculate_tokens_usage(self.workflow_execution_id)
            self.workflow_execution.update(refresh=True)

            logger.warning(
                f"Overall status for Execution ID: {self.workflow_execution.execution_id} "
                f"set to {WorkflowExecutionStatusEnum.INTERRUPTED}"
            )

            if interrupted_state:
                self._interrupt_predecessor_state(interrupted_state)

    def resume_states(self):
        states = WorkflowExecutionState.get_all_by_fields(fields={EXECUTION_ID_KEYWORD: self.workflow_execution_id})
        for state in states:
            if state.status == WorkflowExecutionStatusEnum.INTERRUPTED:
                state.status = WorkflowExecutionStatusEnum.SUCCEEDED
                state.save()

    def finish(self):
        with self.workflow_execution_lock:
            self._refresh_workflow_execution()
            if not self.workflow_execution:
                logger.error(
                    f"Workflow execution not found for execution_id: {self.workflow_execution_id}. "
                    "Cannot mark workflow as finished."
                )
                return

            if self.workflow_execution.overall_status == WorkflowExecutionStatusEnum.ABORTED:
                return

            logger.info(
                f"Overall status for Execution ID: {self.workflow_execution.execution_id} "
                f"set to {WorkflowExecutionStatusEnum.SUCCEEDED}"
            )
            self.workflow_execution.tokens_usage = self._calculate_tokens_usage(
                self.workflow_execution_id,
            )

            self.workflow_execution.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED

            # Update assistant response in history with final output
            self._update_assistant_response_in_history(self.workflow_execution.output)

            self.workflow_execution.update(refresh=True)

        WorkflowMonitoringService.send_workflow_execution_metric(
            workflow_config=self.workflow_config,
            workflow_execution_config=self.workflow_execution,
            user=self.user,
            request_id=self.workflow_execution_id,
        )
        request_summary_manager.clear_summary(self.workflow_execution_id)

    def start_state(
        self,
        workflow_state_id: str,
        task: Any,
        preceding_state_id: Optional[str] = None,
        state_id: Optional[str] = None,
    ) -> str:
        with self.workflow_execution_lock:
            started_at = datetime.now()
            state = WorkflowExecutionState(
                execution_id=self.workflow_execution_id,
                name=workflow_state_id,
                state_id=state_id or workflow_state_id,
                task=str(task),
                status=WorkflowExecutionStatusEnum.IN_PROGRESS,
                started_at=started_at,
                preceding_state_id=preceding_state_id,
            )
            state.save()

            # Stream state start event to client
            if self.thought_queue:
                from codemie.chains.base import StreamedGenerationResult, WorkflowStateEvent

                state_event = WorkflowStateEvent(
                    id=state.id,
                    name=workflow_state_id,
                    task=str(task),
                    status=WorkflowExecutionStatusEnum.IN_PROGRESS.value,
                    event_type="state_start",
                    started_at=started_at.isoformat(),
                )
                result = StreamedGenerationResult(workflow_state=state_event)
                self.thought_queue.send(result.model_dump_json())

            return state.id

    def abort_state(self, execution_state_id: str):
        state = WorkflowExecutionState.get_by_id(id_=execution_state_id)
        state.status = WorkflowExecutionStatusEnum.ABORTED
        state.save()

    def finish_state(self, execution_state_id: str, output: str, status: WorkflowExecutionStatusEnum):
        with self.workflow_execution_lock:
            self._refresh_workflow_execution()
            if self.workflow_execution:
                self.workflow_execution.tokens_usage = self._calculate_tokens_usage(
                    self.workflow_execution_id,
                )
                self.workflow_execution.update(refresh=REFRESH_WAIT_FOR)
            else:
                logger.warning(
                    f"Workflow execution not found for execution_id: {self.workflow_execution_id}. "
                    "Skipping tokens_usage update."
                )

            state = WorkflowExecutionState.get_by_id(id_=execution_state_id)
            state.output = output
            state.status = status
            completed_at = datetime.now()
            state.completed_at = completed_at

            state.save()
            print_output = output or ""
            print_output = print_output if len(print_output) <= 100 else f"{print_output[0:50]}...{print_output[:-50]}"
            logger.debug(f"Successfuly saved state execution {execution_state_id} with output: {print_output}")

            # Stream state finish event to client
            if self.thought_queue:
                from codemie.chains.base import StreamedGenerationResult, WorkflowStateEvent

                state_event = WorkflowStateEvent(
                    id=state.id,
                    name=state.name,
                    task=state.task,
                    output=output,  # Include the actual output/result
                    status=status.value,
                    event_type="state_finish",
                    started_at=state.started_at.isoformat() if state.started_at else None,
                    completed_at=completed_at.isoformat(),
                )
                result = StreamedGenerationResult(workflow_state=state_event)
                self.thought_queue.send(result.model_dump_json())

    def record_transition(
        self,
        from_state_id: Optional[str],
        to_state_id: str,
        workflow_context: dict,
    ) -> Optional[str]:
        """Record a workflow node transition with context snapshot.

        Persists a WorkflowExecutionTransition record and emits a streaming
        transition event when thought_queue is set. This method is non-raising:
        failures are logged but execution continues.

        Args:
            from_state_id: Source state ID that completed execution
            to_state_id: Target state ID to be executed next
            workflow_context: Serialized LangGraph state snapshot (JSON-safe dict)

        Returns:
            str: The new transition record ID on success, None if persistence fails
        """
        from codemie.core.workflow_models import WorkflowExecutionTransition

        try:
            with self.workflow_execution_lock:
                transition = WorkflowExecutionTransition(
                    execution_id=self.workflow_execution_id,
                    from_state_id=from_state_id,
                    to_state_id=to_state_id,
                    workflow_context=workflow_context,
                )
                transition.save()

                logger.debug(
                    f"Recorded workflow transition: {from_state_id} → {to_state_id} "
                    f"(execution_id={self.workflow_execution_id})"
                )

                return transition.id

        except Exception as e:
            # Non-raising: observability failures should never kill workflow execution
            logger.error(
                f"Failed to record workflow transition {from_state_id} → {to_state_id} "
                f"for execution_id={self.workflow_execution_id}: {e}",
                exc_info=True,
            )
            return None

    def _refresh_workflow_execution(self):
        self.workflow_execution = self.find_workflow_execution(self.workflow_execution_id)

    @staticmethod
    def find_workflow_execution(workflow_execution_id: str):
        try:
            execution = WorkflowExecution.get_by_execution_id(workflow_execution_id)
            return execution[0] if execution else None
        except Exception as e:
            logger.error(f"Failed to get workflow execution, execution_id: {workflow_execution_id}")
            raise e

    @staticmethod
    def _calculate_tokens_usage(workflow_execution_id: str):
        return request_summary_manager.get_summary(workflow_execution_id).tokens_usage

    def _update_assistant_response_in_history(self, assistant_response: str | None):
        """
        Update the assistant response in WorkflowExecution.history.

        This updates WorkflowExecution.history which is used for:
        1. Standalone workflow executions (not part of conversations)
        2. Direct retrieval of workflow execution results via API
        3. Backward compatibility with existing production usage

        For workflow chat conversations, the history is also stored in Conversation.history
        as a reference and materialized on retrieval from WorkflowExecutionState and
        WorkflowExecutionStateThought tables.

        This method finds the assistant message in the history (created during execution
        start) and updates it with the final output, thoughts, and token usage.

        Args:
            assistant_response: The final assistant response/output from workflow execution
        """
        from codemie.core.constants import ChatRole

        if not self.workflow_execution.history:
            logger.warning(f"No history found for workflow execution {self.workflow_execution_id}")
            return

        # Find the last assistant message in history
        assistant_message = None
        for message in reversed(self.workflow_execution.history):
            if message.role == ChatRole.ASSISTANT.value:
                assistant_message = message
                break

        if not assistant_message:
            logger.warning(f"No assistant message found in history for workflow execution {self.workflow_execution_id}")
            return

        # Update the assistant message with the final output
        assistant_message.message = assistant_response or ""

        # Get thoughts from workflow execution states
        thoughts = self._get_thoughts_from_states()
        assistant_message.thoughts = thoughts

        # Update tokens usage
        if self.workflow_execution.tokens_usage:
            assistant_message.input_tokens = self.workflow_execution.tokens_usage.input_tokens
            assistant_message.output_tokens = self.workflow_execution.tokens_usage.output_tokens
            assistant_message.money_spent = self.workflow_execution.tokens_usage.money_spent

        # Update response time
        if assistant_message.date:
            response_time = (datetime.now() - assistant_message.date).total_seconds()
            assistant_message.response_time = response_time

        logger.debug(
            f"Updated assistant response in history for execution {self.workflow_execution_id}, "
            f"thoughts count: {len(thoughts)}"
        )

    def _interrupt_predecessor_state(self, interrupted_state_id: str) -> None:
        """Marks states that transition directly into the interrupted state as INTERRUPTED."""
        predecessor_ids = {s.id for s in self.workflow_config.states if interrupted_state_id in s.next.leads_to()}
        states = WorkflowExecutionState.get_all_by_fields(fields={EXECUTION_ID_KEYWORD: self.workflow_execution_id})
        for state in states:
            if state.name in predecessor_ids and state.status == WorkflowExecutionStatusEnum.SUCCEEDED:
                state.status = WorkflowExecutionStatusEnum.INTERRUPTED
                state.save()

    def _get_thoughts_from_states(self):
        """
        Retrieve thoughts from all workflow execution states.

        Returns:
            List of thought dictionaries with workflow state information
        """
        from codemie.core.workflow_models import WorkflowExecutionStateThought

        thoughts = []
        try:
            # Get all states for this execution
            states = WorkflowExecutionState.get_all_by_fields(fields={EXECUTION_ID_KEYWORD: self.workflow_execution_id})

            # Get thoughts for each state
            state_ids = [state.id for state in states]
            if state_ids:
                state_thoughts = WorkflowExecutionStateThought.get_root(
                    state_ids=state_ids, include_children_field=True
                )

                # Convert to thought dictionaries
                for thought in state_thoughts:
                    thoughts.append(
                        {
                            "id": thought.id,
                            "message": thought.content,
                            "author_name": thought.author_name,
                            "author_type": thought.author_type,
                            "in_progress": False,
                        }
                    )

        except Exception as e:
            logger.error(
                f"Failed to retrieve thoughts for workflow execution {self.workflow_execution_id}: {e}",
                exc_info=True,
            )

        return thoughts
