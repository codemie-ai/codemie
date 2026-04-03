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

from __future__ import annotations

import httpx
import requests
from enum import Enum
from typing import List, Optional, Literal

from langgraph.types import default_retry_on
from pydantic import BaseModel, Field
from pydantic import model_validator

from codemie_tools.base.errors import InvalidCredentialsError, TruncatedOutputError

from codemie.core.exceptions import TaskException
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem


class WorkflowMode(str, Enum):
    AUTONOMOUS = "Autonomous"  # Deprecated
    SEQUENTIAL = "Sequential"


class WorkflowErrorFormat(str, Enum):
    """Error format for workflow validation responses."""

    STRING = "string"
    JSON = "json"


class WorkflowAssistantTool(BaseModel):
    name: str
    integration_alias: Optional[str] = None


class WorkflowAssistant(BaseModel):
    id: Optional[str] = None
    assistant_id: Optional[str] = None
    name: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    system_prompt: Optional[str] = None
    limit_tool_output_tokens: Optional[int] = Field(10000)
    tools: list[WorkflowAssistantTool] = Field(default_factory=list)
    datasource_ids: Optional[list[str]] = Field(default_factory=list)
    exclude_extra_context_tools: bool = False
    mcp_servers: list[MCPServerDetails] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_mcp_servers(cls, values: WorkflowAssistant) -> WorkflowAssistant:
        """Validate MCP server configurations"""
        for mcp_server in values.mcp_servers:
            if mcp_server.enabled:
                # Check if at least one configuration method is provided
                has_config = mcp_server.config is not None
                has_command = mcp_server.command is not None

                if not (has_config or has_command):
                    raise ValueError(
                        f"MCP server '{mcp_server.name}' is enabled but missing configuration. "
                        f"Please provide either 'config' or 'command'."
                    )
        return values

    @model_validator(mode='after')
    def validate_temperature(cls, values: WorkflowAssistant) -> WorkflowAssistant:
        """Validate LLM temperature settings (if present)"""

        if values.temperature is None:
            return values

        if not isinstance(values.temperature, float):
            raise ValueError('Expected the temperature to be a float number in 0..2 range')

        if values.temperature < 0 or values.temperature > 2:
            raise ValueError(f"Temperature value should be 0..2, but set: '{values.temperature}'")

        return values


class WorkflowTool(BaseModel):
    id: str
    tool: str
    tool_args: Optional[dict] = None
    integration_alias: Optional[str] = None
    tool_result_json_pointer: Optional[str] = None
    trace: bool = False
    mcp_server: Optional[MCPServerDetails] = None
    resolve_dynamic_values_in_response: bool = False


class CustomWorkflowNode(BaseModel):
    id: str
    custom_node_id: str
    name: Optional[str] = Field(default_factory=str)
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    config: dict = Field(default_factory=dict)

    @model_validator(mode='before')
    def set_custom_node_id(cls, values: dict) -> dict:
        if not values.get('custom_node_id'):
            values['custom_node_id'] = values.get('id')
        return values

    @model_validator(mode='after')
    def validate_exclusive_filters(cls, values: CustomWorkflowNode) -> CustomWorkflowNode:
        if values.config.get("documents_filtering_pattern") and values.config.get("documents_filter"):
            raise ValueError("Only one of 'documents_filtering_pattern' or 'documents_filter' must be provided.")
        return values


class WorkflowStateSwitchCondition(BaseModel):
    condition: str
    state_id: str


class WorkflowStateSwitch(BaseModel):
    cases: list[WorkflowStateSwitchCondition]
    default: str


class WorkflowStateCondition(BaseModel):
    expression: str
    then: str
    otherwise: str


