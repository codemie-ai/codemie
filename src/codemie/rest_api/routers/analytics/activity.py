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

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from codemie.rest_api.models.analytics import (
    TabularResponse,
    UsersListResponse,
)
from codemie.rest_api.routers.analytics._common import (
    CLIENT_SOURCE_FILTER_DESC,
    PROJECTS_FILTER_DESC,
    USERS_FILTER_DESC,
    AnalyticsQueryParams,
    _create_response,
    handle_analytics_errors,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.utils.client_context import ClientSource
from codemie.rest_api.security.user import User
from codemie.service.analytics.analytics_service import AnalyticsService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/projects-spending",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("projects spending analytics")
async def get_projects_spending(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get projects spending analytics."""
    service = AnalyticsService(user)
    data = await service.get_projects_spending(
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
    "/llms-usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("LLMs usage analytics")
async def get_llms_usage(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get LLMs usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_llms_usage(
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
    "/embeddings-usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("embeddings usage analytics")
async def get_embeddings_usage(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get embedding model usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_embeddings_usage(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/users-spending",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("users spending analytics")
async def get_users_spending(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get users spending analytics."""
    service = AnalyticsService(user)
    data = await service.get_users_spending(
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
    "/budget-soft-limit",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("budget soft limit analytics")
async def get_budget_soft_limit(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get budget soft limit analytics."""
    service = AnalyticsService(user)
    data = await service.get_budget_soft_limit(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/budget-hard-limit",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("budget hard limit analytics")
async def get_budget_hard_limit(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get budget hard limit analytics."""
    service = AnalyticsService(user)
    data = await service.get_budget_hard_limit(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/users-activity",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("users activity analytics")
async def get_users_activity(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get users activity analytics."""
    service = AnalyticsService(user)
    data = await service.get_users_activity(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/users-unique-daily",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("unique daily users analytics")
async def get_users_unique_daily(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None, description="Predefined time range (e.g., 'last_30_days')"),
    start_date: datetime | None = Query(None, description="Custom range start date"),
    end_date: datetime | None = Query(None, description="Custom range end date"),
    users: str | None = Query(None, description="Comma-separated user IDs to filter by"),
    projects: str | None = Query(None, description="Comma-separated project names to filter by"),
) -> JSONResponse:
    """Get unique users per day analytics.

    Returns time-series data showing the number of unique active users for each day,
    based on conversation_assistant_usage metric. The endpoint aggregates user activity
    by calendar day (UTC timezone) and counts distinct users for each day.

    **IMPORTANT: All timestamps are in UTC timezone.** Input dates (start_date/end_date) must be
    UTC-aware or will be treated as UTC. Output dates are formatted in UTC timezone (YYYY-MM-DD).

    **Note:** This endpoint returns all date records without pagination (up to 10,000 days).

    Access Control:
    - Admin users can see data for all projects in their applications and applications_admin lists
    - Non-admin users can only see data for projects in their applications list

    Query Parameters:
    - time_period: Use predefined ranges like 'last_7_days', 'last_30_days', or 'last_90_days'
    - start_date/end_date: Specify custom date range in UTC (overrides time_period if provided)
    - users: Filter results to specific users (comma-separated user IDs)
    - projects: Filter results to specific projects (comma-separated project names)

    Response Format:
    - Returns TabularResponse with columns: date (YYYY-MM-DD in UTC), unique_users (count)
    - Includes metadata: timestamp, data_as_of, filters_applied, execution_time_ms
    - All date records are returned in a single response
    """
    service = AnalyticsService(user)
    data = await service.get_users_unique_daily(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/users",
    status_code=status.HTTP_200_OK,
    response_model=UsersListResponse,
    response_model_by_alias=True,
    summary="Get unique users list",
    description="Retrieve list of unique users with activity in the specified time range",
)
@handle_analytics_errors("users list")
async def get_users_list(
    user: User = Depends(authenticate),
    time_period: str | None = Query(
        None,
        description=(
            "Predefined time range (last_hour, last_6_hours, last_24_hours, "
            "last_7_days, last_30_days, last_60_days, last_year)"
        ),
        examples=["last_30_days"],
    ),
    start_date: datetime | None = Query(
        None,
        description="Custom range start (ISO 8601 format). Use with end_date instead of time_period.",
        examples=["2025-11-01T00:00:00Z"],
    ),
    end_date: datetime | None = Query(
        None,
        description="Custom range end (ISO 8601 format). Use with start_date instead of time_period.",
        examples=["2025-12-01T00:00:00Z"],
    ),
    users: str | None = Query(
        None,
        description=USERS_FILTER_DESC,
    ),
    projects: str | None = Query(None, description=PROJECTS_FILTER_DESC, examples=["codemie,project-alpha"]),
    search: str | None = Query(
        None,
        description="Text search for super-admins: filters users by email, username, or display name (ILIKE).",
    ),
) -> JSONResponse:
    """Get list of unique users from metrics logs.

    Returns unique users with activity in the specified time range,
    respecting access control (admin sees all users from admin projects,
    plain user sees only themselves).

    **Access Control:**
    - Plain users: See only themselves
    - Project admins: See all users from projects they administer

    **Time Filtering:**
    - Use `time_period` for predefined ranges (e.g., "last_30_days")
    - OR use `start_date` + `end_date` for custom ranges
    - If neither provided, defaults to last 30 days

    **Additional Filters:**
    - `users`: Filter by specific user IDs (within access control)
    - `projects`: Filter by specific projects (within access control)
    - `search`: Super-admin only — type-ahead filter on email/username/name

    **Response:**
    - Returns list of users with id and name
    - Includes total_count
    - Includes metadata: timestamp, data_as_of, filters_applied, execution_time_ms

    **Caching:**
    - Response includes Cache-Control and ETag headers (5-minute TTL)
    """
    logger.info(
        f"User {user.id} requesting users list. "
        f"Period={time_period}, StartDate={start_date}, EndDate={end_date}, "
        f"Users={users}, Projects={projects}, Search={search!r}"
    )

    service = AnalyticsService(user)
    response_data = await service.get_users_list(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        search=search,
    )

    return _create_response(response_data, UsersListResponse)


@router.get(
    "/projects-activity",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("projects activity analytics")
async def get_projects_activity(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get projects activity analytics."""
    service = AnalyticsService(user)
    data = await service.get_projects_activity(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/projects-unique-daily",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("unique daily projects analytics")
async def get_projects_unique_daily(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None, description="Predefined time range (e.g., 'last_30_days')"),
    start_date: datetime | None = Query(None, description="Custom range start date"),
    end_date: datetime | None = Query(None, description="Custom range end date"),
    users: str | None = Query(None, description="Comma-separated user IDs to filter by"),
    projects: str | None = Query(None, description="Comma-separated project names to filter by"),
) -> JSONResponse:
    """Get unique projects per day analytics.

    Returns time-series data showing the number of unique active projects for each day,
    based on conversation_assistant_usage metric. The endpoint aggregates project activity
    by calendar day (UTC timezone) and counts distinct projects for each day.

    **IMPORTANT: All timestamps are in UTC timezone.** Input dates (start_date/end_date) must be
    UTC-aware or will be treated as UTC. Output dates are formatted in UTC timezone (YYYY-MM-DD).

    **Note:** This endpoint returns all date records without pagination (up to 10,000 days).

    Access Control:
    - Admin users can see data for all projects in their applications and applications_admin lists
    - Non-admin users can only see data for projects in their applications list

    Query Parameters:
    - time_period: Use predefined ranges like 'last_7_days', 'last_30_days', or 'last_90_days'
    - start_date/end_date: Specify custom date range in UTC (overrides time_period if provided)
    - users: Filter results to specific users (comma-separated user IDs)
    - projects: Filter results to specific projects (comma-separated project names)

    Response Format:
    - Returns TabularResponse with columns: date (YYYY-MM-DD in UTC), unique_projects (count)
    - Includes metadata: timestamp, data_as_of, filters_applied, execution_time_ms
    - All date records are returned in a single response
    """
    # Validate time parameters
    if start_date and end_date and start_date > end_date:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="start_date must be before or equal to end_date"
        )

    service = AnalyticsService(user)
    data = await service.get_projects_unique_daily(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)
