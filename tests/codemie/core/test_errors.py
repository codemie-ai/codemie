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

"""Unit tests for error classification and handling in codemie.core.errors."""

import asyncio
import json
import pytest

from codemie.core.error_constants import BUDGET_MESSAGE_KEY, CURRENT_COST_KEY, MAX_BUDGET_KEY
from codemie.core.error_constants import ErrorCategory, ErrorCode
from codemie.core.errors import (
    AgentErrorClassifier,
    ExceptionClassificationPipeline,
    InternalError,
    InternalErrorClassifier,
    LiteLLMErrorClassifier,
)


# ---------------------------------------------------------------------------
# LiteLLMErrorClassifier
# ---------------------------------------------------------------------------


class TestLiteLLMErrorClassifier:
    """Tests for LiteLLMErrorClassifier."""

    def test_classify_parses_json_structured_authentication_error(self):
        """Parse JSON-structured error with litellm.AuthenticationError in message."""
        exc_str = (
            '{"error": {"message": "litellm.AuthenticationError: AzureException - Unknown api key.", '
            '"type": "None", "param": "None", "code": "401"}}'
        )
        classifier = LiteLLMErrorClassifier()
        result = classifier.classify(Exception(exc_str))

        assert result is not None
        assert result.category == ErrorCategory.LITE_LLM
        assert result.lite_llm_error is not None
        assert result.lite_llm_error.error_code == ErrorCode.LITE_LLM_AUTHENTICATION_ERROR
        assert (
            "authentication" in result.lite_llm_error.message.lower()
            or "api key" in result.lite_llm_error.message.lower()
        )

    def test_classify_parses_json_structured_guardrail_error(self):
        """Parse JSON-structured error with guardrail payload in message."""
        guardrail = {
            "error_type": "HATE_SPEECH",
            "reason": "Hate speech detected.",
            "guardrail": "hate_speech",
            "stage": "pre_call",
            "version": "1.1.1",
        }
        exc_dict = {
            "error": {
                "message": json.dumps(guardrail),
                "type": "None",
                "param": "None",
                "code": "400",
            }
        }
        exc_str = json.dumps(exc_dict)
        classifier = LiteLLMErrorClassifier()
        result = classifier.classify(Exception(exc_str))

        assert result is not None
        assert result.category == ErrorCategory.LITE_LLM
        assert result.lite_llm_error is not None
        assert result.lite_llm_error.error_code == ErrorCode.LITE_LLM_CONTENT_POLICY_VIOLATION_ERROR

    def test_classify_parses_json_structured_rate_limit_error(self):
        """Parse JSON-structured error with rate limit in message (keyword fallback)."""
        exc_str = (
            '{"error": {"message": "rate limit exceeded for model gpt-4", '
            '"type": "None", "param": "None", "code": "429"}}'
        )
        classifier = LiteLLMErrorClassifier()
        result = classifier.classify(Exception(exc_str))

        assert result is not None
        assert result.category == ErrorCategory.LITE_LLM
        assert result.lite_llm_error is not None
        assert result.lite_llm_error.error_code == ErrorCode.LITE_LLM_RATE_LIMIT_ERROR

    def test_classify_parses_json_structured_context_window_exceeded(self):
        """Parse JSON-structured error with context window exceeded (keyword fallback)."""
        exc_str = (
            '{"error": {"message": "context window exceeded for this model", '
            '"type": "None", "param": "None", "code": "400"}}'
        )
        classifier = LiteLLMErrorClassifier()
        result = classifier.classify(Exception(exc_str))

        assert result is not None
        assert result.category == ErrorCategory.LITE_LLM
        assert result.lite_llm_error is not None
        assert result.lite_llm_error.error_code == ErrorCode.LITE_LLM_CONTEXT_WINDOW_EXCEEDED_ERROR

    def test_classify_returns_none_for_unparsable_error(self):
        """Return None when exception string does not match proxy format."""
        classifier = LiteLLMErrorClassifier()
        result = classifier.classify(Exception("Some random error without JSON structure"))

        assert result is None

    def test_classify_returns_none_for_plain_string(self):
        """Return None when exception is plain text without error object."""
        classifier = LiteLLMErrorClassifier()
        result = classifier.classify(Exception("Connection refused"))

        assert result is None

    def test_classify_accepts_string_input_via_parse_exception(self):
        """_parse_exception accepts both Exception and str."""
        exc_str = (
            '{"error": {"message": "litellm.RateLimitError: rate limit", '
            '"type": "None", "param": "None", "code": "429"}}'
        )
        classifier = LiteLLMErrorClassifier()
        parsed = classifier._parse_exception(exc_str)

        assert parsed is not None
        assert parsed.get("error_code") == ErrorCode.LITE_LLM_RATE_LIMIT_ERROR

    def test_extract_budget_from_exception_returns_current_cost_and_max_budget(self):
        """_extract_budget_from_exception extracts current_cost and max_budget from exception attributes."""
        classifier = LiteLLMErrorClassifier()
        exc = Exception("budget exceeded")
        exc.current_cost = 15.0  # type: ignore[attr-defined]
        exc.max_budget = 10.0  # type: ignore[attr-defined]

        result = classifier._extract_budget_from_exception(exc)

        assert result[CURRENT_COST_KEY] == 15.0
        assert result[MAX_BUDGET_KEY] == 10.0

    def test_extract_budget_from_exception_returns_empty_when_no_attributes(self):
        """_extract_budget_from_exception returns empty dict when exception has no budget attributes."""
        classifier = LiteLLMErrorClassifier()
        exc = Exception("generic error")

        result = classifier._extract_budget_from_exception(exc)

        assert result == {}

    def test_extract_budget_from_error_dict_extracts_budget_message(self):
        """_extract_budget_from_error_dict extracts budget_message from error body."""
        classifier = LiteLLMErrorClassifier()
        error_dict = {"error": {"message": "Budget exceeded for user abc", "type": None, "param": None, "code": 402}}

        result = classifier._extract_budget_from_error_dict(error_dict)

        assert result[BUDGET_MESSAGE_KEY] == "Budget exceeded for user abc"

    def test_extract_budget_from_error_dict_returns_empty_when_no_budget_in_message(self):
        """_extract_budget_from_error_dict returns empty when message has no budget/exceeded keywords."""
        classifier = LiteLLMErrorClassifier()
        error_dict = {"error": {"message": "Invalid request", "type": None, "param": None, "code": 400}}

        result = classifier._extract_budget_from_error_dict(error_dict)

        assert result == {}

    def test_extract_budget_from_string_extracts_message_with_single_quotes(self):
        """_extract_budget_from_string parses budget_message from string with single-quoted JSON."""
        classifier = LiteLLMErrorClassifier()
        error_str = "budget_exceeded: {'error': {'message': 'User budget exceeded for user abc'}}"

        result = classifier._extract_budget_from_string(error_str)

        assert result[BUDGET_MESSAGE_KEY] == "User budget exceeded for user abc"

    def test_extract_budget_from_string_extracts_message_with_double_quotes(self):
        """_extract_budget_from_string parses budget_message from string with double-quoted JSON."""
        classifier = LiteLLMErrorClassifier()
        error_str = 'budget_exceeded: {"error": {"message": "Spending limit reached"}}'

        result = classifier._extract_budget_from_string(error_str)

        assert result[BUDGET_MESSAGE_KEY] == "Spending limit reached"

    def test_extract_budget_from_string_returns_empty_when_no_json_structure(self):
        """_extract_budget_from_string returns empty when string has no error JSON structure."""
        classifier = LiteLLMErrorClassifier()
        error_str = "budget_exceeded: limit reached"

        result = classifier._extract_budget_from_string(error_str)

        assert result == {}

    def test_extract_schema_validation_context_returns_message_with_schema_keyword(self):
        """_extract_schema_validation_context returns schema_validation_context when message contains schema."""
        classifier = LiteLLMErrorClassifier()
        inner_msg = "Invalid schema: title contains spaces"

        result = classifier._extract_schema_validation_context(inner_msg)

        assert result["schema_validation_context"] == "Invalid schema: title contains spaces"

    def test_extract_schema_validation_context_returns_message_with_response_format(self):
        """_extract_schema_validation_context returns context when message contains response_format."""
        classifier = LiteLLMErrorClassifier()
        inner_msg = "response_format validation failed"

        result = classifier._extract_schema_validation_context(inner_msg)

        assert result["schema_validation_context"] == "response_format validation failed"

    def test_extract_schema_validation_context_returns_empty_when_no_keywords(self):
        """_extract_schema_validation_context returns empty when message has no schema keywords."""
        classifier = LiteLLMErrorClassifier()
        inner_msg = "Invalid request"

        result = classifier._extract_schema_validation_context(inner_msg)

        assert result == {}

    def test_parse_exception_enriches_details_for_budget_exceeded_error(self):
        """_parse_exception enriches details with budget context when error is LITE_LLM_BUDGET_EXCEEDED_ERROR."""
        exc_str = (
            '{"error": {"message": "litellm.BudgetExceededError: Budget exceeded. '
            'Your current spending: 15.0, available budget: 10.0", '
            '"type": "None", "param": "None", "code": "402"}}'
        )
        classifier = LiteLLMErrorClassifier()
        parsed = classifier._parse_exception(exc_str)

        assert parsed is not None
        assert parsed["error_code"] == ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR
        details = parsed.get("details") or {}
        assert BUDGET_MESSAGE_KEY in details or CURRENT_COST_KEY in details or MAX_BUDGET_KEY in details

    def test_parse_exception_enriches_details_for_schema_validation_error(self):
        """_parse_exception enriches details with schema_validation_context for LITE_LLM_BAD_REQUEST_ERROR."""
        exc_str = (
            '{"error": {"message": "Invalid schema: response_format validation failed", '
            '"type": "None", "param": "None", "code": "400"}}'
        )
        classifier = LiteLLMErrorClassifier()
        parsed = classifier._parse_exception(exc_str)

        assert parsed is not None
        assert parsed["error_code"] == ErrorCode.LITE_LLM_BAD_REQUEST_ERROR
        details = parsed.get("details") or {}
        assert details.get("schema_validation_context") == "Invalid schema: response_format validation failed"


