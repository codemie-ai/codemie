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

import ast
import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import logger
from codemie.core.constants import METADATA_CHUNK_NUM, METADATA_FILE_PATH, METADATA_SOURCE, METADATA_TITLE
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.utils import calculate_tokens
from codemie.core.workflow_models import (
    CustomWorkflowNode,
    WorkflowAssistant,
    WorkflowConfig,
    WorkflowState,
)
from codemie.rest_api.models.base import BaseModelWithSQLSupport
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService
from codemie.workflows.constants import (
    TRIPLE_BACKTICKS,
    TRIPLE_TILDES,
    MESSAGES_VARIABLE,
    MESSAGES_LIMIT,
    MESSAGES_TOKENS_LIMIT,
    END_NODE,
    RESULT_FINALIZER_NODE,
    NEXT_KEY,
    CONTEXT_STORE_VARIABLE,
    USER_INPUT,
)
from codemie.workflows.models import AgentMessages
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage
from langgraph.constants import END


def extract_json_content(response: str) -> Optional[Any]:
    """
    Extract JSON content from a response string.

    This method attempts to find JSON content in the provided response string, supporting two formats:
    1. JSON content explicitly labeled and wrapped in triple backticks.
    2. Plain JSON objects without any special wrapping.

    It first checks if the entire response is valid JSON. If not, it then searches for JSON content
    that matches the specified patterns, using regular expressions.

    Args:
        response (str): The response string to search for JSON content.

    Returns:
        Optional[Any]: The extracted JSON content if found and valid, otherwise None.
    """
    is_valid, parsed_json = _is_valid_json(response)
    if is_valid:
        return parsed_json

    # Define regex patterns to match JSON content with or without backticks
    patterns = [
        rf"\s*{re.escape(TRIPLE_BACKTICKS)}json(.*?)\s*{re.escape(TRIPLE_BACKTICKS)}",
        rf"\s*{re.escape(TRIPLE_TILDES)}json(.*?)\s*{re.escape(TRIPLE_TILDES)}",
        r"(\{.*?})",
    ]

    try:
        for pattern in patterns:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
                is_valid, parsed_json = _is_valid_json(json_str)
                if is_valid:
                    return parsed_json
        return None
    except Exception as e:
        logger.error(f"Error during parsing: {str(e)}", exc_info=True)
        return response


def _is_valid_json(json_str: str) -> Tuple[bool, Optional[Any]]:
    try:
        py_obj = ast.literal_eval(json_str)
        return True, py_obj
    except (ValueError, SyntaxError):
        try:
            parsed_json = json.loads(json_str)
            return True, parsed_json
        except json.JSONDecodeError:
            return False, None


def parse_from_string_representation(response: str) -> Any:
    """
    Parse the output from a response string.

    This function attempts to extract and parse JSON content from the given response string. If no valid JSON
    content is found, the original response string is returned. This is useful for handling mixed content types
    where the response might be a plain string or a JSON object.

    Args:
        response (str): The response string to parse.

    Returns:
        Any: The parsed JSON object if JSON content is found and valid, otherwise the original response string.
    """
    try:
        json_content = extract_json_content(response)
        return json_content if json_content is not None else response
    except Exception as e:
        logger.error(f"Error during parsing: {str(e)}", exc_info=True)
        return response


def find_assistant_by_id(assistants: List[WorkflowAssistant], assistant_id: str) -> Optional[WorkflowAssistant]:
    logger.debug(f"Lookup for assistant. Assistant_id: {assistant_id}")
    for assistant in assistants:
        if assistant.id == assistant_id:
            return assistant
    raise ValueError(
        f"Assistant wasn't found in assistants section. AssistantId: {assistant_id}. AvailableAssistants: {assistants}"
    )


def find_custom_node_by_id(custom_nodes: List[CustomWorkflowNode], node_id: str) -> Optional[CustomWorkflowNode]:
    logger.debug(f"Lookup for custom node. Node_id: {node_id}")
    for node in custom_nodes:
        if node.id == node_id:
            return node
    raise ValueError(
        f"Custom Node wasn't found in custom_nodes section. NodeId: {node_id}. AvailableNodes: {custom_nodes}"
    )


