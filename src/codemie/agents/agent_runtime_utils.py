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

import contextlib
import json
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from codemie.agents.agent_log_utils import (
    serialize_messages_for_log,
    serialize_tool_calls_for_log,
    truncate_log_content,
)
from codemie.agents.utils import validate_json_schema
from codemie.configs.logger import logger
from codemie.core.constants import ChatRole
from codemie.service.spend.origin_context import spend_origin


def preprocess_output_schema(
    output_schema: dict[str, Any] | BaseModel,
    *,
    include_description: bool = False,
) -> dict[str, Any] | BaseModel:
    if isinstance(output_schema, dict):
        check = validate_json_schema(output_schema)
        if not check:
            raise ValueError(f"Wrong JSON Schema was put in agent: {output_schema}")
        output_schema["title"] = output_schema.get("title", "StructuredOutput")
        if include_description:
            output_schema["description"] = output_schema.get("description", "Structured output")
    return output_schema


def is_unique_callback(callbacks: Sequence[object], candidate: object) -> bool:
    return not any(isinstance(callback, type(candidate)) for callback in callbacks)


def transform_history(history: list[Any], *, supports_rich_history: bool) -> list[BaseMessage]:
    transformed_history: list[BaseMessage] = []

    for item in history:
        if supports_rich_history and isinstance(item, BaseMessage):
            transformed_history.append(item)
            continue

        if not hasattr(item, "role"):
            continue

        if item.role == ChatRole.USER:
            transformed_history.append(HumanMessage(content=item.message))
        elif item.role == ChatRole.ASSISTANT:
            transformed_history.append(AIMessage(content=item.message))

    return transformed_history


def filter_history(history: list[Any], *, supports_rich_history: bool) -> list[Any]:
    if not supports_rich_history:
        content_filtered = [item for item in history if item.content]
        return sanitize_rich_history_for_llm(content_filtered)

    filtered_history = []
    for item in history:
        if getattr(item, "content", None):
            filtered_history.append(item)
            continue
        if isinstance(item, ToolMessage):
            filtered_history.append(item)
            continue
        if isinstance(item, AIMessage) and getattr(item, "tool_calls", None):
            filtered_history.append(item)
    return sanitize_rich_history_for_llm(filtered_history)


def sanitize_rich_history_for_llm(history: list[BaseMessage]) -> list[BaseMessage]:
    """
    Ensure the history passed to LLM providers is structurally valid for tool replay.

    Rules (current behavior):
    - Keep an assistant tool-call message only if every referenced tool call has a matching ToolMessage.
    - Drop any ToolMessage that does not match a pending assistant tool call.
    - If a tool-call block is interrupted (or left incomplete at end-of-history), drop the whole block.
    """

    sanitizer = _RichHistoryToolReplaySanitizer()
    for item in history:
        sanitizer.process(item)
    sanitizer.finalize()
    return sanitizer.sanitized_history


