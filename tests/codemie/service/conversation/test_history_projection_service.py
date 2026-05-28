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

import re

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from codemie.chains.base import Thought, ThoughtAuthorType
from codemie.core.constants import MAX_TOOL_NAME_LENGTH, ChatRole
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.service.conversation.history_projection_service import (
    NATIVE_TOOLS_MODE,
    TOOL_REPLAY_TYPE,
    TOOL_STATUS_COMPLETED,
    TOOL_STATUS_ERROR,
    ConversationHistoryProjectionService,
)

TOOL_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _build_conversation_with_tool_turn(
    *,
    assistant_message: str,
    tool_output: str,
    status: str = TOOL_STATUS_COMPLETED,
    thought_error: bool = False,
    assistant_id: str = "assistant-a",
) -> Conversation:
    thought = Thought(
        id="tool-call-1",
        author_name="Search Tool",
        author_type=ThoughtAuthorType.Tool.value,
        input_text='{"query": "release notes"}',
        message=tool_output,
        error=thought_error,
        metadata={
            "replay_type": TOOL_REPLAY_TYPE,
            "tool_name": "search_tool",
            "tool_args": {"query": "release notes"},
            "tool_args_text": '{"query": "release notes"}',
            "status": status,
            "result_summary": f"summary::{tool_output}",
        },
    )

    return Conversation(
        conversation_id="conv-123",
        history=[
            GeneratedMessage(
                role=ChatRole.USER,
                message="Find the latest release notes",
                history_index=0,
            ),
            GeneratedMessage(
                role=ChatRole.ASSISTANT,
                message=assistant_message,
                history_index=0,
                assistant_id=assistant_id,
                thoughts=[thought],
            ),
        ],
    )


def test_build_for_request_zero_windows_disables_completed_tool_replay():
    conversation = _build_conversation_with_tool_turn(
        assistant_message="Here are the release notes highlights.",
        tool_output="release notes tool output",
    )

    messages = ConversationHistoryProjectionService.build_for_request(
        conversation=conversation,
        mode=NATIVE_TOOLS_MODE,
        max_full_tool_turns=0,
        max_summarized_tool_turns=0,
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage]
    assert messages[0].content == "Find the latest release notes"
    assert messages[1].content == "Here are the release notes highlights."


def test_build_for_request_skips_duplicate_assistant_message_when_tool_output_matches():
    conversation = _build_conversation_with_tool_turn(
        assistant_message="same tool output",
        tool_output="same tool output",
    )

    messages = ConversationHistoryProjectionService.build_for_request(
        conversation=conversation,
        mode=NATIVE_TOOLS_MODE,
        max_full_tool_turns=1,
        max_summarized_tool_turns=0,
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage, ToolMessage]
    assert messages[1].tool_calls[0]["name"] == "search_tool"
    assert messages[2].content == "same tool output"


def test_build_for_request_replays_error_tool_even_when_windows_are_disabled():
    conversation = _build_conversation_with_tool_turn(
        assistant_message="The tool failed, so I need a fallback approach.",
        tool_output="tool execution failed with timeout",
        status=TOOL_STATUS_ERROR,
        thought_error=True,
    )

    messages = ConversationHistoryProjectionService.build_for_request(
        conversation=conversation,
        mode=NATIVE_TOOLS_MODE,
        max_full_tool_turns=0,
        max_summarized_tool_turns=0,
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage, ToolMessage, AIMessage]
    assert messages[1].tool_calls[0]["name"] == "search_tool"
    assert messages[2].content == "tool execution failed with timeout"
    assert messages[3].content == "The tool failed, so I need a fallback approach."


