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

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from starlette.responses import StreamingResponse

from codemie.chains.base import Thought
from codemie.core.errors import ErrorCode, ToolErrorDetails
from codemie.core.models import BaseModelResponse, ChatRole
from codemie.rest_api.models.conversation import GeneratedMessage
from codemie.service.assistant import tool_output_visibility as tov

A = "codemie.service.assistant.tool_output_visibility.Assistant.get_by_ids_no_permission_check"


def line(**kw):
    return json.dumps(kw) + "\n"


def test_is_hidden():
    assert tov.is_hidden(SimpleNamespace(hide_tool_outputs=True)) is True
    assert tov.is_hidden(SimpleNamespace(hide_tool_outputs=False)) is False
    assert tov.is_hidden(SimpleNamespace()) is False


def test_thought_only_chunk_is_dropped():
    assert tov.strip_chunk(line(thought={"id": "1", "message": "x"}, generated_chunk=None, last=False)) is None


def test_text_chunk_loses_thought_and_debug_but_keeps_text():
    out = json.loads(tov.strip_chunk(line(generated_chunk="hi", thought={"id": "1"}, debug={"a": 1})))
    assert out["generated_chunk"] == "hi" and out["thought"] is None and out["debug"] is None


def test_final_chunk_nulls_tool_errors_keeps_agent_and_execution_error():
    out = json.loads(
        tov.strip_chunk(
            line(
                last=True,
                generated="done",
                tool_errors=[{"e": 1}],
                agent_error={"m": "boom"},
                execution_error="E",
            )
        )
    )
    assert out["tool_errors"] is None and out["agent_error"] == {"m": "boom"} and out["execution_error"] == "E"


@pytest.mark.parametrize("key,val", [("a2ui", {"v": 1}), ("tool_call_pending", {"id": "t"})])
def test_a2ui_and_tool_call_pending_chunks_survive_even_with_thought(key, val):
    out = json.loads(tov.strip_chunk(line(**{key: val}, thought={"id": "1"})))
    assert out[key] == val and out["thought"] is None


def test_security_error_message_moves_to_generated():
    chunk = line(
        last=True,
        generated="",
        thought={"id": "s", "message": "Blocked by policy", "error": True},
        execution_error="guardrails",
    )
    out = json.loads(tov.strip_chunk(chunk))
    assert out["generated"] == "Blocked by policy" and out["thought"] is None


def test_security_error_on_non_final_chunk_is_also_moved_to_generated():
    # on_llm_error can emit the error thought mid-stream with last=False; it must still surface.
    chunk = line(
        last=False,
        generated_chunk="partial",
        thought={"id": "s", "message": "Blocked by policy", "error": True},
        execution_error="guardrails",
    )
    out = json.loads(tov.strip_chunk(chunk))
    assert out["generated"] == "Blocked by policy" and out["thought"] is None and out["generated_chunk"] == "partial"


def test_tool_error_message_is_not_surfaced():
    # A plain tool exception (execution_error="stacktrace") must not leak its text into generated.
    chunk = line(
        last=False,
        generated_chunk=None,
        thought={"id": "t", "message": "HTTP 500 from http://internal.corp/api", "error": True},
        execution_error="stacktrace",
    )
    out = json.loads(tov.strip_chunk(chunk))
    assert out["thought"] is None and not out.get("generated")
    assert "internal.corp" not in json.dumps(out)


def test_unicode_line_separator_inside_json_string_does_not_split_line():
    raw = '{"thought": {"id": "1", "message": "a\u2028b\u0085c"}, "generated_chunk": "hi"}\n'
    raw = raw.replace("\\u2028", "\u2028").replace("\\u0085", "\u0085")
    assert "\u2028" in raw
    out = json.loads(tov.strip_chunk(raw))
    assert out["thought"] is None and out["generated_chunk"] == "hi"
    assert tov.strip_chunk(raw.replace('"generated_chunk": "hi"', '"generated_chunk": null')) is None


def test_non_json_line_is_dropped_and_bytes_handled():
    # Unparseable lines can't be verified free of hidden content, so they must not pass through.
    assert tov.strip_chunk("not json\n") is None
    out = tov.strip_chunk(line(generated_chunk="x", thought={"id": "1"}).encode())
    assert isinstance(out, bytes)


