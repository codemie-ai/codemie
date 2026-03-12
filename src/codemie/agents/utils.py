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
import hashlib
import json
import re
import sys
import threading
import jsonschema
from inspect import signature
from contextlib import contextmanager
from typing import Union, Dict, Type, List, Any, Optional

from pydantic import BaseModel
from langchain_core.tools import ToolException

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import logger, config
from codemie.configs.logger import logging_user_id, logging_uuid, logging_conversation_id, current_user_email
from codemie.core.constants import METADATA_CHUNK_NUM, METADATA_FILE_NAME, METADATA_FILE_PATH, METADATA_SOURCE
from codemie.enterprise.langfuse import (
    get_langfuse_callback_handler,
    is_langfuse_enabled,
    build_agent_metadata_with_workflow_context,
)
from codemie.core.dependecies import get_indexed_repo
from codemie.core.errors import ErrorCode
from codemie.core.litellm_error_classifier import classify_litellm_exception, is_litellm_exception
from codemie.core.models import CodeFields, AssistantChatRequest
from codemie.service.monitoring.base_monitoring_service import send_log_metric, limit_string
from codemie.service.monitoring.metrics_constants import LLM_ERROR_TOTAL_METRIC, MetricsAttributes
import traceback

OPEN_AI_TOOL_NAME_LIMIT = 64

thread_local = threading.local()


class ExecutionErrorEnum(Enum):
    GUARDRAILS = "guardrails"
    STACKTRACE = "stacktrace"


class ThreadSafeStdout:
    """
    In some places of code we need to supress stdout because it produces garbage in logs.
    This implementation is thread safe and disable stdout in specific thread if context managet is used
    it doesn't break the logging module because we use stderr as default channel for our logging
    """

    def __init__(self, original):
        self.original = original

    def write(self, text):
        # Check if THIS THREAD wants suppression
        if getattr(thread_local, 'suppress', False):
            return  # Suppress only for this thread
        return self.original.write(text)

    def flush(self):
        return self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


# Install once at app startup
sys.stdout = ThreadSafeStdout(sys.stdout)


def parse_tool_input(args_schema: Type[BaseModel], tool_input: Union[str, Dict]):
    try:
        input_args = args_schema
        logger.info(f"Starting parser with input: {tool_input}")
        if isinstance(tool_input, str):
            params = parse_to_dict(tool_input)
            result = input_args.model_validate(dict(params))
            return {k: getattr(result, k) for k, v in result.dict().items() if k in tool_input}
        else:
            if input_args is not None:
                result = input_args.model_validate(tool_input)
                return {k: getattr(result, k) for k, v in result.dict().items() if k in tool_input}
        return tool_input
    except Exception as e:
        raise ToolException(f"""
                Cannot parse input parameters.
                Got wrong input: {tool_input}. See description of input parameters.
                Error: {e}
                """)


def parse_to_dict(input_string):
    try:
        # Try parsing it directly first, in case the string is already in correct JSON format
        parsed_dict = json.loads(input_string)
    except json.JSONDecodeError:
        # If that fails, replace single quotes with double quotes
        # and escape existing double quotes
        try:
            # This will convert single quotes to double quotes and escape existing double quotes
            adjusted_string = input_string.replace('\'', '"').replace('"', '\\"')
            # If the above line replaces already correct double quotes, we correct them back
            adjusted_string = adjusted_string.replace('\\"{', '"{').replace('}\\"', '}"')
            # Now try to parse the adjusted string
            parsed_dict = json.loads(adjusted_string)
        except json.JSONDecodeError:
            # Handle any JSON errors
            return None
    return parsed_dict


def get_repo_tree(code_fields: CodeFields):
    index_name = get_indexed_repo(code_fields).get_identifier()
    es = ElasticSearchClient.get_client()
    source = ["metadata.file_path"]
    query = {"match_all": {}}
    res = es.search(index=index_name, query=query, source=source, size=10000)
    response = [hit['_source']['metadata']['file_path'] for hit in res['hits']['hits']]
    # Remove duplicates
    response = list(set(response))
    response.sort()
    return response