def test_build_for_request_downgrades_tool_replay_from_different_assistant_to_text_summary():
    conversation = _build_conversation_with_tool_turn(
        assistant_message="Here are the release notes highlights.",
        tool_output="release notes tool output",
        assistant_id="assistant-a",
    )

    messages = ConversationHistoryProjectionService.build_for_request(
        conversation=conversation,
        mode=NATIVE_TOOLS_MODE,
        max_full_tool_turns=1,
        max_summarized_tool_turns=0,
        current_assistant_id="assistant-b",
        available_tool_names={"search_tool"},
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage, AIMessage]
    assert messages[0].content == "Find the latest release notes"
    assert "Previous tool activity:" in messages[1].content
    assert "search_tool" in messages[1].content
    assert messages[2].content == "Here are the release notes highlights."


def test_build_for_request_keeps_native_tool_replay_for_same_assistant_with_available_tool():
    conversation = _build_conversation_with_tool_turn(
        assistant_message="Here are the release notes highlights.",
        tool_output="release notes tool output",
        assistant_id="assistant-a",
    )

    messages = ConversationHistoryProjectionService.build_for_request(
        conversation=conversation,
        mode=NATIVE_TOOLS_MODE,
        max_full_tool_turns=1,
        max_summarized_tool_turns=0,
        current_assistant_id="assistant-a",
        available_tool_names={"search_tool"},
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage, ToolMessage, AIMessage]
    assert messages[1].tool_calls[0]["name"] == "search_tool"
    assert messages[2].content == "release notes tool output"
    assert messages[3].content == "Here are the release notes highlights."


def test_build_for_request_downgrades_same_assistant_tool_replay_when_tool_is_unavailable():
    conversation = _build_conversation_with_tool_turn(
        assistant_message="Here are the release notes highlights.",
        tool_output="release notes tool output",
        assistant_id="assistant-a",
    )

    messages = ConversationHistoryProjectionService.build_for_request(
        conversation=conversation,
        mode=NATIVE_TOOLS_MODE,
        max_full_tool_turns=1,
        max_summarized_tool_turns=0,
        current_assistant_id="assistant-a",
        available_tool_names={"other_tool"},
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage, AIMessage]
    assert messages[0].content == "Find the latest release notes"
    assert "Previous tool activity:" in messages[1].content
    assert "search_tool" in messages[1].content
    assert messages[2].content == "Here are the release notes highlights."


def test_build_for_request_keeps_supported_native_tool_replay_when_turn_mixes_supported_and_unavailable_tools():
    supported_tool_thought = Thought(
        id="tool-call-1",
        author_name="Search Tool",
        author_type=ThoughtAuthorType.Tool.value,
        input_text='{"query": "release notes"}',
        message="release notes tool output",
        metadata={
            "replay_type": TOOL_REPLAY_TYPE,
            "tool_name": "search_tool",
            "tool_args": {"query": "release notes"},
            "tool_args_text": '{"query": "release notes"}',
            "status": TOOL_STATUS_COMPLETED,
            "result_summary": "summary::release notes tool output",
        },
    )
    subagent_handoff_thought = Thought(
        id="handoff-call-1",
        author_name="Analyst #1",
        author_type=ThoughtAuthorType.Tool.value,
        input_text="Analyze the release notes",
        message="Subassistant completed the release notes analysis",
        metadata={
            "replay_type": TOOL_REPLAY_TYPE,
            "tool_name": "analyst_1",
            "tool_args_text": "Analyze the release notes",
            "status": TOOL_STATUS_COMPLETED,
            "result_summary": "summary::Subassistant completed the release notes analysis",
        },
    )

    conversation = Conversation(
        conversation_id="conv-123",
        history=[
            GeneratedMessage(
                role=ChatRole.USER,
                message="Find the latest release notes",
                history_index=0,
            ),
            GeneratedMessage(
                role=ChatRole.ASSISTANT,
                message="Here are the release notes highlights.",
                history_index=0,
                assistant_id="assistant-a",
                thoughts=[supported_tool_thought, subagent_handoff_thought],
            ),
        ],
    )

    messages = ConversationHistoryProjectionService.build_for_request(
        conversation=conversation,
        mode=NATIVE_TOOLS_MODE,
        max_full_tool_turns=1,
        max_summarized_tool_turns=0,
        current_assistant_id="assistant-a",
        available_tool_names={"search_tool"},
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage, ToolMessage, AIMessage, AIMessage]
    assert messages[1].tool_calls[0]["name"] == "search_tool"
    assert messages[2].content == "release notes tool output"
    assert "Previous tool activity:" in messages[3].content
    assert "analyst_1" in messages[3].content
    assert messages[4].content == "Here are the release notes highlights."


