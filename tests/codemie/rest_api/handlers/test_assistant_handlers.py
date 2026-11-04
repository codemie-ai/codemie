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

from codemie.chains.base import GenerationResult, StreamedGenerationResult
from codemie.core.errors import AgentErrorDetails, ErrorCode, ErrorDetailLevel, ToolErrorDetails
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.handlers.assistant_handlers import ChatHistoryData, StandardAssistantHandler
from codemie.rest_api.utils.client_context import ClientSource, clear_client_source, set_client_source
from codemie.rest_api.routers.utils import run_assistant_in_thread_pool
from codemie.rest_api.security.user import User
from codemie.service.spend.origin_context import current_origin, spend_origin
from codemie.service.spend.traffic_class import TrafficOrigin


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


class TestReadStreamedChunk:
    """Tests for StandardAssistantHandler._read_streamed_chunk() method (F10-e)."""

    def test_read_streamed_chunk_ignores_malformed_json_line_instead_of_raising(self):
        """A single malformed line on the queue must not kill the whole stream
        generator — it should be skipped, keeping the previously accumulated
        response, not raise json.JSONDecodeError uncaught."""
        previous_response = StreamedGenerationResult(generated="previous")

        result = StandardAssistantHandler._read_streamed_chunk("{not valid json", previous_response, a2ui_envelopes=[])

        assert result is previous_response

    def test_read_streamed_chunk_still_parses_valid_json(self):
        previous_response = StreamedGenerationResult(generated="previous")
        value = StreamedGenerationResult(generated="new").model_dump_json()

        result = StandardAssistantHandler._read_streamed_chunk(value, previous_response, a2ui_envelopes=[])

        assert result.generated == "new"


class TestBuildTimeoutResult:
    @pytest.fixture
    def handler(self):
        assistant = Mock()
        assistant.id = "assistant-123"
        assistant.project = "test-project"
        user = Mock(spec=User)
        user.id = "user-123"
        return StandardAssistantHandler(assistant=assistant, user=user, request_uuid="req-uuid")

    def test_timeout_result_hides_stacktrace_in_payload(self, handler, monkeypatch):
        """R3-CR-010: TimeoutError classifies to ErrorCategory.AGENT whose stacktrace
        must honor HIDE_AGENT_STREAMING_EXCEPTIONS and stay inside error_details too."""
        from codemie.configs import config as global_config

        monkeypatch.setattr(global_config, "HIDE_AGENT_STREAMING_EXCEPTIONS", True)
        generator_queue = Mock()

        result = handler._build_timeout_result(
            generator_queue,
            time(),
            include_tool_errors=True,
            error_detail_level=ErrorDetailLevel.STANDARD,
            log_context="[TEST]",
        )

        assert result.success is False
        assert result.agent_error.stacktrace is None
        assert result.error_details["stacktrace"] is None

    def test_timeout_result_redacts_stacktrace_even_when_not_hiding(self, handler, monkeypatch):
        """Tracebacks never reach the client, independent of HIDE_AGENT_STREAMING_EXCEPTIONS."""
        from codemie.configs import config as global_config

        monkeypatch.setattr(global_config, "HIDE_AGENT_STREAMING_EXCEPTIONS", False)
        generator_queue = Mock()

        result = handler._build_timeout_result(
            generator_queue,
            time(),
            include_tool_errors=True,
            error_detail_level=ErrorDetailLevel.STANDARD,
            log_context="[TEST]",
        )

        assert result.agent_error.stacktrace is None
        assert result.error_details["stacktrace"] is None
        assert result.execution_error == "agent_timeout"

    def test_queue_poll_timeout_clamps_zero_and_negative_to_one(self, handler, monkeypatch):
        """R3-CR-007: 0 must not busy-loop, negative must not raise ValueError."""
        from codemie.service.dynamic_config_service import DynamicConfigService

        monkeypatch.setattr(DynamicConfigService, "get_typed_value_safe", staticmethod(lambda *a, **kw: 0))
        assert handler._get_queue_poll_timeout_seconds() == 1

        monkeypatch.setattr(DynamicConfigService, "get_typed_value_safe", staticmethod(lambda *a, **kw: -5))
        assert handler._get_queue_poll_timeout_seconds() == 1

    def test_queue_poll_timeout_passes_through_positive(self, handler, monkeypatch):
        from codemie.service.dynamic_config_service import DynamicConfigService

        monkeypatch.setattr(DynamicConfigService, "get_typed_value_safe", staticmethod(lambda *a, **kw: 30))
        assert handler._get_queue_poll_timeout_seconds() == 30

    def test_a2a_stream_abandoned_producer_yields_timeout_result(self, monkeypatch):
        """R3-CR-011: A2A _drain_stream must not block forever on queue.get()."""
        import asyncio

        from codemie.core.models import AssistantChatRequest
        from codemie.core.thread import ThreadedGenerator
        from codemie.rest_api.handlers.assistant_handlers import A2AAssistantHandler

        assistant = Mock()
        assistant.id = "assistant-123"
        assistant.project = "test-project"
        assistant.agent_card = Mock()
        assistant.agent_card.name = "remote-agent"
        user = Mock(spec=User)
        user.id = "user-123"
        handler = A2AAssistantHandler.__new__(A2AAssistantHandler)
        StandardAssistantHandler.__init__(handler, assistant, user, "req-uuid")
        handler.agent_card = assistant.agent_card

        generator_queue = ThreadedGenerator()  # never fed, never closed
        request = AssistantChatRequest(text="hi", stream=True)

        from codemie.service.dynamic_config_service import DynamicConfigService

        monkeypatch.setattr(DynamicConfigService, "get_typed_value_safe", staticmethod(lambda *a, **kw: 1))

        async def drain():
            with patch.object(handler, "save_chat_history"):
                chunks = []
                async for chunk in handler._drain_stream(generator_queue, request, time()):
                    chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
                    if chunks:
                        break
                return chunks

        chunks = asyncio.run(drain())
        assert len(chunks) == 1
        assert '"success":false' in chunks[0]
        assert "timed out" in chunks[0]