def test_multiple_lines_in_one_chunk():
    chunk = line(thought={"id": "1"}) + line(generated_chunk="a")
    out = tov.strip_chunk(chunk)
    assert out.count("\n") == 1 and json.loads(out)["generated_chunk"] == "a"


def test_strip_stream_sync_and_async():
    chunks = [line(thought={"id": "1"}), line(generated_chunk="a")]
    assert len(list(tov.strip_stream(iter(chunks)))) == 1

    async def agen():
        for c in chunks:
            yield c

    async def collect():
        return [c async for c in tov.strip_stream(agen())]

    assert len(asyncio.run(collect())) == 1


def _msg(assistant_id, thoughts=("t",)):
    return GeneratedMessage(
        role=ChatRole.ASSISTANT,
        message="m",
        assistant_id=assistant_id,
        thoughts=[Thought(id=t) for t in thoughts],
    )


def test_strip_history_checks_each_message_assistant_and_does_not_mutate():
    history = [_msg("hidden"), _msg("open"), _msg(None)]
    with patch(
        A,
        return_value=[
            SimpleNamespace(id="hidden", hide_tool_outputs=True),
            SimpleNamespace(id="open", hide_tool_outputs=False),
        ],
    ) as lookup:
        out = tov.strip_history(history, default_assistant_id="hidden")
    assert [bool(m.thoughts) for m in out] == [False, True, False]  # None falls back to default
    assert all(m.thoughts for m in history)  # input untouched
    lookup.assert_called_once()  # one query for the whole history


def test_strip_history_returns_new_list_when_nothing_hidden():
    history = [_msg("open")]
    with patch(A, return_value=[SimpleNamespace(id="open", hide_tool_outputs=False)]):
        assert tov.strip_history(history, None) is not history
    assert tov.strip_history([], None) is not None


def test_strip_history_none_does_not_raise():
    assert tov.strip_history(None, None) == []


def test_strip_history_unresolvable_assistant_keeps_thoughts():
    # Imported placeholder ids and deleted assistants have no flagged assistant: behaviour unchanged.
    with patch(A, return_value=[]):
        out = tov.strip_history([_msg("gone")], default_assistant_id=None)
    assert len(out[0].thoughts) == 1


def test_interrupted_thought_survives_stream_stripping_reduced():
    thought = {
        "id": "t1",
        "author_name": "send_email",
        "author_type": "Tool",
        "input_text": '{"to": "a"}',
        "message": "Tool call pending",
        "metadata": {"secret": 1},
        "children": [{"id": "c"}],
        "interrupted": True,
    }
    out = json.loads(tov.strip_chunk(line(last=True, thought=thought, generated=None)))
    assert out["last"] is True
    assert out["thought"] == {
        "id": "t1",
        "author_name": "send_email",
        "author_type": "Tool",
        "input_text": '{"to": "a"}',
        "message": "Tool call pending",
        "interrupted": True,
        "in_progress": False,
    }


def test_strip_history_keeps_only_reduced_interrupted_thoughts():
    msg = GeneratedMessage(
        role=ChatRole.ASSISTANT,
        message="m",
        assistant_id="hidden",
        thoughts=[
            Thought(id="plain"),
            Thought(id="pending", author_name="tool", input_text="{}", metadata={"x": 1}, interrupted=True),
        ],
    )
    with patch(A, return_value=[SimpleNamespace(id="hidden", hide_tool_outputs=True)]):
        out = tov.strip_history([msg], None)
    assert [t.id for t in out[0].thoughts] == ["pending"]
    assert out[0].thoughts[0].interrupted is True and out[0].thoughts[0].metadata == {}
    assert len(msg.thoughts) == 2  # input untouched


def test_strip_history_uses_unfiltered_lookup_not_permission_filtered():
    with (
        patch(A, return_value=[]) as unfiltered,
        patch("codemie.service.assistant.tool_output_visibility.Assistant.get_by_ids") as filtered,
    ):
        tov.strip_history([_msg("x")], None)
    unfiltered.assert_called_once()
    filtered.assert_not_called()


def test_is_hidden_by_id_none_does_not_query():
    with patch(A) as lookup:
        assert tov.is_hidden_by_id(None) is False
    lookup.assert_not_called()