class WorkflowNextState(BaseModel):
    state_id: Optional[str] = None
    state_ids: Optional[list] = None
    iter_key: Optional[str] = None
    keep_history: bool = True  # Deprecated: Use include_in_llm_history instead
    override_task: bool = False
    condition: Optional[WorkflowStateCondition] = None
    switch: Optional[WorkflowStateSwitch] = None
    output_key: Optional[str] = None

    # Context Store Configuration Flags
    store_in_context: bool = True
    """
    If true, the state outcome will be stored in the context store.
    Default: True

    When false, the state outcome will not be added to context_store,
    but may still be added to message history if include_in_llm_history is true.
    """

    include_in_llm_history: bool = True
    """
    If true, the state outcome from context store will be included in the
    message history sent to the LLM.
    Default: True

    When false, the outcome is stored in context but not sent to LLM.

    This replaces the deprecated keep_history field.
    """

    clear_prior_messages: bool = False
    """
    If true, all prior messages in the message history will be excluded
    from the message history sent to the LLM starting from this state.
    Default: False

    This effectively creates a "fresh start" for LLM context while preserving
    the full context_store for dynamic expression resolution.
    """

    clear_context_store: bool | Literal["keep_current"] = False
    """
    Controls context store clearing behavior after this state executes.

    Supported values:
    - False (default): Don't clear context store, merge new values with existing context
    - True: Clear entire context store (all keys removed)
    - "keep_current": Keep only new values from this state, discard all previous context

    This allows resetting the context while optionally preserving message history.
    The "keep_current" mode is useful when you want a fresh context without carrying
    forward old state, but still want to populate new values.
    """

    reset_keys_in_context_store: list[str] | None = None
    """
    List of specific keys to remove from the context store during state transition.
    Default: None (no keys are reset)

    When specified, only the listed keys will be removed from the context store,
    while all other keys remain preserved. This provides granular control over
    context cleanup without clearing the entire store.

    Keys that don't exist in the context store are silently ignored.
    If a reset key is also present in the current state's output, it will be
    re-added with the new value.

    Example:
        reset_keys_in_context_store: ["temp_data", "intermediate_result"]
        # Only removes "temp_data" and "intermediate_result", keeps all other keys
    """

    include_in_iterator_context: list[str] = Field(default=["*"])
    """
    Whitelist of context store keys to copy into each iterator branch (iter_key).
    Default: ["*"] — copies the entire context store (backward-compatible).

    Use specific keys to prevent large data from being duplicated across N branches,
    which would cause the LangGraph checkpoint to exceed PostgreSQL JSONB size limits.
    Only the listed keys will be present in each branch's context_store copy.
    The parent context store is not affected.

    Example:
        include_in_iterator_context: ["current_goal", "channel", "jira_project_key"]
        # Branches only get small metadata; review_batches stays in parent store only.
    """

    append_to_context: bool = False
    """
    If true, all values written to context_store by this state are accumulated
    as a list instead of overwriting the existing value.
    Default: False (standard overwrite semantics)

    Useful for parallel iterations via iter_key where multiple branches write
    to the same keys and all results must be preserved.

    Example:
        append_to_context: true
        # Each iteration appends its output so context_store["output"] becomes a list
    """

    def leads_to(self) -> set[str]:
        targets: set[str] = set()
        if self.state_id:
            targets.add(self.state_id)
        if self.state_ids:
            targets.update(self.state_ids)
        if self.condition:
            targets.add(self.condition.then)
            targets.add(self.condition.otherwise)
        if self.switch:
            targets.add(self.switch.default)
            targets.update(c.state_id for c in self.switch.cases)
        return targets

    @model_validator(mode='before')
    def handle_keep_history_backward_compatibility(cls, values: dict) -> dict:
        """Handle backward compatibility for deprecated keep_history field."""
        new_key = "include_in_llm_history"
        old_key = "keep_history"  # Deprecated
        new_value = values.get(new_key)
        old_value = values.get(old_key)

        # If both are provided, warn user
        if new_value is not None and old_value is not None:
            # Log warning but use new value (include_in_llm_history takes precedence)
            pass  # Let the new value take precedence

        # If only old value is provided, migrate to new field
        if old_value is not None and new_value is None:
            values[new_key] = old_value

        return values

    @model_validator(mode='after')
    def validate(cls, values: WorkflowNextState) -> WorkflowNextState:
        if values.condition and values.switch:
            raise ValueError("Only one of 'condition' or 'switch' must be provided.")
        if values.state_ids and values.state_id:
            raise ValueError("Only one of 'state_id' or 'state_ids' must be provided.")
        if values.state_ids and values.iter_key:
            raise ValueError("Cannot iterate over sequence of states provided in 'state_ids'")
        if values.state_ids and (values.condition or values.switch):
            raise ValueError(
                "Cannot use `condition` or `switch` section with sequence of states provided in 'state_ids'"
            )
        return values


