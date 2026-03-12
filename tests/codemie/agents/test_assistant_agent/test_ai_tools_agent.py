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

import pytest
import typing
from unittest.mock import ANY, MagicMock, Mock, patch
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from codemie.agents.assistant_agent import AIToolsAgent, TaskResult
from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.agents.callbacks.monitoring_callback import MonitoringCallback
from codemie.core.constants import ChatRole
from codemie.core.thread import ThreadedGenerator
from codemie.core.models import AssistantChatRequest, ChatMessage, HumanMessage, AIMessage
from codemie.rest_api.security.user import User


class MockTool(BaseTool):
    """Mock tool for testing."""

    name: str = "mock_tool"  # Add type annotation as required by Pydantic
    description: str = "A mock tool for testing purposes"

    def _run(self, *args: typing.Any, **kwargs: typing.Dict[str, typing.Any]) -> str:
        return "Mock tool result"


class OutputSchema(BaseModel):
    example_field: list[str]


@pytest.fixture
def mock_thread_generator():
    return MagicMock(spec=ThreadedGenerator)


@pytest.fixture
def mock_llm():
    class MockLLM:
        def __init__(self):
            self.callbacks = []

    return MockLLM()


@pytest.fixture
def mock_tools():
    return [MockTool()]


@pytest.fixture
def assistant_request():
    return AssistantChatRequest(
        conversation_id="example_conversation_id",
        system_prompt="example_system_prompt",
        history=[],
        text="Test input",
        file_names=["example_file_name"],
    )


@pytest.fixture
def mock_user() -> MagicMock:
    mock_user = MagicMock(spec=User)
    mock_user.id = "example_user_id"
    mock_user.name = "example_user_id"
    mock_user.username = "example_user_id"
    return mock_user


@pytest.fixture
def agent(mock_thread_generator: MagicMock, assistant_request: MagicMock, mock_user: MagicMock) -> AIToolsAgent:
    return AIToolsAgent(
        agent_name="TestAgent",
        description="A test agent",
        tools=[],
        request=assistant_request,
        system_prompt="Test prompt",
        request_uuid="test-uuid",
        user=mock_user,
        llm_model="test-llm-model",
        thread_generator=mock_thread_generator,
    )


@pytest.fixture
def structured_agent(
    mock_thread_generator: MagicMock, assistant_request: MagicMock, mock_user: MagicMock
) -> AIToolsAgent:
    return AIToolsAgent(
        agent_name="TestAgent",
        description="A test agent",
        tools=[],
        request=assistant_request,
        output_schema=OutputSchema,
        system_prompt="Test prompt",
        request_uuid="test-uuid",
        user=mock_user,
        llm_model="test-llm-model",
        thread_generator=mock_thread_generator,
    )


def test_init_agent(
    agent,
    mock_thread_generator: MagicMock,
    assistant_request: AssistantChatRequest,
    mock_user: MagicMock,
    mock_tools: list,
) -> None:
    agent.tools = mock_tools
    with (
        patch("codemie.agents.assistant_agent.create_tool_calling_agent", return_value=MagicMock()),
        patch.object(AIToolsAgent, "get_prompt_template"),
    ):
        agent._configure_tools([])


def test_configure_callbacks_with_stream_steps(
    agent: AIToolsAgent, mock_llm: typing.Any, mock_thread_generator: MagicMock
) -> None:
    mock_thread_generator.is_closed.return_value = False

    callbacks = agent.configure_callbacks(mock_llm)

    assert any(isinstance(callback, MonitoringCallback) for callback in callbacks), "MonitoringCallback not found"
    assert any(
        isinstance(callback, AgentStreamingCallback) for callback in callbacks
    ), "AgentStreamingCallback not found"
    assert isinstance(callbacks[1], AgentStreamingCallback), "AgentStreamingCallback is not initialized properly"
    assert callbacks[1].gen == mock_thread_generator, "AgentStreamingCallback does not have the correct generator"


def test_configure_callbacks_without_stream_steps(agent: AIToolsAgent, mock_llm: typing.Any) -> None:
    agent.stream_steps = False  # Disable streaming

    callbacks = agent.configure_callbacks(mock_llm)

    assert any(isinstance(callback, MonitoringCallback) for callback in callbacks), "MonitoringCallback not found"
    assert not any(
        isinstance(callback, AgentStreamingCallback) for callback in callbacks
    ), "AgentStreamingCallback should not be initialized"


def test_callbacks_added_to_llm(agent: AIToolsAgent, mock_llm: typing.Any) -> None:
    agent.configure_callbacks(mock_llm)

    assert len(mock_llm.callbacks) > 0, "Callbacks not added to LLM"
    assert any(
        isinstance(callback, MonitoringCallback) for callback in mock_llm.callbacks
    ), "MonitoringCallback not found in LLM callbacks"


def test_unique_callbacks(agent: AIToolsAgent, mock_llm: typing.Any) -> None:
    # Add an existing callback to LLM
    existing_callback = MonitoringCallback()
    mock_llm.callbacks.append(existing_callback)

    callbacks = agent.configure_callbacks(mock_llm)

    # Ensure no duplicate MonitoringCallback is added
    assert (
        len([cb for cb in callbacks if isinstance(cb, MonitoringCallback)]) == 1
    ), "Duplicate MonitoringCallback found"


