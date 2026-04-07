# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Error classification constants and user-facing messages for CodeMie.

This module centralizes:
- LiteLLM: mapping between LiteLLM exception types and platform ErrorCode values,
  regex patterns (e.g. LITELLM_ERROR_LOG_PATTERN), and friendly message tables.
- Agent and tool: ErrorCategory, ErrorDetailLevel, ErrorCode for agents and tools;
  HTTP status and message keyword mappings (HTTP_STATUS_TO_ERROR_CODE, ERROR_MESSAGE_PATTERNS);
  agent and tool friendly message tables (AGENT_ERROR_FRIENDLY_MESSAGES, etc.).
"""

from __future__ import annotations

import re
from enum import Enum

from codemie.configs import config


MAX_BUDGET_KEY = 'max_budget'
CURRENT_COST_KEY = 'current_cost'
BUDGET_MESSAGE_KEY = 'budget_message'
GUARDRAILS_REASON = "Hate speech detected (keyword: 'kill all')."


class ErrorCategory(str, Enum):
    """
    High-level categorization of errors in the CodeMie system.

    Separates errors by their source to enable proper handling and debugging.
    """

    AGENT = "agent"  # Agent-level errors (execution, callbacks, etc.)
    TOOL = "tool"  # Tool execution errors (HTTP errors, timeouts, etc.)
    LLM = "llm"  # LLM provider errors (rate limits, content policy, etc.)
    VALIDATION = "validation"  # Input/output validation errors
    LITE_LLM = "lite_llm"  # LLM provider errors
    INTERNAL = "internal"  # Error inside Codemie itself


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

    PLATFORM_ERROR = "platform_error"

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

    # LLM errors
    LLM_BUDGET_EXCEEDED = "llm_budget_exceeded"  # BudgetExceededError
    LLM_TPM_LIMIT = "llm_tpm_limit"  # 429 — tokens-per-minute
    LLM_RPM_LIMIT = "llm_rpm_limit"  # 429 — requests-per-minute
    LLM_INTERNAL_ERROR = "llm_internal_error"  # 500 — InternalServerError / APIError
    LLM_AUTHENTICATION = "llm_authentication"  # 401 — AuthenticationError
    LLM_PERMISSION_DENIED = "llm_permission_denied"  # 403 — PermissionDeniedError
    LLM_TIMEOUT = "llm_timeout"  # 408 — Timeout
    LLM_TRANSITIVE_ERROR = "llm_transitive_error"  # 502 — APIConnectionError / BadGateway
    LLM_UNKNOWN_ERROR = "llm_unknown_error"  # Fallback for unrecognised LLM exceptions

    # LiteLLM exception types (snake_case, 1:1 with exception class names)
    # Reference: https://docs.litellm.ai/docs/exception_mapping
    LITE_LLM_API_CONNECTION_ERROR = "lite_llm_api_connection_error"
    LITE_LLM_API_ERROR = "lite_llm_api_error"
    LITE_LLM_API_TIMEOUT_ERROR = "lite_llm_api_timeout_error"
    LITE_LLM_AUTHENTICATION_ERROR = "lite_llm_authentication_error"
    LITE_LLM_BAD_REQUEST_ERROR = "lite_llm_bad_request_error"
    LITE_LLM_BUDGET_EXCEEDED_ERROR = "lite_llm_budget_exceeded_error"
    LITE_LLM_CONTEXT_WINDOW_EXCEEDED_ERROR = "lite_llm_context_window_exceeded_error"
    LITE_LLM_CONTENT_POLICY_VIOLATION_ERROR = "lite_llm_content_policy_violation_error"
    LITE_LLM_INTERNAL_SERVER_ERROR = "lite_llm_internal_server_error"
    LITE_LLM_NOT_FOUND_ERROR = "lite_llm_not_found_error"
    LITE_LLM_PERMISSION_DENIED_ERROR = "lite_llm_permission_denied_error"
    LITE_LLM_RATE_LIMIT_ERROR = "lite_llm_rate_limit_error"
    LITE_LLM_TPM_LIMIT_ERROR = "lite_llm_tpm_limit_error"  # 429 — tokens-per-minute (Limit type: tokens)
    LITE_LLM_RPM_LIMIT_ERROR = "lite_llm_rpm_limit_error"  # 429 — requests-per-minute (Limit type: requests)
    LITE_LLM_SERVICE_UNAVAILABLE_ERROR = "lite_llm_service_unavailable_error"
    LITE_LLM_TIMEOUT = "lite_llm_timeout"
    LITE_LLM_UNPROCESSABLE_ENTITY_ERROR = "lite_llm_unprocessable_entity_error"

    # Validation errors - input/output validation
    VALIDATION_INPUT = "validation_input"
    VALIDATION_OUTPUT = "validation_output"
    VALIDATION_SCHEMA = "validation_schema"


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

# Error message keywords to error code mapping
ERROR_MESSAGE_PATTERNS: dict[str, ErrorCode] = {
    "timeout": ErrorCode.TOOL_TIMEOUT,
    "network": ErrorCode.TOOL_NETWORK_ERROR,
    "connection": ErrorCode.TOOL_NETWORK_ERROR,
    "validation": ErrorCode.TOOL_VALIDATION,
    "invalid": ErrorCode.TOOL_VALIDATION,
}

# {
#   "error": {
#     "message": "{\"error_type\": \"<ERROR_TYPE>\", \"reason\":
#     \"<Human-readable explanation>\", \"guardrail\": \"<guardrail_name>\",
#     \"stage\": \"pre_call|post_call\", \"version\": \"1.1.1\"}",
#     "type": "None",
#     "param": "None",
#     "code": "400"
#   }
# } <- Example of LiteLLM error log pattern
LITELLM_ERROR_LOG_PATTERN = re.compile(
    r"\{\s*['\"]error['\"]\s*:\s*\{"
    r"(?=(?:(?!\}\s*\}).)*?['\"]message['\"]\s*:)"  # Lookahead: `"message":`
    r"(?=(?:(?!\}\s*\}).)*?['\"]type['\"]\s*:)"  # Lookahead: `"type":`
    r"(?=(?:(?!\}\s*\}).)*?['\"]param['\"]\s*:)"  # Lookahead: `"param":`
    r"(?=(?:(?!\}\s*\}).)*?['\"]code['\"]\s*:)"  # Lookahead: `"code":`
    r".*?\}\s*\}",
    re.DOTALL,
)

# Captures exception name after "litellm." prefix (e.g. APIConnectionError from "litellm.APIConnectionError: ...").
# Source: https://docs.litellm.ai/docs/exception_mapping
LITELLM_EXCEPTION_NAME_PATTERN = re.compile(r"litellm\.(\w+)(?=\s*:|$)")

# LiteLLM/OpenAI exception class name -> ErrorCode (LITE_LLM_* snake_case).
# Source: https://docs.litellm.ai/docs/exception_mapping
LITE_LLM_EXC_TYPE_TO_ERROR_CODE: dict[str, ErrorCode] = {
    "BadRequestError": ErrorCode.LITE_LLM_BAD_REQUEST_ERROR,
    "UnsupportedParamsError": ErrorCode.LITE_LLM_BAD_REQUEST_ERROR,
    "ImageFetchError": ErrorCode.LITE_LLM_BAD_REQUEST_ERROR,
    "InvalidRequestError": ErrorCode.LITE_LLM_BAD_REQUEST_ERROR,
    "APIResponseValidationError": ErrorCode.LITE_LLM_BAD_REQUEST_ERROR,
    "JSONSchemaValidationError": ErrorCode.LITE_LLM_BAD_REQUEST_ERROR,
    "ContextWindowExceededError": ErrorCode.LITE_LLM_CONTEXT_WINDOW_EXCEEDED_ERROR,
    "ContentPolicyViolationError": ErrorCode.LITE_LLM_CONTENT_POLICY_VIOLATION_ERROR,
    "AuthenticationError": ErrorCode.LITE_LLM_AUTHENTICATION_ERROR,
    "PermissionDeniedError": ErrorCode.LITE_LLM_PERMISSION_DENIED_ERROR,
    "NotFoundError": ErrorCode.LITE_LLM_NOT_FOUND_ERROR,
    "Timeout": ErrorCode.LITE_LLM_TIMEOUT,
    "APITimeoutError": ErrorCode.LITE_LLM_API_TIMEOUT_ERROR,
    "UnprocessableEntityError": ErrorCode.LITE_LLM_UNPROCESSABLE_ENTITY_ERROR,
    "RateLimitError": ErrorCode.LITE_LLM_RATE_LIMIT_ERROR,
    "BudgetExceededError": ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR,
    "budget_exceeded": ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR,
    "APIConnectionError": ErrorCode.LITE_LLM_API_CONNECTION_ERROR,
    "APIError": ErrorCode.LITE_LLM_API_ERROR,
    "ServiceUnavailableError": ErrorCode.LITE_LLM_SERVICE_UNAVAILABLE_ERROR,
    "InternalServerError": ErrorCode.LITE_LLM_INTERNAL_SERVER_ERROR,
}

# Message keywords (lowercase) for LiteLLM error secondary classification.
LITELLM_ERROR_KEYWORDS: dict[ErrorCode, list[str]] = {
    ErrorCode.LITE_LLM_CONTEXT_WINDOW_EXCEEDED_ERROR: [
        "context window exceeded",
        "context length exceeded",
        "maximum context length",
    ],
    ErrorCode.LITE_LLM_TPM_LIMIT_ERROR: ["limit type: tokens"],
    ErrorCode.LITE_LLM_RPM_LIMIT_ERROR: ["limit type: requests"],
    ErrorCode.LITE_LLM_RATE_LIMIT_ERROR: [
        "rate limit exceeded",
        "rate limit",
        "limit type: tokens",
        "limit type: requests",
    ],
    ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR: ["exceededbudget"],
    ErrorCode.LITE_LLM_AUTHENTICATION_ERROR: [
        "authentication",
        "unknown api key",
        "invalid api key",
        "unauthorized",
    ],
    ErrorCode.LITE_LLM_PERMISSION_DENIED_ERROR: ["permission denied", "forbidden"],
    ErrorCode.LITE_LLM_NOT_FOUND_ERROR: ["not found", "invalid model"],
    ErrorCode.LITE_LLM_API_TIMEOUT_ERROR: ["timeout", "timed out", "apitimeouterror"],
    ErrorCode.LITE_LLM_UNPROCESSABLE_ENTITY_ERROR: ["unprocessable", "validation error"],
    ErrorCode.LITE_LLM_API_CONNECTION_ERROR: [
        "connection error",
        "apiconnectionerror",
        "connection refused",
    ],
    ErrorCode.LITE_LLM_SERVICE_UNAVAILABLE_ERROR: [
        "service unavailable",
        "serviceunavailable",
    ],
    ErrorCode.LITE_LLM_API_ERROR: ["apierror", "api error"],
    ErrorCode.LITE_LLM_INTERNAL_SERVER_ERROR: [
        "internalservererror",
        "internal server error",
        "internal_server_error",
    ],
    ErrorCode.LITE_LLM_BAD_REQUEST_ERROR: [
        "bad request",
        "invalid request",
        "unsupported params",
        "schema",
        "response_format",
        "json_schema",
    ],
}


AGENT_ERROR_FRIENDLY_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.AGENT_TIMEOUT: config.AGENT_MSG_TIMEOUT,
    ErrorCode.AGENT_TOKEN_LIMIT: config.AGENT_MSG_TOKEN_LIMIT,
    ErrorCode.AGENT_BUDGET_EXCEEDED: config.AGENT_MSG_BUDGET_EXCEEDED,
    ErrorCode.AGENT_CALLBACK_FAILURE: config.AGENT_MSG_CALLBACK_FAILURE,
    ErrorCode.AGENT_NETWORK_ERROR: config.AGENT_MSG_NETWORK_ERROR,
    ErrorCode.AGENT_CONFIGURATION_ERROR: config.AGENT_MSG_CONFIGURATION_ERROR,
    ErrorCode.AGENT_INTERNAL_ERROR: config.AGENT_MSG_INTERNAL_ERROR,
}

LITELLM_ERROR_FRIENDLY_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.LITE_LLM_BAD_REQUEST_ERROR: config.LITELLM_MSG_INVALID_REQUEST,
    ErrorCode.LITE_LLM_CONTEXT_WINDOW_EXCEEDED_ERROR: config.LITELLM_MSG_CONTEXT_LENGTH,
    ErrorCode.LITE_LLM_CONTENT_POLICY_VIOLATION_ERROR: config.LITELLM_MSG_CONTENT_POLICY,
    ErrorCode.LITE_LLM_AUTHENTICATION_ERROR: config.LITELLM_MSG_AUTHENTICATION,
    ErrorCode.LITE_LLM_PERMISSION_DENIED_ERROR: config.LITELLM_MSG_PERMISSION_DENIED,
    ErrorCode.LITE_LLM_NOT_FOUND_ERROR: config.LITELLM_MSG_INVALID_REQUEST,
    ErrorCode.LITE_LLM_TIMEOUT: config.LITELLM_MSG_TIMEOUT,
    ErrorCode.LITE_LLM_API_TIMEOUT_ERROR: config.LITELLM_MSG_TIMEOUT,
    ErrorCode.LITE_LLM_UNPROCESSABLE_ENTITY_ERROR: config.LITELLM_MSG_INVALID_REQUEST,
    ErrorCode.LITE_LLM_RATE_LIMIT_ERROR: config.LITELLM_MSG_RATE_LIMITED,
    ErrorCode.LITE_LLM_TPM_LIMIT_ERROR: config.LITELLM_MSG_TPM_LIMIT,
    ErrorCode.LITE_LLM_RPM_LIMIT_ERROR: config.LITELLM_MSG_RPM_LIMIT,
    ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR: config.LITELLM_MSG_BUDGET_EXCEEDED,
    ErrorCode.LITE_LLM_API_CONNECTION_ERROR: config.LITELLM_MSG_TRANSITIVE_ERROR,
    ErrorCode.LITE_LLM_API_ERROR: config.LITELLM_MSG_INTERNAL_ERROR,
    ErrorCode.LITE_LLM_SERVICE_UNAVAILABLE_ERROR: config.LITELLM_MSG_UNAVAILABLE,
    ErrorCode.LITE_LLM_INTERNAL_SERVER_ERROR: config.LITELLM_MSG_INTERNAL_ERROR,
    ErrorCode.LLM_UNKNOWN_ERROR: config.LITELLM_MSG_UNKNOWN_ERROR,
}
