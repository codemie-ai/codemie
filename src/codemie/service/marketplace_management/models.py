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

"""Storage rows and wire models for Marketplace Management.

The `*Response` models below are the API contract: every route in
`rest_api/routers/marketplace_management.py` declares one as its
`response_model`, so the payload shape is published in `openapi.json` and a
field the handler stops emitting becomes a test failure rather than an empty
column in the admin UI (F-47).

They model what the router returns **today**, field for field. The four DTOs
that used to live here (`OverviewResponse` with `total_assistants`/`by_status`
/`last_audit_ts`, `FacetsResponse` with `entity_kinds`, `BucketRollup`,
`VerdictRow`) described a payload no handler had ever produced and were wired
to nothing; they have been rewritten rather than reused, because reusing a
name whose fields are wrong is how a `response_model` silently deletes half a
response.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as SQLField, SQLModel

# Imported for its side effect on the shared SQLModel metadata, not for its
# name: `mm_review_decision.decided_by_id`, `mm_audit_run.triggered_by_id`,
# `mm_llm_assessment.requested_by_id` and `assistants.marketplace_decided_by`
# all declare a foreign key to `users.id`. SQLAlchemy resolves that target
# lazily, at flush time, so if nothing has imported the table the first INSERT
# fails with `NoReferencedTableError` rather than at import.
from codemie.rest_api.models.user_management import UserDB as _UserDB  # noqa: F401
from codemie.service.marketplace_management.validator.models import (
    CleanupReason,
    GateRule,
    Severity,
    UsageTier,
    VerdictKind,
)


# ============================================================================
# Enums
# ============================================================================


_FK_USERS_ID = "users.id"
_FK_AUDIT_RUN_ID = "mm_audit_run.id"
_ON_DELETE_SET_NULL = "SET NULL"


class MarketplaceStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class ReviewDecision(str, Enum):
    """The five edges of the review state machine (design-persistence.md §3.3).

    `ck_mm_review_decision_decision` pins the same five values in the database,
    so a decision this enum does not name cannot be written even by a caller
    that bypasses the service.
    """

    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    FORCE_PUBLISH = "FORCE_PUBLISH"
    WITHDRAW = "WITHDRAW"
    RE_SUBMIT = "RE_SUBMIT"


class MarketplaceStatusFilter(str, Enum):
    ALL = "ALL"
    VERIFIED = "VERIFIED"  # → APPROVED
    PENDING = "PENDING"  # → PENDING_REVIEW
    REJECTED = "REJECTED"  # → REJECTED


_STATUS_FILTER_MAP: dict[MarketplaceStatusFilter, list[str]] = {
    MarketplaceStatusFilter.ALL: ["APPROVED", "PENDING_REVIEW", "REJECTED"],
    MarketplaceStatusFilter.VERIFIED: ["APPROVED"],
    MarketplaceStatusFilter.PENDING: ["PENDING_REVIEW"],
    MarketplaceStatusFilter.REJECTED: ["REJECTED"],
}


class Bucket(str, Enum):
    VERIFIED = "VERIFIED"
    COMMUNITY = "COMMUNITY"
    DELETE = "DELETE"


class EntityKind(str, Enum):
    ASSISTANT = "ASSISTANT"
    WORKFLOW = "WORKFLOW"


# Event type strings for threshold changes
THRESHOLD_UPDATED = "THRESHOLD_UPDATED"
THRESHOLD_RESET = "THRESHOLD_RESET"


# ============================================================================
# SQLModel table classes
# ============================================================================


class RunStatus(str, Enum):
    """Lifecycle of one audit run — `ck_mm_audit_run_status` in `n1a2u3d4i5t6`.

    `PENDING`/`RUNNING` are the two non-terminal values, and the partial unique
    index `uq_mm_audit_run_active` makes "at most one active run per entity
    kind" a database invariant over exactly this pair.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Statuses whose verdicts a read may be served from.
#
# `PARTIAL` belongs here. A run degrades to PARTIAL when the *similarity* half
# of the pipeline could not run (design-persistence.md §7 F2) — the eight
# deterministic gates still executed against every record and their verdicts
# are exactly as correct as a SUCCEEDED run's. Excluding PARTIAL would mean
# that labelling a degradation also blanked the Overview, which is the silent
# downgrade §7 F2 exists to forbid, wearing a different hat.
SERVEABLE_RUN_STATUSES: tuple[str, ...] = (RunStatus.SUCCEEDED.value, RunStatus.PARTIAL.value)


class RunTrigger(str, Enum):
    """What caused a run — `ck_mm_audit_run_trigger` in `n1a2u3d4i5t6`."""

    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    BACKFILL = "BACKFILL"


# The engine identity stamped onto every run. Bumped when a gate, a threshold
# or the verdict aggregation changes shape, so a stored verdict can be told
# apart from one the current code would produce.
AUDIT_ENGINE_VERSION = "2026.08.22"

