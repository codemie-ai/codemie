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
from functools import partial
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from codemie.agents.assistant_agent import AIToolsAgent, TaskResult
from codemie.chains.base import GenerationResult
from codemie.core.constants import ChatRole
from codemie.core.errors import ErrorDetailLevel
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


def test_from_agent_response_defaults_truncated():
    response = make_generation_result(generated="some output", success=True)

    result = TaskResult.from_agent_response(response)

    assert result.truncated is False


def test_from_agent_response_sets_truncated_when_passed():
    response = make_generation_result(generated="partial output", success=True)

    result = TaskResult.from_agent_response(response, truncated=True)

    assert result.truncated is True


def test_from_agent_response_dict_with_output_key_sets_truncation_signal():
    response = {"output": "agent answer"}

    result = TaskResult.from_agent_response(response, truncated=True)

    assert result.truncated is True


def test_from_agent_response_dict_with_generated_key_defaults_truncation_signal():
    response = {"generated": "chain answer"}

    result = TaskResult.from_agent_response(response)

    assert result.truncated is False


_AGENT_MOD_LANGGRAPH = "codemie.agents.langgraph_agent"


class TestInvokeTaskTruncationThreading:
    """EPMCDME-14145: invoke_task must thread _last_truncated into TaskResult."""

    def _make_langgraph_agent(self):
        from codemie.agents.langgraph_agent import LangGraphAgent

        agent = LangGraphAgent.__new__(LangGraphAgent)
        agent.llm_model = "gpt-4"
        agent.agent_name = "test-agent"
        agent.request_uuid = "test-uuid"
        agent.assistant = MagicMock()
        agent.user = MagicMock()
        agent.conversation_id = "test-conversation-id"
        agent._last_truncated = False
        return agent

    @patch(f"{_AGENT_MOD_LANGGRAPH}.set_llm_context")
    def test_invoke_task_threads_truncation_signal_into_task_result(self, _mock_set_llm_context):
        agent = self._make_langgraph_agent()

        def fake_invoke_agent(inputs):
            agent._last_truncated = True
            return {"output": "partial answer"}

        agent._invoke_agent = fake_invoke_agent
        agent._get_inputs = MagicMock(return_value={})
        agent._truncate_log_content = MagicMock(return_value="")
        agent._serialize_messages_for_log = MagicMock(return_value="")
        agent._filter_history = MagicMock(return_value=[])
        agent._transform_history = MagicMock(return_value=[])

        result = agent.invoke_task()

        assert result.truncated is True

    @patch(f"{_AGENT_MOD_LANGGRAPH}.set_llm_context")
    def test_invoke_task_defaults_truncation_signal_when_not_truncated(self, _mock_set_llm_context):
        agent = self._make_langgraph_agent()

        def fake_invoke_agent(inputs):
            return {"output": "complete answer"}

        agent._invoke_agent = fake_invoke_agent
        agent._get_inputs = MagicMock(return_value={})
        agent._truncate_log_content = MagicMock(return_value="")
        agent._serialize_messages_for_log = MagicMock(return_value="")
        agent._filter_history = MagicMock(return_value=[])
        agent._transform_history = MagicMock(return_value=[])

        result = agent.invoke_task()

        assert result.truncated is False

    @patch(f"{_AGENT_MOD_LANGGRAPH}.set_llm_context")
    def test_invoke_task_resets_truncation_signal_before_invoking(self, _mock_set_llm_context):
        """A stale flag from a prior call must not leak into a fresh invoke_task call."""
        agent = self._make_langgraph_agent()
        agent._last_truncated = True

        def fake_invoke_agent(inputs):
            # Simulate a fresh graph run that does not hit truncation this time.
            return {"output": "complete answer"}

        agent._invoke_agent = fake_invoke_agent
        agent._get_inputs = MagicMock(return_value={})
        agent._truncate_log_content = MagicMock(return_value="")
        agent._serialize_messages_for_log = MagicMock(return_value="")
        agent._filter_history = MagicMock(return_value=[])
        agent._transform_history = MagicMock(return_value=[])

        result = agent.invoke_task()

        assert result.truncated is False


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
    assistant.project = None

    agent = AIToolsAgent.__new__(AIToolsAgent)
    agent.request = request
    agent.assistant = assistant
    agent.conversation_id = request.conversation_id
    agent.agent_name = "test-agent"
    agent.llm_model = "gpt-4"
    agent._resolved_llm_model = None
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


class TestResolveLlmModel:
    """_resolve_llm_model delegates the project rule to ProjectModelPolicy."""

    _FOR_PROJECT = "codemie.service.llm.model_availability_service.ProjectModelPolicy.for_project"

    @staticmethod
    def _agent(project="proj", is_global=False, llm_model="blocked"):
        agent = AIToolsAgent.__new__(AIToolsAgent)
        agent.assistant = MagicMock(project=project, is_global=is_global)
        agent.llm_model = llm_model
        agent._resolved_llm_model = None
        return agent

    def test_whitelist_policy_replaces_a_blocked_model_with_the_default(self):
        from codemie.service.llm.model_availability_service import PolicyMode, ProjectModelPolicy

        policy = ProjectModelPolicy(PolicyMode.WHITELIST, ["gpt-4"], "gpt-4", "proj")
        agent = self._agent()
        with patch(self._FOR_PROJECT, return_value=policy) as for_project:
            assert agent._resolve_llm_model() == "gpt-4"

        for_project.assert_called_once_with("proj", False)

    def test_deny_all_policy_raises(self):
        from codemie.core.exceptions import ModelNotWhitelistedException
        from codemie.service.llm.model_availability_service import PolicyMode, ProjectModelPolicy

        policy = ProjectModelPolicy(PolicyMode.DENY_ALL, [], "gpt-4", "proj")
        with patch(self._FOR_PROJECT, return_value=policy), pytest.raises(ModelNotWhitelistedException):
            self._agent()._resolve_llm_model()

    def test_global_assistant_is_passed_to_the_policy(self):
        from codemie.service.llm.model_availability_service import PolicyMode, ProjectModelPolicy

        agent = self._agent(is_global=True)
        with patch(self._FOR_PROJECT, return_value=ProjectModelPolicy(PolicyMode.UNRESTRICTED)) as for_project:
            assert agent._resolve_llm_model() == "blocked"

        for_project.assert_called_once_with("proj", True)

    def test_assistant_without_project_skips_the_policy(self):
        agent = self._agent(project=None)
        with patch(self._FOR_PROJECT) as for_project:
            assert agent._resolve_llm_model() == "blocked"

        for_project.assert_not_called()


@patch(f"{_AGENT_MOD}.set_logging_info")
@patch(f"{_AGENT_MOD}.AIToolsAgent._agent_streaming")
@patch(f"{_AGENT_MOD}.AIToolsAgent.is_pure_chain", return_value=False)
def test_stream_accepts_error_keywords_passed_by_streaming_handlers(_mock_pure, mock_streaming, _mock_logging):
    """assistant_handlers and hedged_handler call agent.stream(include_tool_errors=..., error_detail_level=...)
    for every agent type; AIToolsAgent (Bedrock, ReAct, non-LangGraph) must accept them (EPMCDME-14855)."""
    agent = _make_agentcore_agent()
    agent.thread_generator = MagicMock()
    agent.thread_context = {}

    stream = partial(agent.stream, include_tool_errors=True, error_detail_level=ErrorDetailLevel.FULL)
    stream()

    mock_streaming.assert_called_once()
    agent.thread_generator.close.assert_called_once()