def get_repo_tree_by_search_phrase_path(code_fields: CodeFields, file_path: str):
    index_name = get_indexed_repo(code_fields).get_identifier()
    es = ElasticSearchClient.get_client()
    source = ["metadata.file_path"]
    query = {"bool": {"must": [{"match_phrase": {METADATA_FILE_PATH: file_path}}]}}
    res = es.search(index=index_name, query=query, source=source, size=10000)
    response = [hit['_source']['metadata']['file_path'] for hit in res['hits']['hits']]
    # Remove duplicates
    response = list(set(response))
    response.sort()
    return response


def get_repo_files_by_search_phrase_path(code_fields: CodeFields, search_phrase: str):
    index_name = get_indexed_repo(code_fields).get_identifier()
    es = ElasticSearchClient.get_client()
    source = ["text", METADATA_FILE_PATH, METADATA_SOURCE, METADATA_FILE_NAME, METADATA_CHUNK_NUM]
    query = {"bool": {"must": [{"match_phrase": {METADATA_FILE_PATH: search_phrase}}]}}
    res = es.search(index=index_name, body={"_source": source, "size": 10000, "query": query})

    response = [
        {
            "text": hit['_source'].get('text', ''),
            "source": hit['_source']['metadata']['source'],
            "file_path": hit['_source']['metadata']['file_path'],
            "file_name": hit['_source']['metadata']['file_name'],
            "unique_key": f"{hit['_source']['metadata']['source']}{hit['_source']['metadata'].get('chunk_num', "")}",
        }
        for hit in res['hits']['hits']
    ]
    logger.info(f"Received {len(response)} files from {index_name}.")

    # Remove duplicates and sort by file_path
    unique_response = {entry['unique_key']: entry for entry in response}

    sorted_response = sorted(unique_response.values(), key=lambda x: x['unique_key'])
    logger.info(f"Reduced duplications: {len(response)}.")
    return sorted_response


def adapt_tool_name(template: str, alias: str) -> str:
    tool_name = template.format(to_snake_case(alias))
    if len(tool_name) > OPEN_AI_TOOL_NAME_LIMIT:
        tool_name = template.format(generate_tool_hash(alias))

    return tool_name


def generate_tool_hash(input_string: str) -> str:
    """Generate an MD5 hash from the input string."""
    # Generate MD5 hash from input string
    hash_object = hashlib.sha256(input_string.encode())
    # Convert the hash to an integer
    hash_integer = int(hash_object.hexdigest(), 16)
    # Use modulo to limit the size of the integer
    unique_number = hash_integer % 100000000
    # Convert the number to a string
    return str(unique_number)


def to_snake_case(input_string: str) -> str:
    """Convert a string to snake_case"""
    # Remove all non-alphanumeric characters
    snake_case_str = re.sub('[^0-9a-zA-Z]+', '_', input_string)

    # Replace spaces with underscores
    snake_case_str = snake_case_str.replace(' ', '_')

    # Remove leading and trailing underscores
    snake_case_str = snake_case_str.strip('_')

    return snake_case_str


def render_text_description_and_args(tools: List[Any]) -> str:
    """Generate a text description of tools including their name, description, and arguments."""
    tool_descriptions = []
    for tool in tools:
        args_schema = str(tool.args)
        sig = ""
        if hasattr(tool, "execute") and tool.execute:
            sig = signature(tool.execute)

        tool_description = (
            f"Tool Name: {tool.name}{sig}\nTool Description: {tool.description}\nTool Arguments: {args_schema}\n"
        )
        tool_descriptions.append(tool_description)
    return "\n".join(tool_descriptions)


def validate_json_schema(schema):
    # Check for dict type
    if not isinstance(schema, dict):
        logger.debug("Schema must be a dictionary.")
        return False
    # Require 'type' and 'properties'
    if 'type' not in schema:
        logger.debug("Schema must have a 'type' key.")
        return False
    if 'properties' not in schema:
        logger.debug("Schema must have a 'properties' key.")
        return False
    # Check that 'type' is a string
    if not isinstance(schema['type'], str):
        logger.debug("'type' must be a string.")
        return False
    # Check that 'properties' is a dict
    if not isinstance(schema['properties'], dict):
        logger.debug("'properties' must be a dictionary.")
        return False
    # Validate against JSON Schema meta-schema
    try:
        jsonschema.Draft7Validator.check_schema(schema)
        return True
    except jsonschema.exceptions.SchemaError as e:
        logger.debug(f"SchemaError: {e}")
        return False


