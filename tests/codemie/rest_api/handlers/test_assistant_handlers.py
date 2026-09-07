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

"""Unit tests for assistant handlers error handling."""

from __future__ import annotations

from time import time
from unittest.mock import Mock, patch

import pytest

from codemie.chains.base import GenerationResult
from codemie.core.errors import AgentErrorDetails, ErrorCode, ErrorDetailLevel, ToolErrorDetails
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.handlers.assistant_handlers import ChatHistoryData, StandardAssistantHandler
from codemie.rest_api.security.user import User


class TestFormatErrors:
    """Tests for StandardAssistantHandler._format_errors() method."""

    def test_format_errors_with_tool_errors_enabled_minimal_level(self):
        """Test formatting tool errors with include_tool_errors=True and MINIMAL detail level."""
        # Setup
        tool_errors = [
            ToolErrorDetails(
                tool_name="jira_search",
                tool_call_id="call_123",
                error_code=ErrorCode.TOOL_AUTHENTICATION,
                message="401 Unauthorized: Invalid credentials",
                http_status=401,
                details={"integration": "jira", "action": "search"},
            ),
            ToolErrorDetails(
                tool_name="git_clone",
                tool_call_id="call_456",
                error_code=ErrorCode.TOOL_TIMEOUT,
                message="Request timeout after 30s",
                http_status=None,
                details={"repo": "example/repo"},
            ),
        ]
        generation_result = GenerationResult(
            generated="Some response",
            time_elapsed=1.5,
            input_tokens_used=100,
            tokens_used=200,
            success=False,
            agent_error=None,
            tool_errors=tool_errors,
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=True, error_detail_level=ErrorDetailLevel.MINIMAL
        )

        # Verify
        assert formatted_agent_error is None
        assert formatted_tool_errors is not None
        assert len(formatted_tool_errors) == 2

        # First error (MINIMAL level)
        assert formatted_tool_errors[0]["tool_name"] == "jira_search"
        assert formatted_tool_errors[0]["error_code"] == "tool_authentication"
        assert formatted_tool_errors[0]["message"] == "401 Unauthorized: Invalid credentials"
        assert "tool_call_id" not in formatted_tool_errors[0]  # Not in MINIMAL
        assert "http_status" not in formatted_tool_errors[0]  # Not in MINIMAL
        assert "details" not in formatted_tool_errors[0]  # Not in MINIMAL

        # Second error (MINIMAL level)
        assert formatted_tool_errors[1]["tool_name"] == "git_clone"
        assert formatted_tool_errors[1]["error_code"] == "tool_timeout"
        assert formatted_tool_errors[1]["message"] == "Request timeout after 30s"

    def test_format_errors_with_tool_errors_enabled_standard_level(self):
        """Test formatting tool errors with include_tool_errors=True and STANDARD detail level."""
        # Setup
        tool_errors = [
            ToolErrorDetails(
                tool_name="confluence_search",
                tool_call_id="call_789",
                error_code=ErrorCode.TOOL_AUTHORIZATION,
                message="403 Forbidden: Insufficient permissions",
                http_status=403,
                details={"space": "DEV", "page_id": "12345"},
            )
        ]
        generation_result = GenerationResult(
            generated="Some response",
            time_elapsed=2.0,
            input_tokens_used=150,
            tokens_used=250,
            success=False,
            agent_error=None,
            tool_errors=tool_errors,
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=True, error_detail_level=ErrorDetailLevel.STANDARD
        )

        # Verify
        assert formatted_agent_error is None
        assert formatted_tool_errors is not None
        assert len(formatted_tool_errors) == 1

        # Error with STANDARD level
        error = formatted_tool_errors[0]
        assert error["tool_name"] == "confluence_search"
        assert error["tool_call_id"] == "call_789"
        assert error["error_code"] == "tool_authorization"
        assert error["message"] == "403 Forbidden: Insufficient permissions"
        assert error["http_status"] == 403
        assert "details" not in error  # Not in STANDARD
        assert "timestamp" not in error  # Not in STANDARD

    def test_format_errors_with_tool_errors_enabled_full_level(self):
        """Test formatting tool errors with include_tool_errors=True and FULL detail level."""
        # Setup
        tool_errors = [
            ToolErrorDetails(
                tool_name="aws_s3",
                tool_call_id="call_abc",
                error_code=ErrorCode.TOOL_SERVER_ERROR,
                message="500 Internal Server Error",
                http_status=500,
                details={"bucket": "my-bucket", "key": "file.txt"},
            )
        ]
        generation_result = GenerationResult(
            generated="Some response",
            time_elapsed=3.0,
            input_tokens_used=200,
            tokens_used=300,
            success=False,
            agent_error=None,
            tool_errors=tool_errors,
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=True, error_detail_level=ErrorDetailLevel.FULL
        )

        # Verify
        assert formatted_agent_error is None
        assert formatted_tool_errors is not None
        assert len(formatted_tool_errors) == 1

        # Error with FULL level
        error = formatted_tool_errors[0]
        assert error["tool_name"] == "aws_s3"
        assert error["tool_call_id"] == "call_abc"
        assert error["error_code"] == "tool_server_error"
        assert error["message"] == "500 Internal Server Error"
        assert error["http_status"] == 500
        assert error["details"] == {"bucket": "my-bucket", "key": "file.txt"}
        assert "timestamp" in error  # Included in FULL

    def test_format_errors_with_tool_errors_disabled(self):
        """Test that tool errors are not included when include_tool_errors=False."""
        # Setup
        tool_errors = [
            ToolErrorDetails(
                tool_name="some_tool",
                tool_call_id="call_123",
                error_code=ErrorCode.TOOL_TIMEOUT,
                message="Timeout occurred",
                http_status=None,
            )
        ]
        generation_result = GenerationResult(
            generated="Some response",
            time_elapsed=1.0,
            input_tokens_used=100,
            tokens_used=200,
            success=False,
            agent_error=None,
            tool_errors=tool_errors,
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=False, error_detail_level=ErrorDetailLevel.STANDARD
        )

        # Verify
        assert formatted_tool_errors is None
        assert formatted_agent_error is None

    def test_format_errors_with_no_tool_errors(self):
        """Test formatting when generation_result has no tool errors."""
        # Setup
        generation_result = GenerationResult(
            generated="Successful response",
            time_elapsed=1.0,
            input_tokens_used=100,
            tokens_used=200,
            success=True,
            agent_error=None,
            tool_errors=None,
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=True, error_detail_level=ErrorDetailLevel.STANDARD
        )

        # Verify
        assert formatted_tool_errors is None
        assert formatted_agent_error is None

    def test_format_errors_with_empty_tool_errors_list(self):
        """Test formatting when generation_result has empty tool errors list."""
        # Setup
        generation_result = GenerationResult(
            generated="Successful response",
            time_elapsed=1.0,
            input_tokens_used=100,
            tokens_used=200,
            success=True,
            agent_error=None,
            tool_errors=[],
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=True, error_detail_level=ErrorDetailLevel.STANDARD
        )

        # Verify
        assert formatted_tool_errors is None
        assert formatted_agent_error is None

    def test_format_errors_with_agent_error_enabled(self):
        """Test formatting with agent error when include_tool_errors=True."""
        # Setup
        agent_error = AgentErrorDetails(
            error_code=ErrorCode.AGENT_TOKEN_LIMIT,
            message="Token limit exceeded: max_output_tokens reached",
            details={"model": "gpt-4", "max_tokens": 4096},
        )
        generation_result = GenerationResult(
            generated=None,
            time_elapsed=2.0,
            input_tokens_used=200,
            tokens_used=None,
            success=False,
            agent_error=agent_error,
            tool_errors=None,
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=True, error_detail_level=ErrorDetailLevel.STANDARD
        )

        # Verify
        assert formatted_tool_errors is None
        assert formatted_agent_error is not None
        assert formatted_agent_error.error_code == ErrorCode.AGENT_TOKEN_LIMIT
        assert formatted_agent_error.message == "Token limit exceeded: max_output_tokens reached"
        assert formatted_agent_error.details == {"model": "gpt-4", "max_tokens": 4096}

    def test_format_errors_with_agent_error_disabled(self):
        """Test that agent error is not included when include_tool_errors=False."""
        # Setup
        agent_error = AgentErrorDetails(
            error_code=ErrorCode.AGENT_TIMEOUT,
            message="Agent execution timeout after 60s",
            details={"timeout_seconds": 60},
        )
        generation_result = GenerationResult(
            generated=None,
            time_elapsed=60.0,
            input_tokens_used=100,
            tokens_used=None,
            success=False,
            agent_error=agent_error,
            tool_errors=None,
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=False, error_detail_level=ErrorDetailLevel.STANDARD
        )

        # Verify
        assert formatted_tool_errors is None
        assert formatted_agent_error is None

    def test_format_errors_with_both_agent_and_tool_errors(self):
        """Test formatting when both agent error and tool errors are present."""
        # Setup
        agent_error = AgentErrorDetails(
            error_code=ErrorCode.AGENT_INTERNAL_ERROR,
            message="Internal agent error occurred",
            details={"phase": "execution"},
        )
        tool_errors = [
            ToolErrorDetails(
                tool_name="database_query",
                tool_call_id="call_001",
                error_code=ErrorCode.TOOL_EXECUTION_FAILED,
                message="Query execution failed",
                http_status=None,
            )
        ]
        generation_result = GenerationResult(
            generated=None,
            time_elapsed=3.0,
            input_tokens_used=150,
            tokens_used=None,
            success=False,
            agent_error=agent_error,
            tool_errors=tool_errors,
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=True, error_detail_level=ErrorDetailLevel.STANDARD
        )

        # Verify both are present
        assert formatted_agent_error is not None
        assert formatted_agent_error.error_code == ErrorCode.AGENT_INTERNAL_ERROR

        assert formatted_tool_errors is not None
        assert len(formatted_tool_errors) == 1
        assert formatted_tool_errors[0]["tool_name"] == "database_query"
        assert formatted_tool_errors[0]["error_code"] == "tool_execution_failed"

    def test_format_errors_with_multiple_tool_errors_different_levels(self):
        """Test formatting multiple tool errors with different error codes."""
        # Setup
        tool_errors = [
            ToolErrorDetails(
                tool_name="tool_1",
                tool_call_id="call_1",
                error_code=ErrorCode.TOOL_AUTHENTICATION,
                message="Authentication failed",
                http_status=401,
            ),
            ToolErrorDetails(
                tool_name="tool_2",
                tool_call_id="call_2",
                error_code=ErrorCode.TOOL_RATE_LIMITED,
                message="Rate limit exceeded",
                http_status=429,
            ),
            ToolErrorDetails(
                tool_name="tool_3",
                tool_call_id="call_3",
                error_code=ErrorCode.TOOL_NOT_FOUND,
                message="Resource not found",
                http_status=404,
            ),
        ]
        generation_result = GenerationResult(
            generated="Partial response",
            time_elapsed=4.0,
            input_tokens_used=200,
            tokens_used=300,
            success=False,
            agent_error=None,
            tool_errors=tool_errors,
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=True, error_detail_level=ErrorDetailLevel.STANDARD
        )

        # Verify
        assert formatted_agent_error is None
        assert formatted_tool_errors is not None
        assert len(formatted_tool_errors) == 3

        # Verify each error is formatted correctly
        assert formatted_tool_errors[0]["error_code"] == "tool_authentication"
        assert formatted_tool_errors[0]["http_status"] == 401

        assert formatted_tool_errors[1]["error_code"] == "tool_rate_limited"
        assert formatted_tool_errors[1]["http_status"] == 429

        assert formatted_tool_errors[2]["error_code"] == "tool_not_found"
        assert formatted_tool_errors[2]["http_status"] == 404

    def test_format_errors_preserves_error_order(self):
        """Test that error formatting preserves the original order of tool errors."""
        # Setup
        tool_errors = [
            ToolErrorDetails(
                tool_name="first_tool",
                tool_call_id="call_1",
                error_code=ErrorCode.TOOL_TIMEOUT,
                message="First error",
            ),
            ToolErrorDetails(
                tool_name="second_tool",
                tool_call_id="call_2",
                error_code=ErrorCode.TOOL_NETWORK_ERROR,
                message="Second error",
            ),
            ToolErrorDetails(
                tool_name="third_tool",
                tool_call_id="call_3",
                error_code=ErrorCode.TOOL_VALIDATION,
                message="Third error",
            ),
        ]
        generation_result = GenerationResult(
            generated="Response",
            time_elapsed=1.0,
            input_tokens_used=100,
            tokens_used=200,
            success=False,
            agent_error=None,
            tool_errors=tool_errors,
        )

        # Execute
        formatted_tool_errors, _ = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=True, error_detail_level=ErrorDetailLevel.MINIMAL
        )

        # Verify order is preserved
        assert formatted_tool_errors[0]["tool_name"] == "first_tool"
        assert formatted_tool_errors[0]["message"] == "First error"

        assert formatted_tool_errors[1]["tool_name"] == "second_tool"
        assert formatted_tool_errors[1]["message"] == "Second error"

        assert formatted_tool_errors[2]["tool_name"] == "third_tool"
        assert formatted_tool_errors[2]["message"] == "Third error"

    def test_format_errors_with_successful_generation_result(self):
        """Test formatting when generation was successful (no errors)."""
        # Setup
        generation_result = GenerationResult(
            generated="Successful response text",
            time_elapsed=1.5,
            input_tokens_used=100,
            tokens_used=200,
            success=True,
            agent_error=None,
            tool_errors=None,
        )

        # Execute
        formatted_tool_errors, formatted_agent_error = StandardAssistantHandler._format_errors(
            generation_result, include_tool_errors=True, error_detail_level=ErrorDetailLevel.FULL
        )

        # Verify
        assert formatted_tool_errors is None
        assert formatted_agent_error is None