def convert_value(value: Any) -> Any:
    # Convert string representations of booleans, integers, and floats to their actual types
    if isinstance(value, str):
        if value.lower() == 'true':
            return True
        elif value.lower() == 'false':
            return False
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            pass
    return value


def _prepare_local_vars(local_vars: dict[str, Any]) -> dict[str, Any]:
    return {key: convert_value(value) for key, value in local_vars.items()}


def prepare_messages(
    formatted_result_list: list[str],
    success: bool,
    human_message: bool = False,
) -> List[BaseMessage]:
    new_messages = [
        HumanMessage(content=[{"type": "text", "text": formatted_result}])
        if human_message
        else AIMessage(
            content=[{"type": "text", "text": formatted_result}],
            response_metadata={"success": success},
        )
        for formatted_result in formatted_result_list
    ]

    return new_messages


def should_summarize_memory(workflow_config: WorkflowConfig, messages: List[BaseMessage]) -> Tuple[int, bool]:
    messages_limit = (
        workflow_config.messages_limit_before_summarization
        if workflow_config.messages_limit_before_summarization
        else MESSAGES_LIMIT
    )
    tokens_limit = (
        workflow_config.tokens_limit_before_summarization
        if workflow_config.tokens_limit_before_summarization
        else MESSAGES_TOKENS_LIMIT
    )
    total_tokens = calculate_tokens(str(messages))
    should_summarize = len(messages) > messages_limit or total_tokens > tokens_limit
    return total_tokens, should_summarize


def get_workflow_input_message(state_schema: dict[str, Any]) -> Optional[str]:
    """
    Retrieves the 'user_input' field from the given state schema.

    This function safely accesses the 'user_input' field from the provided state_schema.
    If the 'user_input' field does not exist, it returns None.

    Args:
        state_schema: An instance of the AgentMessages dictionary from which the user_input is to be retrieved.

    Returns:
        Optional[str]: The user_input string if it exists, otherwise None.
    """
    return state_schema.get(USER_INPUT)


def get_messages_from_state_schema(state_schema: dict[str, Any]) -> List[BaseMessage]:
    """
    Retrieves the 'messages' field from the given AgentMessages instance.

    This function safely accesses the 'messages' field from the provided state_schema.
    If the 'messages' field does not exist, it returns an empty list by default.

    Args:
        state_schema (AgentMessages): An instance of the AgentMessages dictionary
                                      from which the 'messages' field is to be retrieved.

    Returns:
        List: The 'messages' field from the state_schema if it exists, otherwise an empty list.
    """
    return state_schema.get(MESSAGES_VARIABLE, [])


def get_context_store_from_state_schema(state_schema: dict[str, Any]) -> dict[str, str]:
    """
    Retrieves the 'context_store' field from the given state schema.

    This function safely accesses the context store field from the provided state_schema.
    If the field does not exist, it returns an empty dict by default.

    Args:
        state_schema: An instance of the AgentMessages state schema dictionary
                     from which the context store is to be retrieved.

    Returns:
        dict[str, str]: The context store dictionary with resolved key-value pairs if it exists,
                       otherwise an empty dict.
    """
    return state_schema.get(CONTEXT_STORE_VARIABLE, {})


