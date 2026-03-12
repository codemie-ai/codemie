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

"""Unit tests for token truncation detection in LangGraphAgent."""

import pytest
from langchain_core.messages import AIMessage
from unittest.mock import Mock

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.core.exceptions import TokenLimitExceededException


def create_mock_agent(llm_model="gpt-4"):
    """Create a minimal LangGraphAgent instance for testing."""
    mock_request = Mock()
    mock_request.conversation_id = "test-conv-123"
    mock_request.history = []
    mock_request.file_names = []
    mock_request.text = "test input"
    mock_request.system_prompt = None
    mock_request.metadata = {}

    mock_user = Mock()
    mock_user.id = "user-123"
    mock_user.username = "test@example.com"
    mock_user.name = "Test User"

    mock_assistant = Mock()
    mock_assistant.project = "test-project"
    mock_assistant.version = "1.0"

    # Mock the initialization to avoid actual LLM setup
    agent = object.__new__(LangGraphAgent)
    agent.llm_model = llm_model
    agent.request = mock_request
    agent.user = mock_user
    agent.assistant = mock_assistant
    agent.agent_name = "test_agent"
    agent.request_uuid = "test-uuid-123"
    agent.callbacks = []
    agent.tools = []

    return agent


class TestTruncationDetection:
    """Tests for _check_for_truncated_response method."""

    def test_detect_truncation_openai_format_finish_reason_length(self):
        """Test detection with OpenAI format: finish_reason='length'."""
        agent = create_mock_agent(llm_model="gpt-4.1")

        message = AIMessage(
            content="",
            tool_calls=[{"name": "test_tool", "args": {"param": "value"}, "id": "call_123"}],
            response_metadata={"finish_reason": "length"},
        )

        with pytest.raises(TokenLimitExceededException) as exc_info:
            agent._check_for_truncated_response(message)

        assert "TOKEN LIMIT EXCEEDED" in str(exc_info.value)
        assert "finish_reason=length" in str(exc_info.value)
        assert "gpt-4.1" in str(exc_info.value)

    def test_detect_truncation_claude_stop_reason_max_tokens(self):
        """Test detection with Claude format: stop_reason='max_tokens'."""
        agent = create_mock_agent(llm_model="claude-3-7")

        message = AIMessage(
            content="",
            tool_calls=[{"name": "jira_tool", "args": {"method": "GET"}, "id": "call_456"}],
            response_metadata={"stop_reason": "max_tokens", "usage": {"output_tokens": 50}},
        )

        with pytest.raises(TokenLimitExceededException) as exc_info:
            agent._check_for_truncated_response(message)

        assert "stop_reason=max_tokens" in str(exc_info.value)
        assert "claude-3-7" in str(exc_info.value)

    def test_detect_truncation_bedrock_camelcase_stop_reason(self):
        """Test detection with Bedrock format: stopReason (camelCase)."""
        agent = create_mock_agent(llm_model="claude-3-7")

        message = AIMessage(
            content="Partial",
            response_metadata={
                "stopReason": "max_tokens",  # camelCase!
            },
        )

        with pytest.raises(TokenLimitExceededException) as exc_info:
            agent._check_for_truncated_response(message)

        assert "max_tokens" in str(exc_info.value)

    def test_detect_truncation_without_tool_calls(self):
        """Test detection when truncated before tool_calls generated."""
        agent = create_mock_agent(llm_model="claude-3-7")

        message = AIMessage(content="I'll help", response_metadata={"stopReason": "max_tokens"})

        with pytest.raises(TokenLimitExceededException) as exc_info:
            agent._check_for_truncated_response(message)

        assert "before tool arguments could be generated" in str(exc_info.value)

    def test_no_exception_for_normal_completion(self):
        """Test no exception for finish_reason='stop'."""
        agent = create_mock_agent(llm_model="gpt-4.1")

        message = AIMessage(
            content="Complete response",
            tool_calls=[{"name": "tool", "args": {"a": "b"}, "id": "c123"}],
            response_metadata={"finish_reason": "stop"},
        )

        # Should not raise
        agent._check_for_truncated_response(message)

    def test_no_exception_when_no_metadata(self):
        """Test no exception when response_metadata is None."""
        agent = create_mock_agent(llm_model="gpt-4.1")

        message = AIMessage(content="Response")

        # Should not raise
        agent._check_for_truncated_response(message)

    def test_no_exception_for_end_turn(self):
        """Test no exception for Claude's normal completion: stop_reason='end_turn'."""
        agent = create_mock_agent(llm_model="claude-3-7")

        message = AIMessage(content="Complete", response_metadata={"stop_reason": "end_turn"})

        # Should not raise
        agent._check_for_truncated_response(message)

    def test_exception_includes_tool_names(self):
        """Test that exception message includes tool names."""
        agent = create_mock_agent(llm_model="gpt-4.1")

        message = AIMessage(
            content="",
            tool_calls=[
                {"name": "jira_tool", "args": {"method": "GET"}, "id": "1"},
                {"name": "slack_tool", "args": {"channel": "test"}, "id": "2"},
            ],
            response_metadata={"finish_reason": "length"},
        )

        with pytest.raises(TokenLimitExceededException) as exc_info:
            agent._check_for_truncated_response(message)

        error_msg = str(exc_info.value)
        assert "jira_tool" in error_msg
        assert "slack_tool" in error_msg

    def test_exception_contains_support_link(self):
        """Test that exception message contains support link."""
        agent = create_mock_agent(llm_model="claude-3-7")

        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        with pytest.raises(TokenLimitExceededException) as exc_info:
            agent._check_for_truncated_response(message)

        assert "https://epa.ms/codemie-support" in str(exc_info.value)

    def test_exception_has_structured_attributes(self):
        """Test that exception has accessible attributes."""
        agent = create_mock_agent(llm_model="claude-3-7")

        message = AIMessage(content="", response_metadata={"stopReason": "max_tokens", "usage": {"output_tokens": 40}})

        try:
            agent._check_for_truncated_response(message)
            pytest.fail("Expected TokenLimitExceededException")
        except TokenLimitExceededException as e:
            assert e.model == "claude-3-7"
            assert e.truncation_reason == "stop_reason=max_tokens"


