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
from unittest.mock import MagicMock
from codemie.service.aws_bedrock.agentcore.agentcore_config import (
    AgentcoreOutputConfig,
    AgentcoreReasoningConfig,
    AgentcoreResponseConfig,
)
from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseParser


def _json_response_config(text_path="output", reasoning=None):
    return AgentcoreResponseConfig(
        streaming=False,
        body=AgentcoreOutputConfig(text_path=text_path, reasoning=reasoning),
    )


def _streaming_config(text_path="delta", reasoning=None):
    return AgentcoreResponseConfig(
        streaming=True,
        chunk=AgentcoreOutputConfig(text_path=text_path, reasoning=reasoning),
    )


def _sse_stream(lines: list[str]):
    """Mock SSE stream from list of data payloads (already-prefixed data: lines or empty strings)."""
    mock = MagicMock()
    mock.iter_lines.return_value = [(f"data: {line}".encode() if line else b"") for line in lines]
    return mock


# --- parse_json ---


def test_parse_json_simple():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "hello"}).encode()
    text, thoughts = parser.parse_json(body, _json_response_config("output"))
    assert text == "hello"
    assert thoughts == []


def test_parse_json_nested_path():
    parser = AgentcoreResponseParser()
    body = json.dumps({"result": {"answer": "hi"}}).encode()
    text, thoughts = parser.parse_json(body, _json_response_config("result.answer"))
    assert text == "hi"


def test_parse_json_missing_path_raises():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    body = json.dumps({"other": "value"}).encode()
    with pytest.raises(AgentcoreResponseError):
        parser.parse_json(body, _json_response_config("output"))


def test_parse_json_with_reasoning():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer", "thinking": "my reasoning"}).encode()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="unused")
    text, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert text == "answer"
    assert len(thoughts) == 1
    assert thoughts[0].message == "my reasoning"
    assert thoughts[0].in_progress is False


def test_parse_json_reasoning_with_name_and_args():
    parser = AgentcoreResponseParser()
    body = json.dumps(
        {
            "output": "done",
            "thinking": "reasoning text",
            "tool": "SearchTool",
            "args": {"q": "weather"},
        }
    ).encode()
    reasoning = AgentcoreReasoningConfig(
        text_path="thinking",
        active_path="unused",
        name_path="tool",
        args_path="args",
    )
    text, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert thoughts[0].author_name == "SearchTool"
    assert '"q": "weather"' in thoughts[0].input_text


# --- parse_streaming ---


def test_parse_streaming_json_chunks():
    parser = AgentcoreResponseParser()
    config = _streaming_config("delta")
    stream = _sse_stream(
        [
            json.dumps({"delta": "Hello"}),
            json.dumps({"delta": " world"}),
            "",
        ]
    )
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "Hello world"


def test_parse_streaming_thoughts_in_progress():
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="active")
    config = _streaming_config("text", reasoning)
    stream = _sse_stream(
        [
            json.dumps({"thinking": "step 1", "active": True}),
            json.dumps({"thinking": " step 2", "active": True}),
            json.dumps({"thinking": "done", "active": False}),
            json.dumps({"text": "answer"}),
            "",
        ]
    )
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "answer"
    in_progress = [t for t in thoughts if t.in_progress]
    closed = [t for t in thoughts if not t.in_progress]
    assert len(in_progress) >= 1
    assert len(closed) >= 1


def test_parse_streaming_thought_closes_at_stream_end():
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="active")
    config = _streaming_config("text", reasoning)
    stream = _sse_stream(
        [
            json.dumps({"thinking": "still thinking", "active": True}),
            "",
        ]
    )
    text, thoughts = parser.parse_streaming(stream, config)
    assert any(not t.in_progress for t in thoughts)


def test_parse_streaming_skips_non_data_lines():
    parser = AgentcoreResponseParser()
    config = _streaming_config("delta")
    mock_stream = MagicMock()
    mock_stream.iter_lines.return_value = [
        b"event: message",
        b"data: " + json.dumps({"delta": "hi"}).encode(),
        b"",
    ]
    text, thoughts = parser.parse_streaming(mock_stream, config)
    assert text == "hi"


# --- parse_json edge cases ---


def test_parse_json_malformed_body_raises():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    body = b"not valid json"
    with pytest.raises(AgentcoreResponseError, match="streaming response"):
        parser.parse_json(body, _json_response_config("output"))