# Runs written by this module carry the v2 shape: findings and similarity rows
# exist as their own tables. `n1a2u3d4i5t6` stamps every pre-existing run as
# schema_version=1 and the read path refuses to serve an Overview from those,
# because they carry no findings at all (design-persistence.md §6.2).
AUDIT_SCHEMA_VERSION = 2


class MarketplaceAuditRun(SQLModel, table=True):
    """Records a single audit engine execution pass over the assistant catalogue.

    Reconciled with migration `n1a2u3d4i5t6` (EXECUTION-PLAN 5.3). Before this,
    the class declared nine of the twenty-seven columns the table actually has,
    and the nine did not include `started_at TIMESTAMPTZ NOT NULL` — so every
    INSERT died with `NotNullViolation`, `run_audit` swallowed it, and the audit
    feature persisted nothing at all while reporting success.

    **The legacy float epochs `started_ts` / `finished_ts` are kept as a
    deliberate dual write, not retired.** Three reasons, in order of weight:

    1. `started_ts` is still `NOT NULL` with no server default (verified against
       the live database), so an INSERT that omits it fails. Retiring the column
       is a schema change — a migration that drops it — and this step changes no
       schema.
    2. The 94 historical runs carry only the floats for their timing, and
       `n1a2u3d4i5t6` derived `started_at` *from* them. Dropping the source of a
       backfill in the same release that performs it removes the ability to
       re-derive if the backfill is found wrong.
    3. `GET /audit/history` publishes `started_ts` / `finished_ts` as floats in
       its response model (`AuditRunRow`), and Phase 4 pinned that contract.

    The timestamptz columns are the ones every new query orders and filters by;
    the floats are written alongside and read only by the legacy history route.
    Retiring them belongs in a later migration once `AuditRunRow` moves to ISO
    timestamps.
    """

    __tablename__ = "mm_audit_run"

    id: str = SQLField(primary_key=True)
    entity_kind: EntityKind
    schema_version: int = SQLField(
        default=AUDIT_SCHEMA_VERSION, sa_column=Column(SmallInteger, nullable=False, server_default="2")
    )
    status: str = SQLField(
        default=RunStatus.RUNNING.value,
        sa_column=Column(String(16), nullable=False, server_default="SUCCEEDED"),
    )
    trigger: str = SQLField(
        default=RunTrigger.MANUAL.value,
        sa_column=Column(String(16), nullable=False, server_default="MANUAL"),
    )
    triggered_by_id: Optional[str] = SQLField(
        default=None,
        sa_column=Column(String(36), ForeignKey(_FK_USERS_ID, ondelete=_ON_DELETE_SET_NULL), nullable=True),
    )
    runner_id: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))

    # The timestamptz lifecycle. `started_at` is NOT NULL in the database; a
    # default here would hide a caller that forgot it, so it is required.
    started_at: datetime = SQLField(sa_column=Column(TIMESTAMP(timezone=True), nullable=False))
    finished_at: Optional[datetime] = SQLField(default=None, sa_column=Column(TIMESTAMP(timezone=True), nullable=True))
    heartbeat_at: Optional[datetime] = SQLField(default=None, sa_column=Column(TIMESTAMP(timezone=True), nullable=True))

    records_processed: int = SQLField(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    records_failed: int = SQLField(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))

    engine_version: Optional[str] = SQLField(default=None, sa_column=Column(String(32), nullable=True))
    config_hash: Optional[str] = SQLField(default=None, sa_column=Column(String(64), nullable=True))
    similarity_backend: Optional[str] = SQLField(default=None, sa_column=Column(String(32), nullable=True))
    es_index_name: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))
    es_doc_count: Optional[int] = SQLField(default=None, sa_column=Column(Integer, nullable=True))
    error: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))

    config_snapshot: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSONB, nullable=True))
    aggregates: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSONB, nullable=True))

    # --- legacy, dual-written; see the class docstring -----------------------
    started_ts: float
    finished_ts: Optional[float] = None
    total_records: Optional[int] = None
    engine_took_seconds: Optional[float] = None
    source_snapshot: Optional[str] = None
    config_snapshot_json: Optional[str] = None
    # Run-level aggregates that cannot be derived from the per-verdict rows:
    # `per_rule_hits` (quality check -> failing entity ids) and
    # `bucket_usage_retention` (bucket -> % of marketplace usage). Without
    # these the Overview served from the DB reported "No failed checks",
    # "0 critical" and "Usage data not available" even though the audit had
    # computed all three.
    aggregates_json: Optional[str] = None


