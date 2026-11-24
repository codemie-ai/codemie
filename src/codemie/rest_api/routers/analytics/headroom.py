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

from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.analytics import SummariesResponse, TabularResponse
from codemie.rest_api.routers.analytics._common import (
    AnalyticsFilterParams,
    AnalyticsQueryParams,
    _create_response,
    handle_analytics_errors,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.analytics_service import AnalyticsService

router = APIRouter()

HEADROOM_FEATURE = "headroom"


def _ensure_headroom_analytics_enabled() -> None:
    """Headroom analytics follow the Headroom feature flag, not routingAnalytics."""
    if not customer_config.is_feature_enabled(HEADROOM_FEATURE):
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Headroom feature is not available",
            details="The headroom feature flag is disabled on this instance.",
            help="Contact your administrator to enable the Headroom feature.",
        )


@router.get(
    "/headroom/summary",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get Headroom savings summary",
    description=(
        "Retrieve Headroom summary: compacted requests, sessions, original, compacted and saved tokens, "
        "savings rate and the active mode (sdk, sidecar or mixed)."
    ),
)
@handle_analytics_errors("headroom summary analytics")
async def get_headroom_summary(
    user: User = Depends(authenticate),  # noqa: B008 - FastAPI dependency injection
    params: AnalyticsFilterParams = Depends(),  # noqa: B008 - FastAPI dependency injection
) -> JSONResponse:
    """Retrieve Headroom summary: requests, sessions, tokens, savings rate and active mode."""
    _ensure_headroom_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_headroom_summary(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
    )
    return _create_response(data, SummariesResponse)


@router.get(
    "/headroom/by-strategy",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get Headroom savings by strategy",
    description="Retrieve Headroom token savings grouped by compression strategy.",
)
@handle_analytics_errors("headroom by-strategy analytics")
async def get_headroom_by_strategy(
    user: User = Depends(authenticate),  # noqa: B008 - FastAPI dependency injection
    params: AnalyticsQueryParams = Depends(),  # noqa: B008 - FastAPI dependency injection
) -> JSONResponse:
    """Retrieve Headroom token savings grouped by compression strategy."""
    _ensure_headroom_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_headroom_by_strategy(
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
    "/headroom/timeseries",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get Headroom savings over time",
    description="Retrieve Headroom token savings over time.",
)
@handle_analytics_errors("headroom timeseries analytics")
async def get_headroom_timeseries(
    user: User = Depends(authenticate),  # noqa: B008 - FastAPI dependency injection
    params: AnalyticsFilterParams = Depends(),  # noqa: B008 - FastAPI dependency injection
) -> JSONResponse:
    """Retrieve Headroom token savings over time."""
    _ensure_headroom_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_headroom_timeseries(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
    )
    return _create_response(data, TabularResponse)
