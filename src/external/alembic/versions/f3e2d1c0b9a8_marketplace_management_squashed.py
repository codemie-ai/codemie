"""marketplace management

Revision ID: f3e2d1c0b9a8
Revises: e7b3a91c4d58
Create Date: 2026-10-05 00:00:00.000000

Adds the marketplace-management schema.

* `assistants` gains the moderation columns (`marketplace_status`, which is
  `'DRAFT'` by default, plus the submit/decide timestamps, `marketplace_version`,
  `is_verified` and the decision actor and reason), a status CHECK and the
  `ix_assistants_marketplace_status` / `ix_assistants_marketplace_pending_queue`
  indexes. Existing assistants start as `DRAFT` and `is_verified = FALSE`, so
  they go through review like any other.
* `mm_audit_run`, `mm_audit_verdict`, `mm_audit_finding`,
  `mm_audit_similarity` and `mm_llm_assessment` hold the audit: one run, its
  per-assistant verdicts and failed-gate findings, the similarity evidence
  behind a duplicate verdict, and the Assess-with-AI result.
* `mm_review_decision` is the append-only decision log and idempotency ledger.
* `assistant_clone_event` gains `clone_assistant_id` and its index.

Moderation events live in `activity_events`, which already exists.

Transaction notes:

* The whole migration runs in the single transaction `env.py` opens, so the
  `LOCK TABLE alembic_version` it takes holds until the revision is stamped.
  No `autocommit_block()` is used: it would commit that transaction (releasing
  the lock, and leaving the added columns committed but unstamped), so a second
  pod starting in the same rollout would re-run `add_column` and crashloop on a
  duplicate column. The indexes added to the pre-existing `assistants` and
  `assistant_clone_event` tables are therefore plain `CREATE INDEX`, not
  `CONCURRENTLY`; the `add_column` ahead of them already holds an ACCESS
  EXCLUSIVE lock on `assistants` for the transaction, so `CONCURRENTLY` would
  not have bought anything there.
* Every `mm_*` table is created in this migration, so their indexes are built
  with a plain `CREATE INDEX` -- there is no concurrent reader or writer to
  protect against on a table nobody can see yet.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f3e2d1c0b9a8"
down_revision: Union[str, None] = "e7b3a91c4d58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "codemie"

STATUSES = ("DRAFT", "PENDING_REVIEW", "APPROVED", "REJECTED", "WITHDRAWN")
DECISIONS = ("SUBMIT", "APPROVE", "REJECT", "FORCE_PUBLISH", "WITHDRAW", "RE_SUBMIT")
RUN_STATUSES = ("PENDING", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED")
TRIGGERS = ("MANUAL", "SCHEDULED", "BACKFILL")
BUCKETS = ("VERIFIED", "COMMUNITY", "DELETE")
VERDICT_KINDS = ("KEEP", "REVIEW", "CANDIDATE_FOR_CLEANUP")
CLEANUP_REASONS = ("duplicate", "purpose_duplicate", "invalid", "dead")
USAGE_TIERS = ("HOT", "WARM", "COLD", "DEAD")
SIMILARITY_SOURCES = ("CLONE", "ES", "EXACT_NAME", "LEXICAL", "VECTOR")

_STATUS_CHECK = "ck_assistants_marketplace_status"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _in_list(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IS NULL OR {col} IN ({_quoted(values)})"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. assistants -- marketplace columns, status CHECK, backfills
    # ------------------------------------------------------------------
    op.add_column(
        "assistants",
        sa.Column("marketplace_status", sa.String(), nullable=False, server_default="DRAFT"),
    )
    op.create_index(
        "ix_assistants_marketplace_status",
        "assistants",
        ["marketplace_status"],
        if_not_exists=True,
    )

    op.add_column("assistants", sa.Column("marketplace_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("assistants", sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("assistants", sa.Column("marketplace_decided_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column(
        "assistants",
        sa.Column(
            "marketplace_decided_by",
            sa.VARCHAR(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("assistants", sa.Column("marketplace_decision_reason", sa.Text(), nullable=True))
    op.add_column("assistants", sa.Column("marketplace_submitted_at", sa.TIMESTAMP(timezone=True), nullable=True))

    op.execute(
        f"ALTER TABLE codemie.assistants ADD CONSTRAINT {_STATUS_CHECK} "
        f"CHECK (marketplace_status IS NULL OR marketplace_status IN ({_quoted(STATUSES)})) NOT VALID"
    )
    op.execute(f"ALTER TABLE codemie.assistants VALIDATE CONSTRAINT {_STATUS_CHECK}")

    op.create_index(
        "ix_assistants_marketplace_pending_queue",
        "assistants",
        ["marketplace_status", "marketplace_submitted_at"],
        postgresql_where=sa.text("marketplace_status = 'PENDING_REVIEW'"),
        if_not_exists=True,
    )

    # ------------------------------------------------------------------
    # 2. mm_audit_run -- lifecycle + aggregates
    # ------------------------------------------------------------------
    op.create_table(
        "mm_audit_run",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("entity_kind", sa.String(), nullable=False),
        sa.Column("started_ts", sa.Float(), nullable=False),
        sa.Column("finished_ts", sa.Float(), nullable=True),
        sa.Column("total_records", sa.Integer(), nullable=True),
        sa.Column("engine_took_seconds", sa.Float(), nullable=True),
        sa.Column("source_snapshot", sa.Text(), nullable=True),
        sa.Column("config_snapshot_json", sa.Text(), nullable=True),
        sa.Column("aggregates_json", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="2"),
        sa.Column("status", sa.String(16), nullable=False, server_default="SUCCEEDED"),
        sa.Column("trigger", sa.String(16), nullable=False, server_default="MANUAL"),
        sa.Column("triggered_by_id", sa.VARCHAR(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("runner_id", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("records_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engine_version", sa.String(32), nullable=True),
        sa.Column("config_hash", sa.String(64), nullable=True),
        sa.Column("similarity_backend", sa.String(32), nullable=True),
        sa.Column("es_index_name", sa.Text(), nullable=True),
        sa.Column("es_doc_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("aggregates", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_in_list("status", RUN_STATUSES), name="ck_mm_audit_run_status"),
        sa.CheckConstraint(_in_list("trigger", TRIGGERS), name="ck_mm_audit_run_trigger"),
        sa.CheckConstraint("entity_kind IN ('ASSISTANT', 'WORKFLOW')", name="ck_mm_audit_run_entity_kind"),
    )
    op.create_index(
        "ix_mm_audit_run_kind_started",
        "mm_audit_run",
        ["entity_kind", "started_at"],
        postgresql_ops={"started_at": "DESC"},
    )
    op.create_index(
        "ix_mm_audit_run_kind_latest_serveable",
        "mm_audit_run",
        ["entity_kind", "started_at"],
        postgresql_ops={"started_at": "DESC"},
        postgresql_where=sa.text("status IN ('SUCCEEDED', 'PARTIAL') AND schema_version >= 2"),
    )
    op.create_index(
        "uq_mm_audit_run_active",
        "mm_audit_run",
        ["entity_kind"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )

    # ------------------------------------------------------------------
    # 3. mm_audit_verdict -- per-assistant verdict for a run
    # ------------------------------------------------------------------
    op.create_table(
        "mm_audit_verdict",
        sa.Column("audit_run_id", sa.Text(), nullable=False),
        sa.Column("assistant_id", sa.Text(), nullable=False),
        sa.Column("bucket", sa.String(), nullable=True),
        sa.Column("bucket_reason", sa.Text(), nullable=True),
        sa.Column("verdict_kind", sa.String(), nullable=True),
        sa.Column("cleanup_reason", sa.Text(), nullable=True),
        sa.Column("usage_tier", sa.String(), nullable=True),
        sa.Column("users_count", sa.Integer(), nullable=True),
        sa.Column("canonical_id", sa.Text(), nullable=True),
        sa.Column("canonical_name", sa.Text(), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("critical_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("optional_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("canonical_users", sa.Integer(), nullable=True),
        sa.Column("purpose_cluster_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("audit_run_id", "assistant_id"),
        sa.ForeignKeyConstraint(
            ["audit_run_id"], ["mm_audit_run.id"], ondelete="CASCADE", name="fk_mm_audit_verdict_run"
        ),
        sa.CheckConstraint(_in_list("bucket", BUCKETS), name="ck_mm_audit_verdict_bucket"),
        sa.CheckConstraint(_in_list("verdict_kind", VERDICT_KINDS), name="ck_mm_audit_verdict_kind"),
        sa.CheckConstraint(_in_list("cleanup_reason", CLEANUP_REASONS), name="ck_mm_audit_verdict_reason"),
        sa.CheckConstraint(_in_list("usage_tier", USAGE_TIERS), name="ck_mm_audit_verdict_tier"),
    )
    op.create_index("ix_mm_audit_verdict_run_bucket", "mm_audit_verdict", ["audit_run_id", "bucket"])
    op.create_index("ix_mm_audit_verdict_assistant", "mm_audit_verdict", ["assistant_id"])
    op.create_index("ix_mm_audit_verdict_run_reason", "mm_audit_verdict", ["audit_run_id", "cleanup_reason"])
    op.create_index("ix_mm_audit_verdict_run_tier", "mm_audit_verdict", ["audit_run_id", "usage_tier"])
    op.create_index("ix_mm_audit_verdict_run_kind", "mm_audit_verdict", ["audit_run_id", "verdict_kind"])
    op.create_index(
        "ix_mm_audit_verdict_run_cluster",
        "mm_audit_verdict",
        ["audit_run_id", "purpose_cluster_id"],
        postgresql_where=sa.text("purpose_cluster_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 4. mm_audit_finding -- one row per failed gate rule
    # ------------------------------------------------------------------
    op.create_table(
        "mm_audit_finding",
        sa.Column("audit_run_id", sa.Text(), nullable=False),
        sa.Column("assistant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("rule", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("audit_run_id", "assistant_id", "rule"),
        sa.ForeignKeyConstraint(["audit_run_id"], ["mm_audit_run.id"], ondelete="CASCADE"),
        sa.CheckConstraint("severity IN ('CRITICAL', 'OPTIONAL')", name="ck_mm_audit_finding_severity"),
    )
    op.create_index("ix_mm_audit_finding_run_rule", "mm_audit_finding", ["audit_run_id", "rule"])
    op.create_index("ix_mm_audit_finding_run_severity", "mm_audit_finding", ["audit_run_id", "severity"])
    op.create_index("ix_mm_audit_finding_assistant", "mm_audit_finding", ["assistant_id"])

    # ------------------------------------------------------------------
    # 5. mm_audit_similarity -- evidence behind a duplicate verdict
    # ------------------------------------------------------------------
    op.create_table(
        "mm_audit_similarity",
        sa.Column("audit_run_id", sa.Text(), nullable=False),
        sa.Column("assistant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("other_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("other_name", sa.Text(), nullable=True),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("combined", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="ES"),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("audit_run_id", "assistant_id", "other_id"),
        sa.ForeignKeyConstraint(["audit_run_id"], ["mm_audit_run.id"], ondelete="CASCADE"),
        sa.CheckConstraint("combined >= 0.0 AND combined <= 1.0", name="ck_mm_audit_similarity_range"),
        sa.CheckConstraint(
            "severity IS NULL OR severity IN ('CRITICAL', 'OPTIONAL')", name="ck_mm_audit_similarity_sev"
        ),
        sa.CheckConstraint(f"source IN ({_quoted(SIMILARITY_SOURCES)})", name="ck_mm_audit_similarity_source"),
    )
    op.create_index(
        "ix_mm_audit_similarity_run_assistant_rank",
        "mm_audit_similarity",
        ["audit_run_id", "assistant_id", "rank"],
    )
    op.create_index("ix_mm_audit_similarity_assistant", "mm_audit_similarity", ["assistant_id"])
    op.create_index("ix_mm_audit_similarity_other", "mm_audit_similarity", ["other_id"])

    # ------------------------------------------------------------------
    # 6. mm_llm_assessment -- Assess-with-AI result + the prompt that made it
    # ------------------------------------------------------------------
    op.create_table(
        "mm_llm_assessment",
        sa.Column("id", sa.VARCHAR(36), nullable=False),
        sa.Column("entity_kind", sa.String(16), nullable=False, server_default="ASSISTANT"),
        sa.Column("assistant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("audit_run_id", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_name", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("overall_score", sa.SmallInteger(), nullable=True),
        sa.Column("verdict_recommendation", sa.String(16), nullable=True),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("suggested_fixes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by_id", sa.VARCHAR(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["audit_run_id"], ["mm_audit_run.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('SUCCEEDED', 'FAILED')", name="ck_mm_llm_assessment_status"),
        sa.CheckConstraint(
            "overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)",
            name="ck_mm_llm_assessment_score",
        ),
        sa.CheckConstraint(
            "verdict_recommendation IS NULL OR verdict_recommendation IN ('VERIFIED', 'COMMUNITY', 'DELETE')",
            name="ck_mm_llm_assessment_bucket",
        ),
    )
    op.create_index(
        "uq_mm_llm_assessment_cache",
        "mm_llm_assessment",
        ["assistant_id", "prompt_hash", "model", "input_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'SUCCEEDED'"),
    )
    op.create_index(
        "ix_mm_llm_assessment_assistant_created",
        "mm_llm_assessment",
        ["assistant_id", "created_at"],
        postgresql_ops={"created_at": "DESC"},
    )

    # ------------------------------------------------------------------
    # 7. mm_review_decision -- append-only decision log + idempotency ledger
    # ------------------------------------------------------------------
    op.create_table(
        "mm_review_decision",
        sa.Column("id", sa.VARCHAR(36), nullable=False),
        sa.Column("entity_kind", sa.String(16), nullable=False),
        sa.Column("entity_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=False),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("override_notice", sa.Text(), nullable=True),
        sa.Column("audit_run_id", sa.Text(), nullable=True),
        sa.Column("decided_by_id", sa.VARCHAR(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_by_email", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("redacted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["audit_run_id"], ["mm_audit_run.id"], ondelete="SET NULL"),
        sa.CheckConstraint(f"decision IN ({_quoted(DECISIONS)})", name="ck_mm_review_decision_decision"),
        sa.CheckConstraint(f"from_status IN ({_quoted(STATUSES)})", name="ck_mm_review_decision_from"),
        sa.CheckConstraint(f"to_status IN ({_quoted(STATUSES)})", name="ck_mm_review_decision_to"),
        sa.CheckConstraint(
            "decision <> 'REJECT' OR (reason IS NOT NULL AND char_length(reason) BETWEEN 20 AND 2000)",
            name="ck_mm_review_decision_reject_reason",
        ),
        sa.CheckConstraint(
            "decision <> 'FORCE_PUBLISH' OR override_notice IS NOT NULL",
            name="ck_mm_review_decision_override_notice",
        ),
        sa.CheckConstraint("to_version = from_version + 1", name="ck_mm_review_decision_version_step"),
    )
    op.create_index("uq_mm_review_decision_idempotency", "mm_review_decision", ["idempotency_key"], unique=True)
    op.create_index(
        "uq_mm_review_decision_entity_version",
        "mm_review_decision",
        ["entity_kind", "entity_id", "from_version"],
        unique=True,
    )
    op.create_index(
        "ix_mm_review_decision_entity_created",
        "mm_review_decision",
        ["entity_kind", "entity_id", "created_at"],
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index(
        "ix_mm_review_decision_decided_by_created",
        "mm_review_decision",
        ["decided_by_id", "created_at"],
        postgresql_ops={"created_at": "DESC"},
        postgresql_where=sa.text("decided_by_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 8. assistant_clone_event -- clone provenance edge (existing table)
    # ------------------------------------------------------------------
    op.add_column(
        "assistant_clone_event",
        sa.Column("clone_assistant_id", sa.String(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_assistant_clone_event_clone_assistant_id",
        "assistant_clone_event",
        ["clone_assistant_id"],
        schema=_SCHEMA,
        unique=False,
        postgresql_where=sa.text("clone_assistant_id IS NOT NULL"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assistant_clone_event_clone_assistant_id",
        table_name="assistant_clone_event",
        schema=_SCHEMA,
        if_exists=True,
    )
    op.drop_column("assistant_clone_event", "clone_assistant_id", schema=_SCHEMA)

    op.drop_index("ix_mm_review_decision_decided_by_created", table_name="mm_review_decision")
    op.drop_index("ix_mm_review_decision_entity_created", table_name="mm_review_decision")
    op.drop_index("uq_mm_review_decision_entity_version", table_name="mm_review_decision")
    op.drop_index("uq_mm_review_decision_idempotency", table_name="mm_review_decision")
    op.drop_table("mm_review_decision")

    op.drop_index("ix_mm_llm_assessment_assistant_created", table_name="mm_llm_assessment")
    op.drop_index("uq_mm_llm_assessment_cache", table_name="mm_llm_assessment")
    op.drop_table("mm_llm_assessment")

    op.drop_index("ix_mm_audit_similarity_other", table_name="mm_audit_similarity")
    op.drop_index("ix_mm_audit_similarity_assistant", table_name="mm_audit_similarity")
    op.drop_index("ix_mm_audit_similarity_run_assistant_rank", table_name="mm_audit_similarity")
    op.drop_table("mm_audit_similarity")

    op.drop_index("ix_mm_audit_finding_assistant", table_name="mm_audit_finding")
    op.drop_index("ix_mm_audit_finding_run_severity", table_name="mm_audit_finding")
    op.drop_index("ix_mm_audit_finding_run_rule", table_name="mm_audit_finding")
    op.drop_table("mm_audit_finding")

    op.drop_index("ix_mm_audit_verdict_run_cluster", table_name="mm_audit_verdict")
    op.drop_index("ix_mm_audit_verdict_run_kind", table_name="mm_audit_verdict")
    op.drop_index("ix_mm_audit_verdict_run_tier", table_name="mm_audit_verdict")
    op.drop_index("ix_mm_audit_verdict_run_reason", table_name="mm_audit_verdict")
    op.drop_index("ix_mm_audit_verdict_assistant", table_name="mm_audit_verdict")
    op.drop_index("ix_mm_audit_verdict_run_bucket", table_name="mm_audit_verdict")
    op.drop_table("mm_audit_verdict")

    op.drop_index("uq_mm_audit_run_active", table_name="mm_audit_run")
    op.drop_index("ix_mm_audit_run_kind_latest_serveable", table_name="mm_audit_run")
    op.drop_index("ix_mm_audit_run_kind_started", table_name="mm_audit_run")
    op.drop_table("mm_audit_run")

    op.drop_index(
        "ix_assistants_marketplace_pending_queue",
        table_name="assistants",
        if_exists=True,
    )
    op.drop_constraint(_STATUS_CHECK, "assistants", type_="check")
    for col in (
        "marketplace_submitted_at",
        "marketplace_decision_reason",
        "marketplace_decided_by",
        "marketplace_decided_at",
        "is_verified",
        "marketplace_version",
    ):
        op.drop_column("assistants", col)

    op.drop_index(
        "ix_assistants_marketplace_status",
        table_name="assistants",
        if_exists=True,
    )
    op.drop_column("assistants", "marketplace_status")