# ---------------------------------------------------------------------------
# AgentErrorClassifier
# ---------------------------------------------------------------------------


class TestAgentErrorClassifier:
    """Tests for AgentErrorClassifier."""

    def test_classify_timeout_error(self):
        """Classify TimeoutError as AGENT_TIMEOUT."""
        classifier = AgentErrorClassifier()
        result = classifier.classify(TimeoutError("Request timed out"))

        assert result is not None
        assert result.category == ErrorCategory.AGENT
        assert result.agent_error is not None
        assert result.agent_error.error_code == ErrorCode.AGENT_TIMEOUT

    def test_classify_asyncio_timeout_error(self):
        """Classify asyncio.TimeoutError as AGENT_TIMEOUT."""
        classifier = AgentErrorClassifier()
        result = classifier.classify(asyncio.TimeoutError())

        assert result is not None
        assert result.category == ErrorCategory.AGENT
        assert result.agent_error is not None
        assert result.agent_error.error_code == ErrorCode.AGENT_TIMEOUT

    def test_classify_timeout_in_message(self):
        """Classify exception with 'timeout' in message as AGENT_TIMEOUT."""
        classifier = AgentErrorClassifier()
        result = classifier.classify(Exception("Connection timeout exceeded"))

        assert result is not None
        assert result.category == ErrorCategory.AGENT
        assert result.agent_error is not None
        assert result.agent_error.error_code == ErrorCode.AGENT_TIMEOUT

    def test_classify_budget_exceeded(self):
        """Classify budget_exceeded in message as AGENT_BUDGET_EXCEEDED."""
        classifier = AgentErrorClassifier()
        result = classifier.classify(Exception("budget_exceeded: limit reached"))

        assert result is not None
        assert result.category == ErrorCategory.AGENT
        assert result.agent_error is not None
        assert result.agent_error.error_code == ErrorCode.AGENT_BUDGET_EXCEEDED

    def test_try_budget_exceeded_stores_budget_message_in_details(self):
        """_try_budget_exceeded stores extracted budget_message in details, uses config for main message."""
        from codemie.configs import config

        classifier = AgentErrorClassifier()
        error_str = "budget_exceeded: {'error': {'message': 'User budget exceeded for user abc'}}"
        result = classifier._try_budget_exceeded(Exception(error_str), "budget_exceeded")

        assert result is not None
        assert result["error_code"] == ErrorCode.AGENT_BUDGET_EXCEEDED
        assert result["message"] == config.AGENT_MSG_BUDGET_EXCEEDED
        assert result["details"] is not None
        assert result["details"].get(BUDGET_MESSAGE_KEY) == "User budget exceeded for user abc"

    def test_classify_token_limit(self):
        """Classify max_output_tokens / truncation as AGENT_TOKEN_LIMIT."""
        classifier = AgentErrorClassifier()
        result = classifier.classify(Exception("max_output_tokens limit was reached"))

        assert result is not None
        assert result.category == ErrorCategory.AGENT
        assert result.agent_error is not None
        assert result.agent_error.error_code == ErrorCode.AGENT_TOKEN_LIMIT

    def test_classify_callback_failure(self):
        """Classify callback error as AGENT_CALLBACK_FAILURE."""
        classifier = AgentErrorClassifier()
        result = classifier.classify(Exception("callback failed with error"))

        assert result is not None
        assert result.category == ErrorCategory.AGENT
        assert result.agent_error is not None
        assert result.agent_error.error_code == ErrorCode.AGENT_CALLBACK_FAILURE

    def test_classify_connection_error(self):
        """Classify ConnectionError as AGENT_NETWORK_ERROR."""
        classifier = AgentErrorClassifier()
        result = classifier.classify(ConnectionError("Connection refused"))

        assert result is not None
        assert result.category == ErrorCategory.AGENT
        assert result.agent_error is not None
        assert result.agent_error.error_code == ErrorCode.AGENT_NETWORK_ERROR

    def test_classify_network_in_message(self):
        """Classify exception with 'network' in message as AGENT_NETWORK_ERROR."""
        classifier = AgentErrorClassifier()
        result = classifier.classify(Exception("A network error occurred"))

        assert result is not None
        assert result.category == ErrorCategory.AGENT
        assert result.agent_error is not None
        assert result.agent_error.error_code == ErrorCode.AGENT_NETWORK_ERROR

    def test_classify_returns_none_for_generic_exception(self):
        """Return None for generic exception that does not match agent patterns."""
        classifier = AgentErrorClassifier()
        result = classifier.classify(ValueError("Invalid value"))

        assert result is None