class MarketplaceAuditVerdict(SQLModel, table=True):
    """Per-assistant outcome produced by one audit run (composite PK)."""

    __tablename__ = "mm_audit_verdict"
    __table_args__ = (
        Index("ix_mm_audit_verdict_run_bucket", "audit_run_id", "bucket"),
        Index("ix_mm_audit_verdict_assistant", "assistant_id"),
    )

    audit_run_id: str = SQLField(primary_key=True, foreign_key=_FK_AUDIT_RUN_ID)
    assistant_id: str = SQLField(primary_key=True)
    bucket: Optional[Bucket] = None
    bucket_reason: Optional[str] = None
    verdict_kind: Optional[str] = None
    cleanup_reason: Optional[str] = None
    usage_tier: Optional[str] = None
    users_count: Optional[int] = None
    canonical_id: Optional[str] = None
    canonical_name: Optional[str] = None
    match_score: Optional[float] = None
    detail: Optional[str] = None
    # Added by `n1a2u3d4i5t6`. `critical_count` / `optional_count` are
    # denormalised off `mm_audit_finding` so the Cleanup table can render the
    # per-row counts without a second aggregate per page; `canonical_users` and
    # `purpose_cluster_id` were carried on the in-memory `Verdict` and lost on
    # every restart.
    critical_count: int = SQLField(default=0, sa_column=Column(SmallInteger, nullable=False, server_default="0"))
    optional_count: int = SQLField(default=0, sa_column=Column(SmallInteger, nullable=False, server_default="0"))
    canonical_users: Optional[int] = None
    purpose_cluster_id: Optional[int] = None


class MarketplaceAuditFinding(SQLModel, table=True):
    """One failed quality check, for one entity, in one run.

    The row the Overview's "Failed quality checks" table and the Cleanup
    `?rule=` filter are served from. Until `n1a2u3d4i5t6` this existed only as a
    Python `GateResult` that the process discarded on exit (F-30).
    """

    __tablename__ = "mm_audit_finding"
    __table_args__ = (
        Index("ix_mm_audit_finding_run_rule", "audit_run_id", "rule"),
        Index("ix_mm_audit_finding_run_severity", "audit_run_id", "severity"),
        Index("ix_mm_audit_finding_assistant", "assistant_id"),
        CheckConstraint("severity IN ('CRITICAL', 'OPTIONAL')", name="ck_mm_audit_finding_severity"),
    )

    audit_run_id: str = SQLField(
        sa_column=Column(Text, ForeignKey(_FK_AUDIT_RUN_ID, ondelete="CASCADE"), primary_key=True, nullable=False)
    )
    assistant_id: str = SQLField(primary_key=True)
    rule: str = SQLField(sa_column=Column(String(64), primary_key=True, nullable=False))
    severity: str = SQLField(sa_column=Column(String(16), nullable=False))
    message: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))
    detail: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSONB, nullable=True))


class MarketplaceAuditSimilarity(SQLModel, table=True):
    """One scored near-neighbour of an entity, as recorded evidence.

    A duplicate verdict used to collapse to a single `match_score` float, so
    "why is this a duplicate, and of what" was unanswerable after the fact.
    `source` records which backend produced the pair; ES results enter Postgres
    here as evidence and never as live state (design-persistence.md §5.2).
    """

    __tablename__ = "mm_audit_similarity"
    __table_args__ = (
        Index("ix_mm_audit_similarity_run_assistant_rank", "audit_run_id", "assistant_id", "rank"),
        CheckConstraint("combined >= 0.0 AND combined <= 1.0", name="ck_mm_audit_similarity_range"),
        CheckConstraint(
            "severity IS NULL OR severity IN ('CRITICAL', 'OPTIONAL')",
            name="ck_mm_audit_similarity_sev",
        ),
        CheckConstraint(
            "source IN ('CLONE', 'ES', 'EXACT_NAME', 'LEXICAL', 'VECTOR')",
            name="ck_mm_audit_similarity_source",
        ),
    )

    audit_run_id: str = SQLField(
        sa_column=Column(Text, ForeignKey(_FK_AUDIT_RUN_ID, ondelete="CASCADE"), primary_key=True, nullable=False)
    )
    assistant_id: str = SQLField(primary_key=True)
    other_id: str = SQLField(primary_key=True)
    other_name: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))
    rank: int = SQLField(sa_column=Column(SmallInteger, nullable=False))
    combined: float = SQLField(sa_column=Column(Float, nullable=False))
    severity: Optional[str] = SQLField(default=None, sa_column=Column(String(16), nullable=True))
    reason: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))
    source: str = SQLField(default="ES", sa_column=Column(String(16), nullable=False, server_default="ES"))
    signals: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSONB, nullable=True))


