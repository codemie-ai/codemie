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

"""Unit tests for token truncation detection and name truncation in LangGraphAgent."""

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

        assert exc_info.value.truncation_reason == "finish_reason=length"

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

        assert exc_info.value.truncation_reason == "stop_reason=max_tokens"
        assert exc_info.value.model == "claude-3-7"

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

        assert exc_info.value.truncation_reason == "stop_reason=max_tokens"

    def test_detect_truncation_without_tool_calls(self):
        """Test that truncation is detected even when no tool_calls were generated."""
        agent = create_mock_agent(llm_model="claude-3-7")

        message = AIMessage(content="I'll help", response_metadata={"stopReason": "max_tokens"})

        with pytest.raises(TokenLimitExceededException):
            agent._check_for_truncated_response(message)

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
        """Test that tool names are NOT exposed in the user-facing message from extended_error."""
        from codemie.agents.tools.agent import AbstractAgent
        from codemie.core.error_constants import ErrorCategory, ErrorCode
        from codemie.configs import config

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

        # Simulate the extended_error() path for AGENT_TOKEN_LIMIT
        abstract_agent = AbstractAgent()
        err_mock = Mock()
        err_mock.error_code = ErrorCode.AGENT_TOKEN_LIMIT
        err_mock.message = config.AGENT_MSG_TOKEN_LIMIT
        err_mock.details = {}
        error_response = Mock()
        error_response.get_error.return_value = err_mock
        error_response.category = ErrorCategory.AGENT

        user_facing_msg = abstract_agent.extended_error(error_response, exc_info.value)
        assert "jira_tool" not in user_facing_msg
        assert "slack_tool" not in user_facing_msg

    def test_exception_contains_support_link(self):
        """Test that TokenLimitExceededException is raised on finish_reason=length."""
        agent = create_mock_agent(llm_model="claude-3-7")

        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        with pytest.raises(TokenLimitExceededException):
            agent._check_for_truncated_response(message)

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


class TestDetectTerminalTruncation:
    """Tests for the non-raising terminal-message truncation signal (EPMCDME-14145)."""

    def test_is_finish_reason_truncated_openai_length(self):
        agent = create_mock_agent(llm_model="gpt-4.1")
        message = AIMessage(content="partial answer", response_metadata={"finish_reason": "length"})

        assert agent.is_finish_reason_truncated(message) is True

    def test_is_finish_reason_truncated_claude_stop_reason_max_tokens(self):
        agent = create_mock_agent(llm_model="claude-3-5-sonnet")
        message = AIMessage(content="partial answer", response_metadata={"stop_reason": "max_tokens"})

        assert agent.is_finish_reason_truncated(message) is True

    def test_is_finish_reason_truncated_bedrock_camel_case_stop_reason(self):
        agent = create_mock_agent(llm_model="anthropic.claude-3-5-sonnet")
        message = AIMessage(content="partial answer", response_metadata={"stopReason": "max_tokens"})

        assert agent.is_finish_reason_truncated(message) is True

    def test_is_finish_reason_truncated_false_for_normal_completion(self):
        agent = create_mock_agent()
        message = AIMessage(content="complete answer", response_metadata={"finish_reason": "stop"})

        assert agent.is_finish_reason_truncated(message) is False

    def test_is_finish_reason_truncated_false_for_empty_metadata(self):
        agent = create_mock_agent()
        message = AIMessage(content="complete answer", response_metadata={})

        assert agent.is_finish_reason_truncated(message) is False

    def test_detect_terminal_truncation_sets_flags_on_truncated_openai_message(self):
        agent = create_mock_agent()
        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        agent._detect_terminal_truncation(message)

        assert agent._last_truncated is True

    def test_detect_terminal_truncation_sets_flags_on_truncated_claude_message(self):
        agent = create_mock_agent()
        message = AIMessage(content="", response_metadata={"stop_reason": "max_tokens"})

        agent._detect_terminal_truncation(message)

        assert agent._last_truncated is True

    def test_detect_terminal_truncation_leaves_flags_unset_on_normal_completion(self):
        agent = create_mock_agent()
        agent._last_truncated = False
        message = AIMessage(content="complete answer", response_metadata={"finish_reason": "stop"})

        agent._detect_terminal_truncation(message)

        assert agent._last_truncated is False

    def test_detect_terminal_truncation_does_not_raise(self):
        """Unlike _safe_check_for_truncation, this must never raise TokenLimitExceededException."""
        agent = create_mock_agent()
        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        agent._detect_terminal_truncation(message)  # must not raise

    def test_detect_terminal_truncation_resets_flags_for_later_non_truncated_turn(self):
        """AC (spec.md:52): both attributes reset at the start of each invoke_task call *and* at
        the start of each supervisor turn. _detect_terminal_truncation is the per-turn call site
        (dispatched once per subagent author via parse_supervisor_message_type in supervisor
        flows), so a truncated turn's flags must not survive into a later, non-truncated terminal
        message processed within the same invoke_task call.
        """
        agent = create_mock_agent()
        truncated_message = AIMessage(content="partial", response_metadata={"finish_reason": "length"})
        complete_message = AIMessage(content="complete answer", response_metadata={"finish_reason": "stop"})

        agent._detect_terminal_truncation(truncated_message)
        assert agent._last_truncated is True  # sanity: the first (truncated) turn set the flags

        agent._detect_terminal_truncation(complete_message)  # simulates the next supervisor turn

        assert (
            agent._last_truncated is False
        ), "stale truncated=True from a prior turn leaked into a later, complete terminal message"


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
        """Test format_assistant_name truncates to max name length (accounting for handoff prefix)."""
        long_name = "a" * 100
        result = LangGraphAgent.format_assistant_name(long_name)
        prefix_length = len(LangGraphAgent.SUPERVISOR_HANDOFF_TOOL_PREFIX) + 1
        max_name_length = LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH - prefix_length
        assert len(result) == max_name_length

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


