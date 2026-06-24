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

import pytest
from pydantic import ValidationError

from codemie.service.aws_bedrock.agentcore.agentcore_config import (
    AgentcoreHistoryConfig,
    AgentcoreOutputConfig,
    AgentcoreRequestConfig,
    AgentcoreResponseConfig,
)


# --- AgentcoreResponseConfig ---


def test_non_streaming_requires_body():
    with pytest.raises(ValidationError):
        AgentcoreResponseConfig.model_validate({"streaming": False})  # body missing


def test_streaming_requires_chunk():
    with pytest.raises(ValidationError):
        AgentcoreResponseConfig.model_validate({"streaming": True})  # chunk missing


def test_valid_non_streaming_config():
    cfg = AgentcoreResponseConfig.model_validate({"streaming": False, "body": {"text_path": "output"}})
    assert cfg.streaming is False
    assert cfg.body.text_path == "output"
    assert cfg.chunk is None


def test_valid_streaming_config():
    cfg = AgentcoreResponseConfig.model_validate({"streaming": True, "chunk": {"text_path": "delta"}})
    assert cfg.streaming is True
    assert cfg.chunk.text_path == "delta"


def test_reasoning_requires_text_path():
    with pytest.raises(ValidationError):
        AgentcoreOutputConfig.model_validate(
            {
                "text_path": "output",
                "reasoning": {},  # text_path missing
            }
        )


def test_reasoning_active_path_is_optional():
    cfg = AgentcoreOutputConfig.model_validate(
        {
            "text_path": "output",
            "reasoning": {"text_path": "thinking"},  # active_path omitted — valid
        }
    )
    assert cfg.reasoning.active_path is None


# --- AgentcoreResponseConfig.parse_json ---


def test_response_parse_json_new_format():
    raw = json.dumps(
        {
            "request": {"message_path": "input"},
            "response": {"streaming": False, "body": {"text_path": "output"}},
        }
    )
    cfg = AgentcoreResponseConfig.parse_json(raw)
    assert cfg is not None
    assert cfg.body.text_path == "output"


def test_response_parse_json_legacy_format_returns_none():
    raw = '{"message": "__QUERY_PLACEHOLDER__"}'
    assert AgentcoreResponseConfig.parse_json(raw) is None


def test_response_parse_json_none_input():
    assert AgentcoreResponseConfig.parse_json(None) is None


def test_response_parse_json_empty_string_returns_none():
    assert AgentcoreResponseConfig.parse_json("") is None


def test_response_parse_json_invalid_json_returns_none():
    assert AgentcoreResponseConfig.parse_json("not-valid-json") is None


def test_response_parse_json_invalid_response_content_returns_none():
    # response key present but fails validation (non-streaming, body missing)
    raw = json.dumps({"response": {"streaming": False}})
    assert AgentcoreResponseConfig.parse_json(raw) is None


def test_response_parse_json_streaming_config():
    raw = json.dumps({"response": {"streaming": True, "chunk": {"text_path": "delta"}}})
    cfg = AgentcoreResponseConfig.parse_json(raw)
    assert cfg is not None
    assert cfg.streaming is True
    assert cfg.chunk.text_path == "delta"


def test_response_parse_json_with_reasoning():
    raw = json.dumps(
        {
            "response": {
                "streaming": False,
                "body": {"text_path": "output", "reasoning": {"text_path": "thinking", "active_path": "active"}},
            }
        }
    )
    cfg = AgentcoreResponseConfig.parse_json(raw)
    assert cfg.body.reasoning.text_path == "thinking"
    assert cfg.body.reasoning.active_path == "active"


# --- AgentcoreRequestConfig.from_json ---


def test_request_from_json_reads_message_path():
    raw = json.dumps(
        {
            "request": {"message_path": "input"},
            "response": {"streaming": False, "body": {"text_path": "output"}},
        }
    )
    cfg = AgentcoreRequestConfig.from_json(raw)
    assert cfg.message_path == "input"


