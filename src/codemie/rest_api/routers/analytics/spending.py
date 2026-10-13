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

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from codemie.configs.config import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.analytics import (
    SummariesResponse,
    TabularResponse,
)
from codemie.rest_api.routers.analytics._common import (
    CLIENT_SOURCE_FILTER_DESC,
    ERROR_MSG_ACCESS_DENIED,
    ERROR_MSG_ADMIN_HELP,
    PROJECTS_FILTER_DESC,
    USERS_FILTER_DESC,
    AnalyticsQueryParams,
    _create_response,
    handle_analytics_errors,
)
from codemie.rest_api.routers.analytics._spending_support import _authorize_admin_budget_view
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.utils.client_context import ClientSource
from codemie.rest_api.security.user import User
from codemie.clients.postgres import get_async_session
from codemie.service.analytics.analytics_service import AnalyticsService
from codemie.service.analytics.handlers.member_spend_service import member_spend_service
from codemie.service.analytics.spending_formatter import (
    build_spending_metric,
    build_spending_metrics,
    paginate_tabular,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/spending",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get user spending and budget information",
    description="Retrieve current spending, budget limits, and reset period for the authenticated user",
)
@handle_analytics_errors("user spending analytics")
async def get_user_spending(
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get spending analytics for the authenticated user.

    This endpoint retrieves spending information from LiteLLM for budget tracking.
    Returns current spending, budget limits, and time until reset.

    **Access Control:**
    - Users can only see their own spending data

    **Response:**
    - current_spending: Current amount spent in USD
    - budget_limit: Soft budget limit (warning threshold)
    - hard_budget_limit: Hard budget limit (blocking threshold)
    - budget_reset_at: ISO 8601 timestamp of next reset
    - time_until_reset: Formatted time until budget resets (e.g., "5 days 4 hours 31mins")

    **Error Handling:**
    - Gracefully handles API failures with clear messages
    """
    from datetime import timezone
    from codemie.enterprise.litellm.dependencies import (
        get_customer_spending,
        get_proxy_customer_spending,
        get_premium_customer_spending,
        is_premium_models_enabled,
    )

    start_time = datetime.now(timezone.utc)
    logger.info(f"User {user.id} requesting spending analytics")

    # Get standard spending data from LiteLLM
    try:
        spending_data = await asyncio.to_thread(get_customer_spending, user.username, True)
    except Exception as e:
        logger.error(f"Backend error fetching spending for user {user.id}: {e}")
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Unable to retrieve spending information at this time.",
            help="A temporary issue occurred. Please try again later",
        ) from e

    metrics = build_spending_metrics(spending_data, user.username)

    try:
        proxy_spending_data = await asyncio.to_thread(get_proxy_customer_spending, user.username)
        if proxy_spending_data is not None:
            proxy_current_spending = proxy_spending_data.get("total_spend", 0.0)
            logger.debug(f"Proxy spending retrieved for user {user.id}: spend=${proxy_current_spending:.2f}")
            metrics.append(build_spending_metric("cli_current_spending", proxy_current_spending, "number", "currency"))
    except Exception as e:
        logger.warning(f"Failed to fetch proxy spending for user {user.id}: {e}")

    # Include premium budget spending when feature is enabled
    if is_premium_models_enabled():
        try:
            premium_spending_data = await asyncio.to_thread(get_premium_customer_spending, user.username)
            if premium_spending_data is not None:
                premium_current_spending = premium_spending_data.get("total_spend", 0.0)
                logger.debug(f"Premium spending retrieved for user {user.id}: spend=${premium_current_spending:.2f}")
                metrics.append(
                    build_spending_metric(
                        "premium_current_spending",
                        premium_current_spending,
                        "number",
                        "currency",
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to fetch premium spending for user {user.id}: {e}")

    response_data = {
        "data": {"metrics": metrics},
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data_as_of": datetime.now(timezone.utc).isoformat(),
            "filters_applied": {},
            "execution_time_ms": (datetime.now(timezone.utc) - start_time).total_seconds() * 1000,
        },
    }

    return _create_response(response_data, SummariesResponse)


async def _resolve_admin_budget_subject(user: "User", user_id: str) -> tuple[str, str | None]:
    """Look up target user for admin budget view; raise if not found or not authorized."""
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="user_id parameter requires ENABLE_USER_MANAGEMENT to be enabled.",
            help="Remove the user_id parameter or enable user management.",
        )
    from codemie.clients.postgres import get_session
    from codemie.repository.user_project_repository import user_project_repository
    from codemie.service.user.user_management_service import UserManagementService

    def _lookup():
        with get_session() as session:
            db_user = UserManagementService.get_user_by_id(session, user_id)
            project_names = user_project_repository.get_project_names_for_user(session, user_id) if db_user else []
        return db_user, project_names

    target_user_db, target_project_names = await asyncio.to_thread(_lookup)
    if target_user_db is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=f"User {user_id} not found.",
            help="Verify the user_id and try again.",
        )
    _authorize_admin_budget_view(user, target_project_names)
    subject_label = target_user_db.username
    if not subject_label:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=f"User {user_id} has no username; budget lookup cannot be performed.",
            help="Ensure the target user has a valid username configured.",
        )
    logger.info(f"Admin budget view: caller={user.id} viewing target={user_id}")
    return str(target_user_db.id), subject_label


@router.get(
    "/budget_usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get user budget usage",
    description=(
        "Retrieve budget usage for a user: personal budget categories (platform, CLI, premium) "
        "and per-project member allocations. "
        "When user_id is omitted, returns data for the authenticated caller. "
        "When user_id is provided, requires the caller to be a global admin, maintainer, "
        "or project admin sharing at least one project with the target user."
    ),
)
@handle_analytics_errors("budget usage analytics")
async def get_user_budget_usage(
    user: User = Depends(authenticate),
    user_id: str | None = Query(
        default=None,
        min_length=1,
        description=(
            "Optional target user ID. When provided, returns budget usage for that user. "
            "Requires the caller to be a global admin, maintainer, or project admin "
            "for at least one project the target user belongs to. "
            "Only valid when ENABLE_USER_MANAGEMENT=True."
        ),
    ),
) -> TabularResponse:
    from datetime import timezone

    from codemie.clients.postgres import get_async_session
    from codemie.service.analytics.handlers.budget_usage_service import budget_usage_service
    from codemie.service.analytics.response_formatter import ResponseFormatter

    start_time = datetime.now(timezone.utc)

    # --- Resolve subject (target user or caller) ---
    if user_id is not None:
        subject_user_id, subject_label = await _resolve_admin_budget_subject(user, user_id)
    else:
        subject_user_id = user.id
        subject_label = user.username
        if not subject_label:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Your account has no username; budget lookup cannot be performed.",
                help="Ensure your account has a valid username configured.",
            )
        logger.info(f"User {user.id} requesting budget usage analytics")

    try:
        async with get_async_session() as session:
            columns, rows = await budget_usage_service.get_budget_usage(session, subject_user_id, subject_label)
    except ExtendedHTTPException:
        raise
    except Exception as e:
        logger.error(f"DB error fetching budget data for subject={subject_user_id} requested_by={user.id}: {e}")
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Unable to retrieve spending information at this time.",
            help="A temporary issue occurred. Please try again later.",
        ) from e

    execution_time_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

    return ResponseFormatter.format_tabular_response(
        columns=columns,
        rows=rows,
        filters_applied={},
        execution_time_ms=execution_time_ms,
        totals=None,
    )


@router.get(
    "/user-project-spending",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get a user's spend broken down by project",
    description=(
        "Returns one row per project the user belongs to, with spend and configured limit "
        "per budget category for the current budget cycle. Requires the caller to be a global "
        "admin, maintainer, or project admin for at least one project the target user belongs to."
    ),
)
@handle_analytics_errors("user project spending analytics")
async def get_user_project_spending(
    user: User = Depends(authenticate),
    users: str = Query(..., min_length=1, description="Target user email. Exactly one."),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> TabularResponse:
    start_time = datetime.now(timezone.utc)
    target_email = users.split(",")[0].strip()

    target_user, target_projects = await asyncio.to_thread(member_spend_service.resolve_spend_subject, target_email)
    if target_user is None:
        if not user.is_admin:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=ERROR_MSG_ACCESS_DENIED,
                help=ERROR_MSG_ADMIN_HELP,
            )
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=f"User {target_email} not found.",
            help="Verify the email and try again.",
        )
    _authorize_admin_budget_view(user, target_projects)

    async with get_async_session() as session:
        columns, rows = await member_spend_service.get_user_project_spend(session, str(target_user.id))

    return paginate_tabular(
        columns=columns,
        rows=rows,
        filters_applied={"users": target_email},
        start_time=start_time,
        page=page,
        per_page=per_page,
    )


@router.get(
    "/project-member-spending",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get each project member's spend in that project",
    description=(
        "Returns one row per project member, keyed by user_id, with spend and configured limit "
        "per budget category for the current budget cycle. Requires the caller to be a global "
        "admin, maintainer, or admin of the requested project."
    ),
)
@handle_analytics_errors("project member spending analytics")
async def get_project_member_spending(
    user: User = Depends(authenticate),
    projects: str = Query(..., min_length=1, description="Target project name. Exactly one."),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> TabularResponse:
    start_time = datetime.now(timezone.utc)
    project_name = projects.split(",")[0].strip()

    _authorize_admin_budget_view(user, {project_name})

    async with get_async_session() as session:
        columns, rows = await member_spend_service.get_project_member_spend(session, project_name)

    return paginate_tabular(
        columns=columns,
        rows=rows,
        filters_applied={"projects": project_name},
        start_time=start_time,
        page=page,
        per_page=per_page,
    )


# ---------------------------------------------------------------------------
# Engagement widget: weekly histogram
# This endpoint intentionally omits time_period/start_date/end_date params.
# The underlying query always operates on the last 7 days.
# ---------------------------------------------------------------------------


@router.get(
    "/engagement/weekly-histogram",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get weekly spending histogram",
    description=(
        "Returns a time series with one row per 3-hour interval showing money spent "
        "broken down by source: Assistants, Workflows, Datasources, CLI. "
        "Always covers the last 7 days — the dashboard time filter is intentionally ignored."
    ),
)
@handle_analytics_errors("engagement weekly histogram")
async def get_engagement_weekly_histogram(
    user: User = Depends(authenticate),
    users: str | None = Query(None, description=USERS_FILTER_DESC),
    projects: str | None = Query(None, description=PROJECTS_FILTER_DESC, examples=["codemie"]),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get weekly spending histogram — 3h intervals, always last 7 days."""
    logger.info(f"User {user.id} requesting weekly spending histogram")
    service = AnalyticsService(user)
    response_data = await service.get_weekly_spending(
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        client_source=client_source.value if client_source else None,
    )
    return _create_response(response_data, TabularResponse)


# ---------------------------------------------------------------------------
# Spending breakdown by user (platform vs CLI)
# ---------------------------------------------------------------------------


@router.get(
    "/spending/by-users/platform",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get platform spending per user",
    description="Returns per-user spending for platform metrics (Assistants, Workflows, Datasources).",
)
@handle_analytics_errors("platform spending by users")
async def get_spending_by_users_platform(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get platform spending per user (Assistants + Workflows + Datasources)."""
    service = AnalyticsService(user)
    data = await service.get_users_platform_spending(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
        client_source=client_source.value if client_source else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/spending/by-users/cli",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get CLI spending per user",
    description="Returns per-user spending for CLI proxy usage only (cli_request=true), grouped by user_name.",
)
@handle_analytics_errors("CLI spending by users")
async def get_spending_by_users_cli(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get CLI-only spending per user grouped by user_name."""
    service = AnalyticsService(user)
    data = await service.get_users_cli_spending(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
        client_source=client_source.value if client_source else None,
    )
    return _create_response(data, TabularResponse)
