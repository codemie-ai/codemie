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
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from packaging.version import InvalidVersion, Version
from starlette.datastructures import Headers
from starlette.responses import StreamingResponse
from codemie.service.monitoring.base_monitoring_service import limit_string, send_log_metric
from codemie.service.monitoring.metrics_constants import MetricsAttributes, LLM_ERROR_TOTAL_METRIC
from codemie.configs.logger import logging_user_id, current_user_email, logging_conversation_id, logging_uuid
from codemie.core.errors import ErrorResponse, ExceptionClassificationPipeline

if TYPE_CHECKING:
    from codemie.rest_api.security.user import User

# Import from codemie (allowed in integration layer)
from codemie.configs import config, logger
from codemie.clients.postgres import get_async_session
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
from codemie.rest_api.security.authentication import BEARER_AUTHORIZATION_HEADER, authenticate
from codemie.rest_api.security.user import User
from codemie.enterprise.litellm.dependencies import check_user_budget
from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
from codemie.service.budget.budget_resolution_service import (
    BudgetScope,
    ResolvedBudgetContext,
    _resolution_cache,
    budget_resolution_service,
)
from codemie.service.budget.budget_service import budget_service
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.monitoring.llm_proxy_monitoring_service import LLMProxyMonitoringService

from .client import get_llm_proxy_client
from .budget_categories import BudgetCategory
from .credentials import ResolvedLiteLLMUserCredentials, resolve_litellm_user_credentials
from .dependencies import (
    get_category_budget_id,
    get_premium_username,
    is_litellm_enabled,
    is_premium_models_enabled,
)
from .llm_factory import generate_litellm_headers_from_context
from .project_member_runtime_sync import ensure_project_member_runtime_ready
from .runtime_budget_selection import RuntimeBudgetMode, select_runtime_budget_mode
from codemie.repository.project_budget_repository import project_budget_assignment_repository

# Import proxy utils from loader (with enterprise package availability check)
from ..loader import inject_user_into_body, parse_usage_from_response


LITELLM_CUSTOMER_ID_HEADER = "x-litellm-customer-id"

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
    LITELLM_CUSTOMER_ID_HEADER,
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


def _sanitize_local_response_headers(headers: dict) -> dict:
    """Drop upstream body/framing headers when constructing a new local response."""
    return {k: v for k, v in headers.items() if k.lower() not in {"content-length", "content-encoding"}}


def _anonymized_key_fingerprint(secret_value: str | None) -> str | None:
    """Return a stable non-secret fingerprint for provider keys used in logs."""
    if not secret_value:
        return None
    return sha256(secret_value.encode("utf-8")).hexdigest()[:12]


def _log_proxy_auth_source_selected(
    *,
    auth_source: str,
    api_key: str | None,
    request_info: dict | None,
    integration_id: str | None = None,
) -> None:
    integration_part = f"integration_id={integration_id!r} " if integration_id else ""
    logger.debug(
        f"budget_event=proxy_auth_source_selected component=proxy_router "
        f"auth_source={auth_source} {integration_part}api_key_present={bool(api_key)} "
        f"api_key_fingerprint={_anonymized_key_fingerprint(api_key)!r} "
        f"customer_header_applied={bool(request_info and request_info.get('litellm_customer_id'))} "
        f"provider_headers_applied={bool(request_info and request_info.get('budget_provider_headers'))}"
    )


def _apply_proxy_auth_header(
    headers: dict,
    request_info: dict | None,
    user_credentials: ResolvedLiteLLMUserCredentials | None = None,
) -> Response | None:
    if request_info is not None and request_info.get("budget_provider_api_key"):
        provider_key = request_info["budget_provider_api_key"]
        headers["Authorization"] = f"Bearer {provider_key}"
        _log_proxy_auth_source_selected(
            auth_source="project_budget_key",
            api_key=provider_key,
            request_info=request_info,
        )
        return None

    if user_credentials is not None:
        api_key = user_credentials.credentials.api_key
        headers["Authorization"] = f"Bearer {api_key}"
        _log_proxy_auth_source_selected(
            auth_source="user_credentials",
            api_key=api_key,
            request_info=request_info,
            integration_id=user_credentials.alias,
        )
        return None

    proxy_key = config.LITE_LLM_PROXY_APP_KEY or config.LITE_LLM_APP_KEY
    headers["Authorization"] = f"Bearer {proxy_key}"
    _log_proxy_auth_source_selected(auth_source="app_key", api_key=proxy_key, request_info=request_info)
    return None


