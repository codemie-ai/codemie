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

"""Integration tests for assistant handlers streaming error handling.

These tests verify that error handling integrates correctly with streaming responses.
The focus is on testing that _format_errors() is called correctly and error fields
are properly included in StreamedGenerationResult final chunks.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from codemie.chains.base import GenerationResult, StreamedGenerationResult
from codemie.core.errors import AgentErrorDetails, ErrorCode, ErrorDetailLevel, ToolErrorDetails
from codemie.rest_api.handlers.assistant_handlers import StandardAssistantHandler
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    user = Mock(spec=User)
    user.id = "user-123"
    user.username = "testuser"
    user.name = "Test User"
    return user


@pytest.fixture
def mock_assistant():
    """Create a mock assistant."""
    assistant = Mock()
    assistant.id = "assistant-123"
    assistant.name = "Test Assistant"
    assistant.project = "test-project"
    return assistant


@pytest.fixture
def generation_result_with_tool_errors():
    """GenerationResult with tool errors."""
    return GenerationResult(
        generated="Partial response",
        time_elapsed=2.5,
        input_tokens_used=150,
        tokens_used=300,
        success=False,
        agent_error=None,
        tool_errors=[
            ToolErrorDetails(
                tool_name="jira_search",
                tool_call_id="call_123",
                error_code=ErrorCode.TOOL_AUTHENTICATION,
                message="401 Unauthorized: Invalid credentials",
                http_status=401,
                details={"integration": "jira"},
            ),
            ToolErrorDetails(
                tool_name="confluence_api",
                tool_call_id="call_456",
                error_code=ErrorCode.TOOL_TIMEOUT,
                message="Request timeout",
                http_status=None,
            ),
        ],
    )


@pytest.fixture
def generation_result_with_agent_error():
    """GenerationResult with agent error."""
    return GenerationResult(
        generated=None,
        time_elapsed=5.0,
        input_tokens_used=200,
        tokens_used=None,
        success=False,
        agent_error=AgentErrorDetails(
            error_code=ErrorCode.AGENT_TOKEN_LIMIT,
            message="Token limit exceeded",
            details={"model": "gpt-4", "max_tokens": 4096},
        ),
        tool_errors=None,
    )


@pytest.fixture
def generation_result_successful():
    """GenerationResult for successful execution."""
    return GenerationResult(
        generated="Success response",
        time_elapsed=1.5,
        input_tokens_used=100,
        tokens_used=200,
        success=True,
        agent_error=None,
        tool_errors=None,
    )


class TestStreamingErrorIntegration:
    """Integration tests for streaming with error handling."""

    def test_format_errors_integration_with_tool_errors_standard_level(
        self, mock_user, mock_assistant, generation_result_with_tool_errors
    ):
        """Test that _format_errors correctly formats tool errors for streaming at STANDARD level."""
        # Setup
        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")

        # Execute
        tool_errors, agent_error = handler._format_errors(
            generation_result_with_tool_errors,
            include_tool_errors=True,
            error_detail_level=ErrorDetailLevel.STANDARD,
        )

        # Verify
        assert agent_error is None
        assert tool_errors is not None
        assert len(tool_errors) == 2

        # Verify STANDARD level formatting
        assert tool_errors[0]["tool_name"] == "jira_search"
        assert tool_errors[0]["tool_call_id"] == "call_123"
        assert tool_errors[0]["error_code"] == "tool_authentication"
        assert tool_errors[0]["http_status"] == 401
        assert "details" not in tool_errors[0]  # Not in STANDARD

    def test_format_errors_integration_with_tool_errors_minimal_level(
        self, mock_user, mock_assistant, generation_result_with_tool_errors
    ):
        """Test that _format_errors correctly formats tool errors at MINIMAL level."""
        # Setup
        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")

        # Execute
        tool_errors, agent_error = handler._format_errors(
            generation_result_with_tool_errors,
            include_tool_errors=True,
            error_detail_level=ErrorDetailLevel.MINIMAL,
        )

        # Verify MINIMAL level formatting
        assert tool_errors is not None
        assert len(tool_errors) == 2
        assert tool_errors[0]["tool_name"] == "jira_search"
        assert tool_errors[0]["error_code"] == "tool_authentication"
        assert tool_errors[0]["message"] == "401 Unauthorized: Invalid credentials"
        # MINIMAL level should not include these fields
        assert "tool_call_id" not in tool_errors[0]
        assert "http_status" not in tool_errors[0]
        assert "details" not in tool_errors[0]

    def test_format_errors_integration_with_tool_errors_full_level(
        self, mock_user, mock_assistant, generation_result_with_tool_errors
    ):
        """Test that _format_errors correctly formats tool errors at FULL level."""
        # Setup
        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")

        # Execute
        tool_errors, agent_error = handler._format_errors(
            generation_result_with_tool_errors,
            include_tool_errors=True,
            error_detail_level=ErrorDetailLevel.FULL,
        )

        # Verify FULL level formatting includes all fields
        assert tool_errors is not None
        assert tool_errors[0]["tool_name"] == "jira_search"
        assert tool_errors[0]["tool_call_id"] == "call_123"
        assert tool_errors[0]["error_code"] == "tool_authentication"
        assert tool_errors[0]["http_status"] == 401
        assert tool_errors[0]["details"] == {"integration": "jira"}  # Included in FULL
        assert "timestamp" in tool_errors[0]  # Included in FULL

    def test_format_errors_integration_with_agent_error(
        self, mock_user, mock_assistant, generation_result_with_agent_error
    ):
        """Test that _format_errors correctly includes agent error."""
        # Setup
        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")

        # Execute
        tool_errors, agent_error = handler._format_errors(
            generation_result_with_agent_error,
            include_tool_errors=True,
            error_detail_level=ErrorDetailLevel.STANDARD,
        )

        # Verify agent error is included
        assert tool_errors is None
        assert agent_error is not None
        assert agent_error.error_code == ErrorCode.AGENT_TOKEN_LIMIT
        assert "Token limit exceeded" in agent_error.message
        assert agent_error.details == {"model": "gpt-4", "max_tokens": 4096}

    def test_format_errors_integration_disabled(self, mock_user, mock_assistant, generation_result_with_tool_errors):
        """Test that _format_errors excludes errors when include_tool_errors=False."""
        # Setup
        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")

        # Execute
        tool_errors, agent_error = handler._format_errors(
            generation_result_with_tool_errors,
            include_tool_errors=False,
            error_detail_level=ErrorDetailLevel.STANDARD,
        )

        # Verify errors are excluded
        assert tool_errors is None
        assert agent_error is None

    def test_format_errors_integration_successful_result(self, mock_user, mock_assistant, generation_result_successful):
        """Test that _format_errors returns None for successful executions."""
        # Setup
        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")

        # Execute
        tool_errors, agent_error = handler._format_errors(
            generation_result_successful,
            include_tool_errors=True,
            error_detail_level=ErrorDetailLevel.STANDARD,
        )

        # Verify no errors returned for successful execution
        assert tool_errors is None
        assert agent_error is None

    def test_streamed_generation_result_with_errors_can_be_serialized(self, generation_result_with_tool_errors):
        """Test that StreamedGenerationResult with error fields can be serialized to JSON."""
        # Setup - simulate what _serve_data does when creating final chunk
        tool_errors_formatted = [
            err.format_for_level(ErrorDetailLevel.STANDARD) for err in generation_result_with_tool_errors.tool_errors
        ]

        # Create final chunk
        final_chunk = StreamedGenerationResult(
            last=True,
            success=generation_result_with_tool_errors.success,
            agent_error=generation_result_with_tool_errors.agent_error,
            tool_errors=tool_errors_formatted,
            time_elapsed=2.5,
        )

        # Execute - serialize to JSON (as _serve_data does)
        json_output = final_chunk.model_dump_json()
        parsed = json.loads(json_output)

        # Verify structure
        assert parsed["last"] is True
        assert parsed["success"] is False
        assert parsed["tool_errors"] is not None
        assert len(parsed["tool_errors"]) == 2
        assert parsed["tool_errors"][0]["tool_name"] == "jira_search"
        assert parsed["tool_errors"][0]["error_code"] == "tool_authentication"
        assert parsed["time_elapsed"] == 2.5

    def test_streamed_generation_result_with_agent_error_serializable(self, generation_result_with_agent_error):
        """Test that StreamedGenerationResult with agent_error can be serialized."""
        # Create final chunk with agent error
        final_chunk = StreamedGenerationResult(
            last=True,
            success=generation_result_with_agent_error.success,
            agent_error=generation_result_with_agent_error.agent_error,
            tool_errors=None,
            time_elapsed=5.0,
        )

        # Serialize to JSON
        json_output = final_chunk.model_dump_json()
        parsed = json.loads(json_output)

        # Verify
        assert parsed["last"] is True
        assert parsed["success"] is False
        assert parsed["agent_error"] is not None
        assert parsed["agent_error"]["error_code"] == "agent_token_limit"
        assert "Token limit exceeded" in parsed["agent_error"]["message"]

    def test_multiple_tool_errors_preserve_order(self, mock_user, mock_assistant):
        """Test that multiple tool errors preserve their order during formatting."""
        # Setup generation result with multiple errors
        generation_result = GenerationResult(
            generated="Response",
            time_elapsed=1.0,
            input_tokens_used=100,
            tokens_used=200,
            success=False,
            agent_error=None,
            tool_errors=[
                ToolErrorDetails(
                    tool_name="first_tool",
                    error_code=ErrorCode.TOOL_TIMEOUT,
                    message="First error",
                ),
                ToolErrorDetails(
                    tool_name="second_tool",
                    error_code=ErrorCode.TOOL_NETWORK_ERROR,
                    message="Second error",
                ),
                ToolErrorDetails(
                    tool_name="third_tool",
                    error_code=ErrorCode.TOOL_VALIDATION,
                    message="Third error",
                ),
            ],
        )

        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")

        # Execute
        tool_errors, _ = handler._format_errors(
            generation_result,
            include_tool_errors=True,
            error_detail_level=ErrorDetailLevel.MINIMAL,
        )

        # Verify order is preserved
        assert len(tool_errors) == 3
        assert tool_errors[0]["tool_name"] == "first_tool"
        assert tool_errors[0]["message"] == "First error"
        assert tool_errors[1]["tool_name"] == "second_tool"
        assert tool_errors[1]["message"] == "Second error"
        assert tool_errors[2]["tool_name"] == "third_tool"
        assert tool_errors[2]["message"] == "Third error"

    def test_both_agent_and_tool_errors_included(self, mock_user, mock_assistant):
        """Test that both agent_error and tool_errors are included when both present."""
        # Setup generation result with both error types
        generation_result = GenerationResult(
            generated="Failed",
            time_elapsed=3.0,
            input_tokens_used=150,
            tokens_used=200,
            success=False,
            agent_error=AgentErrorDetails(
                error_code=ErrorCode.AGENT_INTERNAL_ERROR,
                message="Internal error",
                details={"phase": "execution"},
            ),
            tool_errors=[
                ToolErrorDetails(
                    tool_name="test_tool",
                    error_code=ErrorCode.TOOL_EXECUTION_FAILED,
                    message="Tool failed",
                )
            ],
        )

        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")

        # Execute
        tool_errors, agent_error = handler._format_errors(
            generation_result,
            include_tool_errors=True,
            error_detail_level=ErrorDetailLevel.STANDARD,
        )

        # Verify both are present
        assert agent_error is not None
        assert agent_error.error_code == ErrorCode.AGENT_INTERNAL_ERROR

        assert tool_errors is not None
        assert len(tool_errors) == 1
        assert tool_errors[0]["tool_name"] == "test_tool"
