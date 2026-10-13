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

from fastapi import APIRouter, Depends, Path, status
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


ROUTING_ANALYTICS_FEATURE = "routingAnalytics"


def _ensure_routing_analytics_enabled() -> None:
    if not customer_config.is_feature_enabled(ROUTING_ANALYTICS_FEATURE):
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Routing Analytics feature is not available",
            details="The routingAnalytics feature flag is disabled on this instance.",
            help="Contact your administrator to enable the Routing Analytics feature.",
        )


@router.get(
    "/routing/summary",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get routing analytics summary",
    description="Retrieve routing analytics summary: session count, request count, and classifier cost.",
)
@handle_analytics_errors("routing summary analytics")
async def get_routing_summary(
    user: User = Depends(authenticate),
    params: AnalyticsFilterParams = Depends(),
) -> JSONResponse:
    """Get routing analytics summary: session count, request count, classifier cost."""
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_routing_summary(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
    )
    return _create_response(data, SummariesResponse)


@router.get(
    "/routing/sessions",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get routing sessions",
    description="Retrieve model switches across all sessions.",
)
@handle_analytics_errors("routing sessions analytics")
async def get_routing_sessions(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get model switches across all sessions."""
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_model_switches(
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
    "/routing/decisions",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get routing decision timeline",
    description="Retrieve individual routing decisions for routing analytics visualizations.",
)
@handle_analytics_errors("routing decision timeline analytics")
async def get_routing_decisions(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get individual routing decisions for the routing timeline chart."""
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_decision_timeline(
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
    "/routing/activity",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get routing activity over time",
)
@handle_analytics_errors("routing activity analytics")
async def get_routing_activity(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get routing request volume over time, grouped by routing tier."""
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_routing_activity(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/routing/paths",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get grouped routing paths",
)
@handle_analytics_errors("routing paths analytics")
async def get_routing_paths(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get router, routed-model, and tier path metrics."""
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_routing_paths(
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
    "/routing/routed-model-distribution",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get routed model distribution",
)
@handle_analytics_errors("routed model distribution analytics")
async def get_routed_model_distribution(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_routed_model_distribution(
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
    "/routing/requested-model-distribution",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get router distribution",
)
@handle_analytics_errors("router distribution analytics")
async def get_requested_model_distribution(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_requested_model_distribution(
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
    "/routing/sessions/{session_id}/switches",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get model switches for a session",
    description="Retrieve model switches for a specific session.",
)
@handle_analytics_errors("routing session switches analytics")
async def get_routing_session_switches(
    session_id: str = Path(..., description="Session identifier"),
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get model switches for a specific session."""
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_model_switches(
        session_id=session_id,
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
    "/routing/tier-distribution",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get routing tier distribution",
    description="Retrieve routing tier distribution analytics.",
)
@handle_analytics_errors("routing tier distribution analytics")
async def get_routing_tier_distribution(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get routing tier distribution."""
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_tier_distribution(
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
    "/routing/decision-source-distribution",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get routing decision source distribution",
    description="Retrieve routing decision source distribution analytics.",
)
@handle_analytics_errors("routing decision source distribution analytics")
async def get_routing_decision_source_distribution(
    user: User = Depends(authenticate),
    params: AnalyticsQueryParams = Depends(),
) -> JSONResponse:
    """Get routing decision source distribution."""
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_decision_source_distribution(
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
    "/routing/classifier-overhead",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get routing classifier overhead",
    description="Retrieve routing classifier overhead: cost and token totals.",
)
@handle_analytics_errors("routing classifier overhead analytics")
async def get_routing_classifier_overhead(
    user: User = Depends(authenticate),
    params: AnalyticsFilterParams = Depends(),
) -> JSONResponse:
    """Get routing classifier overhead: cost and token totals."""
    _ensure_routing_analytics_enabled()
    service = AnalyticsService(user)
    data = await service.get_classifier_overhead(
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
    )
    return _create_response(data, SummariesResponse)
