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

"""Marketplace Management admin router.

All endpoints require admin authentication (authenticate + admin_access_only).

Business logic is delegated to:
  - audit_service.AUDIT_CACHE  (overview, facets, cleanup filtering, audit history)
  - queue_service.MarketplaceQueueService  (pending list + approve/reject/force-publish/withdraw)
  - llm_assessment_service.LLMAssessmentService  (/assess)
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from datetime import datetime
from typing import Annotated, Any, Callable, Coroutine, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from sqlmodel import select
from starlette.concurrency import run_in_threadpool

from codemie.clients.postgres import get_session
from codemie.configs.marketplace_management import get_marketplace_management_config
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    MarketplaceModerationEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.core.constants import SENSITIVE_VALUE_MASK
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.marketplace_management.gate_errors import critical_gate_blocked_error
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.dynamic_config import ConfigValueType
from codemie.rest_api.rate_limit import limiter
from codemie.rest_api.security.authentication import admin_access_only, authenticate
from codemie.rest_api.security.user import User
from codemie.service.customer_config_declarations import build_key
from codemie.service.dynamic_config_service import DynamicConfigService
from codemie.service.marketplace_management import audit_read_repository as audit_reads
from codemie.service.marketplace_management.audit_service import (
    AUDIT_CACHE,
    AuditAlreadyRunningError,
)
from codemie.service.marketplace_management.entity_adapter import get_adapter
from codemie.service.marketplace_management.llm_assessment_service import LLMAssessmentService
from codemie.service.marketplace_management.models import (
    AssessmentResponse,
    AuditConfigResponse,
    AuditHistoryResponse,
    AuditStatusResponse,
    CleanupOrderBy,
    CleanupResponse,
    ConfigUpdateResponse,
    EntityKind,
    FacetsResponse,
    ForcePublishResponse,
    GateResultFilter,
    GatesConfig,
    MarketplaceAssistantsResponse,
    MarketplaceAssistantsRow,
    MarketplaceAuditRun,
    MarketplaceAuditVerdict,
    MarketplaceStatusFilter,
    OverviewResponse,
    PendingOrderBy,
    PendingResponse,
    PendingRow,
    ReviewActionResponse,
    SimilarPair,
    SimilarityConfig,
    UsageTiersConfig,
    _STATUS_FILTER_MAP,
)
from codemie.rest_api.models.bulk_unpublish import BulkUnpublishFailure, BulkUnpublishRequest, BulkUnpublishResponse
from codemie.service.marketplace_management.queue_service import (
    MarketplaceConflictError,
    MarketplaceQueueService,
)
from codemie.service.marketplace_management.validator.models import (
    Bucket,
    CleanupReason,
    GateRule,
    UsageTier,
    VerdictKind,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error envelope (EXECUTION-PLAN 8.2 — F-24, F-75, S14, S15)
# ---------------------------------------------------------------------------


class SafeErrorRoute(APIRoute):
    """Route class that refuses to put internals in a 5xx body.

    The application-wide `handle_exceptions` middleware (`main.py`) formats an
    unhandled exception as ``"Internal Server Error. <br> Stacktrace: {...}"``
    carrying the last 2 KB of ``traceback.format_exc()`` — container paths,
    line numbers, internal symbol names and whatever the exception's own
    message happened to interpolate (a DSN, a row, a provider error). That is
    F-24 (`entity_kind=workflows` -> 500 with a full stack trace) and F-75
    (raw exception text forwarded from `/assess`) in one place.

    A route handler runs *inside* every middleware, so catching here means the
    traceback never reaches the formatter. The client gets a correlation id;
    the detail goes to the log at ERROR with the same id, which is what an
    operator actually needs to join the two.

    Exceptions that the application has registered a handler for — HTTP
    exceptions, request-validation errors, `ExtendedHTTPException`,
    `RateLimitExceeded`, the Elastic and domain-validation errors — are
    re-raised untouched: they are deliberate, already-shaped answers, and
    swallowing them into a 500 would be a worse bug than the one being fixed.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except Exception as exc:  # noqa: BLE001 - deliberate boundary
                if _has_dedicated_handler(request, exc):
                    raise
                correlation_id = _correlation_id(request)
                logger.exception(
                    "[marketplace_management] unhandled error on %s %s (correlation_id=%s)",
                    request.method,
                    request.url.path,
                    correlation_id,
                )
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content={
                        "error": {
                            "message": "Internal server error.",
                            "details": None,
                            "help": ("Quote the correlation id when reporting this; the detail is in the server log."),
                            "correlation_id": correlation_id,
                        },
                        "correlation_id": correlation_id,
                    },
                )

        return safe_handler


def _correlation_id(request: Request) -> str:
    """Reuse the request id the logging middleware already set, if there is one.

    `main.configure_logging` puts `X-Request-ID` (or a fresh uuid4) on
    `request.state.uuid` and feeds it to the log formatter, so quoting it back
    to the client makes the 500 joinable against the log line without inventing
    a second identifier.
    """
    existing = getattr(request.state, "uuid", None)
    return str(existing) if existing else str(uuid.uuid4())


def _has_dedicated_handler(request: Request, exc: Exception) -> bool:
    """Whether the app has registered a handler for this exception type.

    Deliberately ignores a bare `Exception` registration: that is the catch-all
    this route class exists to pre-empt, not a deliberate shape.
    """
    handlers = getattr(request.app, "exception_handlers", None) or {}
    return any(isinstance(key, type) and key is not Exception and isinstance(exc, key) for key in handlers)


# ---------------------------------------------------------------------------
# Rate limits (EXECUTION-PLAN 8.5 — F-37, R1)
# ---------------------------------------------------------------------------
#
# Two endpoints do unbounded work per call and had no brake at all: a full
# audit is one ES round trip and one insert per entity (282 of each today), and
# `/assess` is a billed LLM call. Combined with F-08 that made a stuck UI retry
# loop a one-click self-DoS on the threadpool every synchronous route draws
# from. Values are module constants rather than literals in the decorator so
# they can be read back (and tuned) without re-reading the decorator.

AUDIT_RUN_RATE_LIMIT = "5/minute"
ASSESS_RATE_LIMIT = "10/minute"
BULK_UNPUBLISH_RATE_LIMIT = "1/minute"


router = APIRouter(
    prefix="/v1/marketplace-management",
    tags=["Marketplace Management"],
    dependencies=[Depends(authenticate), Depends(admin_access_only)],
    route_class=SafeErrorRoute,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_queue_service = MarketplaceQueueService()


def _parse_entity_kind(entity_kind: str) -> EntityKind:
    """Accept singular OR plural forms (assistant/assistants, workflow/workflows)."""
    normalized = entity_kind.rstrip("s").upper()  # "assistants" → "ASSISTANT"
    try:
        return EntityKind(normalized)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown entity kind: {entity_kind!r}")


# Path segment shape: kinds appear in URLs as their plural noun so the
# routes read as `/entities/assistants/...`, `/entities/workflows/...`.
_KIND_TO_CONFIG_KEY: dict[EntityKind, str] = {
    EntityKind.ASSISTANT: "assistants",
    EntityKind.WORKFLOW: "workflows",
}


async def resolve_kind(kind: str) -> EntityKind:
    """FastAPI Path dependency: validates a `{kind}` path segment.

    Accepts the plural forms `assistants` or `workflows`. Anything else
    yields a 400 so a misspelt URL never quietly falls back to a default.

    `async def` on purpose: FastAPI dispatches a *synchronous* dependency into
    the shared threadpool exactly like a synchronous handler, so a sync
    dependency on every route in this router would re-open the F-08 hole even
    with the handlers converted. This does no IO, so it belongs on the loop.
    """
    return _parse_entity_kind(kind)


REASON_MIN_CHARS = 20
REASON_MAX_CHARS = 2000


def _validate_reason(reason: str, field: str = "reason") -> str:
    """The single length rule for every free-text justification in this feature.

    Rejection reasons and force-publish override notices are the same kind of
    field — a human explaining a decision that bypasses or overrides the normal
    flow — so they get the same bounds and the same status. This used to be
    duplicated as `assistant._validate_override_notice`, which had already
    drifted to a different status code: the identical rule answered 422 from
    `POST /assistants/{id}/publish` and 400 from the admin queue's
    Force-Publish (MR !4070 review comment #12). 422 is the one kept — it is
    what a body field failing validation means, and what the rest of this
    feature's validation errors already return.

    Returns the stripped value so callers cannot re-derive it differently.
    """
    stripped = (reason or "").strip()
    n = len(stripped)
    if n < REASON_MIN_CHARS or n > REASON_MAX_CHARS:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=f"{field} must be {REASON_MIN_CHARS}-{REASON_MAX_CHARS} chars (got {n}).",
            details={"field": field},
            help=f"Describe the decision in {REASON_MIN_CHARS}-{REASON_MAX_CHARS} characters.",
        )
    return stripped


IdempotencyKey = Annotated[
    Optional[str],
    Header(
        alias="Idempotency-Key",
        description=(
            "Replay token. Sending the same key twice returns the first call's stored "
            "response with `Idempotency-Replayed: true` instead of deciding again."
        ),
    ),
]


async def _decide(response: Response, call, *args, **kwargs) -> dict[str, Any]:
    """Run one queue decision and translate its two failure modes to HTTP.

    `ValueError` -> 404 (no such entity, unchanged behaviour).
    `MarketplaceConflictError` -> **409** carrying the row's current status,
    version and decider, which is the whole point of EXECUTION-PLAN 6.1: the
    admin who lost the race must be told who won it and what the entity is now,
    instead of receiving a 200 for a decision that was discarded (F-03).

    A replay sets `Idempotency-Replayed: true` and returns the body the winning
    call returned, byte for byte, because it is stored rather than recomputed
    (design-persistence.md §4.1).
    """
    try:
        outcome = await run_in_threadpool(call, *args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except MarketplaceConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.to_detail())
    if outcome.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return outcome.result


class MarketplaceAuthError(Exception):
    """Authenticated user carries no usable identity.

    Raised by `_actor_identity` — a domain-layer helper — so that the
    service-level code never imports HTTPException. The router boundary
    (`_require_actor`) translates this to HTTPException(401).
    """


