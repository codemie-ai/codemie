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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from codemie.configs.config import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.analytics import (
    AnalyticsDetailResponse,
    SummariesResponse,
    TabularResponse,
)
from codemie.rest_api.routers.analytics._common import (
    ERROR_MSG_ACCESS_DENIED,
    ERROR_MSG_ADMIN_HELP,
    _create_response,
    handle_analytics_errors,
)
from codemie.rest_api.security.authentication import (
    admin_access_only,
    admin_or_maintainer_or_auditor_access,
    authenticate,
)
from codemie.rest_api.security.user import User
from codemie.service.analytics.analytics_service import AnalyticsService

router = APIRouter()


LEADERBOARD_SNAPSHOT_ID_DESC = "Specific snapshot ID (defaults to latest)"
LEADERBOARD_VIEW_PATTERN = "^(current|monthly|quarterly)$"
LEADERBOARD_VIEW_DESC = "Leaderboard view: current, monthly, quarterly"
LEADERBOARD_SEASON_KEY_DESC = "Optional season key for seasonal views, e.g. 2026-03 or 2026-Q1"


# ── Leaderboard endpoints (read: admin/maintainer/auditor, compute: admin-only) ──


def _require_leaderboard_enabled() -> None:
    """Dependency: raises 503 when LEADERBOARD_ENABLED is False."""
    if not config.LEADERBOARD_ENABLED:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Leaderboard is not enabled")


@router.get(
    "/leaderboard/summary",
    status_code=status.HTTP_200_OK,
    response_model=SummariesResponse,
    response_model_by_alias=True,
    summary="Get leaderboard summary metrics",
    description="Returns high-level leaderboard metrics: total users, tier counts, top score, etc.",
    dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard summary")
async def get_leaderboard_summary(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
) -> JSONResponse:
    """Get leaderboard summary metrics."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_summary(snapshot_id, view=view, season_key=season_key)
    return _create_response(data, SummariesResponse)


@router.get(
    "/leaderboard/entries",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get leaderboard entries table",
    description="Returns paginated leaderboard entries with scores and tier info.",
    dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard entries")
async def get_leaderboard_entries(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
    tier: str | None = Query(
        None,
        description="Filter by tier name (pioneer, expert, advanced, practitioner, newcomer)",
    ),
    search: str | None = Query(
        None,
        description="Search by user name or email (case-insensitive partial match)",
    ),
    intent: str | None = Query(
        None,
        description="Filter by usage intent (e.g. cli_focused, platform_focused, hybrid, sdlc_unicorn)",
    ),
    sort_by: str | None = Query(
        None,
        description="Sort column: rank, total_score, user_name, tier_level",
    ),
    sort_order: str = Query(
        "asc",
        description="Sort direction: asc or desc",
        pattern="^(asc|desc)$",
    ),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> JSONResponse:
    """Get paginated leaderboard entries with optional filtering and sorting."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_entries(
        snapshot_id,
        tier,
        page,
        per_page,
        search,
        intent,
        sort_by,
        sort_order,
        view=view,
        season_key=season_key,
    )
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/user/{user_id}",
    status_code=status.HTTP_200_OK,
    response_model=AnalyticsDetailResponse,
    response_model_by_alias=True,
    summary="Get leaderboard detail for a specific user",
    description="Returns detailed leaderboard data for a single user including dimension breakdowns. "
    "Accepts either a user ID or user email as the path parameter. Admin only.",
    dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard user detail")
async def get_leaderboard_user_detail(
    user_id: str,
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
) -> JSONResponse:
    """Get detailed leaderboard data for a specific user."""
    if not (user.is_admin_or_maintainer or getattr(user, "is_auditor", False)):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ERROR_MSG_ACCESS_DENIED,
            details="Leaderboard API access is restricted to administrators.",
            help=ERROR_MSG_ADMIN_HELP,
        )
    service = AnalyticsService(user)
    data = await service.get_leaderboard_user_detail(user_id, snapshot_id, view=view, season_key=season_key)
    return _create_response(data, AnalyticsDetailResponse)