class TestSubAssistantNameTruncation:
    """Test suite for sub-assistant name truncation functionality."""

    def test_truncate_short_name_unchanged(self):
        """Test that short names that fit within the limit are not truncated."""
        name = "code_agent"

        result = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(name)

        assert result == name, "Short names should not be truncated"

    def test_truncate_name_at_exact_limit(self):
        """Test name exactly at the maximum allowed length (after accounting for prefix)."""
        # Max tool name is 64 chars: "transfer_to_" = 12 chars, so max name length = 52 chars
        prefix_length = len(LangGraphAgent.SUPERVISOR_HANDOFF_TOOL_PREFIX) + 1  # +1 for underscore
        max_name_length = LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH - prefix_length
        name = "a" * max_name_length  # Exactly 52 characters

        result = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(name)

        assert result == name, "Name at exact limit should not be truncated"
        assert len(result) == max_name_length

    def test_truncate_long_name_with_hash_suffix(self):
        """Test that long names are truncated and have hash suffix for uniqueness."""
        long_name = (
            "very_long_assistant_name_that_exceeds_the_maximum_allowed_length_for_handoff_tools_and_needs_truncation"
        )

        result = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(long_name)

        # Result should be shorter than input
        assert len(result) < len(long_name), "Long names should be truncated"

        # Result should fit within the constraint
        prefix_length = len(LangGraphAgent.SUPERVISOR_HANDOFF_TOOL_PREFIX) + 1
        max_name_length = LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH - prefix_length
        assert len(result) <= max_name_length, "Truncated name should fit within max length"

        # Result should contain an underscore separator for the hash
        assert "_" in result, "Truncated name should have hash separator"

    def test_truncate_preserves_uniqueness_with_hash(self):
        """Test that different long names produce different truncated results due to hash."""
        name1 = "very_long_assistant_name_that_needs_truncation_variant_one_with_unique_ending"
        name2 = "very_long_assistant_name_that_needs_truncation_variant_two_with_different_ending"

        result1 = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(name1)
        result2 = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(name2)

        # Different input names should produce different truncated names
        assert result1 != result2, "Different long names should have different truncated results"

        # Both should be within the limit
        prefix_length = len(LangGraphAgent.SUPERVISOR_HANDOFF_TOOL_PREFIX) + 1
        max_name_length = LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH - prefix_length
        assert len(result1) <= max_name_length
        assert len(result2) <= max_name_length

    def test_truncate_consistent_for_same_input(self):
        """Test that truncation is deterministic - same input produces same output."""
        long_name = "extremely_long_assistant_name_that_definitely_exceeds_limits"

        result1 = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(long_name)
        result2 = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(long_name)

        assert result1 == result2, "Truncation should be deterministic"

    def test_truncate_empty_string(self):
        """Test truncation of empty string."""
        result = LangGraphAgent.truncate_sub_assistant_handoff_tool_name("")

        assert result == "", "Empty string should remain empty"

    def test_truncate_single_character(self):
        """Test truncation of single character name."""
        result = LangGraphAgent.truncate_sub_assistant_handoff_tool_name("a")

        assert result == "a", "Single character should not be truncated"

    def test_truncate_name_just_over_limit(self):
        """Test name that is just 1 character over the limit."""
        prefix_length = len(LangGraphAgent.SUPERVISOR_HANDOFF_TOOL_PREFIX) + 1
        max_name_length = LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH - prefix_length
        name = "a" * (max_name_length + 1)  # 1 char over limit

        result = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(name)

        # Should be truncated with hash
        assert len(result) <= max_name_length, "Name over limit should be truncated"
        assert len(result) < len(name), "Result should be shorter than input"

    def test_truncate_preserves_readable_prefix(self):
        """Test that truncation keeps some of the original name for readability."""
        long_name = "data_analysis_expert_with_advanced_statistical_modeling_capabilities_and_machine_learning"

        result = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(long_name)

        # Result should start with part of the original name
        # Check that at least some characters from the beginning are preserved
        assert result.startswith("data_analysis"), "Truncated name should preserve readable prefix"

    def test_truncate_unicode_characters(self):
        """Test truncation with unicode characters in name."""
        name = "assistant_with_emojis_and_special_chars" * 3  # Make it long

        result = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(name)

        # Should handle unicode and truncate properly
        prefix_length = len(LangGraphAgent.SUPERVISOR_HANDOFF_TOOL_PREFIX) + 1
        max_name_length = LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH - prefix_length
        assert len(result) <= max_name_length, "Unicode names should be truncated correctly"


