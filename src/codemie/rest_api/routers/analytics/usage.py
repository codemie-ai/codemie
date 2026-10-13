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
    SummariesResponse,
    TabularResponse,
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
    "/summaries",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get summary metrics",
    description="Retrieve total input/output tokens, cached tokens, and money spent across all usage types",
)
@handle_analytics_errors("analytics summaries")
async def get_summaries(
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
    users: str | None = Query(None, description=USERS_FILTER_DESC),
    projects: str | None = Query(None, description=PROJECTS_FILTER_DESC, examples=["codemie,project-alpha"]),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get summary metrics: total input/output tokens, cached tokens, money spent.

    This endpoint provides aggregated metrics across all usage types (conversations, datasources, workflows).
    Users can only see data for projects they have access to.

    **Access Control:**
    - Plain users: See data for projects in their `applications` list
    - Project admins: See data for projects in `applications` + `applications_admin` lists

    **Time Filtering:**
    - Use `time_period` for predefined ranges (e.g., "last_30_days")
    - OR use `start_date` + `end_date` for custom ranges
    - If neither provided, defaults to last 30 days

    **Additional Filters:**
    - `users`: Filter by specific user IDs (must be within accessible projects)
    - `projects`: Filter by specific projects (must be within user's accessible projects)

    **Response:**
    - Returns 11 metrics including 4 spending metrics:
      - platform_cost: LLM costs from web platform (conversations + workflows, excluding CLI)
      - cli_cost: CLI-specific spending
      - embedding_cost: Embedding model usage costs
      - total_money_spent: Total spending across all categories
    - Also includes token counts and usage statistics
    - Includes metadata: timestamp, data_as_of, filters_applied, execution_time_ms

    **Caching:**
    - Response includes Cache-Control and ETag headers (5-minute TTL)
    """
    logger.info(
        f"User {user.id} requesting summaries. "
        f"Period={time_period}, StartDate={start_date}, EndDate={end_date}, "
        f"Users={users}, Projects={projects}"
    )

    service = AnalyticsService(user)
    response_data = await service.get_summaries(
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
        client_source=client_source.value if client_source else None,
    )

    return _create_response(response_data, SummariesResponse)


# Additional endpoints for comprehensive analytics


@router.get(
    "/assistants-chats",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("assistants chats analytics")
async def get_assistants_chats(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get assistants chats analytics."""
    service = AnalyticsService(user)
    data = await service.get_assistants_chats(
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
    "/workflows",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("workflows analytics")
async def get_workflows(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get workflows analytics."""
    service = AnalyticsService(user)
    data = await service.get_workflows(
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
    "/tools-usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("tools usage analytics")
async def get_tools_usage(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get tools usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_tools_usage(
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
    "/agents-usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("agents usage analytics")
async def get_agents_usage(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get agents usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_agents_usage(
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
    "/power-users",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("power users analytics")
async def get_power_users(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get power users analytics."""
    service = AnalyticsService(user)
    data = await service.get_power_users(
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
    "/knowledge-sharing",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("knowledge sharing analytics")
async def get_knowledge_sharing(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get knowledge sharing analytics."""
    service = AnalyticsService(user)
    data = await service.get_knowledge_sharing(
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
    "/top-agents-usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("top agents usage analytics")
async def get_top_agents_usage(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get top agents usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_top_agents_usage(
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
    "/top-workflow-usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("top workflow usage analytics")
async def get_top_workflow_usage(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get top workflow usage analytics."""
    service = AnalyticsService(user)
    data = await service.get_top_workflow_usage(
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
    "/published-to-marketplace",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("published to marketplace analytics")
async def get_published_to_marketplace(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get published to marketplace analytics."""
    service = AnalyticsService(user)
    data = await service.get_published_to_marketplace(
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
    "/webhooks-invocation",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("webhooks invocation analytics")
async def get_webhooks_invocation(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get webhooks invocation analytics."""
    service = AnalyticsService(user)
    data = await service.get_webhooks_invocation(
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
    "/mcp-servers",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("MCP servers analytics")
async def get_mcp_servers(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get MCP servers analytics."""
    service = AnalyticsService(user)
    data = await service.get_mcp_servers(
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
    "/mcp-servers-by-users",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("MCP servers by users analytics")
async def get_mcp_servers_by_users(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get MCP servers by users analytics."""
    service = AnalyticsService(user)
    data = await service.get_mcp_servers_by_users(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
    )
    return _create_response(data, TabularResponse)