def test_callbacks_with_no_initial_callbacks(agent: AIToolsAgent, mock_llm: typing.Any) -> None:
    mock_llm.callbacks = []  # Set no initial callbacks

    agent.configure_callbacks(mock_llm)

    assert len(mock_llm.callbacks) > 0, "Callbacks should be initialized when LLM has no initial callbacks"


@pytest.mark.parametrize(
    "input_text,history,file_names,expected_input,expected_history",
    [
        ("", [], None, ANY, []),
        ("Custom Input", [], None, "Custom Input", []),
        ("", [ChatMessage(role=ChatRole.USER, message="Hello")], None, ANY, [HumanMessage(content="Hello")]),
    ],
    ids=["empty_input_no_history", "custom_input_no_history", "empty_input_with_history"],
)
def test_get_inputs(agent, input_text, history, file_names, expected_input, expected_history):
    agent.request = Mock(history=history, file_names=file_names)
    inputs = agent._get_inputs(input_text)

    assert 'input' in inputs
    assert 'chat_history' in inputs
    assert inputs['input'] == expected_input
    assert inputs['chat_history'] == expected_history


def test_output_schema_existence(structured_agent: AIToolsAgent):
    assert issubclass(structured_agent.output_schema, BaseModel)


def test_structured_agent_creation_method(structured_agent: AIToolsAgent, monkeypatch):
    structured_agent.tools = [MagicMock()]

    # Patch only the method you want to test
    structured_agent._create_structured_tool_calling_agent = MagicMock(return_value=MagicMock())
    with patch("codemie.agents.assistant_agent.AgentExecutor"):
        monkeypatch.setattr("codemie.agents.assistant_agent.llm_service.get_react_llms", lambda: ["other-llm-model"])

        structured_agent.init_agent()

    structured_agent._create_structured_tool_calling_agent.assert_called_once()


@patch("codemie.repository.base_file_repository.FileObject.from_encoded_url")
def test_invoke_task_exception(mock_decoded_file, agent):
    mock_decoded_file.return_value = MagicMock()

    agent.is_pure_chain = Mock(return_value=True)
    agent._invoke_agent = Mock(side_effect=Exception("Test error"))
    result = agent.invoke_task("Test input")
    assert isinstance(result, TaskResult)
    assert not result.success
    assert "Test error" in result.result


@pytest.mark.parametrize(
    "history,expected",
    [
        (
            [
                ChatMessage(role=ChatRole.USER, message="Hello"),
                ChatMessage(role=ChatRole.ASSISTANT, message="Hi there"),
                ChatMessage(role=ChatRole.ASSISTANT, message="System message"),
            ],
            [HumanMessage(content="Hello"), AIMessage(content="Hi there"), AIMessage(content="System message")],
        ),
        ([], []),
        ([ChatMessage(role=ChatRole.USER, message="Test")], [HumanMessage(content="Test")]),
        (
            [
                ChatMessage(role=ChatRole.ASSISTANT, message="System message"),
                ChatMessage(role=ChatRole.USER, message="User message"),
                ChatMessage(role=ChatRole.ASSISTANT, message="Assistant message"),
            ],
            [
                AIMessage(content="System message"),
                HumanMessage(content="User message"),
                AIMessage(content="Assistant message"),
            ],
        ),
    ],
    ids=["mixed_messages", "empty_history", "single_user_message", "system_user_assistant_messages"],
)
def test_transform_history(agent, history, expected):
    transformed = agent._transform_history(history)
    assert len(transformed) == len(expected)
    for t, e in zip(transformed, expected):
        assert isinstance(t, type(e))
        assert t.content == e.content
    assert all(isinstance(msg, HumanMessage) for msg in transformed if isinstance(msg, HumanMessage))
    assert all(isinstance(msg, AIMessage) for msg in transformed if isinstance(msg, AIMessage))

    original_order = [msg.role for msg in history if msg.role in (ChatRole.USER, ChatRole.ASSISTANT)]
    transformed_order = [type(msg) for msg in transformed]
    assert len(original_order) == len(transformed_order)
    for orig, trans in zip(original_order, transformed_order):
        if orig == ChatRole.USER:
            assert trans == HumanMessage
        elif orig == ChatRole.ASSISTANT:
            assert trans == AIMessage


@pytest.mark.parametrize(
    "history,expected_length",
    [
        (
            [
                HumanMessage(content="Hello"),
                AIMessage(content=""),
                HumanMessage(content="How are you?"),
                AIMessage(content=""),
                AIMessage(content="I'm good, thanks!"),
            ],
            3,
        ),
        ([], 0),
        ([HumanMessage(content=""), AIMessage(content="")], 0),
        ([HumanMessage(content="Test")], 1),
    ],
    ids=["mixed_content_and_empty_messages", "empty_history", "all_empty_messages", "single_non_empty_message"],
)
def test_filter_history(history, expected_length):
    filtered = AIToolsAgent._filter_history(history)
    assert len(filtered) == expected_length
    assert all(msg.content for msg in filtered)
