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

import importlib
import inspect
import pkgutil
import traceback
import uuid

from codemie.core.constants import MermaidContentType
from codemie.service.file_service.mermaid_service import MermaidService
from typing import List, Callable, Any
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.graph import CurveStyle, NodeStyles
from langgraph.constants import END
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from codemie.service.assistant import VirtualAssistantService
from langgraph.types import Send
from pydantic import ValidationError

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.chains.base import ThoughtAuthorType, StreamedGenerationResult, Thought
from codemie.configs import logger, config
from codemie.enterprise.langfuse import (
    clear_workflow_trace_context,
    create_workflow_trace_context,
    get_langfuse_callback_handler,
)
from codemie.core.exceptions import InterruptedException
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.thread import MessageQueue
from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowState,
    WorkflowExecution,
    WorkflowExecutionStatusEnum,
    WorkflowMode,
    WorkflowAssistant,
    WorkflowErrorFormat,
)
from codemie.rest_api.security.user import User
from codemie.rest_api.models.assistant import Assistant, VirtualAssistant
from codemie.service.workflow_execution import WorkflowExecutionService, ThoughtConsumer
from codemie.service.workflow_service import WorkflowService
from codemie.workflows.callbacks.graph_callback import LanggraphNodeCallback
from codemie.workflows.constants import (
    RESULT_FINALIZER_NODE,
    MESSAGES_VARIABLE,
    RECURSION_LIMIT,
    STATE_MISSING_ERR,
    TASK_KEY,
    ITERATION_NODE_NUMBER_KEY,
    SUMMARIZE_MEMORY_NODE,
    TOTAL_ITERATIONS_KEY,
    WorkflowErrorType,
    ITER_SOURCE,
    CONTEXT_STORE_VARIABLE,
    USER_INPUT,
    FIRST_STATE_IN_ITERATION,
    PREVIOUS_EXECUTION_STATE_ID,
    PREVIOUS_EXECUTION_STATE_NAME,
)
from codemie.workflows.checkpoint_saver import CheckpointSaver
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes import BaseNode, AgentNode, ToolNode, ResultFinalizerNode, SummarizeConversationCommandNode
from codemie.workflows.utils import (
    find_custom_node_by_id,
    parse_from_string_representation,
    get_final_state,
    get_messages_from_state_schema,
    evaluate_conditional_route as evaluate,
    initialize_assistant,
    get_context_store_from_state_schema,
)
from codemie.workflows.validation import (
    WorkflowExecutionParsingError,
    WorkflowExecutionConfigSchemaValidationError,
    WorkflowExecutionConfigCrossReferenceValidationError,
    validate_workflow_execution_config_yaml,
    WorkflowConfigResourcesValidationError,
    validate_workflow_config_resources_availability,
    PydanticErrorTransformer,
)
from codemie.workflows.utils.json_utils import UnwrappingJsonPointerEvaluator


