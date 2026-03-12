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

"""Tests for litellm_error_classifier and litellm_error_constants.

Validates that every LLM error scenario from the Jira ticket is correctly
classified into a unique error code with a user-friendly message:
  - budget limit, TPM limit, RPM limit, LLM unavailability,
  - internal LiteLLM errors, transitive LLM errors, context window,
  - content policy, authentication, permission denied, timeout.
"""

from __future__ import annotations

import pytest

from codemie.core.errors import ErrorCode
from codemie.core.litellm_error_constants import (
    EXCEPTION_TYPE_TO_ERROR_CODE,
    LITELLM_ERROR_FRIENDLY_MESSAGES,
    STATUS_TO_ERROR_CODE,
)
from codemie.core.litellm_error_classifier import (
    _classify_by_message,
    _classify_by_status_code,
    _classify_rate_limit_subtype,
    classify_from_http_response,
    classify_litellm_exception,
    is_litellm_exception,
)


# ---------------------------------------------------------------------------
# Helper: build a test exception with controllable attributes
# ---------------------------------------------------------------------------


def _make_exception(
    message: str = "error",
    module: str = "",
    status_code: int | None = None,
    class_name: str | None = None,
) -> Exception:
    """Build an exception with optional module and status_code.

    If *class_name* is set, the exception's type will have that __name__ (for
    testing exception-type-based classification).
    """
    name = class_name if class_name is not None else "_TestException"
    klass = type(name, (Exception,), {"__module__": module})
    exc = klass(message)
    if status_code is not None:
        exc.status_code = status_code  # type: ignore[attr-defined]
    return exc


# ---------------------------------------------------------------------------
# Constants integrity
# ---------------------------------------------------------------------------


class TestLiteLLMErrorConstants:
    """Every error code MUST have a non-empty friendly message."""

    def test_all_error_codes_have_friendly_messages(self):
        for code in LITELLM_ERROR_FRIENDLY_MESSAGES:
            assert code in LITELLM_ERROR_FRIENDLY_MESSAGES, f"Missing friendly message for {code}"

    def test_error_code_values_are_unique(self):
        values = [c.value for c in LITELLM_ERROR_FRIENDLY_MESSAGES]
        assert len(values) == len(set(values))

    def test_status_to_error_code_entries_have_friendly_messages(self):
        for code in STATUS_TO_ERROR_CODE.values():
            assert code in LITELLM_ERROR_FRIENDLY_MESSAGES, f"Missing friendly message for {code}"

    def test_exception_type_to_error_code_entries_have_friendly_messages(self):
        for code in EXCEPTION_TYPE_TO_ERROR_CODE.values():
            assert code in LITELLM_ERROR_FRIENDLY_MESSAGES, f"Missing friendly message for {code}"


# ---------------------------------------------------------------------------
# 429 sub-classification: TPM vs RPM vs generic
# (Ticket: "hitting token per minute limit" / "hitting requests per users limit")
# ---------------------------------------------------------------------------


class TestClassifyRateLimitSubtype:
    @pytest.mark.parametrize(
        "message, expected",
        [
            ("tokens per minute limit reached", ErrorCode.LLM_TPM_LIMIT),
            ("TPM rate exceeded", ErrorCode.LLM_TPM_LIMIT),
            ("token quota exhausted", ErrorCode.LLM_TPM_LIMIT),
            ("requests per minute limit reached", ErrorCode.LLM_RPM_LIMIT),
            ("RPM rate exceeded", ErrorCode.LLM_RPM_LIMIT),
            ("request limit exceeded", ErrorCode.LLM_RPM_LIMIT),
            ("you've hit the rate limit", ErrorCode.LLM_RATE_LIMITED),
        ],
    )
    def test_subtypes(self, message, expected):
        assert _classify_rate_limit_subtype(message) == expected


# ---------------------------------------------------------------------------
# Status-code classification (e.g. exceptions with status_code from HTTP)
# ---------------------------------------------------------------------------


class TestClassifyByStatusCode:
    @pytest.mark.parametrize(
        "status_code, expected",
        [
            (400, ErrorCode.LLM_INVALID_REQUEST),
            (401, ErrorCode.LLM_AUTHENTICATION),
            (403, ErrorCode.LLM_PERMISSION_DENIED),
            (404, ErrorCode.LLM_INVALID_REQUEST),
            (408, ErrorCode.LLM_TIMEOUT),
            (422, ErrorCode.LLM_INVALID_REQUEST),
            (500, ErrorCode.LLM_INTERNAL_ERROR),
            (502, ErrorCode.LLM_TRANSITIVE_ERROR),
            (503, ErrorCode.LLM_UNAVAILABLE),
        ],
    )
    def test_known_status_codes(self, status_code, expected):
        exc = _make_exception(status_code=status_code)
        assert _classify_by_status_code(exc) == expected

    def test_429_with_tpm_message_classified_as_tpm(self):
        exc = _make_exception(message="tokens per minute limit", status_code=429)
        assert _classify_by_status_code(exc) == ErrorCode.LLM_TPM_LIMIT

    def test_429_with_rpm_message_classified_as_rpm(self):
        exc = _make_exception(message="requests per minute exceeded", status_code=429)
        assert _classify_by_status_code(exc) == ErrorCode.LLM_RPM_LIMIT

    def test_429_generic_message_classified_as_rate_limit(self):
        exc = _make_exception(message="slow down", status_code=429)
        assert _classify_by_status_code(exc) == ErrorCode.LLM_RATE_LIMITED

    def test_unknown_status_code_returns_none(self):
        exc = _make_exception(status_code=418)
        assert _classify_by_status_code(exc) is None

    def test_no_status_code_returns_none(self):
        assert _classify_by_status_code(Exception("no status")) is None