def test_build_for_request_preserves_original_order_when_native_and_downgraded_tools_mix():
    unsupported_handoff_thought = Thought(
        id="handoff-call-1",
        author_name="Analyst #1",
        author_type=ThoughtAuthorType.Tool.value,
        input_text="Analyze the release notes",
        message="Subassistant completed the release notes analysis",
        metadata={
            "replay_type": TOOL_REPLAY_TYPE,
            "tool_name": "analyst_1",
            "tool_args_text": "Analyze the release notes",
            "status": TOOL_STATUS_COMPLETED,
            "result_summary": "summary::Subassistant completed the release notes analysis",
        },
    )
    supported_tool_thought = Thought(
        id="tool-call-1",
        author_name="Search Tool",
        author_type=ThoughtAuthorType.Tool.value,
        input_text='{"query": "release notes"}',
        message="release notes tool output",
        metadata={
            "replay_type": TOOL_REPLAY_TYPE,
            "tool_name": "search_tool",
            "tool_args": {"query": "release notes"},
            "tool_args_text": '{"query": "release notes"}',
            "status": TOOL_STATUS_COMPLETED,
            "result_summary": "summary::release notes tool output",
        },
    )

    conversation = Conversation(
        conversation_id="conv-123",
        history=[
            GeneratedMessage(
                role=ChatRole.USER,
                message="Find the latest release notes",
                history_index=0,
            ),
            GeneratedMessage(
                role=ChatRole.ASSISTANT,
                message="Here are the release notes highlights.",
                history_index=0,
                assistant_id="assistant-a",
                thoughts=[unsupported_handoff_thought, supported_tool_thought],
            ),
        ],
    )

    messages = ConversationHistoryProjectionService.build_for_request(
        conversation=conversation,
        mode=NATIVE_TOOLS_MODE,
        max_full_tool_turns=1,
        max_summarized_tool_turns=0,
        current_assistant_id="assistant-a",
        available_tool_names={"search_tool"},
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage, AIMessage, ToolMessage, AIMessage]
    assert "analyst_1" in messages[1].content
    assert messages[2].tool_calls[0]["name"] == "search_tool"
    assert messages[3].content == "release notes tool output"
    assert messages[4].content == "Here are the release notes highlights."


def test_build_for_request_downgrades_each_tool_to_a_separate_text_message():
    first_tool_thought = Thought(
        id="tool-call-1",
        author_name="Search Tool",
        author_type=ThoughtAuthorType.Tool.value,
        input_text='{"query": "release notes"}',
        message="release notes tool output",
        metadata={
            "replay_type": TOOL_REPLAY_TYPE,
            "tool_name": "search_tool",
            "tool_args": {"query": "release notes"},
            "tool_args_text": '{"query": "release notes"}',
            "status": TOOL_STATUS_COMPLETED,
            "result_summary": "summary::release notes tool output",
        },
    )
    second_tool_thought = Thought(
        id="tool-call-2",
        author_name="Repo Lookup",
        author_type=ThoughtAuthorType.Tool.value,
        input_text='{"repo": "codemie"}',
        message="repo lookup output",
        metadata={
            "replay_type": TOOL_REPLAY_TYPE,
            "tool_name": "lookup_repo",
            "tool_args": {"repo": "codemie"},
            "tool_args_text": '{"repo": "codemie"}',
            "status": TOOL_STATUS_COMPLETED,
            "result_summary": "summary::repo lookup output",
        },
    )

    conversation = Conversation(
        conversation_id="conv-123",
        history=[
            GeneratedMessage(
                role=ChatRole.USER,
                message="Find the latest release notes",
                history_index=0,
            ),
            GeneratedMessage(
                role=ChatRole.ASSISTANT,
                message="Here are the release notes highlights.",
                history_index=0,
                assistant_id="assistant-a",
                thoughts=[first_tool_thought, second_tool_thought],
            ),
        ],
    )

    messages = ConversationHistoryProjectionService.build_for_request(
        conversation=conversation,
        mode=NATIVE_TOOLS_MODE,
        max_full_tool_turns=1,
        max_summarized_tool_turns=0,
        current_assistant_id="assistant-b",
        available_tool_names={"search_tool", "lookup_repo"},
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage, AIMessage, AIMessage]
    assert "search_tool" in messages[1].content
    assert "lookup_repo" not in messages[1].content
    assert "lookup_repo" in messages[2].content
    assert "search_tool" not in messages[2].content
    assert messages[3].content == "Here are the release notes highlights."


