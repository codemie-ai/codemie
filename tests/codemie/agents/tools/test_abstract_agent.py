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

"""Unit tests for AbstractAgent error handling."""

import json
from unittest.mock import MagicMock, patch


from codemie.agents.tools.agent import AbstractAgent
from codemie.core.error_constants import ErrorCategory, ErrorCode
from codemie.core.errors import (
    AgentErrorDetails,
    ErrorResponse,
    InternalError,
    LiteLLMError,
    LiteLLMErrorInner,
)
from codemie.enterprise.litellm.proxy_router import handle_agent_exception


class ConcreteAgent(AbstractAgent):
    """Concrete implementation of AbstractAgent for testing."""

    pass


# ---------------------------------------------------------------------------
# handle_agent_exception
# ---------------------------------------------------------------------------


class TestHandleAgentException:
    """Tests for handle_agent_exception."""

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    def test_delegates_to_pipeline(self, mock_send_log_metric):
        """handle_agent_exception delegates to ExceptionClassificationPipeline."""
        exc = TimeoutError("timed out")

        result = handle_agent_exception(exc)

        assert result is not None
        assert result.category == ErrorCategory.AGENT
        assert result.agent_error is not None
        assert result.agent_error.error_code == ErrorCode.AGENT_TIMEOUT
        mock_send_log_metric.assert_called_once()

    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    def test_returns_internal_for_unknown_exception(self, mock_send_log_metric):
        """handle_agent_exception returns INTERNAL for unknown exceptions."""
        exc = ValueError("unknown")

        result = handle_agent_exception(exc)

        assert result is not None
        assert result.category == ErrorCategory.INTERNAL
        assert result.internal is not None
        mock_send_log_metric.assert_called_once()


# ---------------------------------------------------------------------------
# extended_error
# ---------------------------------------------------------------------------


class TestExtendedError:
    """Tests for extended_error."""

    def test_returns_formatted_string_for_internal_error(self):
        """extended_error returns formatted string for INTERNAL category."""
        agent = ConcreteAgent()
        internal = InternalError.from_exception(ValueError("invalid input"))
        error_response = ErrorResponse(
            category=ErrorCategory.INTERNAL,
            internal=internal,
        )
        exc = ValueError("invalid input")

        result = agent.extended_error(error_response, exc)

        assert "AI Agent run failed with error:" in result
        assert "ValueError" in result
        assert "invalid input" in result

    def test_returns_str_exception_for_non_internal(self):
        """extended_error returns str(exception) for non-INTERNAL when no budget/schema details."""
        agent = ConcreteAgent()
        agent_error = AgentErrorDetails(
            error_code=ErrorCode.AGENT_TIMEOUT,
            message="The agent request timed out.",
        )
        error_response = ErrorResponse(
            category=ErrorCategory.AGENT,
            agent_error=agent_error,
        )
        exc = TimeoutError("timed out")

        result = agent.extended_error(error_response, exc)

        assert result == "timed out"

    def test_returns_enriched_message_for_lite_llm_budget_with_current_cost_max_budget(self):
        """extended_error enriches message when LITE_LLM_BUDGET_EXCEEDED_ERROR and details have current_cost, max_budget."""
        agent = ConcreteAgent()
        lite_llm_error = LiteLLMError(
            error_code=ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR,
            message="Budget exceeded.",
            details={"current_cost": 15.0, "max_budget": 10.0},
            error=LiteLLMErrorInner(message="exceeded", type_=None, param=None, code=402),
        )
        error_response = ErrorResponse(
            category=ErrorCategory.LITE_LLM,
            lite_llm_error=lite_llm_error,
        )
        exc = Exception("budget_exceeded")

        result = agent.extended_error(error_response, exc)

        assert "Budget exceeded." in result
        assert "15.0" in result
        assert "10.0" in result
        assert "Budget has been exceeded" in result

    def test_returns_enriched_message_for_agent_budget_with_budget_message(self):
        """extended_error enriches message when AGENT_BUDGET_EXCEEDED and details have budget_message."""
        agent = ConcreteAgent()
        agent_error = AgentErrorDetails(
            error_code=ErrorCode.AGENT_BUDGET_EXCEEDED,
            message="Budget exceeded.",
            details={"budget_message": "User budget exceeded for user abc"},
        )
        error_response = ErrorResponse(
            category=ErrorCategory.AGENT,
            agent_error=agent_error,
        )
        exc = Exception("budget_exceeded")

        result = agent.extended_error(error_response, exc)

        assert "Budget exceeded." in result
        assert "User budget exceeded for user abc" in result

    def test_returns_enriched_message_for_lite_llm_bad_request_with_schema_context(self):
        """extended_error enriches message when LITE_LLM_BAD_REQUEST_ERROR and details have schema_validation_context."""
        agent = ConcreteAgent()
        lite_llm_error = LiteLLMError(
            error_code=ErrorCode.LITE_LLM_BAD_REQUEST_ERROR,
            message="Invalid request.",
            details={"schema_validation_context": "Invalid schema: title contains spaces"},
            error=LiteLLMErrorInner(message="schema error", type_=None, param=None, code=400),
        )
        error_response = ErrorResponse(
            category=ErrorCategory.LITE_LLM,
            lite_llm_error=lite_llm_error,
        )
        exc = Exception("bad request")

        result = agent.extended_error(error_response, exc)

        assert "Invalid request." in result
        assert "Invalid schema: title contains spaces" in result

    def test_returns_str_exception_for_budget_error_when_details_empty(self):
        """extended_error returns str(exception) when budget error but details lack current_cost/max_budget/budget_message."""
        agent = ConcreteAgent()
        agent_error = AgentErrorDetails(
            error_code=ErrorCode.AGENT_BUDGET_EXCEEDED,
            message="Budget exceeded.",
            details={},
        )
        error_response = ErrorResponse(
            category=ErrorCategory.AGENT,
            agent_error=agent_error,
        )
        exc = Exception("budget_exceeded")

        result = agent.extended_error(error_response, exc)

        assert result == "budget_exceeded"

    def test_returns_str_exception_for_bad_request_when_no_schema_context(self):
        """extended_error returns str(exception) when LITE_LLM_BAD_REQUEST_ERROR but no schema_validation_context in details."""
        agent = ConcreteAgent()
        lite_llm_error = LiteLLMError(
            error_code=ErrorCode.LITE_LLM_BAD_REQUEST_ERROR,
            message="Invalid request.",
            details={},
            error=LiteLLMErrorInner(message="bad request", type_=None, param=None, code=400),
        )
        error_response = ErrorResponse(
            category=ErrorCategory.LITE_LLM,
            lite_llm_error=lite_llm_error,
        )
        exc = Exception("bad request")

        result = agent.extended_error(error_response, exc)

        assert result == "bad request"