def test_parse_json_text_path_missing_raises():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    body = json.dumps({"other": "value"}).encode()
    with pytest.raises(AgentcoreResponseError, match="AgentCore Error"):
        parser.parse_json(body, _json_response_config("output"))


def test_parse_json_reasoning_path_not_found_returns_empty_thoughts():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer"}).encode()
    reasoning = AgentcoreReasoningConfig(text_path="missing_path")
    text, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert text == "answer"
    assert thoughts == []


def test_parse_json_reasoning_fanout_multiple_thoughts():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "done", "steps": ["step one", "step two", "step three"]}).encode()
    reasoning = AgentcoreReasoningConfig(text_path="steps")
    text, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert text == "done"
    assert len(thoughts) == 3
    assert [t.message for t in thoughts] == ["step one", "step two", "step three"]


def test_parse_json_reasoning_fanout_name_args_by_index():
    parser = AgentcoreResponseParser()
    body = json.dumps(
        {
            "output": "done",
            "steps": ["reasoning A", "reasoning B"],
            "tools": ["ToolX", "ToolY"],
            "tool_args": [{"x": 1}, {"y": 2}],
        }
    ).encode()
    reasoning = AgentcoreReasoningConfig(text_path="steps", name_path="tools", args_path="tool_args")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert thoughts[0].author_name == "ToolX"
    assert '"x": 1' in thoughts[0].input_text
    assert thoughts[1].author_name == "ToolY"
    assert '"y": 2' in thoughts[1].input_text


# --- parse_streaming edge cases ---


def test_parse_streaming_empty_stream():
    parser = AgentcoreResponseParser()
    config = _streaming_config("delta")
    stream = MagicMock()
    stream.iter_lines.return_value = []
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == ""
    assert thoughts == []


def test_parse_streaming_malformed_json_chunk_appended_raw():
    parser = AgentcoreResponseParser()
    config = _streaming_config("delta")
    stream = MagicMock()
    stream.iter_lines.return_value = [b"data: not-json", b"data: " + json.dumps({"delta": " ok"}).encode()]
    text, thoughts = parser.parse_streaming(stream, config)
    assert "not-json" in text
    assert " ok" in text


def test_parse_streaming_text_path_missing_in_chunk_raises():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    config = _streaming_config("delta")
    stream = _sse_stream([json.dumps({"other": "x"}), json.dumps({"delta": "y"})])
    with pytest.raises(AgentcoreResponseError, match="AgentCore Error"):
        parser.parse_streaming(stream, config)


def test_parse_streaming_reasoning_without_active_path_treated_as_text():
    # active_path=None → active resolves to None → neither True nor False → falls through to text
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking")  # active_path omitted
    config = _streaming_config("text", reasoning)
    stream = _sse_stream([json.dumps({"text": "answer", "thinking": "some thought"})])
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "answer"
    assert thoughts == []


def test_parse_streaming_active_false_without_open_thought_is_noop():
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="active")
    config = _streaming_config("text", reasoning)
    stream = _sse_stream([json.dumps({"active": False, "thinking": "stray"}), json.dumps({"text": "hi"})])
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "hi"
    assert thoughts == []


def test_parse_streaming_reasoning_with_name_and_args():
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="active", name_path="tool", args_path="args")
    config = _streaming_config("text", reasoning)
    stream = _sse_stream(
        [
            json.dumps({"active": True, "thinking": "planning", "tool": "SearchTool", "args": {"q": "test"}}),
            json.dumps({"active": False}),
            json.dumps({"text": "result"}),
        ]
    )
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "result"
    closed = [t for t in thoughts if not t.in_progress]
    assert len(closed) == 1
    assert closed[0].author_name == "SearchTool"


def test_parse_streaming_multiple_separate_thought_blocks():
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="active")
    config = _streaming_config("text", reasoning)
    stream = _sse_stream(
        [
            json.dumps({"active": True, "thinking": "block 1"}),
            json.dumps({"active": False}),
            json.dumps({"text": "mid"}),
            json.dumps({"active": True, "thinking": "block 2"}),
            json.dumps({"active": False}),
            json.dumps({"text": " end"}),
        ]
    )
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "mid end"
    closed = [t for t in thoughts if not t.in_progress]
    assert len(closed) == 2


# --- parse_streaming emit_stream=True ---


def test_parse_streaming_emit_yields_text_per_chunk():
    parser = AgentcoreResponseParser()
    config = _streaming_config("delta")
    stream = _sse_stream([json.dumps({"delta": "Hello"}), json.dumps({"delta": " world"}), ""])
    results = list(parser.parse_streaming(stream, config, emit_stream=True))
    text_chunks = [t for t, _ in results if t is not None]
    assert text_chunks == ["Hello", " world"]