class MarketplaceLLMAssessment(SQLModel, table=True):
    """One Assess-with-AI result, with the model and prompt that produced it.

    `uq_mm_llm_assessment_cache` on `(assistant_id, prompt_hash, model,
    input_hash) WHERE status='SUCCEEDED'` turns a second admin's click into a
    cache hit instead of a second paid call (design-persistence.md §4.3).
    Failures are stored with `status='FAILED'` and excluded from that index, so
    a failure never poisons the cache.
    """

    __tablename__ = "mm_llm_assessment"
    __table_args__ = (
        Index(
            "uq_mm_llm_assessment_cache",
            "assistant_id",
            "prompt_hash",
            "model",
            "input_hash",
            unique=True,
            postgresql_where=text("status = 'SUCCEEDED'"),
        ),
        Index("ix_mm_llm_assessment_assistant_created", "assistant_id", "created_at"),
        CheckConstraint("status IN ('SUCCEEDED', 'FAILED')", name="ck_mm_llm_assessment_status"),
        CheckConstraint(
            "overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)",
            name="ck_mm_llm_assessment_score",
        ),
        CheckConstraint(
            "verdict_recommendation IS NULL OR verdict_recommendation IN ('VERIFIED', 'COMMUNITY', 'DELETE')",
            name="ck_mm_llm_assessment_bucket",
        ),
    )

    id: str = SQLField(sa_column=Column(String(36), primary_key=True, nullable=False))
    entity_kind: str = SQLField(
        default=EntityKind.ASSISTANT.value,
        sa_column=Column(String(16), nullable=False, server_default="ASSISTANT"),
    )
    assistant_id: str
    audit_run_id: Optional[str] = SQLField(
        default=None,
        sa_column=Column(Text, ForeignKey(_FK_AUDIT_RUN_ID, ondelete=_ON_DELETE_SET_NULL), nullable=True),
    )
    status: str = SQLField(sa_column=Column(String(16), nullable=False))
    model: str = SQLField(sa_column=Column(String(128), nullable=False))
    prompt_name: str = SQLField(sa_column=Column(String(64), nullable=False))
    prompt_version: str = SQLField(sa_column=Column(String(32), nullable=False))
    prompt_hash: str = SQLField(sa_column=Column(String(64), nullable=False))
    input_hash: str = SQLField(sa_column=Column(String(64), nullable=False))
    overall_score: Optional[int] = SQLField(default=None, sa_column=Column(SmallInteger, nullable=True))
    verdict_recommendation: Optional[str] = SQLField(default=None, sa_column=Column(String(16), nullable=True))
    fields: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSONB, nullable=True))
    suggested_fixes: Optional[Any] = SQLField(default=None, sa_column=Column(JSONB, nullable=True))
    prompt_tokens: Optional[int] = SQLField(default=None, sa_column=Column(Integer, nullable=True))
    completion_tokens: Optional[int] = SQLField(default=None, sa_column=Column(Integer, nullable=True))
    latency_ms: Optional[int] = SQLField(default=None, sa_column=Column(Integer, nullable=True))
    error: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))
    requested_by_id: Optional[str] = SQLField(
        default=None,
        sa_column=Column(String(36), ForeignKey(_FK_USERS_ID, ondelete=_ON_DELETE_SET_NULL), nullable=True),
    )
    request_id: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))
    created_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    )


class MarketplaceReviewDecision(SQLModel, table=True):
    """One review decision: the append-only history AND the idempotency ledger.

    Written in the same transaction as the compare-and-set `UPDATE` on
    `assistants` (`queue_service.decide`), which is what makes "exactly one of
    two racing admins wins" a database property rather than a hope. Three
    uniqueness rules carry the guarantee (design-persistence.md §4.1):

    * `uq_mm_review_decision_idempotency` -- a retried request with the same
      `Idempotency-Key` replays the stored `result` instead of deciding twice.
    * `uq_mm_review_decision_entity_version` -- at most one decision may ever be
      taken from a given observed version of a given entity, so even a
      hand-written service method that forgot the CAS cannot lose an update.
    * `ck_mm_review_decision_version_step` -- a decision advances the version by
      exactly one, so the log cannot skip a state.

    `ck_mm_review_decision_override_notice` is why F-34 cannot regress: a
    `FORCE_PUBLISH` row without the justification that bypassed the gate is
    unwritable.
    """

    __tablename__ = "mm_review_decision"
    __table_args__ = (
        Index("uq_mm_review_decision_idempotency", "idempotency_key", unique=True),
        Index(
            "uq_mm_review_decision_entity_version",
            "entity_kind",
            "entity_id",
            "from_version",
            unique=True,
        ),
        Index(
            "ix_mm_review_decision_entity_created",
            "entity_kind",
            "entity_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        Index(
            "ix_mm_review_decision_decided_by_created",
            "decided_by_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
            postgresql_where=text("decided_by_id IS NOT NULL"),
        ),
        CheckConstraint(
            "decision IN ('SUBMIT', 'APPROVE', 'REJECT', 'FORCE_PUBLISH', 'WITHDRAW')",
            name="ck_mm_review_decision_decision",
        ),
        CheckConstraint(
            "from_status IN ('DRAFT', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'WITHDRAWN')",
            name="ck_mm_review_decision_from",
        ),
        CheckConstraint(
            "to_status IN ('DRAFT', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'WITHDRAWN')",
            name="ck_mm_review_decision_to",
        ),
        CheckConstraint(
            "decision <> 'REJECT' OR (reason IS NOT NULL AND char_length(reason) BETWEEN 20 AND 2000)",
            name="ck_mm_review_decision_reject_reason",
        ),
        CheckConstraint(
            "decision <> 'FORCE_PUBLISH' OR override_notice IS NOT NULL",
            name="ck_mm_review_decision_override_notice",
        ),
        CheckConstraint("to_version = from_version + 1", name="ck_mm_review_decision_version_step"),
    )

    id: str = SQLField(sa_column=Column(String(36), primary_key=True, nullable=False))
    entity_kind: str = SQLField(sa_column=Column(String(16), nullable=False))
    entity_id: str = SQLField(sa_column=Column(Text, nullable=False))
    decision: str = SQLField(sa_column=Column(String(24), nullable=False))
    from_status: str = SQLField(sa_column=Column(String(24), nullable=False))
    to_status: str = SQLField(sa_column=Column(String(24), nullable=False))
    from_version: int = SQLField(sa_column=Column(Integer, nullable=False))
    to_version: int = SQLField(sa_column=Column(Integer, nullable=False))
    reason: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))
    override_notice: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))
    audit_run_id: Optional[str] = SQLField(
        default=None,
        sa_column=Column(Text, ForeignKey(_FK_AUDIT_RUN_ID, ondelete=_ON_DELETE_SET_NULL), nullable=True),
    )
    decided_by_id: Optional[str] = SQLField(
        default=None,
        sa_column=Column(String(36), ForeignKey(_FK_USERS_ID, ondelete=_ON_DELETE_SET_NULL), nullable=True),
    )
    decided_by_email: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))
    idempotency_key: str = SQLField(sa_column=Column(String(128), nullable=False))
    request_id: Optional[str] = SQLField(default=None, sa_column=Column(Text, nullable=True))
    # The exact response body the winning call returned, so a replay is
    # byte-identical rather than recomputed (design-persistence.md §4.1).
    result: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: Optional[datetime] = SQLField(
        default=None,
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    )


