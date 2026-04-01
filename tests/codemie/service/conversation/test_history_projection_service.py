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

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from codemie.chains.base import Thought, ThoughtAuthorType
from codemie.core.constants import ChatRole
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.service.conversation.history_projection_service import (
    NATIVE_TOOLS_MODE,
    TOOL_REPLAY_TYPE,
    TOOL_STATUS_COMPLETED,
    TOOL_STATUS_ERROR,
    ConversationHistoryProjectionService,
)


def _build_conversation_with_tool_turn(
    *,
    assistant_message: str,
    tool_output: str,
    status: str = TOOL_STATUS_COMPLETED,
    thought_error: bool = False,
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