def test_is_hidden_by_id_unknown_hidden_and_open():
    with patch(A, return_value=[]):
        assert tov.is_hidden_by_id("missing") is False
    with patch(A, return_value=[SimpleNamespace(id="h", hide_tool_outputs=True)]):
        assert tov.is_hidden_by_id("h") is True
    with patch(A, return_value=[SimpleNamespace(id="o", hide_tool_outputs=False)]):
        assert tov.is_hidden_by_id("o") is False


def test_apply_to_response_noop_when_not_hidden():
    sentinel = object()
    assert tov.apply_to_response(SimpleNamespace(hide_tool_outputs=False), sentinel) is sentinel


def test_apply_to_response_sync_base_model_response():
    resp = BaseModelResponse(
        generated="answer",
        thoughts=[Thought(id="1")],
        tool_errors=[ToolErrorDetails(error_code=ErrorCode.TOOL_NOT_FOUND, message="bad", tool_name="t")],
    )
    out = tov.apply_to_response(SimpleNamespace(hide_tool_outputs=True), resp)
    assert out.thoughts == [] and not out.tool_errors and out.generated == resp.generated
    assert resp.thoughts and resp.tool_errors  # original untouched


def test_apply_to_response_streaming_wraps_iterator():
    resp = StreamingResponse(iter([line(thought={"id": "1"}), line(generated_chunk="a")]))
    out = tov.apply_to_response(SimpleNamespace(hide_tool_outputs=True), resp)

    async def collect():
        return [c async for c in out.body_iterator]  # Starlette wraps sync iterators as async

    assert len(asyncio.run(collect())) == 1


def test_apply_to_response_unknown_type_logs_error_and_passes_through():
    sentinel = object()
    with patch.object(tov, "logger") as log:
        assert tov.apply_to_response(SimpleNamespace(hide_tool_outputs=True), sentinel) is sentinel
    log.error.assert_called_once()


def test_strip_response_keeps_only_reduced_interrupted_thoughts():
    response = BaseModelResponse(
        generated="ok",
        thoughts=[Thought(id="plain"), Thought(id="p", interrupted=True, input_text="{}", metadata={"x": 1})],
        time_elapsed=1.0,
    )
    out = tov.strip_response(response)
    assert [t.id for t in out.thoughts] == ["p"]
    assert out.thoughts[0].interrupted is True and out.thoughts[0].metadata == {}
    assert len(response.thoughts) == 2  # input untouched


def test_strip_history_for_conversation_skips_workflow_conversations():
    conversation = SimpleNamespace(is_workflow_conversation=True, history=[_msg("wf")], initial_assistant_id="wf")
    with patch(A, return_value=[SimpleNamespace(id="wf", hide_tool_outputs=True)]) as lookup:
        out = tov.strip_history_for_conversation(conversation)
    assert out[0].thoughts
    lookup.assert_not_called()


@pytest.mark.parametrize(
    ("error", "from_tool", "expected"),
    [
        ("Content blocked by policy", False, "guardrails"),
        ('{"error_type": "HATE_SPEECH", "guardrail": "hate_speech", "stage": "pre_call"}', False, "guardrails"),
        ("rate limit exceeded", False, "stacktrace"),
        # A tool error can echo remote text; it must never be labelled a guardrail block.
        ("Content blocked by upstream proxy", True, "stacktrace"),
    ],
)
def test_classify_execution_error(error, from_tool, expected):
    from codemie.agents.callbacks.callback_utils import _classify_execution_error

    assert _classify_execution_error(Exception(error), from_tool=from_tool) == expected


def test_final_agent_failure_chunk_keeps_friendly_message_and_error_code():
    # _send_error_to_thread ends a failed run with generated=<friendly message> and execution_error=<code>.
    # Hiding tool outputs must not swallow it, or users would see a silent failure after a provider error.
    chunk = line(
        generated="The model provider is rate limiting requests. Please retry shortly.",
        generated_chunk="",
        last=True,
        execution_error="llm_rate_limit_error",
        thought=None,
    )
    out = json.loads(tov.strip_chunk(chunk))
    assert out["generated"] == "The model provider is rate limiting requests. Please retry shortly."
    assert out["execution_error"] == "llm_rate_limit_error" and out["last"] is True
