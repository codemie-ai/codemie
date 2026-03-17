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

from typing import Type, Optional, Any

from codemie.agents.assistant_agent import AIToolsAgent, TaskResult
from codemie.configs import logger, config
from codemie.enterprise.langfuse import get_workflow_trace_context
from codemie.core.exceptions import TaskException
from codemie.core.thought_queue import ThoughtQueue
from codemie.rest_api.security.user import User
from codemie.service.assistant import VirtualAssistantService
from codemie.service.tools.dynamic_value_utils import process_string
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.constants import (
    ITERATION_NODE_NUMBER_KEY,
    SUMMARIZE_MEMORY_NODE,
    TOTAL_ITERATIONS_KEY,
    CURRENT_TASK_KEY,
)
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.base_node import BaseNode, StateSchemaType
from codemie.workflows.utils import (
    get_messages_from_state_schema,
    find_assistant_by_id,
    should_summarize_memory,
    initialize_assistant,
    get_context_store_from_state_schema,
)
from codemie_tools.base.file_object import FileObject
from langgraph.types import Command


class AgentNode(BaseNode[AgentMessages]):
    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        node_name: Optional[str] = "",
        *args,
        **kwargs,
    ):
        """Initialize the AgentNode with AI agent-specific configuration.

        Args:
            callbacks: List of callback handlers for node lifecycle events
            workflow_execution_service: Service for managing workflow execution states
            thought_queue: Queue for managing AI agent thoughts and reasoning
            node_name: Optional name identifier for this node
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments including:
                - summarize_history (bool): Whether to enable memory summarization
                - current_task_key (str): Key for current task in state schema
                - workflow_config (WorkflowConfig): Workflow configuration
                - assistant (AIToolsAgent): Pre-initialized assistant instance
                - user_input (str): User input for the workflow
                - user (User): User object for authentication and context
                - resume_execution (bool): Whether this is a resumed execution
                - execution_id (str): Unique execution identifier
                - file_names (list[str]): Names of attached files if any
        """
        super().__init__(callbacks, workflow_execution_service, thought_queue, node_name, *args, **kwargs)
        self.summarize_history: bool = kwargs.get("summarize_history")
        self.current_task_key: Optional[str] = kwargs.get(CURRENT_TASK_KEY)
        self.assistant: Optional[AIToolsAgent] = kwargs.get("assistant")
        self.user_input: str = kwargs.get("user_input")
        self.user: User = kwargs.get("user")
        self.resume_execution: bool = kwargs.get("resume_execution")
        self.execution_id: str = kwargs.get("execution_id")
        self.file_names: list[str] = kwargs.get("file_names", [])
        self.request_headers: dict[str, str] | None = kwargs.get("request_headers")
        self.disable_cache: Optional[bool] = kwargs.get("disable_cache")

    def execute(self, state_schema: Type[StateSchemaType], execution_context: dict) -> Any:
        """Execute the AI agent to process the current task.

        This method retrieves the assistant from the execution context and invokes
        it with the current task and message history to generate a response.

        Args:
            state_schema: The current state schema containing workflow data and messages
            execution_context: Dictionary containing the initialized assistant and other context

        Returns:
            TaskResult: The result from the AI agent execution including success status
                       and generated content
        """
        assistant: AIToolsAgent = execution_context.get("assistant")
        messages = get_messages_from_state_schema(state_schema=state_schema)
        agent_task_result = assistant.invoke_task(
            workflow_input=self.get_task(state_schema, self.args, self.kwargs), history=messages
        )
        return agent_task_result

    def get_task(self, state_schema: Type[StateSchemaType], *arg, **kwargs):
        """Get the task description for the AI agent to execute.

        This method constructs the task description by processing the workflow state task,
        optionally resolving dynamic values, and appending current task or file information.

        Args:
            state_schema: The current state schema containing workflow data
            *arg: Additional positional arguments
            **kwargs: Additional keyword arguments

        Returns:
            str: The complete task description for the AI agent

        Note:
            If resolve_dynamic_values_in_prompt is enabled, the task will be processed
            for template variables. Current task or file attachment info is appended.
        """
        task = self.workflow_state.task

        task = task or "-"
        current_task = None
        if self.current_task_key:
            current_task = state_schema.get(self.current_task_key, "")
            task += f"\nCurrent task: {current_task}"
        elif self.file_names:
            for fn in self.file_names:
                decoded_file = FileObject.from_encoded_url(fn)
                task += f"\nFile attached: {decoded_file.name}"

        if self.workflow_state.resolve_dynamic_values_in_prompt and task != "-":
            dynamic_vals_context = get_context_store_from_state_schema(state_schema)
            if isinstance(current_task, dict):
                dynamic_vals_context.update(current_task)
            task = process_string(source=task, context=dynamic_vals_context)

        return task

    def before_execution(
        self,
        state_schema: Type[StateSchemaType],
        execution_context: dict,
        *args,
        **kwargs,
    ) -> Command or None:
        """Check if memory summarization is needed and apply guardrails before execution.

        This method evaluates whether the conversation history has grown too large
        and needs to be summarized before proceeding with agent execution.

        Args:
            state_schema: The current state schema containing message history
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments

        Returns:
            Command or None: Command to redirect to summarization node if needed,
                           or None to continue normal execution
        """
        parent_result = super().before_execution(state_schema, execution_context, *args, **kwargs)
        if parent_result:
            return parent_result

        _, should_summarize = should_summarize_memory(self.workflow_config, state_schema)
        if self.summarize_history and should_summarize:
            logger.info(f"Memory too large, should summarize: {self.workflow_state.id}")
            return Command(goto=SUMMARIZE_MEMORY_NODE)

    def after_execution(self, state_schema: Type[StateSchemaType], result: Any, *args, **kwargs):
        """Cleanup after agent execution.

        This method performs post-execution cleanup including calling the parent
        after_execution method and deleting temporary virtual assistants if they
        don't have a persistent assistant_id.

        Args:
            state_schema: The current state schema
            result: The result from agent execution
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments
        """
        super().after_execution(state_schema, result, *args, **kwargs)

        assistant_config = find_assistant_by_id(
            assistants=self.workflow_config.assistants, assistant_id=self.workflow_state.assistant_id
        )
        if not assistant_config.assistant_id:
            VirtualAssistantService.delete_by_execution_id(self.execution_id)

    def generate_execution_context(self, state_schema: Type[StateSchemaType]) -> dict:
        """Generate execution context with initialized assistant.

        Creates the execution context dictionary containing all necessary components
        for agent execution, including an initialized assistant instance.

        Returns:
            dict: Execution context with assistant and other configuration
        """

        dynamic_vals_context = get_context_store_from_state_schema(state_schema)
        return {
            **self.kwargs,
            "assistant": self.assistant
            or self.init_assistant(
                mcp_server_args_preprocessor=lambda arg, initial_dynamic_vals: process_string(
                    source=arg, context=dynamic_vals_context, initial_dynamic_vals=initial_dynamic_vals
                )
            ),
        }

    def init_assistant(
        self,
        mcp_server_args_preprocessor: Optional[callable] = None,
    ):
        """Initialize the AI assistant for this node.

        Creates and configures an AI assistant instance using the workflow configuration,
        user context, and execution parameters. The assistant is initialized with
        appropriate tools, memory, and project context.

        Returns:
            AIToolsAgent: Initialized assistant ready for task execution
        """
        workflow_assistant = find_assistant_by_id(
            assistants=self.workflow_config.assistants, assistant_id=self.workflow_state.assistant_id
        )

        # Retrieve workflow trace context for trace unification
        trace_context = None
        if config.LANGFUSE_TRACES:
            trace_context = get_workflow_trace_context(self.execution_id)

        return initialize_assistant(
            workflow_assistant=workflow_assistant,
            workflow_state=self.workflow_state,
            user_input=self.user_input,
            user=self.user,
            thought_queue=self.thought_queue,
            resume_execution=self.resume_execution,
            execution_id=self.execution_id,
            project_name=self.workflow_config.project,
            file_names=self.file_names,
            mcp_server_args_preprocessor=mcp_server_args_preprocessor,
            request_headers=self.request_headers,
            trace_context=trace_context,  # Pass trace context for nested traces
            disable_cache=self.disable_cache,
        )

    def get_node_name(self, state_schema: Type[AgentMessages]):
        """Get the display name for this agent node.

        Returns the node name with optional iteration information for map-reduce
        operations where multiple iterations are being processed.

        Args:
            state_schema: The current state schema containing iteration information

        Returns:
            str: Display name with iteration info if applicable (e.g., "NodeName 2 of 5")
        """
        result = super().get_node_name(state_schema)
        if self.current_task_key:
            iter_number = state_schema.get(ITERATION_NODE_NUMBER_KEY, 0)
            total_iterations = state_schema.get(TOTAL_ITERATIONS_KEY, 0)
            if total_iterations > 1:
                result += f" {iter_number} of {total_iterations}"
        return result

    def post_process_output(self, state_schema: Type[AgentMessages], task, output: TaskResult) -> str:
        """Process the agent's task result into a string format.

        This method checks if the agent execution was successful and extracts
        the result content. If execution failed, it raises a TaskException.

        Args:
            state_schema: The current state schema
            task: The task description that was executed
            output: The TaskResult from agent execution

        Returns:
            str: The result content from successful agent execution

        Raises:
            TaskException: If the agent execution failed, includes the original exception
        """
        if not output.success:
            raise TaskException(f"Graph node execution failed. {output.result}", original_exc=output.original_exc)

        return output.result