# ---------------------------------------------------------------------------
# Exception-type classification (LiteLLM/OpenAI class name; most reliable)
# ---------------------------------------------------------------------------


class TestClassifyByExceptionType:
    """Classification by exception class name takes precedence over status/message."""

    @pytest.mark.parametrize(
        "class_name, expected",
        [
            ("ContextWindowExceededError", ErrorCode.LLM_CONTEXT_LENGTH),
            ("ContentPolicyViolationError", ErrorCode.LLM_CONTENT_POLICY),
            ("BudgetExceededError", ErrorCode.LLM_BUDGET_EXCEEDED),
            ("AuthenticationError", ErrorCode.LLM_AUTHENTICATION),
            ("PermissionDeniedError", ErrorCode.LLM_PERMISSION_DENIED),
            ("Timeout", ErrorCode.LLM_TIMEOUT),
            ("APITimeoutError", ErrorCode.LLM_TIMEOUT),
            ("ServiceUnavailableError", ErrorCode.LLM_UNAVAILABLE),
            ("InternalServerError", ErrorCode.LLM_INTERNAL_ERROR),
            ("APIError", ErrorCode.LLM_INTERNAL_ERROR),
            ("APIConnectionError", ErrorCode.LLM_TRANSITIVE_ERROR),
            ("BadRequestError", ErrorCode.LLM_INVALID_REQUEST),
            ("NotFoundError", ErrorCode.LLM_INVALID_REQUEST),
        ],
    )
    def test_exception_type_maps_to_error_code(self, class_name, expected):
        exc = _make_exception(message="any message", class_name=class_name)
        code, _ = classify_litellm_exception(exc)
        assert code == expected

    def test_rate_limit_error_with_tpm_message_classified_as_tpm(self):
        exc = _make_exception(
            message="tokens per minute limit exceeded",
            class_name="RateLimitError",
        )
        code, _ = classify_litellm_exception(exc)
        assert code == ErrorCode.LLM_TPM_LIMIT

    def test_rate_limit_error_with_rpm_message_classified_as_rpm(self):
        exc = _make_exception(
            message="requests per minute exceeded",
            class_name="RateLimitError",
        )
        code, _ = classify_litellm_exception(exc)
        assert code == ErrorCode.LLM_RPM_LIMIT

    def test_exception_type_takes_precedence_over_status(self):
        # 400 would map to LLM_INVALID_REQUEST by status; type gives CONTEXT_LENGTH
        exc = _make_exception(
            message="generic error",
            status_code=400,
            class_name="ContextWindowExceededError",
        )
        code, _ = classify_litellm_exception(exc)
        assert code == ErrorCode.LLM_CONTEXT_LENGTH


# ---------------------------------------------------------------------------
# Message-pattern classification (when status_code not available)
# ---------------------------------------------------------------------------


class TestClassifyByMessage:
    @pytest.mark.parametrize(
        "message, expected",
        [
            # Budget (Ticket: "hitting budget limit")
            ("budget_exceeded for user", ErrorCode.LLM_BUDGET_EXCEEDED),
            ("exceeded your budget limit", ErrorCode.LLM_BUDGET_EXCEEDED),
            # Rate limit (Ticket: "hitting requests per users limit")
            ("rate limit exceeded", ErrorCode.LLM_RATE_LIMITED),
            # Context window
            ("context window exceeded", ErrorCode.LLM_CONTEXT_LENGTH),
            ("maximum context length exceeded", ErrorCode.LLM_CONTEXT_LENGTH),
            # Content policy
            ("content policy violation", ErrorCode.LLM_CONTENT_POLICY),
            # Timeout
            ("request timeout occurred", ErrorCode.LLM_TIMEOUT),
            # Unavailability (Ticket: "unavailability of LLM")
            ("service unavailable", ErrorCode.LLM_UNAVAILABLE),
            ("503 error from provider", ErrorCode.LLM_UNAVAILABLE),
            # Transitive errors (Ticket: "transitive LLM errors")
            ("connection error to provider", ErrorCode.LLM_TRANSITIVE_ERROR),
            ("bad gateway", ErrorCode.LLM_TRANSITIVE_ERROR),
            # Authentication
            ("authentication failed", ErrorCode.LLM_AUTHENTICATION),
            ("unauthorized access", ErrorCode.LLM_AUTHENTICATION),
            # Unknown — must NOT falsely match
            ("some random error", None),
            ("model output generated successfully", None),
        ],
    )
    def test_message_patterns(self, message, expected):
        assert _classify_by_message(message) == expected