def test_parse_streaming_emit_yields_thought_frames():
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="active")
    config = _streaming_config("text", reasoning)
    stream = _sse_stream(
        [
            json.dumps({"active": True, "thinking": "step 1"}),
            json.dumps({"active": False}),
            json.dumps({"text": "answer"}),
        ]
    )
    results = list(parser.parse_streaming(stream, config, emit_stream=True))
    all_thoughts = [t for _, thoughts in results for t in thoughts]
    assert len(all_thoughts) >= 1
    text_chunks = [t for t, _ in results if t is not None]
    assert text_chunks == ["answer"]


def test_parse_streaming_emit_closes_unclosed_thought_at_end():
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="active")
    config = _streaming_config("text", reasoning)
    stream = _sse_stream([json.dumps({"active": True, "thinking": "still going"})])
    results = list(parser.parse_streaming(stream, config, emit_stream=True))
    all_thoughts = [t for _, thoughts in results for t in thoughts]
    assert any(not t.in_progress for t in all_thoughts)


def test_parse_streaming_emit_malformed_json_yields_raw():
    parser = AgentcoreResponseParser()
    config = _streaming_config("delta")
    from unittest.mock import MagicMock

    stream = MagicMock()
    stream.iter_lines.return_value = [b"data: not-json", b"data: " + json.dumps({"delta": "ok"}).encode()]
    results = list(parser.parse_streaming(stream, config, emit_stream=True))
    text_chunks = [t for t, _ in results if t is not None]
    assert "not-json" in text_chunks
    assert "ok" in text_chunks


# --- config/response mismatch ---


def test_parse_streaming_text_path_resolves_to_int_coerced_to_str():
    # Runtime returns a number at text_path — should coerce to string, not raise TypeError.
    parser = AgentcoreResponseParser()
    config = _streaming_config("count")
    stream = _sse_stream([json.dumps({"count": 42})])
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "42"


def test_parse_streaming_text_path_resolves_to_bool_coerced_to_str():
    parser = AgentcoreResponseParser()
    config = _streaming_config("flag")
    stream = _sse_stream([json.dumps({"flag": True})])
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "True"


def test_parse_json_reasoning_name_path_missing_from_response():
    # name_path configured but field absent — thought should have author_name=None.
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer", "thinking": "step"}).encode()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", name_path="missing_tool")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert len(thoughts) == 1
    assert thoughts[0].author_name is None


def test_parse_json_reasoning_args_path_missing_from_response():
    # args_path configured but field absent — thought should have input_text=None.
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer", "thinking": "step"}).encode()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", args_path="missing_args")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert len(thoughts) == 1
    assert thoughts[0].input_text is None


def test_parse_streaming_active_path_wrong_name_falls_through_to_text():
    # active_path is configured but field name doesn't match response — resolves to None,
    # neither True nor False branch fires, so chunk is treated as text.
    parser = AgentcoreResponseParser()
    reasoning = AgentcoreReasoningConfig(text_path="thinking", active_path="wrong_active_field")
    config = _streaming_config("text", reasoning)
    stream = _sse_stream([json.dumps({"active": True, "thinking": "thought", "text": "answer"})])
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "answer"
    assert thoughts == []


def test_parse_streaming_double_encoded_chunk():
    # Runtime double-encodes the JSON — inner string is itself a JSON string.
    parser = AgentcoreResponseParser()
    config = _streaming_config("delta")
    inner = json.dumps({"delta": "hello"})
    stream = _sse_stream([json.dumps(inner)])  # outer encode wraps the inner JSON string
    text, thoughts = parser.parse_streaming(stream, config)
    assert text == "hello"


def test_build_extra_payload_clash_with_history_path_history_wins():
    # If extra_payload sets a key at the same path as history_path, history injection overwrites it.
    from codemie.service.aws_bedrock.agentcore.agentcore_request_builder import AgentcoreRequestBuilder
    from codemie.service.aws_bedrock.agentcore.agentcore_config import AgentcoreRequestConfig, AgentcoreHistoryConfig
    from codemie.core.models import ChatMessage
    from codemie.core.constants import ChatRole

    config = AgentcoreRequestConfig(
        message_path="query",
        extra_payload={"messages": "stale"},
        history=AgentcoreHistoryConfig(history_path="messages"),
    )
    history = [ChatMessage(role=ChatRole.USER, message="hi")]
    result = json.loads(AgentcoreRequestBuilder(config).build("go", history))
    assert isinstance(result["messages"], list)
    assert result["messages"][0]["content"] == "hi"


