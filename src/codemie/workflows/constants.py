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

from enum import Enum
from pydantic import BaseModel


class WorkflowErrorType(str, Enum):
    """Enum for workflow validation error types."""

    PARSING = "parsing"
    WORKFLOW_SCHEMA = "workflow_schema"
    MISSING_STATES = "missing_states"
    SCHEMA_VALIDATION = "schema_validation"
    CROSS_REFERENCE_VALIDATION = "cross_reference_validation"
    RESOURCE_VALIDATION = "resource_validation"


class WorkflowErrorResourceType(str, Enum):
    """Enum for workflow resource types in validation errors."""

    ASSISTANT = "assistant"
    TOOL = "tool"
    TOOL_FROM_ASSISTANT = "tool_from_assistant"
    DATASOURCE = "datasource"
    STATE = "state"
    CUSTOM_NODE = "custom_node"


class WorkflowValidationError(BaseModel):
    """Unified error format for workflow validation errors."""

    resource_type: str
    resource_id: str | None = None
    reference_state: str | None = None
    message: str


NEXT_KEY: str = "next"
RESULT_PREFIX = "Result:"
TRIPLE_BACKTICKS = "```"
TRIPLE_TILDES = "~~~"
RESULT_FINALIZER_NODE: str = "result_finalizer_node"
SUMMARIZE_MEMORY_NODE: str = "summarize_memory_node"
SUPERVISOR_FINISH_STEP = "FINISH"
SUPERVISOR_NODE = "Supervisor"
MESSAGES_VARIABLE: str = "messages"
FIRST_STATE_IN_ITERATION: str = "first_state_in_iteration"
USER_INPUT: str = "user_input"
CONTEXT_STORE_VARIABLE: str = "context_store"
ITER_SOURCE: str = "iteration_source"
TASK_KEY: str = "task"
CURRENT_TASK_KEY: str = "current_task_key"
ITERATION_NODE_NUMBER_KEY: str = "iteration_node_number"
TOTAL_ITERATIONS_KEY: str = "total_iterations"
END_NODE: str = "end"
END_STATE: str = "__end__"
RECURSION_LIMIT: int = 50
MESSAGES_LIMIT: int = 25
MESSAGES_TOKENS_LIMIT: int = 50000
MAX_TOKENS_LIMIT: int = 100000
STATE_MISSING_ERR: str = "Workflow must have at least one valid state"
ABORTED_MSG: str = "Execution aborted by user"
NEXT_CMD_STATE: str = "next_command_state"
CLEAR_CONTEXT_STORE_KEEP_CURRENT: str = "keep_current"
CONTEXT_STORE_KEEP_NEW_ONLY_FLAG: str = "context_store_keep_new_only"
GUARDRAIL_CHECKED_FLAG: str = "_guardrail_checked"
PREVIOUS_EXECUTION_STATE_ID: str = "previous_execution_state_id"
PREVIOUS_EXECUTION_STATE_NAME: str = "previous_execution_state_name"
