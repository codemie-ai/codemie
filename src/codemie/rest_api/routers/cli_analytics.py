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

  D1  Session universe = every session the window selects (in-period activity; a
      lifecycle-only session is not selected) that passes the viewer scope and filters,
      with or without cost rows (EPMCDME-15517): a selected session without a priced call
      is counted by Overview, Cost, Users, Repositories, Sessions, Activity and Efficiency alike.
      Money stays cost-based: spend sessions,
      cost and token sums, and the average cost per session come from cost_daily
      rows only, so a cost-less session adds no cost and is not in the average.

  D2  Repository key = the directory the session STARTED in, basename-normalised
      (v_session_dimensions.repository). `cwd` is not stable within a session, so
      anyLast() would be non-deterministic. `repo_remote` would be authoritative
      but covers only ~59% of sessions, so it is not used as the grouping key.

  D3  turns = interactions the user started that talk to the model: a typed prompt, or a
      slash command that made a model request; auto prompts are not turns.
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
from codemie.repository.cli_analytics.scope import ViewerScope
from codemie.repository.cli_analytics.hook_events import datetime_to_ns
from codemie.repository.cli_analytics.ports import (
    IngestResult,
    InvalidTelemetryPayloadError,
    OtlpSignal,
    PublicationRejectedError,
    CliAnalyticsStorageConfigError,
    StalePolicyError,
    TelemetryIngestError,
    TelemetryPayloadTooLargeError,
    TelemetryStorageUnavailableError,
    UnsupportedTelemetryContentTypeError,
)
from codemie.service.analytics.cli_classification import ORGANIZATION_ENCODING, policy_version
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
from codemie.rest_api.routers.cli_analytics_ingest_route import (
    IngestRoute,
    PublicationContract,
    PublicationContractRejected,
    check_publication_contract,
    corporate_organizations,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.cli_analytics_project_gate import build_ingest_gate
from codemie.service.analytics.delivery_framework import get_framework_labels
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler, now_iso
from codemie.service.analytics.handlers.user_identity_resolver import UserIdentityResolver
from codemie.service.analytics.time_parser import TimeParser
from codemie.utils.datetime_utils import to_utc

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
    TelemetryPayloadTooLargeError: 413,
    TelemetryStorageUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _handler() -> LocalAnalyticsHandler:
    """The handler over the configured storage's reader (the storage is built on first use)."""
    return LocalAnalyticsHandler(get_cli_analytics_storage().reader)


def viewer_access(user: User) -> tuple[bool, frozenset[str]]:
    """(unrestricted, admin projects) under the CLI privilege policy, the one `_admin_gate` and `/sessions` use.

    Only a super admin is unrestricted and only the user's own admin projects widen the scope. The shared
    `AccessFilter` also treats an auditor as unrestricted (and drops their admin projects), which would let an
    auditor read other users' shared sessions here although they cannot list them.
    """
    return bool(user.is_admin), frozenset(user.admin_project_names or [])


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


@router.get("/classification-policy", summary="CLI analytics classification policy")
async def classification_policy() -> dict[str, Any]:
    _ensure_enabled()
    orgs = corporate_organizations()
    # `organization_encodings` is optional and not part of the version: a client that sends the encoded organization
    # header must see it here first (the support of one instance does not prove all instances are upgraded).
    policy: dict[str, Any] = {
        "organizations": orgs,
        "version": policy_version(orgs),
        "organization_encodings": [ORGANIZATION_ENCODING],
    }
    if config.CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED:  # key omitted (not null) when off
        policy["publication_contracts"] = ["1"]
    return policy


def _ingest_route(request: Request, contract: PublicationContract | None = None) -> IngestRoute:
    return IngestRoute(request, build_ingest_gate, contract)  # the builder is passed in, so tests patch it here


def _stale_response(exc: StalePolicyError) -> Response:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT, content={"reason": "stale_policy", "policy_version": exc.policy_version}
    )


def _ingest_response(result: IngestResult, route: IngestRoute) -> Response:
    return Response(
        content=result.body, status_code=200, media_type=result.media_type, headers=route.echo_headers(result)
    )


def _publication_rejected_response(exc: PublicationRejectedError) -> Response:
    return JSONResponse(status_code=exc.status_code, content={"reason": exc.reason, "disposition": exc.disposition})


def _ingest_http_error(exc: TelemetryIngestError) -> HTTPException:
    return HTTPException(status_code=_INGEST_ERROR_STATUS.get(type(exc), 503), detail=str(exc))