# ============================================================================
# Pydantic DTOs for API responses
# ============================================================================


# --- shared leaf shapes ------------------------------------------------------


class ReviewGateFinding(BaseModel):
    """One deterministic gate finding returned on the pending queue row.

    Mirrors the same-named class in `rest_api/routers/marketplace_management.py`
    (rule, severity, message) so that `PendingRow.gate_findings` is typed
    rather than left as `List[Dict[str, Any]]`, which gave no schema to callers.
    `detail` is intentionally absent: the raw gate detail carries matched
    locations and patterns that are not needed for queue review.
    """

    rule: str
    severity: str
    message: str


class FacetCount(BaseModel):
    """One selectable filter value and how many entities carry it."""

    value: str
    count: int


class FacetCountWithUsers(FacetCount):
    """A facet count that also carries the adoption behind it (Overview only)."""

    total_users: int


class RuleHit(BaseModel):
    """One quality check, how many entities failed it, and how much it matters.

    `rule` is the `GateRule` enum rather than a bare string so the values the
    facet offers and the values `GET /cleanup?rule=` accepts are the same
    closed set by construction — the drift between those two that produced
    unreachable filter options (F-68) cannot recur.
    """

    rule: GateRule
    count: int
    severity: Severity


class RuleFacet(FacetCount):
    """A quality check as the Cleanup *filter bar* offers it.

    Deliberately separate from `RuleHit`: `/facets` keys the rule id as
    `value` (uniform with every other facet, so one dropdown component renders
    all seven) while `/overview`'s `gate_hits.per_rule` keys it as `rule`. The
    two shapes are not interchangeable and modelling them as one silently
    dropped the id from whichever endpoint lost the coin toss.
    """

    value: GateRule  # type: ignore[assignment]
    severity: Severity


class ClusterFacet(BaseModel):
    """A duplicate cluster, as offered by the Cleanup filter bar."""

    cluster_id: int
    size: int
    canonical_name: Optional[str] = None


# --- GET /entities/{kind}/overview ------------------------------------------


class GateHits(BaseModel):
    """Rollup of the deterministic quality checks across the whole catalogue."""

    critical: int
    optional: int
    per_rule: List[RuleHit] = []


class TopCluster(BaseModel):
    """A large duplicate cluster with the member the audit would keep."""

    cluster_id: int
    size: int
    label: str
    canonical_id: Optional[str] = None
    canonical_name: Optional[str] = None
    canonical_users: Optional[int] = None
    total_users: int


class PendingKpi(BaseModel):
    """The review queue as a dashboard tile (EXECUTION-PLAN 7.3, F-59).

    `count` is the same number `GET /pending` reports as `total` -- both read
    `marketplace_status = 'PENDING_REVIEW'`, so the KPI and the page it links to
    cannot drift.

    `waiting_over_7d` and `avg_wait_days` are measured from
    `marketplace_submitted_at` and are taken only over the rows that carry one.
    Rows published before the approval gate existed have no submission time, so
    they contribute to `count` and to nothing else -- reporting their age as
    zero would make a stale queue look fresh.
    """

    count: int
    waiting_over_7d: int
    avg_wait_days: float