# ---------------------------------------------------------------------------
# InternalErrorClassifier
# ---------------------------------------------------------------------------


class TestInternalErrorClassifier:
    """Tests for InternalErrorClassifier."""

    def test_classify_returns_internal_for_any_exception(self):
        """InternalErrorClassifier always returns ErrorResponse with INTERNAL category."""
        classifier = InternalErrorClassifier()
        result = classifier.classify(ValueError("Something went wrong"))

        assert result is not None
        assert result.category == ErrorCategory.INTERNAL
        assert result.internal is not None
        assert result.internal.error_code == ErrorCode.PLATFORM_ERROR
        assert "type" in (result.internal.details or {})
        assert result.internal.details["type"] == "ValueError"

    def test_internal_error_from_exception(self):
        """InternalError.from_exception builds correct structure."""
        exc = RuntimeError("Unexpected failure")
        internal = InternalError.from_exception(exc)

        assert internal.error_code == ErrorCode.PLATFORM_ERROR
        assert internal.details is not None
        assert internal.details["type"] == "RuntimeError"
        assert internal.details["message"] == "Unexpected failure"
        assert "traceback" in internal.details


# ---------------------------------------------------------------------------
# ExceptionClassificationPipeline
# ---------------------------------------------------------------------------


class TestExceptionClassificationPipeline:
    """Tests for ExceptionClassificationPipeline."""

    def test_pipeline_agent_error_before_internal(self):
        """Agent errors are classified before falling through to Internal."""
        pipeline = ExceptionClassificationPipeline(classifiers=[AgentErrorClassifier(), InternalErrorClassifier()])
        result = pipeline.handle(TimeoutError("timed out"))

        assert result.category == ErrorCategory.AGENT
        assert result.agent_error is not None
        assert result.agent_error.error_code == ErrorCode.AGENT_TIMEOUT

    def test_pipeline_internal_fallback(self):
        """Unknown exceptions fall through to InternalErrorClassifier."""
        pipeline = ExceptionClassificationPipeline(classifiers=[AgentErrorClassifier(), InternalErrorClassifier()])
        result = pipeline.handle(ValueError("unknown"))

        assert result.category == ErrorCategory.INTERNAL
        assert result.internal is not None

    def test_pipeline_empty_classifiers_raises(self):
        """Pipeline with no classifiers raises ValueError."""
        with pytest.raises(ValueError, match="at least one classifier"):
            ExceptionClassificationPipeline(classifiers=[])

    def test_pipeline_litellm_before_agent_when_both_match(self):
        """When exception matches both LiteLLM and Agent patterns, LiteLLM wins (first in chain)."""
        # budget_exceeded matches Agent; if wrapped in LiteLLM JSON format, LiteLLM wins
        exc_str = (
            '{"error": {"message": "litellm.BudgetExceededError: exceeded", '
            '"type": "None", "param": "None", "code": "402"}}'
        )
        pipeline = ExceptionClassificationPipeline(
            classifiers=[
                LiteLLMErrorClassifier(),
                AgentErrorClassifier(),
                InternalErrorClassifier(),
            ]
        )
        result = pipeline.handle(Exception(exc_str))

        assert result.category == ErrorCategory.LITE_LLM
        assert result.lite_llm_error is not None
        assert result.lite_llm_error.error_code == ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR


# ---------------------------------------------------------------------------
# ErrorResponse
# ---------------------------------------------------------------------------


class TestErrorResponse:
    """Tests for ErrorResponse model."""

    def test_get_error_returns_agent_error_for_agent_category(self):
        """get_error returns agent_error when category is AGENT."""
        pipeline = ExceptionClassificationPipeline(classifiers=[AgentErrorClassifier(), InternalErrorClassifier()])
        response = pipeline.handle(TimeoutError("timed out"))

        err = response.get_error()
        assert err is not None
        assert err.error_code == ErrorCode.AGENT_TIMEOUT

    def test_get_error_returns_internal_for_internal_category(self):
        """get_error returns internal when category is INTERNAL."""
        pipeline = ExceptionClassificationPipeline(classifiers=[InternalErrorClassifier()])
        response = pipeline.handle(ValueError("x"))

        err = response.get_error()
        assert err is not None
        assert response.category == ErrorCategory.INTERNAL
