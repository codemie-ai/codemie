# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from codemie.configs import config


def truncate_log_content(content: Any) -> str:
    if not isinstance(content, str):
        content = str(content)
    if len(content) <= config.AI_AGENT_HISTORY_REPLAY_LOG_CONTENT_LIMIT:
        return content
    truncated = content[: config.AI_AGENT_HISTORY_REPLAY_LOG_CONTENT_LIMIT].rstrip()
    return f"{truncated}\n...[truncated]"


def serialize_tool_calls_for_log(tool_calls: list[dict[str, Any]]) -> list[dict[str, str | None]]:
    serialized_calls = []
    for tool_call in tool_calls:
        serialized_calls.append(
            {
                "id": tool_call.get("id"),
                "name": tool_call.get("name"),
                "args": truncate_log_content(json.dumps(tool_call.get("args", {}), ensure_ascii=True, default=str)),
            }
        )
    return serialized_calls


def serialize_messages_for_log(messages: list[Any]) -> str:
    payload = []
    for message in messages:
        content = getattr(message, "content", None)
        if content is None and hasattr(message, "message"):
            content = message.message
        item = {
            "type": message.__class__.__name__,
            "content": truncate_log_content(content if isinstance(content, str) else str(content)),
        }
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            item["tool_calls"] = serialize_tool_calls_for_log(message.tool_calls)
        if isinstance(message, ToolMessage):
            item["tool_call_id"] = message.tool_call_id
            item["name"] = message.name or message.additional_kwargs.get("name")
        payload.append(item)
    return json.dumps(payload, ensure_ascii=True, default=str)