class TestInitHasNoBillingUser:
    def test_init_has_no_billing_user_attribute_and_user_drives_build_agent(self):
        assistant = Mock(id="assistant-123", project="test-project")
        user = Mock(spec=User, id="user-123")

        handler = StandardAssistantHandler(assistant, user, "req-1")

        assert not hasattr(handler, "billing_user")
        assert handler.user is user


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

            call = mock_service.upsert_chat_history.call_args
            params = call.kwargs.get("params") or (call.args[0] if call.args else None)
            assert params is not None
            assert params.background_tasks is sentinel_background_tasks

    def test_background_tasks_defaults_to_none_before_process_request(self, handler):
        """Handler instances start with no background_tasks until process_request sets it"""
        assert handler.background_tasks is None

    @pytest.fixture(autouse=True)
    def reset_client_source_after_test(self):
        yield
        clear_client_source()

    @pytest.mark.parametrize("source", [ClientSource.MS_TEAMS_BOT, ClientSource.OTHER])
    def test_save_chat_history_forwards_client_source_snapshotted_at_construction(
        self, chat_history_data_save_true, source
    ):
        """client_source is snapshotted at __init__ time and survives unchanged
        into upsert_chat_history, even though save_chat_history runs later and
        may run in a copy_context() that has lost the ContextVar value
        (see AssistantRequestHandler.__init__ inline comment)."""
        set_client_source(source)
        try:
            user = Mock(spec=User, id="user-123")
            assistant = Mock(id="assistant-123", project="test-project")
            fresh_handler = StandardAssistantHandler(assistant, user, "request-uuid")
        finally:
            clear_client_source()  # simulate the ContextVar reverting after this "request"

        with (
            patch("codemie.service.llm_service.utils.set_llm_context"),
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService") as mock_service,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock())

            fresh_handler.save_chat_history(chat_history_data_save_true)

            call = mock_service.upsert_chat_history.call_args
            params = call.kwargs.get("params") or (call.args[0] if call.args else None)
            assert params is not None
            assert params.client_source is source


class TestExecutionModelReaders:
    """History replay mode and the saved turn read the model build_agent resolves (agent.llm_model)."""

    @pytest.fixture
    def handler(self):
        user = Mock(spec=User, id="user-123")
        assistant = Mock(id="assistant-123", project="test-project", llm_model_type="own-model")
        return StandardAssistantHandler(assistant, user, "request-uuid")

    def test_history_projection_mode_uses_resolved_model(self, handler):
        request = AssistantChatRequest(text="Hello", llm_model="picked-model", conversation_id="conv-1")
        with (
            patch(
                "codemie.rest_api.handlers.assistant_handlers.BedrockOrchestratorService.is_bedrock_assistant",
                return_value=False,
            ),
            patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.resolve_execution_model",
                return_value="react-model",
            ) as resolve,
            patch(
                "codemie.rest_api.handlers.assistant_handlers.llm_service.get_react_llms",
                return_value=["react-model"],
            ),
        ):
            mode = handler._resolve_history_projection_mode(request)

        resolve.assert_called_once_with(handler.assistant, request)
        assert mode == "text_ledger"

    def test_save_chat_history_passes_resolved_model(self, handler):
        request = AssistantChatRequest(text="Hello", llm_model="picked-model")
        data = ChatHistoryData(execution_start=time(), request=request, response="Response", thoughts=[])
        with (
            patch("codemie.service.llm_service.utils.set_llm_context"),
            patch(
                "codemie.rest_api.handlers.assistant_handlers.BedrockOrchestratorService.is_bedrock_assistant",
                return_value=False,
            ),
            patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.resolve_execution_model",
                return_value="resolved-model",
            ),
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService") as mock_service,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock())
            handler.save_chat_history(data)

        params = mock_service.upsert_chat_history.call_args.args[0]
        assert params.llm_model == "resolved-model"
        assert request.llm_model == "picked-model"

    def test_save_chat_history_sets_llm_context_with_resolved_model(self, handler):
        request = AssistantChatRequest(text="Hello", llm_model="picked-model")
        data = ChatHistoryData(execution_start=time(), request=request, response="Response", thoughts=[])
        with (
            patch("codemie.service.llm_service.utils.set_llm_context") as set_ctx,
            patch(
                "codemie.rest_api.handlers.assistant_handlers.BedrockOrchestratorService.is_bedrock_assistant",
                return_value=False,
            ),
            patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.resolve_execution_model",
                return_value="resolved-model",
            ),
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService"),
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock(routing=None))
            handler.save_chat_history(data)

        assert set_ctx.call_args.args[3] == "resolved-model"

    def test_execution_model_falls_back_when_project_allows_no_model(self, handler):
        from codemie.core.exceptions import ModelNotWhitelistedException

        request = AssistantChatRequest(text="Hello", llm_model="picked-model")
        with (
            patch(
                "codemie.rest_api.handlers.assistant_handlers.BedrockOrchestratorService.is_bedrock_assistant",
                return_value=False,
            ),
            patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.resolve_execution_model",
                side_effect=ModelNotWhitelistedException("picked-model", "test-project", details="d"),
            ),
        ):
            assert handler._execution_llm_model(request) == "picked-model"

    @pytest.mark.parametrize("remote", ["a2a", "bedrock"])
    def test_remote_assistant_turn_records_the_raw_pick_without_policy(self, handler, remote):
        """A2A and Bedrock assistants do not run on a platform model: no policy, the raw pick is recorded."""
        from codemie.rest_api.models.assistant import AssistantType

        if remote == "a2a":
            handler.assistant.type = AssistantType.A2A
        request = AssistantChatRequest(text="Hello", llm_model="picked-model")
        data = ChatHistoryData(execution_start=time(), request=request, response="Response", thoughts=[])
        with (
            patch("codemie.service.llm_service.utils.set_llm_context") as set_ctx,
            patch(
                "codemie.rest_api.handlers.assistant_handlers.BedrockOrchestratorService.is_bedrock_assistant",
                return_value=remote == "bedrock",
            ),
            patch("codemie.rest_api.handlers.assistant_handlers.AssistantService.resolve_execution_model") as resolve,
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService") as mock_service,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock(routing=None))
            handler.save_chat_history(data)

        resolve.assert_not_called()
        assert mock_service.upsert_chat_history.call_args.args[0].llm_model == "picked-model"
        assert set_ctx.call_args.args[3] == "picked-model"

    def test_built_bedrock_agent_turn_records_the_raw_pick(self, handler):
        """A built Bedrock agent carries llm_model "": the saved turn still records the raw pick, not "" or a policy model."""
        request = AssistantChatRequest(text="Hello", llm_model="picked-model")
        data = ChatHistoryData(execution_start=time(), request=request, response="Response", thoughts=[])
        with (
            patch("codemie.service.llm_service.utils.set_llm_context") as set_ctx,
            patch(
                "codemie.rest_api.handlers.assistant_handlers.BedrockOrchestratorService.is_bedrock_assistant",
                return_value=True,
            ),
            patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.build_agent",
                return_value=Mock(llm_model=""),
            ),
            patch("codemie.rest_api.handlers.assistant_handlers.AssistantService.resolve_execution_model") as resolve,
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService") as mock_service,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock(routing=None))
            handler._build_agent(assistant=handler.assistant, request=request, user=handler.user)
            handler.save_chat_history(data)

        resolve.assert_not_called()
        assert mock_service.upsert_chat_history.call_args.args[0].llm_model == "picked-model"
        assert set_ctx.call_args.args[3] == "picked-model"

    def test_replay_and_saved_turn_resolve_once_per_request(self, handler):
        request = AssistantChatRequest(text="Hello", llm_model="picked-model", conversation_id="conv-1")
        data = ChatHistoryData(execution_start=time(), request=request, response="Response", thoughts=[])
        with (
            patch("codemie.service.llm_service.utils.set_llm_context"),
            patch(
                "codemie.rest_api.handlers.assistant_handlers.BedrockOrchestratorService.is_bedrock_assistant",
                return_value=False,
            ),
            patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.resolve_execution_model",
                return_value="resolved-model",
            ) as resolve,
            patch("codemie.rest_api.handlers.assistant_handlers.llm_service.get_react_llms", return_value=[]),
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService"),
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock(routing=None))
            handler._resolve_history_projection_mode(request)
            handler.save_chat_history(data)
            handler.save_chat_history(data)

        resolve.assert_called_once_with(handler.assistant, request)

    def test_saved_turn_uses_the_built_agent_model(self, handler):
        request = AssistantChatRequest(text="Hello", llm_model="picked-model")
        data = ChatHistoryData(execution_start=time(), request=request, response="Response", thoughts=[])
        agent = Mock(llm_model="agent-model")
        with (
            patch("codemie.service.llm_service.utils.set_llm_context") as set_ctx,
            patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.build_agent", return_value=agent
            ) as build,
            patch("codemie.rest_api.handlers.assistant_handlers.AssistantService.resolve_execution_model") as resolve,
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService") as mock_service,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock(routing=None))
            assert handler._build_agent(assistant=handler.assistant, request=request) is agent
            handler.save_chat_history(data)

        build.assert_called_once_with(assistant=handler.assistant, request=request)
        resolve.assert_not_called()
        assert mock_service.upsert_chat_history.call_args.args[0].llm_model == "agent-model"
        assert set_ctx.call_args.args[3] == "agent-model"


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