def _actor_identity(user: User) -> str:
    """The audit-trail name of the caller, taken from the authenticated user.

    `mm_marketplace_event.actor` is free text and predates `decided_by_id`, so
    it needs a *string* rather than an id. Email first because that is what a
    human reading the trail recognises; username and id are fallbacks for
    identity providers that do not release an email claim. Never the request
    body -- that is the whole point of EXECUTION-PLAN 6.5.

    Raises `MarketplaceAuthError` rather than `HTTPException` so the helper
    stays outside the HTTP layer; callers at the router boundary catch and
    translate (CR-007).
    """
    for candidate in (
        getattr(user, "email", None),
        getattr(user, "username", None),
        getattr(user, "id", None),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    # `authenticate` cannot yield a user with no identifier at all; if it ever
    # did, an explicit marker beats a plausible-looking default.
    raise MarketplaceAuthError("Authenticated user carries no identity.")


def _require_actor(user: User) -> str:
    """Router-boundary wrapper: translates `MarketplaceAuthError` to HTTPException(401).

    Every endpoint that needs an actor string calls this instead of
    `_actor_identity` directly, keeping HTTP concerns out of the helper (CR-007).
    """
    try:
        return _actor_identity(user)
    except MarketplaceAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


def _last_run_meta(kind: EntityKind) -> tuple[Optional[str], bool]:
    """Read the newest MarketplaceAuditRun timestamp for *kind* — if any."""
    try:
        with get_session() as session:
            run = session.exec(
                select(MarketplaceAuditRun)
                .where(MarketplaceAuditRun.entity_kind == kind)
                .order_by(MarketplaceAuditRun.started_ts.desc())  # type: ignore[union-attr]
                .limit(1)
            ).first()
    except Exception:
        return None, False
    if run is None:
        return None, False
    # started_ts is a POSIX timestamp — return ISO for the UI.
    from datetime import datetime, timezone as _tz

    iso = datetime.fromtimestamp(run.started_ts, tz=_tz.utc).isoformat()
    return iso, True


def _reject_non_assistant(kind: EntityKind, op: str) -> None:
    """Guard for endpoints not yet wired for the workflow adapter.

    Nothing here supports the workflow kind yet: `WorkflowAdapter.list_records`
    raises `NotImplementedError`, so every read (overview, facets, cleanup) and
    every per-instance mutation (approve, reject, force-publish, withdraw,
    assess, detail) is assistants-only until the WorkflowAdapter lands. The
    reads used to omit this guard and surfaced that as a 500 (F-24); returning
    501 states the truth instead of crashing.
    """
    if kind != EntityKind.ASSISTANT:
        raise HTTPException(
            status_code=501,
            detail=f"{op!r} for entity kind {kind.value} is not implemented yet.",
        )


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


ALL_BUCKETS = "ALL"
"""Query value meaning "every bucket", as opposed to an absent parameter.

An absent `bucket` means the DELETE queue, so "all" has to be said out loud.
"""


def _blank_means_no_filter(value: Any) -> Any:
    """Map the "all buckets" sentinel to "no filter" before enum validation.

    The Cleanup table defaults to `bucket=DELETE` and clears it by sending
    `?bucket=ALL`. Typing the parameter as `Bucket` (which is the point of
    F-48: `?bucket=BOGUS` must 422 instead of quietly returning an empty table)
    would reject that sentinel too, so it is translated to `None` ahead of
    validation. Every other non-member value still 422s.

    The empty string is accepted for the same meaning and is NOT going away.
    It was the original spelling, and a URL is a shared surface: bookmarks,
    pasted links and every Overview filter link shipped so far send it. `ALL`
    exists because the empty one could not be told apart from an unset
    parameter — it read as a serialization slip, and during manual testing it
    was taken for one.
    """
    if value is None:
        return None
    return None if value == "" or value.upper() == ALL_BUCKETS else value


# NOTE: `Query()` lives *inside* the Annotated metadata rather than being the
# parameter default. With `bucket: BucketFilter = Query(default=...)` FastAPI
# builds the field from the bare annotation and the BeforeValidator never
# runs, so `?bucket=` 422s — verified, not assumed.
BucketFilter = Annotated[
    Optional[Bucket],
    BeforeValidator(_blank_means_no_filter),
    Query(
        description=("Surface bucket to list. Send ALL to clear the filter and list every bucket, VERIFIED included."),
    ),
]


# NOTE (EXECUTION-PLAN 6.5, F-14 / F-38 / S7): none of these bodies carries an
# `actor`. It used to: a free-text field defaulting to "admin"/"owner" that was
# written straight into `mm_marketplace_event.actor`, so the audit trail was
# authored by the very request being audited and any admin could sign a takedown
# as somebody else. The decider is now derived from `Depends(authenticate)` and
# from nothing else. A client that still sends `actor` is not rejected -- the
# field is simply ignored, which keeps old callers working while making the
# value inert.


ExpectedVersion = Annotated[
    Optional[int],
    Field(
        ge=0,
        description=(
            "The `marketplace_version` the reviewer looked at. The decision applies only if the row is still at "
            "this version, otherwise 409. Omit to decide against whatever version the row has now."
        ),
    ),
]


class ApproveIn(BaseModel):
    """The decider comes from the access token; `expected_version` pins the content that was reviewed."""

    expected_version: ExpectedVersion = None


class RejectIn(BaseModel):
    reason: str
    expected_version: ExpectedVersion = None


class ForcePublishIn(BaseModel):
    override_notice: str
    expected_version: ExpectedVersion = None


class WithdrawIn(BaseModel):
    """Withdraw carries no fields; the decider comes from the access token."""


# CR-001: constrain llm_model to safe model-ID characters and a reasonable
# length so that an arbitrary string cannot be injected into the LLM call.
# Model IDs in this deployment follow the pattern of their providers
# (e.g. "gpt-4.1", "claude-3-5-sonnet-20241022", "gemini-2.5-flash",
# "bedrock/anthropic.claude-3"); the regex covers all observed forms.
AllowedLlmModel = Annotated[
    str,
    Field(
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._\-/:]{0,127}$",
        max_length=128,
        description="Model ID from the configured LLM catalog.",
    ),
]


class AssessIn(BaseModel):
    llm_model: Optional[AllowedLlmModel] = None


class ConfigUpdate(BaseModel):
    """Partial config update for one entity kind's audit engine configuration.

    Each field corresponds to a top-level section of `AuditConfigResponse`.
    Typed models replace bare `dict` so that unknown keys are rejected (422)
    and integer thresholds cannot be zeroed out via the API (CR-002).
    """

    gates: Optional[GatesConfig] = None
    similarity: Optional[SimilarityConfig] = None
    usage_tiers: Optional[UsageTiersConfig] = None

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# GET /overview
# ---------------------------------------------------------------------------


@router.get("/entities/{kind}/overview", response_model=OverviewResponse)
async def get_overview(
    kind: EntityKind = Depends(resolve_kind),
) -> dict[str, Any]:
    """High-level marketplace health KPIs: bucket rollup, gate hits, discovery hints."""
    _reject_non_assistant(kind, "overview")
    return await run_in_threadpool(_overview_payload, kind)


OPTIONAL_RULES = frozenset({"description_not_equal_name"})


def _gate_hits(per_rule_hits: dict) -> dict:
    """Summarise gate failures as counts of ASSISTANTS, not of findings.

    `critical` and `optional` are rendered under the labels "Failing a critical
    check" and "Failing a minor check", beside "Pending review" and "Assistants
    in marketplace" — every neighbour is a count of assistants. Summing
    `len(ids)` across rules counted an assistant once per rule it failed, so on
    the reference corpus the tile read 55 where 45 assistants were affected: 7
    of them fail more than one critical rule.

    `per_rule` keeps counting distinct assistants per rule, which it always did
    — that is why the summary contradicted the table beneath it.
    """
    critical_ids: set = set()
    optional_ids: set = set()
    for rule, ids in per_rule_hits.items():
        (optional_ids if rule in OPTIONAL_RULES else critical_ids).update(ids)

    return {
        "critical": len(critical_ids),
        "optional": len(optional_ids),
        "per_rule": [
            {
                "rule": rule,
                "count": len(set(ids)),
                "severity": "OPTIONAL" if rule in OPTIONAL_RULES else "CRITICAL",
            }
            for rule, ids in sorted(per_rule_hits.items(), key=lambda kv: -len(set(kv[1])))
        ],
    }


def _bucket_counts(result, run_id: Optional[str]) -> dict[str, int]:
    """Bucket rollup for the Overview tiles and tab badges.

    `result.bucket_distribution` is a stale audit-run snapshot (unpublished
    assistants keep counting). The Cleanup queue tab badge sums all three
    buckets — the queue's "All" default — so each is replaced with the live
    per-bucket count `GET /cleanup` itself uses.
    """
    buckets = dict(result.bucket_distribution)
    if run_id is None:
        return buckets
    with get_session() as session:
        for live_bucket in (Bucket.VERIFIED, Bucket.COMMUNITY, Bucket.DELETE):
            buckets[live_bucket.value] = audit_reads.cleanup_total(
                session,
                run_id,
                bucket=live_bucket.value,
                reason=None,
                tier=None,
                verdict=None,
                mcp=None,
                category=None,
                cluster_id=None,
                q=None,
                rule=None,
            )
    return buckets


def _cluster_label(recs: list) -> str:
    """The two most common name tokens (longer than three characters) across a cluster."""
    words: Counter = Counter()
    for rec in recs:
        words.update(tok for tok in (rec.name or "").lower().split() if len(tok) > 3)
    return " ".join(w for w, _ in words.most_common(2)) or "cluster"


def _top_clusters(purpose_cluster_id: dict[str, int], lookup: dict) -> list[dict]:
    """The five largest duplicate clusters, by member count."""
    cluster_members: dict[int, list[str]] = {}
    for aid, cid in purpose_cluster_id.items():
        if cid >= 0:
            cluster_members.setdefault(cid, []).append(aid)
    top_clusters: list[dict] = []
    for cid, members in sorted(cluster_members.items(), key=lambda kv: -len(kv[1]))[:5]:
        recs = [lookup[m] for m in members if m in lookup]
        if len(members) < 2 or not recs:
            continue
        canonical = max(recs, key=lambda r: r.unique_users_count)
        top_clusters.append(
            {
                "cluster_id": cid,
                "size": len(members),
                "label": _cluster_label(recs),
                "canonical_id": canonical.id,
                "canonical_name": canonical.name,
                "canonical_users": canonical.unique_users_count,
                "total_users": sum(r.unique_users_count for r in recs),
            }
        )
    return top_clusters


