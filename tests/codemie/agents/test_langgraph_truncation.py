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

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from opentelemetry import context as otel_context
from unittest.mock import Mock

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.supervisor.pre_model_hooks import _build_truncation_abort_pre_model_hook
from codemie.core.exceptions import (
    MaxConsecutiveToolErrorsExceededException,
    StreamAbortedException,
    TokenLimitExceededException,
)


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
    mock_assistant.is_global = False

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
    agent.thread_generator = Mock(
        is_closed=Mock(return_value=False),
        is_consumer_aborted=Mock(return_value=False),
        cancellation_reason=None,
    )

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


class TestTruncationFlagsFailedThought:
    """The thought of the truncated LLM call is flagged before the exception is raised."""

    def test_flags_thought_of_truncated_message(self):
        agent = create_mock_agent()
        agent._callback_bridge = Mock()
        message = AIMessage(content="", id="run-1", response_metadata={"finish_reason": "length"})

        with pytest.raises(TokenLimitExceededException):
            agent._safe_check_for_truncation(message, author="sub")

        agent._callback_bridge.on_thought_failed.assert_called_once_with("run-1", author="sub")

    def test_does_not_flag_when_message_has_no_id(self):
        agent = create_mock_agent()
        agent._callback_bridge = Mock()
        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        with pytest.raises(TokenLimitExceededException):
            agent._safe_check_for_truncation(message)

        agent._callback_bridge.on_thought_failed.assert_not_called()

    def test_does_not_flag_complete_response(self):
        agent = create_mock_agent()
        agent._callback_bridge = Mock()
        message = AIMessage(content="ok", id="run-2", response_metadata={"finish_reason": "stop"})

        agent._safe_check_for_truncation(message)

        agent._callback_bridge.on_thought_failed.assert_not_called()


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


