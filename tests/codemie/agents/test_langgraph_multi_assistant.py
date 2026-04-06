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

"""
This module tests the supervisor/subagent architecture and related features.
"""

from uuid import uuid4

import pytest
from unittest.mock import ANY, MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.callbacks import BaseCallbackHandler

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.core.models import AssistantChatRequest
from codemie.chains.base import ThoughtOutputFormat
from codemie.core.thread import ThreadedGenerator
from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback


class TestLangGraphMultiAssistant:
    """Test cases for multi-assistant supervisor functionality."""

    @pytest.fixture
    def mock_user(self):
        user = MagicMock()
        user.id = "test_user_id"
        user.name = "Test User"
        user.username = "testuser"
        return user

    @pytest.fixture
    def mock_request(self):
        request = MagicMock(spec=AssistantChatRequest)
        request.conversation_id = "test_conv_id"
        request.text = "Test request"
        request.history = []
        request.system_prompt = None
        request.file_names = None
        request.metadata = {}
        return request

    @pytest.fixture
    def mock_regular_tool(self):
        """Create a mock regular tool without agent attribute."""
        tool = MagicMock(spec=BaseTool)
        tool.name = "regular_tool"
        tool.description = "A regular tool"
        tool.metadata = {}
        return tool

    @pytest.fixture
    def mock_agent_tool(self):
        """Create a mock agent tool with _agent.agent_executor attribute."""
        tool = MagicMock(spec=BaseTool)
        tool.name = "agent_tool"
        tool.description = "An agent tool"
        tool.metadata = {}

        # Mock the agent executor structure
        mock_agent = MagicMock()
        mock_agent_executor = MagicMock()
        mock_agent.agent_executor = mock_agent_executor
        tool._agent = mock_agent

        return tool

    @pytest.fixture
    def agent_config_with_subagents(self, mock_user, mock_request, mock_regular_tool, mock_agent_tool):
        """Configuration for agent with both regular tools and subagents."""
        assistant = MagicMock()
        assistant.project = "test"
        # Extract the agent_executor from mock_agent_tool for subagents
        mock_subagent = mock_agent_tool._agent.agent_executor
        return {
            "agent_name": "SupervisorAgent",
            "description": "Supervisor agent with subagents",
            "tools": [mock_regular_tool],  # Only regular tools
            "subagents": [mock_subagent],  # Subagents passed separately
            "request": mock_request,
            "system_prompt": "You are a supervisor assistant managing subagents.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-4",
            "assistant": assistant,
        }

    @pytest.fixture
    def supervisor_agent(self, agent_config_with_subagents):
        """Create a supervisor agent with mocked dependencies."""
        with patch("codemie.agents.langgraph_agent.create_supervisor") as mock_create_supervisor:
            mock_supervisor = MagicMock()
            mock_supervisor.compile.return_value = MagicMock()
            mock_create_supervisor.return_value = mock_supervisor

            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**agent_config_with_subagents)
                yield agent

    def test_supervisor_agent_initialization(self, supervisor_agent, mock_regular_tool, mock_agent_tool):
        """Test that supervisor agent is initialized correctly with subagents."""
        assert supervisor_agent.agent_name == "SupervisorAgent"
        assert len(supervisor_agent.tools) == 1  # Only regular tools
        assert len(supervisor_agent.subagents) == 1  # Only subagents
        assert supervisor_agent.tools[0] == mock_regular_tool

    def test_supervisor_agent_uses_create_supervisor(self, mock_user, mock_request, mock_regular_tool):
        """Test that supervisor agent uses create_supervisor instead of create_smart_react_agent."""
        assistant = MagicMock()
        assistant.project = "test"

        # Create a config with subagents
        mock_subagent = MagicMock()
        config = {
            "agent_name": "SupervisorAgent",
            "description": "Supervisor agent with subagents",
            "tools": [mock_regular_tool],
            "subagents": [mock_subagent],  # Subagents passed directly
            "request": mock_request,
            "system_prompt": "You are a supervisor assistant managing subagents.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-4",
            "assistant": assistant,
        }

        with patch("codemie.agents.langgraph_agent.create_supervisor") as mock_create_supervisor:
            with patch("codemie.agents.langgraph_agent.create_smart_react_agent") as mock_create_smart_react:
                mock_supervisor = MagicMock()
                mock_supervisor.compile.return_value = MagicMock()
                mock_create_supervisor.return_value = mock_supervisor

                with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                    LangGraphAgent(**config)

                # Should use create_supervisor, not create_smart_react_agent
                mock_create_supervisor.assert_called_once()
                mock_create_smart_react.assert_not_called()

    def test_regular_agent_uses_create_react_agent(self, mock_user, mock_request, mock_regular_tool):
        """Test that regular agent (no subagents) uses create_smart_react_agent."""
        assistant = MagicMock()
        assistant.project = "test"
        config = {
            "agent_name": "RegularAgent",
            "description": "Regular agent without subagents",
            "tools": [mock_regular_tool],  # Only regular tools
            "request": mock_request,
            "system_prompt": "You are a regular assistant.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-4",
            "assistant": assistant,
        }

        with patch("codemie.agents.langgraph_agent.create_supervisor") as mock_create_supervisor:
            with patch("codemie.agents.langgraph_agent.create_smart_react_agent") as mock_create_smart_react:
                mock_create_smart_react.return_value = MagicMock()

                with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                    LangGraphAgent(**config)

                # Should use create_smart_react_agent, not create_supervisor
                mock_create_smart_react.assert_called_once()
                mock_create_supervisor.assert_not_called()

    def test_check_is_handoff_tool(self):
        """Test handoff tool detection."""
        assert LangGraphAgent._check_is_handoff_tool("transfer_to_analyst") is True
        assert LangGraphAgent._check_is_handoff_tool("transfer_to_researcher") is True
        assert LangGraphAgent._check_is_handoff_tool("regular_tool") is False
        assert LangGraphAgent._check_is_handoff_tool("transfer") is False

    def test_extract_agent_name_from_tool(self):
        """Test agent name extraction from handoff tool."""
        assert LangGraphAgent._extract_agent_name_from_tool("transfer_to_analyst") == "analyst"
        assert LangGraphAgent._extract_agent_name_from_tool("transfer_to_researcher") == "researcher"
        assert LangGraphAgent._extract_agent_name_from_tool("transfer_to_data_scientist") == "data_scientist"

    def test_supervisor_callbacks_initialization(self, supervisor_agent):
        """Test that supervisor callbacks are initialized correctly."""
        assert hasattr(supervisor_agent, 'supervisor_callbacks')
        assert isinstance(supervisor_agent.supervisor_callbacks, list)

    def test_supervisor_callbacks_with_thread_generator(self, agent_config_with_subagents):
        """Test supervisor callbacks include streaming callback when thread_generator is provided."""
        agent_config_with_subagents['thread_generator'] = ThreadedGenerator()
        agent_config_with_subagents['stream_steps'] = True

        with patch("codemie.agents.langgraph_agent.create_supervisor") as mock_create_supervisor:
            mock_supervisor = MagicMock()
            mock_supervisor.compile.return_value = MagicMock()
            mock_create_supervisor.return_value = mock_supervisor

            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**agent_config_with_subagents)

        # Should have AgentStreamingCallback in supervisor_callbacks
        assert len(agent.supervisor_callbacks) == 1
        assert isinstance(agent.supervisor_callbacks[0], AgentStreamingCallback)

    def test_stream_graph_with_subagents(self, supervisor_agent):
        """Test stream_graph method with subagents enabled."""
        mock_stream = [
            ("supervisor", "messages", (AIMessage(content="chunk1"), None)),
            ("supervisor", "updates", {"supervisor": {"messages": [AIMessage(content="chunk2")]}}),
        ]

        supervisor_agent.agent_executor = MagicMock()
        supervisor_agent.agent_executor.stream.return_value = iter(mock_stream)
        supervisor_agent.process_chunk = MagicMock()
        supervisor_agent._on_chain_end = MagicMock()
        supervisor_agent._get_last_ai_message_content = MagicMock(return_value="final_result")

        result = supervisor_agent._stream_graph({"input": "test"})

        # Should call stream with subgraphs=True
        supervisor_agent.agent_executor.stream.assert_called_once()
        call_args = supervisor_agent.agent_executor.stream.call_args
        assert call_args[1]['subgraphs'] is True

        assert result == "final_result"

    def test_stream_graph_without_subagents(self, mock_user, mock_request, mock_regular_tool):
        """Test stream_graph method without subagents."""
        assistant = MagicMock()
        assistant.project = "test"
        config = {
            "agent_name": "RegularAgent",
            "description": "Regular agent",
            "tools": [mock_regular_tool],
            "request": mock_request,
            "system_prompt": "You are a regular assistant.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-4",
            "assistant": assistant,
        }

        with patch("codemie.agents.langgraph_agent.create_smart_react_agent") as mock_create_smart_react:
            mock_create_smart_react.return_value = MagicMock()
            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**config)

        mock_stream = [
            ("messages", (AIMessage(content="chunk1"), None)),
            ("updates", {"agent": {"messages": [AIMessage(content="chunk2")]}}),
        ]

        agent.agent_executor = MagicMock()
        agent.agent_executor.stream.return_value = iter(mock_stream)
        agent.process_chunk = MagicMock()
        agent._on_chain_end = MagicMock()
        agent._get_last_ai_message_content = MagicMock(return_value="final_result")

        agent._stream_graph({"input": "test"})

        # Should call stream with subgraphs=False
        call_args = agent.agent_executor.stream.call_args
        assert call_args[1]['subgraphs'] is False

    def test_process_chunk_supervisor(self, supervisor_agent):
        """Test that supervisor agent uses supervisor chunk processing."""
        chunk = ("supervisor", "messages", "test_value")
        chunks_collector = []

        supervisor_agent._process_chunk_for_supervisor = MagicMock()
        supervisor_agent._process_chunk_for_agent = MagicMock()

        supervisor_agent.process_chunk(chunk, chunks_collector)

        supervisor_agent._process_chunk_for_supervisor.assert_called_once_with(chunk, chunks_collector)
        supervisor_agent._process_chunk_for_agent.assert_not_called()

    def test_parse_supervisor_update_type_handoff(self, supervisor_agent):
        """Test supervisor update parsing for handoff tools - deferred via _pending_handoffs."""
        ai_message = AIMessage(content="Handing off to analyst")
        ai_message.tool_calls = [{"name": "transfer_to_analyst", "args": {"task": "analyze data"}}]

        value = {"supervisor": {"messages": [ai_message]}}
        agent_name = "analyst"

        supervisor_agent._on_supervisor_handoff = MagicMock()
        supervisor_agent.set_thread_context = MagicMock()

        supervisor_agent._LangGraphAgent__parse_supervisor_update_type(value, author=agent_name)

        # With deferred handoff logic, _on_supervisor_handoff is NOT called immediately;
        # the pending handoff is stored in _pending_handoffs to be emitted on the subagent's first chunk.
        supervisor_agent._on_supervisor_handoff.assert_not_called()
        supervisor_agent.set_thread_context.assert_not_called()
        assert agent_name in supervisor_agent._pending_handoffs
        _, stored_author = supervisor_agent._pending_handoffs[agent_name]
        assert stored_author == agent_name

    def test_parse_supervisor_update_type_regular_tool(self, supervisor_agent):
        """Test supervisor update parsing for regular tools."""
        ai_message = AIMessage(content="Using regular tool")
        ai_message.tool_calls = [{"name": "search_tool", "args": {"query": "test"}}]

        value = {"supervisor": {"messages": [ai_message]}}

        supervisor_agent.is_finish_reason_tool_calls = MagicMock(return_value=True)
        supervisor_agent._get_tool_call_args = MagicMock(return_value=("search_tool", "{'query': 'test'}"))
        supervisor_agent._on_tool_start = MagicMock()
        supervisor_agent._on_supervisor_handoff = MagicMock()

        supervisor_agent._LangGraphAgent__parse_supervisor_update_type(value)

        # Should call regular tool start (with run_id), not handoff
        supervisor_agent._on_tool_start.assert_called_once()
        call_args = supervisor_agent._on_tool_start.call_args
        assert call_args.args == ("search_tool", "{'query': 'test'}")
        assert call_args.kwargs.get("run_id") is not None
        supervisor_agent._on_supervisor_handoff.assert_not_called()

    def test_parse_supervisor_update_type_tool_message(self, supervisor_agent):
        """Test supervisor update parsing for tool messages."""
        tool_message = ToolMessage(name="search_tool", content="Search results", tool_call_id="call-123")

        value = {"supervisor": {"messages": [tool_message]}}

        supervisor_agent._parse_tool_message = MagicMock()

        supervisor_agent._LangGraphAgent__parse_supervisor_update_type(value)

        supervisor_agent._parse_tool_message.assert_called_once_with(tool_message, author=None)

    def test_on_supervisor_handoff_callback(self, supervisor_agent):
        """Test supervisor handoff callback execution."""
        mock_callback = MagicMock(spec=BaseCallbackHandler)
        supervisor_agent.supervisor_callbacks = [mock_callback]

        destination = "agent_analyst"
        run_id = uuid4()
        supervisor_agent._on_supervisor_handoff(destination, run_id)

        # Should call on_tool_start on supervisor callbacks
        mock_callback.on_tool_start.assert_called_once()
        call_args = mock_callback.on_tool_start.call_args

        assert call_args[0][0]["name"] == destination
        assert call_args[1]["metadata"]["output_format"] == ThoughtOutputFormat.MARKDOWN.value

    def test_on_subassistant_back_callback(self, supervisor_agent):
        """Test subassistant back callback execution."""
        mock_callback = MagicMock(spec=BaseCallbackHandler)
        supervisor_agent.supervisor_callbacks = [mock_callback]

        output = "Subassistant completed the task"
        supervisor_agent._on_subassistant_back(output)

        # Should call on_tool_end on supervisor callbacks (run_id is generated internally)
        mock_callback.on_tool_end.assert_called_once()
        assert mock_callback.on_tool_end.call_args[0][0] == output

    def test_supervisor_callback_error_handling(self, supervisor_agent):
        """Test error handling in supervisor callbacks."""
        mock_callback = MagicMock(spec=BaseCallbackHandler)
        mock_callback.on_tool_start.side_effect = Exception("Callback error")
        supervisor_agent.supervisor_callbacks = [mock_callback]

        run_id = uuid4()
        with patch("codemie.agents.langgraph_agent.logger") as mock_logger:
            supervisor_agent._on_supervisor_handoff("agent_test", run_id)

            # Should log the error
            mock_logger.error.assert_called_once()

    def test_get_last_ai_message_content_supervisor(self, supervisor_agent):
        """Test getting last AI message content for supervisor agent."""
        ai_message = AIMessage(content="Supervisor response")
        chunk = ("updates", {"supervisor": {"messages": [ai_message]}})

        content = supervisor_agent._get_last_ai_message_content(chunk)
        assert content == "Supervisor response"

    def test_get_last_ai_message_content_regular_agent(self, mock_user, mock_request, mock_regular_tool):
        """Test getting last AI message content for regular agent."""
        assistant = MagicMock()
        assistant.project = "test"
        config = {
            "agent_name": "RegularAgent",
            "description": "Regular agent",
            "tools": [mock_regular_tool],
            "request": mock_request,
            "system_prompt": "You are a regular assistant.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-4",
            "assistant": assistant,
        }

        with patch("codemie.agents.langgraph_agent.create_smart_react_agent") as mock_create_smart_react:
            mock_create_smart_react.return_value = MagicMock()
            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**config)

        ai_message = AIMessage(content="Regular agent response")
        chunk = ("updates", {"agent": {"messages": [ai_message]}})

        content = agent._get_last_ai_message_content(chunk)
        assert content == "Regular agent response"

    def test_parse_supervisor_message_type_valid_ai_message(self, supervisor_agent):
        """Test supervisor message parsing for valid AI messages."""
        ai_message = AIMessage(content="Processing request")
        ai_message.response_metadata = {}  # No handoff back flag

        value = (ai_message, {})
        chunks_collector = []

        supervisor_agent.is_valid_ai_message = MagicMock(return_value=True)
        supervisor_agent._process_agent_streaming = MagicMock()

        supervisor_agent._LangGraphAgent__parse_supervisor_message_type(value, chunks_collector)

        supervisor_agent._process_agent_streaming.assert_called_once_with(
            "Processing request", chunks_collector, ai_message.id, author=None
        )

    def test_parse_supervisor_message_type_handoff_back_ignored(self, supervisor_agent):
        """Test that handoff back messages are ignored in message parsing."""
        ai_message = AIMessage(content="Handoff back")
        ai_message.response_metadata = {"__is_handoff_back": True}

        value = (ai_message, {})
        chunks_collector = []

        supervisor_agent.is_valid_ai_message = MagicMock(return_value=True)
        supervisor_agent._process_agent_streaming = MagicMock()

        supervisor_agent._LangGraphAgent__parse_supervisor_message_type(value, chunks_collector)

        # Should not process streaming for handoff back messages
        supervisor_agent._process_agent_streaming.assert_not_called()

    def test_set_subagent_execution(self, supervisor_agent):
        """Test setting subagent execution context."""
        mock_callback = MagicMock(spec=AgentStreamingCallback)
        mock_thought = MagicMock()
        mock_callback.thoughts_storage.create_thought.return_value = mock_thought

        supervisor_agent.callbacks = [mock_callback]

        supervisor_agent.set_subagent_execution()

        mock_callback.thoughts_storage.create_thought.assert_called_once_with(
            run_id=ANY, tool_name=AgentStreamingCallback.GENERIC_TOOL_NAME
        )
        assert mock_thought.author_type == "Agent"

    def test_supervisor_state_initialization(self, supervisor_agent):
        """Test that supervisor state is initialized."""
        assert hasattr(supervisor_agent, '_supervisor_state')
        assert supervisor_agent._supervisor_state is None

    def test_is_finish_reason_tool_calls_with_tool_calls(self, supervisor_agent):
        """Test tool calls detection when tool_calls attribute exists."""
        message = AIMessage(content="Using tool")
        message.tool_calls = [{"name": "test_tool", "args": {}}]

        result = supervisor_agent.is_finish_reason_tool_calls(message)
        assert result is True

    def test_is_finish_reason_tool_calls_without_tool_calls(self, supervisor_agent):
        """Test tool calls detection when tool_calls attribute doesn't exist."""
        message = AIMessage(content="No tools")
        # Don't set tool_calls attribute

        result = supervisor_agent.is_finish_reason_tool_calls(message)
        assert result is False

    def test_is_finish_reason_tool_calls_empty_tool_calls(self, supervisor_agent):
        """Test tool calls detection with empty tool_calls list."""
        message = AIMessage(content="No tools")
        message.tool_calls = []

        result = supervisor_agent.is_finish_reason_tool_calls(message)
        assert result is False
