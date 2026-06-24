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

from unittest.mock import MagicMock, patch

from codemie.agents.aws_bedrock.bedrock_agent_executor import BedrockAgentExecutor


def _make_executor(history=None):
    assistant = MagicMock()
    return BedrockAgentExecutor(
        assistant=assistant,
        conversation_id="conv-1",
        history_fn=lambda: history or [],
    )


@patch("codemie.agents.aws_bedrock.bedrock_agent_executor.BedrockAgentService.invoke_agent")
def test_invoke_returns_output_dict(mock_invoke):
    mock_invoke.return_value = {"output": "hello", "time_elapsed": 0.1}
    executor = _make_executor()

    result = executor.invoke({"input": "hi"})

    assert result == {"output": "hello", "intermediate_steps": []}


@patch("codemie.agents.aws_bedrock.bedrock_agent_executor.BedrockAgentService.invoke_agent")
def test_invoke_passes_input_and_history(mock_invoke):
    mock_invoke.return_value = {"output": "ok", "time_elapsed": 0.0}
    history = [MagicMock()]
    executor = _make_executor(history=history)

    executor.invoke({"input": "question"})

    mock_invoke.assert_called_once_with(
        assistant=executor._assistant,
        input_text="question",
        conversation_id="conv-1",
        chat_history=history,
    )


@patch("codemie.agents.aws_bedrock.bedrock_agent_executor.BedrockAgentService.invoke_agent")
def test_stream_yields_single_output_chunk(mock_invoke):
    mock_invoke.return_value = {"output": "streamed", "time_elapsed": 0.1}
    executor = _make_executor()

    chunks = list(executor.stream({"input": "hi"}))

    assert chunks == [{"output": "streamed", "intermediate_steps": []}]


def test_callbacks_is_empty_list():
    executor = _make_executor()
    assert executor.callbacks == []
