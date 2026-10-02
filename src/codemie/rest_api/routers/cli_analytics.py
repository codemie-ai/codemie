# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""OTel CLI Analytics endpoints.

Serves the contract consumed by codemie-ui's OTel CLI Analytics tab
(`src/types/localAnalytics.ts`). Field names and shapes follow that contract;
the data behind them comes from the `codemie_analytics` PostgreSQL storage (see
codemie.repository.cli_analytics). This module parses requests and maps storage
errors to HTTP statuses; it never touches an engine directly.

Three modelling decisions are invisible in the payload but change every number
(see ANALYTICS_DISCOVERY/local_analytics_implementation_plan.md §3):

  D1  Session universe = sessions that made a priced API call, i.e. those present
      in coding_agent_cost_daily. The hook-event universe is broader (29 vs 16
      sessions in the trailing 7 days at time of writing) and is what
      /v1/analytics/coding-agents uses; the Local Analytics contract implies the
      priced universe, so KPIs here are restricted to it.

  D2  Repository key = the directory the session STARTED in, basename-normalised
      (v_session_dimensions.repository). `cwd` is not stable within a session, so
      anyLast() would be non-deterministic. `repo_remote` would be authoritative
      but covers only ~59% of sessions, so it is not used as the grouping key.

  D3  turns = claude_code.interaction spans, matching the contract's meaning of a
      user interaction (not the count of API calls).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse

from codemie.configs.config import config
from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.cli_analytics.factory import get_cli_analytics_storage
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.hook_events import datetime_to_ns
from codemie.repository.cli_analytics.ports import (
    IngestResult,
    InvalidTelemetryPayloadError,
    OtlpSignal,
    CliAnalyticsStorageConfigError,
    TelemetryIngestError,
    TelemetryStorageUnavailableError,
    UnsupportedTelemetryContentTypeError,
)
from codemie.rest_api.models.cli_analytics import (
    LocalAnalyticsActivityResponse,
    LocalAnalyticsCostResponse,
    LocalAnalyticsEfficiencyResponse,
    LocalAnalyticsFrameworksResponse,
    LocalAnalyticsOverviewResponse,
    LocalAnalyticsRepositoriesResponse,
    LocalAnalyticsSessionDetailResponse,
    LocalAnalyticsSessionsResponse,
    LocalAnalyticsToolsResponse,
    LocalAnalyticsUsersResponse,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.access_filter import AccessFilter
from codemie.service.analytics.delivery_framework import get_framework_labels
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler, now_iso
from codemie.service.analytics.handlers.user_identity_resolver import UserIdentityResolver
from codemie.service.analytics.time_parser import TimeParser

logger = logging.getLogger(__name__)

DEFAULT_PAGE = 0
DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 1000

_ERR_BODY_TOO_LARGE = "Request body too large"
# The storage refuses its configuration; the reason is logged at startup, not sent to clients.
_ERR_STORAGE_MISCONFIGURED = "Analytics storage is not available"
_PROTOBUF_CONTENT_TYPE = "application/x-protobuf"


_INGEST_ERROR_STATUS: dict[type[TelemetryIngestError], int] = {
    InvalidTelemetryPayloadError: status.HTTP_400_BAD_REQUEST,
    UnsupportedTelemetryContentTypeError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    TelemetryStorageUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _handler() -> LocalAnalyticsHandler:
    """The handler over the configured storage's reader (the storage is built on first use)."""
    return LocalAnalyticsHandler(get_cli_analytics_storage().reader)


async def _admin_gate(request: Request, _: User = Depends(authenticate)) -> None:
    """Allow Local Analytics only for super admins and project admins."""
    user = request.state.user
    if user.is_admin or user.is_applications_admin:
        return

    logger.warning("access_denied_cli_analytics: actor_user_id=%s", user.id)
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details="This action requires administrator or project administrator privileges.",
        help="If you believe you should have access, please contact your system administrator.",
    )


router = APIRouter(
    tags=["OTel CLI Analytics"],
    prefix="/v1/analytics/cli-analytics",
    dependencies=[Depends(authenticate)],
)


def _ensure_enabled() -> None:
    if not customer_config.is_feature_enabled("cliAnalytics"):
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Local Analytics feature is not available",
            details="The localAnalytics feature flag is disabled on this instance.",
            help="Contact your administrator to enable the Local Analytics feature.",
        )


