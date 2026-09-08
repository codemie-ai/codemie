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

import hashlib
import json
import re
import sys
import threading
import jsonschema
from enum import Enum
from inspect import signature
from contextlib import contextmanager
from typing import Dict, Type, List, Any, Optional

from pydantic import BaseModel
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import ToolException

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import logger
from codemie.core.constants import METADATA_CHUNK_NUM, METADATA_FILE_NAME, METADATA_FILE_PATH, METADATA_SOURCE
from codemie.core.errors import LiteLLMErrorClassifier
from codemie.enterprise.langfuse import get_langfuse_client_or_none
from codemie.enterprise.observability import get_observability_provider
from codemie.core.dependecies import get_indexed_repo
from codemie.core.models import CodeFields, AssistantChatRequest


OPEN_AI_TOOL_NAME_LIMIT = 64

thread_local = threading.local()


class ExecutionErrorEnum(Enum):
    GUARDRAILS = "guardrails"
    STACKTRACE = "stacktrace"


class LangfuseLiteLLMErrorOutputCallback(BaseCallbackHandler):
    """Backfills Langfuse Input/Output on failed LiteLLM calls.

    A rejected call (e.g. proxy guardrail 400) ends the root chain via
    ``on_chain_error``, so Langfuse derives no trace input/output and the trace
    renders blank. Write the user query and the classified message instead.
    """

    def __init__(self, user_input: Any = None) -> None:
        self.user_input = user_input

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        error_response = LiteLLMErrorClassifier().classify(error)
        if error_response is None:
            return
        payload = error_response.get_error()
        if payload is None:
            return
        text = (payload.message or "").strip()
        if not text:
            return

        try:
            client = get_langfuse_client_or_none()
            if client is None:
                return
            # Generation span: user-facing message (nested LLM observation).
            update_generation = getattr(client, "update_current_generation", None)
            if callable(update_generation):
                update_generation(output=text)

            trace_io: Dict[str, Any] = {"output": text}
            if self.user_input is not None:
                trace_io["input"] = self.user_input

            set_trace_io = getattr(client, "set_current_trace_io", None)
            if callable(set_trace_io):
                set_trace_io(**trace_io)
            else:
                update_trace = getattr(client, "update_current_trace", None)
                if callable(update_trace):
                    update_trace(**trace_io)
        except Exception as e:
            logger.warning(f"Failed to set Langfuse output for LiteLLM error: {e}")


_HEDGING_CANCELLED_OUTPUT = "[Request Hedging]: FAST_PATH_WON"


def mark_trace_cancelled_by_hedging(user_input: Any = None, request_uuid: Optional[str] = None) -> None:
    """Demote the active observability trace from ERROR to a normal 'cancelled' state.

    Called from the agent thread when its stream is being cancelled because the
    hedging fast path won. Must run BEFORE the underlying stream is closed — at
    that point the provider trace context is still active and the chain span has
    not yet been marked ERROR by ``on_chain_error`` (Langfuse) or otherwise.

    ``user_input`` (optional) is propagated to the trace so the cancelled run is
    not blank — typically the originating user query string.

    Provider-agnostic: routes through the active ``ObservabilityProvider`` so
    both Langfuse and Phoenix backends receive the same demotion semantics
    (Langfuse: observation level DEFAULT; Phoenix: OTEL Status OK).
    """
    provider = get_observability_provider()
    if not provider.is_enabled():
        return
    try:
        provider.update_current_observation(
            input=user_input,
            level="DEFAULT",
            status_message="cancelled_by_hedging",
            output=_HEDGING_CANCELLED_OUTPUT,
        )
        from codemie.core.thread import HedgingCancellationReason  # local import avoids circular dependency

        trace_metadata: Dict[str, Any] = {"cancellation_reason": HedgingCancellationReason.FAST_PATH_WON}
        if request_uuid:
            trace_metadata["request_uuid"] = request_uuid
        provider.update_current_trace(
            input=user_input,
            tags=["hedging_role:agent", "hedging_winner:fast_path", "cancelled_by_hedging"],
            metadata=trace_metadata,
            output=_HEDGING_CANCELLED_OUTPUT,
        )
    except Exception as e:
        logger.warning(f"Failed to demote trace on hedging cancellation: {e}")


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


def parse_tool_input(args_schema: Type[BaseModel], tool_input: str | Dict):
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


_INVALID_TOOL_NAME_CHARS_RE = re.compile(r"[^a-z0-9_-]")
_INVALID_DATASOURCE_NAME_CHARS_RE = _INVALID_TOOL_NAME_CHARS_RE