def test_build_for_request_deduplicates_duplicate_subagent_tool_replay_by_call_id():
    duplicated_handoff_thought = Thought(
        id="handoff-call-1",
        author_name="Presentation Copy Builder",
        author_type=ThoughtAuthorType.Tool.value,
        input_text='Create presentation copy for "EPAM overview"',
        message="Presentation copy has been created and saved to presentation.copy",
        metadata={
            "replay_type": TOOL_REPLAY_TYPE,
            "tool_name": "transfer_to_presentation_copy_builder",
            "tool_args_text": 'Create presentation copy for "EPAM overview"',
            "status": TOOL_STATUS_COMPLETED,
            "result_summary": "summary::Presentation copy has been created and saved to presentation.copy",
        },
    )

    conversation = Conversation(
        conversation_id="conv-123",
        history=[
            GeneratedMessage(
                role=ChatRole.USER,
                message="Build an EPAM overview deck",
                history_index=0,
            ),
            GeneratedMessage(
                role=ChatRole.ASSISTANT,
                message="I'll prepare the deck assets.",
                history_index=0,
                assistant_id="assistant-a",
                thoughts=[duplicated_handoff_thought, duplicated_handoff_thought.model_copy(deep=True)],
            ),
        ],
    )

    messages = ConversationHistoryProjectionService.build_for_request(
        conversation=conversation,
        mode=NATIVE_TOOLS_MODE,
        max_full_tool_turns=1,
        max_summarized_tool_turns=0,
        current_assistant_id="assistant-a",
        available_tool_names={"transfer_to_presentation_copy_builder"},
    )

    assert [type(message) for message in messages] == [HumanMessage, AIMessage, ToolMessage, AIMessage]
    assert messages[1].tool_calls[0]["id"] == "handoff-call-1"
    assert messages[1].tool_calls[0]["name"] == "transfer_to_presentation_copy_builder"
    assert messages[2].tool_call_id == "handoff-call-1"
    assert messages[2].content == "Presentation copy has been created and saved to presentation.copy"
    assert messages[3].content == "I'll prepare the deck assets."


