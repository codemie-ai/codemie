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

import json
from typing import Type, Any
from functools import cached_property

from pydantic import BaseModel, ValidationError

from codemie.core.utils import build_unique_file_objects_list
from codemie.core.workflow_models import WorkflowState, WorkflowConfig, WorkflowTool
from codemie.service.mcp.models import MCPToolInvocationResponse
from codemie.service.tools.dynamic_value_utils import process_values, process_string
from codemie.workflows.constants import TASK_KEY, FIRST_STATE_IN_ITERATION
from codemie.workflows.nodes.base_node import BaseNode, StateSchemaType
from codemie.workflows.models import AgentMessages
from codemie.rest_api.security.user import User
from codemie.configs import logger, config
from codemie.service.assistant import VirtualAssistantService
from codemie.service.tools import ToolsService
from codemie.service.tools import ToolkitService
from codemie.workflows.utils import get_context_store_from_state_schema
from codemie.workflows.utils.json_utils import UnwrappingJsonPointerEvaluator
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.workflows.utils.transform_node_utils import extract_with_array_indices

TOOL_NOT_FOUND_ERROR = "Tool *{tool_id}* not found.\n"
INVALID_SIGNATURE_ERROR = "Tool method arguments are not correct. Expected: {args_desc}"


class ToolNode(BaseNode[AgentMessages]):
    """Execute tools directly within workflows without LLM interaction.

    This node allows workflows to execute tools directly, supporting both regular
    toolkit methods and MCP (Model Context Protocol) server tools. It handles
    argument processing, validation, and execution of configured tools.

    Attributes:
        workflow_config: Configuration for the workflow
        user: User context for tool execution
        execution_id: Unique identifier for the current execution
    """

    def __init__(
        self,
        workflow_state: WorkflowState,
        workflow_config: WorkflowConfig,
        user: User,
        *args,
        **kwargs,
    ):
        """Initialize the ToolNode with workflow and user configuration.

        Args:
            workflow_state: Current workflow state configuration
            workflow_config: Overall workflow configuration containing tool definitions
            user: User context for authentication and permissions
            *args: Additional positional arguments passed to parent
            **kwargs: Additional keyword arguments including:
                - execution_id (str): Unique execution identifier
                - request_headers (dict[str, str] | None): Optional custom headers for MCP propagation
        """
        super().__init__(*args, workflow_state=workflow_state, workflow_config=workflow_config, user=user, **kwargs)

        self.user = user
        self.execution_id: str = kwargs.get("execution_id")
        self.request_headers: dict[str, str] | None = kwargs.get("request_headers")
        self.file_names: list[str] = kwargs.get("file_names", [])

    def execute(self, state_schema: Type[StateSchemaType], execution_context: dict) -> Any:
        """Execute the configured tool based on its type (MCP or regular).

        This method determines whether to execute an MCP server tool or a regular
        toolkit tool based on the tool configuration and dispatches to the
        appropriate execution method.

        Args:
            state_schema: The current state schema containing workflow data
            execution_context: Dictionary containing execution context (unused here)

        Returns:
            Any: The result from tool execution

        Raises:
            ValueError: If MCP is not enabled or MCP server is disabled
            Exception: Any exception from tool execution
        """
        # Check if MCP server is configured for this tool
        return (
            self._execute_mcp_tool(state_schema)
            if self._tool_config.mcp_server
            else self._execute_regular_tool(state_schema)
        )

    def _execute_mcp_tool(self, state_schema: Type[StateSchemaType]) -> Any:
        """Execute a tool from an MCP (Model Context Protocol) server.

        This method connects to the configured MCP server, retrieves available tools,
        finds the specified tool, and executes it with processed arguments from the
        workflow state.

        Args:
            state_schema: The current state schema containing input data

        Returns:
            Any: The result from MCP tool execution, converted to string if needed

        Raises:
            ValueError: If MCP Connect is disabled, MCP server is disabled, or tool not found
            Exception: Any exception from MCP tool execution
        """
        # Check if MCP Connect is enabled
        if not config.MCP_CONNECT_ENABLED:
            raise ValueError("MCP Connect is not enabled in configuration")

        mcp_server = self._tool_config.mcp_server
        # Skip if MCP server is disabled
        if not mcp_server.enabled:
            raise ValueError(f"MCP server '{mcp_server.name}' is disabled")

        try:
            dynamic_vals_context = get_context_store_from_state_schema(state_schema)
            mcp_tools = MCPToolkitService.get_mcp_server_tools(
                mcp_servers=[mcp_server],
                user_id=self.user.id,
                project_name=self.workflow_config.project,
                conversation_id=self.execution_id,
                mcp_server_args_preprocessor=lambda arg, initial_dynamic_vals: process_string(
                    source=arg, context=dynamic_vals_context, initial_dynamic_vals=initial_dynamic_vals
                ),
                mcp_server_single_usage=mcp_server.config.single_usage,
                workflow_execution_id=self.execution_id,
                request_headers=self.request_headers,
            )

            tool = None
            for mcp_tool in mcp_tools:
                if mcp_tool.name == self._tool_config.tool:
                    tool = mcp_tool
                    break

            if not tool:
                available_tools = [t.name for t in mcp_tools]
                raise ValueError(
                    f"Tool '{self._tool_config.tool}' not found in MCP server '{mcp_server.name}'. "
                    f"Available tools: {available_tools}"
                )

            # Execute the tool with the same logic as regular tools
            tool_result = self._execute_tool_with_args(tool, state_schema)
            return (
                "\n".join(str(item) for item in tool_result.content)
                if isinstance(tool_result, MCPToolInvocationResponse)
                else tool_result
            )

        except Exception as e:
            logger.error(
                f"Failed to execute MCP tool '{self._tool_config.tool}' from server '{mcp_server.name}': {str(e)}"
            )
            raise

    def _execute_regular_tool(self, state_schema: Type[StateSchemaType]) -> Any:
        """Execute a tool using the regular toolkit methods.

        This method creates a virtual assistant with the tool configuration,
        retrieves the tool from available toolkits, and executes it with
        processed arguments. The virtual assistant is cleaned up after execution.

        Args:
            state_schema: The current state schema containing input data

        Returns:
            Any: The result from regular tool execution

        Note:
            The temporary virtual assistant is automatically deleted after execution.
        """
        assistant = VirtualAssistantService.create_from_tool_config(
            tool_config=self._tool_config,
            user=self.user,
            project_name=self.workflow_config.project,
            execution_id=self.execution_id,
        )
        toolkits = ToolkitService.get_toolkit_methods()

        file_objects = (
            build_unique_file_objects_list(
                file_names=self.file_names,
                conversation_id=self.execution_id,
            )
            if self.file_names
            else None
        )

        tool = ToolsService.find_tool_from_config(
            self._tool_config, toolkits, assistant, self.user, self.workflow_config.project
        )

        # Direct assignment is required here: CodeExecutorTool.__init__ ignores the `config`
        # kwarg and always calls CodeExecutorConfig.from_env(), so file_objects passed
        # through _initialize_tool / FileConfigMixin never reach self.input_files.
        # The fallback path (FileSystemToolkit.get_tools) also has no access to file_objects.
        # This field is a proper mutable Pydantic field designed exactly for this use case.
        if file_objects and hasattr(tool, "input_files"):
            tool.input_files = file_objects

        try:
            return self._execute_tool_with_args(tool, state_schema)
        finally:
            VirtualAssistantService.delete(assistant.id)

    def _execute_tool_with_args(self, tool, state_schema: Type[StateSchemaType]) -> Any:
        """Execute a tool with argument processing, validation, and error handling.

        This method handles the common logic for executing both MCP and regular tools,
        including argument processing from workflow state, parameter validation using
        tool schemas, and execution with proper error handling.

        Args:
            tool: The tool instance to execute (MCP or regular tool)
            state_schema: The current state schema containing input data

        Returns:
            Any: The result from tool execution

        Raises:
            ValueError: If tool arguments validation fails
            ValidationError: If Pydantic model validation fails
            TypeError: If tool signature validation fails

        Note:
            Missing parameters are set to None for execution. Only non-None values
            are validated against the tool's argument schema.
        """
        tool_args = self.workflow_state.tool_args or self._tool_config.tool_args
        annotations, is_json = self._get_annotations(tool.args_schema)

        if not tool_args:
            tool_args = dict.fromkeys(annotations)

        try:
            tool_input = self._get_tool_args(
                tool_args=tool_args,
                state_schema=state_schema,
            )

            # For validation, only include parameters that have non-None values
            tool_input = {k: v for k, v in tool_input.items() if v is not None}

            # Validate only the parameters we have values for (exclude None values)
            if isinstance(tool.args_schema, dict) and is_json:
                args_class = tool.model_args(tool.args_schema)
                # Only validate non-None parameters
                if tool_input:
                    args_class(**tool_input)
            else:
                # Only validate non-None parameters
                if tool_input and callable(tool.args_schema):
                    # Check if args_schema is callable (Pydantic model) or a dict
                    tool.args_schema(**tool_input)
                    # If it's a dict schema, we can't validate it directly, so skip validation

            # Execute tool with passed parameters
            result = tool.execute(**tool_input)
            # Enforce tokens_size_limit — mirrors CodeMieTool._run() behavior bypassed by direct execute() call
            if hasattr(tool, "apply_tokens_limit"):
                result = tool.apply_tokens_limit(result)

            logger.debug(f"Tool node execution result: {result}")
            return result

        except (ValidationError, TypeError) as e:
            logger.error(f"Tool arguments validation failed with error: {e}")
            if is_json:
                signature = {k: prop.get('type', 'unknown') for k, prop in annotations.items()}
            else:
                signature = {k: v.__name__ for k, v in annotations.items()}
            raise ValueError(INVALID_SIGNATURE_ERROR.format(args_desc=signature))

    @staticmethod
    def _get_annotations(tool_args: BaseModel | dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Extract annotations/properties from tool argument schema.

        This method handles different tool argument schema formats including
        Pydantic models with annotations and JSON Schema dictionaries.

        Args:
            tool_args: Tool argument schema (Pydantic model or dict)

        Returns:
            tuple[dict[str, Any], bool]: A tuple containing:
                - Dictionary of parameter annotations/properties
                - Boolean indicating if this is a JSON schema format
        """
        if hasattr(tool_args, '__annotations__'):
            # Class with annotations
            return tool_args.__annotations__, False
        elif isinstance(tool_args, dict) and 'properties' in tool_args:
            # JSON Schema format
            return tool_args.get('properties', {}), True
        else:
            # Assume it's a dict schema
            return tool_args, False

    def get_task(self, state_schema: AgentMessages, *arg, **kwargs):
        """Get the task description for this tool execution.

        Args:
            state_schema: The current state schema (unused)
            *arg: Additional positional arguments (unused)
            **kwargs: Additional keyword arguments (unused)

        Returns:
            str: Task description indicating which tool will be called
        """
        return f"Call {self._tool_config.tool}"

    @cached_property
    def _tool_config(self) -> WorkflowTool:
        """Get the tool configuration from the workflow configuration.

        This cached property retrieves the tool configuration based on the tool_id
        specified in the workflow state. The configuration is cached for performance.

        Returns:
            WorkflowTool: The tool configuration object from the workflow YAML

        Raises:
            ValueError: If the specified tool_id is not found in the workflow configuration
        """
        tool_id = self.workflow_state.tool_id
        tool_config = next(
            (tool for tool in self.workflow_config.tools if tool.id == tool_id),
            None,
        )

        if not tool_config:
            raise ValueError(TOOL_NOT_FOUND_ERROR.format(tool_id=self.workflow_state.tool_id))

        return tool_config

    def _get_tool_args(self, tool_args: dict, state_schema: Type[StateSchemaType]) -> dict:
        """Extract and process tool arguments from workflow state and input messages.

        When input_key is configured on the tool, arguments are resolved from the
        sub-namespace at context_store[input_key] instead of the root context_store.
        This enables namespace isolation for multiple tools and native passing of
        complex dict/list values without Jinja2 stringification.

        Args:
            tool_args: Dictionary of tool arguments to process
            state_schema: The current state schema containing input data

        Returns:
            dict: Processed tool arguments with resolved values
        """
        context_store = get_context_store_from_state_schema(state_schema)

        if self._tool_config.input_key:
            namespace = extract_with_array_indices(context_store, self._tool_config.input_key)
            dynamic_vals_context = namespace if isinstance(namespace, dict) else {}
        else:
            dynamic_vals_context = dict(context_store)

        if state_schema.get(FIRST_STATE_IN_ITERATION):
            task = state_schema.get(TASK_KEY)
            if isinstance(task, dict):
                dynamic_vals_context.update(task)

        return process_values(tool_args, dynamic_vals_context)

    def post_process_output(self, state_schema: Type[StateSchemaType], task, output) -> str:
        """Post-process the tool execution output into string format.

        This method converts the tool output into a string format suitable for
        workflow processing. It handles Pydantic models, JSON serialization,
        and optional JSON pointer extraction for specific result nodes.

        Args:
            state_schema: The current state schema (unused)
            task: The task description (unused)
            output: The raw output from tool execution

        Returns:
            str: Processed output as a string, potentially with JSON pointer extraction

        Note:
            If tool_result_json_pointer is configured, only the specified JSON node
            will be extracted from the result.
        """
        result = output
        if isinstance(output, BaseModel):
            result = output.model_dump_json()
        elif not isinstance(output, str):
            result = json.dumps(output)
        # Extract specific node from execution result if set in tool settings
        if self._tool_config.tool_result_json_pointer:
            result = UnwrappingJsonPointerEvaluator.get_node_by_pointer(
                result, self._tool_config.tool_result_json_pointer
            )

        if self._tool_config.resolve_dynamic_values_in_response:
            dynamic_vals_context = get_context_store_from_state_schema(state_schema)
            result = process_string(source=result, context=dynamic_vals_context)

        return result
