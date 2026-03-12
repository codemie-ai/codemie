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
Error models for structured error handling in CodeMie agents.

This module provides comprehensive error classification and structured error details
to ensure errors from tools, agents, and LLM providers are properly captured and
exposed to API clients without being absorbed by the model.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ErrorCategory(str, Enum):
    """
    High-level categorization of errors in the CodeMie system.

    Separates errors by their source to enable proper handling and debugging.
    """

    AGENT = "agent"  # Agent-level errors (execution, callbacks, etc.)
    TOOL = "tool"  # Tool execution errors (HTTP errors, timeouts, etc.)
    LLM = "llm"  # LLM provider errors (rate limits, content policy, etc.)
    VALIDATION = "validation"  # Input/output validation errors


class ErrorDetailLevel(str, Enum):
    """
    Error verbosity level for API responses.

    Controls how much detail is included in tool error responses:
    - MINIMAL: Only error code and message (for production clients)
    - STANDARD: Adds HTTP status and tool_call_id (default, for debugging)
    - FULL: Includes all details including timestamp (for development)
    """

    MINIMAL = "minimal"
    STANDARD = "standard"
    FULL = "full"


class ErrorCode(str, Enum):
    """
    Specific error codes for detailed error classification.

    These codes enable clients to programmatically handle different error scenarios
    without parsing error messages.
    """

    # Agent errors - internal execution issues
    AGENT_TIMEOUT = "agent_timeout"
    AGENT_TOKEN_LIMIT = "agent_token_limit"
    AGENT_BUDGET_EXCEEDED = "agent_budget_exceeded"
    AGENT_CALLBACK_FAILURE = "agent_callback_failure"
    AGENT_NETWORK_ERROR = "agent_network_error"
    AGENT_CONFIGURATION_ERROR = "agent_configuration_error"
    AGENT_INTERNAL_ERROR = "agent_internal_error"

    # Tool errors - external service/integration errors
    TOOL_AUTHENTICATION = "tool_authentication"  # 401 Unauthorized
    TOOL_AUTHORIZATION = "tool_authorization"  # 403 Forbidden
    TOOL_NOT_FOUND = "tool_not_found"  # 404 Not Found
    TOOL_CONFLICT = "tool_conflict"  # 409 Conflict
    TOOL_RATE_LIMITED = "tool_rate_limited"  # 429 Too Many Requests
    TOOL_SERVER_ERROR = "tool_server_error"  # 5xx Server Error
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_VALIDATION = "tool_validation"  # Invalid tool input
    TOOL_NETWORK_ERROR = "tool_network_error"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"  # Generic tool failure

    # LLM errors - provider-specific errors
    LLM_CONTENT_POLICY = "llm_content_policy"
    LLM_CONTEXT_LENGTH = "llm_context_length"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_RATE_LIMITED = "llm_rate_limited"
    LLM_INVALID_REQUEST = "llm_invalid_request"
    LLM_SCHEMA_VALIDATION = "llm_schema_validation"

    # LLM errors — extended for LiteLLM error classification
    # Reference: https://docs.litellm.ai/docs/exception_mapping
    LLM_BUDGET_EXCEEDED = "llm_budget_exceeded"  # BudgetExceededError
    LLM_TPM_LIMIT = "llm_tpm_limit"  # 429 — tokens-per-minute
    LLM_RPM_LIMIT = "llm_rpm_limit"  # 429 — requests-per-minute
    LLM_INTERNAL_ERROR = "llm_internal_error"  # 500 — InternalServerError / APIError
    LLM_AUTHENTICATION = "llm_authentication"  # 401 — AuthenticationError
    LLM_PERMISSION_DENIED = "llm_permission_denied"  # 403 — PermissionDeniedError
    LLM_TIMEOUT = "llm_timeout"  # 408 — Timeout
    LLM_TRANSITIVE_ERROR = "llm_transitive_error"  # 502 — APIConnectionError / BadGateway
    LLM_UNKNOWN_ERROR = "llm_unknown_error"  # Fallback for unrecognised LLM exceptions

    # Validation errors - input/output validation
    VALIDATION_INPUT = "validation_input"
    VALIDATION_OUTPUT = "validation_output"
    VALIDATION_SCHEMA = "validation_schema"