class TestTruncationHelperMethods:
    """Test helper methods used in truncation detection."""

    def test_get_truncation_indicator_openai_length(self):
        """Test _get_truncation_indicator with finish_reason='length'."""
        agent = create_mock_agent()
        response_metadata = {"finish_reason": "length"}

        result = agent._get_truncation_indicator(response_metadata)

        assert result == "finish_reason=length"

    def test_get_truncation_indicator_openai_max_tokens(self):
        """Test _get_truncation_indicator with finish_reason='max_tokens'."""
        agent = create_mock_agent()
        response_metadata = {"finish_reason": "max_tokens"}

        result = agent._get_truncation_indicator(response_metadata)

        assert result == "finish_reason=max_tokens"

    def test_get_truncation_indicator_claude_stop_reason(self):
        """Test _get_truncation_indicator with stop_reason='max_tokens'."""
        agent = create_mock_agent()
        response_metadata = {"stop_reason": "max_tokens"}

        result = agent._get_truncation_indicator(response_metadata)

        assert result == "stop_reason=max_tokens"

    def test_get_truncation_indicator_bedrock_camelcase(self):
        """Test _get_truncation_indicator with stopReason (camelCase)."""
        agent = create_mock_agent()
        response_metadata = {"stopReason": "max_tokens"}

        result = agent._get_truncation_indicator(response_metadata)

        assert result == "stop_reason=max_tokens"

    def test_get_truncation_indicator_no_truncation(self):
        """Test _get_truncation_indicator with normal completion."""
        agent = create_mock_agent()
        response_metadata = {"finish_reason": "stop"}

        result = agent._get_truncation_indicator(response_metadata)

        assert result is None

    def test_log_incomplete_tool_calls_with_tools(self):
        """Test _log_incomplete_tool_calls with tool_calls present."""
        agent = create_mock_agent()
        message = AIMessage(
            content="",
            tool_calls=[
                {"name": "search_tool", "args": {"query": "test"}, "id": "1"},
                {"name": "write_tool", "args": {"file": "test.py"}, "id": "2"},
            ],
        )

        result = agent._log_incomplete_tool_calls(message)

        assert "while generating" in result
        assert "search_tool" in result
        assert "write_tool" in result

    def test_log_incomplete_tool_calls_without_tools(self):
        """Test _log_incomplete_tool_calls when no tool_calls."""
        agent = create_mock_agent()
        message = AIMessage(content="Partial response")

        result = agent._log_incomplete_tool_calls(message)

        assert result == "before tool arguments could be generated"

    def test_safe_check_for_truncation_raises_token_exception(self):
        """Test _safe_check_for_truncation re-raises TokenLimitExceededException."""
        agent = create_mock_agent()
        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        with pytest.raises(TokenLimitExceededException):
            agent._safe_check_for_truncation(message)

    def test_safe_check_for_truncation_no_exception_on_normal(self):
        """Test _safe_check_for_truncation doesn't raise on normal completion."""
        agent = create_mock_agent()
        message = AIMessage(content="Complete response", response_metadata={"finish_reason": "stop"})

        # Should not raise
        agent._safe_check_for_truncation(message)


class TestStaticMethods:
    """Test static helper methods in LangGraphAgent."""

    def test_format_assistant_name_removes_spaces(self):
        """Test format_assistant_name replaces spaces with underscores."""
        result = LangGraphAgent.format_assistant_name("My Test Agent")
        assert result == "my_test_agent"

    def test_format_assistant_name_removes_special_chars(self):
        """Test format_assistant_name replaces special characters with underscores."""
        result = LangGraphAgent.format_assistant_name("Test<Agent>Name|")
        assert result == "test_agent_name_"

    def test_format_assistant_name_truncates_long_names(self):
        """Test format_assistant_name truncates to max length."""
        long_name = "a" * 100
        result = LangGraphAgent.format_assistant_name(long_name)
        assert len(result) == LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH

    def test_check_is_handoff_tool_true(self):
        """Test _check_is_handoff_tool identifies handoff tools."""
        result = LangGraphAgent._check_is_handoff_tool("transfer_to_agent1")
        assert result is True

    def test_check_is_handoff_tool_false(self):
        """Test _check_is_handoff_tool rejects non-handoff tools."""
        result = LangGraphAgent._check_is_handoff_tool("search_tool")
        assert result is False

    def test_extract_agent_name_from_tool(self):
        """Test _extract_agent_name_from_tool extracts agent name."""
        result = LangGraphAgent._extract_agent_name_from_tool("transfer_to_code_agent")
        assert result == "code_agent"

    def test_filter_history_removes_empty_messages(self):
        """Test _filter_history removes messages with empty content."""
        history = [AIMessage(content="Hello"), AIMessage(content=""), AIMessage(content="World")]

        result = LangGraphAgent._filter_history(history)

        assert len(result) == 2
        assert result[0].content == "Hello"
        assert result[1].content == "World"
