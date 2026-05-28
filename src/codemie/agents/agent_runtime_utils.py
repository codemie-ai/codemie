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
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from codemie.agents.agent_log_utils import (
    serialize_messages_for_log,
    serialize_tool_calls_for_log,
    truncate_log_content,
)
from codemie.agents.utils import validate_json_schema
from codemie.core.constants import ChatRole


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
        return [item for item in history if item.content]

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
    return filtered_history


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
