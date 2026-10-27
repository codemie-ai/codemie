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

"""Retention for marketplace audit history (EXECUTION-PLAN 5.5, design-persistence.md 6.5).

Sizing evidence from the dev database: 94 runs and 24 454 verdict rows
accumulated in three days for 282 assistants. Nothing prunes them today, so the
tables grow without bound and every read path pays for history nobody reads.

The statement below deletes **runs only**. Verdicts, findings and similarity
rows disappear with their parent because M1 (`n1a2u3d4i5t6`) installs those
foreign keys `ON DELETE CASCADE`; `mm_llm_assessment.audit_run_id` is
`ON DELETE SET NULL`, so an assessment outlives the run that requested it and a
purge degrades it rather than destroying it.

The `id <> (SELECT ... ORDER BY started_at DESC LIMIT 1)` guard is the whole
point of writing this by hand instead of reusing a generic "delete older than"
helper: the Overview, the Cleanup table and the facets are all served from the
newest SUCCEEDED run, so purging it on a quiet environment blanks the admin UI
until somebody notices and re-runs the audit.

Interface follows `activity_repository.delete_older_than` (`:339-342`): the
caller supplies the session and owns the commit, so a purge can be composed
into a larger transaction, and the row count comes back for the log line.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy import Column, DateTime, String, column, delete, select, table, text

from codemie.configs import logger

# Only a run a read can be served from is worth protecting from the purge.
# That is SUCCEEDED *and* PARTIAL: a PARTIAL run ran every gate and wrote every
# verdict, it merely lost the similarity backend (design-persistence.md §7 F2).
from codemie.service.marketplace_management.models import (  # noqa: E402
    SERVEABLE_RUN_STATUSES as _SERVEABLE_STATUSES,
)

# A lightweight table expression rather than `models.MarketplaceAuditRun`.
# `status` and `started_at` are columns M1 (`n1a2u3d4i5t6`) adds and the ORM row
# has not caught up with yet (it still carries only the float `started_ts`);
# binding to the SQLModel class here would either force an unrelated model
# change into this step or silently purge on the wrong column. No schema is
# named, exactly as the ORM does it, so `search_path` resolves it the same way
# in tests and in production.
_run = table(
    "mm_audit_run",
    column("id", String),
    column("entity_kind", String),
    column("status", String),
    Column("started_at", DateTime(timezone=True)),
)


class MarketplaceAuditRetentionRepository(ABC):
    """Purge policy for `mm_audit_run` and, by cascade, its children."""

    @abstractmethod
    def delete_older_than(self, *, entity_kind: str, cutoff: datetime, session) -> int:
        """Delete runs of `entity_kind` started before `cutoff`.

        Never deletes the newest SUCCEEDED run of that kind, whatever its age.
        Returns the number of runs removed; the caller commits.
        """


class SQLMarketplaceAuditRetentionRepository(MarketplaceAuditRetentionRepository):
    """Postgres-backed implementation of design-persistence.md 6.5."""

    def delete_older_than(self, *, entity_kind: str, cutoff: datetime, session) -> int:
        newest_good = (
            select(_run.c.id)
            .where(_run.c.entity_kind == entity_kind)
            .where(_run.c.status.in_(_SERVEABLE_STATUSES))
            .order_by(_run.c.started_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            delete(_run)
            .where(_run.c.entity_kind == entity_kind)
            .where(_run.c.started_at < cutoff)
            # IS DISTINCT FROM, not `<>`: on an environment with no SUCCEEDED run
            # at all the subquery is NULL, and `id <> NULL` is NULL, so a plain
            # `<>` would delete nothing and hide the fact that retention is not
            # running.
            .where(_run.c.id.is_distinct_from(newest_good))
        )
        result = session.execute(statement)
        deleted = result.rowcount or 0
        logger.info(
            "[marketplace_audit] retention: removed %s %s run(s) started before %s",
            deleted,
            entity_kind,
            cutoff.isoformat(),
        )
        return deleted


marketplace_audit_retention_repository: MarketplaceAuditRetentionRepository = SQLMarketplaceAuditRetentionRepository()


# ===========================================================================
# Deletion cleanup (EXECUTION-PLAN 8.7; F-39, R12, S16)
# ===========================================================================
#
# THE POLICY, AND WHY IT IS A POLICY AND NOT A CASCADE
# ----------------------------------------------------
# `design-persistence.md` §2.4 deliberately declares no foreign key from
# `assistant_id` to `assistants.id`: moderation history must be able to outlive
# the entity it describes, so that a reviewer cannot erase the record of what
# they decided by deleting the thing they decided about. A cascade would
# destroy exactly that guarantee, so the cleanup below is an explicit, stated
# decision and an explicit job — never `ON DELETE CASCADE`.
#
# The decision is split, because the rows are not the same kind of fact:
#
#   PURGE (hard DELETE) — `mm_audit_verdict`, `mm_audit_finding`,
#     `mm_audit_similarity` (BOTH the `assistant_id` and the `other_id` side),
#     `mm_llm_assessment`.
#     These are machine-generated assessments *of the deleted content*. Their
#     `detail`, `message`, `fields` and `payload_json` columns quote the
#     assistant's own prompt and description, and `mm_audit_similarity.
#     other_name` carries the deleted assistant's *name* on a surviving
#     assistant's row — so purging only one side still leaks it. Once the
#     subject no longer exists these rows carry no accountability value about
#     anybody: they are a machine's opinion of content that is gone. They are
#     also precisely the rows that resurrect a deleted assistant as a Cleanup
#     row with a NULL name (F-39: `audit_read_repository.cleanup_page` LEFT
#     JOINs `assistants`, so a missing assistant does not remove the row).
#
#   ANONYMISE (redact in place) — `mm_review_decision`.
#     This row is not about the content, it is about a *person*: which
#     administrator approved, rejected, force-published or withdrew, from which
#     status and version to which, when. That is the accountability record
#     §2.4's missing FK exists to preserve, and deleting it would let a reviewer
#     erase the evidence of their own force-publish simply by deleting the
#     assistant afterwards. The row therefore survives with its decision,
#     statuses, versions, actor and timestamp intact, while the free text that
#     quotes the deleted content — `reason`, `override_notice` and the stored
#     `result` payload — is nulled and the row is stamped `redacted_at`
#     (`d1e2l3p4r4g5`). Redaction is recorded rather than silent: without the
#     stamp, a redacted decision is indistinguishable from one that never
#     carried a reason, and a data-retention audit cannot be left to guess.
#
# `mm_audit_run` is not touched. A run is a snapshot of the whole marketplace at
# a point in time; one deleted assistant does not invalidate it, and deleting it
# would take every *other* assistant's history with it.
#
# TWO ENTRY POINTS, BECAUSE ONE CANNOT DO BOTH JOBS
# -------------------------------------------------
#   `purge_entity`  runs synchronously in the delete path, so the moderation
#                   data is gone by the time the API answers the delete.
#   `purge_orphans` is the sweep. The hook cannot fix history — every assistant
#                   deleted before this shipped left rows behind — and it cannot
#                   cover a delete that crashed between the two statements. The
#                   sweep is scoped to runs of one `entity_kind`, because a
#                   WORKFLOW verdict's `assistant_id` is a workflow id and is
#                   *supposed* to be absent from `assistants`; sweeping without
#                   that scope would delete every workflow verdict in the table.
#
# AND WHO CALLS THEM (13.2 item 1)
# --------------------------------
# There are three callers and exactly one policy, which is the point: they all
# run the code above rather than each growing their own idea of what a deleted
# assistant's history is worth.
#
#   1. `Assistant._purge_marketplace_moderation_data` — the delete path.
#   2. `_setup_marketplace_moderation_purge_scheduler` — nightly, 03:10 UTC.
#   3. `AuditCache._reap_orphans_this_run_may_have_created` — the last act of
#      every audit run, added because 1 cannot reach the rows the run itself is
#      about to write. An audit reads the corpus, computes, then persists; an
#      assistant deleted inside that window is purged of moderation data it does
#      not have yet and then acquires a verdict row from the in-flight run. That
#      is the whole explanation for the 1 orphan measured at Phase 11 and the 3
#      at Phase 12 — one of them beside a decision row whose `redacted_at` proves
#      the hook did fire. Until the next nightly sweep those rows are Cleanup
#      entries with a NULL name that 404 when clicked (F-39), so the run that
#      creates them reaps them before it closes.

# Substituted for free text that quoted the deleted entity. At least 20
# characters, because `ck_mm_review_decision_reject_reason` demands it of a
# REJECT row — the schema will not let a rejection lose its justification, only
# have it replaced by an explicit statement that it was redacted.
REDACTION_TOMBSTONE = "[redacted: the entity this decision refers to was deleted]"

# Child tables keyed to the entity, and the column that keys them. Purged in
# this order so that a failure part-way through never leaves a verdict row
# without the findings that explain it.
_ENTITY_SCOPED_PURGES: tuple[tuple[str, str], ...] = (
    ("mm_audit_finding", "assistant_id"),
    ("mm_audit_similarity", "assistant_id"),
    ("mm_audit_similarity", "other_id"),
    ("mm_audit_verdict", "assistant_id"),
    ("mm_llm_assessment", "assistant_id"),
)


class MarketplaceModerationPurgeRepository(ABC):
    """Deletion cleanup for the moderation tables (design-persistence.md §2.4)."""

    @abstractmethod
    def purge_entity(self, *, entity_kind: str, entity_id: str, session) -> int:
        """Apply the policy above to one entity. Returns rows affected; caller commits."""

    @abstractmethod
    def purge_orphans(self, *, entity_kind: str, session, limit: int = 1000) -> int:
        """Apply it to every entity of `entity_kind` that no longer exists.

        Returns the number of *entities* reclaimed, bounded by `limit` so one
        sweep cannot take an unbounded lock; the job re-runs daily.
        """


class SQLMarketplaceModerationPurgeRepository(MarketplaceModerationPurgeRepository):
    """Postgres-backed implementation.

    Written as explicit `text()` statements rather than through the SQLModel
    classes for the same reason `SQLMarketplaceAuditRetentionRepository` is:
    `mm_review_decision.redacted_at` is deliberately absent from the ORM (R10 —
    an ORM that declares a column unconditionally turns a downgrade into an
    outage for every query against that table), so it can only be written here.
    No schema is named, exactly as the ORM does it, so `search_path` resolves
    the tables the same way in tests and in production.
    """

    # Only ASSISTANT-kind moderation data is keyed to `assistants.id`. Anything
    # else has to opt in explicitly rather than be swept by accident.
    _CATALOGUE_TABLE_FOR_KIND = {"ASSISTANT": "assistants"}

    def purge_entity(self, *, entity_kind: str, entity_id: str, session) -> int:
        affected = 0
        for table_name, col_name in _ENTITY_SCOPED_PURGES:
            result = session.execute(text(f"DELETE FROM {table_name} WHERE {col_name} = :eid"), {"eid": entity_id})
            affected += result.rowcount or 0

        # ANONYMISE. A tombstone rather than NULL, for two reasons. The first is
        # forced: `ck_mm_review_decision_reject_reason` requires a REJECT to
        # carry 20-2000 characters of reason and
        # `ck_mm_review_decision_override_notice` requires a FORCE_PUBLISH to
        # carry a notice, so nulling those columns makes the row unwritable —
        # the schema itself refuses to let a rejection lose its justification.
        # The second is that it is simply better: NULL says "there was never a
        # reason", the tombstone says "there was one and it was redacted, on
        # this date". `result` has no such constraint and is a whole stored
        # response body, so it goes to NULL.
        #
        # `redacted_at IS NULL` keeps a re-run idempotent and stops a second
        # sweep from overwriting the timestamp of the first.
        result = session.execute(
            text(
                "UPDATE mm_review_decision "
                "   SET reason = CASE WHEN reason IS NULL THEN NULL ELSE :tombstone END, "
                "       override_notice = CASE WHEN override_notice IS NULL THEN NULL "
                "                              ELSE :tombstone END, "
                "       result = NULL, "
                "       redacted_at = now() "
                " WHERE entity_kind = :kind AND entity_id = :eid AND redacted_at IS NULL"
            ),
            {"kind": entity_kind, "eid": entity_id, "tombstone": REDACTION_TOMBSTONE},
        )
        affected += result.rowcount or 0

        logger.info(
            "[marketplace_audit] purge: %s row(s) of moderation data for %s %s "
            "(assessments purged, review decisions redacted)",
            affected,
            entity_kind,
            entity_id,
        )
        return affected

    def purge_orphans(self, *, entity_kind: str, session, limit: int = 1000) -> int:
        catalogue = self._CATALOGUE_TABLE_FOR_KIND.get(entity_kind)
        if catalogue is None:
            logger.info(
                "[marketplace_audit] purge sweep: no catalogue table for entity_kind=%s, skipping",
                entity_kind,
            )
            return 0

        # The candidate set is deliberately built from the *verdict* table
        # joined to its run, because that is the only place the entity kind of
        # an `assistant_id` can be established. `mm_review_decision` and
        # `mm_llm_assessment` carry `entity_kind` directly and are unioned in,
        # so an entity whose verdicts were already purged but whose decision was
        # never redacted is still reachable.
        candidates = [
            row[0]
            for row in session.execute(
                text(
                    f"""
                    SELECT eid FROM (
                        SELECT DISTINCT v.assistant_id AS eid
                          FROM mm_audit_verdict v
                          JOIN mm_audit_run r ON r.id = v.audit_run_id
                         WHERE r.entity_kind = :kind
                        UNION
                        SELECT DISTINCT d.entity_id
                          FROM mm_review_decision d
                         WHERE d.entity_kind = :kind AND d.redacted_at IS NULL
                        UNION
                        SELECT DISTINCT l.assistant_id
                          FROM mm_llm_assessment l
                         WHERE l.entity_kind = :kind
                    ) AS subjects
                     WHERE NOT EXISTS (
                        SELECT 1 FROM {catalogue} c WHERE c.id = subjects.eid
                     )
                     ORDER BY eid
                     LIMIT :limit
                    """
                ),
                {"kind": entity_kind, "limit": limit},
            ).all()
        ]

        for entity_id in candidates:
            self.purge_entity(entity_kind=entity_kind, entity_id=entity_id, session=session)

        if candidates:
            logger.info(
                "[marketplace_audit] purge sweep: reclaimed moderation data for %s deleted %s(s)",
                len(candidates),
                entity_kind,
            )
        return len(candidates)


marketplace_moderation_purge_repository: MarketplaceModerationPurgeRepository = (
    SQLMarketplaceModerationPurgeRepository()
)
