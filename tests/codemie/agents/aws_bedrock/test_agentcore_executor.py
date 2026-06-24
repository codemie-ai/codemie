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

import uuid
from unittest.mock import MagicMock, patch

from codemie.agents.aws_bedrock.agentcore_executor import AgentCoreExecutor
from codemie.chains.base import ThoughtAuthorType


def _thought_dict(message="thinking", name=None):
    return {
        "id": str(uuid.uuid4()),
        "in_progress": False,
        "message": message,
        "author_name": name,
        "input_text": None,
        "author_type": ThoughtAuthorType.Agent,
        "parent_id": None,
        "metadata": {},
        "output_format": "text",
        "error": False,
        "interrupted": False,
        "aborted": False,
        "children": [],
    }


def _make_executor(history=None, thread_generator=None):
    assistant = MagicMock()
    return AgentCoreExecutor(
        assistant=assistant,
        conversation_id="conv-1",
        history_fn=lambda: history or [],
        thread_generator=thread_generator,
    )


@patch("codemie.agents.aws_bedrock.agentcore_executor.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_invoke_returns_output_dict(mock_invoke):
    mock_invoke.return_value = {"output": "answer", "thoughts": [], "time_elapsed": 0.1}
    executor = _make_executor()

    result = executor.invoke({"input": "hi"})

    assert result == {"output": "answer", "intermediate_steps": []}


@patch("codemie.agents.aws_bedrock.agentcore_executor.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_invoke_populates_thoughts(mock_invoke):
    t = _thought_dict("step 1")
    mock_invoke.return_value = {"output": "answer", "thoughts": [t], "time_elapsed": 0.1}
    executor = _make_executor()

    executor.invoke({"input": "hi"})

    assert len(executor.thoughts) == 1
    assert isinstance(executor.thoughts[0], dict)
    assert executor.thoughts[0]["message"] == "step 1"


@patch("codemie.agents.aws_bedrock.agentcore_executor.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_invoke_clears_previous_thoughts(mock_invoke):
    t1 = _thought_dict("old")
    t2 = _thought_dict("new")
    mock_invoke.side_effect = [
        {"output": "first", "thoughts": [t1], "time_elapsed": 0.0},
        {"output": "second", "thoughts": [t2], "time_elapsed": 0.0},
    ]
    executor = _make_executor()

    executor.invoke({"input": "first"})
    executor.invoke({"input": "second"})

    assert len(executor.thoughts) == 1
    assert executor.thoughts[0]["message"] == "new"


@patch("codemie.agents.aws_bedrock.agentcore_executor.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_invoke_passes_input_and_history(mock_invoke):
    mock_invoke.return_value = {"output": "ok", "thoughts": [], "time_elapsed": 0.0}
    history = [MagicMock()]
    executor = _make_executor(history=history)

    executor.invoke({"input": "question"})

    mock_invoke.assert_called_once_with(
        assistant=executor._assistant,
        input_text="question",
        conversation_id="conv-1",
        history=history,
    )


@patch("codemie.agents.aws_bedrock.agentcore_executor.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_stream_yields_single_chunk_and_populates_thoughts(mock_invoke):
    t = _thought_dict("stream thought")
    mock_invoke.return_value = {"output": "streamed", "thoughts": [t], "time_elapsed": 0.1}
    thread_gen = MagicMock()
    executor = _make_executor(thread_generator=thread_gen)

    chunks = list(executor.stream({"input": "hi"}))

    assert chunks == [{"output": "streamed"}]
    assert len(executor.thoughts) == 1
    assert executor.thoughts[0]["message"] == "stream thought"


@patch("codemie.agents.aws_bedrock.agentcore_executor.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_stream_passes_thread_generator(mock_invoke):
    mock_invoke.return_value = {"output": "ok", "thoughts": [], "time_elapsed": 0.0}
    thread_gen = MagicMock()
    executor = _make_executor(thread_generator=thread_gen)

    list(executor.stream({"input": "hi"}))

    mock_invoke.assert_called_once_with(
        assistant=executor._assistant,
        input_text="hi",
        conversation_id="conv-1",
        history=[],
        thread_generator=thread_gen,
    )


def test_callbacks_contains_self():
    executor = _make_executor()
    assert executor.callbacks == [executor]


def test_thoughts_initially_empty():
    executor = _make_executor()
    assert executor.thoughts == []