@router.get(
    "/leaderboard/tiers",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get tier distribution",
    description="Returns user count and percentage per tier.",
    dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard tier distribution")
async def get_leaderboard_tier_distribution(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
) -> JSONResponse:
    """Get tier distribution data."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_tier_distribution(snapshot_id, view=view, season_key=season_key)
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/scores",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get score distribution",
    description="Returns histogram of user scores in 10-point bins.",
    dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard score distribution")
async def get_leaderboard_score_distribution(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
) -> JSONResponse:
    """Get score distribution histogram."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_score_distribution(snapshot_id, view=view, season_key=season_key)
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/dimensions",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get dimension breakdown",
    description="Returns average scores per scoring dimension across all users.",
    dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard dimension breakdown")
async def get_leaderboard_dimension_breakdown(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
) -> JSONResponse:
    """Get dimension breakdown with averages."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_dimension_breakdown(snapshot_id, view=view, season_key=season_key)
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/top-performers",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get top performers",
    description="Returns top N leaderboard entries by total score.",
    dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard top performers")
async def get_leaderboard_top_performers(
    user: User = Depends(authenticate),
    snapshot_id: str | None = Query(None, description=LEADERBOARD_SNAPSHOT_ID_DESC),
    view: str = Query("current", description=LEADERBOARD_VIEW_DESC, pattern=LEADERBOARD_VIEW_PATTERN),
    season_key: str | None = Query(None, description=LEADERBOARD_SEASON_KEY_DESC),
    limit: int = Query(3, ge=1, le=50, description="Number of top performers to return"),
) -> JSONResponse:
    """Get top N performers."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_top_performers(snapshot_id, limit, view=view, season_key=season_key)
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/snapshots",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get leaderboard snapshots",
    description="Returns paginated list of leaderboard computation snapshots.",
    dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard snapshots")
async def get_leaderboard_snapshots(
    user: User = Depends(authenticate),
    view: str | None = Query(
        None,
        description="Optional leaderboard view filter: current, monthly, quarterly",
        pattern=LEADERBOARD_VIEW_PATTERN,
    ),
    status: str | None = Query(None, description="Optional snapshot status filter"),
    is_final: bool | None = Query(None, description="Optional finality filter"),
    page: int = Query(0, ge=0),
    per_page: int = Query(10, ge=1, le=100),
) -> JSONResponse:
    """Get list of leaderboard snapshots."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_snapshots(page, per_page, view=view, status=status, is_final=is_final)
    return _create_response(data, TabularResponse)


@router.get(
    "/leaderboard/seasons",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get available leaderboard seasons",
    description="Returns available completed monthly or quarterly leaderboard seasons for UI selectors.",
    dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard seasons")
async def get_leaderboard_seasons(
    user: User = Depends(authenticate),
    view: str = Query(
        ..., description="Seasonal leaderboard view: monthly or quarterly", pattern="^(monthly|quarterly)$"
    ),
    page: int = Query(0, ge=0),
    per_page: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    """Get available monthly or quarterly seasons."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_seasons(view, page, per_page)
    return _create_response(data, TabularResponse)


@router.post(
    "/leaderboard/compute",
    status_code=status.HTTP_200_OK,
    response_model=AnalyticsDetailResponse,
    response_model_by_alias=True,
    summary="Trigger leaderboard computation",
    description="Manually triggers a leaderboard computation. Admin only.",
    dependencies=[Depends(admin_access_only), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard compute")
async def trigger_leaderboard_computation(
    user: User = Depends(authenticate),
    period_days: int = Query(30, ge=1, le=365, description="Number of days to include in computation"),
    view: str = Query(
        "current",
        description="Leaderboard view to compute: current, monthly, quarterly",
        pattern="^(current|monthly|quarterly)$",
    ),
    season_key: str | None = Query(
        None, description="Optional season key for seasonal computation, e.g. 2026-03 or 2026-Q1"
    ),
) -> JSONResponse:
    """Trigger a manual leaderboard computation."""
    service = AnalyticsService(user)
    data = await service.trigger_leaderboard_computation(period_days, view=view, season_key=season_key)
    return _create_response(data, AnalyticsDetailResponse)


@router.get(
    "/leaderboard/framework",
    status_code=status.HTTP_200_OK,
    response_model=AnalyticsDetailResponse,
    response_model_by_alias=True,
    summary="Get leaderboard framework metadata",
    description="Returns static scoring framework metadata: dimension descriptions, "
    "component explanations, tier definitions, intent definitions, and scoring principles. "
    "This data is static and can be cached indefinitely by the client.",
    dependencies=[Depends(admin_or_maintainer_or_auditor_access), Depends(_require_leaderboard_enabled)],
)
@handle_analytics_errors("leaderboard framework")
async def get_leaderboard_framework(
    user: User = Depends(authenticate),
) -> JSONResponse:
    """Get leaderboard scoring framework metadata."""
    service = AnalyticsService(user)
    data = await service.get_leaderboard_framework()
    return _create_response(data, AnalyticsDetailResponse)