class TestSaveChatHistory:
    """Tests for save_chat_history guard logic"""

    @pytest.fixture
    def handler(self):
        """Create handler instance"""
        user = Mock(spec=User, id="user-123")
        assistant = Mock(id="assistant-123", project="test-project")
        return StandardAssistantHandler(assistant, user, "request-uuid")

    @pytest.fixture
    def chat_history_data_save_true(self):
        """ChatHistoryData with save_history=True"""
        request = AssistantChatRequest(text="Hello", save_history=True)
        return ChatHistoryData(execution_start=time(), request=request, response="Response", thoughts=[])

    @pytest.fixture
    def chat_history_data_save_false(self):
        """ChatHistoryData with save_history=False"""
        request = AssistantChatRequest(text="Hello", save_history=False)
        return ChatHistoryData(execution_start=time(), request=request, response="Response", thoughts=[])

    def test_save_chat_history_calls_upsert_when_true(self, handler, chat_history_data_save_true):
        """When save_history=True, calls ConversationService.upsert_chat_history"""
        with (
            patch("codemie.service.llm_service.utils.set_llm_context"),
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService") as mock_service,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock())

            handler.save_chat_history(chat_history_data_save_true)

            mock_service.upsert_chat_history.assert_called_once()
            mock_manager.clear_summary.assert_called_once()

    def test_save_chat_history_skips_upsert_when_false(self, handler, chat_history_data_save_false):
        """When save_history=False, skips ConversationService.upsert_chat_history"""
        with (
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService") as mock_service,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            handler.save_chat_history(chat_history_data_save_false)

            mock_service.upsert_chat_history.assert_not_called()
            mock_manager.clear_summary.assert_called_once()  # Always cleaned up

    def test_save_chat_history_logs_debug_when_skipped(self, handler, chat_history_data_save_false):
        """When skipping, logs debug message"""
        with (
            patch("codemie.rest_api.handlers.assistant_handlers.logger") as mock_logger,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager"),
        ):
            handler.save_chat_history(chat_history_data_save_false)

            mock_logger.debug.assert_called_once()
            call_args = mock_logger.debug.call_args[0][0]
            assert "save_history=False" in call_args

    def test_save_chat_history_passes_background_tasks_through(self, handler, chat_history_data_save_true):
        """save_chat_history forwards self.background_tasks to upsert_chat_history"""
        sentinel_background_tasks = Mock()
        handler.background_tasks = sentinel_background_tasks

        with (
            patch("codemie.service.llm_service.utils.set_llm_context"),
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService") as mock_service,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock())

            handler.save_chat_history(chat_history_data_save_true)

            _, call_kwargs = mock_service.upsert_chat_history.call_args
            assert call_kwargs["background_tasks"] is sentinel_background_tasks

    def test_background_tasks_defaults_to_none_before_process_request(self, handler):
        """Handler instances start with no background_tasks until process_request sets it"""
        assert handler.background_tasks is None


def test_populate_conversation_history_uses_legacy_chat_history_when_feature_flag_disabled():
    user = Mock(spec=User)
    user.id = "user-123"
    user.username = "testuser"
    user.name = "Test User"

    assistant = Mock()
    assistant.id = "assistant-123"
    assistant.llm_model_type = "test-model"

    handler = StandardAssistantHandler(assistant=assistant, user=user, request_uuid="test-uuid")
    request = AssistantChatRequest(conversation_id="conv-123", history=[], text="hello")

    conversation = Mock()
    conversation.finished_at = None
    legacy_history = [Mock(message="legacy")]
    conversation.to_chat_history.return_value = legacy_history
    conversation.user_id = "user-123"

    with (
        patch(
            "codemie.rest_api.handlers.assistant_handlers.DynamicConfigService.get_typed_value",
            return_value=False,
        ),
        patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=conversation),
        patch("codemie.rest_api.handlers.assistant_handlers.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.handlers.assistant_handlers.ConversationHistoryProjectionService.build_for_request"
        ) as projection_mock,
    ):
        handler._populate_conversation_history(request)

    projection_mock.assert_not_called()
    conversation.to_chat_history.assert_called_once_with()
    assert request.history == legacy_history


def test_populate_conversation_history_passes_active_assistant_context_to_projection():
    user = Mock(spec=User)
    user.id = "user-123"
    user.username = "testuser"
    user.name = "Test User"

    assistant = Mock()
    assistant.id = "assistant-123"
    assistant.llm_model_type = "test-model"
    first_tool = Mock()
    first_tool.name = "search_tool"
    second_tool = Mock()
    second_tool.name = "lookup_repo"
    assistant.toolkits = [Mock(tools=[first_tool, second_tool])]
    assistant.mcp_servers = []

    handler = StandardAssistantHandler(assistant=assistant, user=user, request_uuid="test-uuid")
    request = AssistantChatRequest(conversation_id="conv-123", history=[], text="hello")

    conversation = Mock()
    conversation.finished_at = None
    conversation.user_id = "user-123"
    projected_history = [Mock(message="projected")]

    with (
        patch(
            "codemie.rest_api.handlers.assistant_handlers.DynamicConfigService.get_typed_value",
            return_value=True,
        ),
        patch.object(handler, "_resolve_history_projection_mode", return_value="native_tools"),
        patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=conversation),
        patch("codemie.rest_api.handlers.assistant_handlers.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.handlers.assistant_handlers.ConversationHistoryProjectionService.build_for_request",
            return_value=projected_history,
        ) as projection_mock,
    ):
        handler._populate_conversation_history(request)

    projection_mock.assert_called_once_with(
        conversation=conversation,
        mode="native_tools",
        current_assistant_id="assistant-123",
        available_tool_names={"search_tool", "lookup_repo"},
    )
    assert request.history == projected_history


def test_populate_conversation_history_returns_403_not_409_for_unauthorized_finished_conversation():
    """Ability.READ must run before the finished guard so a 409 cannot leak that
    another user's conversation exists and is finished."""
    from datetime import datetime

    from fastapi import status

    from codemie.core.exceptions import ExtendedHTTPException

    user = Mock(spec=User)
    user.id = "user-123"
    assistant = Mock()
    assistant.id = "assistant-123"
    handler = StandardAssistantHandler(assistant=assistant, user=user, request_uuid="test-uuid")
    request = AssistantChatRequest(conversation_id="conv-123", history=[], text="hello")

    conversation = Mock()
    conversation.id = "conv-123"
    conversation.user_id = "other-user"
    conversation.finished_at = datetime(2026, 1, 1, 12, 0, 0)

    with (
        patch.object(handler, "_is_conversation_replay_v2_enabled", return_value=True),
        patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=conversation),
        patch("codemie.rest_api.handlers.assistant_handlers.Ability.can", return_value=False),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            handler._populate_conversation_history(request)

    assert exc_info.value.code == status.HTTP_403_FORBIDDEN


def test_legacy_populate_guards_finished_conversation_when_history_already_provided():
    """Legacy populate used to return immediately when request.history was set,
    skipping the 409 before inference."""
    from datetime import datetime

    from fastapi import status

    from codemie.core.exceptions import ExtendedHTTPException

    from codemie.core.models import ChatMessage, ChatRole

    user = Mock(spec=User)
    user.id = "user-123"
    assistant = Mock()
    assistant.id = "assistant-123"
    handler = StandardAssistantHandler(assistant=assistant, user=user, request_uuid="test-uuid")
    request = AssistantChatRequest(
        conversation_id="conv-123",
        history=[ChatMessage(role=ChatRole.USER, message="prior")],
        text="hello",
    )

    conversation = Mock()
    conversation.id = "conv-123"
    conversation.user_id = "user-123"
    conversation.finished_at = datetime(2026, 1, 1, 12, 0, 0)

    with (
        patch.object(handler, "_is_conversation_replay_v2_enabled", return_value=False),
        patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=conversation),
        patch("codemie.rest_api.handlers.assistant_handlers.Ability.can", return_value=True),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            handler._populate_conversation_history(request)

    assert exc_info.value.code == status.HTTP_409_CONFLICT
    conversation.to_chat_history.assert_not_called()


def test_filter_thoughts_drops_replay_only_entries_when_feature_flag_disabled():
    thoughts = [
        {
            "id": "tool-thought",
            "parent_id": "handoff-1",
            "message": "",
            "author_name": "Search Tool",
            "author_type": "tool",
            "input_text": '{"query": "release notes"}',
            "error": False,
            "metadata": {"replay_type": "tool_replay"},
        },
        {
            "id": "assistant-thought",
            "parent_id": "handoff-2",
            "message": "visible message",
            "author_name": "Assistant",
            "author_type": "assistant",
            "input_text": "",
            "error": False,
        },
    ]

    with patch(
        "codemie.rest_api.handlers.assistant_handlers.DynamicConfigService.get_typed_value",
        return_value=False,
    ):
        filtered = StandardAssistantHandler._filter_thoughts(thoughts)

    assert len(filtered) == 1
    assert filtered[0].id == "assistant-thought"
    assert filtered[0].parent_id == "handoff-2"
    assert filtered[0].message == "visible message"


def test_filter_thoughts_preserves_parent_id_when_feature_flag_enabled():
    thoughts = [
        {
            "id": "child-thought",
            "parent_id": "handoff-42",
            "message": "tool result",
            "author_name": "Lookup Repo",
            "author_type": "tool",
            "input_text": '{"query": "parallel branch"}',
            "error": False,
            "metadata": {"replay_type": "tool_replay"},
            "output_format": "markdown",
            "in_progress": False,
        }
    ]

    with patch(
        "codemie.rest_api.handlers.assistant_handlers.DynamicConfigService.get_typed_value",
        return_value=True,
    ):
        filtered = StandardAssistantHandler._filter_thoughts(thoughts)

    assert len(filtered) == 1
    assert filtered[0].id == "child-thought"
    assert filtered[0].parent_id == "handoff-42"
    assert filtered[0].message == "tool result"


class TestGuardConversationNotFinished:
    """Tests for StandardAssistantHandler._guard_conversation_not_finished."""

    @pytest.fixture
    def handler(self):
        assistant = Mock()
        assistant.id = "assistant-123"
        assistant.project = "test-project"
        user = Mock(spec=User)
        user.id = "user-123"
        return StandardAssistantHandler(assistant=assistant, user=user, request_uuid="req-uuid")

    def test_no_conversation_id_skips_guard(self, handler):
        request = Mock(spec=AssistantChatRequest)
        request.conversation_id = None

        with patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id") as mock_find:
            handler._guard_conversation_not_finished(request)
            mock_find.assert_not_called()

    def test_conversation_not_found_skips_guard(self, handler):
        request = Mock(spec=AssistantChatRequest)
        request.conversation_id = "conv-abc"

        with patch(
            "codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id",
            return_value=None,
        ):
            handler._guard_conversation_not_finished(request)  # must not raise

    def test_active_conversation_skips_guard(self, handler):
        from codemie.rest_api.models.conversation import Conversation as ConvModel

        request = Mock(spec=AssistantChatRequest)
        request.conversation_id = "conv-abc"

        conv = Mock(spec=ConvModel)
        conv.finished_at = None

        with (
            patch(
                "codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id",
                return_value=conv,
            ),
            patch("codemie.rest_api.handlers.assistant_handlers.Ability.can", return_value=True),
        ):
            handler._guard_conversation_not_finished(request)  # must not raise

    def test_finished_conversation_raises_409(self, handler):
        from datetime import datetime

        from fastapi import status

        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.rest_api.models.conversation import Conversation as ConvModel

        request = Mock(spec=AssistantChatRequest)
        request.conversation_id = "conv-abc"

        conv = Mock(spec=ConvModel)
        conv.id = "conv-abc"
        conv.finished_at = datetime(2026, 1, 1, 12, 0, 0)

        with (
            patch(
                "codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id",
                return_value=conv,
            ),
            patch("codemie.rest_api.handlers.assistant_handlers.Ability.can", return_value=True),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                handler._guard_conversation_not_finished(request)

        assert exc_info.value.code == status.HTTP_409_CONFLICT

    def test_process_request_raises_409_before_inference_for_finished_conversation(self, handler):
        """The guard must fire inside process_request before AssistantService.build_agent is called."""
        from datetime import datetime

        from fastapi import BackgroundTasks, status

        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.rest_api.models.conversation import Conversation as ConvModel

        request = Mock(spec=AssistantChatRequest)
        request.conversation_id = "conv-abc"
        request.stream = True
        request.background_task = False
        request.interactive_response = None
        request.file_names = []

        conv = Mock(spec=ConvModel)
        conv.id = "conv-abc"
        conv.finished_at = datetime(2026, 1, 1, 12, 0, 0)

        raw_request = Mock()
        raw_request.state.uuid = "req-uuid"

        with (
            patch(
                "codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id",
                return_value=conv,
            ),
            patch("codemie.rest_api.handlers.assistant_handlers.Ability.can", return_value=True),
            patch("codemie.rest_api.handlers.assistant_handlers.AssistantService.build_agent") as mock_build_agent,
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                handler.process_request(request, BackgroundTasks(), raw_request)

        assert exc_info.value.code == status.HTTP_409_CONFLICT
        mock_build_agent.assert_not_called()

    def test_process_request_raises_409_when_legacy_history_already_provided(self, handler):
        """process_request must still 409 a finished conversation when v2 is off and
        the client already sent history (legacy populate used to skip the guard)."""
        from datetime import datetime

        from fastapi import BackgroundTasks, status

        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.rest_api.models.conversation import Conversation as ConvModel

        request = Mock(spec=AssistantChatRequest)
        request.conversation_id = "conv-abc"
        request.stream = True
        request.background_task = False
        request.interactive_response = None
        request.file_names = []
        request.history = [Mock()]

        conv = Mock(spec=ConvModel)
        conv.id = "conv-abc"
        conv.finished_at = datetime(2026, 1, 1, 12, 0, 0)

        raw_request = Mock()
        raw_request.state.uuid = "req-uuid"

        with (
            patch.object(handler, "_is_conversation_replay_v2_enabled", return_value=False),
            patch(
                "codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id",
                return_value=conv,
            ),
            patch("codemie.rest_api.handlers.assistant_handlers.Ability.can", return_value=True),
            patch("codemie.rest_api.handlers.assistant_handlers.AssistantService.build_agent") as mock_build_agent,
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                handler.process_request(request, BackgroundTasks(), raw_request)

        assert exc_info.value.code == status.HTTP_409_CONFLICT
        mock_build_agent.assert_not_called()