# ---------------------------------------------------------------------------
# is_litellm_exception — detects whether an exception originates from LiteLLM
# ---------------------------------------------------------------------------


class TestIsLitellmException:
    def test_detects_litellm_module(self):
        exc = _make_exception(module="litellm.exceptions")
        assert is_litellm_exception(exc) is True

    def test_detects_openai_module(self):
        exc = _make_exception(module="openai._exceptions")
        assert is_litellm_exception(exc) is True

    def test_detects_litellm_in_message(self):
        exc = Exception("litellm.RateLimitError: rate limit hit")
        assert is_litellm_exception(exc) is True

    def test_detects_budget_pattern_in_message(self):
        exc = Exception("budget_exceeded for user abc")
        assert is_litellm_exception(exc) is True

    def test_rejects_plain_exception(self):
        exc = ValueError("something unrelated went wrong")
        assert is_litellm_exception(exc) is False


# ---------------------------------------------------------------------------
# classify_from_http_response — proxy path (status + optional body)
# ---------------------------------------------------------------------------


class TestClassifyFromHttpResponse:
    def test_status_only_uses_status_map(self):
        assert classify_from_http_response(503) == ErrorCode.LLM_UNAVAILABLE
        assert classify_from_http_response(401) == ErrorCode.LLM_AUTHENTICATION
        assert classify_from_http_response(429) == ErrorCode.LLM_RATE_LIMITED
        assert classify_from_http_response(418) == ErrorCode.LLM_UNKNOWN_ERROR

    def test_body_type_rate_limit_error(self):
        assert classify_from_http_response(429, body_type="rate_limit_error") == ErrorCode.LLM_RATE_LIMITED

    def test_body_type_with_tpm_message_refines_to_tpm(self):
        assert (
            classify_from_http_response(
                429,
                body_message="tokens per minute limit",
                body_type="rate_limit_error",
            )
            == ErrorCode.LLM_TPM_LIMIT
        )

    def test_body_type_invalid_request_with_context_message_refines_to_context_length(self):
        assert (
            classify_from_http_response(
                400,
                body_message="litellm.ContextWindowExceededError: context length exceeded",
                body_type="invalid_request_error",
            )
            == ErrorCode.LLM_CONTEXT_LENGTH
        )

    def test_body_type_invalid_request_with_content_policy_refines_to_content_policy(self):
        assert (
            classify_from_http_response(
                400,
                body_message="ContentPolicyViolationError: policy violation",
                body_type="invalid_request_error",
            )
            == ErrorCode.LLM_CONTENT_POLICY
        )

    def test_body_message_fallback_when_status_unknown(self):
        assert (
            classify_from_http_response(418, body_message="budget_exceeded for user") == ErrorCode.LLM_BUDGET_EXCEEDED
        )


# ---------------------------------------------------------------------------
# classify_litellm_exception — end-to-end with synthetic exceptions
# Tests the full chain: exception_type → status_code → message → UNKNOWN
# ---------------------------------------------------------------------------


class TestClassifyEndToEnd:
    def test_status_code_503_returns_unavailable_with_friendly_message(self):
        exc = _make_exception(message="error", status_code=503)
        code, message = classify_litellm_exception(exc)
        assert code == ErrorCode.LLM_UNAVAILABLE
        assert message == LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_UNAVAILABLE]

    def test_budget_message_returns_budget_exceeded_with_friendly_message(self):
        exc = Exception("budget_exceeded for user test-user")
        code, message = classify_litellm_exception(exc)
        assert code == ErrorCode.LLM_BUDGET_EXCEEDED
        # For generic Exception without budget details, should return base message only
        assert message == LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_BUDGET_EXCEEDED]

    def test_unrecognised_litellm_exception_returns_unknown(self):
        exc = _make_exception(module="litellm.exceptions", message="something totally new")
        code, message = classify_litellm_exception(exc)
        assert code == ErrorCode.LLM_UNKNOWN_ERROR
        assert message == LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_UNKNOWN_ERROR]

    def test_429_with_tpm_returns_tpm_limit_friendly_message(self):
        exc = _make_exception(message="tokens per minute limit", status_code=429)
        code, message = classify_litellm_exception(exc)
        assert code == ErrorCode.LLM_TPM_LIMIT
        assert "tokens-per-minute" in message.lower()

    def test_429_with_rpm_returns_rpm_limit_friendly_message(self):
        exc = _make_exception(message="requests per minute exceeded", status_code=429)
        code, message = classify_litellm_exception(exc)
        assert code == ErrorCode.LLM_RPM_LIMIT
        assert "requests-per-minute" in message.lower()
