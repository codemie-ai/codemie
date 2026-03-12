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

from typing import List, Optional

from codemie.configs import logger
from codemie.rest_api.models.conversation import GeneratedMessage


def materialize_history(history: List[GeneratedMessage], workflow_id: Optional[str] = None) -> List[GeneratedMessage]:
    """
    Materialize workflow execution references in conversation history.

    This function detects GeneratedMessage entries that contain workflow_execution_ref=True
    and replaces them with the actual materialized history from the execution
    (thoughts from workflow execution states and final output).

    Args:
        history: List of GeneratedMessage objects that may contain execution references
        workflow_id: Optional workflow ID to set as assistant_id for materialized messages

    Returns:
        List of GeneratedMessage objects with all references materialized
    """
    if not history:
        return []

    materialized_history = []

    for message in history:
        # Check if this is a workflow execution reference
        if message.workflow_execution_ref and message.execution_id:
            # Materialize the execution reference
            try:
                materialized = _materialize_execution_reference(message, workflow_id)
                materialized_history.append(materialized)
            except Exception as e:
                logger.error(
                    f"Failed to materialize execution reference {message.execution_id}: {e}",
                    exc_info=True,
                    extra={
                        "execution_id": message.execution_id,
                        "history_index": message.history_index,
                    },
                )
                # Keep the original message if materialization fails
                materialized_history.append(message)
        else:
            # Regular message, keep as-is
            materialized_history.append(message)

    return materialized_history


def _materialize_execution_reference(message: GeneratedMessage, workflow_id: Optional[str] = None) -> GeneratedMessage:
    """
    Materialize a single workflow execution reference into full message with thoughts.

    Args:
        message: GeneratedMessage with workflow_execution_ref=True and execution_id
        workflow_id: Optional workflow ID to set as assistant_id

    Returns:
        GeneratedMessage with materialized thoughts and output
    """
    from codemie.service.workflow_service import WorkflowService

    execution_id = message.execution_id

    # Get the workflow execution
    execution = WorkflowService.find_workflow_execution_by_id(execution_id)
    if not execution:
        logger.warning(f"Workflow execution {execution_id} not found, keeping reference as-is")
        return message

    # Get thoughts from workflow execution states
    thoughts = _get_execution_thoughts(execution_id)

    # Get final output from execution
    final_output = execution.output or ""

    # If no explicit message and we have thoughts, use the last thought's message
    if not final_output and thoughts:
        final_output = thoughts[-1].get("message", "")

    # Create materialized message
    materialized = GeneratedMessage(
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
        # Preserve reference fields for tracking
        workflow_execution_ref=True,
        execution_id=execution_id,
    )

    return materialized


def _normalize_children(thought: dict) -> dict:
    """
    Recursively normalize the children field to ensure it's always a list.

    Args:
        thought: Dictionary representing a thought that may have None as children

    Returns:
        The same thought dictionary with children normalized to a list
    """
    if thought.get('children') is None:
        thought['children'] = []

    # Recursively normalize nested children
    if thought['children']:
        thought['children'] = [_normalize_children(child) for child in thought['children']]

    return thought


def _get_execution_thoughts(execution_id: str) -> List[dict]:
    """
    Retrieve thoughts from workflow execution states.

    Args:
        execution_id: The workflow execution ID

    Returns:
        List of Thought objects with state information
    """
    from codemie.core.workflow_models import WorkflowExecutionState, WorkflowExecutionStateThought

    try:
        # Get all states for this execution
        states = WorkflowExecutionState.get_all_by_fields(fields={"execution_id.keyword": execution_id})

        # Get thoughts for each state
        state_ids = [state.id for state in states]
        if not state_ids:
            return []

        state_thoughts = WorkflowExecutionStateThought.get_root(state_ids=state_ids, include_children_field=True)

        # Convert to Thought format expected by frontend
        thoughts = []
        for thought in state_thoughts:
            thought_dict = thought.model_dump()
            # Map WorkflowExecutionStateThought fields to Thought fields
            thought_obj = {
                'id': thought_dict.get('id'),
                'author_name': thought_dict.get('author_name'),
                'author_type': thought_dict.get('author_type'),
                'message': thought_dict.get('content', ''),
                'input_text': thought_dict.get('input_text'),
                'children': thought_dict.get('children', []),
                'in_progress': False,  # Historical thoughts are never in progress
            }
            # Normalize children recursively to ensure no None values
            thoughts.append(_normalize_children(thought_obj))

        return thoughts
    except Exception as e:
        logger.error(f"Failed to get thoughts for execution {execution_id}: {e}", exc_info=True)
        return []
