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
Service for materializing workflow execution references in conversation history.

This module provides functionality to resolve workflow execution references stored
in conversation history into their full materialized form (thoughts, output, etc.).
"""

from dataclasses import dataclass, field
from typing import List, Optional

from codemie.configs import logger
from codemie.core.workflow_models import WorkflowExecution, WorkflowExecutionStatusEnum
from codemie.rest_api.models.conversation import GeneratedMessage

_VISIBLE_WITHOUT_OUTPUT = {
    WorkflowExecutionStatusEnum.IN_PROGRESS,
    WorkflowExecutionStatusEnum.ABORTED,
    WorkflowExecutionStatusEnum.FAILED,
    WorkflowExecutionStatusEnum.INTERRUPTED,
}


@dataclass
class MaterializedConversation:
    history: list[GeneratedMessage] = field(default_factory=list)


def materialize_workflow_conversation(
    history: List[GeneratedMessage], workflow_id: Optional[str] = None
) -> MaterializedConversation:
    """
    Materialize workflow execution references in conversation history and capture
    the status of the last execution.

    Detects GeneratedMessage entries with workflow_execution_ref=True and replaces
    them with the actual materialized content (thoughts, output, tokens) from the
    execution.

    Args:
        history: List of GeneratedMessage objects that may contain execution references
        workflow_id: Optional workflow ID to set as assistant_id for materialized messages

    Returns:
        MaterializedConversation with resolved history
    """
    if not history:
        return MaterializedConversation()

    materialized_history = []

    for message in history:
        if message.workflow_execution_ref and message.execution_id:
            try:
                materialized = _materialize_execution_reference(message, workflow_id)
                materialized_history.append(materialized)
            except Exception as e:
                logger.error(
                    f"Failed to materialize execution reference {message.execution_id}: {e}",
                    exc_info=True,
                )
                materialized_history.append(message)
        else:
            materialized_history.append(message)

    return MaterializedConversation(history=materialized_history)


def _materialize_execution_reference(message: GeneratedMessage, workflow_id: Optional[str] = None) -> GeneratedMessage:
    """
    Materialize a single workflow execution reference into full message with thoughts.

    Args:
        message: GeneratedMessage with workflow_execution_ref=True and execution_id
        workflow_id: Optional workflow ID to set as assistant_id

    Returns:
        Materialized GeneratedMessage
    """
    from codemie.service.workflow_service import WorkflowService

    execution_id = message.execution_id

    execution = WorkflowService.find_workflow_execution_by_id(execution_id)
    if not execution:
        logger.warning(f"Workflow execution {execution_id} not found, keeping reference as-is")
        return message

    thoughts = _get_execution_thoughts(execution_id, history_index=message.history_index)
    final_output = _resolve_execution_output(execution, thoughts, execution_id)

    return GeneratedMessage(
        role=message.role,
        message=final_output,
        history_index=message.history_index,
        date=message.date or execution.update_date or execution.date,
        assistant_id=workflow_id or message.assistant_id,
        thoughts=thoughts,
        response_time=message.response_time,
        input_tokens=execution.tokens_usage.input_tokens if execution.tokens_usage else None,
        output_tokens=execution.tokens_usage.output_tokens if execution.tokens_usage else None,
        money_spent=execution.tokens_usage.money_spent if execution.tokens_usage else None,
        workflow_execution_ref=True,
        execution_id=execution_id,
        execution_status=execution.overall_status,
    )


def _resolve_execution_output(execution: WorkflowExecution, thoughts: List[dict], execution_id: str) -> str:
    """Return hydrated assistant text, empty while the workflow run is still in progress."""
    run_in_progress = execution.overall_status == WorkflowExecutionStatusEnum.IN_PROGRESS or any(
        thought.get("in_progress") for thought in thoughts
    )
    if run_in_progress:
        return ""

    final_output = execution.output or ""
    if not final_output and thoughts:
        final_output = thoughts[-1].get("message", "")
    if not final_output:
        final_output = _get_last_completed_state_output(execution_id) or ""
    return final_output


def _get_last_completed_state_output(execution_id: str) -> Optional[str]:
    from codemie.core.workflow_models import WorkflowExecutionState

    try:
        states = WorkflowExecutionState.get_all_by_fields(
            fields={"execution_id.keyword": execution_id}, order_by="date"
        )
        return next((s.output for s in reversed(states) if s.output), None)
    except Exception as e:
        logger.error(f"Failed to get last state output for execution {execution_id}: {e}", exc_info=True)
        return None


def _get_execution_thoughts(execution_id: str, history_index: Optional[int] = None) -> List[dict]:
    """
    Retrieve thoughts for a workflow execution ordered by creation time.

    Includes completed states with output and states that are still in progress,
    aborted, failed, or interrupted even when output is empty.

    Args:
        execution_id: The workflow execution ID
        history_index: When provided, return only states tagged with this turn index.
            Falls back to all states when no states carry a history_index (legacy data).

    Returns:
        List of thought dicts, one per visible execution state
    """
    from codemie.core.workflow_models import WorkflowExecutionState

    try:
        states = WorkflowExecutionState.get_all_by_fields(
            fields={"execution_id.keyword": execution_id}, order_by="date"
        )

        if history_index is not None and any(s.history_index is not None for s in states):
            filtered = [s for s in states if s.history_index == history_index]
            if filtered:
                states = filtered

        return [
            {
                "id": state.id,
                "author_name": state.name,
                "author_type": "WorkflowState",
                "message": state.output or "",
                "input_text": state.task or None,
                "children": [],
                "in_progress": state.status == WorkflowExecutionStatusEnum.IN_PROGRESS,
                "interrupted": state.status == WorkflowExecutionStatusEnum.INTERRUPTED,
                "aborted": state.status == WorkflowExecutionStatusEnum.ABORTED,
            }
            for state in states
            if state.output or state.status in _VISIBLE_WITHOUT_OUTPUT
        ]
    except Exception as e:
        logger.error(f"Failed to get thoughts for execution {execution_id}: {e}", exc_info=True)
        return []