async def _ingest_otlp(request: Request, signal: OtlpSignal) -> Response:
    _ensure_enabled()
    try:
        contract = check_publication_contract(request, request.state.user)
    except PublicationContractRejected as exc:
        return exc.response()
    body = await request.body()
    if len(body) > config.ANALYTICS_INGEST_MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=_ERR_BODY_TOO_LARGE)
    content_type = request.headers.get("content-type", _PROTOBUF_CONTENT_TYPE)
    route = _ingest_route(request, contract)
    try:
        result = await get_cli_analytics_storage().ingestor.ingest_otlp(
            signal,
            body,
            content_type,
            gate=route.gate,
            user_email=route.sender,
            evidence_session_id=route.evidence_session_id,
            stale_policy_version=route.stale_policy_version,
        )
    except StalePolicyError as exc:
        return _stale_response(exc)
    except PublicationRejectedError as exc:
        return _publication_rejected_response(exc)
    except TelemetryIngestError as exc:
        raise _ingest_http_error(exc) from exc
    except CliAnalyticsStorageConfigError as exc:  # retried by the client until the configuration is fixed
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_ERR_STORAGE_MISCONFIGURED) from exc
    return _ingest_response(result, route)


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
        subscription_type: str | None = Query(
            None, description="Comma-separated subscription types (codemie, corporate, personal, unknown, other)"
        ),
        project_origin: str | None = Query(
            None,
            description="Comma-separated project origins (personal, linked_folder, profile, "
            "registered_repository, default_project, legacy_project, none, other)",
        ),
    ) -> None:
        self.time_period = time_period
        self.start_date = to_utc(start_date) if start_date else None
        self.end_date = to_utc(end_date) if end_date else None
        self.users = users
        self.projects = projects
        self.repositories = repositories
        self.subscription_type = subscription_type
        self.project_origin = project_origin

    @staticmethod
    def _split(value: str | None) -> list[str] | None:
        if not value:
            return None
        items = [v.strip() for v in value.split(",") if v.strip()]
        return items or None

    async def resolve(self, user: User, project_unattributed: bool = False) -> LocalAnalyticsFilter:
        return await self._resolve(user, project_unattributed, own_only=False)

    async def resolve_sessions(
        self, user: User, project_unattributed: bool = False, personal_only: bool = False
    ) -> LocalAnalyticsFilter:
        """The `/sessions` variant: the viewer's own Personal sessions only (never widened) or today's scope.

        An ordinary non-admin is always own_only; an admin is own_only when `personal_only` is set, and then
        projects, users and is_project_unattributed are ignored. Other filters only narrow.
        """
        privileged = user.is_admin or user.is_applications_admin
        return await self._resolve(user, project_unattributed, own_only=personal_only or not privileged)

    async def _resolve(self, user: User, project_unattributed: bool, *, own_only: bool) -> LocalAnalyticsFilter:
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
        unrestricted, admin_projects = viewer_access(user)
        if own_only:
            # The scope's owner match is the restriction; no project/user selector may apply on top of it.
            emails, projects, project_unattributed = None, None, False
        elif not unrestricted:
            visible = set(admin_projects)
            projects = list(set(projects) & visible) if projects else list(visible)
            # No visible admin project: select nothing rather than all data.
            deny_all = not projects
        if project_unattributed and not own_only:
            # Sessions without a project are visible to super admins only.
            projects = None
            deny_all = deny_all or not unrestricted

        return LocalAnalyticsFilter(
            start_dt=start_dt,
            end_dt=end_dt,
            users=emails,
            projects=projects,
            repositories=self._split(self.repositories),
            deny_all=deny_all,
            project_unattributed=project_unattributed,
            subscription_types=self._split(self.subscription_type),
            project_origins=self._split(self.project_origin),
            # Shared aggregates and lists never include Personal sessions; only /sessions can be own_only.
            scope=ViewerScope(
                viewer_email=user.email,
                unrestricted=unrestricted,
                admin_projects=admin_projects,
                mode="own_only" if own_only else "exclude",
            ),
        )


