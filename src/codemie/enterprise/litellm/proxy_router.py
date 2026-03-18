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

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from starlette.datastructures import Headers
from starlette.responses import StreamingResponse

if TYPE_CHECKING:
    from codemie.rest_api.security.user import User

# Import from codemie (allowed in integration layer)
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
    HEADER_CODEMIE_CLI,
    HEADER_CODEMIE_CLIENT,
    HEADER_CODEMIE_SESSION_ID,
    HEADER_CODEMIE_REQUEST_ID,
    HEADER_CODEMIE_CLI_MODEL,
    HEADER_CODEMIE_INTEGRATION,
    HEADER_CODEMIE_CLI_BRANCH,
    HEADER_CODEMIE_CLI_REPOSITORY,
    HEADER_CODEMIE_CLI_PROJECT,
)
from codemie.core.dependecies import litellm_context
from codemie.core.utils import calculate_token_cost
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.authentication import BEARER_AUTHORIZATION_HEADER
from codemie.rest_api.security.user import User
from codemie.enterprise.litellm.dependencies import check_user_budget
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.monitoring.llm_proxy_monitoring_service import LLMProxyMonitoringService
from codemie.core.errors import ErrorCode
from codemie.core.litellm_error_constants import STATUS_TO_ERROR_CODE
from codemie.service.monitoring.base_monitoring_service import send_log_metric
from codemie.service.monitoring.metrics_constants import LLM_ERROR_TOTAL_METRIC, MetricsAttributes

from .client import get_llm_proxy_client
from .dependencies import is_litellm_enabled, get_premium_username, is_premium_models_enabled
from .llm_factory import generate_litellm_headers_from_context

# Import proxy utils from loader (with enterprise package availability check)
from ..loader import inject_user_into_body, parse_usage_from_response


# HTTP headers that should NOT be forwarded between proxies (hop-by-hop headers)
# See: https://datatracker.ietf.org/doc/html/rfc2616#section-13.5.1
PROXY_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    # CodeMie-specific headers (used for internal tracking, not forwarded)
    HEADER_CODEMIE_INTEGRATION,
    HEADER_CODEMIE_CLIENT,
    HEADER_CODEMIE_SESSION_ID,
    HEADER_CODEMIE_REQUEST_ID,
    BEARER_AUTHORIZATION_HEADER.lower(),
}

# Hop-by-hop headers that must NOT be forwarded from upstream responses to clients.
# Starlette's StreamingResponse manages its own transfer framing; forwarding these
# from the upstream causes protocol conflicts (e.g. double chunked encoding).
PROXY_RESPONSE_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

# Proxy router for LiteLLM endpoints
# No prefix - endpoints will be registered with full paths (both /v1/* and /*)
proxy_router = APIRouter(
    tags=["LLM Proxy"],
    prefix="",
    dependencies=[],
)


def _extract_request_info(headers: Headers | httpx.Headers | dict) -> dict:
    """Extract request metadata from headers (uses codemie constants)."""
    return {
        CLIENT_TYPE: headers.get(HEADER_CODEMIE_CLIENT, "unknown"),
        SESSION_ID: headers.get(HEADER_CODEMIE_SESSION_ID, str(uuid.uuid4())),
        REQUEST_ID: headers.get(HEADER_CODEMIE_REQUEST_ID, str(uuid.uuid4())),
        LLM_MODEL: headers.get(HEADER_CODEMIE_CLI_MODEL, "unknown"),
        USER_AGENT: headers.get("User-Agent", "unknown"),
        CODEMIE_CLI: headers.get(HEADER_CODEMIE_CLI, ""),
        BRANCH: headers.get(HEADER_CODEMIE_CLI_BRANCH, ""),
        REPOSITORY: headers.get(HEADER_CODEMIE_CLI_REPOSITORY, ""),
        PROJECT: headers.get(HEADER_CODEMIE_CLI_PROJECT, ""),
    }


