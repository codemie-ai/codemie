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

"""LLM Proxy monitoring service for tracking proxy requests and usage."""

from datetime import datetime
from typing import Optional

from codemie.configs import config, logger
from codemie.core.constants import (
    REQUEST_ID,
    LLM_MODEL,
    SESSION_ID,
    CLIENT_TYPE,
    USER_AGENT,
    CODEMIE_CLI,
    BRANCH,
    REPOSITORY,
    PROJECT,
)
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService, limit_string
from codemie.service.monitoring.metrics_constants import MetricsAttributes

LLM_PROXY_REQUESTS_TOTAL = "llm_proxy_requests_total"
LLM_PROXY_ERRORS_TOTAL = "llm_proxy_errors_total"
LLM_PROXY_USAGE = "codemie_litellm_proxy_usage"

# LLM Proxy - Metric attribute keys
ENDPOINT = "endpoint"
RESPONSE_STATUS = "response_status"
DURATION_MS = "duration_ms"
ERROR_CODE = "error_code"
ERROR_MESSAGE = "error_message"


class LLMProxyMonitoringService(BaseMonitoringService):
    """Service for monitoring LLM proxy requests and usage patterns."""

    @classmethod
    def track_proxy_metrics(
        cls,
        user: User,
        endpoint: str,
        request_info: dict,
        request_body: dict,
        response_status: int,
        start_time: datetime,
        end_time: datetime,
        error_message: Optional[str] = None,
    ):
        """
        Unified method to track BOTH metrics AND LangFuse traces.

        Performance optimized: Non-blocking, uses singleton Langfuse client, no per-request flush().

        Metrics tracked:
        - Request count for all requests (with duration_ms attribute for aggregations)
        - Error count for failed requests (status >= 400)
        - LangFuse traces (if config.LANGFUSE_TRACES and config.LLM_PROXY_LANGFUSE_TRACES are enabled)

        Args:
            user: Authenticated user
            endpoint: API endpoint path (e.g., "/v1/chat/completions")
            request_info: Request information from headers (client_type, model, session_id, etc.)
            request_body: LLM request body (messages, model, parameters, etc.) for trace input
            response_status: HTTP response status code
            start_time: Request start datetime
            end_time: Request end datetime
            error_message: Optional error message for failed requests (status >= 400)
        """
        try:
            # Calculate duration here (not in router)
            duration_ms = (end_time - start_time).total_seconds() * 1000

            # Track proxy request (synchronous, non-blocking)
            # Langfuse SDK uses background threads internally, no need for asyncio.to_thread()
            cls._track_proxy_request(
                user=user,
                endpoint=endpoint,
                request_info=request_info,
                duration_ms=duration_ms,
                status_code=response_status,
            )

            # Track errors if request failed
            if response_status >= 400:
                cls._track_error(
                    user=user,
                    endpoint=endpoint,
                    request_info=request_info,
                    status_code=response_status,
                    error_message=error_message,
                )

            # Track LangFuse trace (synchronous, non-blocking)
            cls._track_langfuse_trace(
                user=user,
                endpoint=endpoint,
                request_info=request_info,
                request_body=request_body,
                response_status=response_status,
                duration_ms=duration_ms,
                start_time=start_time,
                end_time=end_time,
                error_message=error_message,
            )

        except Exception as e:
            # Never let metrics/tracing tracking crash the request
            logger.warning(f"Error tracking proxy metrics/traces: {e}", exc_info=True)

    @classmethod
    def _track_proxy_request(
        cls,
        user: User,
        endpoint: str,
        request_info: dict,
        duration_ms: float,
        status_code: int,
    ):
        """
        Internal method to track LLM proxy request count.

        Counts each request (success and failure) with duration_ms as an attribute.
        Use duration_ms attribute to calculate avg/max/min request duration.
        Use response_status attribute to filter by success/failure in dashboards.

        Args:
            user: User to track
            endpoint: API endpoint (e.g., /v1/chat/completions)
            request_info: Request metadata (client_type, model, session_id, request_id, user_agent)
            duration_ms: Request duration in milliseconds (stored as attribute)
            status_code: HTTP status code
        """
        try:
            # Validate duration
            if duration_ms < 0:
                duration_ms = 0

            # Build base attributes
            attributes = {
                MetricsAttributes.USER_ID: user.id,
                MetricsAttributes.USER_NAME: user.username,
                MetricsAttributes.USER_EMAIL: user.username,
                ENDPOINT: endpoint,
                RESPONSE_STATUS: status_code,
                DURATION_MS: int(duration_ms),
            }

            # Add sanitized request context attributes
            sanitized_info = cls._sanitize_request_info(request_info)
            attributes.update(sanitized_info)

            # Send request count metric
            cls.send_count_metric(
                name=LLM_PROXY_REQUESTS_TOTAL,
                attributes=attributes,
            )

        except Exception as e:
            logger.warning(f"Error tracking proxy request: {str(e)}")

    @classmethod
    def _track_error(
        cls,
        user: User,
        endpoint: str,
        request_info: dict,
        status_code: int,
        error_message: Optional[str] = None,
    ):
        """
        Internal method to track LLM proxy errors.

        Counts all errors (status >= 400) with error_code attribute for categorization.
        Use error_code/response_status to filter by error type (404, 429, 500, etc.) in dashboards.

        Args:
            user: Authenticated user
            endpoint: API endpoint
            request_info: Request information (client_type, model, session_id, request_id, user_agent)
            status_code: HTTP status code
            error_message: Optional error message details
        """
        try:
            # Build base attributes
            attributes = {
                MetricsAttributes.USER_ID: user.id,
                MetricsAttributes.USER_NAME: user.username,
                MetricsAttributes.USER_EMAIL: user.username,
                ENDPOINT: endpoint,
                ERROR_CODE: status_code,
                RESPONSE_STATUS: status_code,
            }

            # Add sanitized request context attributes
            sanitized_info = cls._sanitize_request_info(request_info)
            attributes.update(sanitized_info)

            # Add error message if provided
            if error_message:
                attributes[ERROR_MESSAGE] = limit_string(error_message)

            # Send error metric
            cls.send_count_metric(
                name=LLM_PROXY_ERRORS_TOTAL,
                attributes=attributes,
            )

        except Exception as e:
            logger.warning(f"Error tracking error metric: {str(e)}")

    @classmethod
    def _track_langfuse_trace(
        cls,
        user: User,
        endpoint: str,
        request_info: dict,
        request_body: dict,
        response_status: int,
        duration_ms: float,
        start_time: datetime,
        end_time: datetime,
        error_message: Optional[str] = None,
    ):
        """
        Internal method to send LangFuse trace.

        Performance optimized: Uses singleton Langfuse client, no blocking flush(), simplified trace structure.

        Creates trace with exact same structure as agents:
        - Same metadata fields: langfuse_session_id, langfuse_user_id, langfuse_tags, run_name, llm_model
        - Same tag format: "key:value" (e.g., "llm_model:gpt-4")
        - Session grouping via session_id

        Args:
            user: Authenticated user
            endpoint: API endpoint path
            request_info: Request information (SESSION_ID, LLM_MODEL, CLIENT_TYPE, REQUEST_ID, USER_AGENT)
            request_body: LLM request body (messages, model, parameters, etc.)
            response_status: HTTP status code
            duration_ms: Request duration in milliseconds
            start_time: Request start datetime
            end_time: Request end datetime
            error_message: Optional error message for failed requests
        """
        # Check if LLM proxy tracing is enabled
        if not config.LLM_PROXY_LANGFUSE_TRACES:
            return

        # Lazy import breaks the circular dependency:
        # llm_proxy_monitoring_service → enterprise → litellm.proxy_router → llm_proxy_monitoring_service
        from codemie.enterprise.langfuse import get_langfuse_client_or_none  # noqa: PLC0415

        # Get LangFuse client (returns None if not available or disabled)
        langfuse = get_langfuse_client_or_none()
        if langfuse is None:
            return

        # Session ID required for grouping (same as agents)
        session_id = request_info.get(SESSION_ID)
        if not session_id:
            logger.debug("Skipping LangFuse trace: no session_id in request")
            return

        try:
            # Extract fields from request_info
            model = request_info.get(LLM_MODEL, "unknown")
            client_type = request_info.get(CLIENT_TYPE, "unknown")
            request_id = request_info.get(REQUEST_ID)
            user_agent = request_info.get(USER_AGENT, "unknown")

            # Build tags with EXACT same format as agents: "key:value"
            langfuse_tags = [
                f"llm_model:{model}",
                f"endpoint:{endpoint}",
                f"client_type:{client_type}",
                f"status:{response_status}",
            ]

            # Create trace name (similar to agent "run_name")
            trace_name = f"llm_proxy{endpoint.replace('/v1/', '_').replace('/', '_')}"

            # Create span using SDK v3 API
            # start_span() creates a top-level trace span
            span = langfuse.start_span(
                name=trace_name,
                input=request_body,  # LLM request body (messages, model, parameters, etc.)
                metadata={
                    # CRITICAL: Use EXACT same field names as agents
                    "langfuse_session_id": session_id,
                    "langfuse_user_id": user.username,
                    "langfuse_tags": langfuse_tags,
                    "run_name": trace_name,
                    "llm_model": model,
                    # Additional proxy-specific metadata
                    "endpoint": endpoint,
                    "response_status": response_status,
                    "duration_ms": int(duration_ms),
                    "request_id": request_id,
                    "client_type": client_type,
                    "user_agent": user_agent,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "user_id": user.username,
                    "session_id": session_id,
                },
            )

            # Update span with output and status
            span.update(
                output={"status": response_status, "duration_ms": int(duration_ms)},
                level="ERROR" if response_status >= 400 else "DEFAULT",
                status_message=error_message if error_message else "success",
            )

            # End the span
            span.end()

            # NO flush() - let SDK batch and auto-flush in background
            # The SDK will batch traces and flush every 10 seconds or when 1000 traces accumulate

            logger.debug(
                f"LangFuse trace queued: name={trace_name}, session_id={session_id}, "
                f"user={user.username}, model={model}, status={response_status}, "
                f"duration={duration_ms:.2f}ms"
            )

        except Exception as e:
            # Langfuse SDK has built-in error handling and graceful degradation
            logger.warning(f"Error sending LangFuse trace: {str(e)}")

    @classmethod
    def _sanitize_request_info(cls, request_info: dict) -> dict:
        """
        Sanitize request_info to prevent memory bloat.

        Limits string lengths and removes potentially large fields to ensure
        bounded memory usage for metrics and traces.

        Args:
            request_info: Raw request information dictionary

        Returns:
            Sanitized dictionary with size-limited values
        """
        sanitized = {}

        # Maximum string length for any field
        max_string_length = 500

        for key, value in request_info.items():
            if value is None:
                continue

            # Limit string lengths
            if isinstance(value, str):
                sanitized[key] = limit_string(value, max_length=max_string_length)
            # Keep numeric and boolean values as-is
            elif isinstance(value, (int, float, bool)):
                sanitized[key] = value
            # Convert other types to string with length limit
            else:
                sanitized[key] = limit_string(str(value), max_length=max_string_length)

        return sanitized

    @staticmethod
    def _is_cli_request(request_info: dict) -> bool:
        """Return True if the request originated from the codemie-code CLI."""
        return bool(request_info.get(CODEMIE_CLI))

    @classmethod
    def track_usage(
        cls,
        user: User,
        endpoint: str,
        request_info: dict,
        llm_model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        money_spent: float,
        cached_tokens_money_spent: float,
        status_code: int,
        cache_creation_tokens: int = 0,
    ):
        """
        Track token usage and cost for LiteLLM proxy requests.

        Emits ``codemie_litellm_proxy_usage`` with a ``cli_request`` attribute
        derived from the X-CodeMie-CLI header to differentiate CLI from non-CLI traffic.

        Args:
            user: Authenticated user
            endpoint: API endpoint path (e.g., "/v1/chat/completions")
            request_info: Request information from headers (client_type, model, session_id, etc.)
            llm_model: Model name used
            input_tokens: Number of input/prompt tokens
            output_tokens: Number of output/completion tokens
            cached_tokens: Number of cache-read tokens (for Anthropic prompt caching)
            money_spent: Total cost in USD
            cached_tokens_money_spent: Cost of cached tokens in USD
            status_code: HTTP response status code
            cache_creation_tokens: Number of cache-creation tokens (Anthropic prompt caching)
        """
        try:
            cli_request = cls._is_cli_request(request_info)

            attributes = {
                MetricsAttributes.USER_ID: user.id,
                MetricsAttributes.USER_NAME: user.username,
                MetricsAttributes.USER_EMAIL: user.username,
                MetricsAttributes.LLM_MODEL: llm_model,
                MetricsAttributes.INPUT_TOKENS: input_tokens,
                MetricsAttributes.OUTPUT_TOKENS: output_tokens,
                MetricsAttributes.CACHE_READ_INPUT_TOKENS: cached_tokens,
                MetricsAttributes.CACHE_CREATION_TOKENS: cache_creation_tokens,
                MetricsAttributes.MONEY_SPENT: money_spent,
                MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: cached_tokens_money_spent,
                ENDPOINT: endpoint,
                RESPONSE_STATUS: status_code,
                BRANCH: request_info.get(BRANCH, ""),
                REPOSITORY: request_info.get(REPOSITORY, ""),
                MetricsAttributes.PROJECT: request_info.get(PROJECT, ""),
                "cli_request": cli_request,
            }

            sanitized_info = cls._sanitize_request_info(request_info)
            attributes.update(sanitized_info)

            cls.send_count_metric(
                name=LLM_PROXY_USAGE,
                attributes=attributes,
            )

            logger.debug(
                f"LLM proxy usage tracked: user={user.username}, model={llm_model}, "
                f"input={input_tokens}, output={output_tokens}, cached={cached_tokens}, "
                f"cache_creation={cache_creation_tokens}, cli_request={cli_request}, "
                f"cost=${money_spent:.6f}, cached_cost=${cached_tokens_money_spent:.6f}"
            )

        except Exception as e:
            logger.warning(f"Error tracking proxy usage: {str(e)}")
