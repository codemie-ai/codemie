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

from codemie.core.constants import ChatRole
from codemie.core.models import ChatMessage
from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreHistoryConfig, AgentcoreRequestConfig
from codemie.service.aws_bedrock.agentcore.agentcore_request_builder import AgentcoreRequestBuilder
from codemie.service.aws_bedrock.agentcore.utils import resolve_json_path, set_json_path


# --- resolve_json_path ---


def test_resolve_json_path_top_level():
    assert resolve_json_path({"output": "hello"}, "output") == "hello"


def test_resolve_json_path_nested():
    assert resolve_json_path({"result": {"answer": "hi"}}, "result.answer") == "hi"


def test_resolve_json_path_list_index():
    assert resolve_json_path({"choices": ["a", "b"]}, "choices.0") == "a"


def test_resolve_json_path_deeply_nested():
    data = {"a": {"b": {"c": "deep"}}}
    assert resolve_json_path(data, "a.b.c") == "deep"


def test_resolve_json_path_array_fanout():
    data = {"items": [{"text": "a"}, {"text": "b"}]}
    assert resolve_json_path(data, "items.text") == ["a", "b"]


def test_resolve_json_path_array_fanout_filters_none():
    data = {"items": [{"text": "a"}, {}, {"text": "c"}]}
    assert resolve_json_path(data, "items.text") == ["a", "c"]


def test_resolve_json_path_array_fanout_all_none_returns_none():
    data = {"items": [{}, {}]}
    assert resolve_json_path(data, "items.text") is None


def test_resolve_json_path_missing_key_returns_none():
    assert resolve_json_path({"a": 1}, "b") is None


def test_resolve_json_path_none_data_returns_none():
    assert resolve_json_path(None, "a") is None


def test_resolve_json_path_empty_path_returns_none():
    assert resolve_json_path({"a": 1}, "") is None


# --- set_json_path ---


def test_set_json_path_top_level():
    d = {}
    set_json_path(d, "message", "hello")
    assert d == {"message": "hello"}


def test_set_json_path_nested():
    d = {}
    set_json_path(d, "input.query", "hello")
    assert d == {"input": {"query": "hello"}}


def test_set_json_path_overwrites_existing():
    d = {"message": "old"}
    set_json_path(d, "message", "new")
    assert d["message"] == "new"


# --- AgentcoreRequestBuilder ---


def test_builder_simple_path():
    config = AgentcoreRequestConfig(message_path="message")
    result = json.loads(AgentcoreRequestBuilder(config).build("hello world"))
    assert result == {"message": "hello world"}


def test_builder_nested_path():
    config = AgentcoreRequestConfig(message_path="input.query")
    result = json.loads(AgentcoreRequestBuilder(config).build("test query"))
    assert result == {"input": {"query": "test query"}}


def test_builder_returns_bytes():
    config = AgentcoreRequestConfig(message_path="prompt")
    assert isinstance(AgentcoreRequestBuilder(config).build("q"), bytes)


# --- history injection ---


def _history_config(**kwargs):
    return AgentcoreHistoryConfig(history_path="messages", **kwargs)


def _msg(role: ChatRole, text: str) -> ChatMessage:
    return ChatMessage(role=role, message=text)


def test_build_with_history_injects_turns():
    config = AgentcoreRequestConfig(message_path="query", history=_history_config())
    history = [_msg(ChatRole.USER, "Hello"), _msg(ChatRole.ASSISTANT, "Hi there!")]
    result = json.loads(AgentcoreRequestBuilder(config).build("What next?", history))
    assert result["query"] == "What next?"
    assert result["messages"] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]


def test_build_without_history_config_ignores_history():
    config = AgentcoreRequestConfig(message_path="message")
    result = json.loads(AgentcoreRequestBuilder(config).build("hi", [_msg(ChatRole.USER, "should not appear")]))
    assert result == {"message": "hi"}


def test_build_with_history_config_and_none_history():
    config = AgentcoreRequestConfig(message_path="query", history=_history_config())
    result = json.loads(AgentcoreRequestBuilder(config).build("hi", None))
    assert "messages" not in result


def test_build_with_history_config_and_empty_history():
    config = AgentcoreRequestConfig(message_path="query", history=_history_config())
    result = json.loads(AgentcoreRequestBuilder(config).build("hi", []))
    assert "messages" not in result


def test_build_history_custom_role_labels():
    config = AgentcoreRequestConfig(
        message_path="query",
        history=_history_config(user_role="human", assistant_role="bot"),
    )
    history = [_msg(ChatRole.USER, "ping"), _msg(ChatRole.ASSISTANT, "pong")]
    result = json.loads(AgentcoreRequestBuilder(config).build("next", history))
    assert result["messages"][0]["role"] == "human"
    assert result["messages"][1]["role"] == "bot"


