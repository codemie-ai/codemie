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

"""
LiteLLM friendly messages, status-code mapping, and classification constants.

Error codes are defined in ``codemie.core.errors.ErrorCode`` (``LLM_*`` members).

Official references (keep aligned when changing mappings):
  - Exception mapping (Python): https://docs.litellm.ai/docs/exception_mapping
  - Proxy error format (HTTP):   https://docs.litellm.ai/docs/proxy/error_diagnosis
  - LiteLLM exceptions (source):  https://github.com/BerriAI/litellm/blob/main/litellm/exceptions.py

Proxy HTTP error body shape (LiteLLM returns this on 4xx/5xx):
  {"error": {"message": "...", "type": "<type>", "param": null, "code": "<code>"}}
  - ``message``: Error description; may include exception class name (e.g. litellm.ContentPolicyViolationError).
  - ``type``: OpenAI-style: invalid_request_error, auth_error, rate_limit_error, internal_server_error.
  - ``code``: String, often HTTP status (e.g. "429") or provider-specific (e.g. "invalid_api_key").
"""

from __future__ import annotations

from codemie.core.errors import ErrorCode


# Single source of truth: HTTP status code -> ErrorCode (used by classifier and proxy).
# Aligned with LiteLLM Exception Mapping status codes.
STATUS_TO_ERROR_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.LLM_INVALID_REQUEST,
    401: ErrorCode.LLM_AUTHENTICATION,
    403: ErrorCode.LLM_PERMISSION_DENIED,
    404: ErrorCode.LLM_INVALID_REQUEST,
    408: ErrorCode.LLM_TIMEOUT,
    422: ErrorCode.LLM_INVALID_REQUEST,
    429: ErrorCode.LLM_RATE_LIMITED,
    500: ErrorCode.LLM_INTERNAL_ERROR,
    502: ErrorCode.LLM_TRANSITIVE_ERROR,
    503: ErrorCode.LLM_UNAVAILABLE,
}

# Proxy and callers that need string values use this (derived for DRY).
STATUS_TO_CODE: dict[int, str] = {k: v.value for k, v in STATUS_TO_ERROR_CODE.items()}

# LiteLLM/OpenAI exception class name -> ErrorCode. Authoritative when exception type is available.
# Source: https://docs.litellm.ai/docs/exception_mapping
EXCEPTION_TYPE_TO_ERROR_CODE: dict[str, ErrorCode] = {
    "BadRequestError": ErrorCode.LLM_INVALID_REQUEST,
    "ContextWindowExceededError": ErrorCode.LLM_CONTEXT_LENGTH,
    "ContentPolicyViolationError": ErrorCode.LLM_CONTENT_POLICY,
    "AuthenticationError": ErrorCode.LLM_AUTHENTICATION,
    "PermissionDeniedError": ErrorCode.LLM_PERMISSION_DENIED,
    "NotFoundError": ErrorCode.LLM_INVALID_REQUEST,
    "Timeout": ErrorCode.LLM_TIMEOUT,
    "APITimeoutError": ErrorCode.LLM_TIMEOUT,
    "UnprocessableEntityError": ErrorCode.LLM_INVALID_REQUEST,
    "RateLimitError": ErrorCode.LLM_RATE_LIMITED,
    "BudgetExceededError": ErrorCode.LLM_BUDGET_EXCEEDED,
    "APIConnectionError": ErrorCode.LLM_TRANSITIVE_ERROR,
    "APIError": ErrorCode.LLM_INTERNAL_ERROR,
    "ServiceUnavailableError": ErrorCode.LLM_UNAVAILABLE,
    "InternalServerError": ErrorCode.LLM_INTERNAL_ERROR,
}

# Proxy error body "type" field -> ErrorCode (for classify_from_http_response when body is parsed).
# invalid_request_error can be refined using body message (context window vs content policy).
PROXY_ERROR_TYPE_TO_ERROR_CODE: dict[str, ErrorCode] = {
    "invalid_request_error": ErrorCode.LLM_INVALID_REQUEST,
    "auth_error": ErrorCode.LLM_AUTHENTICATION,
    "rate_limit_error": ErrorCode.LLM_RATE_LIMITED,
    "internal_server_error": ErrorCode.LLM_INTERNAL_ERROR,
}


LITELLM_ERROR_FRIENDLY_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.LLM_BUDGET_EXCEEDED: (
        "Your LLM usage budget has been exceeded. Please contact your administrator to increase the limit."
    ),
    ErrorCode.LLM_RATE_LIMITED: (
        "The LLM service is temporarily overloaded due to rate limiting. Please wait a moment and try again."
    ),
    ErrorCode.LLM_TPM_LIMIT: (
        "The tokens-per-minute limit for the LLM has been reached. Please wait a moment and try again."
    ),
    ErrorCode.LLM_RPM_LIMIT: (
        "The requests-per-minute limit for the LLM has been reached. Please wait a moment and try again."
    ),
    ErrorCode.LLM_UNAVAILABLE: ("The LLM service is currently unavailable. Please try again later or contact support."),
    ErrorCode.LLM_INTERNAL_ERROR: (
        "An internal error occurred in the LLM service. Please try again later or contact support."
    ),
    ErrorCode.LLM_CONTEXT_LENGTH: (
        "The input is too long for the selected model's context window. Please reduce the input size and try again."
    ),
    ErrorCode.LLM_CONTENT_POLICY: (
        "The request was rejected due to the LLM provider's content policy. Please modify your input and try again."
    ),
    ErrorCode.LLM_AUTHENTICATION: ("LLM authentication failed. Please verify your credentials or contact support."),
    ErrorCode.LLM_PERMISSION_DENIED: (
        "Access to the LLM model was denied. Please check your permissions or contact support."
    ),
    ErrorCode.LLM_TIMEOUT: "The LLM request timed out. Please try again.",
    ErrorCode.LLM_TRANSITIVE_ERROR: ("A transient connectivity error occurred with the LLM service. Please try again."),
    ErrorCode.LLM_INVALID_REQUEST: (
        "The LLM request was invalid or could not be processed. Please check your input and try again."
    ),
    ErrorCode.LLM_SCHEMA_VALIDATION: (
        "The workflow output_schema is invalid. Common issues: schema title contains spaces or special "
        "characters (use underscores or hyphens), arrays missing 'items' definition, or objects missing "
        "'additionalProperties: false'. See workflow troubleshooting documentation for validation methods."
    ),
    ErrorCode.LLM_UNKNOWN_ERROR: "An unexpected LLM error occurred. Please try again or contact support.",
}


RATE_LIMIT_TPM_PATTERNS: list[str] = [
    "token",
    "tpm",
    "tokens per minute",
    "tokensperminute",
]

RATE_LIMIT_RPM_PATTERNS: list[str] = [
    "rpm",
    "requests per minute",
    "requestsperminute",
    "request limit",
]

BUDGET_EXCEEDED_PATTERNS: list[str] = [
    "budget_exceeded",
    "budget exceeded",
    "budgetexceedederror",
    "exceeded your budget",
    "max budget",
]