def _top_facet_values(records: list, values_of) -> list[dict]:
    """The five most common values `values_of(record)` yields, with their total users."""
    counter: Counter = Counter(v for r in records for v in values_of(r))
    return [
        {
            "value": name,
            "count": count,
            "total_users": sum(r.unique_users_count for r in records if name in values_of(r)),
        }
        for name, count in counter.most_common(5)
    ]


def _overview_payload(kind: EntityKind) -> dict[str, Any]:
    """Blocking body of GET /overview — three DB/ES reads plus aggregation."""
    result = AUDIT_CACHE.get_or_run(kind)
    records = get_adapter(kind).list_records()
    lookup = {r.id: r for r in records}
    run_id = AUDIT_CACHE.latest_run_id(kind)

    # Gate hits summary
    gate_hits = _gate_hits(result.per_rule_hits)

    top_clusters = _top_clusters(result.purpose_cluster_id, lookup)
    top_mcps = _top_facet_values(records, lambda r: r.mcp_servers)
    top_categories = _top_facet_values(records, lambda r: r.categories)

    # Why each entity was flagged, in the CleanupReason vocabulary (F-06).
    # The Overview's "flagged by reason" breakdown and the Cleanup filter bar
    # both read this; without it every flagged assistant showed an empty
    # reason even though the verdicts carried one all along.
    reason_counter: Counter = Counter(v.reason.value for v in result.verdicts.values() if v.reason)
    # Verdict-kind and usage-tier rollups the UI contract already declares.
    verdict_counter: Counter = Counter(v.kind.value for v in result.verdicts.values() if v.kind)
    tier_counter: Counter = Counter(t.value for t in result.usage_tiers.values() if t)

    # Expose the last-audit timestamp so the UI can render "Last audit: X
    # ago" and, when there has never been an audit, prompt the admin to
    # click Run audit. The AuditResult is empty (total=0, empty distros)
    # when no audit has ever completed for this kind.
    last_run_at, has_run = _last_run_meta(kind)
    return {
        # `total` answers "how many assistants are in the marketplace right
        # now", so it is counted now (EXECUTION-PLAN 12.3). It used to be
        # `result.total` -- `mm_audit_run.total_records` of the newest servable
        # run -- which is a snapshot taken whenever the last audit happened.
        # Phase 11 watched the tile read 220 while Postgres held 219, and it
        # self-corrected on the next audit. Before the first audit ever runs the
        # same field reported 0 for a populated catalogue (219 rows in this
        # scope on the reference database), because the placeholder result has
        # no rows in it.
        #
        # `records` is already in hand for the MCP/category rollups below and is
        # the same catalogue the audit counts (`AssistantAdapter.list_records`),
        # so this costs no extra query. Same reasoning as the `pending` KPI a
        # few lines down: a question about the present is answered from the
        # present.
        "total": len(records),
        # Same query the Marketplace Assistants tab itself pages over, so the
        # badge can't disagree with what the tab renders (unlike `total`,
        # which is `is_global`-based and can diverge from it, F-20).
        "marketplace_assistants_total": _queue_service.list_marketplace_assistants(
            statuses=_STATUS_FILTER_MAP[MarketplaceStatusFilter.ALL], page=1, page_size=1
        )[1],
        # The snapshot is kept, not discarded, because `buckets`, `gate_hits`,
        # `cleanup_reasons`, `verdicts` and `usage_tiers` are all counted over
        # the population the run saw -- anything turning those into percentages
        # needs that population's size as the denominator, and the live count is
        # the wrong one. It is named for what it is and travels with
        # `last_run_at`, which is when it was true.
        "audited_total": result.total,
        "took_seconds": result.took_seconds,
        "buckets": _bucket_counts(result, run_id),
        "bucket_usage": dict(result.bucket_usage_retention),
        "latest_audit_run_id": run_id,
        "last_run_at": last_run_at,
        "has_ever_run": has_run,
        "gate_hits": gate_hits,
        "cleanup_reasons": dict(reason_counter),
        "verdicts": dict(verdict_counter),
        "usage_tiers": dict(tier_counter),
        # The review-queue KPI (EXECUTION-PLAN 7.3, F-59). It is read from the
        # queue itself rather than from the audit result: the audit is a
        # periodic snapshot, and "how many submissions are waiting right now" is
        # a question about the present. `_pending_kpi` uses the same
        # PENDING_REVIEW predicate `GET /pending` pages over, so the tile and
        # the page can never disagree.
        "pending": _pending_kpi(kind),
        "top_clusters": top_clusters,
        "top_mcps": top_mcps,
        "top_categories": top_categories,
    }


def _pending_kpi(kind: EntityKind) -> dict[str, Any]:
    """Queue size and wait times, or an honest zero block for a kind with no queue.

    The workflow queue is not wired yet (`_pending_payload` returns an empty
    page for it), so the KPI answers the same way rather than counting
    assistants under a workflow heading.
    """
    if kind != EntityKind.ASSISTANT:
        return {"count": 0, "waiting_over_7d": 0, "avg_wait_days": 0.0}
    return _queue_service.pending_stats()


# ---------------------------------------------------------------------------
# GET /facets
# ---------------------------------------------------------------------------


@router.get("/entities/{kind}/facets", response_model=FacetsResponse)
async def get_facets(
    kind: EntityKind = Depends(resolve_kind),
) -> dict[str, Any]:
    """Distinct MCP, category, and cluster facets for dropdown filters."""
    _reject_non_assistant(kind, "facets")
    return await run_in_threadpool(_facets_payload, kind)


def _facets_payload(kind: EntityKind) -> dict[str, Any]:
    """Blocking body of GET /facets — SQL `GROUP BY`, not a Python `Counter`.

    Four queries, all bounded by the number of *distinct values* rather than by
    the size of the catalogue (design-persistence.md §5.3):

    * one `UNION ALL` for every run-scoped vocabulary (reason / bucket / tier /
      verdict / rule / severity);
    * one for the duplicate clusters;
    * one each for the MCP and category facets, which are counted over the
      catalogue rather than the run so the dropdowns are populated before the
      first audit — exactly as the in-memory path behaved.

    The response shape is unchanged. What changed is that it survives a
    restart: the vocabularies used to come from an `AuditResult` that existed
    only in the process that produced it, so four of the seven facets emptied
    on every deploy (F-33).
    """
    with get_session() as session:
        run_id = audit_reads.latest_succeeded_run_id(session, kind.value)

        mcps = audit_reads.catalogue_facet(session, "mcps")
        categories = audit_reads.catalogue_facet(session, "categories")

        if run_id is None:
            # No audit has produced servable data yet. Everything derived from a
            # run is empty; the catalogue facets are not.
            return {
                "mcps": [{"value": f["value"], "count": f["count"]} for f in mcps],
                "categories": [{"value": f["value"], "count": f["count"]} for f in categories],
                "clusters": [],
                "reasons": [],
                "tiers": [],
                "buckets": [],
                "rules": [],
            }

        facets = audit_reads.run_facets(session, run_id)
        clusters = audit_reads.cluster_facets(session, run_id)
        severities = audit_reads.rule_severities(session, run_id)

    def _counts(name: str) -> list[dict]:
        return [{"value": value, "count": count} for value, count in facets.get(name, [])]

    return {
        "mcps": [{"value": f["value"], "count": f["count"]} for f in mcps],
        "categories": [{"value": f["value"], "count": f["count"]} for f in categories],
        "clusters": clusters,
        "reasons": _counts("reason"),
        "tiers": _counts("tier"),
        "buckets": _counts("bucket"),
        # A rule with no hits is not a filter an admin can usefully pick — it
        # would render an empty table with no explanation. `mm_audit_finding`
        # only holds rules that fired, so that property now holds by
        # construction rather than by an `if ids` in the loop.
        "rules": [
            {"value": rule, "count": count, "severity": severities.get(rule, "CRITICAL")}
            for rule, count in facets.get("rule", [])
        ],
    }


# ---------------------------------------------------------------------------
# GET /cleanup
# ---------------------------------------------------------------------------