class WorkflowExecutor:
    INTERRUPT_CONFIRMATION_MSG = "**Workflow is stopped, waiting for confirmation to continue** <br>"
    workflow_execution_config: Optional[WorkflowExecution] = None

    @classmethod
    def create_executor(
        cls,
        workflow_config: WorkflowConfig,
        user_input: str,
        user: User,
        resume_execution: bool = False,
        execution_id: str = None,
        file_names: Optional[list[str]] = None,
        request_headers: dict[str, str] | None = None,
        thought_queue: Optional[MessageQueue] = None,
        session_id: Optional[str] = None,
        disable_cache: Optional[bool] = False,
        tags: Optional[list[str]] = None,
        delete_on_completion: bool = False,
    ):
        """
        Create a workflow executor with the specified configuration.

        Args:
            workflow_config: Workflow configuration
            user_input: User input for the workflow
            user: User executing the workflow
            resume_execution: Whether to resume from checkpoint
            execution_id: Execution ID for tracking
            file_names: Optional list of file names for file-based workflows
            request_headers: HTTP headers to propagate
            thought_queue: Optional pre-created queue (MessageQueue protocol, e.g., ThreadedGenerator
                          for streaming mode or ThoughtQueue for custom cases). If not provided,
                          a new ThoughtQueue will be created for background mode.
            session_id: Optional session ID for Langfuse tracing. If not provided, execution_id will be used
            disable_cache: Disable Prompt Caching
        """
        workflow_config.parse_execution_config()
        file_names = file_names or []

        # Use provided queue or create new one for background execution
        if thought_queue is None:
            thought_queue = ThoughtQueue()
            thought_queue.set_context('user_id', user.id)

        if workflow_config.mode == WorkflowMode.AUTONOMOUS:
            from codemie.workflows.supervisor_workflow import SupervisorWorkflowExecutor

            return SupervisorWorkflowExecutor(
                workflow_config=workflow_config,
                user_input=user_input,
                user=user,
                thought_queue=thought_queue,
                resume_execution=resume_execution,
                execution_id=execution_id,
                file_names=file_names,
                request_headers=request_headers,
                session_id=session_id,
                disable_cache=disable_cache,
                tags=tags,
                delete_on_completion=delete_on_completion,
            )
        else:
            return cls(
                workflow_config=workflow_config,
                user_input=user_input,
                user=user,
                thought_queue=thought_queue,
                resume_execution=resume_execution,
                execution_id=execution_id,
                file_names=file_names,
                request_headers=request_headers,
                session_id=session_id,
                disable_cache=disable_cache,
                tags=tags,
                delete_on_completion=delete_on_completion,
            )

    @staticmethod
    def _raise_validation_error(error_format: str, json_error: dict, string_error: str) -> None:
        """
        Helper method to raise validation errors in the appropriate format.

        Args:
            error_format: Error format - "string" or "json"
            json_error: Error dict for JSON format
            string_error: Error string for string format

        Raises:
            ValueError: With either string message or dict based on error_format
        """
        if error_format == WorkflowErrorFormat.JSON.value:
            raise ValueError(json_error)
        raise ValueError(string_error)

    @staticmethod
    def validate_workflow(workflow_config: WorkflowConfig, user: User, error_format: str = "string"):
        """
        Validate workflow configuration.

        Args:
            workflow_config: The workflow configuration to validate
            user: The user performing the validation
            error_format: Error format - "string" (default, HTML <br> tags) or "json" (structured dict)

        Raises:
            ValueError: With either string message (error_format="string") or dict (error_format="json")
        """
        try:
            validate_workflow_execution_config_yaml(workflow_config.yaml_config)
        except WorkflowExecutionParsingError as e:
            error_dict = e.to_dict()
            WorkflowExecutor._raise_validation_error(
                error_format,
                error_dict,
                "\nInvalid YAML format was provided \n".replace('\n', '<br>'),
            )
        except (
            WorkflowExecutionConfigSchemaValidationError,
            WorkflowExecutionConfigCrossReferenceValidationError,
        ) as e:
            error_dict = e.to_dict()
            # error_dict already has proper 'message' field from new format
            WorkflowExecutor._raise_validation_error(
                error_format,
                error_dict,
                f"\nInvalid YAML config was provided: \n{e}\n".replace('\n', '<br>'),
            )

        try:
            workflow_config.parse_execution_config()
        except ValidationError as e:
            # Transform Pydantic errors to WorkflowValidationErrorDetail format
            errors = PydanticErrorTransformer(e, workflow_config).transform()

            json_error = {
                "error_type": WorkflowErrorType.WORKFLOW_SCHEMA.value,
                "message": "Configuration contains validation errors",
                "errors": errors,
            }
            string_error = f"\nInvalid workflow schema was provided: {e.errors()[0]['msg']}"
            WorkflowExecutor._raise_validation_error(error_format, json_error, string_error)

        if not workflow_config.states and workflow_config.mode != WorkflowMode.AUTONOMOUS:
            WorkflowExecutor._raise_validation_error(
                error_format,
                {"error_type": WorkflowErrorType.MISSING_STATES.value, "message": STATE_MISSING_ERR},
                STATE_MISSING_ERR,
            )

        try:
            validate_workflow_config_resources_availability(workflow_config, user)
        except WorkflowConfigResourcesValidationError as e:
            error_dict = e.to_dict()
            # error_dict already has proper 'message' field from new format
            string_error = (
                f"\nWorkflow can't be created because the following Assistants "
                f"/ Tools / Data sources do not exist: \n{e}\n".replace('\n', '<br>')
            )
            WorkflowExecutor._raise_validation_error(error_format, error_dict, string_error)

        return WorkflowExecutor.create_executor(
            workflow_config=workflow_config,
            user_input="Validate graph. No actions required",
            user=user,
            execution_id=str(uuid.uuid4()),
        )._init_workflow()

    @staticmethod
    def validate_workflow_and_draw(workflow_config: WorkflowConfig, user: User, error_format: str = "string"):
        workflow = WorkflowExecutor.validate_workflow(workflow_config, user, error_format)

        try:
            graph = workflow.get_graph()
            # Remove edges pointing to themselves,
            # they are for technical purposes and should not be in the graph diagram
            graph.edges = [edge for edge in graph.edges if edge.source != edge.target]

            mermaid_syntax = graph.draw_mermaid(
                with_styles=False,
                curve_style=CurveStyle.BASIS,
                node_colors=NodeStyles(first="#c3a6ff", last="#c3a6ff", default="#c3a6ff"),
                wrap_label_n_words=9,
            )
            content = MermaidService.draw_mermaid(
                mermaid_code=mermaid_syntax,
                type=MermaidContentType.SVG,
            )
            if content is None:
                logger.error('Failed to draw Mermaid PNG')
                return None

            return content
        except Exception as e:
            logger.error(f'Failed to draw Mermaid PNG: {str(e)}')
            return None

    def __init__(
        self,
        workflow_config: WorkflowConfig,
        user_input: str,
        user: User,
        thought_queue: MessageQueue = None,
        file_names: Optional[list[str]] = None,
        resume_execution: bool = False,
        execution_id: str = None,
        request_headers: dict[str, str] | None = None,
        session_id: Optional[str] = None,
        disable_cache: Optional[bool] = False,
        tags: Optional[list[str]] = None,
        delete_on_completion: bool = False,
    ):
        self.workflow_config = workflow_config
        self.user_input = user_input
        self.file_names = file_names or []
        self.user = user
        self.resume_execution = resume_execution
        self.execution_id = execution_id
        self.session_id = session_id
        self.tags = tags
        self.request_headers = request_headers
        self.thought_queue = thought_queue
        self.delete_on_completion = delete_on_completion
        self.workflow_execution_service = WorkflowExecutionService(
            workflow_config=workflow_config,
            workflow_execution_id=execution_id,
            user=user,
            thought_queue=thought_queue,
        )
        self.graph_callback = LanggraphNodeCallback(
            self.thought_queue,
            author=ThoughtAuthorType.Agent,
        )
        self.callbacks = [self.graph_callback]
        self.disable_cache = disable_cache

    def _init_workflow(self) -> CompiledStateGraph:
        workflow = self.init_state_graph()
        entry_point = self.get_workflow_entry_point()
        workflow.set_entry_point(entry_point)
        self.build_workflow(workflow)

        compile_args = {"debug": config.verbose}

        if self._interrupt_before_states:
            compile_args["interrupt_before"] = self._interrupt_before_states
            compile_args["checkpointer"] = CheckpointSaver()

        return workflow.compile(**compile_args)

    def init_state_graph(self) -> StateGraph:
        return StateGraph(AgentMessages)

    def build_workflow(self, workflow: StateGraph):
        self.initialize_nodes(workflow, self.workflow_config)
        self.init_workflow_edges(workflow, self.workflow_config)

    def get_workflow_entry_point(self) -> str:
        return self.workflow_config.states[0].id

    def _count_single_target_edges(self, state: WorkflowState, incoming_edges: dict[str, int]) -> None:
        """Count incoming edges for single target state.

        Args:
            state: Workflow state configuration
            incoming_edges: Dictionary tracking incoming edge counts per node
        """
        if state.next.state_id:
            target = state.next.state_id
            incoming_edges[target] = incoming_edges.get(target, 0) + 1

    def _count_parallel_target_edges(self, state: WorkflowState, incoming_edges: dict[str, int]) -> None:
        """Count incoming edges for parallel fan-out targets.

        Args:
            state: Workflow state configuration
            incoming_edges: Dictionary tracking incoming edge counts per node
        """
        if state.next.state_ids:
            for target in state.next.state_ids:
                incoming_edges[target] = incoming_edges.get(target, 0) + 1

    def _count_condition_target_edges(self, state: WorkflowState, incoming_edges: dict[str, int]) -> None:
        """Count incoming edges for conditional branch targets (if/else).

        Args:
            state: Workflow state configuration
            incoming_edges: Dictionary tracking incoming edge counts per node
        """
        if state.next.condition:
            then_target = state.next.condition.then
            otherwise_target = state.next.condition.otherwise
            incoming_edges[then_target] = incoming_edges.get(then_target, 0) + 1
            incoming_edges[otherwise_target] = incoming_edges.get(otherwise_target, 0) + 1

    def _count_switch_target_edges(self, state: WorkflowState, incoming_edges: dict[str, int]) -> None:
        """Count incoming edges for switch case targets.

        Args:
            state: Workflow state configuration
            incoming_edges: Dictionary tracking incoming edge counts per node
        """
        if state.next.switch:
            default_target = state.next.switch.default
            incoming_edges[default_target] = incoming_edges.get(default_target, 0) + 1
            for case in state.next.switch.cases:
                target = case.state_id
                incoming_edges[target] = incoming_edges.get(target, 0) + 1

    def find_convergence_nodes(self, workflow_config: WorkflowConfig) -> set[str]:
        """Identify nodes with multiple incoming edges (convergence points).

        These nodes need defer=True to ensure they wait for ALL parallel branches
        to complete before executing. Without defer=True, they execute once per
        incoming edge, causing duplicate executions.

        Returns:
            Set of node IDs that are convergence points
        """
        incoming_edges = {}  # node_id -> count of incoming edges

        for state in workflow_config.states:
            # Count incoming edges from all transition types
            self._count_single_target_edges(state, incoming_edges)
            self._count_parallel_target_edges(state, incoming_edges)
            self._count_condition_target_edges(state, incoming_edges)
            self._count_switch_target_edges(state, incoming_edges)

        # Nodes with more than 1 incoming edge are convergence points
        convergence_nodes = {node_id for node_id, count in incoming_edges.items() if count > 1}

        if convergence_nodes:
            logger.info(
                f"Detected convergence nodes (will use defer=True): {sorted(convergence_nodes)}. "
                f"These nodes have multiple incoming edges and need synchronization."
            )

        return convergence_nodes

    def initialize_nodes(self, workflow: StateGraph, workflow_config: WorkflowConfig):
        # Detect convergence nodes BEFORE adding nodes
        convergence_nodes = self.find_convergence_nodes(workflow_config)

        for state in workflow_config.states:
            self.initialize_node(state, workflow, workflow_config, self.find_map_nodes(), convergence_nodes)

        workflow.add_node(
            SUMMARIZE_MEMORY_NODE,
            SummarizeConversationCommandNode(
                self.callbacks,
                self.workflow_execution_service,
                self.thought_queue,
                self.workflow_config,
                execution_id=self.execution_id,
            ),
        )
        if workflow_config.enable_summarization_node:
            # Add predefined node for result summarization
            workflow.add_node(
                RESULT_FINALIZER_NODE,
                ResultFinalizerNode(
                    self.callbacks,
                    self.workflow_execution_service,
                    self.thought_queue,
                    workflow_config=self.workflow_config,
                    execution_id=self.execution_id,
                ),
            )

    def initialize_node(
        self, state, workflow, workflow_config: WorkflowConfig, map_states: list[str], convergence_nodes: set[str]
    ):
        """Initialize a workflow node with proper defer configuration.

        Args:
            state: Node configuration
            workflow: LangGraph StateGraph instance
            workflow_config: Full workflow configuration
            map_states: List of nodes that use map-reduce pattern
            convergence_nodes: Set of node IDs that have multiple incoming edges
        """
        # Check if this node is a convergence point
        is_convergence_node = state.id in convergence_nodes

        logger.debug(
            f"Initialize workflow node. "
            f"WorkflowId={workflow_config.id}. "
            f"WorkflowName={workflow_config.name}. "
            f"Node={state.id}. "
            f"IsConvergence={is_convergence_node}"
        )

        retry_policy = workflow_config.get_effective_retry_policy(state=state)

        if state.assistant_id:
            node = self.init_agent_node(state, map_states)
            workflow.add_node(state.id, node, retry=retry_policy, defer=is_convergence_node)
        elif state.custom_node_id:
            node = self.init_custom_node(state)
            workflow.add_node(state.id, node, retry=retry_policy, defer=is_convergence_node)
        elif state.tool_id:
            node = self.init_tool_node(state, map_states)
            workflow.add_node(state.id, node, retry=retry_policy, defer=is_convergence_node)
        else:
            raise ValueError(f"Invalid state configuration. {state}")

        if is_convergence_node:
            logger.info(
                f"Node '{state.id}' configured with defer=True (convergence point with multiple incoming edges)"
            )

    def init_custom_node(self, state):
        node = find_custom_node_by_id(self.workflow_config.custom_nodes, state.custom_node_id)
        logger.debug(f"Initialize workflow with custom node. NodeId: {state.custom_node_id}. Node: {node}")
        # Attempt to get the method from the instance based on custom_node_id. Returns None if not found.
        # Dynamically import the workflow.nodes package
        package_name = 'codemie.workflows.nodes'
        package = importlib.import_module(package_name)
        # Iterate through all modules in the workflows.nodes package to match the file name
        for _, module_name, _ in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
            logger.debug(f"Lookup module. {module_name}")
            if module_name.split('.')[-1] == node.custom_node_id:
                module = importlib.import_module(module_name)

                # Find the first class in the module
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    # Find the matching module
                    if obj.__module__ != module_name:
                        continue

                    if not issubclass(obj, BaseNode):
                        continue

                    found_class = obj
                    logger.debug(f"Class {name} found in module {module_name}")
                    instance = found_class(
                        self.callbacks,
                        self.workflow_execution_service,
                        self.thought_queue,
                        workflow_config=self.workflow_config,
                        custom_node=node,
                        workflow_state=state,
                        execution_id=self.execution_id,
                    )
                    return instance

        raise ImportError(f"{node.custom_node_id} class not found in {package_name} package")

    def initialize_assistant(self, assistant: WorkflowAssistant, workflow_state: WorkflowState = None) -> AIToolsAgent:
        return initialize_assistant(
            workflow_assistant=assistant,
            workflow_state=workflow_state,
            user_input=self.user_input,
            file_names=self.file_names,
            user=self.user,
            thought_queue=self.thought_queue,
            resume_execution=self.resume_execution,
            execution_id=self.execution_id,
            project_name=self.workflow_config.project,
            request_headers=self.request_headers,
            disable_cache=self.disable_cache,
        )

    @classmethod
    def get_iterable_items_by_pointer(cls, source: Any, pointer: str):
        result = UnwrappingJsonPointerEvaluator.get_node_by_pointer(source, pointer)

        if isinstance(result, list):
            return result

        return [result]

    @classmethod
    def get_iterable_items_by_key(cls, source: Any, iter_key: str):
        if isinstance(source, list):
            items_to_process = source
        elif isinstance(source, dict):
            items_to_process = source.get(iter_key, [])
        else:
            items_to_process = [source]
        return items_to_process

    @classmethod
    def _build_parallel_context(
        cls, context_store: dict[str, Any], workflow_state: WorkflowState, is_in_iteration: bool
    ) -> dict[str, Any]:
        """Return the context_store copy for a parallel branch.

        For nested iterations (is_in_iteration=True) the original reference is reused.
        For first-level parallelization a copy is made, filtered by include_in_iterator_context.
        ["*"] (default) copies the entire store; any other list whitelists specific keys.
        """
        if is_in_iteration:
            return context_store
        include_keys = workflow_state.next.include_in_iterator_context
        if include_keys == ["*"]:
            return {**context_store}
        return {k: v for k, v in context_store.items() if k in include_keys}

    def continue_iteration(self, state_schema: dict[str, Any], workflow_state: WorkflowState) -> List[Send]:
        messages = get_messages_from_state_schema(state_schema=state_schema)
        context_store = get_context_store_from_state_schema(state_schema=state_schema)
        iter_key = workflow_state.next.iter_key
        send_to_node = workflow_state.next.state_id

        if iter_key in state_schema and not workflow_state.finish_iteration:
            result = state_schema[iter_key]
            items_to_process = [result]
        else:
            last_message_content = state_schema.get(ITER_SOURCE, "")
            result = parse_from_string_representation(last_message_content)
            if iter_key.startswith('/'):
                items_to_process = self.get_iterable_items_by_pointer(result, iter_key)
            else:
                items_to_process = self.get_iterable_items_by_key(result, iter_key)
        # Get iterations counters from schema if present.
        # Will be reused to maintain counters in further steps
        iter_number = state_schema.get(ITERATION_NODE_NUMBER_KEY)
        # Maintain both iterations from schema and from items to process for multiple nodes iterations
        # First iteration will have items_to_process not empty and no TOTAL_ITERATIONS_KEY in schema.
        # Second and further iterations will have TOTAL_ITERATIONS_KEY in schema
        total_iterations = len(items_to_process) if items_to_process else state_schema.get(TOTAL_ITERATIONS_KEY, 0)

        if not isinstance(items_to_process, list):
            raise ValueError(f"Expected list of items to process in {iter_key}. Got: {items_to_process}")

        logger.debug(
            f"Continue iteration. SendToNode: {send_to_node}. "
            f"IterKey: {iter_key}. "
            f"{iter_number} of {total_iterations}. "
        )

        # Only clone for first-level parallelization, not for nested iterations
        # Detect nested iteration: if ITERATION_NODE_NUMBER_KEY exists, we're already in a parallel branch
        is_in_iteration = iter_number is not None and iter_number > 0

        parallel_context = self._build_parallel_context(context_store, workflow_state, is_in_iteration)

        send_actions = [
            Send(
                send_to_node,
                {
                    TASK_KEY: item,
                    # Clone only for first-level parallelization, not nested
                    MESSAGES_VARIABLE: messages.copy() if not is_in_iteration else messages,
                    CONTEXT_STORE_VARIABLE: parallel_context,
                    ITERATION_NODE_NUMBER_KEY: iter_number if iter_number else index + 1,
                    TOTAL_ITERATIONS_KEY: total_iterations,
                    FIRST_STATE_IN_ITERATION: iter_key not in state_schema,
                    PREVIOUS_EXECUTION_STATE_ID: state_schema.get(PREVIOUS_EXECUTION_STATE_ID),
                    PREVIOUS_EXECUTION_STATE_NAME: state_schema.get(PREVIOUS_EXECUTION_STATE_NAME),
                },
            )
            for index, item in enumerate(items_to_process)
        ]
        return send_actions

    def init_workflow_edges(self, workflow: StateGraph, workflow_config: WorkflowConfig):
        enable_summarization_node = self.workflow_config.enable_summarization_node
        for transition in workflow_config.states:
            self._process_transition(workflow, transition, enable_summarization_node)

        if enable_summarization_node:
            workflow.add_edge(RESULT_FINALIZER_NODE, END)

    def _process_transition(self, workflow: StateGraph, transition, enable_summarization_node: bool):
        """Process a single transition and add appropriate edges to the workflow."""
        if transition.next.state_id:
            self._handle_single_state(workflow, transition, enable_summarization_node)

        if transition.next.state_ids:
            self._handle_multiple_states(workflow, transition, enable_summarization_node)

        if transition.next.condition:
            self._handle_condition(workflow, transition, enable_summarization_node)

        if transition.next.switch:
            self._handle_switch(workflow, transition, enable_summarization_node)

    def _handle_single_state(self, workflow: StateGraph, transition, enable_summarization_node: bool):
        source = transition.id
        target = get_final_state(transition.next.state_id, enable_summarization_node)
        transition_nodes = {target: target, source: source}
        if transition.next.iter_key:
            workflow.add_conditional_edges(
                source,
                lambda _self=self, _transition=transition: self.continue_iteration(_self, _transition),
                transition_nodes,
            )

        else:
            workflow.add_edge(source, target)

    def _handle_multiple_states(self, workflow: StateGraph, transition, enable_summarization_node: bool):
        for next_state in transition.next.state_ids:
            target = get_final_state(next_state, enable_summarization_node)
            workflow.add_edge(transition.id, target)

    def _handle_condition(self, workflow: StateGraph, transition, enable_summarization_node: bool):
        source = transition.id
        condition = transition.next.condition
        then_state = get_final_state(condition.then, enable_summarization_node)
        otherwise_state = get_final_state(condition.otherwise, enable_summarization_node)
        transition_nodes = {then_state: then_state, otherwise_state: otherwise_state, source: source}

        workflow.add_conditional_edges(
            source,
            lambda _self=self,
            _transition=transition,
            _enable_summarization_node=self.workflow_config.enable_summarization_node: evaluate(
                _self, _transition, _enable_summarization_node
            ),
            transition_nodes,
        )

    def _handle_switch(self, workflow: StateGraph, transition, enable_summarization_node: bool):
        source = transition.id
        default_state = get_final_state(transition.next.switch.default, enable_summarization_node)
        transition_nodes = {default_state: default_state, source: source}

        for switch_case in transition.next.switch.cases:
            target = get_final_state(switch_case.state_id, enable_summarization_node)
            transition_nodes[target] = target

        workflow.add_conditional_edges(
            source,
            lambda _self=self,
            _transition=transition,
            _enable_summarization_node=self.workflow_config.enable_summarization_node: evaluate(
                _self, _transition, _enable_summarization_node
            ),
            transition_nodes,
        )

    def init_agent_node(
        self, state: WorkflowState, map_states: list[str], assistant: Assistant | VirtualAssistant | None = None
    ) -> Callable:
        return AgentNode(
            callbacks=self.callbacks,
            workflow_execution_service=self.workflow_execution_service,
            thought_queue=self.thought_queue,
            node_name=state.id,
            summarize_history=True,
            workflow_state=state,
            current_task_key=TASK_KEY if state.id in map_states else None,
            workflow_config=self.workflow_config,
            assistant=assistant,
            user_input=self.user_input,
            user=self.user,
            resume_execution=self.resume_execution,
            execution_id=self.execution_id,
            file_names=self.file_names,
            request_headers=self.request_headers,
            disable_cache=self.disable_cache,
        )

    def init_tool_node(self, state: WorkflowState, map_states: list[str]) -> Callable:
        """Initialize a node that calls a tool directly"""
        return ToolNode(
            callbacks=self.callbacks,
            workflow_execution_service=self.workflow_execution_service,
            thought_queue=self.thought_queue,
            node_name=state.id,
            summarize_history=True,
            workflow_state=state,
            current_task_key=TASK_KEY if state.id in map_states else None,
            workflow_config=self.workflow_config,
            assistant=None,
            user_input=self.user_input,
            user=self.user,
            resume_execution=self.resume_execution,
            execution_id=self.execution_id,
            request_headers=self.request_headers,
            file_names=self.file_names,
        )

    def find_map_nodes(self) -> list[str]:
        """
        Finds and returns the map state ID and task list key from the workflow configuration.
        """
        map_nodes = []
        for state in self.workflow_config.states:
            if state.next.iter_key:
                map_nodes.append(state.next.state_id or state.next.condition.then)
        return map_nodes

    def _execute_workflow_stream(self, enable_verbose_consumer: bool = False):
        """
        Shared workflow execution logic for both background and streaming modes.

        Args:
            enable_verbose_consumer: Whether to start ThoughtConsumer for database persistence
        """
        # Set user context for LLM budget tracking in this workflow execution thread
        # This must be set here since background tasks don't inherit context from the request handler
        if self.user:
            from codemie.configs.logger import set_logging_info

            user_email = self.user.username or self.user.id
            set_logging_info(
                uuid=self.execution_id, user_id=self.user.id, conversation_id=self.execution_id, user_email=user_email
            )

            # # Set LiteLLM context with user's credentials for workflow execution
            # # This ensures all LLM invocations in workflow nodes use the user's LiteLLM key
            from codemie.service.llm_service.utils import set_llm_context

            set_llm_context(None, self.workflow_config.project, self.user)

            logger.debug(
                f"Set user context for workflow execution: user_email={user_email}, "
                f"execution_id={self.execution_id}, project={self.workflow_config.project}"
            )

        self._start_thought_consumer_if_enabled(enable_verbose_consumer)
        graph_config = self._build_graph_config()
        chunks_collector = []

        try:
            self._run_workflow_execution(graph_config, chunks_collector)
        except InterruptedException as e:
            self._handle_interrupt(str(e.message), e.interrupted_state, chunks_collector)
        except Exception as e:
            self._handle_task_exception(e, chunks_collector)
        finally:
            self.thought_queue.close()
            if self.delete_on_completion:
                self._auto_delete_execution()
            # Clear trace context to prevent memory leaks
            clear_workflow_trace_context(self.execution_id)
            VirtualAssistantService.delete_by_execution_id(self.execution_id)

    def _start_thought_consumer_if_enabled(self, enable_verbose_consumer: bool):
        """Start ThoughtConsumer for database persistence if enabled."""
        if enable_verbose_consumer:
            ThoughtConsumer.run(execution_id=self.execution_id, message_queue=self.thought_queue)

    def _build_graph_config(self) -> RunnableConfig:
        """Build LangGraph configuration with callbacks and limits."""
        recursion_limit = self.workflow_config.recursion_limit or RECURSION_LIMIT
        max_concurrency = self.workflow_config.get_max_concurrency()

        graph_config = RunnableConfig(
            configurable={"thread_id": self.execution_id},
            recursion_limit=recursion_limit,
            max_concurrency=max_concurrency,
            callbacks=[],
        )

        if langfuse_handler := get_langfuse_callback_handler():
            graph_config["callbacks"].append(langfuse_handler)

            # Create trace context for workflow trace unification
            trace_context = create_workflow_trace_context(
                execution_id=self.execution_id,
                workflow_id=self.workflow_config.id,
                workflow_name=self.workflow_config.name,
                user_id=str(self.user.id) if self.user else None,
                session_id=self.session_id,
                tags=self.tags,
            )

            # Add metadata from trace context
            if trace_context and trace_context.metadata:
                graph_config["run_name"] = trace_context.metadata.get("run_name")
                if "metadata" not in graph_config:
                    graph_config["metadata"] = {}
                graph_config["metadata"].update(trace_context.metadata)

        return graph_config

    def _run_workflow_execution(self, graph_config: RunnableConfig, chunks_collector: list):
        """Execute the workflow and collect output chunks."""
        inputs = self.on_workflow_start()
        workflow = self._init_workflow()

        self._process_workflow_chunks(workflow, inputs, graph_config, chunks_collector)
        self._check_for_interruption(workflow, graph_config)
        self.workflow_execution_service.finish()

    def _process_workflow_chunks(self, workflow, inputs, graph_config: RunnableConfig, chunks_collector: list):
        """Stream workflow chunks and collect final summaries."""
        for chunk in workflow.stream(inputs, config=graph_config):
            if not chunk:
                continue

            self._collect_final_summaries_from_chunk(chunk, chunks_collector)

    def _collect_final_summaries_from_chunk(self, chunk, chunks_collector: list):
        """Extract and collect final_summary messages from workflow chunk."""
        for value in chunk.values():
            if self._is_final_summary(value):
                message = f"{value['final_summary']}"
                chunks_collector.append(message)

    def _is_final_summary(self, value) -> bool:
        """Check if chunk value contains a final summary."""
        return value and isinstance(value, dict) and "final_summary" in value

    def _check_for_interruption(self, workflow, graph_config: RunnableConfig):
        """Check if workflow was interrupted and raise exception if needed."""
        if not self._interrupt_before_states:
            return

        state = workflow.get_state(config=graph_config)
        if state.next:
            last_message = state.values['messages'][-1].content
            interrupted_state = next((s for s in state.next if s in self._interrupt_before_states), state.next[0])
            raise InterruptedException(last_message, interrupted_state)

    def stream(self):
        """Execute workflow in background mode with ThoughtConsumer for database persistence"""
        self._execute_workflow_stream(enable_verbose_consumer=True)

    def stream_to_client(self):
        """
        Stream workflow execution directly to client via ThreadedGenerator.

        Also enables ThoughtConsumer for database persistence when using DualQueue,
        allowing thoughts to be saved even during streaming mode.
        """
        self._execute_workflow_stream(enable_verbose_consumer=True)

    def on_workflow_start(self):
        input_message = HumanMessage(content=self.user_input)
        messages = [input_message]
        initial_context = parse_from_string_representation(self.user_input)
        if not isinstance(initial_context, dict):
            initial_context = {}
        if self.file_names:
            from codemie_tools.base.file_object import FileObject

            initial_context["file_names"] = [FileObject.from_encoded_url(fn).name for fn in self.file_names]
        if self.resume_execution and self.execution_id:
            inputs = None  # None means langchain will use the last checkpoint
            self.workflow_execution_config = WorkflowService.find_workflow_execution_by_id(self.execution_id)
            self.workflow_execution_config.start_progress()
            self.workflow_execution_service.resume_states()
        elif self.execution_id:
            # If the execution already present (normal flow from the frontend)
            # Store user_input as string directly
            inputs = {MESSAGES_VARIABLE: messages, CONTEXT_STORE_VARIABLE: initial_context, USER_INPUT: self.user_input}
            logger.debug(f"Streaming workflow. Name={self.workflow_config.name}. UserInput={messages}")
            self.workflow_execution_config = WorkflowService.find_workflow_execution_by_id(self.execution_id)
        else:
            # Store user_input as string directly
            inputs = {MESSAGES_VARIABLE: messages, CONTEXT_STORE_VARIABLE: initial_context, USER_INPUT: self.user_input}
            logger.debug(f"Streaming workflow. Name={self.workflow_config.name}. UserInput={messages}")
            self.workflow_execution_config = WorkflowService.create_workflow_execution(
                self.workflow_config, self.user.as_user_model(), self.user_input
            )
            self.execution_id = self.workflow_execution_config.execution_id
        return inputs

    @property
    def _interrupt_before_states(self):
        """Returns array of states to wait for user confirmation"""
        return [state.id for state in self.workflow_config.states if state.interrupt_before]

    def _handle_interrupt(self, message: str, interrupted_state: str, chunks_collector: List[str]) -> None:
        """Handle when workflow was interrupted by user"""
        self.workflow_execution_service.interrupt(interrupted_state)

        chunks_collector.append(self.INTERRUPT_CONFIRMATION_MSG)
        chunks_collector.append(message)
        logger.info("Workflow was interrupted")

    def _handle_task_exception(self, e: Exception, chunks_collector: List[str]) -> None:
        """Handle when an exception is raised during the workflow execution"""
        stacktrace = traceback.format_exc()
        exception_type = type(e).__name__
        error_message = str(e)
        full_error_message = f"An error occured during execution: \n {exception_type}: {error_message}"

        state_id = self.workflow_execution_service.start_state(workflow_state_id="AI/Run Thoughts", task="")
        logger.error(full_error_message)
        self.thought_queue.send(
            StreamedGenerationResult(
                thought=Thought(
                    id=str(uuid.uuid4()),
                    in_progress=False,
                    message=full_error_message,
                    author_type=ThoughtAuthorType.System.value,
                    author_name=exception_type,
                ),
                context={"execution_state_id": state_id},
            ).model_dump_json()
        )

        self.workflow_execution_service.finish_state(
            execution_state_id=state_id, output=error_message, status=WorkflowExecutionStatusEnum.FAILED
        )

        self.workflow_execution_service.fail(error_class=exception_type, error_message=error_message)

        chunks_collector.append(f"AI Agent run failed with error: {exception_type}: {error_message}")

        logger.error(f"AI Agent run failed with error: {stacktrace}", exc_info=True)

    def _auto_delete_execution(self):
        """Auto-delete workflow execution after completion if delete_on_completion is set."""
        try:
            execution = WorkflowService.find_workflow_execution_by_id(self.execution_id)
            if not execution:
                logger.warning(f"Cannot auto-delete: execution {self.execution_id} not found")
                return

            # Only delete on terminal states (success/failure), not interrupt/abort
            terminal_states = (WorkflowExecutionStatusEnum.SUCCEEDED, WorkflowExecutionStatusEnum.FAILED)
            if execution.overall_status not in terminal_states:
                logger.info(
                    f"Skipping auto-delete for execution {self.execution_id}: "
                    f"status={execution.overall_status}, not terminal"
                )
                return

            # Skip if execution is part of a conversation (would leave dangling refs)
            if execution.conversation_id:
                logger.warning(
                    f"Skipping auto-delete for execution {self.execution_id}: "
                    f"linked to conversation {execution.conversation_id}"
                )
                return

            # Langfuse trace safety: By this point (finally block of _execute_workflow_stream),
            # all LangGraph callbacks have fired and any Langfuse trace is already formed
            # and queued in the SDK's internal buffer for async sending. No explicit check needed.
            WorkflowExecution.delete(execution.id)
            logger.info(f"Auto-deleted workflow execution {self.execution_id}")
        except Exception as e:
            logger.error(f"Failed to auto-delete workflow execution {self.execution_id}: {e}")
