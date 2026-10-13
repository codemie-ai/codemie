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

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.analytics import (
    TabularResponse,
)
from codemie.rest_api.routers.analytics._common import (
    AnalyticsFilterParams,
    _create_response,
    handle_analytics_errors,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.analytics_service import AnalyticsService
from codemie.service.analytics.handlers.cli_handler import EnrichedUserScope

router = APIRouter()


_ENRICHMENT_DISABLED_MSG = "User enrichment analytics is not enabled for this instance."


_ENRICHED_USER_ROUTE_PARAMS: dict = {
    "status_code": status.HTTP_200_OK,
    "response_model": TabularResponse,
    "response_model_by_alias": True,
}


class CLIEnrichedUserBaseParams(AnalyticsFilterParams):
    """Shared query parameters for enriched-user analytics endpoints."""


class CLIEnrichedUserParams(CLIEnrichedUserBaseParams):
    page: int = Query(0, ge=0)
    per_page: int = Query(50, ge=1, le=1000)
    top_n: int | None = Query(None, ge=1, description="Limit results to top N rows")


async def _run_enriched_user_insight(
    user: User,
    scope: EnrichedUserScope,
    params: CLIEnrichedUserParams,
) -> JSONResponse:
    if not customer_config.is_feature_enabled("userEnrichmentEnabled"):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=_ENRICHMENT_DISABLED_MSG,
            details="Enable the 'features:userEnrichmentEnabled' component in customer-config.yaml.",
        )
    service = AnalyticsService(user)
    data = await service.get_cli_insights_by_enriched_user(
        scope=scope,
        time_period=params.time_period,
        start_date=params.start_date,
        end_date=params.end_date,
        users=params.users_list,
        projects=params.projects_list,
        page=params.page,
        per_page=params.per_page,
        top_n=params.top_n,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/cli-insights-by-enriched-user-primary-skill",
    **_ENRICHED_USER_ROUTE_PARAMS,
)
@handle_analytics_errors("CLI insights by enriched user primary skill")
async def get_cli_insights_by_enriched_user_primary_skill(
    user: User = Depends(authenticate),
    params: CLIEnrichedUserParams = Depends(),
) -> JSONResponse:
    """CLI users and cost grouped by primary skill (requires userEnrichmentEnabled)."""
    return await _run_enriched_user_insight(user, EnrichedUserScope.PRIMARY_SKILL, params)


@router.get(
    "/cli-insights-by-enriched-user-country",
    **_ENRICHED_USER_ROUTE_PARAMS,
)
@handle_analytics_errors("CLI insights by enriched user country")
async def get_cli_insights_by_enriched_user_country(
    user: User = Depends(authenticate),
    params: CLIEnrichedUserParams = Depends(),
) -> JSONResponse:
    """CLI users and cost grouped by country (requires userEnrichmentEnabled)."""
    return await _run_enriched_user_insight(user, EnrichedUserScope.COUNTRY, params)


@router.get(
    "/cli-insights-by-enriched-user-city",
    **_ENRICHED_USER_ROUTE_PARAMS,
)
@handle_analytics_errors("CLI insights by enriched user city")
async def get_cli_insights_by_enriched_user_city(
    user: User = Depends(authenticate),
    params: CLIEnrichedUserParams = Depends(),
) -> JSONResponse:
    """CLI users and cost grouped by city (requires userEnrichmentEnabled)."""
    return await _run_enriched_user_insight(user, EnrichedUserScope.CITY, params)


@router.get(
    "/cli-insights-by-enriched-user-job-title",
    **_ENRICHED_USER_ROUTE_PARAMS,
)
@handle_analytics_errors("CLI insights by enriched user job title")
async def get_cli_insights_by_enriched_user_job_title(
    user: User = Depends(authenticate),
    params: CLIEnrichedUserParams = Depends(),
) -> JSONResponse:
    """CLI users and cost grouped by job title (requires userEnrichmentEnabled)."""
    return await _run_enriched_user_insight(user, EnrichedUserScope.JOB_TITLE, params)


@router.get(
    "/cli-insights-by-enriched-user-job-title-group",
    **_ENRICHED_USER_ROUTE_PARAMS,
)
@handle_analytics_errors("CLI insights by enriched user job title group")
async def get_cli_insights_by_enriched_user_job_title_group(
    user: User = Depends(authenticate),
    params: CLIEnrichedUserParams = Depends(),
) -> JSONResponse:
    """CLI users and cost grouped by job title group (requires userEnrichmentEnabled)."""
    return await _run_enriched_user_insight(user, EnrichedUserScope.JOB_TITLE_GROUP, params)