def _ingest_response(result: IngestResult) -> Response:
    return Response(content=result.body, status_code=200, media_type=result.media_type)


def _ingest_http_error(exc: TelemetryIngestError) -> HTTPException:
    return HTTPException(status_code=_INGEST_ERROR_STATUS.get(type(exc), 503), detail=str(exc))


async def _ingest_otlp(request: Request, signal: OtlpSignal) -> Response:
    _ensure_enabled()
    body = await request.body()
    if len(body) > config.ANALYTICS_INGEST_MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=_ERR_BODY_TOO_LARGE)
    content_type = request.headers.get("content-type", _PROTOBUF_CONTENT_TYPE)
    try:
        result = await get_cli_analytics_storage().ingestor.ingest_otlp(signal, body, content_type)
    except TelemetryIngestError as exc:
        raise _ingest_http_error(exc) from exc
    except CliAnalyticsStorageConfigError as exc:  # retried by the client until the configuration is fixed
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_ERR_STORAGE_MISCONFIGURED) from exc
    return _ingest_response(result)


def _parse_ndjson(body: bytes) -> list[dict]:
    records = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except (ValueError, RecursionError):  # JSONDecodeError, UnicodeDecodeError, 4,300+ digit integers
            logger.debug("Skipping malformed NDJSON line")
            continue
        if isinstance(record, dict):  # a hook event is an object
            records.append(record)
        else:
            logger.debug("Skipping NDJSON line that is not an object")
    return records


def handle_errors(endpoint_name: str) -> Callable:
    def decorator(func: Callable[..., Awaitable[JSONResponse]]) -> Callable[..., Awaitable[JSONResponse]]:
        # functools.wraps is load-bearing, not cosmetic: it sets __wrapped__, which is how
        # FastAPI recovers the endpoint's real signature. Without it FastAPI introspects
        # (*args, **kwargs) and rejects every request with 422 "args field required".
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> JSONResponse:
            try:
                return await func(*args, **kwargs)
            except ExtendedHTTPException:
                raise
            except CliAnalyticsStorageConfigError as e:
                logger.error(f"Analytics storage unavailable for {endpoint_name}: {e}")
                raise ExtendedHTTPException(
                    code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    message=_ERR_STORAGE_MISCONFIGURED,
                    details="The analytics storage refuses its configuration; see the server log.",
                    help="Contact your administrator.",
                ) from e
            except (asyncpg.PostgresError, asyncpg.InterfaceError) as e:
                logger.error(f"Analytics storage failed for {endpoint_name}: {type(e).__name__}: {e}")
                raise ExtendedHTTPException(
                    code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    message=_ERR_STORAGE_MISCONFIGURED,
                    details="The analytics storage is unavailable; see the server log.",
                    help="Please try again later.",
                ) from e
            except OSError as e:  # the network, TLS or a timeout; before ValueError, which TLS errors also are
                logger.error(f"Analytics storage unreachable for {endpoint_name}: {type(e).__name__}: {e}")
                raise ExtendedHTTPException(
                    code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    message=_ERR_STORAGE_MISCONFIGURED,
                    details="The analytics storage cannot be reached; see the server log.",
                    help="Please try again later.",
                ) from e
            except ValueError as e:
                logger.warning(f"Invalid parameters for {endpoint_name}: {e}")
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Invalid request parameters",
                    details=str(e),
                    help="Please check your query parameter values.",
                ) from e
            except Exception as e:
                logger.exception(f"Failed to get {endpoint_name}: {e}")
                raise ExtendedHTTPException(
                    code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    message=f"Failed to retrieve {endpoint_name}",
                    details="An internal error occurred. Contact support if the issue persists.",
                    help="Please try again or contact support if the issue persists.",
                ) from e

        return wrapper

    return decorator


