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

import ast
import asyncio
import re
import traceback
from abc import abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator, ValidationError

from codemie.configs import config, logger
from codemie.core.error_constants import (
    AGENT_ERROR_FRIENDLY_MESSAGES,
    BUDGET_MESSAGE_KEY,
    CURRENT_COST_KEY,
    LITE_LLM_EXC_TYPE_TO_ERROR_CODE,
    LITELLM_ERROR_FRIENDLY_MESSAGES,
    LITELLM_ERROR_KEYWORDS,
    LITELLM_ERROR_LOG_PATTERN,
    LITELLM_EXCEPTION_NAME_PATTERN,
    MAX_BUDGET_KEY,
    ErrorCode,
    ErrorDetailLevel,
    ErrorCategory,
    HTTP_STATUS_TO_ERROR_CODE,
    ERROR_MESSAGE_PATTERNS,
    GUARDRAILS_REASON,
)


# ---------------------------------------------------------------------------
# Error models
# ---------------------------------------------------------------------------
class BaseError(BaseModel):
    error_code: ErrorCode = Field(..., description="Classified error code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[dict[str, Any]] = Field(None, description="Additional error context")


class ToolErrorDetails(BaseError):
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
    http_status: Optional[int] = Field(None, description="HTTP status code if applicable")
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


class AgentErrorDetails(BaseError):
    """
    Structured details for an agent-level error.

    Captures agent execution failures that are not related to specific tools,
    such as token limits, budget constraints, timeouts, callbacks, or network errors.

    Attributes:
        error_code: Classified error code for programmatic handling
        message: Human-readable error message
        details: Additional error context
        stacktrace: Full stacktrace for debugging (only in debug mode)
    """

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


class GuardrailErrorModel(BaseModel):
    """Guardrail error payload embedded as JSON in LiteLLM error message."""

    error_type: str = Field(description="Guardrail error type.")
    reason: str = Field(description="Human-readable explanation of why the guardrail triggered.")
    guardrail: str = Field(description="Name of the guardrail that triggered the exception.")
    stage: str = Field(description="Stage at which the guardrail was triggered.")
    version: str = Field(description="Gateway version (from pyproject.toml) that produced this error payload.")

    class Config:
        json_schema_extra = {
            "example": {
                "error_type": "HATE_SPEECH",
                "reason": GUARDRAILS_REASON,
                "guardrail": "hate_speech",
                "stage": "pre_call",
                "version": "1.1.1",
            }
        }


class LiteLLMErrorInner(BaseModel):
    """Inner error object from LiteLLM error response."""

    message: str | GuardrailErrorModel = Field(
        description=(
            "Original error message string or parsed guardrail payload "
            "when message contains JSON with guardrail_error structure."
        )
    )
    type_: str | None = Field(
        alias="type",
        default=None,
        description="Provider-specific error type identifier from LiteLLM/OpenAI response.",
    )

    param: str | None = Field(
        default=None, description="Name of the request parameter associated with this error, if any."
    )
    code: int = Field(description="Provider HTTP error code corresponding to this failure.")

    @field_validator("type_", mode="before")
    @classmethod
    def normalize_type_none(cls, value: str | None) -> str | None:
        """Treat literal string 'none' (case-insensitive) as None; proxy may send type as "None"."""
        if isinstance(value, str) and value.strip().lower() == "none":
            return None
        return value

    @field_validator("code", mode="before")
    @classmethod
    def code_to_int(cls, value: str | int) -> int:
        """Coerce code from string to int (e.g. JSON "401") or return as is."""
        return int(value)

    @field_validator("message", mode="before")
    @classmethod
    def parse_guardrail_message(cls, value) -> Any:
        """Pre-validate message: if it is a guardrail JSON payload, convert to GuardrailErrorModel."""
        try:
            return GuardrailErrorModel.model_validate_json(value)
        except ValidationError:
            return value

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "message": "litellm.AuthenticationError: AzureException AuthenticationError - "
                    "Unknown api key. No fallback model group found "
                    "for original model_group=gpt-4o-2024-11-20.",
                    "type": None,
                    "param": None,
                    "code": 401,
                },
                {
                    "message": {
                        "error_type": "HATE_SPEECH",
                        "reason": GUARDRAILS_REASON,
                        "guardrail": "hate_speech",
                        "stage": "pre_call",
                        "version": "1.1.1",
                    },
                    "type": None,
                    "param": None,
                    "code": 400,
                },
            ]
        }