class ToolErrorDetails(BaseModel):
    """
    Structured details for a tool execution error.

    Captures comprehensive information about tool failures to enable proper
    debugging and error handling without relying on the LLM to interpret errors.

    Attributes:
        tool_name: Name of the tool that failed
        tool_call_id: Unique identifier for this tool invocation
        error_code: Classified error code for programmatic handling
        message: Human-readable error message
        http_status: HTTP status code if applicable (401, 403, 404, 5xx, etc.)
        details: Additional error context (integration name, action, etc.)
        timestamp: ISO 8601 timestamp of when error occurred
    """

    tool_name: str = Field(..., description="Name of the tool that encountered an error")
    tool_call_id: Optional[str] = Field(None, description="Unique identifier for the tool call")
    error_code: ErrorCode = Field(..., description="Classified error code")
    message: str = Field(..., description="Human-readable error message")
    http_status: Optional[int] = Field(None, description="HTTP status code if applicable")
    details: Optional[dict[str, Any]] = Field(None, description="Additional error context")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 timestamp",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "tool_name": "jira_search",
                "tool_call_id": "call_abc123",
                "error_code": "tool_authentication",
                "message": "401 Unauthorized: Invalid or expired credentials",
                "http_status": 401,
                "details": {"integration": "jira", "action": "search", "project": "PROJ"},
                "timestamp": "2026-01-30T15:30:00Z",
            }
        }

    def to_minimal(self) -> dict[str, Any]:
        """Return minimal error info (code + message only)."""
        return {
            "tool_name": self.tool_name,
            "error_code": self.error_code.value,
            "message": self.message,
        }

    def to_standard(self) -> dict[str, Any]:
        """Return standard error info (+ http_status, tool_call_id)."""
        return {
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "error_code": self.error_code.value,
            "message": self.message,
            "http_status": self.http_status,
        }

    def to_full(self) -> dict[str, Any]:
        """Return full error info (all fields)."""
        return self.model_dump(mode="json")

    def format_for_level(self, level: "ErrorDetailLevel") -> dict[str, Any]:
        """Format error details based on verbosity level."""
        if level == ErrorDetailLevel.MINIMAL:
            return self.to_minimal()
        elif level == ErrorDetailLevel.STANDARD:
            return self.to_standard()
        else:
            return self.to_full()