async def _extract_model_from_request_body(request: Request, request_info: dict) -> tuple[bytes, dict, str]:
    """
    Extract model from request body and validate against header.

    Args:
        request: FastAPI request
        request_info: Request metadata (contains header model if present)

    Returns:
        (body_bytes, body_json, model_name): Buffered body, parsed JSON, and extracted model
    """
    body_bytes = await request.body()

    model_from_header = request_info.get(LLM_MODEL, "unknown")
    model_from_body = "unknown"
    body_json = {}

    try:
        body_json = json.loads(body_bytes)
        model_from_body = body_json.get("model", "unknown")

        if model_from_header != "unknown" and model_from_header != model_from_body:
            logger.debug(
                f"Model mismatch detected! Header={model_from_header}, "
                f"Body={model_from_body}. Using body as source of truth."
            )

        logger.debug(f"Extracted model from request body: {model_from_body}")

    except json.JSONDecodeError as e:
        logger.debug(f"Invalid JSON in request body: {e}")
        model_from_body = model_from_header  # Fallback to header
    except Exception as e:
        logger.debug(f"Failed to extract model from body: {e}")
        model_from_body = model_from_header  # Fallback to header

    return body_bytes, body_json, model_from_body


def _inject_user_into_request_body_from_bytes(body_bytes: bytes, user_id: str, request_info: dict):
    """
    Inject user into buffered request body for LiteLLM budget tracking.

    Args:
        body_bytes: Buffered request body
        user_id: User ID to inject
        request_info: Request metadata (session_id, request_id)

    Returns:
        AsyncGenerator: Modified body stream with user injected
    """
    if inject_user_into_body is None:
        # Fallback: passthrough without user injection
        async def passthrough():
            yield body_bytes

        return passthrough()

    async def bytes_to_stream():
        yield body_bytes

    return inject_user_into_body(
        body_stream=bytes_to_stream(),
        content_type="application/json",
        username=user_id,
        session_id=request_info.get(SESSION_ID),
        request_id=request_info.get(REQUEST_ID),
    )


async def _create_body_stream_with_optional_injection(
    body_bytes: bytes, has_own_credentials: bool, user: User, request_info: dict
):
    """
    Create body stream with or without user injection.

    When the premium models budget feature is enabled and the requested model matches a
    premium alias, the injected LiteLLM username is derived as
    ``{user.username}_{budget_name}`` so that spend is attributed to the separate premium
    budget identity.  Otherwise the standard ``user.username`` is used.

    Args:
        body_bytes: Buffered request body
        has_own_credentials: Whether user has their own integration credentials
        user: Authenticated user
        request_info: Request metadata

    Returns:
        AsyncGenerator: Body stream (modified or original)
    """
    if has_own_credentials:
        # Passthrough without user injection - budget tracked against integration key
        logger.debug(f"Passthrough mode (own credentials): {user.username}")

        async def passthrough():
            yield body_bytes

        return passthrough()

    else:
        llm_model = request_info.get(LLM_MODEL, "unknown")
        username = get_premium_username(user.username, llm_model) or user.username

        logger.debug(f"Injecting user for budget tracking: {username} (model={llm_model})")

        check_user_budget(user_id=user.username)

        return _inject_user_into_request_body_from_bytes(
            body_bytes=body_bytes, user_id=username, request_info=request_info
        )


def _prepare_proxy_headers(request: Request) -> dict | Response:
    """
    Prepare headers for proxying (uses codemie services).

    Args:
        request: FastAPI request

    Returns:
        dict | Response: Headers or error response
    """
    # Extract and filter hop-by-hop headers
    headers = {k: v for k, v in request.headers.items() if k.lower() not in PROXY_HOP_BY_HOP_HEADERS}

    # Get integration key or use default
    integration_id = request.headers.get(HEADER_CODEMIE_INTEGRATION)
    if integration_id:
        try:
            api_key = _get_integration_api_key(integration_id)
            headers["Authorization"] = f"Bearer {api_key}"
        except HTTPException as e:
            return Response(content=e.detail, status_code=e.status_code)
    else:
        proxy_key = config.LITE_LLM_PROXY_APP_KEY or config.LITE_LLM_APP_KEY
        headers["Authorization"] = f"Bearer {proxy_key}"

    # Add project tags from context
    try:
        context = litellm_context.get(None)
        if context:
            additional_headers = generate_litellm_headers_from_context(context)
            if additional_headers:
                headers.update(additional_headers)
    except LookupError:
        pass

    return headers