def test_build_history_custom_field_paths():
    config = AgentcoreRequestConfig(
        message_path="query",
        history=AgentcoreHistoryConfig(
            history_path="ctx.turns",
            role_path="speaker",
            message_path="text",
        ),
    )
    result = json.loads(AgentcoreRequestBuilder(config).build("go", [_msg(ChatRole.USER, "hello")]))
    assert result["ctx"]["turns"] == [{"speaker": "user", "text": "hello"}]


def test_build_history_none_message_uses_empty_string():
    config = AgentcoreRequestConfig(message_path="query", history=_history_config())
    result = json.loads(AgentcoreRequestBuilder(config).build("hi", [ChatMessage(role=ChatRole.USER, message=None)]))
    assert result["messages"][0]["content"] == ""


def test_build_no_history_arg_backward_compat():
    config = AgentcoreRequestConfig(message_path="message")
    result = json.loads(AgentcoreRequestBuilder(config).build("hello"))
    assert result == {"message": "hello"}


def test_build_history_turn_order_preserved():
    config = AgentcoreRequestConfig(message_path="q", history=_history_config())
    history = [
        _msg(ChatRole.USER, "first"),
        _msg(ChatRole.ASSISTANT, "second"),
        _msg(ChatRole.USER, "third"),
    ]
    result = json.loads(AgentcoreRequestBuilder(config).build("q", history))
    contents = [t["content"] for t in result["messages"]]
    assert contents == ["first", "second", "third"]


def test_build_history_single_assistant_turn():
    config = AgentcoreRequestConfig(message_path="q", history=_history_config())
    result = json.loads(AgentcoreRequestBuilder(config).build("q", [_msg(ChatRole.ASSISTANT, "prior reply")]))
    assert result["messages"] == [{"role": "assistant", "content": "prior reply"}]


def test_build_history_deeply_nested_history_path():
    config = AgentcoreRequestConfig(
        message_path="q",
        history=AgentcoreHistoryConfig(history_path="ctx.history.turns"),
    )
    result = json.loads(AgentcoreRequestBuilder(config).build("hi", [_msg(ChatRole.USER, "hey")]))
    assert result["ctx"]["history"]["turns"] == [{"role": "user", "content": "hey"}]


def test_build_empty_string_query():
    config = AgentcoreRequestConfig(message_path="message")
    result = json.loads(AgentcoreRequestBuilder(config).build(""))
    assert result == {"message": ""}


# --- extra_payload ---


def test_extra_payload_dict_included_in_request():
    config = AgentcoreRequestConfig(message_path="message", extra_payload={"sessionId": "s1", "mode": "fast"})
    result = json.loads(AgentcoreRequestBuilder(config).build("hello"))
    assert result["sessionId"] == "s1"
    assert result["mode"] == "fast"
    assert result["message"] == "hello"


def test_extra_payload_message_overrides_extra():
    """message_path write wins over any same-key value in extra_payload."""
    config = AgentcoreRequestConfig(message_path="message", extra_payload={"message": "stale"})
    result = json.loads(AgentcoreRequestBuilder(config).build("fresh"))
    assert result["message"] == "fresh"


def test_extra_payload_nested_keys_preserved():
    config = AgentcoreRequestConfig(
        message_path="query",
        extra_payload={"metadata": {"version": "1", "region": "us-east-1"}},
    )
    result = json.loads(AgentcoreRequestBuilder(config).build("hi"))
    assert result["metadata"] == {"version": "1", "region": "us-east-1"}
    assert result["query"] == "hi"


def test_extra_payload_not_mutated_between_calls():
    """Builder must deep-copy extra_payload so repeated calls don't share state."""
    config = AgentcoreRequestConfig(message_path="message", extra_payload={"tags": ["a"]})
    AgentcoreRequestBuilder(config).build("first")
    AgentcoreRequestBuilder(config).build("second")
    assert config.extra_payload == {"tags": ["a"]}


def test_extra_payload_none_is_no_op():
    config = AgentcoreRequestConfig(message_path="message", extra_payload=None)
    result = json.loads(AgentcoreRequestBuilder(config).build("hi"))
    assert result == {"message": "hi"}


def test_extra_payload_json_string_accepted():
    """extra_payload can be supplied as a JSON string and is parsed into a dict."""
    config = AgentcoreRequestConfig(message_path="message", extra_payload='{"sessionId": "x"}')
    assert config.extra_payload == {"sessionId": "x"}


def test_extra_payload_invalid_json_string_raises():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentcoreRequestConfig(message_path="message", extra_payload="not json {")


def test_extra_payload_json_array_raises():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentcoreRequestConfig(message_path="message", extra_payload="[1, 2, 3]")