# --- AgentcoreResponseError ---


def test_parse_json_missing_text_path_raises():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    body = json.dumps({"other_key": "hello"}).encode()
    with pytest.raises(AgentcoreResponseError, match="AgentCore Error"):
        parser.parse_json(body, _json_response_config(text_path="output"))


def test_parse_json_text_path_is_dict_raises():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    body = json.dumps({"output": {"nested": "value"}}).encode()
    with pytest.raises(AgentcoreResponseError, match="dict"):
        parser.parse_json(body, _json_response_config(text_path="output"))


def test_parse_json_text_path_is_list_raises():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    body = json.dumps({"output": ["a", "b"]}).encode()
    with pytest.raises(AgentcoreResponseError, match="list"):
        parser.parse_json(body, _json_response_config(text_path="output"))


def test_parse_streaming_text_path_is_dict_raises():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    stream = _sse_stream([json.dumps({"delta": {"nested": "oops"}})])
    with pytest.raises(AgentcoreResponseError, match="dict"):
        parser.parse_streaming(stream, _streaming_config(text_path="delta"))


def test_parse_streaming_text_path_is_list_raises():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    stream = _sse_stream([json.dumps({"delta": ["a", "b"]})])
    with pytest.raises(AgentcoreResponseError, match="list"):
        parser.parse_streaming(stream, _streaming_config(text_path="delta"))


def test_parse_json_sse_body_raises_streaming_mismatch_error():
    import pytest
    from codemie.service.aws_bedrock.agentcore.agentcore_response_parser import AgentcoreResponseError

    parser = AgentcoreResponseParser()
    sse_body = b'data: {"text": "hi"}\n\ndata: {"text": "there"}\n\n'
    with pytest.raises(AgentcoreResponseError, match="streaming response"):
        parser.parse_json(sse_body, _json_response_config("output"))


# --- thoughts_path array extraction ---


def test_parse_json_thoughts_path_extracts_array_of_objects():
    parser = AgentcoreResponseParser()
    body = json.dumps(
        {
            "output": "The answer",
            "thoughts": [{"text": "I thought X"}, {"text": "I considered Y"}],
        }
    ).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    text, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert text == "The answer"
    assert len(thoughts) == 2
    assert thoughts[0].message == "I thought X"
    assert thoughts[1].message == "I considered Y"


def test_parse_json_thoughts_path_with_name_and_args():
    parser = AgentcoreResponseParser()
    body = json.dumps(
        {
            "output": "done",
            "steps": [
                {"text": "reasoning A", "tool": "SearchTool", "params": {"q": "test"}},
                {"text": "reasoning B", "tool": "AnalysisTool", "params": {"x": 1}},
            ],
        }
    ).encode()
    reasoning = AgentcoreReasoningConfig(
        thoughts_path="steps",
        text_path="text",
        name_path="tool",
        args_path="params",
    )
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert len(thoughts) == 2
    assert thoughts[0].message == "reasoning A"
    assert thoughts[0].author_name == "SearchTool"
    assert '"q": "test"' in thoughts[0].input_text
    assert thoughts[1].author_name == "AnalysisTool"


def test_parse_json_thoughts_path_missing_array():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer"}).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert thoughts == []


def test_parse_json_thoughts_path_non_list():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer", "thoughts": "not a list"}).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert thoughts == []


def test_parse_json_thoughts_path_empty_array():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer", "thoughts": []}).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert thoughts == []


def test_parse_json_thoughts_path_skips_null_text():
    parser = AgentcoreResponseParser()
    body = json.dumps(
        {
            "output": "answer",
            "thoughts": [
                {"text": "real thought"},
                {"other": "no text here"},
                {"text": "another thought"},
            ],
        }
    ).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert len(thoughts) == 2
    assert thoughts[0].message == "real thought"
    assert thoughts[1].message == "another thought"


def test_parse_json_thoughts_path_scalar_items():
    parser = AgentcoreResponseParser()
    body = json.dumps(
        {
            "output": "answer",
            "thoughts": ["plain string 1", "plain string 2"],
        }
    ).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert len(thoughts) == 2
    assert thoughts[0].message == "plain string 1"
    assert thoughts[1].message == "plain string 2"