def get_run_config(
    request: Optional[AssistantChatRequest],
    llm_model: str,
    agent_name: str,
    conversation_id: Optional[str] = None,
    username: Optional[str] = None,
    additional_tags: Optional[List[str]] = None,
    assistant_version: Optional[int] = None,
    trace_context=None,  # For workflow trace unification
) -> Dict[str, Any]:
    """
    Creates a run configuration based on the request, model, and agent name.
    Extracts langfuse_tags from request metadata if available and sets up Langfuse configuration.

    The function supports disabling traces on a per-request basis by setting
    langfuse_traces_enabled="false" in request metadata, which overrides the global
    config.LANGFUSE_TRACES setting.

    Args:
        request: The AssistantChatRequest, which may contain metadata with langfuse_tags
                and langfuse_traces_enabled.
        llm_model: The name of the LLM model being used.
        agent_name: The name of the agent being run.
        conversation_id: Optional conversation ID for Langfuse session tracking.
        username: Optional username for Langfuse user tracking.
        additional_tags: Optional list of additional tags to add to langfuse_tags.
        assistant_version: Optional assistant version number to include in tags.
        trace_context: Optional TraceContext for workflow trace unification. When provided,
                      agent traces are nested under the workflow trace.

    Returns:
        A dictionary with run configuration parameters.
    """
    # If tracing is disabled globally or overridden by request, return empty config
    if not _should_enable_langfuse_tracing(request):
        return {}

    # If no conversation_id provided (needed for session tracking), return empty config
    if not conversation_id:
        return {}

    # Collect all tags from different sources
    langfuse_tags = _collect_langfuse_tags(llm_model, agent_name, additional_tags, request, assistant_version)

    # Get LangFuse callback handler from centralized function
    langfuse_callback_handler = get_langfuse_callback_handler()
    if not langfuse_callback_handler:
        return {}

    # Build metadata using centralized builder (handles workflow context)
    metadata = build_agent_metadata_with_workflow_context(
        agent_name=agent_name,
        conversation_id=conversation_id,
        llm_model=llm_model,
        username=username,
        tags=langfuse_tags,
        trace_context=trace_context,
    )

    # Log trace type
    if trace_context and hasattr(trace_context, 'workflow_id'):
        logger.info(f"NESTED TRACE: agent='{agent_name}', execution_id={trace_context.execution_id}")
    else:
        logger.debug(f"STANDALONE TRACE: agent='{agent_name}'")

    # Return the complete run config
    return {
        "callbacks": [langfuse_callback_handler],
        "run_name": agent_name,
        "metadata": metadata,
    }


def _should_enable_langfuse_tracing(request: Optional[AssistantChatRequest]) -> bool:
    """
    Determine if Langfuse tracing should be enabled based on enterprise availability, config, and request metadata.
    Priority: HAS_LANGFUSE > config.LANGFUSE_TRACES > request.metadata.langfuse_traces_enabled
    """
    # Check if LangFuse is available and enabled (handles HAS_LANGFUSE + config.LANGFUSE_TRACES)
    if not is_langfuse_enabled():
        return False

    # Extract and evaluate request-specific override
    trace_setting = request.metadata.get("langfuse_traces_enabled") if (request and request.metadata) else None

    if trace_setting is not None:
        logger.info(
            f"Request metadata contains 'langfuse_traces_enabled': {trace_setting}. "
            f"Conversation ID: {request.conversation_id if request else 'N/A'}"
        )

        if isinstance(trace_setting, str):
            trace_setting = trace_setting.strip().lower() == "true"
        elif not isinstance(trace_setting, bool):
            logger.warning("Unsupported type for 'langfuse_traces_enabled'; defaulting to False.")
            trace_setting = False

        return trace_setting

    # Default to global tracing setting
    return config.LANGFUSE_TRACES


def _collect_langfuse_tags(
    llm_model: str,
    agent_name: str,
    additional_tags: Optional[List[str]],
    request: Optional[AssistantChatRequest],
    assistant_version: Optional[int] = None,
) -> List[str]:
    """
    Collect Langfuse tags from all available sources: default, additional_tags, request metadata, and assistant version.
    Default tags are prefixed with their type for better categorization.
    Additional and user-provided tags also should be having prefix with tag's name
    """
    # Start with default tags
    tags = [f"llm_model:{llm_model}", f"agent_name:{agent_name}"]

    # Add assistant version tag if provided
    if assistant_version is not None:
        version_tag = f"assistant_version:{assistant_version}"
        tags.append(version_tag)

    # Add user-provided additional tags
    if additional_tags:
        tags.extend(additional_tags)

    # Add tags from request metadata if available
    if request and request.metadata and "langfuse_tags" in request.metadata:
        metadata_tags = request.metadata.get("langfuse_tags")
        if isinstance(metadata_tags, list):
            tags.extend(metadata_tags)

    return tags


