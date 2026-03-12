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
Classifier that maps LiteLLM / LLM API errors to ``ErrorCode``.

Classification order (most to least reliable): exception type name, status_code,
message pattern matching. No dependency on the litellm Python package.

Reference: https://docs.litellm.ai/docs/exception_mapping
"""

from __future__ import annotations

from litellm.exceptions import ContentPolicyViolationError, BudgetExceededError
from codemie.configs import logger
from codemie.core.errors import ErrorCode
from codemie.core.litellm_error_constants import (
    BUDGET_EXCEEDED_PATTERNS,
    EXCEPTION_TYPE_TO_ERROR_CODE,
    LITELLM_ERROR_FRIENDLY_MESSAGES,
    PROXY_ERROR_TYPE_TO_ERROR_CODE,
    RATE_LIMIT_RPM_PATTERNS,
    RATE_LIMIT_TPM_PATTERNS,
    STATUS_TO_ERROR_CODE,
)
from openai import BadRequestError

# Extend type with future used LiteLLM exception types
LiteLLMException = BadRequestError | BudgetExceededError


def _match_any_pattern(text: str, patterns: list[str]) -> bool:
    """Check whether *text* contains any of the *patterns*."""
    text_lower = text.lower()
    return any(p in text_lower for p in patterns)


def _classify_rate_limit_subtype(error_message: str) -> ErrorCode:
    """Distinguish TPM / RPM / generic rate-limit by message content."""
    if _match_any_pattern(error_message, RATE_LIMIT_TPM_PATTERNS):
        return ErrorCode.LLM_TPM_LIMIT
    if _match_any_pattern(error_message, RATE_LIMIT_RPM_PATTERNS):
        return ErrorCode.LLM_RPM_LIMIT
    return ErrorCode.LLM_RATE_LIMITED


def _refine_error_code(code: ErrorCode | None, error_message: str) -> ErrorCode | None:
    """Apply message-based refinement for certain error types.

    Args:
        code: Initial error code from exception type or status code
        error_message: Error message to analyze for refinement

    Returns:
        Refined error code or original code if no refinement applies
    """
    if code is None:
        return None

    if code == ErrorCode.LLM_RATE_LIMITED:
        return _classify_rate_limit_subtype(error_message)

    if code == ErrorCode.LLM_INVALID_REQUEST:
        refined = _refine_invalid_request_from_message(error_message)
        return refined if refined else code

    return code


def _classify_by_exception_type(exc: Exception) -> ErrorCode | None:
    """Classification via exception class name (LiteLLM/OpenAI). Most reliable when available."""
    name = type(exc).__name__
    code = EXCEPTION_TYPE_TO_ERROR_CODE.get(name)
    return _refine_error_code(code, str(exc))


def _classify_by_status_code(exc: Exception) -> ErrorCode | None:
    """Classification via ``status_code`` attribute (e.g. from HTTP response). Uses shared STATUS_TO_ERROR_CODE."""
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        return None

    code = STATUS_TO_ERROR_CODE.get(status_code)
    return _refine_error_code(code, str(exc))


def _is_context_window_error(msg_lower: str) -> bool:
    """Check if message indicates context window exceeded."""
    return "context" in msg_lower and ("window" in msg_lower or "length" in msg_lower)


def _is_content_policy_error(msg_lower: str) -> bool:
    """Check if message indicates content policy violation."""
    return "content" in msg_lower and "policy" in msg_lower


def _is_rate_limit_error(msg_lower: str) -> bool:
    """Check if message indicates rate limiting."""
    return "rate" in msg_lower and "limit" in msg_lower


def _is_unavailable_error(msg_lower: str) -> bool:
    """Check if message indicates service unavailable."""
    return "service unavailable" in msg_lower or "503" in msg_lower


def _is_transitive_error(msg_lower: str) -> bool:
    """Check if message indicates transitive/connection error."""
    return "connection" in msg_lower or "bad gateway" in msg_lower or "502" in msg_lower


def _is_authentication_error(msg_lower: str) -> bool:
    """Check if message indicates authentication failure."""
    return "authentication" in msg_lower or "unauthorized" in msg_lower or "401" in msg_lower


def _classify_by_message(error_message: str) -> ErrorCode | None:
    """Classification by scanning the error message text."""
    msg_lower = error_message.lower()

    if _match_any_pattern(msg_lower, BUDGET_EXCEEDED_PATTERNS):
        return ErrorCode.LLM_BUDGET_EXCEEDED

    if _is_schema_validation_error(msg_lower):
        return ErrorCode.LLM_SCHEMA_VALIDATION

    if _is_rate_limit_error(msg_lower):
        return _classify_rate_limit_subtype(error_message)

    if _is_context_window_error(msg_lower):
        return ErrorCode.LLM_CONTEXT_LENGTH

    if _is_content_policy_error(msg_lower):
        return ErrorCode.LLM_CONTENT_POLICY

    if "timeout" in msg_lower:
        return ErrorCode.LLM_TIMEOUT

    if _is_unavailable_error(msg_lower):
        return ErrorCode.LLM_UNAVAILABLE

    if _is_transitive_error(msg_lower):
        return ErrorCode.LLM_TRANSITIVE_ERROR

    if _is_authentication_error(msg_lower):
        return ErrorCode.LLM_AUTHENTICATION

    return None


def is_litellm_exception(exc: Exception) -> bool:
    """Return True if *exc* appears to be an LLM/LiteLLM API error (module or message)."""
    exc_module = type(exc).__module__ or ""
    if "litellm" in exc_module or "openai" in exc_module:
        return True

    error_str = str(exc).lower()
    if "litellm" in error_str:
        return True

    return _match_any_pattern(error_str, BUDGET_EXCEEDED_PATTERNS)


def _refine_invalid_request_from_message(body_message: str) -> ErrorCode | None:
    """Return refined error code if message matches budget, schema, context, or content errors; else None."""
    msg_lower = body_message.lower()

    # Check for budget exceeded errors (can come as BadRequestError with 400 status)
    if _match_any_pattern(msg_lower, BUDGET_EXCEEDED_PATTERNS):
        return ErrorCode.LLM_BUDGET_EXCEEDED

    # Check for schema validation errors
    if _is_schema_validation_error(msg_lower):
        return ErrorCode.LLM_SCHEMA_VALIDATION

    if "contextwindowexceedederror" in msg_lower or (
        "context" in msg_lower and ("window" in msg_lower or "length" in msg_lower)
    ):
        return ErrorCode.LLM_CONTEXT_LENGTH
    if "contentpolicyviolationerror" in msg_lower or ("content" in msg_lower and "policy" in msg_lower):
        return ErrorCode.LLM_CONTENT_POLICY
    return None


def _is_schema_validation_error(msg_lower: str) -> bool:
    """Detect if error message indicates schema validation failure.

    Uses a general approach to catch ANY schema validation error from Azure OpenAI/LiteLLM,
    not just specific patterns. If the error mentions schema-related terms, it's likely
    a schema validation issue.

    Args:
        msg_lower: Lowercased error message

    Returns:
        True if this appears to be a schema validation error
    """
    # General schema-related keywords that indicate schema validation errors
    schema_keywords = [
        "schema",  # Catches "invalid schema", "schema validation", "schema error", etc.
        "response_format",  # Azure OpenAI structured output parameter
        "json_schema",  # OpenAI API parameter for schema
    ]
    return any(keyword in msg_lower for keyword in schema_keywords)


def _refine_code_from_body(code: ErrorCode, body_message: str | None) -> ErrorCode:
    """Apply message-based refinements for rate-limit and invalid-request codes."""
    if not body_message:
        return code
    if code == ErrorCode.LLM_RATE_LIMITED:
        return _classify_rate_limit_subtype(body_message)
    if code == ErrorCode.LLM_INVALID_REQUEST:
        refined = _refine_invalid_request_from_message(body_message)
        if refined is not None:
            return refined
    return code


def _resolve_error_code(exc: Exception) -> ErrorCode:
    """Resolve ErrorCode from exception: type name -> status_code -> message. Used by classify_litellm_exception."""
    logger.debug(exc)
    error_code = _classify_by_exception_type(exc)
    if error_code is not None:
        return error_code
    error_code = _classify_by_status_code(exc)
    if error_code is not None:
        return error_code
    error_code = _classify_by_message(str(exc))
    if error_code is not None:
        return error_code
    return ErrorCode.LLM_UNKNOWN_ERROR


def classify_from_http_response(
    status_code: int,
    body_message: str | None = None,
    body_type: str | None = None,
) -> ErrorCode:
    """Classify an LLM error from proxy HTTP response (status and optional parsed body).

    Used when we have status (and optionally error body type/message) but no Python exception.
    Order: body_type -> status_code -> body_message patterns -> LLM_UNKNOWN_ERROR.

    Args:
        status_code: HTTP status code (e.g. 429).
        body_message: Optional ``error.message`` from proxy JSON body.
        body_type: Optional ``error.type`` from proxy JSON (e.g. rate_limit_error).

    Returns:
        Resolved ErrorCode.
    """
    if body_type is not None:
        code = PROXY_ERROR_TYPE_TO_ERROR_CODE.get(body_type)
        if code is not None:
            return _refine_code_from_body(code, body_message)

    code = STATUS_TO_ERROR_CODE.get(status_code)
    if code is not None:
        return _refine_code_from_body(code, body_message)

    if body_message:
        message_code = _classify_by_message(body_message)
        if message_code is not None:
            return message_code

    return ErrorCode.LLM_UNKNOWN_ERROR


def _parse_schema_validation_context(exc: ContentPolicyViolationError) -> str:
    """Parse schema validation error context.

    Extracts:
        - message: Origin API error message

    Args:
        exc: Original exception

    Returns:
        Error message string
    """
    msg = exc.body.get("message")  # pyright: ignore

    return msg


# ============================================================================
# Message Building Functions
# ============================================================================


def _enrich_friendly_message(exc: LiteLLMException, error_code: ErrorCode, base_message: str) -> str:
    """Enrich friendly message with context from error message.

    Routes to appropriate parsing and building functions based on error code.

    Args:
        exc: Original LiteLLM exception
        error_code: Classified error code
        base_message: Base friendly message from constants

    Returns:
        Enriched friendly message with extracted context

    """
    # Rate limit errors
    if error_code == ErrorCode.LLM_SCHEMA_VALIDATION:
        context = _parse_schema_validation_context(exc)
        return base_message + "\n" + context if context else base_message

    if error_code == ErrorCode.LLM_BUDGET_EXCEEDED:
        context = _parse_llm_budget_error_context(exc)
        return base_message + "\n" + context if context else base_message
    # Generic errors - just add model name if available
    return base_message


def _extract_budget_from_attributes(exc: LiteLLMException) -> str:
    """Extract budget info from BudgetExceededError attributes."""
    if hasattr(exc, "current_cost") and hasattr(exc, "max_budget"):
        return (
            f"Budget has been exceeded: Your current spending: {exc.current_cost}, "
            f"available budget: {exc.max_budget}"
        )
    return ""


def _extract_budget_from_body(exc: LiteLLMException) -> str:
    """Extract budget info from BadRequestError body."""
    if isinstance(exc, BadRequestError) and hasattr(exc, "body") and isinstance(exc.body, dict):
        error_dict = exc.body.get("error", {})
        if isinstance(error_dict, dict):
            return error_dict.get("message", "")
    return ""


def _extract_budget_from_string(error_str: str) -> str:
    """Extract budget info from string representation using regex."""
    if ("Budget has been exceeded" in error_str or "budget_exceeded" in error_str.lower()) and (
        "{'error':" in error_str or '{"error":' in error_str
    ):
        import re

        match = re.search(r"'message': '([^']+)'", error_str)
        if match:
            return match.group(1)
        match = re.search(r'"message": "([^"]+)"', error_str)
        if match:
            return match.group(1)
    return ""


def _parse_llm_budget_error_context(exc: LiteLLMException) -> str:
    """Parse budget exceeded error context from exception.

    Handles both BudgetExceededError (with attributes) and BadRequestError (from body).

    Args:
        exc: LiteLLM exception (BudgetExceededError or BadRequestError)

    Returns:
        Formatted budget error message with cost details
    """
    try:
        # Try BudgetExceededError attributes first
        result = _extract_budget_from_attributes(exc)
        if result:
            return result

        # Try extracting from BadRequestError body
        result = _extract_budget_from_body(exc)
        if result:
            return result

        # Fallback to parsing from string representation
        return _extract_budget_from_string(str(exc))

    except (AttributeError, KeyError, TypeError) as e:
        logger.debug(f"Could not parse budget error context: {str(e)}")
        return ""


def classify_litellm_exception(exc: Exception) -> tuple[ErrorCode, str]:
    """Classify an exception into an ``ErrorCode`` and a user-friendly message.

    Classification strategy (ordered by reliability):
      1. Exception type name (e.g. RateLimitError, ContextWindowExceededError).
      2. ``status_code`` attribute on the exception (e.g. from HTTP response).
      3. Message-text pattern matching.
      4. Fallback to ``LLM_UNKNOWN_ERROR``.

    The friendly message is enriched with context extracted from the error message
    (e.g., model name, rate limits, token counts).

    Args:
        exc: The caught exception.

    Returns:
        Tuple of (error_code, enriched_friendly_message).
    """
    error_code = _resolve_error_code(exc)
    error_message = str(exc)
    base_message = LITELLM_ERROR_FRIENDLY_MESSAGES.get(
        error_code,
        LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_UNKNOWN_ERROR],
    )

    # Enrich message with context from error
    friendly_message = _enrich_friendly_message(exc, error_code, base_message)
    logger.debug(f"Classified LiteLLM exception as {error_code.value}: {type(exc).__name__}: {error_message[:200]}")

    return error_code, friendly_message