@lru_cache(maxsize=128)
def _get_integration_api_key(integration_id: str) -> str:
    """
    Get decrypted API key from integration (uses codemie SettingsService).

    Args:
        integration_id: Integration ID

    Returns:
        str: Decrypted API key

    Raises:
        HTTPException: If integration not found
    """
    # Lazy import to avoid dependency issues
    from codemie.rest_api.models.settings import CredentialTypes, LiteLLMCredentials
    from codemie.service.settings.settings import SettingsService

    try:
        credentials = SettingsService.get_credentials(
            credential_type=CredentialTypes.LITE_LLM,
            integration_id=integration_id,
            required_fields=SettingsService.LITELLM_FIELDS,
            credential_class=LiteLLMCredentials,
        )

        if not credentials:
            raise HTTPException(status_code=404, detail=f"LLM Proxy integration '{integration_id}' not found")

        return credentials.api_key

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve API key for '{integration_id}': {str(e)}")


async def _parse_usage_with_cost(response_content: bytes, llm_model: str, is_streaming: bool) -> dict:
    """
    Thin wrapper: Get cost config from codemie service and call pure enterprise logic.

    Args:
        response_content: Response bytes
        llm_model: Model name
        is_streaming: Is streaming response

    Returns:
        dict: Usage data with costs
    """
    # Check if enterprise package is available
    if parse_usage_from_response is None:
        # Fallback: return zero usage
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "cache_creation_tokens": 0,
            "money_spent": 0.0,
            "cached_tokens_money_spent": 0.0,
        }

    # Get cost config from codemie service
    try:
        cost_config = llm_service.get_model_cost(llm_model)
    except Exception as e:
        logger.warning(f"Failed to get cost config for {llm_model}: {e}")
        cost_config = {}

    # Call pure enterprise business logic with codemie callback
    return parse_usage_from_response(
        response_content=response_content,
        is_streaming=is_streaming,
        cost_config=cost_config,
        cost_calculator=calculate_token_cost,
        llm_model=llm_model,
    )


