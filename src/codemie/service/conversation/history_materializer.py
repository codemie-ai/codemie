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
from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.rest_api.models.conversation import GeneratedMessage


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

    thoughts = _get_execution_thoughts(execution_id)

    final_output = execution.output or ""
    if not final_output and thoughts:
        final_output = thoughts[-1].get("message", "")

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
    )


def _get_execution_thoughts(execution_id: str) -> List[dict]:
    """
    Retrieve thoughts for a workflow execution ordered by creation time.

    Args:
        execution_id: The workflow execution ID

    Returns:
        List of thought dicts, one per state that has an output
    """
    from codemie.core.workflow_models import WorkflowExecutionState

    try:
        states = WorkflowExecutionState.get_all_by_fields(
            fields={"execution_id.keyword": execution_id}, order_by="date"
        )
        return [
            {
                "id": state.id,
                "author_name": state.name,
                "author_type": "WorkflowState",
                "message": state.output or "",
                "input_text": None,
                "children": [],
                "in_progress": False,
                "interrupted": state.status == WorkflowExecutionStatusEnum.INTERRUPTED,
                "aborted": state.status == WorkflowExecutionStatusEnum.ABORTED,
            }
            for state in states
            if state.output
        ]
    except Exception as e:
        logger.error(f"Failed to get thoughts for execution {execution_id}: {e}", exc_info=True)
        return []