class TestCheckTruncationInChunk:
    """Tests for _check_truncation_in_chunk (A.1: detect truncation inside _stream_graph's loop)."""

    def test_check_truncation_in_chunk_keeps_text_only_truncated_answer(self):
        """A text-only answer cut at the limit is kept, not failed (EPMCDME-15787 rule)."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = None
        message = AIMessage(content="partial answer, cut off", response_metadata={"finish_reason": "length"})
        chunk = ("updates", {"agent": {"messages": [message]}})

        agent._check_truncation_in_chunk(chunk)  # must not raise

    def test_check_truncation_in_chunk_raises_for_invalid_tool_calls(self):
        """Tool-call arguments cut mid-JSON land in invalid_tool_calls and must still fail the run."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = None
        message = AIMessage(
            content="",
            invalid_tool_calls=[{"name": "search", "args": '{"q": "cut', "id": "call_1", "error": "bad json"}],
            response_metadata={"finish_reason": "length"},
        )
        chunk = ("updates", {"agent": {"messages": [message]}})

        with pytest.raises(TokenLimitExceededException):
            agent._check_truncation_in_chunk(chunk)

    def test_check_truncation_in_chunk_raises_for_truncated_tool_call(self):
        """A max_tokens cutoff on a message with tool calls must raise (A.1)."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = None
        message = AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {}, "id": "call_1"}],
            response_metadata={"finish_reason": "length"},
        )
        chunk = ("updates", {"agent": {"messages": [message]}})

        with pytest.raises(TokenLimitExceededException):
            agent._check_truncation_in_chunk(chunk)

    def test_check_truncation_in_chunk_noop_for_non_updates_chunk(self):
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = None
        agent._check_truncation_in_chunk(("messages", ("token", {})))  # must not raise

    def test_check_truncation_in_chunk_raises_for_supervisor_node_with_subagents(self):
        """CR-007: a truncated AIMessage under the "supervisor" key of a 3-tuple subgraph
        chunk must still raise when self.subagents is truthy."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = [Mock()]
        message = AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {}, "id": "call_1"}],
            response_metadata={"finish_reason": "length"},
        )
        chunk = (("supervisor",), "updates", {"supervisor": {"messages": [message]}})

        with pytest.raises(TokenLimitExceededException):
            agent._check_truncation_in_chunk(chunk)

    def test_check_truncation_in_chunk_raises_for_subagent_node_key_with_subagents(self):
        """CR-006: a truncated AIMessage under a sub-agent's own node key (not "supervisor")
        must also raise; the check must not be limited to a fixed target_field."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = [Mock()]
        message = AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {}, "id": "call_1"}],
            response_metadata={"finish_reason": "length"},
        )
        chunk = (("analyst",), "updates", {"analyst": {"messages": [message]}})

        with pytest.raises(TokenLimitExceededException):
            agent._check_truncation_in_chunk(chunk)

    def test_check_truncation_in_chunk_handles_2tuple_with_subagents(self):
        """R3-CR-004: a 2-tuple while subagents is truthy must not crash the unpack —
        the namespace strip must key on the chunk shape, not the subagents flag."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = [Mock()]
        message = AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {}, "id": "call_1"}],
            response_metadata={"finish_reason": "length"},
        )
        chunk = ("updates", {"agent": {"messages": [message]}})

        with pytest.raises(TokenLimitExceededException):
            agent._check_truncation_in_chunk(chunk)

    def test_check_truncation_in_chunk_handles_3tuple_without_subagents(self):
        """R3-CR-004: a 3-tuple (subgraph chunk) while subagents is falsy must also be
        tolerated — strip the namespace by shape instead of assuming it absent."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = None
        message = AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {}, "id": "call_1"}],
            response_metadata={"finish_reason": "length"},
        )
        chunk = (("supervisor",), "updates", {"supervisor": {"messages": [message]}})

        with pytest.raises(TokenLimitExceededException):
            agent._check_truncation_in_chunk(chunk)


class TestStreamGraphCancellation:
    """Tests for A.2 (close stream on every exit) and A.3 (reset supervisor coordinator)."""

    def test_stream_graph_closes_stream_on_process_chunk_exception(self):
        """A.2: an exception inside the loop body must still close the LangGraph stream."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = None
        agent.require_tool_confirmation = False
        mock_stream = Mock()
        mock_stream.__iter__ = Mock(return_value=iter([("updates", {"agent": {"messages": []}})]))
        agent.agent_executor = Mock(stream=Mock(return_value=mock_stream))
        agent.thread_generator = Mock(is_closed=Mock(return_value=False))
        agent.process_chunk = Mock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            agent._stream_graph({"messages": []})

        mock_stream.close.assert_called_once()

    def test_stream_graph_close_failure_does_not_mask_original_exception(self):
        """CR-004: if stream.close() itself raises during the finally, the original
        exception that triggered it must still be the one that propagates."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = None
        agent.require_tool_confirmation = False
        mock_stream = Mock()
        mock_stream.__iter__ = Mock(return_value=iter([("updates", {"agent": {"messages": []}})]))
        mock_stream.close = Mock(side_effect=ValueError("close failed"))
        agent.agent_executor = Mock(stream=Mock(return_value=mock_stream))
        agent.thread_generator = Mock(is_closed=Mock(return_value=False))
        agent.process_chunk = Mock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            agent._stream_graph({"messages": []})

        mock_stream.close.assert_called_once()

    def test_stream_graph_records_exception_on_span_before_closing_stream(self):
        """CR-005: the exception must be recorded on the OTEL span before stream.close()
        runs, since closing the stream demotes the current span to a non-recording NoOp."""
        from unittest.mock import patch

        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = None
        agent.require_tool_confirmation = False
        call_order = []
        mock_stream = Mock()
        mock_stream.__iter__ = Mock(return_value=iter([("updates", {"agent": {"messages": []}})]))
        mock_stream.close = Mock(side_effect=lambda: call_order.append("close"))
        agent.agent_executor = Mock(stream=Mock(return_value=mock_stream))
        agent.thread_generator = Mock(is_closed=Mock(return_value=False))
        agent.process_chunk = Mock(side_effect=RuntimeError("boom"))

        with patch(
            "codemie.agents.langgraph_agent.record_exception_on_span",
            side_effect=lambda exc: call_order.append("record"),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                agent._stream_graph({"messages": []})

        assert call_order == ["record", "close"]

    def test_stream_graph_marks_consumer_aborted_before_closing_stream_on_tool_error_cap(self):
        """EPMCDME-16233 sync point: the consecutive tool-error cap raises on the consumer side
        (inside process_chunk). It must set the shared abort flag before stream.close() blocks
        on running sub-agents, so a sibling's next pre_model_hook stops instead of calling its model."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.subagents = None
        agent.require_tool_confirmation = False
        call_order = []
        mock_stream = Mock()
        mock_stream.__iter__ = Mock(return_value=iter([("updates", {"agent": {"messages": []}})]))
        mock_stream.close = Mock(side_effect=lambda: call_order.append("close"))
        agent.agent_executor = Mock(stream=Mock(return_value=mock_stream))
        agent.thread_generator = Mock(
            is_closed=Mock(return_value=False),
            mark_consumer_aborted=Mock(side_effect=lambda: call_order.append("mark_aborted")),
        )
        agent.process_chunk = Mock(side_effect=MaxConsecutiveToolErrorsExceededException("3 consecutive tool errors"))

        with pytest.raises(MaxConsecutiveToolErrorsExceededException):
            agent._stream_graph({"messages": []})

        assert call_order == ["mark_aborted", "close"]

    def test_stream_exception_path_resets_supervisor_coordinator(self):
        """A.3: a fatal exception clears the coordinator's dangling handoff state."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent._supervisor_coordinator = Mock()
        agent._agent_streaming = Mock(side_effect=RuntimeError("boom"))
        agent._send_error_to_thread = Mock()
        agent.thread_generator = Mock(is_closed=Mock(return_value=False))
        agent.request_uuid = "test-uuid"
        agent._pending_tool_confirmation = False
        agent.require_tool_confirmation = False
        agent._otel_context = otel_context.get_current()
        agent.conversation_id = None

        agent.stream()

        agent._supervisor_coordinator.reset.assert_called_once()

    def test_stream_mcp_auth_exception_path_resets_supervisor_coordinator(self):
        """CR-002: the MCP-auth fatal-error exit must also clear the coordinator's dangling handoff state,
        the same as the generic exception branch (A.3 is unqualified by exception type)."""
        from codemie.core.exceptions import MCPAuthenticationRequiredException

        agent = create_mock_agent(llm_model="gpt-4.1")
        agent._supervisor_coordinator = Mock()
        auth_error = MCPAuthenticationRequiredException(payload={"server": "jira"})
        agent._agent_streaming = Mock(side_effect=auth_error)
        agent.thread_generator = Mock(is_closed=Mock(return_value=False))
        agent.request_uuid = "test-uuid"
        agent._pending_tool_confirmation = False
        agent.require_tool_confirmation = False
        agent._otel_context = otel_context.get_current()
        agent.conversation_id = None

        with pytest.raises(MCPAuthenticationRequiredException):
            agent.stream()

        agent._supervisor_coordinator.reset.assert_called_once()

    def test_stream_success_path_sets_success_true(self):
        """CR-001: the happy-path final chunk must set success=True, mirroring the new
        success=False wired into the error path."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent._supervisor_coordinator = Mock()
        agent._agent_streaming = Mock(return_value="final answer")
        agent.thread_generator = Mock(is_closed=Mock(return_value=False))
        agent.thread_context = None
        agent.request_uuid = "test-uuid-success"
        agent._pending_tool_confirmation = False
        agent.require_tool_confirmation = False
        agent._otel_context = otel_context.get_current()
        agent.conversation_id = None

        agent.stream()

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["success"] is True