def _apply_budget_provider_headers(headers: dict, request_info: dict | None) -> None:
    if request_info is not None and request_info.get("litellm_customer_id"):
        headers[LITELLM_CUSTOMER_ID_HEADER] = request_info["litellm_customer_id"]
        logger.debug(
            f"budget_event=proxy_customer_header_applied component=proxy_router "
            f"header_name={LITELLM_CUSTOMER_ID_HEADER!r} "
            f"litellm_customer_key={request_info['litellm_customer_id']!r}"
        )
    if request_info is not None and request_info.get("budget_provider_headers"):
        headers.update(request_info["budget_provider_headers"])
        logger.debug(
            f"budget_event=proxy_provider_headers_applied component=proxy_router "
            f"provider_header_names={sorted(request_info['budget_provider_headers'].keys())!r}"
        )


def _apply_context_headers(headers: dict) -> None:
    try:
        context = litellm_context.get(None)
        if context:
            additional_headers = generate_litellm_headers_from_context(context)
            if additional_headers:
                headers.update(additional_headers)
    except LookupError:
        pass


# Proxy router for LiteLLM endpoints
# No prefix - endpoints will be registered with full paths (both /v1/* and /*)
proxy_router = APIRouter(
    tags=["LLM Proxy"],
    prefix="",
    dependencies=[],
)