def handle_agent_exception(e: Exception) -> tuple[str, str | None]:
    """Classify the exception and return a user-friendly message with an error code.

    Returns:
        Tuple of (user_message, execution_error_code).
        ``execution_error_code`` is an ``ErrorCode`` value (str) or ``None``
        when the error is not LLM-specific.
    """
    error_message = str(e)

    # --- LiteLLM / LLM errors ---
    if is_litellm_exception(e):
        error_code, friendly_message = classify_litellm_exception(e)

        logger.error(f"LiteLLM error [{error_code.value}]: {error_message}")
        _emit_llm_error_log(error_code.value, error_message, exc=e)

        user_msg = config.CODEMIE_SUPPORT_MSG if config.HIDE_AGENT_STREAMING_EXCEPTIONS else friendly_message
        return user_msg, error_code.value

    # --- Legacy budget_exceeded handling (non-LiteLLM path) ---
    if "budget_exceeded" in error_message.lower():
        budget_message = _extract_budget_message(error_message)
        if config.HIDE_AGENT_STREAMING_EXCEPTIONS:
            user_msg = config.CODEMIE_SUPPORT_MSG
        else:
            user_msg = budget_message or "Budget limit has been reached."

        log_msg = budget_message or error_message
        logger.error(f"Budget exceeded: {log_msg}")

        error_code_value = ErrorCode.LLM_BUDGET_EXCEEDED.value
        _emit_llm_error_log(error_code_value, log_msg, exc=e)

        return user_msg, error_code_value

    # --- General errors ---
    stacktrace = traceback.format_exc()
    exception_type = type(e).__name__
    logger.error(f"AI Agent failed with error: {stacktrace}", exc_info=True)
    return f"AI Agent run failed with error: {exception_type}: {error_message}", None


def _extract_budget_message(error_message: str) -> str | None:
    """Try to extract a human-readable budget message from an error string."""
    try:
        import ast

        dict_match = re.search(r"\{.*\}", error_message)
        if dict_match:
            dict_str = dict_match.group()
            error_data = ast.literal_eval(dict_str)
            if "error" in error_data and "message" in error_data["error"]:
                return error_data["error"]["message"]
            if "message" in error_data:
                return error_data["message"]
    except (ValueError, SyntaxError, KeyError):
        pass
    return None


def _emit_llm_error_log(
    error_code: str,
    error_message: str,
    exc: Exception | None = None,
) -> None:
    """Emit a structured log entry for ELK alerting via the existing ``send_log_metric``.

    Includes request context from ``contextvars`` (set by ``set_logging_info``)
    and exception attributes (``model``, ``llm_provider``, ``status_code``)
    for traceability and correlation with ``conversation_assistant_usage``.
    """
    try:
        attributes: dict[str, object] = {
            MetricsAttributes.LLM_ERROR_CODE: error_code,
            MetricsAttributes.ERROR: limit_string(error_message),
            MetricsAttributes.USER_ID: logging_user_id.get("-"),
            MetricsAttributes.USER_EMAIL: current_user_email.get("-"),
            MetricsAttributes.CONVERSATION_ID: logging_conversation_id.get("-"),
            "request_uuid": logging_uuid.get("-"),
        }
        if exc is not None:
            llm_model = getattr(exc, "model", None)
            llm_provider = getattr(exc, "llm_provider", None)
            status_code = getattr(exc, "status_code", None)
            if llm_model:
                attributes[MetricsAttributes.LLM_MODEL] = llm_model
            if llm_provider:
                attributes["llm_provider"] = llm_provider
            if status_code is not None:
                attributes["status_code"] = status_code
        send_log_metric(LLM_ERROR_TOTAL_METRIC, attributes)
    except Exception as log_exc:
        logger.warning(f"Failed to emit LLM error log metric: {log_exc}")


@contextmanager
def suppress_stdout():
    """Thread-safe suppression"""
    thread_local.suppress = True
    try:
        yield
    finally:
        thread_local.suppress = False