def _metadata(
    start_ns: int, data_as_of: str | None, unpriced: list[str], catalog_source: str | None, fallback: datetime
) -> dict[str, Any]:
    return {
        "timestamp": now_iso(),
        "data_as_of": data_as_of or fallback.date().isoformat(),
        "execution_time_ms": round((time.monotonic_ns() - start_ns) / 1_000_000, 2),
        "unpriced_models": unpriced,
        "catalog_source": catalog_source,
        "cost_incomplete": bool(unpriced),
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
    try:
        contract = check_publication_contract(request, request.state.user)
    except PublicationContractRejected as exc:
        return exc.response()
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
    route = _ingest_route(request, contract)
    try:
        result = await get_cli_analytics_storage().ingestor.ingest_hook_events(
            events,
            route.sender,
            now_ns,
            gate=route.gate,
            evidence_session_id=route.evidence_session_id,
            stale_policy_version=route.stale_policy_version,
        )
    except StalePolicyError as exc:
        return _stale_response(exc)
    except PublicationRejectedError as exc:
        return _publication_rejected_response(exc)
    except TelemetryIngestError as exc:
        raise _ingest_http_error(exc) from exc
    except CliAnalyticsStorageConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_ERR_STORAGE_MISCONFIGURED) from exc
    return _ingest_response(result, route)


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
    data, pricer, data_as_of = await _handler().get_overview(f)
    metadata = _metadata(start_ns, data_as_of, pricer.unpriced, pricer.catalog_source, f.end_dt)
    return _respond({"data": data, "metadata": metadata}, LocalAnalyticsOverviewResponse)


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
    data, pricer = await _handler().get_cost(f)
    metadata = _metadata(start_ns, None, pricer.unpriced, pricer.catalog_source, f.end_dt)
    return _respond({"data": data, "metadata": metadata}, LocalAnalyticsCostResponse)


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
    data, pricer = await _handler().get_users(f, page, per_page)
    await _attach_user_ids(data["rows"])
    metadata = _metadata(start_ns, None, pricer.unpriced, pricer.catalog_source, f.end_dt)
    return _respond({"data": data, "metadata": metadata}, LocalAnalyticsUsersResponse)


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
    data, pricer = await _handler().get_users(f, None, None)
    data["total_count"] = None  # unpaginated by contract
    await _attach_user_ids(data["rows"])
    metadata = _metadata(start_ns, None, pricer.unpriced, pricer.catalog_source, f.end_dt)
    return _respond({"data": data, "metadata": metadata}, LocalAnalyticsUsersResponse)


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
    include_branches: bool = Query(False, description="Include nested branch rows within each repository row"),
    search: str | None = Query(None, description="Filter by repository name"),
) -> JSONResponse:
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve(user)
    # Omitting both page and per_page means "all repositories with nested branches".
    fetch_all = page is None and per_page is None
    if fetch_all:
        include_branches = True
        resolved_page, resolved_per_page = 0, MAX_PER_PAGE
    else:
        resolved_page = page if page is not None else DEFAULT_PAGE
        resolved_per_page = per_page if per_page is not None else DEFAULT_PER_PAGE
    data, pricer = await _handler().get_repositories(f, resolved_page, resolved_per_page, include_branches, search)
    metadata = _metadata(start_ns, None, pricer.unpriced, pricer.catalog_source, f.end_dt)
    return _respond({"data": data, "metadata": metadata}, LocalAnalyticsRepositoriesResponse)


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
    return _respond(
        {"data": data, "metadata": _metadata(start_ns, None, [], None, f.end_dt)}, LocalAnalyticsToolsResponse
    )


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
    return _respond(
        {"data": data, "metadata": _metadata(start_ns, None, [], None, f.end_dt)}, LocalAnalyticsActivityResponse
    )


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
    data, pricer = await _handler().get_efficiency(f)
    metadata = _metadata(start_ns, None, pricer.unpriced, pricer.catalog_source, f.end_dt)
    return _respond({"data": data, "metadata": metadata}, LocalAnalyticsEfficiencyResponse)


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
    is_project_unattributed: bool = Query(False, description="Return only sessions with no project"),
    personal_only: bool = Query(
        False, description="Return only the caller's own Personal sessions (always on for non-admins)"
    ),
) -> JSONResponse:
    # No _admin_gate: any authenticated user may list; a non-admin is forced to own Personal by the scope.
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    f = await filters.resolve_sessions(user, is_project_unattributed, personal_only)
    resolved_page = page if page is not None else DEFAULT_PAGE
    resolved_per_page = per_page if per_page is not None else DEFAULT_PER_PAGE
    data, pricer, data_as_of = await _handler().get_sessions(
        f, resolved_page, resolved_per_page, sort_by, search, framework, is_unattributed, branch
    )
    metadata = _metadata(start_ns, data_as_of, pricer.unpriced, pricer.catalog_source, f.end_dt)
    return _respond({"data": data, "metadata": metadata}, LocalAnalyticsSessionsResponse)


@router.get(
    "/sessions/{trace_id}",
    response_model=LocalAnalyticsSessionDetailResponse,
    summary="Local analytics session detail",
)
@handle_errors("local analytics session detail")
async def get_session_detail(
    user: User = Depends(authenticate),
    trace_id: str = Path(..., description="Session id"),
) -> JSONResponse:
    # No _admin_gate: the reader's detail predicate lets a non-admin see only their own Personal session.
    # An unavailable ClickHouse is indistinguishable from a missing trace here; preserve the 404 contract.
    _ensure_enabled()
    start_ns = time.monotonic_ns()
    # Authorization comes first (one cheap read, no existence leak): the owner of a Personal session,
    # or an admin allowed to see the non-Personal session's project. Nothing is fetched before it.
    unrestricted, admin_projects = viewer_access(user)
    scope = ViewerScope(
        viewer_email=user.email, unrestricted=unrestricted, admin_projects=admin_projects, mode="detail"
    )
    detail = None
    if await get_cli_analytics_storage().reader.authorize_session(trace_id, scope):
        detail, unpriced, catalog_source = await _handler().get_session_detail(trace_id, scope=scope)
    if detail is None:
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
        "catalog_source": catalog_source,
        "cost_incomplete": bool(unpriced),
    }
    return _respond({"data": detail, "metadata": metadata}, LocalAnalyticsSessionDetailResponse)