class WorkflowRetryPolicy(BaseModel):
    initial_interval: Optional[float] = None
    backoff_factor: Optional[float] = None
    max_interval: Optional[float] = None
    max_attempts: Optional[int] = None

    @staticmethod
    def custom_retry_on(exc: Exception) -> bool:
        custom_retry_on = True
        if isinstance(exc, TaskException) and exc.original_exc is not None:
            exc = exc.original_exc
        if isinstance(exc, (InvalidCredentialsError, TruncatedOutputError)):
            return False
        if isinstance(exc, httpx.HTTPStatusError):
            custom_retry_on = exc.response.status_code not in (401, 403, 404)
        if isinstance(exc, requests.HTTPError):
            custom_retry_on = exc.response.status_code not in (401, 403, 404) if exc.response is not None else True

        # overwitten check for langchain default_retry_on, where bool(exc.response) returns False for all error codes,
        # so we need to explicitly check if exc.response is not None
        if isinstance(exc, requests.HTTPError):
            return 500 <= exc.response.status_code < 600 if exc.response is not None else True

        return custom_retry_on and default_retry_on(exc)


class WorkflowState(BaseModel):
    id: str
    assistant_id: Optional[str] = None
    custom_node_id: Optional[str] = None
    task: str = ""
    finish_iteration: bool = False
    next: WorkflowNextState
    output_schema: Optional[str] = None  # JSON schema
    retry_policy: WorkflowRetryPolicy = WorkflowRetryPolicy()
    interrupt_before: bool = False
    tool_id: Optional[str] = None
    tool_args: Optional[dict] = None
    resolve_dynamic_values_in_prompt: bool = False
    result_as_human_message: bool = False

    _TYPE_UNDEFINED_ERROR = "One of 'assistant_id', 'custom_node_id' or 'tool_id' must be provided."
    _TYPE_OVERDEFINED_ERROR = "Only one of 'assistant_id', 'custom_node_id' or 'tool_id' can be provided."

    @model_validator(mode='before')
    def handle_interrupt_backward_compatibility(cls, values: dict) -> dict:
        new_key = "interrupt_before"
        old_key = "wait_for_user_confirmation"  # Deprecated
        new_value = values.get(new_key)
        old_value = values.get(old_key)
        if new_value is not None and old_value is not None:  # In case JSON schema didn't catch the issue
            raise ValueError(
                f"Only one of '{new_key}' or '{old_key}' must be provided. Since they are functionally equivalent, it "
                f"is suggested to remove '{old_key}' as it is deprecated and will be removed in future versions."
            )
        if old_value is not None:
            values[new_key] = old_value
            del values[old_key]

        return values

    @model_validator(mode='after')
    def check_state_type(cls, values: WorkflowState) -> WorkflowState:
        if not any([values.assistant_id, values.custom_node_id, values.tool_id]):
            raise ValueError(cls._TYPE_UNDEFINED_ERROR)
        if sum(id is not None for id in [values.assistant_id, values.custom_node_id, values.tool_id]) > 1:
            raise ValueError(cls._TYPE_OVERDEFINED_ERROR)
        return values


# API Models, TODO: move to rest_api module
class CreateWorkflowRequest(BaseModel):
    id: Optional[str] = None
    name: str
    mode: Optional[WorkflowMode] = WorkflowMode.SEQUENTIAL
    description: str
    project: str
    icon_url: Optional[str] = None
    yaml_config: Optional[str] = None
    shared: bool = True
    assistants: Optional[list[WorkflowAssistant]] = []
    states: Optional[list[WorkflowState]] = []
    supervisor_prompt: Optional[str] = ""
    meta_config: Optional[str] = None
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None


class UpdateWorkflowRequest(BaseModel):
    id: Optional[str] = None
    name: str
    description: str
    project: str
    mode: WorkflowMode = WorkflowMode.SEQUENTIAL
    icon_url: Optional[str] = None
    shared: bool = True
    yaml_config: Optional[str] = None
    supervisor_prompt: str = ""
    meta_config: Optional[str] = None
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
