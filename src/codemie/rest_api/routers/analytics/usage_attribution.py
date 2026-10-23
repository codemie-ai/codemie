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

"""Usage Attribution endpoints: who pays for Web Platform and CLI usage, CLI categories, chargeback candidates.

Admins, maintainers and auditors only; 404 when ``features:usageAttribution`` is off. There is no project or user
filter: the tab always attributes all usage. ``overrides`` carries the caller's setting overrides (admins and
maintainers only), see ``service/usage_attribution/settings.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import wraps
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from codemie.configs.config import config
from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.analytics import PaginationMetadata
from codemie.rest_api.models.usage_attribution import (
    UsageAttributionResponse,
    UsageAttributionSettingsResponse,
    UsageAttributionTableResponse,
)
from codemie.rest_api.routers.analytics._common import handle_analytics_errors
from codemie.rest_api.security.authentication import (
    admin_or_maintainer_access_only,
    admin_or_maintainer_or_auditor_access,
    authenticate,
)
from codemie.rest_api.security.user import User
from codemie.service.usage_attribution.analysis import (
    TIME_PERIODS,
    AnalysisUnavailableError,
    RangeError,
    RangeInfo,
    RefreshTooSoonError,
)
from codemie.service.usage_attribution.constants import CATEGORIES, FEATURE_KEY, PEOPLE_GROUP_BY
from codemie.service.usage_attribution.handler import (
    PEOPLE_FILTERS,
    EntityNotFoundError,
    FeatureUnavailableError,
    TuningForbiddenError,
    UsageAttributionHandler,
    effective_settings,
    range_for,
)
from codemie.service.usage_attribution.limits import CustomLimitError
from codemie.service.usage_attribution.settings import EffectiveSettings, SettingsConfigError, SettingsError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usage-attribution")

_OVERRIDES_DESC = "Setting overrides: base64url-encoded JSON of {dotted.key: value}, at most 4 KB (admins, maintainers)"
_SORT_ORDER_PATTERN = "^(asc|desc)$"
_ERROR_HELP = "Contact your administrator if the problem persists."


def _require_usage_attribution_enabled() -> None:
    """Dependency: 404 when the feature flag is off, like other disabled analytics families."""
    if not customer_config.is_feature_enabled(FEATURE_KEY):
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Usage Attribution is not available",
            details="The usageAttribution feature flag is disabled on this instance.",
            help="Contact your administrator to enable Usage Attribution.",
        )


_GATES = [Depends(admin_or_maintainer_or_auditor_access), Depends(_require_usage_attribution_enabled)]
# Recompute changes what everyone sees (it drops the cached analysis): admins and maintainers only.
_REFRESH_GATES = [Depends(admin_or_maintainer_access_only), Depends(_require_usage_attribution_enabled)]
_TIME_PERIOD_PATTERN = "^(" + "|".join(TIME_PERIODS) + ")$"


class UsageAttributionParams(BaseModel):
    """The period, and the caller's setting overrides. There are no users or projects parameters."""

    time_period: str | None = Query(None, pattern=_TIME_PERIOD_PATTERN)
    start_date: datetime | None = Query(None)
    end_date: datetime | None = Query(None)
    overrides: str | None = Query(None, description=_OVERRIDES_DESC, max_length=16384)


def _error(
    code: int, message: str, details: str | None = None, help_text: str | None = None, retry_after: int | None = None
) -> JSONResponse:
    """``{"error": {"message", "details", "help", "retry_after"?}}``. ``retry_after`` repeats the Retry-After header
    in seconds: a page on another origin can't read the header, which CORS doesn't expose."""
    error: dict[str, Any] = {"message": message, "details": details, "help": help_text}
    if retry_after:
        error["retry_after"] = int(retry_after)
    response = JSONResponse(status_code=code, content={"error": error})
    if retry_after:
        response.headers["Retry-After"] = str(int(retry_after))
    response.headers["Cache-Control"] = "no-store"
    return response


def _settings_error_response(exc: SettingsError) -> JSONResponse:
    return _error(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "Invalid analysis settings",
        f"{exc.key}: {exc.message}" if exc.key else exc.message,
        "Open Analysis Settings to review the values, or reset them to the defaults.",
    )


def _tuning_forbidden_response(_exc: TuningForbiddenError) -> JSONResponse:
    return _error(
        status.HTTP_403_FORBIDDEN,
        "Access denied",
        "Only admins and maintainers can change the analysis settings; auditors see them read-only.",
        "Reset the analysis settings to the defaults.",
    )


