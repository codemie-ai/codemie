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
from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from codemie.agents.assistant_agent import AIToolsAgent, TaskResult
from codemie.chains.base import GenerationResult
from codemie.core.constants import ChatRole
from codemie.core.models import AssistantChatRequest, ChatMessage
from codemie.rest_api.models.assistant import AssistantType


class SampleOutput(BaseModel):
    field: str
    value: int


def make_generation_result(generated=None, success=True):
    return GenerationResult(
        generated=generated,
        time_elapsed=0.1,
        input_tokens_used=None,
        tokens_used=None,
        success=success,
    )


def test_from_agent_response_with_string_result():
    response = make_generation_result(generated="some output", success=True)

    result = TaskResult.from_agent_response(response)

    assert result.result == "some output"
    assert result.success is True


def test_from_agent_response_success_mirrors_response():
    response = make_generation_result(generated="output", success=False)

    result = TaskResult.from_agent_response(response)

    assert result.result == "output"
    assert result.success is False


def test_from_agent_response_none_generated_returns_empty_string():
    response = make_generation_result(generated=None, success=False)

    result = TaskResult.from_agent_response(response)

    assert result.result == ""
    assert result.success is False


def test_from_agent_response_empty_string_generated_returns_empty_string():
    response = make_generation_result(generated="", success=False)

    result = TaskResult.from_agent_response(response)

    assert result.result == ""
    assert result.success is False


def test_from_agent_response_dict_generated_is_json_serialized():
    payload = {"key": "value", "count": 3}
    response = make_generation_result(generated=payload, success=True)

    result = TaskResult.from_agent_response(response)

    assert result.result == json.dumps(payload)
    assert result.success is True


def test_from_agent_response_pydantic_generated_is_json_serialized():
    model = SampleOutput(field="test", value=42)
    response = make_generation_result(generated=model, success=True)

    result = TaskResult.from_agent_response(response)

    assert result.result == model.model_dump_json()
    assert result.success is True


def test_from_agent_response_dict_with_output_key():
    response = {"output": "agent answer", "intermediate_steps": [("step1", "obs1")]}

    result = TaskResult.from_agent_response(response)

    assert result.result == "agent answer"
    assert result.success is True
    assert result.intermediate_steps == [("step1", "obs1")]


def test_from_agent_response_dict_with_generated_key():
    response = {"generated": "chain answer"}

    result = TaskResult.from_agent_response(response)

    assert result.result == "chain answer"
    assert result.success is True


def test_from_agent_response_dict_with_no_known_key():
    response = {"unknown_key": "some value"}

    result = TaskResult.from_agent_response(response)

    assert result.result == ""
    assert result.success is False


_AGENT_MOD = "codemie.agents.assistant_agent"


def _make_agentcore_agent(history=None):
    from codemie.agents.aws_bedrock.agentcore_executor import AgentCoreExecutor

    request = AssistantChatRequest(text="hello", conversation_id="conv-1")
    if history is not None:
        request.history = history

    assistant = MagicMock()
    assistant.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    assistant.bedrock_agentcore_runtime = MagicMock()
    assistant.bedrock_agentcore_runtime.runtime_arn = "arn:aws:bedrock:us-east-1::agent/test"
    assistant.bedrock = None

    agent = AIToolsAgent.__new__(AIToolsAgent)
    agent.request = request
    agent.assistant = assistant
    agent.conversation_id = request.conversation_id
    agent.agent_name = "test-agent"
    agent.llm_model = "gpt-4"
    agent.request_uuid = "test-uuid"
    agent.user = MagicMock()
    agent.trace_context = None
    agent.agent_executor = AgentCoreExecutor(
        assistant=assistant,
        conversation_id=request.conversation_id,
        history_fn=lambda: request.history,
    )
    return agent


@patch(f"{_AGENT_MOD}.AIToolsAgent._get_tool_errors", return_value=[])
@patch(f"{_AGENT_MOD}.AIToolsAgent._persist_generated_workspace_files")
@patch("codemie.agents.aws_bedrock.agentcore_executor.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_generate_forwards_chat_history_to_orchestrator(mock_invoke, _mock_persist, _mock_errors):
    history = [ChatMessage(role=ChatRole.USER, message="prior turn")]
    mock_invoke.return_value = {"output": "response text", "thoughts": [], "time_elapsed": 0.1}

    agent = _make_agentcore_agent(history=history)
    agent.generate()

    mock_invoke.assert_called_once()
    _, kwargs = mock_invoke.call_args
    assert kwargs["history"] == history