class TestThoughtFinalizationOnSuccess:
    """finalize_thoughts is applied to the callback's raw thoughts before persisting."""

    @pytest.fixture
    def handler(self):
        assistant = Mock()
        assistant.id = "assistant-123"
        assistant.project = "test-project"
        user = Mock(spec=User)
        user.id = "user-123"
        return StandardAssistantHandler(assistant=assistant, user=user, request_uuid="req-uuid")

    @staticmethod
    def _mock_agent():
        agent = Mock()
        agent.generate.return_value = GenerationResult(
            generated="Some response",
            time_elapsed=1.0,
            input_tokens_used=10,
            tokens_used=20,
            success=True,
            agent_error=None,
            tool_errors=None,
        )
        agent.get_thoughts_from_callback.return_value = [{'id': 't1', 'in_progress': True, 'children': []}]
        return agent

    def test_handle_sync_finalizes_stale_in_progress_thought_on_success(self, handler):
        raw_request = Mock()
        raw_request.state.uuid = "req-uuid"

        with (
            patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.build_agent",
                return_value=self._mock_agent(),
            ),
            patch("codemie.rest_api.handlers.assistant_handlers.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.assistant_handlers.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
        ):
            handler._handle_sync(request=AssistantChatRequest(text="run"), raw_request=raw_request, execution_start=0.0)

        persisted = mock_save.call_args[0][0].thoughts
        assert persisted[0]['in_progress'] is False

    def test_background_generate_finalizes_stale_in_progress_thought_on_success(self, handler):
        raw_request = Mock()
        raw_request.state.uuid = "req-uuid"

        with (
            patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.build_agent",
                return_value=self._mock_agent(),
            ),
            patch("codemie.rest_api.handlers.assistant_handlers.extract_custom_headers", return_value={}),
            patch("codemie.rest_api.handlers.assistant_handlers.set_disable_prompt_cache"),
            patch.object(handler, "save_chat_history") as mock_save,
        ):
            handler._background_generate(
                request=AssistantChatRequest(text="run"),
                background_task_id="task-1",
                raw_request=raw_request,
                execution_start=0.0,
            )

        persisted = mock_save.call_args[0][0].thoughts
        assert persisted[0]['in_progress'] is False


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