def _custom_limit_response(exc: CustomLimitError) -> JSONResponse:
    return _error(status.HTTP_429_TOO_MANY_REQUESTS, "Too many analyses", exc.message, None, exc.retry_after)


def _refresh_too_soon_response(exc: RefreshTooSoonError) -> JSONResponse:
    return _error(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Recompute was requested too recently",
        "Recompute runs at most once a minute for a period and settings.",
        None,
        exc.retry_after,
    )


def _analysis_unavailable_response(exc: AnalysisUnavailableError) -> JSONResponse:
    return _error(
        exc.status_code,
        "Usage attribution analysis is not available",
        exc.message,
        _ERROR_HELP,
        exc.retry_after,
    )


def _entity_not_found_response(_exc: EntityNotFoundError) -> JSONResponse:
    return _error(
        status.HTTP_404_NOT_FOUND,
        "Not found",
        "Nothing was found for it in the analysis of this period.",
        "Reload the tab: the analysis may have been recomputed.",
    )


def _feature_unavailable_response(exc: FeatureUnavailableError) -> JSONResponse:
    return _error(status.HTTP_404_NOT_FOUND, "Not available", exc.message, _ERROR_HELP)


def _range_error_response(exc: RangeError) -> JSONResponse:
    return _error(
        status.HTTP_400_BAD_REQUEST,
        "Invalid period",
        str(exc),
        "Check the time period, or the start and end dates.",
    )


def _settings_config_error_response(exc: SettingsConfigError) -> JSONResponse:
    return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "Usage Attribution is misconfigured", str(exc), _ERROR_HELP)


# Order does not matter: none of these domain error types are subclasses of one another.
_ERROR_RESPONSE_MAPPERS: list[tuple[type[Exception], Callable[[Any], JSONResponse]]] = [
    (SettingsError, _settings_error_response),
    (TuningForbiddenError, _tuning_forbidden_response),
    (CustomLimitError, _custom_limit_response),
    (RefreshTooSoonError, _refresh_too_soon_response),
    (AnalysisUnavailableError, _analysis_unavailable_response),
    (EntityNotFoundError, _entity_not_found_response),
    (FeatureUnavailableError, _feature_unavailable_response),
    (RangeError, _range_error_response),
    (SettingsConfigError, _settings_config_error_response),
]


def _unexpected_error_response() -> JSONResponse:
    logger.exception("usage attribution request failed")
    return _error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Usage attribution request failed",
        "An unexpected error occurred; it was logged.",
        _ERROR_HELP,
    )


def _map_usage_attribution_error(exc: Exception) -> JSONResponse:
    for exc_type, respond in _ERROR_RESPONSE_MAPPERS:
        if isinstance(exc, exc_type):
            return respond(exc)
    return _unexpected_error_response()