class OverviewResponse(BaseModel):
    """High-level marketplace health KPIs.

    `cleanup_reasons`, `verdicts`, `usage_tiers` and `pending` are declared
    required on purpose: all four were absent from the payload while the UI
    contract already promised them (F-06, F-59), and an optional field would let
    that regress silently a second time.
    """

    #: Live count of the catalogue at request time -- what "Assistants in
    #: marketplace" means. See `audited_total` for the audited population.
    total: int
    #: Row count of `GET /marketplace-assistants` (status=ALL), same predicate
    #: (`marketplace_status IN (APPROVED, PENDING_REVIEW, REJECTED)`). Separate
    #: from `total`, which is `is_global`-based and can disagree with it (F-20).
    marketplace_assistants_total: int
    #: Row count of the run every other aggregate on this response was computed
    #: from, i.e. the denominator for `buckets` / `gate_hits` /
    #: `cleanup_reasons` / `verdicts` / `usage_tiers`. It is as old as
    #: `last_run_at` and must be read together with it. Required, not optional:
    #: `total` used to carry these semantics silently, and an optional field
    #: would let the ambiguity return the moment a serializer dropped it.
    audited_total: int
    took_seconds: float
    buckets: Dict[str, int] = {}
    bucket_usage: Dict[str, float] = {}
    latest_audit_run_id: Optional[str] = None
    last_run_at: Optional[str] = None
    has_ever_run: bool
    gate_hits: GateHits
    cleanup_reasons: Dict[str, int]
    verdicts: Dict[str, int]
    usage_tiers: Dict[str, int]
    pending: PendingKpi
    top_clusters: List[TopCluster] = []
    top_mcps: List[FacetCountWithUsers] = []
    top_categories: List[FacetCountWithUsers] = []


# --- GET /entities/{kind}/facets --------------------------------------------


class FacetsResponse(BaseModel):
    """Every vocabulary the Cleanup filter bar offers.

    All seven keys are required: a facet served as `undefined` is a dropdown
    the UI has to hardcode, which is how the reason filter came to offer nine
    codes of which one was reachable (F-33, F-68).
    """

    mcps: List[FacetCount]
    categories: List[FacetCount]
    clusters: List[ClusterFacet]
    reasons: List[FacetCount]
    tiers: List[FacetCount]
    buckets: List[FacetCount]
    rules: List[RuleFacet]


# --- GET /entities/{kind}/cleanup -------------------------------------------


class SimilarPair(BaseModel):
    """The closest other entity found for a row. Mirrors `SimilarityPair.to_dict()`."""

    a_id: str
    b_id: str
    b_name: Optional[str] = None
    b_slug: Optional[str] = None
    b_project: Optional[str] = None
    signals: Dict[str, float] = {}
    combined: float
    severity: Optional[Severity] = None
    reason: Optional[str] = None
    b_verdict_kind: Optional[str] = None


class CleanupRow(BaseModel):
    """One row of the Cleanup table. Every field the table renders is flat on the row."""

    id: str
    name: Optional[str] = None
    creator: Optional[str] = None
    categories: List[str] = []
    unique_users_count: Optional[int] = None
    unique_likes_count: Optional[int] = None
    usage_tier: Optional[UsageTier] = None
    bucket: Optional[Bucket] = None
    bucket_reason: Optional[str] = None
    verdict_kind: Optional[VerdictKind] = None
    cleanup_reason: Optional[CleanupReason] = None
    canonical_id: Optional[str] = None
    canonical_name: Optional[str] = None
    canonical_users: Optional[int] = None
    critical_count: int = 0
    optional_count: int = 0
    detail: Optional[str] = None
    top_similar: Optional[SimilarPair] = None


class CleanupResponse(BaseModel):
    """A page of the Cleanup table. `total` counts the whole filtered set."""

    total: int
    page: int
    page_size: int
    items: List[CleanupRow] = []


# --- POST /audit/run, GET /audit/status, GET /audit/history -----------------


class AuditStatusResponse(BaseModel):
    """Server-held state of the current or most recent audit for one kind.

    `POST /audit/run` returns this too: it hands the work to a worker thread
    and answers 202 with the status of the run it just started (or, if one was
    already in flight, of that one).
    """

    is_running: bool
    run_id: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None

    # --- degradation (EXECUTION-PLAN 9.2, design-persistence.md §7 F2) ---
    #
    # Without these four, "the marketplace has no duplicates" and "the
    # duplicate detector never ran" are the same response body (F-25, F-27,
    # R14). `es_doc_count` is the coverage number the UI needs to say
    # "similarity ran against 19 of 219" rather than implying it ran against
    # all of them.
    status: Optional[RunStatus] = None
    similarity_backend: Optional[str] = None
    es_doc_count: Optional[int] = None
    degraded: bool = False
    degraded_reason: Optional[str] = None


class AuditRunRow(BaseModel):
    """One persisted `mm_audit_run` row, as the history table renders it."""

    id: str
    entity_kind: Optional[EntityKind] = None
    started_ts: float
    finished_ts: Optional[float] = None
    total_records: Optional[int] = None
    engine_took_seconds: Optional[float] = None


class AuditHistoryResponse(BaseModel):
    """Recent audit runs, newest first."""

    runs: List[AuditRunRow] = []


# --- GET /entities/{kind}/pending -------------------------------------------