def test_normalize_tool_name_removes_invalid_characters():
    r"""Test that _normalize_tool_name removes characters not matching ^[a-zA-Z0-9_\.-]+$"""
    normalize = ConversationHistoryProjectionService._normalize_tool_name

    # Special characters replaced with underscore
    result = normalize("Sub/Assistant\\with: different chars")
    assert result == "sub_assistant_with_different_chars"
    assert TOOL_NAME_REGEX.match(result)

    result = normalize("Tool.!@#$%^&*()Name")
    assert result == "tool_name"
    assert TOOL_NAME_REGEX.match(result)

    result = normalize("Tool!@#$%^&*()Name")
    assert result == "tool_name"
    assert TOOL_NAME_REGEX.match(result)

    result = normalize("special chars: / \\ | ? *")
    assert result == "special_chars"
    assert TOOL_NAME_REGEX.match(result)

    # Spaces replaced with underscore
    result = normalize("Tool Name With Spaces")
    assert result == "tool_name_with_spaces"
    assert TOOL_NAME_REGEX.match(result)

    # Valid characters preserved (letters, numbers, dots, hyphens, underscores)
    result = normalize("valid_tool-name_123")
    assert result == "valid_tool-name_123"
    assert TOOL_NAME_REGEX.match(result)

    result = normalize("test-tool_v1_beta")
    assert result == "test-tool_v1_beta"
    assert TOOL_NAME_REGEX.match(result)

    # Multiple underscores collapsed
    result = normalize("multiple___underscores")
    assert result == "multiple_underscores"
    assert TOOL_NAME_REGEX.match(result)

    result = normalize("a____b____c")
    assert result == "a_b_c"
    assert TOOL_NAME_REGEX.match(result)

    # Leading/trailing underscores stripped
    result = normalize("___leading_trailing___")
    assert result == "leading_trailing"
    assert TOOL_NAME_REGEX.match(result)

    result = normalize("__tool__")
    assert result == "tool"
    assert TOOL_NAME_REGEX.match(result)

    # Empty or all invalid characters fallback to unknown_tool
    result = normalize("")
    assert result == "unknown_tool"
    assert TOOL_NAME_REGEX.match(result)

    result = normalize("!!!")
    assert result == "unknown_tool"
    assert TOOL_NAME_REGEX.match(result)

    result = normalize("@#$%")
    assert result == "unknown_tool"
    assert TOOL_NAME_REGEX.match(result)

    result = normalize(None)
    assert result == "unknown_tool"
    assert TOOL_NAME_REGEX.match(result)

    # Uppercase converted to lowercase
    result = normalize("UPPERCASE")
    assert result == "uppercase"
    assert TOOL_NAME_REGEX.match(result)

    result = normalize("MixedCase")
    assert result == "mixedcase"
    assert TOOL_NAME_REGEX.match(result)


def test_normalize_tool_name_enforces_max_length():
    """Test that _normalize_tool_name truncates to MAX_TOOL_NAME_LENGTH"""
    normalize = ConversationHistoryProjectionService._normalize_tool_name

    # Tool name exactly at max length
    exact_length = "a" * MAX_TOOL_NAME_LENGTH
    result = normalize(exact_length)
    assert result == exact_length
    assert len(result) == MAX_TOOL_NAME_LENGTH
    assert TOOL_NAME_REGEX.match(result)

    # Tool name exceeding max length gets truncated
    too_long = "a" * (MAX_TOOL_NAME_LENGTH + 10)
    result = normalize(too_long)
    assert len(result) == MAX_TOOL_NAME_LENGTH
    assert result == "a" * MAX_TOOL_NAME_LENGTH
    assert TOOL_NAME_REGEX.match(result)

    # Complex name that exceeds after normalization
    long_complex = "Very_Long_Tool_Name_" * 10  # Much longer than MAX_TOOL_NAME_LENGTH
    result = normalize(long_complex)
    assert len(result) == MAX_TOOL_NAME_LENGTH
    assert result.startswith("very_long_tool_name_")
    assert TOOL_NAME_REGEX.match(result)


def test_normalize_tool_name_combined_scenarios():
    """Test _normalize_tool_name with combined edge cases"""
    normalize = ConversationHistoryProjectionService._normalize_tool_name

    # Invalid chars + length limit
    long_invalid = ("tool!name@special#" * 10)[: MAX_TOOL_NAME_LENGTH + 20]
    result = normalize(long_invalid)
    assert len(result) <= MAX_TOOL_NAME_LENGTH
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for c in result)
    assert TOOL_NAME_REGEX.match(result)

    # Leading/trailing invalid + collapsing underscores
    result = normalize("___tool!!!name___")
    assert result == "tool_name"
    assert TOOL_NAME_REGEX.match(result)

    # All valid regex chars work
    valid_chars = "abc123_def_-456"
    result = normalize(valid_chars)
    assert result == valid_chars.lower()
    assert TOOL_NAME_REGEX.match(result)