class AgentErrorDetails(BaseModel):
    """
    Structured details for an agent-level error.

    Captures agent execution failures that are not related to specific tools,
    such as token limits, budget constraints, or internal errors.

    Attributes:
        error_code: Classified error code for programmatic handling
        message: Human-readable error message
        details: Additional error context
        stacktrace: Full stacktrace for debugging (only in debug mode)
    """

    error_code: ErrorCode = Field(..., description="Classified error code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[dict[str, Any]] = Field(None, description="Additional error context")
    stacktrace: Optional[str] = Field(None, description="Full stacktrace for debugging (only in debug/full error mode)")

    class Config:
        json_schema_extra = {
            "example": {
                "error_code": "agent_token_limit",
                "message": "Token limit exceeded: The configured max_output_tokens limit was reached",
                "details": {"model": "gpt-4.1", "max_tokens": 4096, "truncation_reason": "max_tokens"},
                "stacktrace": None,
            }
        }


class ErrorResponse(BaseModel):
    """
    Complete error response with separated agent and tool errors.

    This structure ensures that errors from different sources are clearly
    distinguished and not absorbed by the LLM's response text.

    Attributes:
        category: High-level error category
        agent_error: Agent-level error details (if applicable)
        tool_errors: List of tool errors (multiple tools can fail)
        is_recoverable: Whether the error can be retried or recovered from
    """

    category: ErrorCategory = Field(..., description="High-level error category")
    agent_error: Optional[AgentErrorDetails] = Field(None, description="Agent-level error details")
    tool_errors: Optional[list[ToolErrorDetails]] = Field(None, description="Tool error details")
    is_recoverable: bool = Field(False, description="Whether the error is recoverable")

    class Config:
        json_schema_extra = {
            "example": {
                "category": "tool",
                "agent_error": None,
                "tool_errors": [
                    {
                        "tool_name": "jira_search",
                        "tool_call_id": "call_abc123",
                        "error_code": "tool_authentication",
                        "message": "401 Unauthorized: Invalid credentials",
                        "http_status": 401,
                        "details": {"integration": "jira"},
                        "timestamp": "2026-01-30T15:30:00Z",
                    }
                ],
                "is_recoverable": True,
            }
        }


# HTTP status code to error code mapping
HTTP_STATUS_TO_ERROR_CODE: dict[int, ErrorCode] = {
    # Client errors (4xx)
    401: ErrorCode.TOOL_AUTHENTICATION,
    403: ErrorCode.TOOL_AUTHORIZATION,
    404: ErrorCode.TOOL_NOT_FOUND,
    409: ErrorCode.TOOL_CONFLICT,
    429: ErrorCode.TOOL_RATE_LIMITED,
    # Server errors (5xx)
    500: ErrorCode.TOOL_SERVER_ERROR,
    501: ErrorCode.TOOL_SERVER_ERROR,
    502: ErrorCode.TOOL_SERVER_ERROR,
    503: ErrorCode.TOOL_SERVER_ERROR,
    504: ErrorCode.TOOL_SERVER_ERROR,
    505: ErrorCode.TOOL_SERVER_ERROR,
    506: ErrorCode.TOOL_SERVER_ERROR,
    507: ErrorCode.TOOL_SERVER_ERROR,
    508: ErrorCode.TOOL_SERVER_ERROR,
    509: ErrorCode.TOOL_SERVER_ERROR,
    510: ErrorCode.TOOL_SERVER_ERROR,
    511: ErrorCode.TOOL_SERVER_ERROR,
}


def _classify_by_http_status(status_code: int) -> ErrorCode:
    """
    Classify error based on HTTP status code only.

    Uses dictionary mapping for efficient lookup of common HTTP status codes.
    This is the primary classification method - message-based classification is only
    used as a fallback when status code classification yields a generic result.

    Args:
        status_code: HTTP status code

    Returns:
        ErrorCode enum value corresponding to the status code
    """
    # Try direct lookup first
    if status_code in HTTP_STATUS_TO_ERROR_CODE:
        return HTTP_STATUS_TO_ERROR_CODE[status_code]

    # Fallback for any 5xx not in the dict
    if 500 <= status_code < 600:
        return ErrorCode.TOOL_SERVER_ERROR

    # Default for unknown status codes
    return ErrorCode.TOOL_EXECUTION_FAILED


# Error message keywords to error code mapping
ERROR_MESSAGE_PATTERNS: dict[str, ErrorCode] = {
    "timeout": ErrorCode.TOOL_TIMEOUT,
    "network": ErrorCode.TOOL_NETWORK_ERROR,
    "connection": ErrorCode.TOOL_NETWORK_ERROR,
    "validation": ErrorCode.TOOL_VALIDATION,
    "invalid": ErrorCode.TOOL_VALIDATION,
}


def _classify_by_message(error_message: str) -> ErrorCode:
    """
    Classify error based on error message content.

    Checks for keywords in the error message to determine error type.

    Args:
        error_message: Error message text

    Returns:
        ErrorCode enum value based on message content
    """
    error_lower = error_message.lower()

    # Check each pattern
    for keyword, error_code in ERROR_MESSAGE_PATTERNS.items():
        if keyword in error_lower:
            return error_code

    # Default for unrecognized error messages
    return ErrorCode.TOOL_EXECUTION_FAILED


def classify_http_error(status_code: int, error_message: str = "") -> ErrorCode:
    """
    Classify HTTP status codes into appropriate ErrorCode values.

    Uses a two-stage classification approach:
    1. Primary: Classification by HTTP status code using dictionary mapping
    2. Fallback: If status yields generic result, analyze error message content

    Args:
        status_code: HTTP status code
        error_message: Optional error message for additional context

    Returns:
        ErrorCode enum value corresponding to the status code or message
    """
    # First, try classification by HTTP status
    error_code = _classify_by_http_status(status_code)

    # If we got a generic error and have a message, try message-based classification
    if error_code == ErrorCode.TOOL_EXECUTION_FAILED and error_message:
        error_code = _classify_by_message(error_message)

    return error_code


def is_recoverable_error(error_code: ErrorCode) -> bool:
    """
    Determine if an error is recoverable (can be retried).

    Args:
        error_code: Error code to check

    Returns:
        True if the error can be retried, False otherwise
    """
    recoverable_errors = {
        ErrorCode.TOOL_TIMEOUT,
        ErrorCode.TOOL_RATE_LIMITED,
        ErrorCode.TOOL_SERVER_ERROR,
        ErrorCode.TOOL_NETWORK_ERROR,
        ErrorCode.AGENT_NETWORK_ERROR,
        ErrorCode.LLM_RATE_LIMITED,
        ErrorCode.LLM_UNAVAILABLE,
    }
    return error_code in recoverable_errors
