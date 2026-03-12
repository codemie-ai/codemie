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
Service for formatting workflow execution history for chat context.

This module provides functionality to retrieve and format previous workflow executions
from a conversation, creating a structured markdown string that can be injected into
new workflow executions to provide conversational context.
"""

from typing import List
from datetime import datetime

from codemie.configs import logger
from codemie.core.workflow_models import (
    WorkflowExecution,
    WorkflowExecutionState,
    WorkflowExecutionStatusEnum,
)
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage


def _get_execution_ids_from_conversation(conversation_id: str, workflow_id: str) -> List[str]:
    """
    Extract execution IDs from conversation history.

    Args:
        conversation_id: The conversation ID
        workflow_id: The workflow ID for logging

    Returns:
        List of execution IDs found in conversation history
    """
    conversation = Conversation.find_by_id(conversation_id)
    if not conversation or not conversation.history:
        logger.debug(
            "No conversation history found for conversation_id: %s",
            conversation_id,
            extra={"workflow_id": workflow_id},
        )
        return []

    execution_ids = []
    for message in conversation.history:
        if isinstance(message, GeneratedMessage) and message.execution_id:
            execution_ids.append(message.execution_id)

    if not execution_ids:
        logger.debug(
            "No workflow executions found in conversation history",
            extra={"workflow_id": workflow_id},
        )

    return execution_ids


def _get_completed_executions(execution_ids: List[str], workflow_id: str) -> List[WorkflowExecution]:
    """
    Query and filter workflow executions to only completed ones.

    Args:
        execution_ids: List of execution IDs from conversation
        workflow_id: The workflow ID to filter executions

    Returns:
        List of completed workflow executions, sorted by date (oldest first)
    """
    if not execution_ids:
        return []

    # Query workflow executions for this workflow
    all_executions = WorkflowExecution.get_by_workflow_id(workflow_id)

    # Filter to only executions in conversation history
    conversation_executions = [execution for execution in all_executions if execution.execution_id in execution_ids]

    # Filter to only completed executions
    completed_executions = [
        execution
        for execution in conversation_executions
        if execution.overall_status
        not in (WorkflowExecutionStatusEnum.IN_PROGRESS, WorkflowExecutionStatusEnum.NOT_STARTED)
    ]

    if not completed_executions:
        logger.debug(
            "No completed workflow executions found in conversation",
            extra={
                "workflow_id": workflow_id,
                "total_executions": len(conversation_executions),
            },
        )
        return []

    # Sort by date (oldest first)
    completed_executions.sort(key=lambda x: x.date if x.date else datetime.min)

    return completed_executions


def _get_workflow_output(execution: WorkflowExecution) -> str:
    """
    Get workflow output from execution, falling back to last state output if needed.

    For historical reasons, WorkflowExecution.output is often empty,
    and the real output is stored in the last workflow execution state.

    Args:
        execution: The workflow execution

    Returns:
        The workflow output string, or 'N/A' if not available
    """
    if execution.output:
        return execution.output

    # Try to get output from the last state
    states = WorkflowExecutionState.get_all_by_fields({"execution_id.keyword": execution.execution_id})
    if states:
        # Sort by date (latest first) and get the last state's output
        states.sort(key=lambda s: s.date if s.date else datetime.min, reverse=True)
        return states[0].output if states[0].output else 'N/A'

    return 'N/A'


def _format_executions_as_markdown(executions: List[WorkflowExecution]) -> str:
    """
    Format workflow executions into structured markdown.

    Shows only the user input and the final workflow output (from last execution state)
    for each execution, without intermediate steps.

    Args:
        executions: List of workflow executions to format

    Returns:
        Formatted markdown string with simple input/output pairs
    """
    history_parts = ["# Previous Workflow Executions\n"]

    for idx, execution in enumerate(executions, 1):
        workflow_output = _get_workflow_output(execution)

        history_parts.append(f"\n## Execution {idx}")
        history_parts.append(f"**User Input:** {execution.prompt or 'N/A'}")
        history_parts.append(f"**Workflow Output:** {workflow_output}\n")

    history_parts.append("\n## Current Request")

    return "\n".join(history_parts)


def format_execution_history(conversation_id: str, workflow_id: str) -> str:
    """
    Retrieves and formats all previous workflow executions for a conversation.

    This function queries the conversation history to find all workflow executions,
    retrieves their full details, and formats them into a structured markdown string
    that can be prepended to new workflow inputs.

    Args:
        conversation_id: The ID of the conversation containing the workflow executions
        workflow_id: The workflow ID to filter executions

    Returns:
        A structured markdown string containing the execution history, or empty string
        if no previous executions exist or an error occurs.

    Format:
        # Previous Workflow Executions

        ## Execution 1
        **User Input:** What's the weather?
        **Workflow Output:** The weather is sunny with 75°F.

        ## Execution 2
        **User Input:** Should I bring an umbrella?
        **Workflow Output:** No, you don't need an umbrella today.

        ## Current Request
    """
    try:
        # Get execution IDs from conversation
        execution_ids = _get_execution_ids_from_conversation(conversation_id, workflow_id)
        if not execution_ids:
            return ""

        # Get completed executions
        completed_executions = _get_completed_executions(execution_ids, workflow_id)
        if not completed_executions:
            return ""

        # Format into structured markdown
        history_markdown = _format_executions_as_markdown(completed_executions)

        logger.info(
            "Formatted execution history for workflow chat",
            extra={
                "workflow_id": workflow_id,
                "execution_count": len(completed_executions),
                "history_length": len(history_markdown),
            },
        )

        return history_markdown

    except Exception as e:
        logger.error(
            "Failed to format execution history: %s",
            e,
            extra={"workflow_id": workflow_id},
            exc_info=True,
        )
        # Return empty string on error - workflow can still execute without history
        return ""