def usage_attribution_errors(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Map the domain errors to HTTP responses: 422 invalid settings, 403 tuning not allowed, 404 unknown entity,
    429 limits (with Retry-After), 503/504 analysis unavailable."""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except ExtendedHTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - details (SQL, Elasticsearch bodies) stay in the log
            return _map_usage_attribution_error(exc)

    return wrapper


def _respond(model: type[BaseModel], payload: dict[str, Any]) -> JSONResponse:
    validated = model.model_validate(payload)
    response = JSONResponse(content=validated.model_dump(by_alias=True), status_code=status.HTTP_200_OK)
    # A stale analysis (shown while a computation failed or runs) must not stay in the browser cache.
    stale = bool((payload.get("analysis") or {}).get("stale"))
    response.headers["Cache-Control"] = "no-store" if config.is_local or stale else "private, max-age=300"
    return response


def _prepare(
    user: User, params: UsageAttributionParams
) -> tuple[UsageAttributionHandler, EffectiveSettings, RangeInfo]:
    eff = effective_settings(user, params.overrides)
    rng = range_for(eff, params.time_period, params.start_date, params.end_date)
    return UsageAttributionHandler(user), eff, rng


def _composite(rng: RangeInfo, data: dict[str, Any]) -> dict[str, Any]:
    analysis = data.pop("analysis", None)
    return {"data": data, "range": rng.info(), "analysis": analysis}


def _table(rng: RangeInfo, data: dict[str, Any]) -> dict[str, Any]:
    block = {"rows": data["rows"], "totals": data.get("totals")}
    if "none" in data:  # People groups: the work no other candidate shares
        block["none"] = data["none"]
    return {
        "data": block,
        "pagination": PaginationMetadata(**data["pagination"]).model_dump(),
        "counts": data.get("counts") or {},
        "threshold": data.get("threshold"),
        "display": data.get("display"),
        "range": rng.info(),
        "analysis": data["analysis"],
    }


# ---------------------------------------------------------------------------------------------------------------------
# Fast
# ---------------------------------------------------------------------------------------------------------------------
@router.get(
    "/totals",
    response_model=UsageAttributionResponse,
    summary="Total, Web Platform, CLI, Charged Back and Who Pays",
    description="Fast sums without the analysis (A1): about 2-3 s on first load, cached for 15 minutes.",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution totals")
@usage_attribution_errors
async def get_totals(user: User = Depends(authenticate), params: UsageAttributionParams = Depends()) -> JSONResponse:
    handler, _eff, rng = _prepare(user, params)
    data = await handler.totals(rng)
    return _respond(UsageAttributionResponse, {"data": data, "range": rng.info(), "analysis": None})


@router.get(
    "/settings",
    response_model=UsageAttributionSettingsResponse,
    summary="Analysis settings: defaults, the tunable schema and whether the caller may tune",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution settings")
@usage_attribution_errors
async def get_settings(user: User = Depends(authenticate)) -> JSONResponse:
    data = UsageAttributionHandler(user).settings()
    return _respond(UsageAttributionSettingsResponse, {"data": data})


# ---------------------------------------------------------------------------------------------------------------------
# Analysis-backed
# ---------------------------------------------------------------------------------------------------------------------
@router.get(
    "/cli-summary",
    response_model=UsageAttributionResponse,
    summary="CLI categories: tiles, Where the Money Goes, Who Pays and CLI Spend by Category",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution CLI summary")
@usage_attribution_errors
async def get_cli_summary(
    user: User = Depends(authenticate), params: UsageAttributionParams = Depends()
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    return _respond(UsageAttributionResponse, _composite(rng, await handler.cli_summary(rng, eff)))


@router.get(
    "/shared-projects",
    response_model=UsageAttributionTableResponse,
    summary="Shared Projects Without a Budget",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution shared projects")
@usage_attribution_errors
async def get_shared_projects(
    user: User = Depends(authenticate),
    params: UsageAttributionParams = Depends(),
    scope: str = Query("threshold", pattern="^(threshold|all)$"),
    search: str | None = Query(None, max_length=200),
    page: int = Query(0, ge=0),
    per_page: int = Query(10, ge=1, le=200),
    sort_by: str | None = Query(None, max_length=40),
    sort_order: str = Query("desc", pattern=_SORT_ORDER_PATTERN),
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    data = await handler.shared_projects(rng, eff, scope, search, page, per_page, sort_by, sort_order)
    return _respond(UsageAttributionTableResponse, _table(rng, data))


@router.get(
    "/shared-projects/{project_name}",
    response_model=UsageAttributionResponse,
    summary="Shared project popup",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution shared project")
@usage_attribution_errors
async def get_shared_project(
    project_name: str = Path(..., max_length=200),
    user: User = Depends(authenticate),
    params: UsageAttributionParams = Depends(),
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    return _respond(UsageAttributionResponse, _composite(rng, await handler.shared_project(rng, eff, project_name)))


@router.get(
    "/repository-groups",
    response_model=UsageAttributionTableResponse,
    summary="Repository Groups in Personal Projects",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution repository groups")
@usage_attribution_errors
async def get_repository_groups(
    user: User = Depends(authenticate),
    params: UsageAttributionParams = Depends(),
    scope: str = Query("threshold", pattern="^(threshold|all)$"),
    search: str | None = Query(None, max_length=200),
    page: int = Query(0, ge=0),
    per_page: int = Query(10, ge=1, le=200),
    sort_by: str | None = Query(None, max_length=40),
    sort_order: str = Query("desc", pattern=_SORT_ORDER_PATTERN),
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    data = await handler.repository_groups(rng, eff, scope, search, page, per_page, sort_by, sort_order)
    return _respond(UsageAttributionTableResponse, _table(rng, data))


@router.get(
    "/repository-groups/{group_id}",
    response_model=UsageAttributionResponse,
    summary="Repository group popup",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution repository group")
@usage_attribution_errors
async def get_repository_group(
    group_id: str = Path(..., pattern="^g-[0-9a-f]{6,40}$"),
    user: User = Depends(authenticate),
    params: UsageAttributionParams = Depends(),
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    return _respond(UsageAttributionResponse, _composite(rng, await handler.repository_group(rng, eff, group_id)))


@router.get(
    "/breakdown",
    response_model=UsageAttributionResponse,
    summary="CLI spend paid from personal budgets, by country or job title group",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution breakdown")
@usage_attribution_errors
async def get_breakdown(
    user: User = Depends(authenticate),
    params: UsageAttributionParams = Depends(),
    dimension: str = Query("country", pattern="^(country|job)$"),
    category: str = Query("production", pattern="^(" + "|".join(CATEGORIES) + ")$"),
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    return _respond(UsageAttributionResponse, _composite(rng, await handler.breakdown(rng, eff, dimension, category)))


@router.get(
    "/people",
    response_model=UsageAttributionTableResponse,
    summary="People",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution people")
@usage_attribution_errors
async def get_people(
    user: User = Depends(authenticate),
    params: UsageAttributionParams = Depends(),
    filter: str = Query("all", pattern="^(" + "|".join(PEOPLE_FILTERS) + ")$"),
    search: str | None = Query(None, max_length=200),
    page: int = Query(0, ge=0),
    per_page: int = Query(10, ge=1, le=200),
    sort_by: str | None = Query(None, max_length=40),
    sort_order: str = Query("desc", pattern=_SORT_ORDER_PATTERN),
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    data = await handler.people(rng, eff, filter, search, page, per_page, sort_by, sort_order)
    return _respond(UsageAttributionTableResponse, _table(rng, data))


@router.get(
    "/people/{user_id}",
    response_model=UsageAttributionResponse,
    summary="Person popup",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution person")
@usage_attribution_errors
async def get_person(
    user_id: str = Path(..., max_length=200),
    user: User = Depends(authenticate),
    params: UsageAttributionParams = Depends(),
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    return _respond(UsageAttributionResponse, _composite(rng, await handler.person(rng, eff, user_id)))


@router.get(
    "/people-summary",
    response_model=UsageAttributionResponse,
    summary="People: chargeback candidates and how much of their work they share (tiles)",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution people summary")
@usage_attribution_errors
async def get_people_summary(
    user: User = Depends(authenticate), params: UsageAttributionParams = Depends()
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    return _respond(UsageAttributionResponse, _composite(rng, await handler.people_summary(rng, eff)))


@router.get(
    "/people-groups",
    response_model=UsageAttributionTableResponse,
    summary="People: chargeback candidates grouped by common condition",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution people groups")
@usage_attribution_errors
async def get_people_groups(
    user: User = Depends(authenticate),
    params: UsageAttributionParams = Depends(),
    group_by: str = Query("common", pattern="^(" + "|".join(PEOPLE_GROUP_BY) + ")$"),
    min_amount: int = Query(0, ge=0, le=100000, description="Per 30 days, scaled to the period"),
    search: str | None = Query(None, max_length=200),
    page: int = Query(0, ge=0),
    per_page: int = Query(25, ge=1, le=200),
    sort_by: str | None = Query(None, max_length=40),
    sort_order: str = Query("desc", pattern=_SORT_ORDER_PATTERN),
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    data = await handler.people_groups(rng, eff, group_by, min_amount, search, page, per_page, sort_by, sort_order)
    return _respond(UsageAttributionTableResponse, _table(rng, data))


@router.get(
    "/people-groups/{group_by}/{group_id}",
    response_model=UsageAttributionResponse,
    summary="People: one group of chargeback candidates",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution people group")
@usage_attribution_errors
async def get_people_group(
    group_by: str = Path(..., pattern="^(" + "|".join(PEOPLE_GROUP_BY) + ")$"),
    group_id: str = Path(..., max_length=80),
    user: User = Depends(authenticate),
    params: UsageAttributionParams = Depends(),
) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    return _respond(UsageAttributionResponse, _composite(rng, await handler.people_group(rng, eff, group_by, group_id)))


@router.get(
    "/status",
    response_model=UsageAttributionResponse,
    summary="How It Works: coverage, data quality, run details and the settings in effect",
    dependencies=_GATES,
)
@handle_analytics_errors("usage attribution status")
@usage_attribution_errors
async def get_status(user: User = Depends(authenticate), params: UsageAttributionParams = Depends()) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    return _respond(UsageAttributionResponse, _composite(rng, await handler.status(rng, eff)))


@router.post(
    "/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Recompute the analysis of a period and settings (at most once a minute; admins and maintainers)",
    dependencies=_REFRESH_GATES,
)
@handle_analytics_errors("usage attribution refresh")
@usage_attribution_errors
async def post_refresh(user: User = Depends(authenticate), params: UsageAttributionParams = Depends()) -> JSONResponse:
    handler, eff, rng = _prepare(user, params)
    data = await handler.refresh(rng, eff)
    response = JSONResponse(content={"data": data, "range": rng.info()}, status_code=status.HTTP_202_ACCEPTED)
    response.headers["Cache-Control"] = "no-store"
    return response