def exclude_prior_messages(state_schema: dict[str, Any], current_messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    Exclude all prior messages from the message history, keeping only current messages.

    This creates a "fresh start" for the LLM message history while preserving
    the context store for dynamic value resolution.

    Args:
        state_schema: The current state schema
        current_messages: The new messages to keep

    Returns:
        list[BaseMessage]: List containing RemoveMessage for old messages and current messages
    """
    existing_messages = get_messages_from_state_schema(state_schema)

    if not existing_messages:
        return current_messages

    logger.info(f"Excluding {len(existing_messages)} prior messages from LLM history")

    # Create RemoveMessage for each existing message
    remove_messages = [RemoveMessage(id=msg.id) for msg in existing_messages]

    # Return removed messages + new messages
    return remove_messages + current_messages


def get_final_state(state_id: str, enable_summarization_node: bool) -> str:
    if state_id != END_NODE:
        return state_id
    return RESULT_FINALIZER_NODE if enable_summarization_node else END


def get_documents_tree_by_datasource_id(datasource_id: str, include_content: bool = False) -> List[Dict[str, Any]]:
    datasource = IndexInfo.find_by_id(datasource_id)

    if not datasource:
        raise ValueError(f"Datasource with id {datasource_id} hasn't been found")

    repo_id = datasource.get_index_identifier()
    query = {"match_all": {}}
    source_fields, unique_key = _get_source_fields_on_index_type(datasource)

    content_field = _adjust_content_if_enabled(datasource, include_content, source_fields)

    res = ElasticSearchClient.get_client().search(index=repo_id, query=query, _source=source_fields, size=10_000)

    if include_content:
        documents = [
            {content_field: document["_source"][content_field], **document["_source"]["metadata"]}
            for document in res["hits"]["hits"]
        ]
        unique_documents_dict = {
            f"{document[unique_key]}{document.get("chunk_num", "")}": document for document in documents
        }
        # Sort the result by keys and extract the values
        sorted_unique_documents = [value for key, value in sorted(unique_documents_dict.items())]
    else:
        documents = [{**document["_source"]["metadata"]} for document in res["hits"]["hits"]]
        uniqueness_key = "file_path" if datasource.is_code_index() else unique_key
        unique_documents_dict = {document[uniqueness_key]: document for document in documents}
        sorted_unique_documents = sorted(unique_documents_dict.values(), key=lambda doc: doc[uniqueness_key])

    return sorted_unique_documents


def _adjust_content_if_enabled(
    datasource: BaseModelWithSQLSupport, include_content: bool, source_fields: List[str]
) -> str:
    if not include_content:
        return ""

    content_field = "content" if datasource.is_google_doc_index() else "text"
    source_fields.append(content_field)
    return content_field


def _get_source_fields_on_index_type(datasource: BaseModelWithSQLSupport) -> Tuple[list, str]:
    source_fields = [METADATA_CHUNK_NUM]
    unique_key = "source"
    if datasource.is_code_index():
        source_fields.extend([METADATA_SOURCE, METADATA_FILE_PATH])
    else:
        source_fields.append(METADATA_TITLE)
        if datasource.is_google_doc_index():
            unique_key = "title"
        else:
            source_fields.append(METADATA_SOURCE)
    return source_fields, unique_key


def evaluate_conditional_route(
    state_schema: AgentMessages, workflow_state: WorkflowState, enable_summarization_node: bool
) -> str:
    messages = get_messages_from_state_schema(state_schema=state_schema)
    next_candidate = state_schema.get(NEXT_KEY, workflow_state.next.state_id)[-1]
    logger.info(f"Evaluate conditional route. Started. State: {workflow_state.id}, NextCandidate: {next_candidate}")

    if isinstance(messages[-1], AIMessage):
        task_result_metadata = messages[-1].response_metadata
        task_result = bool(task_result_metadata and task_result_metadata.get('success', False))
    else:
        # For custom workflows AIMessage might absent since state is transferred in different keys. So, assuming
        # task is completed and need to transfer to the next state from config. Need to enhance this logic
        # when new use-cases appear
        task_result = True
    next_state = _evaluate_workflow_next_node(
        next_candidate=next_candidate, task_result=task_result, enable_summarization_node=enable_summarization_node
    )
    logger.info(
        "Evaluate conditional route. Completed. "
        f"State: {workflow_state.id}, "
        f"NextCandidate: {next_candidate}. "
        f"Result: {next_state}"
    )
    return next_state


def evaluate_next_candidate(
    execution_result: str, workflow_state: WorkflowState, enable_summarization_node: bool
) -> str:
    if workflow_state.next.condition:
        return _handle_condition_transition_candidate(execution_result, workflow_state, enable_summarization_node)
    elif workflow_state.next.switch:
        return _handle_switch_transition_candidate(execution_result, workflow_state, enable_summarization_node)
    else:
        return _evaluate_workflow_next_node(
            enable_summarization_node=enable_summarization_node, next_candidate=workflow_state.next.state_id
        )


def _evaluate_expression(condition: str, local_vars: dict) -> bool:
    """Evaluates a single condition with the given execution result."""
    try:
        logger.debug(f"Evaluate condition. Condition: {condition}. LocalVars: {local_vars}")
        return bool(eval(condition, {}, local_vars))
    except Exception as e:
        logger.error(f"Error evaluating condition: {condition}. Error: {str(e)}")
        return False


def _evaluate_condition(execution_result: str, condition: str) -> bool:
    """Evaluates a single condition with the given execution result."""
    try:
        result = parse_from_string_representation(execution_result)
        local_vars = _prepare_local_vars({**result, "keys": result.keys()})
        return _evaluate_expression(condition, local_vars)
    except Exception as e:
        logger.error(f"Error evaluating condition: {condition}. Error: {str(e)}")
        return False


def _evaluate_workflow_next_node(
    enable_summarization_node: bool, next_candidate: str = END, task_result: bool = True
) -> str:
    next_node = END if next_candidate in (END, END_NODE) or not task_result else next_candidate
    if enable_summarization_node and next_node == END:
        next_node = RESULT_FINALIZER_NODE
    return next_node


def _handle_condition_transition_candidate(
    execution_result: str, workflow_state: WorkflowState, enable_summarization_node: bool
) -> str:
    try:
        condition = workflow_state.next.condition.expression
        match_result = _evaluate_condition(execution_result, condition)
        next_state = workflow_state.next.condition.then if match_result else workflow_state.next.condition.otherwise
        return _evaluate_workflow_next_node(
            enable_summarization_node=enable_summarization_node, next_candidate=next_state
        )
    except Exception as e:
        logger.error("Condition evaluation error: %s", str(e), exc_info=True)
        return _evaluate_workflow_next_node(
            enable_summarization_node=enable_summarization_node, next_candidate=workflow_state.next.condition.otherwise
        )


def _handle_switch_transition_candidate(
    execution_result: str, workflow_state: WorkflowState, enable_summarization_node: bool
) -> str:
    try:
        switch_cases = workflow_state.next.switch.cases
        result = parse_from_string_representation(execution_result)
        local_vars = _prepare_local_vars({**result, "keys": result.keys()})
        for case in switch_cases:
            condition = case.condition
            next_state = case.state_id
            match_result = _evaluate_expression(condition, local_vars)
            logger.debug("Evaluate switch condition. Condition: %s. match_result: %s", condition, match_result)
            if match_result:
                return _evaluate_workflow_next_node(
                    enable_summarization_node=enable_summarization_node, next_candidate=next_state
                )

        return _evaluate_workflow_next_node(
            enable_summarization_node=enable_summarization_node, next_candidate=workflow_state.next.switch.default
        )
    except Exception as e:
        logger.error("Switch condition evaluation error: %s", str(e), exc_info=True)
        return _evaluate_workflow_next_node(
            enable_summarization_node=enable_summarization_node, next_candidate=workflow_state.next.switch.default
        )


class DotDict:
    """Wrapper class that allows dot notation access to dictionary values.

    This enables conditions like 'pull_request.id' to work by providing
    attribute-style access to dict keys.

    Example:
        >>> d = DotDict({'id': 123, 'name': 'test'})
        >>> d.id
        123
        >>> d.name
        'test'
    """

    def __init__(self, data):
        self._data = data

    def __getattr__(self, key):
        """Allow attribute access for dict keys."""
        if key.startswith('_'):
            # Avoid infinite recursion for internal attributes
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'")
        value = self._data.get(key)
        if isinstance(value, dict):
            # Recursively wrap nested dicts
            return DotDict(value)
        return value

    def __getitem__(self, key):
        """Support bracket notation as well."""
        return self._data[key]

    def get(self, key, default=None):
        """Support dict.get() method."""
        return self._data.get(key, default)


def initialize_assistant(
    user_input: str,
    user: User,
    workflow_assistant: WorkflowAssistant,
    workflow_state: WorkflowState = None,
    thought_queue: ThoughtQueue = None,
    file_name: Optional[str] = None,
    resume_execution: bool = False,
    execution_id: str = None,
    project_name: str = None,
    mcp_server_args_preprocessor: Optional[callable] = None,
    request_headers: dict[str, str] | None = None,
    trace_context=None,  # For workflow trace unification
    disable_cache: Optional[bool] = False,
) -> AIToolsAgent:
    return AssistantService.build_agent_for_workflow(
        workflow_assistant=workflow_assistant,
        workflow_state=workflow_state,
        user_input=user_input,
        user=user,
        thread_generator=thought_queue,
        request_uuid=execution_id,
        resume_execution=resume_execution,
        execution_id=execution_id,
        tool_callbacks=[AgentStreamingCallback(thought_queue)],
        project_name=project_name,
        file_names=[file_name] if file_name else None,
        mcp_server_args_preprocessor=mcp_server_args_preprocessor,
        request_headers=request_headers,
        trace_context=trace_context,  # Pass through to service
        disable_cache=disable_cache,
    )


# JSONB storage limits
MAX_JSONB_SIZE_BYTES = 1_000_000  # 1MB limit for workflow state storage
MAX_STRING_LENGTH = 50_000  # Truncate individual strings
MAX_RECURSION_DEPTH = 20  # Prevent infinite recursion


def serialize_state(value: Any, _depth: int = 0) -> Any:
    """Serialize workflow state to JSON-safe format for JSONB storage with size limits.

    Handles conversion of complex types to JSON-serializable format:
    - LangChain messages → strings
    - Pydantic models → dictionaries
    - Tuples → lists
    - Nested structures → recursively serialized
    - Non-serializable objects → string representation

    Enforces size limits:
    - Maximum 1MB total JSONB size
    - Maximum 50K per string field
    - Maximum 20 levels of nesting

    Args:
        value: Value to serialize (can be dict, list, object, primitive, etc.)
        _depth: Internal recursion depth tracker (do not set manually)

    Returns:
        JSON-safe representation of the value with size limits enforced
    """
    from langchain_core.messages import BaseMessage
    from pydantic import BaseModel

    # Prevent excessive recursion
    if _depth > MAX_RECURSION_DEPTH:
        logger.warning(f"Workflow state serialization exceeded max depth ({MAX_RECURSION_DEPTH})")
        return "[MAX_DEPTH_EXCEEDED]"

    # Primitives with string truncation
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            logger.debug(f"Truncating string from {len(value)} to {MAX_STRING_LENGTH} chars")
            return value[:MAX_STRING_LENGTH] + "...[TRUNCATED]"
        return value

    # LangChain messages → string representation (with truncation)
    if isinstance(value, BaseMessage):
        msg_str = str(value)
        if len(msg_str) > MAX_STRING_LENGTH:
            return msg_str[:MAX_STRING_LENGTH] + "...[TRUNCATED]"
        return msg_str

    # Pydantic models → dict (with fallback to string)
    if isinstance(value, BaseModel):
        try:
            return value.model_dump()
        except Exception:
            return str(value)[:MAX_STRING_LENGTH]

    # Dictionaries → recursively serialize values
    if isinstance(value, dict):
        return {k: serialize_state(v, _depth + 1) for k, v in value.items()}

    # Lists and tuples → recursively serialize items
    if isinstance(value, (list, tuple)):
        return [serialize_state(item, _depth + 1) for item in value]

    # Final fallback: try JSON serialization, otherwise convert to string
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        str_value = str(value)
        if len(str_value) > MAX_STRING_LENGTH:
            return str_value[:MAX_STRING_LENGTH] + "...[TRUNCATED]"
        return str_value


def check_state_size(serialized_state: Any, execution_id: str) -> Any:
    """Check if serialized state exceeds size limit and truncate if needed.

    Args:
        serialized_state: The serialized state dict
        execution_id: Execution ID for logging

    Returns:
        Original state if within limits, truncated metadata if too large
    """
    try:
        serialized_json = json.dumps(serialized_state)
        size_bytes = len(serialized_json.encode('utf-8'))

        if size_bytes > MAX_JSONB_SIZE_BYTES:
            logger.warning(
                f"Workflow state for execution {execution_id} exceeds size limit: "
                f"{size_bytes} bytes (limit: {MAX_JSONB_SIZE_BYTES}). State will be truncated."
            )
            # Return minimal metadata instead of full state
            return {
                "_truncated": True,
                "_original_size_bytes": size_bytes,
                "_limit_bytes": MAX_JSONB_SIZE_BYTES,
                "_message": "Workflow state exceeded storage limit and was truncated",
                "_execution_id": execution_id,
            }

        return serialized_state
    except Exception as e:
        logger.error(f"Failed to check state size for execution {execution_id}: {e}")
        return serialized_state