def _check_cli_version(request: Request) -> None:
    """Reject CLI requests whose X-CodeMie-CLI version is below the configured minimum.

    Non-CLI requests (no header) and requests when CODEMIE_MIN_CLI_VERSION is unset
    are always allowed through.
    """
    min_version_str = config.CODEMIE_MIN_CLI_VERSION
    if not min_version_str:
        return

    cli_header = request.headers.get(HEADER_CODEMIE_CLI, "").strip()
    if not cli_header:
        return

    # Misconfigured CODEMIE_MIN_CLI_VERSION — InvalidVersion propagates as 500.
    min_version = Version(min_version_str)

    # Header is either "codemie-cli/X.Y.Z" or plain "X.Y.Z".
    version_str = cli_header.rsplit("/", 1)[-1]

    try:
        cli_version = Version(version_str)
    except InvalidVersion:
        logger.warning(
            f"Rejected proxy request: invalid CLI version header '{cli_header}', minimum required is {min_version_str}"
        )
        raise HTTPException(
            status_code=426,
            detail=(
                f"Unsupported CodeMie CLI version '{cli_header}'. "
                f"Please upgrade to CodeMie CLI {min_version_str} or higher. "
                f"Run: npm install -g @codemieai/code"
            ),
        )

    if cli_version < min_version:
        logger.warning(f"Rejected proxy request: CLI version '{cli_version}' is below minimum '{min_version_str}'")
        raise HTTPException(
            status_code=426,
            detail=(
                f"Unsupported CodeMie CLI version '{cli_version}'. "
                f"Please upgrade to CodeMie CLI {min_version_str} or higher. "
                f"Run: npm install -g @codemieai/code"
            ),
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


def _stream_body_bytes(body_bytes: bytes):
    async def passthrough():
        yield body_bytes

    return passthrough()


@dataclass(slots=True)
class BudgetAvailability:
    user_budget_ids: dict[str, str | None] = field(default_factory=dict)
    project_scopes: set[BudgetCategory] = field(default_factory=set)


def _get_cached_project_budget_scopes(project_name: str | None, user_id: str) -> set[BudgetCategory] | None:
    if not project_name:
        return set()

    categories = (BudgetCategory.PLATFORM, BudgetCategory.CLI, BudgetCategory.PREMIUM_MODELS)
    if not all((project_name, category.value, user_id) in _resolution_cache for category in categories):
        return None

    scopes = {
        category for category in categories if _resolution_cache[(project_name, category.value, user_id)] is not None
    }
    logger.debug(
        f"budget_event=project_budget_availability_cache_hit component=proxy_router "
        f"user_id={user_id!r} project_name={project_name!r} project_scopes={sorted(scope.value for scope in scopes)!r}"
    )
    return scopes


async def _probe_project_budget_scopes(project_name: str | None, user_id: str) -> set[BudgetCategory]:
    if not project_name:
        return set()

    cached_scopes = _get_cached_project_budget_scopes(project_name, user_id)
    if cached_scopes is not None:
        return cached_scopes

    categories = [
        BudgetCategory.PLATFORM.value,
        BudgetCategory.CLI.value,
        BudgetCategory.PREMIUM_MODELS.value,
    ]
    async with get_async_session() as session:
        rows = await project_budget_assignment_repository.get_project_budget_categories_batch(
            session=session,
            project_name=project_name,
            user_id=user_id,
            categories=categories,
        )

    scopes: set[BudgetCategory] = set()
    for category in (BudgetCategory.PLATFORM, BudgetCategory.CLI, BudgetCategory.PREMIUM_MODELS):
        cache_key = (project_name, category.value, user_id)
        resolved_or_context = rows.get(category) or rows.get(category.value)
        if resolved_or_context is None:
            _resolution_cache[cache_key] = None
            continue

        scopes.add(category)
        if isinstance(resolved_or_context, ResolvedBudgetContext):
            _resolution_cache[cache_key] = resolved_or_context
            continue

        _resolution_cache[cache_key] = ResolvedBudgetContext(
            scope=BudgetScope.PROJECT,
            project_name=project_name,
            budget_category=CoreBudgetCategory(category.value),
            budget_id=resolved_or_context.budget_id,
            effective_budget_id=resolved_or_context.effective_budget_id,
            shared_budget_id=resolved_or_context.shared_budget_id,
            override_budget_id=resolved_or_context.override_budget_id,
            member_allocation_id=resolved_or_context.allocation_id,
            provider_metadata=resolved_or_context.budget_provider_metadata,
            member_provider_metadata=resolved_or_context.member_provider_metadata,
        )

    logger.debug(
        f"budget_event=project_budget_availability_probed component=proxy_router "
        f"user_id={user_id!r} project_name={project_name!r} project_scopes={sorted(scope.value for scope in scopes)!r}"
    )
    return scopes


async def _resolve_budget_availability(user: User, request_info: dict) -> BudgetAvailability:
    return BudgetAvailability(
        user_budget_ids=await budget_service.get_all_category_budget_ids_for_request(user.id),
        project_scopes=await _probe_project_budget_scopes(request_info.get(PROJECT) or None, user.id),
    )


def _resolve_tracking_identity(
    *,
    user: User,
    request_info: dict,
    availability: BudgetAvailability,
) -> tuple[BudgetCategory, str, str | None, str]:
    llm_model = request_info.get(LLM_MODEL, "unknown")
    username = get_premium_username(user.username, llm_model)
    premium_budget_id = availability.user_budget_ids.get(BudgetCategory.PREMIUM_MODELS.value)
    if username is not None and BudgetCategory.PREMIUM_MODELS in availability.project_scopes:
        logger.debug(
            f"budget_event=runtime_category_selected component=proxy_router user_id={user.id!r} "
            f"username={user.username!r} project_name={request_info.get(PROJECT)!r} "
            f"budget_category={BudgetCategory.PREMIUM_MODELS.value!r} budget_id={premium_budget_id!r} "
            f"model={llm_model!r} reason=project_premium_available"
        )
        return BudgetCategory.PREMIUM_MODELS, username, premium_budget_id, llm_model

    if not availability.project_scopes:
        premium_budget_id = premium_budget_id or get_category_budget_id(BudgetCategory.PREMIUM_MODELS)

    if username is not None and premium_budget_id:
        logger.debug(
            f"budget_event=runtime_category_selected component=proxy_router user_id={user.id!r} "
            f"username={user.username!r} project_name={request_info.get(PROJECT)!r} "
            f"budget_category={BudgetCategory.PREMIUM_MODELS.value!r} budget_id={premium_budget_id!r} "
            f"model={llm_model!r} reason=premium_model"
        )
        return BudgetCategory.PREMIUM_MODELS, username, premium_budget_id, llm_model

    return _resolve_non_premium_tracking_identity(
        user=user,
        request_info=request_info,
        availability=availability,
        llm_model=llm_model,
    )


def _is_cli_request(request_info: dict) -> bool:
    return bool(request_info.get(CODEMIE_CLI)) or request_info.get(CLIENT_TYPE) in {
        "codemie-cli",
        "codemie_cli",
    }


def _resolve_cli_tracking_identity(
    *,
    user: User,
    request_info: dict,
    availability: BudgetAvailability,
    llm_model: str,
) -> tuple[BudgetCategory, str, str | None, str] | None:
    from codemie.enterprise.litellm.budget_categories import build_user_id

    project_has_budget = bool(availability.project_scopes)
    cli_budget_id = availability.user_budget_ids.get(BudgetCategory.CLI.value)
    configured_cli_budget_id = None if project_has_budget else get_category_budget_id(BudgetCategory.CLI)
    cli_available = BudgetCategory.CLI in availability.project_scopes or cli_budget_id or configured_cli_budget_id
    if not cli_available:
        return None

    selected_budget_id = cli_budget_id if project_has_budget else (cli_budget_id or configured_cli_budget_id)
    logger.debug(
        f"budget_event=runtime_category_selected component=proxy_router user_id={user.id!r} "
        f"username={user.username!r} project_name={request_info.get(PROJECT)!r} "
        f"budget_category={BudgetCategory.CLI.value!r} "
        f"budget_id={selected_budget_id!r} "
        f"model={llm_model!r} reason={'project_cli_available' if project_has_budget else 'cli_request'}"
    )
    return (
        BudgetCategory.CLI,
        build_user_id(user.username, BudgetCategory.CLI),
        selected_budget_id,
        llm_model,
    )


def _resolve_non_premium_tracking_identity(
    *,
    user: User,
    request_info: dict,
    llm_model: str,
    availability: BudgetAvailability | None = None,
    category_budget_ids: dict[str, str | None] | None = None,
) -> tuple[BudgetCategory, str, str | None, str]:
    availability = availability or BudgetAvailability(user_budget_ids=category_budget_ids or {})
    cli_tracking_identity = None
    if _is_cli_request(request_info):
        cli_tracking_identity = _resolve_cli_tracking_identity(
            user=user,
            request_info=request_info,
            availability=availability,
            llm_model=llm_model,
        )

    if cli_tracking_identity is not None:
        return cli_tracking_identity

    project_has_budget = bool(availability.project_scopes)
    logger.debug(
        f"budget_event=runtime_category_selected component=proxy_router user_id={user.id!r} "
        f"username={user.username!r} project_name={request_info.get(PROJECT)!r} "
        f"budget_category={BudgetCategory.PLATFORM.value!r} "
        f"budget_id={availability.user_budget_ids.get(BudgetCategory.PLATFORM.value)!r} "
        f"model={llm_model!r} reason={'project_platform_default' if project_has_budget else 'platform_default'}"
    )
    return (
        BudgetCategory.PLATFORM,
        user.username,
        availability.user_budget_ids.get(BudgetCategory.PLATFORM.value),
        llm_model,
    )


async def _create_body_stream_with_optional_injection(
    body_bytes: bytes,
    user: User,
    request_info: dict,
    user_credentials: ResolvedLiteLLMUserCredentials | None = None,
):
    """
    Create body stream with or without user injection.

    When a dedicated proxy budget applies, the injected LiteLLM username is derived as
    ``{user.username}_{budget_name}`` so that spend is attributed to a separate budget
    identity. Premium-model attribution takes precedence; otherwise proxy requests may
    use the dedicated proxy budget. If neither feature applies, the standard
    ``user.username`` is used.

    Args:
        body_bytes: Buffered request body
        user: Authenticated user
        request_info: Request metadata

    Returns:
        AsyncGenerator: Body stream (modified or original)
    """
    if user_credentials is not None:
        logger.debug(
            f"budget_event=runtime_mode_selected component=proxy_router user_id={user.id!r} "
            f"username={user.username!r} project_name={request_info.get(PROJECT)!r} "
            f"mode={RuntimeBudgetMode.USER_CREDENTIALS_BYPASS.value!r} "
            f"reason=user_credentials setting_alias={user_credentials.alias!r}"
        )
        return _stream_body_bytes(body_bytes)

    availability = await _resolve_budget_availability(user, request_info)
    category, username, tracking_budget_id, llm_model = _resolve_tracking_identity(
        user=user,
        request_info=request_info,
        availability=availability,
    )

    project_runtime = await _resolve_project_budget_runtime(
        user=user,
        category=category,
        request_info=request_info,
    )
    project_name = request_info.get(PROJECT) or None
    if project_runtime is not None:
        from codemie.service.settings.settings import SettingsService

        member_tracking_enabled = SettingsService.get_project_member_budget_tracking_enabled(project_name)
        selection = select_runtime_budget_mode(
            has_user_litellm_credentials=False,
            project_name=project_name,
            project_member_tracking_enabled=member_tracking_enabled,
            resolved_project_budget=True,
        )
        project_runtime_username = project_runtime.body_overrides.get("user")
        if selection.mode == RuntimeBudgetMode.PROJECT_BUDGET_WITH_MEMBER_TRACKING:
            if not isinstance(project_runtime_username, str) or not project_runtime_username:
                raise RuntimeError(
                    f"Project member runtime selected but provider returned no runtime user for {project_name!r}"
                )
            username = project_runtime_username
            logger.debug(
                f"budget_event=runtime_mode_selected component=proxy_router user_id={user.id!r} "
                f"username={user.username!r} project_name={project_name!r} "
                f"budget_category={category.value!r} model={llm_model!r} "
                f"mode={selection.mode.value!r} provider_member_ref={project_runtime_username!r} "
                f"litellm_customer_key={project_runtime_username!r}"
            )
            request_info["litellm_customer_id"] = username
            return _inject_user_into_request_body_from_bytes(
                body_bytes=body_bytes,
                user_id=username,
                request_info=request_info,
            )

        logger.debug(
            f"budget_event=runtime_mode_selected component=proxy_router user_id={user.id!r} "
            f"username={user.username!r} project_name={project_name!r} "
            f"budget_category={category.value!r} model={llm_model!r} "
            f"mode={selection.mode.value!r} reason=project_key_only"
        )
        return _stream_body_bytes(body_bytes)

    budget_id = tracking_budget_id if category != BudgetCategory.PLATFORM else None
    await budget_service.track_proxy_budget_assignment_for_request(
        user_id=user.id,
        category=category,
        budget_id=tracking_budget_id,
    )

    if budget_id:
        logger.debug(
            f"budget_event=runtime_mode_selected component=proxy_router user_id={user.id!r} "
            f"username={user.username!r} project_name={project_name!r} "
            f"budget_category={category.value!r} budget_id={budget_id!r} model={llm_model!r} "
            f"mode={RuntimeBudgetMode.GLOBAL_OR_PERSONAL_BUDGET.value!r}"
        )

    logger.debug(
        f"budget_event=runtime_budget_user_injected component=proxy_router user_id={user.id!r} "
        f"username={user.username!r} provider_member_ref={username!r} "
        f"litellm_customer_key={username!r} budget_category={category.value!r} "
        f"budget_id={budget_id!r} model={llm_model!r}"
    )

    check_user_budget(user_email=username, budget_id=budget_id, user_id=user.id)
    request_info["litellm_customer_id"] = username

    return _inject_user_into_request_body_from_bytes(body_bytes=body_bytes, user_id=username, request_info=request_info)


async def _resolve_project_budget_runtime(user: User, category: BudgetCategory, request_info: dict):
    """Resolve project-scoped budget runtime overrides, falling back to global behavior."""
    project_name = request_info.get(PROJECT) or None
    if not project_name:
        logger.debug(
            f"budget_event=budget_resolution_global_fallback component=proxy_router user_id={user.id!r} "
            f"username={user.username!r} project_name={project_name!r} budget_category={category.value!r} "
            f"reason=missing_project_name"
        )
        return None

    core_category = CoreBudgetCategory(category.value)
    logger.debug(
        f"budget_event=runtime_project_budget_resolution_started component=proxy_router user_id={user.id!r} "
        f"username={user.username!r} project_name={project_name!r} budget_category={category.value!r} "
        f"model={request_info.get(LLM_MODEL)!r}"
    )
    await ensure_project_member_runtime_ready(
        user_id=user.id,
        user_email=user.username,
        project_name=project_name,
        budget_category=core_category,
    )

    async with get_async_session() as session:
        resolved = await budget_resolution_service.resolve(
            session,
            user_id=user.id,
            project_name=project_name,
            budget_category=core_category,
        )
    logger.debug(
        f"budget_event=runtime_budget_resolved component=proxy_router user_id={user.id!r} "
        f"username={user.username!r} project_name={project_name!r} "
        f"budget_category={category.value!r} scope={resolved.scope.value!r} "
        f"budget_id={resolved.budget_id!r} effective_budget_id={resolved.effective_budget_id!r} "
        f"shared_budget_id={resolved.shared_budget_id!r} override_budget_id={resolved.override_budget_id!r} "
        f"allocation_id={resolved.member_allocation_id!r} model={request_info.get(LLM_MODEL)!r}"
    )

    provider_result = await budget_resolution_service.dispatch_runtime(
        resolved, user_id=user.id, user_email=user.username, model=request_info.get(LLM_MODEL)
    )
    if provider_result is None:
        logger.debug(
            f"budget_event=runtime_project_budget_resolution_skipped component=proxy_router user_id={user.id!r} "
            f"username={user.username!r} project_name={project_name!r} budget_category={category.value!r} "
            f"reason=no_provider_result"
        )
        return None

    if provider_result.headers:
        request_info["budget_provider_headers"] = provider_result.headers
    if provider_result.api_key:
        request_info["budget_provider_api_key"] = provider_result.api_key
    if provider_result.base_url:
        request_info["budget_provider_base_url"] = provider_result.base_url
    logger.debug(
        f"budget_event=runtime_provider_overrides_applied component=proxy_router user_id={user.id!r} "
        f"username={user.username!r} project_name={project_name!r} budget_category={category.value!r} "
        f"provider={provider_result.provider!r} api_key_present={provider_result.api_key is not None} "
        f"api_key_fingerprint={_anonymized_key_fingerprint(provider_result.api_key)!r} "
        f"base_url_present={provider_result.base_url is not None} headers_applied={bool(provider_result.headers)} "
        f"body_overrides_applied={bool(provider_result.body_overrides)} "
        f"provider_header_names={sorted(provider_result.headers.keys())!r} "
        f"litellm_customer_key={provider_result.body_overrides.get('user')!r}"
    )

    return provider_result


def _prepare_proxy_headers(
    request: Request,
    request_info: dict | None = None,
    user_credentials: ResolvedLiteLLMUserCredentials | None = None,
) -> dict | Response:
    """
    Prepare headers for proxying (uses codemie services).

    Args:
        request: FastAPI request

    Returns:
        dict | Response: Headers or error response
    """
    # Extract and filter hop-by-hop headers
    headers = {k: v for k, v in request.headers.items() if k.lower() not in PROXY_HOP_BY_HOP_HEADERS}

    auth_error = _apply_proxy_auth_header(headers, request_info, user_credentials)
    if auth_error is not None:
        return auth_error
    _apply_budget_provider_headers(headers, request_info)
    _apply_context_headers(headers)

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


def handle_agent_exception(exc: Exception) -> ErrorResponse:
    """Classify an agent exception and return a structured error response.

    Uses a chain-of-responsibility pipeline.
    First classifier that recognizes the exception wins; fallback always returns Internal.

    Args:
        exc: The exception raised during agent execution (LiteLLM call, Agent call, etc.).

    Returns:
        ErrorResponse: Structured response suitable for API responses and client handling.
    """

    pipeline = ExceptionClassificationPipeline.get_pipeline()
    error_response: ErrorResponse = pipeline.handle(exc)
    emit_llm_error_log(error_response, exc)
    return error_response


def emit_llm_error_log(
    error_response: ErrorResponse,
    exc: Exception | None = None,
) -> None:
    """Emit a structured log entry for ELK alerting via ``send_log_metric``."""
    error_code: str = error_response.get_error().error_code.value
    error_message = str(error_response.get_error().details)
    try:
        attributes: dict[str, object] = {
            MetricsAttributes.LLM_ERROR_CODE: error_code,
            MetricsAttributes.ERROR: limit_string(error_message),
            MetricsAttributes.USER_ID: logging_user_id.get("-"),
            MetricsAttributes.USER_EMAIL: current_user_email.get("-"),
            MetricsAttributes.CONVERSATION_ID: logging_conversation_id.get("-"),
            MetricsAttributes.REQUEST_UUID: logging_uuid.get("-"),
            MetricsAttributes.REQUEST_ID: logging_uuid.get("-"),
            MetricsAttributes.SESSION_ID: logging_conversation_id.get("-"),
        }
        if exc is not None:
            llm_model = getattr(exc, "model", None)
            llm_provider = getattr(exc, "llm_provider", None)
            status_code = getattr(exc, "status_code", None)
            if llm_model:
                attributes[MetricsAttributes.LLM_MODEL] = llm_model
            if llm_provider:
                attributes["llm_provider"] = llm_provider
            if status_code is not None:
                attributes["status_code"] = status_code
        send_log_metric(LLM_ERROR_TOTAL_METRIC, attributes)
    except Exception as log_exc:
        logger.warning(f"Failed to emit LLM error log metric: {log_exc}")


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
      3. The premium models budget feature is enabled (a ``premium_models`` budget
         is configured in ``predefined budgets``).

    Returns ``None`` when any condition is not met (caller should pass the original bytes
    through unchanged).
    """
    budget_name = get_category_budget_id(BudgetCategory.PREMIUM_MODELS) or ""
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
                headers=_sanitize_local_response_headers(response_headers),
                media_type="application/json",
            )

    return Response(
        content=body_bytes,
        status_code=downstream_response.status_code,
        headers=_sanitize_local_response_headers(response_headers),
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

    # Check CLI version
    _check_cli_version(request)

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

    user_credentials = resolve_litellm_user_credentials(
        user_id=user.id,
        username=user.username,
        project_name=request_info.get(PROJECT),
    )

    # Create body stream (with or without user injection)
    body_stream = await _create_body_stream_with_optional_injection(
        body_bytes=body_bytes,
        user=user,
        request_info=request_info,
        user_credentials=user_credentials,
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
    headers = _prepare_proxy_headers(request, request_info, user_credentials=user_credentials)
    if isinstance(headers, Response):
        return headers

    # Proxy request
    provider_base_url = request_info.get("budget_provider_base_url")
    if provider_base_url:
        url = httpx.URL(f"{str(provider_base_url).rstrip('/')}/{endpoint.lstrip('/')}")
    else:
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