class LiteLLMError(BaseError):
    """LiteLLM error response extracted from string"""

    error: LiteLLMErrorInner = Field(description="Structured LiteLLM error")

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "error": {
                        "message": "litellm.AuthenticationError: AzureException AuthenticationError - "
                        "Unknown api key. No fallback model group found for original "
                        "model_group=gpt-4o-2024-11-20.",
                        "type": None,
                        "param": None,
                        "code": 401,
                    },
                    "error_code": "lite_llm_authentication_error",
                    "message": "Authentication failed. Please check your API key.",
                    "details": None,
                },
                {
                    "error": {
                        "message": {
                            "error_type": "HATE_SPEECH",
                            "reason": GUARDRAILS_REASON,
                            "guardrail": "hate_speech",
                            "stage": "pre_call",
                            "version": "1.1.1",
                        },
                        "type": None,
                        "param": None,
                        "code": 400,
                    },
                    "error_code": "lite_llm_content_policy_violation_error",
                    "message": "Content was blocked by the content policy.",
                    "details": None,
                },
            ]
        }


class InternalError(BaseError):
    """
    Structured details for an internal (Codemie-level) error.

    Used when the agent fails with an unclassified exception; captures
    message, exception type, and stack trace for debugging.

    Attributes:
        message: Human-readable error message (e.g. "AI Agent run failed with error: ...")
        error_code: Human-readable error code
        details: Additional details
    """

    error_code: ErrorCode = Field(default=ErrorCode.PLATFORM_ERROR, description="Classified error code")

    @classmethod
    def from_exception(cls, exc: Exception | str) -> "InternalError":
        """Build InternalError from an exception."""

        message = str(exc)
        traceback_msg = traceback.format_exc() if isinstance(exc, Exception) else message
        exc_type = type(exc).__name__ if isinstance(exc, Exception) else config.GLOBAL_FALLBACK_MSG

        return cls.model_validate(
            {
                "message": config.GLOBAL_FALLBACK_MSG,
                "details": {
                    "type": exc_type,
                    "message": message,
                    "traceback": traceback_msg,
                },
            }
        )

    class Config:
        json_schema_extra = {
            "example": {
                "error_code": "platform_error",
                "message": "AI Agent run failed with error: ValueError: invalid input",
                "details": {
                    "type": "ZeroDivisionError",
                    "message": "division by zero",
                    "traceback": "Traceback (most recent call last):\n  ...",
                },
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
        lite_llm_error: LiteLLM-level error details (if applicable)
        internal: Codemie-level error details (if applicable)
        is_recoverable: Whether the error can be retried or recovered from
    """

    category: ErrorCategory = Field(..., description="High-level error category")
    agent_error: Optional[AgentErrorDetails] = Field(None, description="Agent-level error details")
    tool_errors: Optional[list[ToolErrorDetails]] = Field(None, description="Tool error details")
    lite_llm_error: Optional[LiteLLMError] = Field(None, description="LiteLLM-level error details")
    internal: Optional[InternalError] = Field(None, description="Codemie-level error details")
    is_recoverable: bool = Field(False, description="Whether the error is recoverable")

    __CATEGORY_TO_ERROR_ATTR = {
        ErrorCategory.AGENT: "agent_error",
        ErrorCategory.LITE_LLM: "lite_llm_error",
        ErrorCategory.INTERNAL: "internal",
    }

    def get_error(self) -> AgentErrorDetails | LiteLLMError | InternalError | None:
        """Return the error payload for this response. Supported categories: AGENT, LITE_LLM, INTERNAL.
        For category TOOL use the tool_errors attribute directly."""
        attr_name = self.__CATEGORY_TO_ERROR_ATTR[self.category]
        return getattr(self, attr_name, None)

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "category": "lite_llm",
                    "agent_error": None,
                    "tool_errors": None,
                    "lite_llm_error": {
                        "error": {
                            "message": "litellm.RateLimitError: rate limit exceeded",
                            "type": None,
                            "param": None,
                            "code": 429,
                        },
                        "error_code": "lite_llm_rate_limit_error",
                        "message": "The LLM service is temporarily overloaded due to rate limiting. "
                        "Please wait a moment and try again.",
                        "details": None,
                    },
                    "internal": None,
                    "is_recoverable": False,
                },
                {
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
                    "lite_llm_error": None,
                    "internal": None,
                    "is_recoverable": True,
                },
            ]
        }


# ---------------------------------------------------------------------------
# Exception classifier
# ---------------------------------------------------------------------------
class LiteLLMErrorClassifier:
    """Classifies exceptions as LiteLLM errors when proxy is enabled
    and the exception string matches the proxy format"""

    def _try_message_litellm_prefix(self, inner_msg: str) -> ErrorCode | None:
        """Extract exception name from 'litellm.ExceptionName' in message and map to code."""
        if match := LITELLM_EXCEPTION_NAME_PATTERN.search(inner_msg):
            # group(1) == exception class name after "litellm." (e.g. APIConnectionError, AuthenticationError)
            return LITE_LLM_EXC_TYPE_TO_ERROR_CODE.get(match.group(1), ErrorCode.LLM_UNKNOWN_ERROR)

    def _try_message_substring(self, inner_msg: str) -> ErrorCode | None:
        """Match known exception names as substrings in message (longest first)."""
        return next(
            (
                LITE_LLM_EXC_TYPE_TO_ERROR_CODE[k]
                for k in sorted(
                    LITE_LLM_EXC_TYPE_TO_ERROR_CODE.keys(),
                    key=len,
                    reverse=True,
                )
                if k in inner_msg
            ),
            ErrorCode.LLM_UNKNOWN_ERROR,
        )

    def _get_error_code(self, inner) -> ErrorCode | None:
        error_code = None
        if isinstance(inner.message, GuardrailErrorModel):
            error_code = ErrorCode.LITE_LLM_CONTENT_POLICY_VIOLATION_ERROR
        elif pre_error_code := LITE_LLM_EXC_TYPE_TO_ERROR_CODE.get(inner.type_, None):
            error_code = pre_error_code
        else:
            inner_msg = inner.message if isinstance(inner.message, str) else ""
            inner_msg_lower = inner_msg.lower()
            for key, value in LITELLM_ERROR_KEYWORDS.items():
                if any(kw in inner_msg_lower for kw in value):
                    error_code = key
                    break
            if error_code is None:
                error_code = self._try_message_litellm_prefix(inner_msg) or self._try_message_substring(inner_msg)
        return error_code

    def _extract_budget_from_exception(self, exc: Exception) -> dict[str, Any]:
        """Extract budget info from exception attributes (BudgetExceededError)."""
        details: dict[str, Any] = {}
        if hasattr(exc, CURRENT_COST_KEY) and hasattr(exc, MAX_BUDGET_KEY):
            current_cost = getattr(exc, CURRENT_COST_KEY, None)
            max_budget = getattr(exc, MAX_BUDGET_KEY, None)
            if current_cost is not None or max_budget is not None:
                details[CURRENT_COST_KEY] = current_cost
                details[MAX_BUDGET_KEY] = max_budget
        return details

    def _extract_budget_from_error_dict(self, error_dict: dict[str, Any]) -> dict[str, Any] | None:
        """Extract budget info from parsed error body (error.message)."""
        details: dict[str, Any] = {}
        try:
            error_inner = error_dict.get("error") or {}
            if isinstance(error_inner, dict):
                msg = error_inner.get("message", "")
                if msg and ("budget" in str(msg).lower() or "exceeded" in str(msg).lower()):
                    details[BUDGET_MESSAGE_KEY] = msg
        except (KeyError, TypeError):
            pass
        return details

    def _extract_budget_from_string(self, error_str: str) -> dict[str, Any]:
        """Extract budget message from string representation using regex."""
        details: dict[str, Any] = {}
        if ("Budget has been exceeded" in error_str or "budget_exceeded" in error_str.lower()) and (
            "{'error':" in error_str or '{"error":' in error_str
        ):
            match = re.search(r"'message': '([^']+)'", error_str)
            if match:
                details[BUDGET_MESSAGE_KEY] = match.group(1)
                return details
            match = re.search(r'"message": "([^"]+)"', error_str)
            if match:
                details[BUDGET_MESSAGE_KEY] = match.group(1)
        return details

    def _extract_schema_validation_context(self, inner_msg: str) -> dict[str, Any]:
        """Extract schema validation context from error message."""
        details: dict[str, Any] = {}
        schema_keywords = ["schema", "response_format", "json_schema"]
        msg_lower = inner_msg.lower()
        if any(kw in msg_lower for kw in schema_keywords) and inner_msg.strip():
            details["schema_validation_context"] = inner_msg.strip()
        return details

    def __get_add_info(self, exception, error_dict) -> dict[str, Any]:
        """Get additional information from the exception or error dictionary."""
        add_info = {}
        if isinstance(exception, Exception):
            add_info: dict[str, Any] | None = self._extract_budget_from_exception(exception)
        if not add_info:
            add_info: dict[str, Any] | None = self._extract_budget_from_error_dict(error_dict)
        if not add_info:
            add_info: dict[str, Any] | None = self._extract_budget_from_string(str(exception) if exception else "")

        return add_info

    def _enrich_details(self, result, error_code, inner, exception, error_dict) -> dict[str, Any]:
        """Enrich details with parsed context (budget, schema validation)."""
        inner_msg = inner.message if isinstance(inner.message, str) else ""
        if error_code == ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR:
            if add_info := self.__get_add_info(exception, error_dict):
                result["details"].update(add_info)

        elif error_code == ErrorCode.LITE_LLM_BAD_REQUEST_ERROR:
            schema_ctx = self._extract_schema_validation_context(inner_msg)
            if schema_ctx:
                result["details"].update(schema_ctx)

        return result

    def _parse_exception(self, exception: Exception | str) -> Optional[dict[str, Any]]:
        """
        Extract and parse LiteLLM error dict from error log string.

        The exception string must contain a fragment matching LITELLM_ERROR_LOG_PATTERN
        (proxy-style JSON with \"error\": {\"message\", \"type\", \"param\", \"code\"}).

        Returns:
            Dict if error object is found and parsed successfully, None otherwise.
        """
        if isinstance(exception, Exception):
            exception = str(exception)

        if not (match := LITELLM_ERROR_LOG_PATTERN.search(exception)):
            return None

        try:
            error_dict = ast.literal_eval(match.group(0))
            inner = LiteLLMErrorInner.model_validate(error_dict["error"])
        except Exception:
            return None

        error_code: ErrorCode | None = self._get_error_code(inner)
        result = {
            "error": inner,
            "details": error_dict,
            "error_code": error_code,
            "message": LITELLM_ERROR_FRIENDLY_MESSAGES.get(
                error_code,
                LITELLM_ERROR_FRIENDLY_MESSAGES[ErrorCode.LLM_UNKNOWN_ERROR],
            ),
        }

        result: dict[str, Any] = self._enrich_details(result, error_code, inner, exception, error_dict)

        return result

    def classify(self, exc: Exception | str) -> ErrorResponse | None:
        """Return ErrorResponse with category LITE_LLM if the exception string matches the proxy format, else None."""
        parsed: dict | None = self._parse_exception(exc)

        if parsed:
            return ErrorResponse.model_validate(
                {
                    "category": ErrorCategory.LITE_LLM,
                    "lite_llm_error": LiteLLMError.model_validate(parsed),
                }
            )


class AgentErrorClassifier:
    """Classifies exceptions as agent-level errors (budget, timeout, token limit, callback, network, etc.)."""

    def build_agent_error_dict(
        self,
        error_code: ErrorCode,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build dict for AgentErrorDetails.model_validate; uses friendly message map if message not provided."""
        msg = message or AGENT_ERROR_FRIENDLY_MESSAGES.get(
            error_code,
            config.AGENT_MSG_FALLBACK,
        )
        return {
            "error_code": error_code,
            "message": msg,
            "details": details,
            "stacktrace": traceback.format_exc(),
        }

    def get_agent_budget_message(self, details: dict[str, Any] | None) -> str | None:
        """Extract budget message from parsed details dict."""
        if details is None:
            return None
        return (details.get("error") or {}).get("message") or details.get("message")

    def _try_budget_exceeded(self, exception: Exception | str, msg: str) -> Optional[dict[str, Any]]:
        """
        Check if the exception indicates agent budget exceeded.
        Parses optional JSON-like details from the message and returns agent error dict on match.
        """
        if "budget_exceeded" not in msg:
            return None
        details = None
        try:
            if dict_match := re.search(r"\{.*\}", str(exception)):
                details = ast.literal_eval(dict_match.group(0))
        except (ValueError, SyntaxError, KeyError):
            pass

        budget_msg = self.get_agent_budget_message(details)
        merged_details = dict(details) if isinstance(details, dict) else {}
        if budget_msg:
            merged_details[BUDGET_MESSAGE_KEY] = budget_msg
        return self.build_agent_error_dict(ErrorCode.AGENT_BUDGET_EXCEEDED, message=None, details=merged_details)

    def _try_timeout(self, exc_obj: Exception | None, msg: str) -> Optional[dict[str, Any]]:
        """
        Check if the exception is a timeout (TimeoutError, asyncio.TimeoutError, or message).
        Returns agent error dict on match.
        """
        if exc_obj is not None:
            if isinstance(exc_obj, TimeoutError):
                return self.build_agent_error_dict(ErrorCode.AGENT_TIMEOUT)
            try:
                if isinstance(exc_obj, asyncio.TimeoutError):
                    return self.build_agent_error_dict(ErrorCode.AGENT_TIMEOUT)
            except Exception:
                pass
        if "timeout" in msg:
            return self.build_agent_error_dict(ErrorCode.AGENT_TIMEOUT)
        return None

    def _try_token_limit(self, msg: str) -> Optional[dict[str, Any]]:
        """
        Check if the exception indicates token/output limit (max_output_tokens, truncation, etc.).
        Returns agent error dict on match.
        """
        if not any(x in msg for x in ("max_output_tokens", "truncat", "token limit", "token limit exceeded")):
            return None
        return self.build_agent_error_dict(ErrorCode.AGENT_TOKEN_LIMIT)

    def _try_callback_failure(self, msg: str) -> Optional[dict[str, Any]]:
        """
        Check if the exception indicates a callback error or failure.
        Returns agent error dict on match.
        """
        if "callback" not in msg or ("error" not in msg and "failed" not in msg):
            return None
        return self.build_agent_error_dict(ErrorCode.AGENT_CALLBACK_FAILURE)

    def _try_network_error(self, exc_obj: Exception | None, msg: str) -> Optional[dict[str, Any]]:
        """
        Check if the exception is a network/connection error (ConnectionError, OSError with errno, or message).
        Returns agent error dict on match.
        """
        if exc_obj is not None:
            if isinstance(exc_obj, ConnectionError):
                return self.build_agent_error_dict(ErrorCode.AGENT_NETWORK_ERROR)
            if isinstance(exc_obj, OSError) and getattr(exc_obj, "errno", None) is not None:
                return self.build_agent_error_dict(ErrorCode.AGENT_NETWORK_ERROR)
        if "connection" in msg or "network" in msg:
            return self.build_agent_error_dict(ErrorCode.AGENT_NETWORK_ERROR)
        return None

    def _try_configuration_error(self, exc_obj: Exception | None, msg: str) -> Optional[dict[str, Any]]:
        """
        Check if the exception is a configuration/validation error (e.g. Pydantic ValidationError).
        Returns agent error dict on match.
        """
        if exc_obj is not None and type(exc_obj).__name__ == "ValidationError":
            return self.build_agent_error_dict(ErrorCode.AGENT_CONFIGURATION_ERROR)
        if "configuration" in msg or "validation" in msg:
            return self.build_agent_error_dict(ErrorCode.AGENT_CONFIGURATION_ERROR)
        return None

    def parse_exception(self, exception: Exception | str) -> Optional[dict[str, Any]]:
        """
        Parse an exception and return agent error dict if it is a known agent-level error.

        Tries, in order: budget exceeded, timeout, token limit, callback failure,
        network error, configuration error. First match wins.
        """
        exc_obj = exception if isinstance(exception, Exception) else None
        msg = str(exception).lower() if exception else ""

        result = self._try_budget_exceeded(exception, msg)
        if result is not None:
            return result
        result = self._try_timeout(exc_obj, msg)
        if result is not None:
            return result
        result = self._try_token_limit(msg)
        if result is not None:
            return result
        result = self._try_callback_failure(msg)
        if result is not None:
            return result
        result = self._try_network_error(exc_obj, msg)
        if result is not None:
            return result
        result = self._try_configuration_error(exc_obj, msg)
        if result is not None:
            return result
        return None

    def classify(self, exc: Exception) -> Optional[ErrorResponse]:
        """Return ErrorResponse with category AGENT if the
        exception matches agent error patterns (budget, timeout,
        token limit, callback, network, configuration), else None."""
        parsed: dict | None = self.parse_exception(exc)
        if parsed is None:
            return None

        agent_error = AgentErrorDetails.model_validate(parsed)
        logger.error(f"Agent error [{agent_error.error_code.value}]: {agent_error.message or str(exc)}")
        return ErrorResponse.model_validate(
            {
                "category": ErrorCategory.AGENT,
                "agent_error": agent_error,
            }
        )


class InternalErrorClassifier:
    """Fallback: treats any exception as internal (platform) error. Never returns None."""

    def classify(self, exc: Exception | str) -> ErrorResponse:
        """Return ErrorResponse with category INTERNAL;
        wraps any exception as InternalError (fallback when no other classifier matches)."""
        internal_error = InternalError.from_exception(exc)
        logger.error(
            f"AI Agent failed with error: {(internal_error.details or {}).get('traceback', '')}",
            exc_info=True,
        )
        return ErrorResponse.model_validate(
            {
                "category": ErrorCategory.INTERNAL,
                "internal": internal_error,
            }
        )


# ---------------------------------------------------------------------------
# Exception classification pipeline
# ---------------------------------------------------------------------------
@runtime_checkable
class ExceptionClassifier(Protocol):
    """Protocol for exception classifiers in the pipeline. First non-None result wins."""

    @abstractmethod
    def classify(self, exc: Exception | str) -> ErrorResponse | None:
        """Return ErrorResponse if this classifier recognizes the exception, else None."""
        ...


class ExceptionClassificationPipeline:
    """Runs a chain of exception classifiers; first non-None result is returned. Last is fallback."""

    def __init__(self, classifiers: list[ExceptionClassifier]) -> None:
        if not classifiers:
            raise ValueError("Pipeline must have at least one classifier (fallback).")
        self._classifiers = classifiers

    def handle(self, exc: Exception | str) -> ErrorResponse:
        """Run classifiers in order; return first non-None result. Last classifier must not return None."""
        for classifier in self._classifiers:
            if response := classifier.classify(exc):
                return response
        raise RuntimeError("Fallback classifier must always return an ErrorResponse.")

    @classmethod
    def get_pipeline(cls) -> "ExceptionClassificationPipeline":
        """Build the default chain: LiteLLM (if activated) -> Agent -> Internal (fallback)."""
        from codemie.enterprise import HAS_LITELLM  # import here to avoid circular import

        pipeline = []
        if HAS_LITELLM and config.LLM_PROXY_ENABLED and config.LLM_PROXY_MODE == "lite_llm":
            pipeline.append(LiteLLMErrorClassifier())

        pipeline.append(AgentErrorClassifier())
        pipeline.append(InternalErrorClassifier())

        return cls(pipeline)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
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
