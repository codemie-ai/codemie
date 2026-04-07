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

"""Integration tests for AIToolsAgent error flow during streaming."""

import json
from unittest.mock import MagicMock, patch

import pytest

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.security.user import User
from codemie.core.thread import ThreadedGenerator


@pytest.fixture
def test_setup():
    """Create agent with mocks for error flow tests."""
    mock_request = MagicMock(spec=AssistantChatRequest)
    mock_request.text = "Test request"
    mock_request.file_name = None
    mock_request.file_names = None
    mock_request.conversation_id = "test-conversation-id"
    mock_request.history = []
    mock_request.system_prompt = None
    mock_request.metadata = {}
    mock_request.version = None

    mock_user = MagicMock(spec=User)
    mock_user.id = "test-user-id"
    mock_user.name = "Test User"
    mock_user.username = "Test User"

    mock_thread_generator = MagicMock(spec=ThreadedGenerator)
    mock_thread_generator.is_closed.return_value = False

    with patch.object(AIToolsAgent, "init_agent", return_value=MagicMock()):
        with patch("codemie.agents.assistant_agent.get_llm_by_credentials", return_value=MagicMock()):
            agent = AIToolsAgent(
                agent_name="test_agent",
                description="Test agent",
                tools=[],
                request=mock_request,
                system_prompt="Test system prompt",
                request_uuid="test-uuid",
                user=mock_user,
                llm_model="gpt-3.5-turbo",
                thread_generator=mock_thread_generator,
            )

    return {
        "agent": agent,
        "mock_request": mock_request,
        "mock_user": mock_user,
        "mock_thread_generator": mock_thread_generator,
    }


@patch("codemie.agents.assistant_agent.logger")
@patch("codemie.agents.assistant_agent.set_logging_info")
@patch("codemie.enterprise.langfuse.get_langfuse_callback_handler", return_value=None)
def test_stream_calls_send_error_response_when_agent_streaming_raises(
    mock_langfuse, mock_set_logging, mock_logger, test_setup
):
    """When _agent_streaming raises, stream() calls send_error_response with correct args."""
    agent = test_setup["agent"]
    mock_thread_generator = test_setup["mock_thread_generator"]
    exc = TimeoutError("Request timed out")

    with patch.object(agent, "_agent_streaming", side_effect=exc):
        with patch.object(agent, "send_error_response") as mock_send_error:
            agent.stream()

            mock_send_error.assert_called_once()
            call_args = mock_send_error.call_args[0]
            assert call_args[0] is mock_thread_generator
            assert call_args[1] is agent.thread_context
            assert call_args[2] is exc
            assert isinstance(call_args[3], float)
            assert call_args[4] == []


@patch("codemie.agents.assistant_agent.logger")
@patch("codemie.agents.assistant_agent.set_logging_info")
@patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
@patch("codemie.agents.tools.agent.config")
@patch("codemie.enterprise.langfuse.get_langfuse_callback_handler", return_value=None)
def test_stream_error_sends_correct_payload_to_generator(
    mock_langfuse, mock_config, mock_send_log_metric, mock_set_logging, mock_logger, test_setup
):
    """When _agent_streaming raises, generator receives error payload with generated and execution_error."""
    mock_config.HIDE_AGENT_STREAMING_EXCEPTIONS = True
    agent = test_setup["agent"]
    mock_thread_generator = test_setup["mock_thread_generator"]
    exc = TimeoutError("Request timed out")

    with patch.object(agent, "_agent_streaming", side_effect=exc):
        agent.stream()

    mock_thread_generator.send.assert_called()
    call_arg = mock_thread_generator.send.call_args[0][0]
    payload = json.loads(call_arg)

    assert payload.get("last") is True
    assert payload.get("execution_error") == "agent_timeout"
    assert "generated" in payload
    assert payload.get("error_details") is None
    mock_thread_generator.close.assert_called_once()