def sanitize_tool_name(name: str) -> str:
    """Sanitize a tool name to [a-z0-9_-]+, enforcing a 64-char limit with hash suffix on overflow.

    Lowercases first, replaces invalid chars with '_'. Required because AWS Bedrock Converse
    rejects tool names containing characters outside [a-zA-Z0-9_-], and cross-provider
    consistency requires lowercase.
    """
    if not name:
        return name
    sanitized = _INVALID_TOOL_NAME_CHARS_RE.sub("_", name.lower())
    if len(sanitized) > OPEN_AI_TOOL_NAME_LIMIT:
        suffix = generate_tool_hash(name)
        keep = OPEN_AI_TOOL_NAME_LIMIT - len(suffix) - 1
        sanitized = f"{sanitized[:keep].rstrip('_')}_{suffix}"
    return sanitized


def sanitize_datasource_name(name: str) -> str:
    """Normalize a datasource repo_name to [a-z0-9_-]+."""
    if not name:
        return name
    sanitized = _INVALID_DATASOURCE_NAME_CHARS_RE.sub("_", name.lower())
    return sanitized.strip("_")


def adapt_tool_name(template: str, alias: str) -> str:
    tool_name = template.format(sanitize_datasource_name(alias))
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
    request_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Creates a run configuration based on the request, model, and agent name.
    Extracts tags from request metadata if available and sets up observability configuration.

    The function supports disabling traces on a per-request basis by setting
    observability_traces_enabled (or langfuse_traces_enabled for Langfuse) in request metadata,
    which overrides the global provider enable setting.

    Args:
        request: The AssistantChatRequest, which may contain metadata with tags
                and observability_traces_enabled.
        llm_model: The name of the LLM model being used.
        agent_name: The name of the agent being run.
        conversation_id: Optional conversation ID for session tracking.
        username: Optional username for user tracking.
        additional_tags: Optional list of additional tags.
        assistant_version: Optional assistant version number to include in tags.
        trace_context: Optional trace context for workflow trace unification. When provided,
                      agent traces are nested under the workflow trace.

    Returns:
        A dictionary with run configuration parameters.
    """
    provider = get_observability_provider()
    request_metadata = request.metadata if (request and request.metadata) else None

    # If tracing is disabled globally or overridden by request, return empty config
    if not provider.should_trace_request(request_metadata):
        return {}

    # If no conversation_id provided (needed for session tracking), return empty config
    if not conversation_id:
        return {}

    # Collect all tags from different sources
    tags = _collect_trace_tags(llm_model, agent_name, additional_tags, request, assistant_version)

    # Get callback handler(s) — None for auto-instrumentation providers (e.g., Phoenix uses OTEL)
    # Provider may return a single handler or a list of handlers.
    handler = provider.get_callback_handler()
    callbacks: list[Any] = []
    if isinstance(handler, list):
        callbacks.extend(handler)
    elif handler is not None:
        callbacks.append(handler)

    if callbacks:
        # LiteLLM error tracking; per-request so a rejected call carries its query onto the trace.
        callbacks.append(LangfuseLiteLLMErrorOutputCallback(user_input=request.text if request else None))
    elif not provider.is_enabled():
        # No callbacks and provider disabled — nothing to trace
        return {}

    # Build metadata (provider-specific keys for session/user/tag attribution)
    metadata = provider.build_agent_metadata(
        agent_name=agent_name,
        conversation_id=conversation_id,
        llm_model=llm_model,
        username=username,
        tags=tags,
        trace_context=trace_context,
    )
    # Mirror the fast-path trace's request_uuid on the agent trace so both paths
    # are correlatable in the observability UI under the same key.
    if request_uuid:
        metadata["request_uuid"] = request_uuid

    # Log trace type
    if trace_context and hasattr(trace_context, "workflow_id"):
        logger.info(f"NESTED TRACE: agent='{agent_name}', execution_id={trace_context.execution_id}")
    else:
        logger.debug(f"STANDALONE TRACE: agent='{agent_name}'")

    # Obtain a provider-agnostic trace context manager (sets trace name, user, session, tags)
    trace_ctx = provider.get_trace_context(
        trace_name=agent_name,
        user_id=username,
        session_id=conversation_id,
        tags=tags,
    )

    return {
        "callbacks": callbacks,
        "run_name": agent_name,
        "metadata": metadata,
        "_trace_ctx": trace_ctx,
    }


def _collect_trace_tags(
    llm_model: str,
    agent_name: str,
    additional_tags: Optional[List[str]],
    request: Optional[AssistantChatRequest],
    assistant_version: Optional[int] = None,
) -> List[str]:
    """
    Collect trace tags from all available sources: default, additional_tags, request metadata, and assistant version.
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


@contextmanager
def suppress_stdout():
    """Thread-safe suppression"""
    thread_local.suppress = True
    try:
        yield
    finally:
        thread_local.suppress = False
