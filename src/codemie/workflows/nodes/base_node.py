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
import traceback
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Type, Any, Optional

from langgraph.types import Command

from codemie.configs import logger
from codemie.core.workflow_models import WorkflowExecutionStatusEnum, WorkflowState, WorkflowConfig
from codemie.core.thought_queue import ThoughtQueue
from codemie.rest_api.models.assistant import AssistantBase
from codemie.rest_api.models.guardrail import GuardrailEntity, GuardrailSource
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.guardrail.utils import EntityConfig
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.service.workflow_service import WorkflowService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.constants import (
    GUARDRAIL_CHECKED_FLAG,
    MESSAGES_VARIABLE,
    ABORTED_MSG,
    NEXT_KEY,
    END_NODE,
    TASK_KEY,
    CONTEXT_STORE_VARIABLE,
    ITER_SOURCE,
    ITERATION_NODE_NUMBER_KEY,
    TOTAL_ITERATIONS_KEY,
    FIRST_STATE_IN_ITERATION,
    CLEAR_CONTEXT_STORE_KEEP_CURRENT,
    CONTEXT_STORE_KEEP_NEW_ONLY_FLAG,
    PREVIOUS_EXECUTION_STATE_ID,
)
from codemie.workflows.models import CONTEXT_STORE_DELETE_MARKER, CONTEXT_STORE_APPEND_MARKER
from codemie.workflows.utils import (
    prepare_messages,
    parse_from_string_representation,
    evaluate_next_candidate,
    exclude_prior_messages,
    serialize_state,
    check_state_size,
)
from codemie.workflows.utils.guardrail_replacement_utils import extract_message_texts, update_message_texts

StateSchemaType = TypeVar('StateSchemaType', bound=dict[str, Any])


class ExecutionAbortedException(Exception):
    pass