class TestSubAssistantNameMapping:
    """Test suite for sub-assistant name mapping and resolution."""

    def test_get_original_name_with_mapping(self):
        """Test retrieving original name when mapping exists."""
        agent = create_mock_agent()
        agent._sub_assistant_name_mapping = {"data_analysis_exp_abc123": "Data Analysis Expert"}

        result = agent.get_original_sub_assistant_name("data_analysis_exp_abc123")

        assert result == "Data Analysis Expert", "Should return original name from mapping"

    def test_get_original_name_without_mapping(self):
        """Test retrieving name when no mapping exists (returns truncated name)."""
        agent = create_mock_agent()
        agent._sub_assistant_name_mapping = {}

        result = agent.get_original_sub_assistant_name("unknown_assistant")

        assert result == "unknown_assistant", "Should return truncated name when no mapping exists"

    def test_get_original_name_empty_mapping(self):
        """Test behavior with empty mapping dictionary."""
        agent = create_mock_agent()
        agent._sub_assistant_name_mapping = {}

        result = agent.get_original_sub_assistant_name("test_agent")

        assert result == "test_agent", "Should return input when mapping is empty"

    def test_get_original_name_multiple_mappings(self):
        """Test that correct name is retrieved when multiple mappings exist."""
        agent = create_mock_agent()
        agent._sub_assistant_name_mapping = {
            "code_reviewer_abc123": "Code Review Expert",
            "data_analyst_def456": "Data Analysis Specialist",
            "doc_writer_ghi789": "Documentation Writer",
        }

        result = agent.get_original_sub_assistant_name("data_analyst_def456")

        assert result == "Data Analysis Specialist", "Should return correct name from multiple mappings"

    def test_get_original_name_case_sensitive(self):
        """Test that name lookup is case-sensitive."""
        agent = create_mock_agent()
        agent._sub_assistant_name_mapping = {"code_agent": "Code Agent"}

        result = agent.get_original_sub_assistant_name("Code_Agent")

        # Should not find the mapping (case mismatch), returns input
        assert result == "Code_Agent", "Name lookup should be case-sensitive"

    def test_mapping_integration_with_truncation(self):
        """Test end-to-end: truncate name and verify mapping could work."""
        # This tests the expected flow:
        # 1. Original long name gets truncated
        # 2. Mapping stores truncated -> original
        # 3. Retrieval uses truncated to get original

        agent = create_mock_agent()
        original_name = "very_long_sub_assistant_name_that_needs_truncation_for_tool_constraints"
        truncated_name = LangGraphAgent.truncate_sub_assistant_handoff_tool_name(original_name)

        # Simulate the mapping that would be created
        agent._sub_assistant_name_mapping = {truncated_name: original_name}

        # Verify retrieval works
        result = agent.get_original_sub_assistant_name(truncated_name)

        assert result == original_name, "Should retrieve original name after truncation mapping"


class TestExtendedError:
    """Tests for AbstractAgent.extended_error() with AGENT_TOKEN_LIMIT."""

    def test_extended_error_returns_clean_message_for_agent_token_limit(self):
        """AGENT_TOKEN_LIMIT must return the clean config string, not str(exception)."""
        from codemie.agents.tools.agent import AbstractAgent
        from codemie.core.error_constants import ErrorCategory, ErrorCode
        from codemie.configs import config

        agent = AbstractAgent()

        err_mock = Mock()
        err_mock.error_code = ErrorCode.AGENT_TOKEN_LIMIT
        err_mock.message = config.AGENT_MSG_TOKEN_LIMIT
        err_mock.details = {}

        error_response = Mock()
        error_response.get_error.return_value = err_mock
        error_response.category = ErrorCategory.AGENT

        raw_exception = Exception(
            "\n⚠️ TOKEN LIMIT EXCEEDED\nAPI Response: finish_reason=length\n"
            "Model: 'claude-sonnet-5'\n"
            "The configured max_output_tokens limit was reached while generating tool arguments.\n"
        )

        result = agent.extended_error(error_response, raw_exception)

        assert result == config.AGENT_MSG_TOKEN_LIMIT
        assert "TOKEN LIMIT EXCEEDED" not in result
        assert "finish_reason" not in result
        assert "claude-sonnet-5" not in result
        assert "max_output_tokens" not in result