@dataclass(slots=True)
class _RichHistoryToolReplaySanitizer:
    sanitized_history: list[BaseMessage] = field(default_factory=list)
    pending_ai_message: AIMessage | None = None
    pending_tool_call_ids: set[str] = field(default_factory=set)
    pending_tool_messages: list[ToolMessage] = field(default_factory=list)

    def process(self, item: BaseMessage) -> None:
        if self._handle_ai_tool_call(item):
            return
        if self._handle_tool_message(item):
            return
        self._handle_regular_message(item)

    def finalize(self) -> None:
        self._drop_pending_assistant_block(reason="end_of_history")

    def _handle_ai_tool_call(self, item: BaseMessage) -> bool:
        if not (isinstance(item, AIMessage) and getattr(item, "tool_calls", None)):
            return False

        if self.pending_ai_message is not None:
            self._drop_pending_assistant_block(reason="consecutive_assistant_tool_calls")

        self.pending_ai_message = item
        self.pending_tool_call_ids = {
            str(tool_call_id) for tool_call in item.tool_calls if (tool_call_id := tool_call.get("id"))
        }
        self.pending_tool_messages = []

        if not self.pending_tool_call_ids:
            # No (valid) IDs to match against; keep message as-is (legacy behavior).
            self.sanitized_history.append(item)
            self.pending_ai_message = None

        return True

    def _handle_tool_message(self, item: BaseMessage) -> bool:
        if not isinstance(item, ToolMessage):
            return False

        tool_call_id = getattr(item, "tool_call_id", None)
        if self._is_expected_tool_message(tool_call_id):
            self.pending_tool_call_ids.discard(tool_call_id)
            self.pending_tool_messages.append(item)
            self._flush_completed_assistant_block()
            return True

        self._log_dropped_orphan_tool_message(item, tool_call_id)
        return True

    def _handle_regular_message(self, item: BaseMessage) -> None:
        if self.pending_ai_message is not None:
            self._drop_pending_assistant_block(reason=f"interrupted_by_{type(item).__name__}")
        self.sanitized_history.append(item)

    def _is_expected_tool_message(self, tool_call_id: str | None) -> bool:
        return bool(self.pending_ai_message is not None and tool_call_id and tool_call_id in self.pending_tool_call_ids)

    def _log_dropped_orphan_tool_message(self, item: ToolMessage, tool_call_id: str | None) -> None:
        logger.info(
            "Dropping orphan tool replay message before LLM invocation. "
            f"ToolName={item.name or item.additional_kwargs.get('name') or 'tool'}, "
            f"ToolCallId={tool_call_id}"
        )

    def _drop_pending_assistant_block(self, reason: str) -> None:
        if self.pending_ai_message is None:
            return

        dropped_ids = sorted(self.pending_tool_call_ids)
        logger.warning(
            "Dropping malformed assistant tool-call replay block before LLM invocation. "
            f"Reason={reason}, ToolCallIds={dropped_ids}"
        )
        self.pending_ai_message = None
        self.pending_tool_call_ids = set()
        self.pending_tool_messages = []

    def _flush_completed_assistant_block(self) -> None:
        if self.pending_ai_message is None:
            return
        if self.pending_tool_call_ids:
            return

        self.sanitized_history.append(self.pending_ai_message)
        self.sanitized_history.extend(self.pending_tool_messages)
        self.pending_ai_message = None
        self.pending_tool_call_ids = set()
        self.pending_tool_messages = []


def serialize_messages(messages: list[Any]) -> str:
    return serialize_messages_for_log(messages)


def serialize_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, str | None]]:
    return serialize_tool_calls_for_log(tool_calls)


def truncate_log_value(content: Any) -> str:
    return truncate_log_content(content)


def serialize_inputs(inputs: dict[str, Any], *, messages_key: str) -> str:
    payload: dict[str, Any] = {}
    for key, value in inputs.items():
        if key == messages_key and isinstance(value, list):
            payload[key] = json.loads(serialize_messages_for_log(value))
            continue
        payload[key] = truncate_log_content(str(value))
    return json.dumps(payload, ensure_ascii=True, default=str)


def serialize_response(response: Any) -> str:
    return truncate_log_content(str(response))


def with_captured_origin(method):
    """Re-publish the origin the agent captured at construction around an agent entry point.

    The agent and its own model are built where the entry point published an origin, but the run
    is handed to `assistant_executor`, a plain thread pool that copies no context. Every model built
    during the run - a tool's own LLM, a history-compaction summary, a retrieval embedding - is
    therefore constructed on a thread where nothing is published and would send no origin at all.
    Publishing the captured value again on the invoking thread gives all of them the origin the run
    started with, and the token form restores whatever the pooled thread carried before, so a
    finished run leaves nothing behind for the next one. Lives here rather than on an agent class
    because the tool-call-resume mixin needs it too, and the mixin is imported by the agents.
    """

    @wraps(method)
    def invoke_with_origin(self, *args, **kwargs):
        origin = getattr(self, "_origin", None)
        with spend_origin(origin) if origin else contextlib.nullcontext():
            return method(self, *args, **kwargs)

    return invoke_with_origin