class FilterParams:
    def __init__(
        self,
        time_period: str | None = Query(None, description="e.g. last_7_days, last_30_days"),
        start_date: datetime | None = Query(None, description="Custom range start (ISO 8601)"),
        end_date: datetime | None = Query(None, description="Custom range end (ISO 8601)"),
        users: str | None = Query(None, description="Comma-separated CodeMie user IDs or emails"),
        projects: str | None = Query(None, description="Comma-separated CodeMie project names"),
        repositories: str | None = Query(None, description="Comma-separated repository names"),
    ) -> None:
        self.time_period = time_period
        self.start_date = start_date
        self.end_date = end_date
        self.users = users
        self.projects = projects
        self.repositories = repositories

    @staticmethod
    def _split(value: str | None) -> list[str] | None:
        if not value:
            return None
        items = [v.strip() for v in value.split(",") if v.strip()]
        return items or None

    async def resolve(self, user: User) -> LocalAnalyticsFilter:
        start_dt, end_dt = TimeParser.parse(self.time_period, self.start_date, self.end_date)

        # Raw rows are kept for less time than the rollups. A longer window would mix a year of
        # cost with fewer days of turns/tools/files, so the requested range is clamped and logged.
        retention_days = get_cli_analytics_storage().raw_retention_days
        max_span = TimeParser.PERIODS["last_7_days"] / 7 * retention_days
        if (end_dt - start_dt) > max_span:
            clamped = end_dt - max_span
            logger.warning(
                "cli_analytics: requested window %s..%s exceeds the %sd raw-table retention; "
                "clamping start to %s so trace-sourced metrics stay consistent with cost metrics",
                start_dt.isoformat(),
                end_dt.isoformat(),
                retention_days,
                clamped.isoformat(),
            )
            start_dt = clamped

        # The shared user filter sends CodeMie user IDs; the storage is keyed by email.
        emails = self._split(self.users)
        if emails:
            rows = [{"user": u} for u in emails]
            await UserIdentityResolver.resolve_rows(rows, "user", target="email")
            emails = [r["user"] for r in rows]

        projects = self._split(self.projects)
        deny_all = False
        ctx = AccessFilter(user).get_project_access_context()
        if not ctx.is_admin:
            visible = set(ctx.admin_projects)
            projects = list(set(projects) & visible) if projects else list(visible)
            # No visible admin project: select nothing rather than all data.
            deny_all = not projects

        return LocalAnalyticsFilter(
            start_dt=start_dt,
            end_dt=end_dt,
            users=emails,
            projects=projects,
            repositories=self._split(self.repositories),
            deny_all=deny_all,
        )


def _metadata(start_ns: int, data_as_of: str | None, unpriced: list[str], fallback: datetime) -> dict:
    return {
        "timestamp": now_iso(),
        "data_as_of": data_as_of or fallback.date().isoformat(),
        "execution_time_ms": round((time.monotonic_ns() - start_ns) / 1_000_000, 2),
        "unpriced_models": unpriced,
    }


def _respond(payload: dict, model_class: Any) -> JSONResponse:
    validated = model_class(**payload)
    body = validated.model_dump(by_alias=True)
    response = JSONResponse(content=body, status_code=status.HTTP_200_OK)
    if config.is_local:
        response.headers["Cache-Control"] = "no-store"
    else:
        response.headers["Cache-Control"] = "private, max-age=300"
        response.headers["ETag"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return response


# ── Ingest endpoints ────────────────────────────────────────────────────────


@router.post("/logs", status_code=200)
async def ingest_logs(request: Request) -> Response:
    return await _ingest_otlp(request, "logs")


@router.post("/metrics", status_code=200)
async def ingest_metrics(request: Request) -> Response:
    return await _ingest_otlp(request, "metrics")


@router.post("/traces", status_code=200)
async def ingest_traces(request: Request) -> Response:
    return await _ingest_otlp(request, "traces")


@router.post("/event-hooks", status_code=200)
async def ingest_event_hooks(request: Request) -> Response:
    _ensure_enabled()
    ct = request.headers.get("content-type", "")
    if ct and not ct.startswith("application/x-ndjson"):
        raise HTTPException(status_code=415, detail="Expected application/x-ndjson")
    raw = await request.body()
    if len(raw) > config.ANALYTICS_INGEST_MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=_ERR_BODY_TOO_LARGE)
    if not raw.strip():
        raise HTTPException(status_code=400, detail="Empty body")
    events = _parse_ndjson(raw)
    if not events:
        raise HTTPException(status_code=400, detail="No parseable events")
    now_ns = datetime_to_ns(datetime.now(timezone.utc))
    user = getattr(request.state, "user", None)
    user_email = user.email if user and user.email else ""
    try:
        result = await get_cli_analytics_storage().ingestor.ingest_hook_events(events, user_email, now_ns)
    except TelemetryIngestError as exc:
        raise _ingest_http_error(exc) from exc
    except CliAnalyticsStorageConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_ERR_STORAGE_MISCONFIGURED) from exc
    return _ingest_response(result)