class TestInvokeAgentSupervisorReset:
    """Tests for CR-005: _invoke_agent's fatal-exception branch must reset the
    supervisor coordinator too, mirroring stream()'s except branch."""

    def test_invoke_agent_exception_path_resets_supervisor_coordinator(self):
        """A fatal exception on the non-streaming invoke path (invoke/invoke_task/
        generate) must clear the coordinator's dangling handoff state -- otherwise the
        next call on this agent instance inherits stale active-handoff bindings from
        the failed run."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent._supervisor_coordinator = Mock()
        agent._get_run_config = Mock(return_value={})
        agent._stream_graph = Mock(side_effect=RuntimeError("boom"))
        agent._get_user_error_message = Mock(return_value="error message")
        agent._build_generation_result = Mock(return_value=Mock())

        agent._invoke_agent({"messages": []})

        agent._supervisor_coordinator.reset.assert_called_once()


class TestSendErrorToThreadPopulatesErrorFields:
    """Tests for B.7: the final SSE chunk on the error path must carry structured error fields."""

    def test_send_error_to_thread_populates_error_fields(self):
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.thread_generator = Mock()
        agent.thread_context = None
        agent.extended_error = Mock(return_value="sanitized message")

        error = TokenLimitExceededException(
            message="Token limit exceeded", model="gpt-4.1", truncation_reason="finish_reason=length"
        )
        agent._send_error_to_thread(error, execution_start=0.0, chunks_collector=[], include_tool_errors=True)

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["success"] is False
        assert sent["agent_error"]["error_code"] == "agent_token_limit"
        assert sent["error_details"] is not None

    def test_send_error_to_thread_redacts_stacktrace_when_hiding_exceptions(self, monkeypatch):
        """CR-003: agent_error.stacktrace must honor HIDE_AGENT_STREAMING_EXCEPTIONS,
        the same gate already applied to user_message two lines earlier in this method."""
        from codemie.configs import config as global_config

        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.thread_generator = Mock()
        agent.thread_context = None
        agent.extended_error = Mock(return_value="sanitized message")
        monkeypatch.setattr(global_config, "HIDE_AGENT_STREAMING_EXCEPTIONS", True)

        error = TokenLimitExceededException(
            message="Token limit exceeded", model="gpt-4.1", truncation_reason="finish_reason=length"
        )
        agent._send_error_to_thread(error, execution_start=0.0, chunks_collector=[], include_tool_errors=True)

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["agent_error"]["error_code"] == "agent_token_limit"
        assert sent["agent_error"]["stacktrace"] is None

    def test_send_error_to_thread_redacts_stacktrace_in_error_details_too(self, monkeypatch):
        """F2: error_details (built from error_response.get_error()) must also honor
        HIDE_AGENT_STREAMING_EXCEPTIONS. The stacktrace redaction previously only touched
        a separate model_copy assigned to the local `agent_error` var, never the object
        get_error() returns, so error_details kept leaking the full stacktrace."""
        from codemie.configs import config as global_config

        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.thread_generator = Mock()
        agent.thread_context = None
        agent.extended_error = Mock(return_value="sanitized message")
        monkeypatch.setattr(global_config, "HIDE_AGENT_STREAMING_EXCEPTIONS", True)

        try:
            raise TokenLimitExceededException(
                message="Token limit exceeded", model="gpt-4.1", truncation_reason="finish_reason=length"
            )
        except TokenLimitExceededException as error:
            agent._send_error_to_thread(error, execution_start=0.0, chunks_collector=[], include_tool_errors=True)

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["error_details"]["stacktrace"] is None

    def test_send_error_to_thread_redacts_internal_traceback_when_hiding_exceptions(self, monkeypatch):
        """R3-CR-003: InternalError.details['traceback'] must honor HIDE_AGENT_STREAMING_EXCEPTIONS,
        the same gate already applied to agent_error.stacktrace on the AGENT path. An unclassified
        exception routes to ErrorCategory.INTERNAL, whose traceback previously leaked to the client."""
        from codemie.configs import config as global_config

        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.thread_generator = Mock()
        agent.thread_context = None
        agent.extended_error = Mock(return_value="sanitized message")
        monkeypatch.setattr(global_config, "HIDE_AGENT_STREAMING_EXCEPTIONS", True)

        try:
            raise RuntimeError("unclassified boom")
        except RuntimeError as error:
            agent._send_error_to_thread(error, execution_start=0.0, chunks_collector=[], include_tool_errors=True)

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        # INTERNAL errors carry a fixed short message, never the exception text or the partial output
        assert sent["agent_error"]["error_code"] == "platform_error"
        assert sent["agent_error"]["message"] == global_config.AGENT_MSG_INTERNAL_ERROR
        assert sent["agent_error"]["stacktrace"] is None
        assert sent["error_details"] is not None
        assert sent["error_details"]["details"]["traceback"] is None

    def test_send_error_to_thread_populates_tool_errors(self):
        """CR-008: the ToolErrorDetails formatting wired into tool_errors must be verified,
        not just the agent_error/error_details fields."""
        from codemie.core.errors import AgentErrorDetails, ErrorDetailLevel, ErrorResponse, ToolErrorDetails
        from codemie.core.error_constants import ErrorCategory, ErrorCode

        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.thread_generator = Mock()
        agent.thread_context = None
        agent.extended_error = Mock(return_value="sanitized message")

        tool_error = ToolErrorDetails(
            error_code=ErrorCode.TOOL_EXECUTION_FAILED,
            message="Tool failed",
            tool_name="jira_search",
            tool_call_id="call_abc123",
            http_status=500,
        )
        error_response = ErrorResponse(
            category=ErrorCategory.AGENT,
            agent_error=AgentErrorDetails(error_code=ErrorCode.AGENT_CALLBACK_FAILURE, message="Callback failed"),
            tool_errors=[tool_error],
        )

        from unittest.mock import patch

        with patch("codemie.agents.langgraph_agent.handle_agent_exception", return_value=error_response):
            agent._send_error_to_thread(
                RuntimeError("boom"), execution_start=0.0, chunks_collector=[], include_tool_errors=True
            )

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["tool_errors"] == [tool_error.format_for_level(ErrorDetailLevel.STANDARD)]

    def test_send_error_to_thread_gates_structured_fields_by_default(self):
        """CR-004: include_tool_errors=False (the default) must suppress the *rich*
        structured fields -- tool_errors and full error_details, which can carry
        per-tool payloads or (without HIDE_AGENT_STREAMING_EXCEPTIONS) a raw
        stacktrace -- on the streaming error path, the same gate
        StandardAssistantHandler._format_errors already applies on the sync/timeout
        paths, which this call site was bypassing entirely.

        agent_error is the one exception (EPMCDME-14855, 2026-09-22): it's still
        always populated when the error is agent-category, but stripped down to just
        {error_code, message} -- both already public elsewhere in this same chunk
        (message is folded into `generated`, error_code is always `execution_error`),
        so this doesn't reopen CR-004's actual concern (leaking tool/stacktrace
        detail the client didn't ask for)."""
        from codemie.core.errors import AgentErrorDetails, ErrorResponse, ToolErrorDetails
        from codemie.core.error_constants import ErrorCategory, ErrorCode

        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.thread_generator = Mock()
        agent.thread_context = None
        agent.extended_error = Mock(return_value="sanitized message")

        tool_error = ToolErrorDetails(
            error_code=ErrorCode.TOOL_EXECUTION_FAILED,
            message="Tool failed",
            tool_name="jira_search",
            tool_call_id="call_abc123",
            http_status=500,
        )
        error_response = ErrorResponse(
            category=ErrorCategory.AGENT,
            agent_error=AgentErrorDetails(
                error_code=ErrorCode.AGENT_CALLBACK_FAILURE,
                message="Callback failed",
                details={"internal": "should not reach the client"},
                stacktrace="Traceback (most recent call last): ...",
            ),
            tool_errors=[tool_error],
        )

        from unittest.mock import patch

        with patch("codemie.agents.langgraph_agent.handle_agent_exception", return_value=error_response):
            agent._send_error_to_thread(RuntimeError("boom"), execution_start=0.0, chunks_collector=[])

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["tool_errors"] is None
        assert sent["error_details"] is None
        assert sent["success"] is False
        # Short and safe: error_code + message only, the richer details/stacktrace are dropped
        # even though include_tool_errors was False (so redact_for_streaming() never ran).
        assert sent["agent_error"] == {
            "error_code": "agent_callback_failure",
            "message": "Callback failed",
            "details": None,
            "stacktrace": None,
        }

    def test_send_error_to_thread_separates_prior_content_from_error_message(self):
        """EPMCDME-14855, 2026-09-22: found live against a real truncation -- the
        pre-truncation response text and the appended error message ran together with
        no separator ("...representThe configured output token limit was reached...").
        `generated` must keep the two visually distinct."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.thread_generator = Mock()
        agent.thread_context = None
        agent.extended_error = Mock(return_value="The configured output token limit was reached.")

        error = TokenLimitExceededException(
            message="Token limit exceeded", model="gpt-4.1", truncation_reason="finish_reason=length"
        )
        chunks_collector = ["...these marks, dating back approximately 20,000 years, represent"]
        agent._send_error_to_thread(error, execution_start=0.0, chunks_collector=chunks_collector)

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert "representThe configured" not in sent["generated"]
        assert sent["generated"] == (
            "...these marks, dating back approximately 20,000 years, represent"
            "\n\nThe configured output token limit was reached."
        )

    def test_send_error_to_thread_no_leading_separator_when_nothing_generated_yet(self):
        """No prior chunk (e.g. truncated before any output) -- the error message
        must not start with a stray separator."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.thread_generator = Mock()
        agent.thread_context = None
        agent.extended_error = Mock(return_value="The configured output token limit was reached.")

        error = TokenLimitExceededException(
            message="Token limit exceeded", model="gpt-4.1", truncation_reason="finish_reason=length"
        )
        agent._send_error_to_thread(error, execution_start=0.0, chunks_collector=[])

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["generated"] == "The configured output token limit was reached."

    def test_send_error_to_thread_no_extra_separator_when_prior_chunk_ends_in_whitespace(self):
        """A prior chunk that already ends in whitespace/newline must not get a second
        separator glued on."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.thread_generator = Mock()
        agent.thread_context = None
        agent.extended_error = Mock(return_value="The configured output token limit was reached.")

        error = TokenLimitExceededException(
            message="Token limit exceeded", model="gpt-4.1", truncation_reason="finish_reason=length"
        )
        chunks_collector = ["Some prior paragraph.\n\n"]
        agent._send_error_to_thread(error, execution_start=0.0, chunks_collector=chunks_collector)

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["generated"] == "Some prior paragraph.\n\nThe configured output token limit was reached."


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


class TestTruncationAbortPreModelHook:
    """Tests for _build_truncation_abort_pre_model_hook (EPMCDME-16233)."""

    def test_truncation_abort_hook_raises_on_truncated_last_message(self):
        agent = create_mock_agent(llm_model="gpt-4.1")
        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "test_tool", "args": {"param": "value"}, "id": "call_123"}],
                    response_metadata={"finish_reason": "length"},
                )
            ]
        }

        hook = _build_truncation_abort_pre_model_hook(agent)

        with pytest.raises(TokenLimitExceededException):
            hook(state)

    def test_truncation_abort_hook_raises_on_closed_stream(self):
        agent = create_mock_agent(llm_model="gpt-4.1")
        agent.thread_generator = Mock(is_closed=Mock(return_value=True), cancellation_reason=None)
        state = {"messages": [AIMessage(content="normal response")]}

        hook = _build_truncation_abort_pre_model_hook(agent)

        with pytest.raises(StreamAbortedException):
            hook(state)

    def test_truncation_abort_hook_noop_on_normal_open_stream(self):
        agent = create_mock_agent(llm_model="gpt-4.1")
        state = {"messages": [AIMessage(content="normal response")]}

        hook = _build_truncation_abort_pre_model_hook(agent)

        assert hook(state) is None

    def test_truncation_abort_hook_noop_on_empty_messages(self):
        agent = create_mock_agent(llm_model="gpt-4.1")
        state = {"messages": []}

        hook = _build_truncation_abort_pre_model_hook(agent)

        assert hook(state) is None

    def test_truncation_abort_hook_raises_when_truncated_ai_message_is_followed_by_tool_message(self):
        """A truncated tool-call response that still executed (langgraph's default ToolNode
        swallows tool errors into a ToolMessage rather than raising) must still be caught on
        the next pre_model_hook pass, even though a ToolMessage now sits on top of it — this
        is what actually stops the recursion-limit loop in a real compiled graph (EPMCDME-16233).
        """
        agent = create_mock_agent(llm_model="gpt-4.1")
        state = {
            "messages": [
                HumanMessage(content="do something"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "test_tool", "args": {"param": "value"}, "id": "call_123"}],
                    response_metadata={"finish_reason": "length"},
                ),
                ToolMessage(content="did value", name="test_tool", tool_call_id="call_123"),
            ]
        }

        hook = _build_truncation_abort_pre_model_hook(agent)

        with pytest.raises(TokenLimitExceededException):
            hook(state)

    def test_truncation_abort_hook_does_not_look_past_a_new_human_turn(self):
        """A previous turn's truncated AIMessage must never fail a brand-new turn."""
        agent = create_mock_agent(llm_model="gpt-4.1")
        state = {
            "messages": [
                HumanMessage(content="first turn"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "test_tool", "args": {"param": "value"}, "id": "call_123"}],
                    response_metadata={"finish_reason": "length"},
                ),
                ToolMessage(content="did value", name="test_tool", tool_call_id="call_123"),
                AIMessage(content="here is the result"),
                HumanMessage(content="second, unrelated turn"),
            ]
        }

        hook = _build_truncation_abort_pre_model_hook(agent)

        assert hook(state) is None