@router.get("/entities/{kind}/cleanup", response_model=CleanupResponse)
async def get_cleanup(
    kind: EntityKind = Depends(resolve_kind),
    bucket: BucketFilter = Bucket.DELETE,
    reason: Optional[CleanupReason] = Query(default=None),
    tier: Optional[UsageTier] = Query(default=None),
    verdict: Optional[VerdictKind] = Query(default=None),
    mcp: Optional[str] = Query(default=None, max_length=100),
    category: Optional[str] = Query(default=None, max_length=100),
    cluster_id: Optional[int] = Query(default=None),
    q: Optional[str] = Query(default=None, max_length=200),
    rule: Optional[GateRule] = Query(
        default=None,
        description=(
            "Quality-check id from GET /overview's gate_hits.per_rule — returns "
            "only entities that failed that check."
        ),
    ),
    order_by_raw: str | None = Query(default=None, alias="order_by"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Filtered cleanup queue.

    Defaults to bucket=DELETE.  Pass bucket="" (or bucket=ALL) to clear the
    bucket filter — that lists every bucket, VERIFIED included. An explicit
    bucket=VERIFIED still narrows to just that one. The filter logic mirrors
    the PoC /api/marketplace/cleanup.

    `order_by` is accepted as a raw string and coerced to `CleanupOrderBy`
    safely: an unrecognised value silently falls back to the default ordering
    rather than returning a 422, so a FE client using a value the current
    server does not know yet degrades gracefully (EPMCDME-15333).
    """
    _reject_non_assistant(kind, "cleanup")
    try:
        order_by: CleanupOrderBy | None = CleanupOrderBy(order_by_raw) if order_by_raw else None
    except ValueError:
        order_by = None
    return await run_in_threadpool(
        _cleanup_payload,
        kind,
        bucket,
        reason,
        tier,
        verdict,
        mcp,
        category,
        cluster_id,
        q,
        rule,
        order_by,
        page,
        page_size,
    )


def _cleanup_payload(
    kind: EntityKind,
    bucket: Optional[Bucket],
    reason: Optional[CleanupReason],
    tier: Optional[UsageTier],
    verdict: Optional[VerdictKind],
    mcp: Optional[str],
    category: Optional[str],
    cluster_id: Optional[int],
    q: Optional[str],
    rule: Optional[GateRule],
    order_by: Optional[CleanupOrderBy],
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Blocking body of GET /cleanup — `WHERE` / `ORDER BY` / `LIMIT` in SQL.

    Replaces the loop-and-slice that asked the audit cache for every verdict and
    the adapter for every record before returning `page_size` of them (F-61).
    Three bounded statements now: `count(*)` for the total, one page of rows,
    and the rank-1 similarity row for the ids on that page.

    The run is pinned once, here, and threaded through all three, so a re-audit
    landing mid-request cannot mix two runs into one response
    (design-persistence.md §4.2 point 4).

    Response shape is unchanged, field for field, including the deprecated
    nested `verdict` object.
    """
    empty = {"total": 0, "page": page, "page_size": page_size, "items": []}
    with get_session() as session:
        run_id = audit_reads.latest_succeeded_run_id(session, kind.value)
        if run_id is None:
            return empty

        filters = {
            "bucket": bucket.value if bucket else None,
            "reason": reason.value if reason else None,
            "tier": tier.value if tier else None,
            "verdict": verdict.value if verdict else None,
            "mcp": mcp,
            "category": category,
            "cluster_id": cluster_id,
            "q": q,
            "rule": rule.value if rule else None,
        }
        total = audit_reads.cleanup_total(session, run_id, **filters)
        rows = audit_reads.cleanup_page(session, run_id, page=page, page_size=page_size, order_by=order_by, **filters)
        top_similar = audit_reads.top_similarity_for(session, run_id, [row["id"] for row in rows])

    items = [_cleanup_row(row, top_similar.get(row["id"])) for row in rows]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def _cleanup_row(row: dict, top_similar: Optional[dict]) -> dict[str, Any]:
    """One `mm_audit_verdict` row as the Cleanup table reads it.

    Every field the table renders is flat on the row, and each is emitted
    unconditionally -- as an explicit null when there is no value -- so a missing
    key never masquerades as a missing value.
    """
    verdict_kind = row["verdict_kind"]
    cleanup_reason = row["cleanup_reason"]
    return {
        "id": row["id"],
        "name": row["name"],
        "creator": row["creator"],
        "categories": row["categories"] or [],
        "unique_users_count": row["unique_users_count"],
        "unique_likes_count": row["unique_likes_count"],
        "usage_tier": row["usage_tier"],
        "bucket": row["bucket"],
        "bucket_reason": row["bucket_reason"],
        "verdict_kind": verdict_kind,
        "cleanup_reason": cleanup_reason,
        "canonical_id": row["canonical_id"],
        "canonical_name": row["canonical_name"],
        "canonical_users": row["canonical_users"],
        "critical_count": row["critical_count"] or 0,
        "optional_count": row["optional_count"] or 0,
        "detail": row["detail"],
        "top_similar": top_similar,
    }


# ---------------------------------------------------------------------------
# POST /audit/run
# ---------------------------------------------------------------------------


@router.post(
    "/entities/{kind}/audit/run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AuditStatusResponse,
)
@limiter.limit(lambda: AUDIT_RUN_RATE_LIMIT)
async def run_audit(
    request: Request,
    kind: EntityKind = Depends(resolve_kind),
) -> dict[str, Any]:
    """Start an audit pass in the background and return immediately.

    A full audit takes minutes (gate checks + similarity + persistence), so
    running it inline held the request open and starved the worker; clients
    could not tell a slow audit from a dead button. The run now happens on a
    worker thread and the caller polls ``GET /audit/status``.

    A run of one entity kind is a **deployment-wide** singleton, enforced by the
    partial unique index `uq_mm_audit_run_active` rather than by any in-process
    flag (design-persistence.md 3.3). A second call — a double-click, a second
    admin, or an admin served by a different pod mid-rollout — is refused with
    **409** carrying the in-flight run's id, so the caller can poll that run
    instead of believing it started one (F-17, F-49).

    Rate limited (F-37): a run is one ES round trip and one insert per entity.
    `request` is present because slowapi reads the client key off it; it is not
    used by the handler itself.
    """
    try:
        return AUDIT_CACHE.start_audit(kind)
    except AuditAlreadyRunningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail())


@router.get("/entities/{kind}/audit/status", response_model=AuditStatusResponse)
async def get_audit_status(
    kind: EntityKind = Depends(resolve_kind),
) -> dict[str, Any]:
    """Whether an audit is currently running, and how the last one ended.

    Server-held state, so it is correct for every admin and survives a page
    reload — the UI cannot know this on its own.
    """
    return AUDIT_CACHE.audit_status(kind)


# ---------------------------------------------------------------------------
# GET /audit/history
# ---------------------------------------------------------------------------


@router.get("/entities/{kind}/audit/history", response_model=AuditHistoryResponse)
async def get_audit_history(
    kind: EntityKind = Depends(resolve_kind),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """List recent persisted audit runs."""
    return await run_in_threadpool(_audit_history_payload, kind, limit)


def _audit_history_payload(kind: EntityKind, limit: int) -> dict[str, Any]:
    """Blocking body of GET /audit/history — one Postgres query."""
    with get_session() as session:
        runs = session.exec(
            select(MarketplaceAuditRun)
            .where(MarketplaceAuditRun.entity_kind == kind)
            .order_by(MarketplaceAuditRun.started_ts.desc())  # type: ignore[union-attr]
            .limit(limit)
        ).all()

    return {
        "runs": [
            {
                "id": run.id,
                "entity_kind": run.entity_kind.value if run.entity_kind else None,
                "started_ts": run.started_ts,
                "finished_ts": run.finished_ts,
                "total_records": run.total_records,
                "engine_took_seconds": run.engine_took_seconds,
            }
            for run in runs
        ]
    }


# ---------------------------------------------------------------------------
# GET /pending
# ---------------------------------------------------------------------------


@router.get("/entities/{kind}/pending", response_model=PendingResponse)
async def list_pending(
    kind: EntityKind = Depends(resolve_kind),
    page: int = Query(default=1, ge=1),
    # Capped at 100, not the router's generic 200 (see /cleanup, /audit/history):
    # unlike those, each row here runs a full synchronous gate recompute --
    # eight gates including a regex credential scan -- on one threadpool
    # worker (EPMCDME-14234), so the ceiling has to bound that per-request
    # cost, not just the SQL read. 100 matches this repo's other
    # expensive-per-row list endpoints (e.g. skill.py, mcp_config.py).
    page_size: int = Query(default=25, ge=1, le=100),
    category: Annotated[
        str | None,
        BeforeValidator(_blank_means_no_filter),
        Query(),
    ] = None,
    gate_result: Annotated[
        GateResultFilter | None,
        BeforeValidator(_blank_means_no_filter),
        Query(),
    ] = None,
    order_by: Annotated[
        PendingOrderBy | None,
        BeforeValidator(_blank_means_no_filter),
        Query(),
    ] = None,
) -> dict[str, Any]:
    """Paginated list of entities awaiting marketplace review."""
    return await run_in_threadpool(_pending_payload, kind, page, page_size, category, gate_result, order_by)


def _submitted_by(assistant: Any) -> Optional[str]:
    """Display name of whoever owns a pending row.

    `Assistant.created_by` is a `CreatedByUser`, not a string; handing the
    object to `PendingRow.submitted_by: Optional[str]` raised a
    ValidationError and 500ed the whole page (F-09).
    """
    creator = getattr(assistant, "created_by", None)
    return creator.name if creator else None


def _submitted_at(assistant: Any) -> Optional[float]:
    """POSIX timestamp for "when did this land in the queue".

    `marketplace_submitted_at` (migration `s1u2b3m4t5d6`) is the real answer and
    is what the queue orders by. It is NULL for every row published before the
    approval gate existed, so `update_date` stays as a fallback for those: it is
    a proxy — an edit while pending rewrites it — but it is better than a blank
    Submitted column on rows that predate the column itself.

    The wire field is named **`submitted_at`**, matching `PendingRow` and the
    published `openapi.json`. The admin UI reads `submitted_ts`
    (`MarketplacePendingPage.tsx:334`) and renders an em-dash for every row as a
    result; the frontend is the side that moves (EXECUTION-PLAN 5.6).
    """
    stamp = (
        getattr(assistant, "marketplace_submitted_at", None)
        or getattr(assistant, "update_date", None)
        or getattr(assistant, "date", None)
    )
    if stamp is None:
        return None
    try:
        return stamp.timestamp()
    except AttributeError:
        return None


def _apply_gate_result_filter(
    rows: list[dict],
    gate_result: GateResultFilter | None,
) -> list[dict]:
    """Post-filter a list of PendingRow dicts by live gate finding severity.

    `total` in PendingResponse is always the SQL count; this filter only
    narrows `items`. Operates on dicts (after PendingRow.model_dump()).
    """
    predicate = _GATE_RESULT_PREDICATES.get(gate_result) if gate_result is not None else None
    return [r for r in rows if predicate(r)] if predicate else rows


def _severity_count(row: dict, severity: str) -> int:
    return sum(1 for f in row["gate_findings"] if f.get("severity") == severity)


_GATE_RESULT_PREDICATES: dict[GateResultFilter, Callable[[dict], bool]] = {
    GateResultFilter.all_clear: lambda r: (
        not r.get("gate_recompute_failed", False)
        and _severity_count(r, "CRITICAL") == 0
        and _severity_count(r, "OPTIONAL") == 0
    ),
    GateResultFilter.has_critical: lambda r: _severity_count(r, "CRITICAL") > 0,
    GateResultFilter.has_optional_only: lambda r: (
        _severity_count(r, "CRITICAL") == 0 and _severity_count(r, "OPTIONAL") > 0
    ),
}


def _gate_config_or_none():
    """The gate `Config`, or None when it cannot be built.

    A bad admin PUT /config edit (e.g. an invalid credential_patterns regex)
    must not 500 the whole page -- same best-effort boundary as
    _review_gate_findings takes around the identical construction call.
    None means "skip recompute for every row this request", not a crash.
    """
    from codemie.service.marketplace_management.validator.config import Config as GateConfig

    try:
        return GateConfig.for_assistant_gates()
    except Exception as exc:  # noqa: BLE001 - a bad config must not blank the page
        logger.warning("[marketplace_management] gate config unavailable, pending rows show no findings: %s", exc)
        return None


def _live_gate_findings(a, cfg) -> tuple[list[dict], bool]:
    """(findings, recompute_failed) for one pending row."""
    if cfg is None:
        return [], True
    try:
        return [finding.to_dict() for finding in _compute_gate_findings(a, cfg)], False
    except Exception as exc:  # noqa: BLE001 - one bad row must not blank the page
        logger.warning("[marketplace_management] gate findings unavailable for %s: %s", a.id, exc)
        return [], True


def _pending_categories(a) -> list:
    if hasattr(a, "model_dump"):
        return a.model_dump(include={"categories"}).get("categories") or []
    return getattr(a, "categories", None) or []


def _pending_payload(
    kind: EntityKind,
    page: int,
    page_size: int,
    category: str | None = None,
    gate_result: GateResultFilter | None = None,
    order_by: PendingOrderBy | None = None,
) -> dict[str, Any]:
    """Blocking body of GET /pending — the queue read hits Postgres.

    Ordering and pagination both live in the query (`queue_service.list_pending`).
    This used to load the entire queue and slice it in Python, against a query
    with no `ORDER BY` — so the page boundaries were drawn across an undefined
    order (F-94).

    `category` and `order_by` are pushed into the SQL query; `gate_result` is a
    Python post-filter applied after live gate recompute. `total` always reflects
    the SQL count and is never recalculated after the gate filter (EPMCDME-14628).
    """
    if kind != EntityKind.ASSISTANT:
        # Workflow queue is not wired yet — return an explicit empty page so
        # the UI shows an empty-state instead of a 500.
        return {"total": 0, "page": page, "page_size": page_size, "items": []}
    assistants, total = _queue_service.list_pending(
        page=page,
        page_size=page_size,
        category=category,
        order_by=order_by or PendingOrderBy.submitted_asc,
    )

    # One extra query for the page: who submitted each row (EXECUTION-PLAN
    # 7.6/7.1) — still only recoverable from the SUBMIT decision, not from the
    # assistants table. `gate_findings` is no longer read from this stored
    # snapshot: it is recomputed live below (EPMCDME-14234), the same way the
    # detail endpoint computes it, so a post-submit edit can never leave the
    # queue disagreeing with `GET /entities/{kind}/{eid}` about the current
    # assistant.
    submissions = _queue_service.latest_submissions([a.id for a in assistants])

    cfg = _gate_config_or_none()
    rows: list[dict] = []
    for a in assistants:
        gate_findings, gate_recompute_failed = _live_gate_findings(a, cfg)
        rows.append(
            PendingRow(
                assistant_id=a.id,
                name=a.name,
                project=a.project if hasattr(a, "project") else None,
                marketplace_status=a.marketplace_status,
                submitted_at=_submitted_at(a),
                submitted_by=submissions.get(a.id, {}).get("submitted_by") or _submitted_by(a),
                gate_findings=gate_findings,
                gate_recompute_failed=gate_recompute_failed,
                categories=_pending_categories(a),
            ).model_dump()
        )

    rows = _apply_gate_result_filter(rows, gate_result)

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": rows,
    }


# ---------------------------------------------------------------------------
# GET /marketplace-assistants
# ---------------------------------------------------------------------------


@router.get("/entities/{kind}/marketplace-assistants", response_model=MarketplaceAssistantsResponse)
async def list_marketplace_assistants(
    kind: EntityKind = Depends(resolve_kind),
    status: Annotated[
        Optional[MarketplaceStatusFilter],
        BeforeValidator(_blank_means_no_filter),
        Query(),
    ] = None,
    page: int = Query(default=1, ge=1),
    page_size: Optional[int] = Query(default=None, ge=1, le=100),
) -> dict[str, Any]:
    """List of marketplace assistants, filterable by review status.

    Paginated when `page_size` is given; omit it to get every matching row in one response.
    """
    db_statuses = _STATUS_FILTER_MAP[status or MarketplaceStatusFilter.ALL]
    return await run_in_threadpool(_marketplace_assistants_payload, kind, db_statuses, page, page_size)


def _load_marketplace_assistants_gate_config() -> Any:
    """Gate config for recomputing findings; None when unavailable."""
    from codemie.service.marketplace_management.validator.config import Config as GateConfig

    try:
        return GateConfig.for_assistant_gates()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[marketplace_management] gate config unavailable for marketplace-assistants: %s", exc)
        return None


def _safe_gate_findings(assistant: Any, cfg: Any) -> tuple[list[dict], bool]:
    """Return (findings, recompute_failed); never raises."""
    if cfg is None:
        return [], True
    try:
        return [finding.to_dict() for finding in _compute_gate_findings(assistant, cfg)], False
    except Exception as exc:  # noqa: BLE001
        logger.warning("[marketplace_management] gate findings unavailable for %s: %s", assistant.id, exc)
        return [], True


def _assistant_categories(assistant: Any) -> list:
    if hasattr(assistant, "model_dump"):
        categories = assistant.model_dump(include={"categories"}).get("categories")
    else:
        categories = getattr(assistant, "categories", None)
    return categories or []


def _marketplace_assistant_row(assistant: Any, cfg: Any) -> dict:
    gate_findings, gate_recompute_failed = _safe_gate_findings(assistant, cfg)
    return MarketplaceAssistantsRow(
        assistant_id=assistant.id,
        name=assistant.name,
        project=assistant.project if hasattr(assistant, "project") else None,
        marketplace_status=assistant.marketplace_status,
        submitted_at=_submitted_at(assistant),
        submitted_by=_submitted_by(assistant),
        gate_findings=gate_findings,
        gate_recompute_failed=gate_recompute_failed,
        categories=_assistant_categories(assistant),
        is_verified=bool(assistant.is_verified),
        marketplace_decision_reason=assistant.marketplace_decision_reason,
    ).model_dump()


def _marketplace_assistants_payload(
    kind: EntityKind,
    db_statuses: list[str],
    page: int,
    page_size: Optional[int],
) -> dict[str, Any]:
    """Blocking body of GET /marketplace-assistants. `page_size=None` returns all rows."""
    if kind != EntityKind.ASSISTANT:
        return {"total": 0, "page": page, "page_size": page_size or 0, "items": []}
    assistants, total = _queue_service.list_marketplace_assistants(
        statuses=db_statuses,
        page=page,
        page_size=page_size,
    )

    cfg = _load_marketplace_assistants_gate_config()
    rows = [_marketplace_assistant_row(a, cfg) for a in assistants]

    return {
        "total": total,
        "page": page,
        "page_size": page_size if page_size is not None else len(rows),
        "items": rows,
    }


# ---------------------------------------------------------------------------
# POST /entities/{kind}/bulk-unpublish
# ---------------------------------------------------------------------------


@router.post("/entities/{kind}/bulk-unpublish", response_model=BulkUnpublishResponse)
@limiter.limit(lambda: BULK_UNPUBLISH_RATE_LIMIT)
async def bulk_unpublish(
    request: Request,
    body: BulkUnpublishRequest,
    kind: EntityKind = Depends(resolve_kind),
    user: User = Depends(authenticate),
) -> BulkUnpublishResponse:
    """Unpublish multiple entities in a single request.

    Partial semantics: every entity_id is attempted independently -- one
    entity_id raising never aborts the ones after it (MR !4070 review comment
    #17), and a de-index failure is reported as a failure rather than folded
    into the success count, since `withdraw()` commits the status change and
    only reports indexing as a best-effort side effect (CR-023 in
    queue_service.decide). The response documents which succeeded (unpublished
    count) and which failed (failed list with per-entity reasons). Callers
    should inspect the failed list to identify IDs that need a retry or manual
    intervention. FastAPI automatically validates entity_ids constraints (422
    for empty or >500 items).
    """
    _reject_non_assistant(kind, "bulk-unpublish")
    actor = _require_actor(user)
    succeeded = 0
    failures: list[BulkUnpublishFailure] = []
    for entity_id in body.entity_ids:
        try:
            outcome = await run_in_threadpool(
                _queue_service.withdraw,
                entity_id,
                actor,
                decided_by_id=getattr(user, "id", None),
                decided_by_email=getattr(user, "email", None),
            )
        except ValueError as exc:
            failures.append(BulkUnpublishFailure(entity_id=entity_id, reason=str(exc)))
            continue
        except MarketplaceConflictError as exc:
            failures.append(BulkUnpublishFailure(entity_id=entity_id, reason=exc.to_detail().get("detail", str(exc))))
            continue
        except Exception as exc:  # noqa: BLE001 - one bad entity must not 500 the rest of the batch
            logger.exception("bulk_unpublish: unexpected error withdrawing %r", entity_id)
            failures.append(BulkUnpublishFailure(entity_id=entity_id, reason=f"Unexpected error: {exc}"))
            continue

        indexing = outcome.result.get("indexing")
        if indexing is not None and not indexing.get("ok", True):
            failures.append(
                BulkUnpublishFailure(
                    entity_id=entity_id,
                    reason=f"Unpublished but de-indexing failed: {indexing.get('error') or indexing.get('state')}",
                )
            )
            continue
        succeeded += 1
    return BulkUnpublishResponse(unpublished=succeeded, failed=failures)


# ---------------------------------------------------------------------------
# POST /pending/{aid}/approve
# ---------------------------------------------------------------------------


@router.post("/entities/{kind}/pending/{eid}/approve", response_model=ReviewActionResponse)
async def approve_pending(
    eid: str,
    body: ApproveIn,
    response: Response,
    kind: EntityKind = Depends(resolve_kind),
    user: User = Depends(authenticate),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Approve a pending marketplace submission.

    The body is the compare-and-set in `queue_service.decide`; a second admin
    acting on the same version gets 409, not a silently discarded decision.
    """
    _reject_non_assistant(kind, "approve")
    # --- CRITICAL-findings gate (EPMCDME-14734, spec §5) ---
    # The gate check is passed as a callable into queue_service.approve(), which
    # forwards it to decide() where it runs immediately before the CAS UPDATE.
    # Running it inside decide() closes the TOCTOU window that exists when the
    # check and the commit are separated by a full HTTP round-trip (CR-004).
    # Any exception from the hook (404, 422, 503, ...) propagates through _decide
    # rather than being swallowed, so gate unavailability blocks approve instead
    # of silently bypassing it (CR-003).
    from codemie.service.marketplace_management.validator.config import Config as GateConfig
    from codemie.service.marketplace_management.validator.models import Severity

    # CR-001: the config load behind `for_assistant_gates` does a synchronous DB
    # read, so it is wrapped in run_in_threadpool rather than called inline —
    # this is the one async caller, which is why it cannot just call the
    # classmethod directly like the others do.
    _gate_cfg = await run_in_threadpool(GateConfig.for_assistant_gates)

    def _critical_gate_check(_aid: str) -> None:
        """Raises the shared structured 422 when CRITICAL gate findings are present."""
        _a = _load_assistant_or_404(_aid)
        # Without `detail` (the matched location and the pattern that fired): the
        # reviewer sees the same fields as the review drawer (`ReviewGateFinding`).
        _findings = [
            {"rule": f.rule, "severity": f.severity.value, "message": f.message}
            for f in _compute_gate_findings(_a, _gate_cfg)
        ]
        _critical = [f for f in _findings if f.get("severity") == Severity.CRITICAL.value]
        if _critical:
            raise critical_gate_blocked_error(
                _a.id,
                _findings,
                _critical,
                help="Fix each rule listed in details.gate_findings, or use force-publish to override.",
            )

    return await _decide(
        response,
        _queue_service.approve,
        assistant_id=eid,
        actor=_require_actor(user),
        gate_check=_critical_gate_check,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
        decided_by_id=getattr(user, "id", None),
        decided_by_email=getattr(user, "email", None),
    )


# ---------------------------------------------------------------------------
# POST /pending/{aid}/reject
# ---------------------------------------------------------------------------


@router.post("/entities/{kind}/pending/{eid}/reject", response_model=ReviewActionResponse)
async def reject_pending(
    eid: str,
    body: RejectIn,
    response: Response,
    kind: EntityKind = Depends(resolve_kind),
    user: User = Depends(authenticate),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Reject a pending submission: unpublish, unverify and de-index it.

    Rejection is a takedown, not a relabelling — see `queue_service` (F-04).
    """
    _reject_non_assistant(kind, "reject")
    reason = _validate_reason(body.reason)
    return await _decide(
        response,
        _queue_service.reject,
        assistant_id=eid,
        actor=_require_actor(user),
        reason=reason,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
        decided_by_id=getattr(user, "id", None),
        decided_by_email=getattr(user, "email", None),
    )


# ---------------------------------------------------------------------------
# POST /pending/{aid}/force-publish
# ---------------------------------------------------------------------------


@router.post("/entities/{kind}/pending/{eid}/force-publish", response_model=ForcePublishResponse)
async def force_publish_pending(
    eid: str,
    body: ForcePublishIn,
    response: Response,
    kind: EntityKind = Depends(resolve_kind),
    user: User = Depends(authenticate),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Force-publish an entity to the marketplace, bypassing review gates.

    The `override_notice` is now *persisted* on the decision row rather than
    validated, echoed and discarded (F-34), and
    `ck_mm_review_decision_override_notice` makes losing it again impossible.
    """
    _reject_non_assistant(kind, "force-publish")
    override_notice = _validate_reason(body.override_notice, field="override_notice")
    return await _decide(
        response,
        _queue_service.force_publish,
        assistant_id=eid,
        actor=_require_actor(user),
        override_notice=override_notice,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
        decided_by_id=getattr(user, "id", None),
        decided_by_email=getattr(user, "email", None),
    )


# ---------------------------------------------------------------------------
# POST /pending/{aid}/withdraw
# ---------------------------------------------------------------------------


@router.post("/entities/{kind}/pending/{eid}/withdraw", response_model=ReviewActionResponse)
async def withdraw_pending(
    eid: str,
    response: Response,
    body: WithdrawIn = Body(default=WithdrawIn()),
    kind: EntityKind = Depends(resolve_kind),
    user: User = Depends(authenticate),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Withdraw an entity from the marketplace: unpublish, unverify, de-index."""
    _reject_non_assistant(kind, "withdraw")
    return await _decide(
        response,
        _queue_service.withdraw,
        assistant_id=eid,
        actor=_require_actor(user),
        idempotency_key=idempotency_key,
        decided_by_id=getattr(user, "id", None),
        decided_by_email=getattr(user, "email", None),
    )


# ---------------------------------------------------------------------------
# POST /assess/{aid}
# ---------------------------------------------------------------------------


@router.post("/entities/{kind}/{eid}/assess", response_model=AssessmentResponse)
@limiter.limit(lambda: ASSESS_RATE_LIMIT)
async def assess_assistant(
    request: Request,
    eid: str,
    body: AssessIn = Body(default=AssessIn()),
    kind: EntityKind = Depends(resolve_kind),
    user: User = Depends(authenticate),
) -> AssessmentResponse:
    """Run an LLM quality assessment on a single entity.

    Rate limited (F-37): every call bills a provider, so an impatient click or
    a retry loop is a cost incident as well as a load one.
    """
    _reject_non_assistant(kind, "assess")
    return await run_in_threadpool(
        LLMAssessmentService.assess_assistant,
        assistant_id=eid,
        user=user,
        llm_model=body.llm_model,
    )


# NOTE: the two static `/entities/{kind}/config` routes MUST stay declared
# above the dynamic `/entities/{kind}/{eid}` route below. Starlette matches
# routes in declaration order, so a `{eid}` route declared first swallows
# `config` as an entity id and 404s (F-12). Test:
# test_marketplace_management_contract.py::test_get_config_returns_the_config.
# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------


@router.get("/entities/{kind}/config", response_model=AuditConfigResponse)
async def get_config(
    kind: EntityKind = Depends(resolve_kind),
) -> dict[str, Any]:
    """Return the audit engine configuration for a specific entity kind.

    Each kind carries its own gate thresholds, similarity settings, and
    weights — assistants and workflows may enforce different rules.
    """
    cfg = await run_in_threadpool(AUDIT_CACHE._config_for, kind)  # type: ignore[attr-defined]
    return cfg.to_dict()


# ---------------------------------------------------------------------------
# PUT /entities/{kind}/config
# ---------------------------------------------------------------------------


def _require_persistable_updates(updates: dict[str, Any]) -> None:
    """400 for an empty patch, or for a section that has no stored reader yet."""
    if not updates:
        raise HTTPException(status_code=400, detail="Empty config update.")
    if "usage_tiers" in updates:
        raise HTTPException(
            status_code=400,
            detail="usage_tiers cannot be updated through this endpoint: it is not persisted yet.",
        )


def _merged_config(current: dict[str, Any], updates: dict[str, Any]) -> Any:
    """Merge *updates* into the *current* config dict and re-validate the whole thing (400 on failure)."""
    from codemie.service.marketplace_management.validator.config import from_dict

    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            current[key].update(value)
        else:
            current[key] = value
    try:
        return from_dict(current)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _config_sections_to_persist(updates: dict[str, Any], new_cfg: Any) -> list[tuple[str, dict]]:
    """The camelCase payloads `_read_*_overrides()` expects, for the sections this patch touched."""
    sections = new_cfg.to_dict()
    persist: list[tuple[str, dict]] = []
    if "gates" in updates:
        gates = sections["gates"]
        persist.append(
            (
                "marketplaceCoreGates",
                {
                    "descriptionMin": gates["description_min"],
                    "systemPromptMin": gates["system_prompt_min"],
                    "substancePromptAltMin": gates["substance_prompt_alt_min"],
                },
            )
        )
    if "similarity" in updates:
        sim = sections["similarity"]
        persist.append(
            (
                "marketplaceSimilarity",
                {
                    "criticalThreshold": sim["thresholds"]["critical"],
                    "optionalThreshold": sim["thresholds"]["optional"],
                    "reportThreshold": sim["report_threshold"],
                    "shortlistSize": sim["shortlist_size"],
                },
            )
        )
    return persist


async def _persist_config_sections(sections: list[tuple[str, dict]], user: User) -> None:
    """Write each section to DynamicConfigService; 503 if any write fails (nothing is acknowledged)."""

    def _write_to_store() -> None:
        for component_id, payload in sections:
            DynamicConfigService.set(
                build_key(component_id),
                json.dumps(payload),
                ConfigValueType.STRING,
                "Updated via PUT /entities/{kind}/config",
                user,
            )

    try:
        await run_in_threadpool(_write_to_store)
    except Exception as exc:
        logger.error(
            "[marketplace_management] update_config: DynamicConfigService write failed: %s",
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail="Config could not be persisted; nothing was changed.",
        )


@router.put("/entities/{kind}/config", response_model=ConfigUpdateResponse)
async def update_config(
    body: ConfigUpdate,
    kind: EntityKind = Depends(resolve_kind),
    user: User = Depends(authenticate),
) -> dict[str, Any]:
    """Apply partial config updates to the kind-scoped audit config.

    Changes are persisted to DynamicConfigService (CR-012) so they survive a
    pod restart.  The sections written back to storage use the camelCase JSON
    keys that `_read_*_overrides()` already knows how to read:

    * ``gates``       → ``CUSTOMER_CONFIG__MARKETPLACE_CORE_GATES``
    * ``similarity``  → ``CUSTOMER_CONFIG__MARKETPLACE_SIMILARITY``

    ``usage_tiers`` (percentile thresholds) has no corresponding reader in
    `configs/marketplace_management.py` yet, so it cannot be persisted and the
    audit would never see it (the validator Config is rebuilt from storage on
    every call). It is refused with 400 rather than acknowledged and ignored.

    Each persisted section records a ``THRESHOLD_UPDATED`` activity event, the
    same way the declarations router does for these settings.

    Returns 503 when the DynamicConfigService write fails so that a silent
    200 cannot imply a durable update that was not actually stored.
    """
    updates = body.model_dump(exclude_none=True)
    _require_persistable_updates(updates)

    cfg = await run_in_threadpool(AUDIT_CACHE._config_for, kind)  # type: ignore[attr-defined]
    new_cfg = _merged_config(cfg.to_dict(), updates)

    # Write the sections the reader already knows about (usage_tiers was refused above).
    sections = _config_sections_to_persist(updates, new_cfg)
    if sections:
        await _persist_config_sections(sections, user)

    # The persisted values are what the audit reads (it rebuilds its Config from
    # storage on every call), so there is nothing to apply in memory. Invalidate
    # the cache and record the change like the declarations path does.
    from codemie.rest_api.routers.customer_config import _handle_threshold_hook

    for component_id, payload in sections:
        await _handle_threshold_hook(
            component_id, MarketplaceModerationEvent.THRESHOLD_UPDATED, getattr(user, "id", None), payload
        )
    return {"ok": True, "entity_kind": kind.value, "config": new_cfg.to_dict()}


# ---------------------------------------------------------------------------
# GET /assistants/{aid}
# ---------------------------------------------------------------------------


class ReviewPromptVariable(BaseModel):
    """One prompt variable as a reviewer may see it.

    Same three fields the canonical assistant detail returns, and the same
    masking rule: the default of an `is_sensitive` variable is replaced with
    `SENSITIVE_VALUE_MASK` (F-19). Nothing else about the variable is secret,
    and a reviewer needs the key and the description to judge the assistant.
    """

    key: str
    description: Optional[str] = None
    default_value: str = ""
    is_sensitive: bool = False


class ReviewGateFinding(BaseModel):
    """One deterministic gate finding, without its `detail` payload.

    `Finding.detail` carries the matched location and the pattern that fired;
    the rule, the severity and the (already redacted) message are what a
    reviewer acts on.
    """

    rule: str
    severity: str
    message: str


class PolishCheckResult(BaseModel):
    """Result of a single Polish quality gate check for one assistant (EPMCDME-15352)."""

    check: str  # "description" | "categories" | "icon" | "starters"
    passed: bool
    threshold: int | bool | None = None


class EntityReviewDetail(BaseModel):
    """What a moderator is shown about one entity — and nothing more.

    EXECUTION-PLAN 8.1 (F-18, F-19, S11, S12, R6). This route used to answer
    with `assistant.model_dump(mode="json")`: all 54 columns, to an audience of
    every admin **or maintainer**, for every assistant in the tenant. Three of
    those columns are the problem:

    * `toolkits` and `mcp_servers` embed `SettingsBase.credential_values` and
      raw MCP arguments — the very structures the publish gate scans *for
      inline credentials* (`gates.gate_no_inline_credentials`);
    * `system_prompt_history` is every superseded revision of the instructions;
    * `prompt_variables` carried unmasked sensitive defaults, which the
      canonical `/v1/assistants/id/{id}` route masks.

    So this is an allow-list, not a redaction pass: a column added to
    `Assistant` tomorrow is absent here until somebody decides it belongs.
    Capability is reported as *names and counts*, which is what "does this
    assistant do anything, and with what" actually needs.

    The current system prompt is not here either. A reviewer does need to read
    it, so it has its own endpoint (`GET /entities/{kind}/{eid}/prompt`) that
    logs who read which prompt — a separately auditable call, per 8.1.
    """

    id: Optional[str] = None
    name: str
    description: str
    categories: list[str] = []
    icon_url: Optional[str] = None
    conversation_starters: list[str] = []

    # --- submitter and timestamps -------------------------------------------
    submitted_by: Optional[str] = None
    creator: Optional[str] = None
    project: Optional[str] = None
    created_date: Optional[datetime] = None
    updated_date: Optional[datetime] = None
    marketplace_submitted_at: Optional[datetime] = None

    # --- review state --------------------------------------------------------
    marketplace_status: Optional[str] = None
    marketplace_version: int = 0
    is_global: Optional[bool] = None
    is_verified: bool = False
    marketplace_decided_at: Optional[datetime] = None
    marketplace_decided_by: Optional[str] = None
    marketplace_decision_reason: Optional[str] = None

    # --- adoption ------------------------------------------------------------
    unique_users_count: Optional[int] = None
    unique_likes_count: Optional[int] = None
    unique_dislikes_count: Optional[int] = None
    clone_count: Optional[int] = None

    # --- capability, by name and count only ----------------------------------
    toolkit_names: list[str] = []
    mcp_server_names: list[str] = []
    skill_count: int = 0
    nested_assistant_count: int = 0
    datasource_count: int = 0
    prompt_variables: list[ReviewPromptVariable] = []

    # --- the prompt, described rather than disclosed --------------------------
    system_prompt_length: int = 0
    system_prompt_revisions: int = 0

    # --- audit ---------------------------------------------------------------
    gate_findings: list[ReviewGateFinding] = []
    critical_count: int = 0
    optional_count: int = 0
    top_similar: Optional[SimilarPair] = None

    # --- override attribution (set only while the current state came from a force-publish) ---
    override_notice: Optional[str] = None
    overridden_by: Optional[str] = None
    bypassed_gate_findings: Optional[list[ReviewGateFinding]] = None
    bypassed_gate_findings_unavailable: bool = False

    # --- audit verdict (EPMCDME-15056) ----------------------------------------
    verdict_kind: Optional[str] = None
    cleanup_reason: Optional[str] = None
    bucket: Optional[str] = None
    decision_detail: Optional[str] = None

    # --- audit verdict extended fields (EPMCDME-15352) -----------------------
    bucket_reason: Optional[str] = None
    usage_tier: Optional[str] = None
    polish_check_results: Optional[list[PolishCheckResult]] = None
    similarity_failed: bool = False


class EntityPromptResponse(BaseModel):
    """The current system prompt, on its own separately audited endpoint."""

    id: Optional[str] = None
    name: str
    system_prompt: str
    system_prompt_revisions: int = 0


def _mask_prompt_variables(assistant: Any) -> list[ReviewPromptVariable]:
    """The canonical route's masking rule, applied to the moderation route.

    Mirrors `assistant._get_masked_prompt_variables`: replace the default of a
    sensitive variable with `SENSITIVE_VALUE_MASK`, leave every other variable
    alone. Reimplemented here rather than imported because importing the
    assistant router into this one would pull the whole chat stack in with it,
    and because masking everything would be a different bug, not a fix.
    """
    masked: list[ReviewPromptVariable] = []
    for variable in getattr(assistant, "prompt_variables", None) or []:
        default = getattr(variable, "default_value", "") or ""
        is_sensitive = bool(getattr(variable, "is_sensitive", False))
        masked.append(
            ReviewPromptVariable(
                key=getattr(variable, "key", ""),
                description=getattr(variable, "description", None),
                default_value=SENSITIVE_VALUE_MASK if (is_sensitive and default) else default,
                is_sensitive=is_sensitive,
            )
        )
    return masked


def _compute_gate_findings(assistant: Any, cfg: Any) -> list[Any]:
    """Pure per-assistant gate recompute; no try/except — callers decide the
    failure boundary (single best-effort for the detail endpoint, per-row
    isolation for the paginated list)."""
    from codemie.service.marketplace_management.entity_adapter import assistant_to_record
    from codemie.service.marketplace_management.validator import gates

    return gates.run_all(assistant_to_record(assistant), cfg)


def _review_gate_findings(assistant: Any) -> list[ReviewGateFinding]:
    """Run the eight deterministic gates for this one entity.

    Same two collaborators the batch auditor and the publish gate use
    (`assistant_to_record` + `gates.run_all`), so the drawer cannot disagree
    with either about the same assistant. Best effort: a reviewer losing the
    gate column is an inconvenience, losing the drawer is an outage.
    """
    try:
        from codemie.service.marketplace_management.validator.config import Config as GateConfig

        cfg = GateConfig.for_assistant_gates()
        return [
            ReviewGateFinding(
                rule=finding.rule,
                severity=finding.severity.value,
                message=finding.message,
            )
            for finding in _compute_gate_findings(assistant, cfg)
        ]
    except Exception as exc:  # noqa: BLE001 - enrichment, not the answer
        logger.warning("[marketplace_management] gate findings unavailable: %s", exc)
        return []


def _review_override(assistant: Any) -> dict[str, Any]:
    """Who force-published this entity, why, and which CRITICAL findings that waived.

    Empty unless the entity's current version was produced by a FORCE_PUBLISH, so a
    later approve or withdraw does not keep showing a stale override. Best effort,
    like the other enrichment on this drawer.
    """
    if getattr(assistant, "marketplace_status", None) != "APPROVED":
        return {}
    try:
        found = _queue_service.latest_force_publish(
            str(assistant.id), int(getattr(assistant, "marketplace_version", 0) or 0)
        )
    except Exception as exc:  # noqa: BLE001 - enrichment, not the answer
        logger.warning("[marketplace_management] override record unavailable for %s: %s", assistant.id, exc)
        return {}
    if not found:
        return {}
    found["bypassed_gate_findings"] = [ReviewGateFinding(**f) for f in found["bypassed_gate_findings"]]
    return found


def _review_similarity(aid: str) -> Optional[dict[str, Any]]:
    """The rank-1 similarity row for this entity from the last good run.

    Same read the Cleanup table uses, for a single id. Returns None when no run
    has succeeded yet or the row was not scored — and, deliberately, when the
    database is unreachable: similarity is context, not the answer.
    """
    try:
        with get_session() as session:
            run_id = audit_reads.latest_succeeded_run_id(session, EntityKind.ASSISTANT.value)
            if run_id is None:
                return None
            return audit_reads.top_similarity_for(session, run_id, [aid]).get(aid)
    except Exception as exc:  # noqa: BLE001 - enrichment, not the answer
        logger.warning("[marketplace_management] similarity unavailable for %s: %s", aid, exc)
        return None


def _similarity_failed(similar: Optional[dict[str, Any]], verdict_kind: Optional[str]) -> bool:
    """Whether the Similarity check of the review detail fails.

    Only a structural match at or above the critical threshold can fail it. A vector (ES) match has no
    severity and never does, and neither does a match that was itself flagged for cleanup while this
    entity was not: the weaker side of the pair is the one that goes.
    """
    if not similar or similar.get("b_name") is None or similar.get("reason") == "es_knn":
        return False
    if float(similar["combined"]) < get_marketplace_management_config("assistants").similarity.critical_threshold:
        return False
    matched_flagged = similar.get("b_verdict_kind") == "CANDIDATE_FOR_CLEANUP"
    return not (matched_flagged and verdict_kind != "CANDIDATE_FOR_CLEANUP")


def _review_verdict(assistant_id: str) -> dict[str, Any]:
    """The last serveable audit verdict for this entity (EPMCDME-15056).

    Returns a dict with verdict_kind, cleanup_reason, bucket and decision_detail
    keys, or an empty dict when no serveable verdict row exists for this entity.
    Both SUCCEEDED and PARTIAL runs are serveable — see design-persistence.md §7 F2.
    Returns empty (not None) on error so the caller can always unpack with **.
    """
    try:
        with get_session() as session:
            run_id = audit_reads.latest_succeeded_run_id(session, EntityKind.ASSISTANT.value)
            if run_id is None:
                return {}
            stmt = (
                select(MarketplaceAuditVerdict)
                .where(
                    MarketplaceAuditVerdict.audit_run_id == run_id,
                    MarketplaceAuditVerdict.assistant_id == assistant_id,
                )
                .limit(1)
            )
            row = session.exec(stmt).first()
            if row is None:
                return {}
            return {
                "verdict_kind": row.verdict_kind,
                "cleanup_reason": row.cleanup_reason,
                "bucket": row.bucket.value if row.bucket is not None else None,
                "decision_detail": row.detail,
                "bucket_reason": getattr(row, 'bucket_reason', None),
                "usage_tier": getattr(row, 'usage_tier', None),
            }
    except Exception as exc:  # noqa: BLE001 - enrichment, not the answer
        logger.warning("[marketplace_management] verdict unavailable for %s: %s", assistant_id, exc)
        return {}


def _review_polish_checks(aid: str) -> list[PolishCheckResult] | None:
    """Evaluate all four Polish quality checks for this assistant (EPMCDME-15352).

    Returns a list of four PolishCheckResult objects (one per check) regardless
    of which pass or fail. Returns None on any error so the caller treats it as
    unavailable without crashing. Follows the same best-effort enrichment
    pattern as _review_verdict() and _review_gate_findings().
    """
    try:
        from codemie.service.marketplace_management.validator.config import Config as GateConfig

        cfg = GateConfig.for_assistant_gates()
        pg = cfg.verification.polish_gates
        assistant = Assistant.get_by_id(aid)
        if assistant is None:
            return None
        return [
            PolishCheckResult(
                check="description",
                passed=len(assistant.description or "") >= pg.description_min,
                threshold=pg.description_min,
            ),
            PolishCheckResult(
                check="categories",
                passed=len(assistant.categories or []) >= pg.categories_min,
                threshold=pg.categories_min,
            ),
            PolishCheckResult(
                check="icon",
                passed=bool(assistant.icon_url) or not pg.require_icon,
                threshold=pg.require_icon,
            ),
            PolishCheckResult(
                check="starters",
                passed=len(assistant.conversation_starters or []) >= pg.conversation_starters_min,
                threshold=pg.conversation_starters_min,
            ),
        ]
    except Exception as exc:  # noqa: BLE001 - enrichment, not the answer
        logger.warning("[marketplace_management] polish checks unavailable for %s: %s", aid, exc)
        return None


# Statuses that mean an assistant has actually entered the marketplace review
# flow at least once. `DRAFT` is excluded on purpose: it is the column's
# default (`src/codemie/rest_api/models/assistant.py`), so every assistant in
# the tenant that was never submitted is also `DRAFT` -- scoping on "has a
# marketplace_status" would scope to nothing at all. Without this check these
# endpoints loaded *any* assistant by id regardless of marketplace membership
# (MR !4070 review comment #16): any admin/maintainer could read any private
# assistant's system prompt in any project.
_MARKETPLACE_ENGAGED_STATUSES = {"PENDING_REVIEW", "APPROVED", "REJECTED", "WITHDRAWN"}


def _load_assistant_or_404(aid: str) -> Assistant:
    try:
        assistant = Assistant.get_by_id(aid)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Assistant {aid!r} not found")
    if assistant is None:
        raise HTTPException(status_code=404, detail=f"Assistant {aid!r} not found")
    status = getattr(assistant, "marketplace_status", None)
    # A DRAFT row that is already public is a pre-migration assistant: it is in the
    # marketplace and in the Cleanup queue, so it must be reviewable (and removable).
    is_public_draft = status == "DRAFT" and bool(getattr(assistant, "is_global", False))
    if status not in _MARKETPLACE_ENGAGED_STATUSES and not is_public_draft:
        raise HTTPException(status_code=404, detail=f"Assistant {aid!r} not found")
    return assistant


@router.get(
    "/entities/{kind}/{eid}/prompt",
    response_model=EntityPromptResponse,
)
async def get_entity_prompt(
    eid: str,
    kind: EntityKind = Depends(resolve_kind),
    user: User = Depends(authenticate),
) -> EntityPromptResponse:
    """The current system prompt, as a separate and separately audited read.

    EXECUTION-PLAN 8.1 allows the full prompt "behind a separate, separately
    audited call" — this is it. Splitting it out means opening the review
    drawer no longer discloses the instructions of every assistant in the
    tenant as a side effect; reading them is now a deliberate act recorded as
    a real `activity_events` row naming the reader and the entity, not just a
    log line (MR !4070 review comment #16).

    Superseded revisions (`system_prompt_history`) are *not* returned: no
    review decision depends on what the prompt used to say, and the history is
    the largest share of what F-18 exposed. Only how many there are.
    """
    _reject_non_assistant(kind, "prompt")
    return await run_in_threadpool(_get_assistant_prompt, eid, _require_actor(user), getattr(user, "id", None))


def _get_assistant_prompt(aid: str, actor: str, actor_id: Optional[str]) -> EntityPromptResponse:
    assistant = _load_assistant_or_404(aid)
    logger.info(
        "[marketplace_management] system prompt of assistant %s read by %s",
        aid,
        actor,
    )
    with get_session() as session:
        activity_event_repository.insert(
            ActivityEventCreate(
                domain=ActivityDomain.MARKETPLACE,
                event_type=MarketplaceModerationEvent.ASSISTANT_PROMPT_VIEWED,
                entity_type=ActivityEntityType.ASSISTANT,
                entity_id=aid,
                actor_id=actor_id,
                attributes={"actor": actor},
            ),
            session,
        )
        session.commit()
    return EntityPromptResponse(
        id=assistant.id,
        name=assistant.name,
        system_prompt=assistant.system_prompt or "",
        system_prompt_revisions=len(getattr(assistant, "system_prompt_history", None) or []),
    )


@router.get(
    "/entities/{kind}/{eid}",
    response_model=EntityReviewDetail,
    response_model_by_alias=True,
)
async def get_entity_detail(
    eid: str,
    kind: EntityKind = Depends(resolve_kind),
) -> EntityReviewDetail:
    """Drawer detail view for a single entity — dispatches by kind."""
    _reject_non_assistant(kind, "detail")
    return await run_in_threadpool(_get_assistant_detail, eid)


def _get_assistant_detail(aid: str) -> EntityReviewDetail:
    """Review projection for a single assistant (EXECUTION-PLAN 8.1).

    Was `assistant.model_dump(mode="json")` — see `EntityReviewDetail` for what
    that disclosed and to whom. Everything below is named explicitly, so the
    set of fields this endpoint can ever return is the set written here.
    """
    assistant = _load_assistant_or_404(aid)

    findings = _review_gate_findings(assistant)
    similar = _review_similarity(aid)
    verdict = _review_verdict(aid)
    polish_checks = _review_polish_checks(aid)
    created_by = getattr(assistant, "created_by", None)
    override = _review_override(assistant)

    return EntityReviewDetail(
        id=assistant.id,
        name=assistant.name,
        description=assistant.description,
        categories=list(getattr(assistant, "categories", None) or []),
        icon_url=getattr(assistant, "icon_url", None),
        conversation_starters=list(getattr(assistant, "conversation_starters", None) or []),
        submitted_by=getattr(created_by, "name", None) if created_by else None,
        creator=getattr(assistant, "creator", None),
        project=getattr(assistant, "project", None),
        created_date=getattr(assistant, "created_date", None),
        updated_date=getattr(assistant, "updated_date", None),
        marketplace_submitted_at=getattr(assistant, "marketplace_submitted_at", None),
        marketplace_status=getattr(assistant, "marketplace_status", None),
        marketplace_version=getattr(assistant, "marketplace_version", 0) or 0,
        is_global=getattr(assistant, "is_global", None),
        is_verified=bool(getattr(assistant, "is_verified", False)),
        marketplace_decided_at=getattr(assistant, "marketplace_decided_at", None),
        marketplace_decided_by=getattr(assistant, "marketplace_decided_by", None),
        marketplace_decision_reason=getattr(assistant, "marketplace_decision_reason", None),
        unique_users_count=getattr(assistant, "unique_users_count", None),
        unique_likes_count=getattr(assistant, "unique_likes_count", None),
        unique_dislikes_count=getattr(assistant, "unique_dislikes_count", None),
        clone_count=getattr(assistant, "clone_count", None),
        toolkit_names=[toolkit.toolkit for toolkit in (getattr(assistant, "toolkits", None) or [])],
        mcp_server_names=[server.name for server in (getattr(assistant, "mcp_servers", None) or [])],
        skill_count=len(getattr(assistant, "skill_ids", None) or []),
        nested_assistant_count=len(getattr(assistant, "assistant_ids", None) or []),
        datasource_count=len(getattr(assistant, "context", None) or []),
        prompt_variables=_mask_prompt_variables(assistant),
        system_prompt_length=len(getattr(assistant, "system_prompt", None) or ""),
        system_prompt_revisions=len(getattr(assistant, "system_prompt_history", None) or []),
        gate_findings=findings,
        critical_count=sum(1 for f in findings if f.severity == "CRITICAL"),
        optional_count=sum(1 for f in findings if f.severity == "OPTIONAL"),
        override_notice=override.get("override_notice"),
        overridden_by=override.get("overridden_by"),
        bypassed_gate_findings=override.get("bypassed_gate_findings"),
        bypassed_gate_findings_unavailable=override.get("bypassed_gate_findings_unavailable", False),
        top_similar=SimilarPair(**similar) if similar else None,
        verdict_kind=verdict.get("verdict_kind"),
        cleanup_reason=verdict.get("cleanup_reason"),
        bucket=verdict.get("bucket"),
        decision_detail=verdict.get("decision_detail"),
        bucket_reason=verdict.get("bucket_reason"),
        usage_tier=verdict.get("usage_tier"),
        polish_check_results=polish_checks,
        similarity_failed=_similarity_failed(similar, verdict.get("verdict_kind")),
    )