class BaseNode(ABC, Generic[StateSchemaType]):
    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        node_name: Optional[str] = "",
        execution_id: Optional[str] = None,
        workflow_state: WorkflowState = None,
        workflow_config: Optional[WorkflowConfig] = None,
        *args,
        **kwargs,
    ):
        """Initialize the BaseNode with required services and configuration.

        Args:
            callbacks: List of callback handlers for node lifecycle events
            workflow_execution_service: Service for managing workflow execution states
            thought_queue: Queue for managing AI agent thoughts and reasoning
            node_name: Optional name identifier for this node. Defaults to class name if empty
            execution_id: Optional unique identifier for the current execution
            workflow_state: Configuration and state information for the workflow
            workflow_config: Optional workflow configuration containing assistants, tools, and settings
            *args: Additional positional arguments passed to child classes
            **kwargs: Additional keyword arguments passed to child classes
        """
        self.node_name = node_name
        self.callbacks = callbacks
        self.workflow_execution_service = workflow_execution_service
        self.thought_queue = thought_queue
        self.execution_id = execution_id
        self.workflow_state: WorkflowState = workflow_state
        self.workflow_config: Optional[WorkflowConfig] = workflow_config
        self.args = args
        self.kwargs = kwargs

    @abstractmethod
    def execute(self, state_schema: Type[StateSchemaType], execution_context: dict) -> Any:
        """Execute the main logic of this node.

        This is an abstract method that must be implemented by child classes.
        It contains the core functionality that the node performs during workflow execution.

        Args:
            state_schema: The current state schema containing workflow data and messages
            execution_context: Dictionary containing execution context variables and services

        Returns:
            Any: The result of the node execution, which will be processed by post_process_output

        Raises:
            NotImplementedError: If not implemented by child class
        """
        pass

    @abstractmethod
    def get_task(self, state_schema: Type[StateSchemaType], *arg, **kwargs):
        """Get the task description for this node.

        This is an abstract method that must be implemented by child classes.
        It should return a string describing what task this node is performing,
        which is used for logging, monitoring, and user feedback.

        Args:
            state_schema: The current state schema containing workflow data and messages
            *arg: Additional positional arguments
            **kwargs: Additional keyword arguments

        Returns:
            str: A human-readable description of the task being performed

        Raises:
            NotImplementedError: If not implemented by child class
        """
        pass

    def __call__(self, state_schema: Type[StateSchemaType]):
        """Execute the node with full lifecycle management.

        This method orchestrates the complete node execution lifecycle including:
        - Pre-execution hooks and checks
        - Callback notifications
        - Actual execution
        - Post-processing
        - Error handling
        - State finalization

        Args:
            state_schema: The current state schema containing workflow data and messages

        Returns:
            dict: Updated state schema with execution results and next step information

        Raises:
            ExecutionAbortedException: If execution is aborted during processing
            Exception: Any exception from the execute method is re-raised after cleanup
        """
        execution_context = self.generate_execution_context(state_schema)

        if command := self.before_execution(state_schema, execution_context, self.args, self.kwargs):
            logger.info(f"Redirecting to: {command}, from: {self.get_node_name(state_schema)}")
            return command

        task = self.get_task(state_schema, self.args, self.kwargs)
        execution_state_id = self.workflow_execution_service.start_state(
            workflow_state_id=self.get_node_name(state_schema),
            task=task,
        )
        raw_output = None

        try:
            if self._is_execution_aborted():
                raise ExecutionAbortedException

            # Call the on_start method of each callback
            for callback in self.callbacks:
                callback.on_node_start(
                    state_id=execution_state_id,
                    node_name=self.get_node_name(state_schema),
                    task=task,
                    execution_context=execution_context,
                )

            raw_output = self.execute(state_schema, execution_context)
            processed_output = self.post_process_output(state_schema, task, raw_output)

            if self._is_execution_aborted():
                status = WorkflowExecutionStatusEnum.ABORTED
            else:
                status = WorkflowExecutionStatusEnum.SUCCEEDED

            self.workflow_execution_service.finish_state(
                execution_state_id=execution_state_id, output=processed_output, status=status
            )

            # Call the on_complete method of each callback
            for callback in self.callbacks:
                callback.on_node_end(
                    output=processed_output,
                    execution_state_id=execution_state_id,
                    execution_context=execution_context,
                )

            final_state = self.finalize_and_update_state(
                raw_output=raw_output,
                processed_output=processed_output,
                success=True,  # add logic to make it dynamic
                state_schema=state_schema,
            )

            # Record transition from previous state to current state
            # workflow_context captures the state at the transition point (input to this node)
            previous_state_id = state_schema.get(PREVIOUS_EXECUTION_STATE_ID)

            # Serialize INPUT state (before node execution) with size limits
            # Exclude internal tracking fields from workflow_context
            state_for_serialization = {k: v for k, v in state_schema.items() if k != PREVIOUS_EXECUTION_STATE_ID}
            serialized_state = serialize_state(state_for_serialization)
            checked_state = check_state_size(serialized_state, self.workflow_execution_service.workflow_execution_id)

            self.workflow_execution_service.record_transition(
                from_state_id=previous_state_id,
                to_state_id=execution_state_id,
                workflow_context=checked_state,
            )

            # Store current execution state ID for next node in this track
            final_state[PREVIOUS_EXECUTION_STATE_ID] = execution_state_id

            return final_state
        except ExecutionAbortedException:
            self.workflow_execution_service.abort_state(execution_state_id)
            return {MESSAGES_VARIABLE: [ABORTED_MSG], NEXT_KEY: [END_NODE]}
        except Exception as e:
            self.handle_execution_failure(e)
            self.workflow_execution_service.finish_state(
                execution_state_id,
                output=str(e),
                status=WorkflowExecutionStatusEnum.FAILED,
            )
            # Call the on_complete method of each callback
            for callback in self.callbacks:
                callback.on_node_fail(exception=e, args=self.args, kwargs=self.kwargs)
            raise e
        finally:
            self.after_execution(result=raw_output, state_schema=state_schema, args=self.args, kwargs=self.kwargs)

    @classmethod
    def _prepare_iter_task_messages(cls, state_schema: Type[StateSchemaType]) -> list[str]:
        """Extract iteration task messages from state schema.

        Args:
            state_schema: The current state schema

        Returns:
            list[str]: List containing the task representation if task exists
        """
        fist_state_in_iteration = state_schema.get(FIRST_STATE_IN_ITERATION, False)
        if fist_state_in_iteration and (task := state_schema.get(TASK_KEY)):
            return [repr(task)]
        return []

    def _prepare_context_store_update(self, iter_task_messages: list[str], processed_output: str) -> list[str]:
        """Prepare context store updates based on configuration flags.

        Args:
            iter_task_messages: List of iteration task messages
            processed_output: The processed output string

        Returns:
            list[str]: Context store strings to add
        """
        if not self.workflow_state:
            return []

        state_next = self.workflow_state.next
        if not state_next.store_in_context:
            return []

        # Convert all messages to strings for context store
        context_messages = iter_task_messages + [processed_output]
        prepared_context_store = [str(msg_content) for msg_content in context_messages]
        return prepared_context_store

    def _prepare_message_history_update(
        self, state_schema: Type[StateSchemaType], iter_task_messages: list[str], processed_output: str, success: bool
    ) -> list:
        """Prepare message history updates based on configuration flags.

        Args:
            state_schema: The current state schema
            iter_task_messages: List of iteration task messages
            processed_output: The processed output string
            success: Whether execution was successful

        Returns:
            list: Message history entries to add
        """
        if not self.workflow_state:
            return []

        state_next = self.workflow_state.next
        should_include_in_llm = state_next.include_in_llm_history
        should_exclude_prior = state_next.clear_prior_messages

        if not should_include_in_llm and not should_exclude_prior:
            return []

        if should_include_in_llm:
            llm_messages = iter_task_messages + [processed_output]
            new_messages = prepare_messages(
                llm_messages,
                success,
                self.workflow_state.result_as_human_message,
            )

            if should_exclude_prior:
                num_new = len(llm_messages)
                newly_added = new_messages[-num_new:] if num_new > 0 else []
                return exclude_prior_messages(state_schema, newly_added)
            return new_messages

        # Only exclude_prior is set
        return exclude_prior_messages(state_schema, [])

    def _build_base_final_state(
        self, processed_output: str, prepared_messages: list, prepared_context_store: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Build the base final state dictionary.

        Args:
            processed_output: The processed output string
            prepared_messages: Prepared message history
            prepared_context_store: Prepared context store dictionary

        Returns:
            dict: Base final state with messages and next key
        """
        enable_summarization = self.workflow_config.enable_summarization_node if self.workflow_config else False

        # Determine next node
        if self.workflow_state:
            next_node = evaluate_next_candidate(processed_output, self.workflow_state, enable_summarization)
        else:
            next_node = END_NODE

        return {
            MESSAGES_VARIABLE: prepared_messages,
            CONTEXT_STORE_VARIABLE: prepared_context_store,
            NEXT_KEY: [next_node],
        }

    def _add_iteration_state(
        self, final_state: dict[str, Any], state_schema: Type[StateSchemaType], processed_output: str
    ) -> None:
        """Add iteration-specific state if iter_key is configured.

        Args:
            final_state: The final state dictionary to update
            state_schema: The current state schema
            processed_output: The processed output string
        """
        if not self.workflow_state:
            return

        state_next = self.workflow_state.next
        if not state_next.iter_key:
            return

        final_state[ITER_SOURCE] = processed_output
        final_state[ITERATION_NODE_NUMBER_KEY] = state_schema.get(ITERATION_NODE_NUMBER_KEY, 0)
        final_state[TOTAL_ITERATIONS_KEY] = state_schema.get(TOTAL_ITERATIONS_KEY, 0)

        # Handle current_task_key if present (agent_node specific logic)
        current_task_key = self.kwargs.get('current_task_key')
        if current_task_key:
            final_state[state_next.iter_key] = (
                processed_output if state_next.override_task else state_schema.get(current_task_key, "")
            )

    def _add_output_key(self, final_state: dict[str, Any], raw_output: Any, processed_output: str) -> None:
        """Add output key to final state if configured.

        Args:
            final_state: The final state dictionary to update
            raw_output: The raw output from execution
            processed_output: The processed output string
        """
        if not self.workflow_state:
            return

        output_key = self.workflow_state.next.output_key
        if not output_key:
            return

        actual_value = raw_output.result if hasattr(raw_output, 'result') else processed_output

        if self.workflow_state.next.append_to_context:
            # When accumulating, write only to context_store (not top-level state key)
            # so consumers read the accumulated list from context_store
            final_state[CONTEXT_STORE_VARIABLE][output_key] = {CONTEXT_STORE_APPEND_MARKER: [actual_value]}
        else:
            final_state[output_key] = actual_value
            final_state[CONTEXT_STORE_VARIABLE][output_key] = actual_value

    @classmethod
    def _merge_list_to_dict(cls, parsed_output: list) -> dict:
        """Merge all dict items from a list into a single dictionary.

        Args:
            parsed_output: List potentially containing dict items

        Returns:
            dict: Merged dictionary from all dict items in the list
        """
        merged_dict = {}
        for item in parsed_output:
            if isinstance(item, dict):
                merged_dict.update(item)
        return merged_dict

    def _collect_new_values(self, state_schema: Type[StateSchemaType], processed_output: str) -> dict:
        """Collect new values from state schema task and processed output.

        Args:
            state_schema: The current state schema
            processed_output: The processed output string

        Returns:
            dict: Combined dictionary of new values from task and output
        """
        new_values = {}

        # Add task values if available and not in iteration
        # Skip task values when IN_ITERATION is true to avoid polluting context store
        first_state_in_iteration = state_schema.get(FIRST_STATE_IN_ITERATION, False)
        if first_state_in_iteration:
            task = state_schema.get(TASK_KEY)
            if isinstance(task, dict):
                new_values.update(task)

        # Parse and add output values
        parsed_output = parse_from_string_representation(processed_output)

        if isinstance(parsed_output, list):
            parsed_output = self._merge_list_to_dict(parsed_output)

        if isinstance(parsed_output, dict):
            new_values.update(parsed_output)

        return new_values

    @classmethod
    def _apply_deletion_markers(cls, dynamic_values: dict, keys_to_reset: list[str]) -> None:
        """Mark specified keys for deletion in the context store.

        Args:
            dynamic_values: Dictionary to mark keys in
            keys_to_reset: List of keys to mark for deletion
        """
        for key in keys_to_reset:
            dynamic_values[key] = CONTEXT_STORE_DELETE_MARKER

    def _prepare_resolved_context_store(
        self, state_schema: Type[StateSchemaType], processed_output: str
    ) -> dict[str, Any] | None:
        """Prepare and resolve context store with new values and deletions.

        Args:
            state_schema: The current state schema
            processed_output: The processed output string

        Returns:
            dict[str, str] | None: Resolved context store, or None if cleared
        """
        if not self.workflow_state:
            return {}

        state_next = self.workflow_state.next

        # Handle clear context store flag
        clear_context_store = state_next.clear_context_store
        if isinstance(clear_context_store, bool) and clear_context_store:
            return None

        if not state_next.store_in_context:
            return {}

        # Collect new values from task and output
        new_values = self._collect_new_values(state_schema, processed_output)

        # Wrap values for append keys with sentinel so the reducer accumulates them
        if state_next.append_to_context:
            new_values = {key: {CONTEXT_STORE_APPEND_MARKER: [value]} for key, value in new_values.items()}

        # Insert sentinel AFTER wrapping so the reducer can recognize it as a plain flag
        if clear_context_store == CLEAR_CONTEXT_STORE_KEEP_CURRENT:
            new_values[CONTEXT_STORE_KEEP_NEW_ONLY_FLAG] = True

        # Apply deletion markers if configured
        if state_next.reset_keys_in_context_store:
            self._apply_deletion_markers(new_values, state_next.reset_keys_in_context_store)

        return new_values

    def finalize_and_update_state(
        self, raw_output: Any, processed_output: str, success: bool, state_schema: Type[StateSchemaType]
    ) -> dict[str, Any]:
        """Finalize execution and update workflow state with results.

        This method updates the workflow state schema based on the configured flags:
        - store_in_context: Controls whether to add to context store
        - include_in_llm_history: Controls whether to add to message history
        - clear_prior_messages: Removes all prior messages from LLM history
        - clear_context_store: Clears the context store

        NEW: Context store now contains pre-resolved dynamic values as dict[str, str]

        Args:
            raw_output: The raw output from execution
            processed_output: The processed output string
            success: Whether execution was successful
            state_schema: The current state schema to update

        Returns:
            dict: Updated state schema with messages, context store, and next step info
        """
        # Prepare iteration task messages
        prepared_context_store = self._prepare_resolved_context_store(state_schema, processed_output)

        # Prepare message history updates
        iter_task_messages = self._prepare_iter_task_messages(state_schema)
        prepared_messages = self._prepare_message_history_update(
            state_schema, iter_task_messages, processed_output, success
        )

        # Build final state
        final_state = self._build_base_final_state(processed_output, prepared_messages, prepared_context_store)
        self._add_iteration_state(final_state, state_schema, processed_output)
        self._add_output_key(final_state, raw_output, processed_output)

        return final_state

    def _is_execution_aborted(self) -> bool:
        """Check if the current workflow execution has been aborted.

        This method queries the workflow execution service to determine if the
        current execution has been marked as aborted by an external trigger.

        Returns:
            bool: True if execution is aborted, False otherwise
        """
        if not self.execution_id:
            return False

        workflow_execution = WorkflowService.find_workflow_execution_by_id(self.execution_id)
        if not workflow_execution:
            logger.warning(
                f"Workflow execution not found for execution_id: {self.execution_id}. "
                "Assuming execution is not aborted."
            )
            return False
        return workflow_execution.overall_status == WorkflowExecutionStatusEnum.ABORTED

    def before_execution(
        self, state_schema: Type[StateSchemaType], execution_context: dict, *args, **kwargs
    ) -> Command or None:
        """Hook method called before node execution.

        This method provides an opportunity for child classes to perform pre-execution
        logic, validation, or routing decisions. If a Command is returned, it will
        redirect the workflow to a different node.

        It also covers the guardrail application for each node validating the input based
        on assigned guardrails for the workflow itself and for specific assistants.

        Args:
            state_schema: The current state schema
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments

        Returns:
            Command or None: Command to redirect workflow, or None to continue normal execution
        """
        if self.workflow_config and self.workflow_config.id:
            agent = execution_context.get("assistant")
            assistant = getattr(agent, 'assistant', None) if agent else None

            self._apply_node_input_guardrails(state_schema, assistant=assistant)

        pass

    def after_execution(self, state_schema: Type[StateSchemaType], result: Any, *args, **kwargs):
        """Hook method called after node execution.

        This method provides an opportunity for child classes to perform post-execution
        cleanup, logging, or state management.

        Args:
            state_schema: The current state schema
            result: The result returned from the execute method
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments
        """
        pass

    def handle_execution_failure(self, e: Exception):
        """Handle execution failures and log error information.

        This method is called when an exception occurs during node execution.
        It logs the error with stack trace information for debugging purposes.

        Args:
            e: The exception that occurred during execution
        """
        logger.error(f"Exception occurred in node: {e}, {traceback.format_exc()}")

    def get_node_name(self, state_schema: Type[StateSchemaType]):
        """Get the display name for this node.

        Returns the configured node name if available, otherwise falls back
        to the class name. This name is used for logging and monitoring.

        Args:
            state_schema: The current state schema (unused in base implementation)

        Returns:
            str: The display name for this node
        """
        return self.node_name if self.node_name else self.__class__.__name__

    def generate_execution_context(self, state_schema: Type[StateSchemaType]):
        """Generate the execution context dictionary for this node.

        Creates a dictionary containing context variables that will be passed
        to the execute method. Child classes can override this to provide
        additional context-specific data.

        Returns:
            dict: Dictionary containing execution context variables
        """
        return {
            **self.kwargs,
        }

    def post_process_output(self, state_schema: Type[StateSchemaType], task, output) -> str:
        """Post-process the raw output from the execute method.

        This method converts the raw output from execute() into a string format
        suitable for storage and further processing. Child classes can override
        this to implement custom output formatting.

        Args:
            state_schema: The current state schema
            task: The task description from get_task()
            output: The raw output from the execute method

        Returns:
            str: Processed output as a JSON string
        """
        return json.dumps(output)

    def _apply_node_input_guardrails(
        self, state_schema: Type[StateSchemaType], assistant: Optional[AssistantBase] = None
    ):
        """
        Apply workflow guardrails to the input for this node.

        Extracts the most recent human message from state and validates it
        against workflow guardrails if configured.

        Args:
            state_schema: The current state schema containing messages

        Raises:
            ValueError: If input is blocked by guardrails
        """
        from codemie.workflows.utils import get_messages_from_state_schema

        workflow_state_id = self.workflow_state.id if self.workflow_state else None

        # Get messages from state and get the most recent message (the input to this node)
        messages = get_messages_from_state_schema(state_schema)

        recent_message = messages[-1] if messages else None
        if not recent_message or not self.workflow_config:
            return

        # Check if this message has already been guardrailed - each node only validates
        # the last message - this is for workflow retries only
        metadata = getattr(recent_message, 'additional_kwargs', {}).get("metadata", {})
        if metadata.get(GUARDRAIL_CHECKED_FLAG):
            logger.debug(f"Node {workflow_state_id}: Input already guardrailed, skipping")
            return

        logger.debug(
            f"Applying workflow guardrails to node input. "
            f"WorkflowId={self.workflow_config.id}, "
            f"NodeId={workflow_state_id}"
        )

        entity_configs = [
            EntityConfig(
                entity_type=GuardrailEntity.WORKFLOW,
                entity_id=str(self.workflow_config.id),
                project_name=self.workflow_config.project,
            )
        ]

        if assistant:
            entity_configs.append(
                EntityConfig(
                    entity_type=GuardrailEntity.ASSISTANT,
                    entity_id=str(assistant.id),
                    project_name=assistant.project,
                )
            )

        # We do not validate system prompts as the guardrails will just treat 99% of them as PROMPT_ATTACK
        text_contents = extract_message_texts(recent_message)
        if not text_contents:
            return

        guardrailed_texts, blocked_reasons = GuardrailService.apply_guardrails_for_entities(
            entity_configs=entity_configs,
            input=text_contents,
            source=GuardrailSource.INPUT,
        )

        # should never happen, but covering all bases
        if not isinstance(guardrailed_texts, list):
            logger.error(f"Apply guardrail returned unexpected response, guardrailed_texts={guardrailed_texts}")
            return

        if blocked_reasons:
            logger.warning(
                f"Node input blocked by guardrails. "
                f"WorkflowId={self.workflow_config.id}, "
                f"NodeId={workflow_state_id}, "
                f"Reasons={json.dumps(blocked_reasons, indent=2, default=str)}"
            )

            # Mark workflow as failed
            self.workflow_execution_service.fail(
                error_class="GuardrailBlockedException",
                error_message=(
                    f"Node input blocked by guardrails. Reasons: {json.dumps(blocked_reasons, indent=2, default=str)}"
                ),
            )

            raise ValueError(
                f"Node input blocked by guardrails. Reasons: {json.dumps(blocked_reasons, indent=2, default=str)}"
            )

        # Update message content with guardrailed text
        if any(g != o for g, o in zip(guardrailed_texts, text_contents, strict=True)):
            logger.info(
                f"Node input modified by guardrails. WorkflowId={self.workflow_config.id}, NodeId={workflow_state_id}"
            )
            # mutate the message in place
            update_message_texts(recent_message, guardrailed_texts)

        # ALWAYS mark message as guardrailed (even if content wasn't modified)
        if "metadata" not in recent_message.additional_kwargs:
            recent_message.additional_kwargs["metadata"] = {}

        recent_message.additional_kwargs["metadata"][GUARDRAIL_CHECKED_FLAG] = True