class TestABackgroundRunCarriesTheRequestsOrigin:
    """The worker builds its agent on a pool thread that copies no context, and the background task
    runs in the outer request context where the router's origin block never applied - so the origin
    reaches the worker only through the snapshot the handler takes."""

    @pytest.fixture
    def handler(self):
        user = Mock(spec=User, id="user-123")
        assistant = Mock(id="assistant-123", project="test-project")
        assistant.name = "Test assistant"
        return StandardAssistantHandler(assistant, user, "request-uuid")

    def test_the_worker_is_run_inside_a_snapshot_of_the_publishing_context(self, handler):
        from concurrent.futures import ThreadPoolExecutor

        background_tasks = Mock()
        with (
            patch("codemie.rest_api.handlers.assistant_handlers.BackgroundTasksService") as service,
            patch("codemie.rest_api.handlers.assistant_handlers.BackgroundTaskRequest"),
            patch("codemie.rest_api.handlers.assistant_handlers.AssistantDetails"),
            spend_origin(TrafficOrigin.CONVERSATION),
        ):
            service.return_value.save.return_value = "task-1"
            handler._handle_background(
                AssistantChatRequest(text="Hello"), background_tasks, raw_request=Mock(), execution_start=time()
            )

        submitted = background_tasks.add_task.call_args.args
        assert submitted[0] is run_assistant_in_thread_pool
        assert submitted[2] == handler._background_generate
        run_in_snapshot = submitted[1]
        # A bare pool thread sees nothing; run through the snapshot it sees what the request published.
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(current_origin).result() is None
            assert pool.submit(run_in_snapshot, current_origin).result() is TrafficOrigin.CONVERSATION