# ── Read endpoints ──────────────────────────────────────────────────────────


@router.get(
    "/overview",
    response_model=LocalAnalyticsOverviewResponse,
    summary="Local analytics overview",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics overview")
async def get_overview(
    user: User = Depends(authenticate),
    filters: FilterParams = Depends(FilterParams),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    data, unpriced, data_as_of = await _handler().get_overview(f)
    return _respond(
        {"data": data, "metadata": _metadata(start_ns, data_as_of, unpriced, f.end_dt)}, LocalAnalyticsOverviewResponse
    )


@router.get(
    "/cost",
    response_model=LocalAnalyticsCostResponse,
    summary="Local analytics cost breakdown",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics cost")
async def get_cost(
    user: User = Depends(authenticate),
    filters: FilterParams = Depends(FilterParams),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    data, unpriced = await _handler().get_cost(f)
    return _respond(
        {"data": data, "metadata": _metadata(start_ns, None, unpriced, f.end_dt)}, LocalAnalyticsCostResponse
    )


@router.get(
    "/users",
    response_model=LocalAnalyticsUsersResponse,
    summary="Local analytics user leaderboard",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics users")
async def get_users(
    user: User = Depends(authenticate),
    filters: FilterParams = Depends(FilterParams),
    page: int | None = Query(None, ge=0),
    per_page: int | None = Query(None, ge=1, le=MAX_PER_PAGE),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    data = await _handler().get_users(f, page, per_page)
    await _attach_user_ids(data["rows"])
    return _respond({"data": data, "metadata": _metadata(start_ns, None, [], f.end_dt)}, LocalAnalyticsUsersResponse)


@router.get(
    "/user-charts",
    response_model=LocalAnalyticsUsersResponse,
    summary="Local analytics user chart data (unpaginated)",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics user charts")
async def get_user_charts(
    user: User = Depends(authenticate),
    filters: FilterParams = Depends(FilterParams),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    data = await _handler().get_users(f, None, None)
    data["total_count"] = None  # unpaginated by contract
    await _attach_user_ids(data["rows"])
    return _respond({"data": data, "metadata": _metadata(start_ns, None, [], f.end_dt)}, LocalAnalyticsUsersResponse)


async def _attach_user_ids(rows: list[dict]) -> None:
    """Resolve each row's email back to a CodeMie user UUID for the UI drill-down.

    `user_repository.afind_users_by_emails` does not exist on this branch; the
    established path here is UserIdentityResolver with target='id'.
    """
    if not rows:
        return
    lookup = [{"user": row["developer_name"]} for row in rows]
    try:
        await UserIdentityResolver.resolve_rows(lookup, "user", target="id")
    except Exception as e:  # identity resolution must never fail the analytics response
        logger.warning(f"cli_analytics: user id resolution failed: {e}")
        return
    for row, resolved in zip(rows, lookup, strict=False):
        value = resolved.get("user")
        row["user_id"] = value if value and value != row["developer_name"] else None


@router.get(
    "/repositories",
    response_model=LocalAnalyticsRepositoriesResponse,
    summary="Local analytics repository breakdown",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics repositories")
async def get_repositories(
    user: User = Depends(authenticate),
    filters: FilterParams = Depends(FilterParams),
    page: int | None = Query(None, ge=0),
    per_page: int | None = Query(None, ge=1, le=MAX_PER_PAGE),
    include_branches: bool = Query(False, description="Return per-branch rows instead of per-repo rows"),
    search: str | None = Query(None, description="Filter by repository name"),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    # Omitting both page and per_page means "all repositories, per-branch rows".
    fetch_all = page is None and per_page is None
    if fetch_all:
        include_branches = True
        resolved_page, resolved_per_page = 0, MAX_PER_PAGE
    else:
        resolved_page = page if page is not None else DEFAULT_PAGE
        resolved_per_page = per_page if per_page is not None else DEFAULT_PER_PAGE
    data = await _handler().get_repositories(f, resolved_page, resolved_per_page, include_branches, search)
    return _respond(
        {"data": data, "metadata": _metadata(start_ns, None, [], f.end_dt)}, LocalAnalyticsRepositoriesResponse
    )


@router.get(
    "/tools",
    response_model=LocalAnalyticsToolsResponse,
    summary="Local analytics tools and models",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics tools")
async def get_tools(
    user: User = Depends(authenticate),
    filters: FilterParams = Depends(FilterParams),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    data = await _handler().get_tools(f)
    return _respond({"data": data, "metadata": _metadata(start_ns, None, [], f.end_dt)}, LocalAnalyticsToolsResponse)


@router.get(
    "/activity",
    response_model=LocalAnalyticsActivityResponse,
    summary="Local analytics activity heatmap",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics activity")
async def get_activity(
    user: User = Depends(authenticate),
    filters: FilterParams = Depends(FilterParams),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    data = await _handler().get_activity(f)
    return _respond({"data": data, "metadata": _metadata(start_ns, None, [], f.end_dt)}, LocalAnalyticsActivityResponse)


@router.get(
    "/efficiency",
    response_model=LocalAnalyticsEfficiencyResponse,
    summary="Local analytics efficiency",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics efficiency")
async def get_efficiency(
    user: User = Depends(authenticate),
    filters: FilterParams = Depends(FilterParams),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    data, unpriced = await _handler().get_efficiency(f)
    return _respond(
        {"data": data, "metadata": _metadata(start_ns, None, unpriced, f.end_dt)}, LocalAnalyticsEfficiencyResponse
    )


@router.get(
    "/frameworks",
    response_model=LocalAnalyticsFrameworksResponse,
    summary="Available delivery frameworks",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics frameworks")
async def get_frameworks(user: User = Depends(authenticate)) -> JSONResponse:
    _ensure_enabled()
    return _respond({"data": get_framework_labels()}, LocalAnalyticsFrameworksResponse)


@router.get(
    "/sessions",
    response_model=LocalAnalyticsSessionsResponse,
    summary="Local analytics session list",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics sessions")
async def get_sessions(
    user: User = Depends(authenticate),
    filters: FilterParams = Depends(FilterParams),
    page: int | None = Query(None, ge=0),
    per_page: int | None = Query(None, ge=1, le=MAX_PER_PAGE),
    sort_by: Literal["start_time", "cost_usd", "ctx_per_call"] = Query("start_time"),
    search: str | None = Query(None, description="Filter by prompt, repository or branch"),
    framework: str | None = Query(None, description="Filter by delivery framework"),
    is_unattributed: bool = Query(False, description="Return only sessions with no attributed repository"),
    branch: str | None = Query(None, description="Filter sessions by branch name"),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    resolved_page = page if page is not None else DEFAULT_PAGE
    resolved_per_page = per_page if per_page is not None else DEFAULT_PER_PAGE
    data, unpriced, data_as_of = await _handler().get_sessions(
        f, resolved_page, resolved_per_page, sort_by, search, framework, is_unattributed, branch
    )
    return _respond(
        {"data": data, "metadata": _metadata(start_ns, data_as_of, unpriced, f.end_dt)}, LocalAnalyticsSessionsResponse
    )


@router.get(
    "/sessions/{trace_id}",
    response_model=LocalAnalyticsSessionDetailResponse,
    summary="Local analytics session detail",
    dependencies=[Depends(_admin_gate)],
)
@handle_errors("local analytics session detail")
async def get_session_detail(
    user: User = Depends(authenticate),
    trace_id: str = Path(..., description="Session id"),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    detail, unpriced = await _handler().get_session_detail(trace_id)
    if detail is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Session not found",
            details=f"No session found with trace_id={trace_id!r}.",
            help="Check that the trace_id is correct.",
        )

    # Project scoping: a project admin may only open sessions from projects they administer.
    ctx = AccessFilter(user).get_project_access_context()
    if not ctx.is_admin:
        visible = set(ctx.admin_projects)
        rows = await get_cli_analytics_storage().reader.get_session_detail_meta(trace_id)
        project = str(rows[0].get("project_name") or "") if rows else ""
        if project not in visible:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Session not found",
                details=f"No session found with trace_id={trace_id!r}.",
                help="Check that the trace_id is correct.",
            )

    metadata = {
        "timestamp": now_iso(),
        "data_as_of": detail.get("start_time") or "",
        "execution_time_ms": round((time.monotonic_ns() - start_ns) / 1_000_000, 2),
        "unpriced_models": unpriced,
    }
    return _respond({"data": detail, "metadata": metadata}, LocalAnalyticsSessionDetailResponse)