def _emit_proxy_llm_error_log(
    response_status: int,
    user: "User",
    endpoint: str,
    llm_model: str,
    session_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Emit a structured log entry for ELK alerting when the proxy gets an error.

    Uses the existing ``send_log_metric`` mechanism so ELK can filter/alert
    on ``metric_name=codemie_llm_error_total`` + ``llm_error_code``.
    """
    error_code = STATUS_TO_ERROR_CODE.get(response_status, ErrorCode.LLM_UNKNOWN_ERROR).value

    try:
        send_log_metric(
            LLM_ERROR_TOTAL_METRIC,
            {
                MetricsAttributes.LLM_ERROR_CODE: error_code,
                MetricsAttributes.ERROR: f"LLM proxy returned HTTP {response_status}",
                MetricsAttributes.USER_ID: user.id if user else "-",
                MetricsAttributes.USER_EMAIL: user.username if user else "-",
                MetricsAttributes.LLM_MODEL: llm_model,
                "status_code": response_status,
                "endpoint": endpoint,
                "session_id": session_id or "-",
                "request_id": request_id or "-",
            },
        )
    except Exception as e:
        logger.warning(f"Failed to emit proxy LLM error metric: {e}")

    logger.warning(
        f"LLM proxy error: status={response_status}, error_code={error_code}, "
        f"user={user.username}, endpoint={endpoint}, model={llm_model}, "
        f"session={session_id}, request={request_id}"
    )


async def _streaming_response_with_usage_tracking(
    downstream_response: httpx.Response,
    user: "User",
    endpoint: str,
    request_info: dict,
    llm_model: str,
    background_tasks: BackgroundTasks,
):
    """
    Stream response with usage tracking (uses codemie services).

    Args:
        downstream_response: LiteLLM proxy response
        user: Authenticated user
        endpoint: Endpoint path
        request_info: Request metadata
        llm_model: Model name
        background_tasks: FastAPI background tasks

    Yields:
        bytes: Response chunks
    """
    buffer = bytearray()
    stream_completed = False
    chunks_received = 0
    total_bytes = 0

    session_id = request_info.get(SESSION_ID)
    request_id = request_info.get(REQUEST_ID)

    logger.debug(
        f"[STREAM-START] Usage tracking: session={session_id}, request={request_id}, "
        f"endpoint={endpoint}, model={llm_model}, status={downstream_response.status_code}"
    )

    try:
        async for chunk in downstream_response.aiter_raw():
            chunks_received += 1
            total_bytes += len(chunk)
            buffer.extend(chunk)
            logger.debug(
                f"[STREAM-CHUNK] session={session_id}, request={request_id}, chunk_size={len(chunk)}, "
                f"total_bytes={total_bytes}, chunk_num={chunks_received}"
            )
            yield chunk
        stream_completed = True
        logger.debug(
            f"[STREAM-COMPLETED] session={session_id}, request={request_id}, "
            f"total_chunks={chunks_received}, total_bytes={total_bytes}"
        )
    except Exception as e:
        logger.error(
            f"[STREAM-ERROR] Usage tracking interrupted: session={session_id}, request={request_id}, "
            f"endpoint={endpoint}, model={llm_model}, chunks={chunks_received}, bytes={total_bytes}, "
            f"exception={type(e).__name__}, error={str(e)}",
            exc_info=True,
        )
        # Return without re-raising so the generator closes cleanly.
        # The partial buffer is discarded; usage is not tracked for incomplete streams.
        return
    finally:
        try:
            await downstream_response.aclose()
            logger.debug(f"[STREAM-CLOSED] session={session_id}, request={request_id}, completed={stream_completed}")
        except Exception as close_err:
            logger.warning(
                f"[STREAM-CLOSE-ERROR] Failed to close downstream: session={session_id}, "
                f"request={request_id}, error={str(close_err)}"
            )

    # Track usage only when the full stream was received without errors
    if stream_completed and config.LLM_PROXY_TRACK_USAGE:
        content_type = downstream_response.headers.get("content-type", "")
        is_streaming = "text/event-stream" in content_type or "stream" in content_type

        logger.debug(
            f"[USAGE-PARSE-START] session={session_id}, request={request_id}, "
            f"content_type={content_type}, is_streaming={is_streaming}, buffer_size={len(buffer)}"
        )

        # Parse usage (calls pure enterprise logic via thin wrapper)
        usage_data = await _parse_usage_with_cost(
            response_content=bytes(buffer),
            llm_model=llm_model,
            is_streaming=is_streaming,
        )

        logger.debug(
            f"[USAGE-PARSE-RESULT] session={session_id}, request={request_id}, "
            f"input={usage_data['input_tokens']}, output={usage_data['output_tokens']}, "
            f"cached={usage_data['cached_tokens']}, cost=${usage_data['money_spent']:.6f}"
        )

        # Track usage if valid
        if usage_data["input_tokens"] > 0 or usage_data["output_tokens"] > 0:
            logger.debug(f"[USAGE-TRACK] session={session_id}, request={request_id}, queuing task")
            background_tasks.add_task(
                LLMProxyMonitoringService.track_usage,
                user=user,
                endpoint=endpoint,
                request_info=request_info,
                llm_model=llm_model,
                input_tokens=usage_data["input_tokens"],
                output_tokens=usage_data["output_tokens"],
                cached_tokens=usage_data["cached_tokens"],
                cache_creation_tokens=usage_data.get("cache_creation_tokens", 0),
                money_spent=usage_data["money_spent"],
                cached_tokens_money_spent=usage_data["cached_tokens_money_spent"],
                status_code=downstream_response.status_code,
            )
        else:
            logger.debug(f"[USAGE-SKIP] session={session_id}, request={request_id}, no tokens")


async def _passthrough_stream(downstream_response: httpx.Response, request_info: dict | None = None):
    """
    Forward raw downstream bytes to the client with safe error handling.

    Used for the non-usage-tracking path.  Ensures the downstream connection
    is always closed and that mid-stream exceptions do not propagate to
    Starlette (which would drop the client connection without sending the
    final HTTP terminator).

    Args:
        downstream_response: Open httpx streaming response
        request_info: Optional request metadata for logging

    Yields:
        bytes: Raw response chunks
    """
    if request_info is None:
        request_info = {}

    session_id = request_info.get(SESSION_ID, "unknown")
    request_id = request_info.get(REQUEST_ID, "unknown")
    chunks_received = 0
    total_bytes = 0

    logger.debug(
        f"[STREAM-START] Passthrough: session={session_id}, request={request_id}, "
        f"status={downstream_response.status_code}"
    )

    try:
        async for chunk in downstream_response.aiter_raw():
            chunks_received += 1
            total_bytes += len(chunk)
            logger.debug(
                f"[STREAM-CHUNK] Passthrough: session={session_id}, request={request_id}, "
                f"chunk_size={len(chunk)}, total_bytes={total_bytes}, chunk_num={chunks_received}"
            )
            yield chunk
        logger.debug(
            f"[STREAM-COMPLETED] Passthrough: session={session_id}, request={request_id}, "
            f"total_chunks={chunks_received}, total_bytes={total_bytes}"
        )
    except Exception as e:
        logger.error(
            f"[STREAM-ERROR] Passthrough interrupted: session={session_id}, request={request_id}, "
            f"chunks={chunks_received}, bytes={total_bytes}, "
            f"exception={type(e).__name__}, error={str(e)}",
            exc_info=True,
        )
    finally:
        try:
            await downstream_response.aclose()
            logger.debug(f"[STREAM-CLOSED] Passthrough: session={session_id}, request={request_id}")
        except Exception as close_err:
            logger.warning(
                f"[STREAM-CLOSE-ERROR] Passthrough close failed: session={session_id}, "
                f"request={request_id}, error={str(close_err)}"
            )


def _build_premium_budget_error_body(body_bytes: bytes) -> bytes | None:
    """Check whether *body_bytes* is a LiteLLM budget-exceeded error for a premium user.

    Returns replacement JSON bytes with a user-friendly message when all conditions hold:
      1. The response body is valid JSON with ``error.type == "budget_exceeded"``.
      2. The error message contains ``_{budget_name} over budget`` — i.e. the LiteLLM
         ``end_user`` was the derived premium identity ``{email}_{budget_name}``.
      3. The premium models budget feature is enabled (``LITELLM_PREMIUM_MODELS_BUDGET_NAME``
         is non-empty).

    Returns ``None`` when any condition is not met (caller should pass the original bytes
    through unchanged).
    """
    budget_name = config.LITELLM_PREMIUM_MODELS_BUDGET_NAME
    if not budget_name:
        return None

    try:
        error_data = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        return None

    error = error_data.get("error", {})
    if not isinstance(error, dict):
        return None

    if error.get("type") != "budget_exceeded":
        return None

    # The end_user injected into LiteLLM for premium models is "{email}_{budget_name}".
    # LiteLLM surfaces it in the message as: "End User=<identity> over budget."
    error_message = error.get("message", "")
    if f"_{budget_name} over budget" not in error_message:
        return None

    premium_models = config.LITELLM_PREMIUM_MODELS_ALIASES
    models_list = ", ".join(premium_models) if premium_models else "premium models"
    friendly_message = (
        f"Your budget for premium models ({models_list}) has been exceeded. "
        f"To continue, please switch to regular models. "
        f"If you are using codemie-cli, run 'codemie setup' and select a different model, "
        f"or pass the --model flag (e.g. codemie --model <regular-model>). "
        f"For more information refer to https://docs.codemie.ai/user-guide/codemie-cli/"
    )

    replacement = {
        "error": {
            "message": friendly_message,
            "type": "budget_exceeded",
            "param": None,
            "code": "400",
        }
    }
    return json.dumps(replacement).encode()


async def _handle_error_response(
    downstream_response: httpx.Response,
    response_headers: dict,
) -> Response:
    """Read an error response body and return an appropriate ``Response``.

    For premium-budget-exceeded errors the body is replaced with a user-friendly
    message (see ``_build_premium_budget_error_body``).  All other error bodies are
    forwarded unchanged.

    The downstream connection is always closed before returning.
    """
    try:
        body_bytes = await downstream_response.aread()
    except Exception as exc:
        logger.warning(f"[ERROR-BODY-READ] Failed to read error response body: {exc}")
        body_bytes = b""
    finally:
        try:
            await downstream_response.aclose()
        except Exception as close_err:
            logger.warning(f"[ERROR-BODY-CLOSE] Failed to close error response: {close_err}")

    if is_premium_models_enabled():
        replacement = _build_premium_budget_error_body(body_bytes)
        if replacement is not None:
            logger.debug("[PREMIUM-BUDGET-ERROR] Replacing raw budget error with user-friendly message")
            return Response(
                content=replacement,
                status_code=400,
                headers=response_headers,
                media_type="application/json",
            )

    return Response(
        content=body_bytes,
        status_code=downstream_response.status_code,
        headers=response_headers,
        media_type=downstream_response.headers.get("content-type"),
    )


async def _proxy_to_llm_proxy(
    request: Request,
    user: User,
    endpoint: str,
    background_tasks: BackgroundTasks,
):
    """
    Main proxy orchestrator (thin coordination layer).

    Coordinates pure enterprise logic with codemie services.

    Args:
        request: FastAPI request
        user: Authenticated user
        endpoint: Target endpoint path
        background_tasks: FastAPI background tasks

    Returns:
        StreamingResponse: Proxied response
    """
    start_time = datetime.now()

    # Extract request info (uses codemie constants)
    request_info = _extract_request_info(request.headers)

    body_bytes, request_body, model_from_body = await _extract_model_from_request_body(request, request_info)

    request_info[LLM_MODEL] = model_from_body

    # Check if proxy enabled
    if not is_litellm_enabled():
        raise HTTPException(
            status_code=400,
            detail=f"LLM Proxy endpoint {endpoint} not available. LLM_PROXY_ENABLED={config.LLM_PROXY_ENABLED}",
        )

    # Check if user has their own integration credentials
    # When using own credentials, budget is tracked against the integration key, NOT the user
    has_own_credentials = request.headers.get(HEADER_CODEMIE_INTEGRATION) is not None

    # Create body stream (with or without user injection)
    body_stream = await _create_body_stream_with_optional_injection(
        body_bytes=body_bytes, has_own_credentials=has_own_credentials, user=user, request_info=request_info
    )

    # Extract IDs for logging
    session_id = request_info.get(SESSION_ID)
    request_id = request_info.get(REQUEST_ID)
    llm_model = request_info.get(LLM_MODEL, "unknown")

    logger.debug(
        f"LLM proxy: session={session_id}, request={request_id}, "
        f"user={user.username}, endpoint={endpoint}, model={llm_model}"
    )

    # Prepare headers (uses codemie services)
    headers = _prepare_proxy_headers(request)
    if isinstance(headers, Response):
        return headers

    # Proxy request
    url = httpx.URL(path=endpoint)

    try:
        llm_proxy_client = get_llm_proxy_client()

        downstream_request = llm_proxy_client.build_request(
            method=request.method,
            url=url,
            headers=headers,
            content=body_stream,
            timeout=config.LLM_PROXY_TIMEOUT,
        )

        logger.debug(
            f"[REQUEST-SENT] session={session_id}, request={request_id}, method={request.method}, "
            f"endpoint={endpoint}, timeout={config.LLM_PROXY_TIMEOUT}"
        )

        downstream_response = await llm_proxy_client.send(downstream_request, stream=True)

        end_time = datetime.now()
        response_status = downstream_response.status_code
        duration_ms = (end_time - start_time).total_seconds() * 1000

        logger.debug(
            f"[RESPONSE-RECEIVED] session={session_id}, request={request_id}, status={response_status}, "
            f"duration_ms={duration_ms:.1f}, content_type={downstream_response.headers.get('content-type', 'unknown')}"
        )

        # Track metrics
        background_tasks.add_task(
            LLMProxyMonitoringService.track_proxy_metrics,
            user=user,
            endpoint=endpoint,
            request_info=request_info,
            response_status=response_status,
            start_time=start_time,
            end_time=end_time,
            request_body=request_body,
        )

        # Emit structured LLM error log for non-successful responses (alertable in ELK)
        if response_status >= 400:
            _emit_proxy_llm_error_log(
                response_status=response_status,
                user=user,
                endpoint=endpoint,
                llm_model=llm_model,
                session_id=session_id,
                request_id=request_id,
            )

    except httpx.RequestError as e:
        end_time = datetime.now()

        logger.error(
            f"[PROXY-ERROR] Request error: session={session_id}, request={request_id}, "
            f"endpoint={endpoint}, exception={type(e).__name__}, error={str(e)}",
            exc_info=True,
        )

        # Track error metrics
        background_tasks.add_task(
            LLMProxyMonitoringService.track_proxy_metrics,
            user=user,
            endpoint=endpoint,
            request_info=request_info,
            request_body=request_body,
            response_status=503,
            start_time=start_time,
            end_time=end_time,
            error_message=str(e),
        )

        return Response(content=f"Error connecting to downstream service: {e}", status_code=503)

    # Strip hop-by-hop headers before forwarding the upstream response.
    # Starlette's StreamingResponse manages its own transfer framing, so
    # forwarding headers like transfer-encoding or connection from the upstream
    # would create protocol conflicts with the client.
    response_headers = {
        k: v for k, v in downstream_response.headers.items() if k.lower() not in PROXY_RESPONSE_HOP_BY_HOP_HEADERS
    }

    # Return streaming response with optional usage tracking
    if config.LLM_PROXY_TRACK_USAGE and response_status == 200:
        logger.debug(f"[STREAMING-PATH] session={session_id}, request={request_id}, using usage_tracking path")
        return StreamingResponse(
            _streaming_response_with_usage_tracking(
                downstream_response=downstream_response,
                user=user,
                endpoint=endpoint,
                request_info=request_info,
                llm_model=llm_model,
                background_tasks=background_tasks,
            ),
            status_code=downstream_response.status_code,
            headers=response_headers,
            media_type=downstream_response.headers.get("content-type"),
        )
    else:
        logger.debug(
            f"[STREAMING-PATH] session={session_id}, request={request_id}, using passthrough "
            f"(track_usage={config.LLM_PROXY_TRACK_USAGE}, status={response_status})"
        )
        # Error responses are small — read them fully so we can inspect / replace the body.
        if response_status >= 400:
            return await _handle_error_response(downstream_response, response_headers)
        return StreamingResponse(
            _passthrough_stream(downstream_response, request_info),
            status_code=downstream_response.status_code,
            headers=response_headers,
            media_type=downstream_response.headers.get("content-type"),
        )


def _create_proxy_endpoint(endpoint: str):
    """
    Factory to create proxy endpoint handler with dynamic path parameters.

    SECURITY NOTE: This function uses exec() to dynamically generate function signatures.
    This is safe because:
    1. Endpoint comes from server configuration (LITE_LLM_PROXY_ENDPOINTS), not user input
    2. Path parameter names are validated to contain only alphanumeric characters and underscores
    3. FastAPI requires explicit parameters in function signatures (cannot use **kwargs)
    4. All inputs are controlled by server administrators, not end users

    Args:
        endpoint: Endpoint path from server config (may contain path parameters like {model_name})

    Returns:
        Async function that handles the proxy request with proper FastAPI signature

    Raises:
        ValueError: If path parameter names contain invalid characters
    """
    path_params = re.findall(r'\{(\w+)}', endpoint)

    if not path_params:
        # Simple handler without path parameters
        async def proxy_handler(
            request: Request,
            background_tasks: BackgroundTasks,
            user: User = Depends(authenticate),
        ):
            return await _proxy_to_llm_proxy(
                request=request, user=user, endpoint=endpoint, background_tasks=background_tasks
            )

        return proxy_handler

    # SECURITY VALIDATION: Ensure path parameters contain only safe characters
    # This prevents any potential code injection through parameter names
    for param in path_params:
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', param):
            raise ValueError(
                f"Invalid path parameter name '{param}' in endpoint '{endpoint}'. "
                f"Parameter names must be valid Python identifiers (alphanumeric + underscore only)."
            )

    # Dynamic handler with path parameters
    param_annotations = ', '.join([f'{p}: str' for p in path_params])

    func_code = f"""
async def proxy_handler(
    request: Request,
    background_tasks: BackgroundTasks,
    {param_annotations},
    user: User = Depends(authenticate)
):
    forward_path = endpoint_template
    param_values = {{{', '.join([f"'{p}': {p}" for p in path_params])}}}
    for param_name, param_value in param_values.items():
        forward_path = forward_path.replace(f"{{{{{{param_name}}}}}}", param_value)

    return await _proxy_to_llm_proxy(
        request=request, user=user, endpoint=forward_path, background_tasks=background_tasks
    )
"""

    namespace = {
        'Request': Request,
        'BackgroundTasks': BackgroundTasks,
        'User': User,
        'Depends': Depends,
        'authenticate': authenticate,
        '_proxy_to_llm_proxy': _proxy_to_llm_proxy,
        'endpoint_template': endpoint,
    }

    exec(func_code, namespace)
    return namespace['proxy_handler']


def register_proxy_endpoints():
    """
    Explicitly register proxy endpoints if LiteLLM is enabled.

    This function should be called from the router module to register
    all configured LiteLLM proxy endpoints on the proxy_router.

    Design principle: Explicit is better than implicit.
    This makes endpoint registration visible and testable.
    """
    if not is_litellm_enabled():
        logger.debug("LiteLLM not enabled, skipping proxy endpoint registration")
        return

    logger.info("Registering LiteLLM proxy endpoints")

    # Register all proxy endpoints from configuration
    for endpoint_config in config.LITE_LLM_PROXY_ENDPOINTS:
        try:
            if isinstance(endpoint_config, dict):
                endpoint_path = endpoint_config.get("path")
                http_methods = endpoint_config.get("methods", ["POST"])
                if not endpoint_path:
                    logger.error(f"Endpoint config missing 'path': {endpoint_config}")
                    continue
            else:
                logger.error(f"Invalid endpoint config: {endpoint_config}")
                continue

            safe_name = endpoint_path.replace('/', '_').replace('{', '').replace('}', '').replace(':', '_')

            proxy_router.add_api_route(
                path=endpoint_path,
                endpoint=_create_proxy_endpoint(endpoint=endpoint_path),
                methods=http_methods,
                name=f"llm_proxy{safe_name}",
            )

            logger.debug(f"Registered LLM proxy endpoint: {', '.join(http_methods)} {endpoint_path}")

        except Exception as e:
            logger.error(f"Failed to register endpoint '{endpoint_config}': {e}")
