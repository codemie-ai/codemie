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

from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from codemie.rest_api.models.analytics import (
    SummariesResponse,
    TabularResponse,
)
from codemie.rest_api.routers.analytics._common import (
    CLIENT_SOURCE_FILTER_DESC,
    AnalyticsQueryParams,
    _create_response,
    handle_analytics_errors,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.utils.client_context import ClientSource
from codemie.rest_api.security.user import User
from codemie.service.analytics.analytics_service import AnalyticsService

router = APIRouter()


@router.get(
    "/cli-summary",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI summary analytics")
async def get_cli_summary(
    user: User = Depends(authenticate),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI summary analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_summary(
        time_period,
        start_date,
        end_date,
        [u.strip() for u in users.split(",")] if users else None,
        [p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, SummariesResponse)


@router.get(
    "/cli-agents",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI agents analytics")
async def get_cli_agents(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI agents analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_agents(
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
    "/cli-llms",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI LLMs analytics")
async def get_cli_llms(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
    client_source: ClientSource | None = Query(None, description=CLIENT_SOURCE_FILTER_DESC),
) -> JSONResponse:
    """Get CLI LLMs analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_llms(
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
    "/cli-users",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI users analytics")
async def get_cli_users(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI users analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_users(
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
    "/cli-errors",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI errors analytics")
async def get_cli_errors(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI errors analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_errors(
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
    "/cli-repositories",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI repositories analytics")
async def get_cli_repositories(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI repositories analytics."""
    service = AnalyticsService(user)
    data = await service.get_cli_repositories(
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
    "/cli-top-performers",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI top performers analytics")
async def get_cli_top_performers(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI top performers ranked by total lines added."""
    service = AnalyticsService(user)
    data = await service.get_cli_top_performers(
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
    "/cli-top-versions",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI top versions analytics")
async def get_cli_top_versions(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI top versions ranked by usage count."""
    service = AnalyticsService(user)
    data = await service.get_cli_top_versions(
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
    "/cli-top-proxy-endpoints",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI top proxy endpoints analytics")
async def get_cli_top_proxy_endpoints(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI top proxy endpoints ranked by request count."""
    service = AnalyticsService(user)
    data = await service.get_cli_top_proxy_endpoints(
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
    "/cli-tools",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI tools usage analytics")
async def get_cli_tools_usage(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI tool usage analytics showing which tools are used most frequently."""
    service = AnalyticsService(user)
    data = await service.get_cli_tools_usage(
        params.time_period,
        params.start_date,
        params.end_date,
        params.users_list,
        params.projects_list,
        params.page,
        params.per_page,
    )
    return _create_response(data, TabularResponse)