def test_request_from_json_defaults_when_no_request_key():
    raw = json.dumps({"response": {"streaming": False, "body": {"text_path": "output"}}})
    cfg = AgentcoreRequestConfig.from_json(raw)
    assert cfg.message_path == "message"
    assert cfg.history is None


def test_request_from_json_none_returns_defaults():
    cfg = AgentcoreRequestConfig.from_json(None)
    assert cfg.message_path == "message"
    assert cfg.history is None


def test_request_from_json_empty_string_returns_defaults():
    cfg = AgentcoreRequestConfig.from_json("")
    assert cfg.message_path == "message"
    assert cfg.history is None


def test_request_from_json_empty_request_object_returns_defaults():
    raw = json.dumps({"request": {}, "response": {"streaming": False, "body": {"text_path": "out"}}})
    cfg = AgentcoreRequestConfig.from_json(raw)
    assert cfg.message_path == "message"
    assert cfg.history is None


def test_request_from_json_invalid_json_returns_defaults():
    cfg = AgentcoreRequestConfig.from_json("not-valid-json")
    assert cfg.message_path == "message"


def test_request_from_json_invalid_history_returns_defaults():
    # history sub-object present but missing required history_path → ValidationError caught → defaults
    raw = json.dumps(
        {
            "request": {"message_path": "q", "history": {}},
            "response": {"streaming": False, "body": {"text_path": "out"}},
        }
    )
    cfg = AgentcoreRequestConfig.from_json(raw)
    assert cfg.message_path == "message"
    assert cfg.history is None


def test_request_from_json_history_all_custom_fields():
    raw = json.dumps(
        {
            "request": {
                "message_path": "input",
                "history": {
                    "history_path": "ctx.turns",
                    "role_path": "speaker",
                    "message_path": "text",
                    "user_role": "human",
                    "assistant_role": "bot",
                },
            },
            "response": {"streaming": False, "body": {"text_path": "out"}},
        }
    )
    cfg = AgentcoreRequestConfig.from_json(raw)
    assert cfg.message_path == "input"
    assert cfg.history.history_path == "ctx.turns"
    assert cfg.history.role_path == "speaker"
    assert cfg.history.message_path == "text"
    assert cfg.history.user_role == "human"
    assert cfg.history.assistant_role == "bot"


def test_request_from_json_with_history():
    raw = json.dumps(
        {
            "request": {
                "message_path": "query",
                "history": {"history_path": "messages"},
            },
            "response": {"streaming": False, "body": {"text_path": "output"}},
        }
    )
    cfg = AgentcoreRequestConfig.from_json(raw)
    assert cfg.history.history_path == "messages"
    assert cfg.history.role_path == "role"


# --- AgentcoreHistoryConfig ---


def test_history_config_requires_history_path():
    with pytest.raises(ValidationError):
        AgentcoreHistoryConfig.model_validate({})


def test_history_config_defaults():
    cfg = AgentcoreHistoryConfig(history_path="messages")
    assert cfg.role_path == "role"
    assert cfg.message_path == "content"
    assert cfg.user_role == "user"
    assert cfg.assistant_role == "assistant"


def test_history_config_custom_values():
    cfg = AgentcoreHistoryConfig(
        history_path="ctx.turns",
        role_path="speaker",
        message_path="text",
        user_role="human",
        assistant_role="bot",
    )
    assert cfg.history_path == "ctx.turns"
    assert cfg.role_path == "speaker"
    assert cfg.message_path == "text"
    assert cfg.user_role == "human"
    assert cfg.assistant_role == "bot"


def test_request_config_history_defaults_to_none():
    cfg = AgentcoreRequestConfig()
    assert cfg.history is None


def test_request_config_accepts_history():
    cfg = AgentcoreRequestConfig(
        message_path="query",
        history=AgentcoreHistoryConfig(history_path="messages"),
    )
    assert cfg.history.history_path == "messages"