class PendingRow(BaseModel):
    """Summary of an assistant awaiting marketplace review."""

    assistant_id: str
    name: Optional[str] = None
    project: Optional[str] = None
    marketplace_status: Optional[str] = None
    submitted_at: Optional[float] = None
    submitted_by: Optional[str] = None
    # What the deterministic gates say about the assistant right now
    # (EXECUTION-PLAN 7.6, spec P1-21; recomputed live per EPMCDME-14234).
    # Computed the same way as the detail endpoint (`GET /entities/{kind}/{eid}`),
    # so this field always reflects the assistant's current data rather than a
    # submission-time snapshot that a post-submit edit could make stale. Empty
    # for rows where recompute finds nothing to flag, including rows that
    # predate the publish-time gate.
    gate_findings: List[ReviewGateFinding] = []
    categories: List[str] = []
    # True when gate recompute failed (config load failure or per-row exception).
    # Such rows are excluded from the all_clear post-filter bucket; an empty
    # gate_findings list could mean "clean" or "evaluation unavailable", and
    # a reviewer must never approve on the assumption it means clean (CR-002,
    # EPMCDME-14628).
    gate_recompute_failed: bool = False

    @field_validator("submitted_by", mode="before")
    @classmethod
    def _flatten_created_by(cls, value: Any) -> Any:
        """Accept an `Assistant.created_by` object as well as a bare name.

        `Assistant.created_by` is a `CreatedByUser`, not a string. Passing it
        straight through raised a ValidationError and 500ed the whole Pending
        page (F-09). The caller now projects `.name` itself, but the queue is
        fed from a table where that object is the natural value, so the model
        coerces rather than exploding. Anything that is neither None, a string
        nor a named object still fails validation.
        """
        if value is None or isinstance(value, str):
            return value
        name = getattr(value, "name", None)
        return name if isinstance(name, str) else value


class PendingResponse(BaseModel):
    """A page of the moderation queue."""

    total: int
    page: int
    page_size: int
    items: List[PendingRow] = []


class MarketplaceAssistantsRow(PendingRow):
    """PendingRow extended with status-badge fields for the multi-status tab."""

    is_verified: bool = False
    marketplace_decision_reason: str | None = None


class MarketplaceAssistantsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[MarketplaceAssistantsRow]


# --- review actions ---------------------------------------------------------


class IndexingOutcomeModel(BaseModel):
    """What happened to this entity's marketplace search-index projection.

    Present on every decision that has one (approve / reject / force-publish /
    withdraw). It exists because its absence was the delivery mechanism for
    F-87: the index write was wrapped in a bare `except`, so a decision whose
    projection never landed answered 200 exactly like one that worked, and 200
    published assistants went missing from marketplace search with no response,
    metric or status anywhere reporting it.

    `state == "FAILED"` does **not** mean the decision failed. The decision is
    committed and authoritative; the index is a projection of it and is
    self-healing on the next replay, reindex or backfill.
    """

    action: str = Field(description='"index" or "remove"')
    state: str = Field(description='"OK" or "FAILED"')
    attempts: int = Field(description="How many times the write was tried")
    error: Optional[str] = None


class ReviewActionResponse(BaseModel):
    """Outcome of approve / reject / withdraw on one entity."""

    ok: bool
    entity_kind: EntityKind
    entity_id: str
    status: MarketplaceStatus
    indexing: Optional[IndexingOutcomeModel] = None


class ForcePublishResponse(ReviewActionResponse):
    """Force-publish additionally echoes the override notice and what it bypassed."""

    override_notice: str
    #: CRITICAL gate findings waived by this override; `[]` (never omitted) when there were none.
    bypassed_gate_findings: List[ReviewGateFinding] = []
    #: True when the gates could not be evaluated, so `[]` does not mean "nothing was waived".
    bypassed_gate_findings_unavailable: bool = False


# --- GET / PUT /entities/{kind}/config --------------------------------------
#
# Mirrors `validator.config.Config.to_dict()` exactly. Modelled rather than
# left as a free dict so the Settings page's form fields are generated from
# the same source as the payload they edit.


class GatesConfig(BaseModel):
    name_min: int = Field(ge=1)
    name_max: int = Field(ge=1)
    description_min: int = Field(ge=1)
    system_prompt_min: int = Field(ge=1)
    substance_prompt_alt_min: int = Field(ge=1)
    banned_generic_names: List[str] = []
    credential_patterns: List[str] = []

    model_config = ConfigDict(extra="forbid")


class SimilarityThresholds(BaseModel):
    critical: float
    optional: float


class TfidfConfig(BaseModel):
    max_features: int
    ngram_range: List[int] = []
    min_df: int


class SimilarityConfig(BaseModel):
    weights: Dict[str, float] = {}
    thresholds: SimilarityThresholds
    report_threshold: float
    text_backend: str
    minilm_model: str
    system_prompt_ceiling: int
    shortlist_size: int
    tfidf: TfidfConfig


class UsageTiersConfig(BaseModel):
    hot_percentile: int = Field(ge=1, le=100)
    warm_percentile: int = Field(ge=1, le=100)
    cold_percentile: int = Field(ge=1, le=100)

    model_config = ConfigDict(extra="forbid")


