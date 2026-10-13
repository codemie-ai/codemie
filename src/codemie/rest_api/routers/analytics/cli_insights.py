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
    AnalyticsDetailResponse,
    SummariesResponse,
    TabularResponse,
)
from codemie.rest_api.routers.analytics._common import (
    AnalyticsQueryParams,
    _create_response,
    handle_analytics_errors,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.analytics_service import AnalyticsService

router = APIRouter()


@router.get(
    "/cli-insights-weekday-pattern",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights weekday pattern analytics")
async def get_cli_insights_weekday_pattern(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI Insights weekday pattern widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_weekday_pattern(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
        page=params.page,
        per_page=params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-hourly-usage",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights hourly usage analytics")
async def get_cli_insights_hourly_usage(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI Insights hourly usage widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_hourly_usage(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
        page=params.page,
        per_page=params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-session-depth",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights session depth analytics")
async def get_cli_insights_session_depth(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI Insights session depth widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_session_depth(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
        page=params.page,
        per_page=params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-user-classification",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user classification analytics")
async def get_cli_insights_user_classification(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI Insights user classification widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_user_classification(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
        page=params.page,
        per_page=params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-top-users-by-cost",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights top users by cost analytics")
async def get_cli_insights_top_users_by_cost(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI Insights top users by cost widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_top_users_by_cost(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
        page=params.page,
        per_page=params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-top-spenders",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights top spenders analytics")
async def get_cli_insights_top_spenders(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI Insights Top Spenders table data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_top_spenders(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
        page=params.page,
        per_page=params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-users",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights all users analytics")
async def get_cli_insights_all_users(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI Insights all users table data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_all_users(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
        page=params.page,
        per_page=params.per_page,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-user-detail",
    status_code=status.HTTP_200_OK,
    response_model=AnalyticsDetailResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user detail analytics")
async def get_cli_insights_user_detail(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user detail drilldown data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_detail(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, AnalyticsDetailResponse)


@router.get(
    "/cli-insights-user-key-metrics",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user key metrics analytics")
async def get_cli_insights_user_key_metrics(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user key metrics widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_key_metrics(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, SummariesResponse)


@router.get(
    "/cli-insights-user-tools",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user tools analytics")
async def get_cli_insights_user_tools(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user tools widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_tools(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-user-models",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user models analytics")
async def get_cli_insights_user_models(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user models widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_models(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-user-workflow-intent",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user workflow intent analytics")
async def get_cli_insights_user_workflow_intent(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user workflow intent widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_workflow_intent(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, SummariesResponse)


@router.get(
    "/cli-insights-user-classification-detail",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user classification detail analytics")
async def get_cli_insights_user_classification_detail(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user classification widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_classification_detail(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, SummariesResponse)


@router.get(
    "/cli-insights-user-category-breakdown",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user category breakdown analytics")
async def get_cli_insights_user_category_breakdown(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user category breakdown widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_category_breakdown(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-user-repositories",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights user repositories analytics")
async def get_cli_insights_user_repositories(
    user: User = Depends(authenticate),
    user_name: str = Query(...),
    user_id: str | None = Query(None),
    time_period: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    users: str | None = Query(None),
    projects: str | None = Query(None),
) -> JSONResponse:
    """Get CLI Insights user repositories widget data."""
    service = AnalyticsService(user)
    normalized_user_id = user_id if isinstance(user_id, str) else None
    data = await service.get_cli_insights_user_repositories(
        user_name=user_name,
        user_id=normalized_user_id,
        time_period=time_period,
        start_date=start_date,
        end_date=end_date,
        users=[u.strip() for u in users.split(",")] if users else None,
        projects=[p.strip() for p in projects.split(",")] if projects else None,
    )
    return _create_response(data, TabularResponse)