# ---------------------------------------------------------------------------
# send_error_response
# ---------------------------------------------------------------------------


class TestSendErrorResponse:
    """Tests for send_error_response."""

    @patch("codemie.agents.tools.agent.config")
    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    def test_hide_exceptions_true_friendly_message_and_error_details(self, mock_send_log_metric, mock_config):
        """When HIDE_AGENT_STREAMING_EXCEPTIONS=True: generated has friendly message, error_details populated."""
        mock_config.HIDE_AGENT_STREAMING_EXCEPTIONS = True
        agent = ConcreteAgent()
        mock_generator = MagicMock()
        thread_context = {"conversation_id": "test-123"}
        exc = TimeoutError("timed out")
        execution_start = 0.0
        chunks_collector = []

        agent.send_error_response(
            mock_generator,
            thread_context,
            exc,
            execution_start,
            chunks_collector,
        )

        mock_generator.send.assert_called_once()
        call_arg = mock_generator.send.call_args[0][0]
        payload = json.loads(call_arg)

        assert "generated" in payload
        assert "timed out" in payload["generated"].lower() or "timeout" in payload["generated"].lower()
        assert payload.get("error_details") is None
        assert payload["last"] is True
        assert payload["execution_error"] == "agent_timeout"

    @patch("codemie.agents.tools.agent.config")
    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    def test_hide_exceptions_false_legacy_flow_error_details_none(self, mock_send_log_metric, mock_config):
        """When HIDE_AGENT_STREAMING_EXCEPTIONS=False: generated has full error text, error_details=None."""
        mock_config.HIDE_AGENT_STREAMING_EXCEPTIONS = False
        agent = ConcreteAgent()
        mock_generator = MagicMock()
        thread_context = None
        exc = TimeoutError("timed out")
        execution_start = 0.0
        chunks_collector = ["some chunk"]

        agent.send_error_response(
            mock_generator,
            thread_context,
            exc,
            execution_start,
            chunks_collector,
        )

        mock_generator.send.assert_called_once()
        call_arg = mock_generator.send.call_args[0][0]
        payload = json.loads(call_arg)

        assert "generated" in payload
        assert "timed out" in payload["generated"]
        assert "some chunk" in payload["generated"]
        assert payload.get("error_details") is None
        assert payload["last"] is True
        assert payload["execution_error"] == "agent_timeout"

    @patch("codemie.agents.tools.agent.config")
    @patch("codemie.enterprise.litellm.proxy_router.send_log_metric")
    def test_hide_exceptions_false_internal_error_uses_extended_error(self, mock_send_log_metric, mock_config):
        """When HIDE_AGENT_STREAMING_EXCEPTIONS=False and INTERNAL: generated uses extended_error format."""
        mock_config.HIDE_AGENT_STREAMING_EXCEPTIONS = False
        agent = ConcreteAgent()
        mock_generator = MagicMock()
        thread_context = None
        exc = ValueError("unknown failure")
        execution_start = 0.0
        chunks_collector = []

        agent.send_error_response(
            mock_generator,
            thread_context,
            exc,
            execution_start,
            chunks_collector,
        )

        call_arg = mock_generator.send.call_args[0][0]
        payload = json.loads(call_arg)

        assert "AI Agent run failed with error:" in payload["generated"]
        assert "ValueError" in payload["generated"]
        assert "unknown failure" in payload["generated"]
        assert payload.get("error_details") is None
