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
from ast import literal_eval
from typing import Any

from codemie.configs import config
from codemie.service.constants import AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY
from codemie.service.conversation.history_projection_service import (
    SKILL_TOOL_NAME,
    TOOL_REPLAY_TYPE,
    TOOL_STATUS_RUNNING,
)
from codemie.service.dynamic_config_service import DynamicConfigService


def _safe_parse_tool_args(input_text: str) -> dict[str, Any] | None:
    if not input_text:
        return None

    for parser in (json.loads, literal_eval):
        try:
            parsed = parser(input_text)
        except (ValueError, SyntaxError, TypeError, RecursionError):
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _is_conversation_replay_v2_enabled() -> bool:
    return DynamicConfigService.get_bool_value_safe(
        AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY,
        default=config.AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED,
    )


def _build_tool_metadata(tool_name: str, input_text: str) -> dict[str, Any]:
    if not _is_conversation_replay_v2_enabled():
        return {}

    normalized_name = tool_name.replace(' ', '_').lower()
    metadata: dict[str, Any] = {
        "replay_type": TOOL_REPLAY_TYPE,
        "tool_name": normalized_name,
        "tool_args_text": input_text,
        "status": TOOL_STATUS_RUNNING,
    }
    parsed_args = _safe_parse_tool_args(input_text)
    if parsed_args:
        metadata["tool_args"] = parsed_args
    if normalized_name == SKILL_TOOL_NAME:
        metadata["preserve_full_output"] = True
    return metadata


def _summarize_tool_output(tool_name: str, output: str) -> str:
    limit = (
        config.AI_AGENT_HISTORY_REPLAY_FULL_TOOL_RESULT_LIMIT
        if tool_name == SKILL_TOOL_NAME
        else config.AI_AGENT_HISTORY_REPLAY_SUMMARY_TOOL_RESULT_LIMIT
    )
    text = output.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}\n...[truncated]"


def _truncate_for_log(value: str, limit: int = config.AI_AGENT_HISTORY_REPLAY_LOG_CONTENT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}...[truncated]"
