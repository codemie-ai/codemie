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

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from codemie.rest_api.models.analytics import (
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
    "/cli-insights-project-classification",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights project classification analytics")
async def get_cli_insights_project_classification(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI Insights project classification widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_project_classification(
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
    "/cli-insights-top-projects-by-cost",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
)
@handle_analytics_errors("CLI insights top projects by cost analytics")
async def get_cli_insights_top_projects_by_cost(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get CLI Insights top projects by cost widget data."""
    service = AnalyticsService(user)
    data = await service.get_cli_insights_top_projects_by_cost(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
        page=params.page,
        per_page=params.per_page,
    )
    return _create_response(data, TabularResponse)