class CoreGatesConfig(BaseModel):
    description_min: int
    system_prompt_min: int
    substance_prompt_alt_min: int


class PolishGatesConfig(BaseModel):
    description_min: int
    categories_min: int
    require_icon: bool
    conversation_starters_min: int


class VerificationConfig(BaseModel):
    hot_min_users: int
    warm_min_users: int
    cold_min_users: int
    grace_days: int
    archive_dead_after_days: int
    core_gates: CoreGatesConfig
    polish_gates: PolishGatesConfig


class AuditConfigResponse(BaseModel):
    """The audit engine configuration for one entity kind."""

    gates: GatesConfig
    similarity: SimilarityConfig
    usage_tiers: UsageTiersConfig
    verification: VerificationConfig


class ConfigUpdateResponse(BaseModel):
    """Result of `PUT /config`: the config as it now stands, after the merge."""

    ok: bool
    entity_kind: EntityKind
    config: AuditConfigResponse


class AssessmentSource(str, Enum):
    """Which engine produced an assessment.

    `DETERMINISTIC` is not a degraded LLM answer — it is the eight-gate registry
    at `validator/gates.py` scored into the same five dimensions. Per
    `design-persistence.md` §7 F1 the LLM is *advisory*, so a model outage must
    downgrade the answer, never fail the request. The reviewer needs to know
    which one they are reading, hence the field on the wire.
    """

    LLM = "LLM"
    DETERMINISTIC = "DETERMINISTIC"


class AssessmentDimension(BaseModel):
    """Score + rationale for one quality dimension.

    OpenAI structured-output requires closed schemas (no extra keys), so we
    model each dimension explicitly instead of using a bare Dict[str, Any].

    `score` is bounded 0-100 (F-75): the prompt has always declared the range
    and nothing enforced it, so a model that answered 900 propagated straight
    through to the reviewer and into `mm_llm_assessment`, where the CHECK
    constraint `ck_mm_llm_assessment_score` would then reject the row.
    """

    score: int = Field(ge=0, le=100)
    justification: str
    suggested_fix: str

    model_config = ConfigDict(extra="forbid")


class AssessmentFields(BaseModel):
    """Per-dimension scores for the 5 quality dimensions.

    Fixed keys instead of a free-form dict so the schema is closed for
    OpenAI's structured-output validator.
    """

    metadata_polish: AssessmentDimension
    description_quality: AssessmentDimension
    prompt_substance: AssessmentDimension
    category_fit: AssessmentDimension
    uniqueness: AssessmentDimension

    model_config = ConfigDict(extra="forbid")


class AssessmentLLMOutput(BaseModel):
    """Exactly what the model is asked to return — the closed structured-output schema.

    Deliberately *not* the wire model. `with_structured_output` turns this class
    into the JSON schema handed to the provider, so every field added here is a
    field the model must invent. The provenance metadata a reviewer needs
    (cached, source, timestamp) is therefore added by `AssessmentResponse`
    below, outside the model's reach.
    """

    fields: AssessmentFields
    suggested_fixes: List[str]
    overall_score: int = Field(ge=0, le=100)
    verdict_recommendation: Bucket

    model_config = ConfigDict(extra="forbid")


class AssessmentResponse(AssessmentLLMOutput):
    """Structured quality assessment for a single assistant, as returned by the API.

    The LLM-authored half is inherited; the four fields below are provenance the
    service fills in. `cached` and `assessed_at` make the §4.3 cache visible
    rather than silent — a reviewer who sees a stale-looking verdict can tell
    that it was produced earlier, and by which model.
    """

    cached: bool = False
    source: AssessmentSource = AssessmentSource.LLM
    assessed_at: Optional[datetime] = None
    llm_model: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# Pending queue filter/sort enums (EPMCDME-14628)
# ============================================================================


class GateResultFilter(str, Enum):
    """Post-filter for the pending queue by live gate finding severity.

    Applied after live gate recompute in the router (`_apply_gate_result_filter`).
    Does not affect `PendingResponse.total`, which always reflects the SQL count.
    """

    all_clear = "all_clear"
    has_critical = "has_critical"
    has_optional_only = "has_optional_only"


class PendingOrderBy(str, Enum):
    """Sort order for the pending queue SQL ORDER BY clause.

    `submitted_asc` is the default (oldest-first, specs D-53, P2-18).
    """

    submitted_asc = "submitted_asc"
    submitted_desc = "submitted_desc"
    name_asc = "name_asc"


class CleanupOrderBy(str, Enum):
    """Sort order for the cleanup queue SQL ORDER BY clause.

    Mirrors the SORT_OPTIONS vocabulary defined in MarketplaceCleanupFilters.tsx.
    """

    score_desc = "score_desc"
    users_desc = "users_desc"
    users_asc = "users_asc"
    likes_desc = "likes_desc"
    likes_asc = "likes_asc"
    name_asc = "name_asc"
    name_desc = "name_desc"
