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

"""Marketplace queue operations: submit, approve, reject, force_publish, withdraw, list_pending.

EXECUTION-PLAN 6.1-6.4 and 6.6; design-persistence.md §3.3 and §4.1.

Every mutating operation is one call into `decide()`, and `decide()` is a single
compare-and-set:

    UPDATE assistants
       SET marketplace_status = :to, is_global = :g, is_verified = :v,
           marketplace_version = marketplace_version + 1, ...
     WHERE id = :id
       AND marketplace_version = :observed        -- lost-update guard
       AND marketplace_status = ANY(:allowed)     -- state-machine guard
    RETURNING ...

plus the `mm_review_decision` insert, in one transaction. Zero rows updated
means somebody else moved first, and the caller gets a `MarketplaceConflictError`
carrying the *current* state so the UI can say "adminA approved this three
seconds ago" instead of silently discarding a decision.

What this replaces, and why it had to be replaced:

The previous implementation read the row, mutated the Python object, and
committed. Two admins therefore both read the same row and both committed; last
write won. That is not theoretical -- `mm_marketplace_event` ids 5 and 6 on the
live database record an approve and a reject of the same assistant 572
microseconds apart, both answering 200, leaving the row `REJECTED` while still
`is_global = true`. No single operation can produce that state, and no reader can
interpret it (F-03).

Three further defects fall out of the same rewrite:

* `reject` and `withdraw` now clear `is_global` and `is_verified` and call
  `PlatformIndexingService.remove_single_assistant`, so the take-it-down button
  takes the entity down instead of merely relabelling it (F-04, F-46). That
  method existed with zero call sites in this feature.
* The transition table is encoded in the `WHERE` clause rather than in prose, so
  approving an already-APPROVED row is a 409 instead of a silent no-op that
  appends a decision which never happened (F-05).
* `force_publish` persists its `override_notice` instead of validating, echoing
  and discarding it, and `ck_mm_review_decision_override_notice` makes losing it
  again impossible (F-34).

The Elasticsearch side effect deliberately stays *outside* the transaction. It
is an upsert (or delete) of one document keyed by assistant id, so it is
idempotent by construction and safe to re-issue on replay; holding a row lock
across a network call to ES is what §3.2 option C was rejected for.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from codemie.clients.postgres import get_session
from codemie.rest_api.models.assistant import Assistant
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    MarketplaceModerationEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.marketplace_management.models import (
    EntityKind,
    MarketplaceReviewDecision,
    MarketplaceStatus,
    PendingOrderBy,
    ReviewDecision,
)
from codemie.service.marketplace_management.observability import (
    METRIC_APPROVE,
    METRIC_FORCE_PUBLISH,
    METRIC_REJECT,
    METRIC_RE_SUBMIT,
    METRIC_SUBMIT,
    METRIC_WITHDRAW,
    track_marketplace_metric,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pending queue sort-order mapping (EPMCDME-14628)
# ---------------------------------------------------------------------------

_ORDER_CLAUSES: dict[PendingOrderBy, list[Any]] = {
    PendingOrderBy.submitted_asc: [
        Assistant.marketplace_submitted_at.asc().nullslast(),  # type: ignore[attr-defined]
        Assistant.id.asc(),  # type: ignore[attr-defined]
    ],
    PendingOrderBy.submitted_desc: [
        Assistant.marketplace_submitted_at.desc().nullslast(),  # type: ignore[attr-defined]
        Assistant.id.asc(),  # type: ignore[attr-defined]
    ],
    PendingOrderBy.name_asc: [
        Assistant.name.asc(),  # type: ignore[attr-defined]
        Assistant.id.asc(),  # type: ignore[attr-defined]
    ],
}


# ---------------------------------------------------------------------------
# The state machine (design-persistence.md §3.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionRule:
    """One row of the §3.3 transition table, in the form the CAS statement needs.

    `to_is_verified is None` means "leave the column alone" -- only `SUBMIT`
    does that, because a submission neither grants nor revokes verification.
    """

    allowed_from: tuple[str, ...]
    to_status: str
    to_is_global: bool
    to_is_verified: Optional[bool]
    # The two observability channels this transition writes to (9.3, 9.4).
    # Carried on the rule rather than branched on at the call site so that
    # adding a transition cannot silently add an unobservable one.
    activity_event_type: str
    metric: str
    stamps_submitted_at: bool = False
    reindex: bool = False
    deindex: bool = False
    # De-index only when the row was public going in. SUBMIT makes a row private
    # (`to_is_global=False`) but a never-public draft has nothing in the index to
    # remove, whereas a grandfathered public DRAFT does: without this it stays in
    # marketplace search, unreviewed, until a moderator decides or a reindex prunes it.
    deindex_if_was_public: bool = False
    # Extra source statuses honoured only while the row is public (`is_global`).
    # The migration leaves assistants that were already public as DRAFT with
    # `is_global = true`; they must stay unpublishable, while a private DRAFT
    # (never public, nothing to withdraw) still conflicts.
    allowed_from_if_global: tuple[str, ...] = ()


_D = MarketplaceStatus
TRANSITIONS: Mapping[ReviewDecision, TransitionRule] = {
    ReviewDecision.SUBMIT: TransitionRule(
        allowed_from=(_D.DRAFT.value, _D.REJECTED.value, _D.WITHDRAWN.value),
        to_status=_D.PENDING_REVIEW.value,
        to_is_global=False,
        to_is_verified=None,
        activity_event_type=MarketplaceModerationEvent.ASSISTANT_SUBMITTED,
        metric=METRIC_SUBMIT,
        stamps_submitted_at=True,
        deindex_if_was_public=True,
    ),
    ReviewDecision.APPROVE: TransitionRule(
        allowed_from=(_D.PENDING_REVIEW.value,),
        to_status=_D.APPROVED.value,
        to_is_global=True,
        to_is_verified=True,
        activity_event_type=MarketplaceModerationEvent.ASSISTANT_APPROVED,
        metric=METRIC_APPROVE,
        reindex=True,
    ),
    ReviewDecision.REJECT: TransitionRule(
        allowed_from=(_D.PENDING_REVIEW.value,),
        to_status=_D.REJECTED.value,
        to_is_global=False,
        to_is_verified=False,
        activity_event_type=MarketplaceModerationEvent.ASSISTANT_REJECTED,
        metric=METRIC_REJECT,
        deindex=True,
    ),
    ReviewDecision.FORCE_PUBLISH: TransitionRule(
        allowed_from=(
            _D.DRAFT.value,
            _D.PENDING_REVIEW.value,
            _D.REJECTED.value,
            _D.WITHDRAWN.value,
        ),
        to_status=_D.APPROVED.value,
        to_is_global=True,
        to_is_verified=True,
        activity_event_type=MarketplaceModerationEvent.ASSISTANT_FORCE_PUBLISHED,
        metric=METRIC_FORCE_PUBLISH,
        reindex=True,
    ),
    ReviewDecision.WITHDRAW: TransitionRule(
        allowed_from=(_D.PENDING_REVIEW.value, _D.APPROVED.value),
        allowed_from_if_global=(_D.DRAFT.value,),
        to_status=_D.WITHDRAWN.value,
        to_is_global=False,
        to_is_verified=False,
        activity_event_type=MarketplaceModerationEvent.ASSISTANT_WITHDRAWN,
        metric=METRIC_WITHDRAW,
        deindex=True,
    ),
    # RE_SUBMIT is the "the content under review/approval changed" edge. From APPROVED it
    # takes the assistant private and back into the queue; from PENDING_REVIEW it keeps it
    # in the queue but advances the version (so a reviewer's stale decision CAS-conflicts
    # instead of approving content they never saw) and writes the ledger row and event
    # that a silent in-memory bounce to DRAFT never did. `marketplace_submitted_at` is kept,
    # so the edit does not cost the submitter their place in the queue.
    ReviewDecision.RE_SUBMIT: TransitionRule(
        allowed_from=(_D.APPROVED.value, _D.PENDING_REVIEW.value),
        to_status=_D.PENDING_REVIEW.value,
        to_is_global=False,
        to_is_verified=False,
        activity_event_type=MarketplaceModerationEvent.ASSISTANT_RE_SUBMITTED,
        metric=METRIC_RE_SUBMIT,
        deindex=True,
    ),
}


class MarketplaceConflictError(Exception):
    """Somebody else moved first, or the entity was never in a state to move from.

    Carries the row's *current* state so the caller can render an explanation
    rather than a bare failure: which status it is in now, which version the
    client would have to re-read, and who decided it when.
    """

    status_code = 409

    def __init__(
        self,
        *,
        entity_kind: str,
        entity_id: str,
        decision: str,
        expected_version: int,
        allowed_from: Sequence[str],
        current_status: Optional[str],
        current_version: Optional[int],
        current_decided_by: Optional[str] = None,
        current_decided_at: Optional[datetime] = None,
    ) -> None:
        self.entity_kind = entity_kind
        self.entity_id = entity_id
        self.decision = decision
        self.expected_version = expected_version
        self.allowed_from = tuple(allowed_from)
        self.current_status = current_status
        self.current_version = current_version
        self.current_decided_by = current_decided_by
        self.current_decided_at = current_decided_at
        super().__init__(
            f"{decision} on {entity_kind.lower()} {entity_id!r} conflicts: it is "
            f"{current_status!r} at version {current_version} "
            f"(expected version {expected_version}, allowed from {list(allowed_from)})."
        )

    def to_detail(self) -> dict[str, Any]:
        """The 409 body. Every field the admin UI needs to explain the refusal."""
        return {
            "error": "conflict",
            "message": str(self),
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "decision": self.decision,
            "expected_version": self.expected_version,
            "allowed_from": list(self.allowed_from),
            "marketplace_status": self.current_status,
            "marketplace_version": self.current_version,
            "marketplace_decided_by": self.current_decided_by,
            "marketplace_decided_at": (self.current_decided_at.isoformat() if self.current_decided_at else None),
        }


@dataclass(frozen=True)
class DecisionExtras:
    """Optional, rarely-used inputs to `decide()`, bundled to keep its signature short.

    `pre_cas_hook`, when set, is called with the assistant id immediately before
    the CAS UPDATE runs (see `decide`). `submitted_at` pins the submission stamp;
    `result_extra` is merged into the stored result body.
    """

    submitted_at: Optional[datetime] = None
    result_extra: Optional[Mapping[str, Any]] = None
    pre_cas_hook: Optional[Callable[[str], None]] = None


@dataclass(frozen=True)
class DecisionOutcome:
    """What a decision produced: the response body, and whether it was a replay."""

    result: dict[str, Any]
    replayed: bool
    decision_id: Optional[str] = None
    to_version: Optional[int] = None


# The compare-and-set. Written as SQL rather than assembled through the ORM
# because the guarantee lives in the `WHERE` clause: a `session.add()` of a
# mutated object cannot express "only if nobody moved since I read this".
_CAS_SQL = text(
    """
    UPDATE codemie.assistants
       SET marketplace_status          = :to_status,
           is_global                   = :to_is_global,
           is_verified                 = COALESCE(CAST(:to_is_verified AS boolean), is_verified),
           marketplace_decided_by      = :decided_by_id,
           marketplace_decided_at      = now(),
           marketplace_decision_reason = :reason,
           marketplace_submitted_at    = CASE WHEN CAST(:stamp_submitted AS boolean)
                                              THEN CAST(:submitted_at AS timestamptz)
                                              ELSE marketplace_submitted_at END,
           marketplace_version         = marketplace_version + 1,
           update_date                 = now()
     WHERE id                  = :entity_id
       AND marketplace_version = :expected_version
       AND (marketplace_status = ANY(CAST(:allowed_from AS text[]))
            OR (is_global AND marketplace_status = ANY(CAST(:allowed_from_if_global AS text[]))))
    RETURNING marketplace_version AS to_version,
              marketplace_status,
              marketplace_submitted_at,
              is_global,
              is_verified,
              name
    """
)

_CURRENT_SQL = text(
    """
    SELECT marketplace_status, marketplace_version, marketplace_decided_by,
           marketplace_decided_at, name, is_global
      FROM codemie.assistants
     WHERE id = :entity_id
    """
)


class MarketplaceQueueService:
    """Lifecycle operations for assistants in the marketplace review queue."""

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def approve(
        self,
        assistant_id: str,
        actor: str,
        *,
        expected_version: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        decided_by_id: Optional[str] = None,
        decided_by_email: Optional[str] = None,
        request_id: Optional[str] = None,
        audit_run_id: Optional[str] = None,
        gate_check: Optional[Callable[[str], None]] = None,
    ) -> DecisionOutcome:
        """Approve a pending submission: publish it, verify it, index it.

        `gate_check` is an optional callable that receives the assistant_id and
        raises (typically HTTPException 422) if approval should be blocked.
        It is called inside `decide()` immediately before the CAS UPDATE, which
        closes the TOCTOU window that exists when the check and the commit run
        in separate DB round-trips (CR-004).
        """
        return self.decide(
            assistant_id=assistant_id,
            decision=ReviewDecision.APPROVE,
            actor=actor,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            decided_by_id=decided_by_id,
            decided_by_email=decided_by_email,
            request_id=request_id,
            audit_run_id=audit_run_id,
            extras=DecisionExtras(pre_cas_hook=gate_check),
        )

    def reject(
        self,
        assistant_id: str,
        actor: str,
        reason: Optional[str] = None,
        *,
        expected_version: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        decided_by_id: Optional[str] = None,
        decided_by_email: Optional[str] = None,
        request_id: Optional[str] = None,
        audit_run_id: Optional[str] = None,
    ) -> DecisionOutcome:
        """Reject a pending submission: unpublish it, unverify it, de-index it."""
        return self.decide(
            assistant_id=assistant_id,
            decision=ReviewDecision.REJECT,
            actor=actor,
            reason=reason,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            decided_by_id=decided_by_id,
            decided_by_email=decided_by_email,
            request_id=request_id,
            audit_run_id=audit_run_id,
        )

    def force_publish(
        self,
        assistant_id: str,
        actor: str,
        *,
        override_notice: Optional[str] = None,
        expected_version: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        decided_by_id: Optional[str] = None,
        decided_by_email: Optional[str] = None,
        request_id: Optional[str] = None,
        audit_run_id: Optional[str] = None,
    ) -> DecisionOutcome:
        """Publish over the review gate, recording the justification for doing so.

        Also records *what was overridden*: the CRITICAL gate findings at the moment
        of the CAS, stored on the decision as `result["bypassed_gate_findings"]`
        (`[]` when there were none), so an override can always be traced to the rules
        it waived. Both entry points -- the queue's Force-Publish action and the
        publish-time `force_publish` flag -- come through here, so both record it.
        Computed in the pre-CAS hook, as approve does, so the findings are the ones
        in force when the decision committed. A replay returns the stored body.
        """
        recorded: dict[str, Any] = {"bypassed_gate_findings": []}

        def _record_bypassed_findings(entity_id: str) -> None:
            findings = self._critical_gate_findings(entity_id)
            if findings is None:
                # Gates could not be evaluated. A force-publish is the override of last
                # resort, so it must not be blocked by that -- but the record says so
                # rather than claiming nothing was waived.
                recorded["bypassed_gate_findings_unavailable"] = True
            else:
                recorded["bypassed_gate_findings"] = findings

        return self.decide(
            assistant_id=assistant_id,
            decision=ReviewDecision.FORCE_PUBLISH,
            actor=actor,
            override_notice=override_notice,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            decided_by_id=decided_by_id,
            decided_by_email=decided_by_email,
            request_id=request_id,
            audit_run_id=audit_run_id,
            extras=DecisionExtras(result_extra=recorded, pre_cas_hook=_record_bypassed_findings),
        )

    @staticmethod
    def _critical_gate_findings(assistant_id: str) -> Optional[list[dict[str, str]]]:
        """The CRITICAL deterministic-gate findings for this assistant now, or None if unavailable.

        Same collaborators as the publish gate and the review drawer
        (`assistant_to_record` + `gates.run_all`); `rule`/`severity`/`message` only,
        without the matched-location `detail`.
        """
        try:
            from codemie.service.marketplace_management.entity_adapter import assistant_to_record
            from codemie.service.marketplace_management.validator import gates
            from codemie.service.marketplace_management.validator.config import Config as GateConfig
            from codemie.service.marketplace_management.validator.models import Severity

            assistant = Assistant.find_by_id(assistant_id)
            if assistant is None:
                return None
            findings = gates.run_all(assistant_to_record(assistant), GateConfig.for_assistant_gates())
            return [
                {"rule": f.rule, "severity": f.severity.value, "message": f.message}
                for f in findings
                if f.severity == Severity.CRITICAL
            ]
        except Exception as exc:  # noqa: BLE001 - recorded as unavailable, never blocks the override
            logger.warning("Gate findings unavailable for force-publish of %s: %s", assistant_id, exc)
            return None

    def latest_force_publish(self, entity_id: str, version: int) -> Optional[dict[str, Any]]:
        """The FORCE_PUBLISH decision that produced `version` of this entity, if that is how it got there.

        None when the entity's current state came from some other decision (or none),
        so a later approve/withdraw does not keep showing a stale override.
        """
        sql = text(
            """
            SELECT override_notice, decided_by_email, result
              FROM codemie.mm_review_decision
             WHERE decision = 'FORCE_PUBLISH'
               AND entity_kind = :kind
               AND entity_id = :entity_id
               AND to_version = :version
             ORDER BY created_at DESC
             LIMIT 1
            """
        )
        with get_session() as session:
            row = (
                session.exec(
                    sql, params={"kind": EntityKind.ASSISTANT.value, "entity_id": entity_id, "version": version}
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        result = dict(row["result"] or {})
        return {
            "override_notice": row["override_notice"],
            "overridden_by": row["decided_by_email"],
            "bypassed_gate_findings": list(result.get("bypassed_gate_findings") or []),
            "bypassed_gate_findings_unavailable": bool(result.get("bypassed_gate_findings_unavailable")),
        }

    def withdraw(
        self,
        assistant_id: str,
        actor: str,
        *,
        reason: Optional[str] = None,
        expected_version: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        decided_by_id: Optional[str] = None,
        decided_by_email: Optional[str] = None,
        request_id: Optional[str] = None,
        audit_run_id: Optional[str] = None,
    ) -> DecisionOutcome:
        """Withdraw from the marketplace: unpublish, unverify, de-index."""
        return self.decide(
            assistant_id=assistant_id,
            decision=ReviewDecision.WITHDRAW,
            actor=actor,
            reason=reason,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            decided_by_id=decided_by_id,
            decided_by_email=decided_by_email,
            request_id=request_id,
            audit_run_id=audit_run_id,
        )

    def submit(
        self,
        assistant_id: str,
        actor: str,
        *,
        expected_version: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        decided_by_id: Optional[str] = None,
        decided_by_email: Optional[str] = None,
        request_id: Optional[str] = None,
        gate_findings: Optional[Sequence[Mapping[str, Any]]] = None,
        submitted_by: Optional[str] = None,
    ) -> datetime:
        """Put an assistant into the review queue and stamp when it got there.

        This is the transition that had no writer: `marketplace_submitted_at`
        was a declared response field nothing assigned (F-31), and the moment a
        submission *entered* the queue left no trace at all -- the one event a
        reviewer most wants to see.

        The timestamp is taken once, here, rather than defaulted in the
        database, so the value that orders the queue is the same value the
        SUBMITTED event carries. Phase 7.1 calls this from the publish branch
        once the approval gate is armed; until then the gate ships OFF and
        nothing in the product reaches it. Returns the stamped time so the
        caller can echo it.
        """
        submitted_at = datetime.now(timezone.utc)
        # The deterministic gate findings travel *with* the submission
        # (EXECUTION-PLAN 7.6, spec P1-21): the reviewer reads what the gates
        # said at the moment of submission, rather than re-running them later
        # against an assistant the author has since edited.
        extra: dict[str, Any] = {}
        if gate_findings is not None:
            extra["gate_findings"] = [dict(f) for f in gate_findings]
        if submitted_by:
            extra["submitted_by"] = submitted_by
        outcome = self.decide(
            assistant_id=assistant_id,
            decision=ReviewDecision.SUBMIT,
            actor=actor,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            decided_by_id=decided_by_id,
            decided_by_email=decided_by_email,
            request_id=request_id,
            extras=DecisionExtras(submitted_at=submitted_at, result_extra=extra or None),
        )
        stamped = outcome.result.get("submitted_at")
        if isinstance(stamped, str):
            return datetime.fromisoformat(stamped)
        return submitted_at

    def re_submit(
        self,
        assistant_id: str,
        actor: str,
        *,
        expected_version: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        decided_by_id: Optional[str] = None,
        decided_by_email: Optional[str] = None,
        request_id: Optional[str] = None,
        gate_findings: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> DecisionOutcome:
        """Put an edited APPROVED or PENDING_REVIEW assistant (back) into PENDING_REVIEW.

        Mirrors submit() in structure: gate findings at the moment of the edit
        are snapshotted into the mm_review_decision.result JSONB so reviewers
        see what triggered the re-review without re-running the gates against
        content that may have changed again by the time they look (spec §3).
        """
        extra: dict[str, Any] = {}
        if gate_findings is not None:
            extra["gate_findings"] = [dict(f) for f in gate_findings]
        return self.decide(
            assistant_id=assistant_id,
            decision=ReviewDecision.RE_SUBMIT,
            actor=actor,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            decided_by_id=decided_by_id,
            decided_by_email=decided_by_email,
            request_id=request_id,
            extras=DecisionExtras(result_extra=extra or None),
        )

    # ------------------------------------------------------------------
    # The one write path
    # ------------------------------------------------------------------

    def decide(
        self,
        *,
        assistant_id: str,
        decision: ReviewDecision,
        actor: str,
        reason: Optional[str] = None,
        override_notice: Optional[str] = None,
        expected_version: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        decided_by_id: Optional[str] = None,
        decided_by_email: Optional[str] = None,
        request_id: Optional[str] = None,
        audit_run_id: Optional[str] = None,
        entity_kind: EntityKind = EntityKind.ASSISTANT,
        extras: Optional[DecisionExtras] = None,
    ) -> DecisionOutcome:
        """Apply one review decision, exactly once, or raise.

        Raises `ValueError` if the entity does not exist (the router maps that
        to 404) and `MarketplaceConflictError` if the CAS matched no row (409).

        `extras.pre_cas_hook`, when set, is called with `assistant_id` immediately
        before the CAS UPDATE runs, inside the same DB session.  It may raise
        any exception to abort the decision (e.g. HTTPException 422 to block an
        approve when CRITICAL gate findings are detected).  Replays bypass the
        hook — they replay what already committed, so re-checking the gate is
        both unnecessary and potentially inconsistent.
        """
        extras = extras or DecisionExtras()
        submitted_at = extras.submitted_at
        pre_cas_hook = extras.pre_cas_hook
        rule = TRANSITIONS[decision]
        stamp_submitted = rule.stamps_submitted_at
        if stamp_submitted and submitted_at is None:
            submitted_at = datetime.now(timezone.utc)

        with get_session() as session:
            current = session.exec(_CURRENT_SQL, params={"entity_id": assistant_id}).mappings().first()
            if current is None:
                raise ValueError(f"Assistant {assistant_id!r} not found")

            observed = current["marketplace_version"] if expected_version is None else expected_version
            idem_key = idempotency_key or self._derive_key(
                entity_kind=entity_kind,
                entity_id=assistant_id,
                decision=decision,
                expected_version=observed,
                decided_by_id=decided_by_id or actor,
            )

            # Layer 2 (§4.1): a retry of a decision already taken replays the
            # stored body instead of deciding a second time.
            replay = self._find_replay(session, idem_key)
            if replay is not None:
                # A replay cannot know whether the row was public at the original
                # attempt; removing an absent document is a no-op, so assume it was.
                indexing = self._side_effects(rule, assistant_id, replay.get("_assistant_name"), was_public=True)
                # The stored body is replayed byte for byte (§4.1) -- except for
                # `indexing`, which describes *this* attempt. A replay is the
                # self-healing retry for an index write that failed the first
                # time, so echoing the first attempt's verdict would be wrong in
                # both directions.
                return DecisionOutcome(
                    result=self._with_indexing(replay["result"], indexing),
                    replayed=True,
                    decision_id=replay["id"],
                    to_version=replay["to_version"],
                )

            # Run the optional pre-CAS gate check immediately before the UPDATE
            # so the check and the write are as close together as possible (CR-004).
            # Any exception from the hook aborts the decision without touching the
            # row.  Replays skip this -- they committed on the first attempt.
            if pre_cas_hook is not None:
                pre_cas_hook(assistant_id)

            updated = (
                session.exec(
                    _CAS_SQL,
                    params={
                        "to_status": rule.to_status,
                        "to_is_global": rule.to_is_global,
                        "to_is_verified": rule.to_is_verified,
                        "decided_by_id": decided_by_id,
                        "reason": reason,
                        "stamp_submitted": stamp_submitted,
                        "submitted_at": submitted_at,
                        "entity_id": assistant_id,
                        "expected_version": observed,
                        "allowed_from": list(rule.allowed_from),
                        "allowed_from_if_global": list(rule.allowed_from_if_global),
                    },
                )
                .mappings()
                .first()
            )

            if updated is None:
                # CAS matched no row: either the source status is not in
                # allowed_from (disallowed transition) or a concurrent write
                # changed the version between our initial read and the UPDATE.
                # Re-read with a fresh session after rollback to get the winner's
                # committed state so the error message is accurate (CR-010 only
                # applies to subsequent writes, not to error-only reads).
                session.rollback()
                with get_session() as _fresh:
                    _snapshot = (
                        _fresh.exec(_CURRENT_SQL, params={"entity_id": assistant_id}).mappings().first() or current
                    )
                raise MarketplaceConflictError(
                    entity_kind=entity_kind.value,
                    entity_id=assistant_id,
                    decision=decision.value,
                    expected_version=observed,
                    allowed_from=rule.allowed_from,
                    current_status=_snapshot["marketplace_status"],
                    current_version=_snapshot["marketplace_version"],
                    current_decided_by=_snapshot["marketplace_decided_by"],
                    current_decided_at=_snapshot["marketplace_decided_at"],
                )

            result = self._result_body(
                entity_kind=entity_kind,
                entity_id=assistant_id,
                updated=updated,
                override_notice=override_notice,
                stamp_submitted=stamp_submitted,
                extra=extras.result_extra,
            )
            decision_id = str(uuid.uuid4())
            session.add(
                MarketplaceReviewDecision(
                    id=decision_id,
                    entity_kind=entity_kind.value,
                    entity_id=assistant_id,
                    decision=decision.value,
                    from_status=current["marketplace_status"],
                    to_status=rule.to_status,
                    from_version=observed,
                    to_version=int(updated["to_version"]),
                    reason=reason,
                    override_notice=override_notice,
                    audit_run_id=audit_run_id,
                    decided_by_id=decided_by_id,
                    decided_by_email=decided_by_email,
                    idempotency_key=idem_key,
                    request_id=request_id,
                    result=result,
                )
            )
            # The audit trail, in the platform's own append-only log rather
            # than in `mm_marketplace_event` (F-35). Same session, so the event
            # and the decision commit or roll back together: a moderation
            # action that happened without a trace, or a trace of one that
            # never happened, are both worse than either alone.
            #
            # `insert` opens a SAVEPOINT (`activity_repository.py:140`) and
            # swallows its own failures, so a malformed event can never abort
            # the transaction that carries the decision.
            self._record_activity_event(
                session,
                rule=rule,
                entity_kind=entity_kind,
                entity_id=assistant_id,
                actor=actor,
                actor_id=decided_by_id,
                attributes={
                    "decision": decision.value,
                    "from_status": current["marketplace_status"],
                    "to_status": rule.to_status,
                    "to_version": int(updated["to_version"]),
                    "reason": reason,
                    "override_notice": override_notice,
                    "audit_run_id": audit_run_id,
                    "request_id": request_id,
                },
            )

            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                return self._resolve_integrity_error(
                    exc,
                    entity_kind=entity_kind,
                    entity_id=assistant_id,
                    decision=decision,
                    rule=rule,
                    observed=observed,
                    idem_key=idem_key,
                )

            assistant_name = updated["name"]

        # CR-023: ES unavailability must not fail a committed operation.
        # _side_effects() is idempotent; a replay or background job will retry.
        try:
            indexing = self._side_effects(rule, assistant_id, assistant_name, was_public=bool(current["is_global"]))
        except Exception:
            logger.warning(
                "marketplace_queue: _side_effects failed after commit; "
                "index sync will be retried. assistant_id=%r decision=%r",
                assistant_id,
                decision.value,
                exc_info=True,
            )
            indexing = None

        # CR-022: metrics emission must not fail a committed operation.
        # After the commit, and only for a decision that actually moved the row:
        # a replay returns above without reaching here, so a double-clicked
        # approve counts once (F-36).
        try:
            track_marketplace_metric(
                rule.metric,
                entity_id=assistant_id,
                entity_name=assistant_name,
                actor_id=decided_by_id or actor,
                actor_email=decided_by_email,
                additional_attributes={
                    "entity_kind": entity_kind.value,
                    "decision": decision.value,
                    "from_status": current["marketplace_status"],
                    "to_status": rule.to_status,
                },
            )
        except Exception:
            logger.warning(
                "marketplace_queue: track_marketplace_metric failed after commit; "
                "metric will be lost. assistant_id=%r decision=%r",
                assistant_id,
                decision.value,
                exc_info=True,
            )
        return DecisionOutcome(
            result=self._with_indexing(result, indexing),
            replayed=False,
            decision_id=decision_id,
            to_version=int(updated["to_version"]),
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_pending(
        self,
        page: int = 1,
        page_size: int = 25,
        *,
        category: str | None = None,
        order_by: PendingOrderBy | None = None,
    ) -> tuple[list[Assistant], int]:
        """One ordered page of the review queue, plus the size of the whole queue.

        Oldest submission first (specs D-53 and P2-18). Both the order and the
        page boundaries are expressed in SQL: the router used to fetch every
        pending row and slice the list in Python, and the query carried no
        `ORDER BY` at all, so two `LIMIT/OFFSET` reads of the same queue were
        two independent samples -- a submission could appear on both pages or on
        neither (F-94).

        `NULLS LAST` because every row that exists today predates the approval
        gate and therefore has no honest submission time; unknown goes to the
        back rather than to the front. `id` breaks ties so the order is total --
        without it, rows sharing a timestamp (or all the NULLs) could still
        shuffle between pages.

        `category` is an exact JSONB containment filter: only rows whose
        `categories` array includes the given string are returned, and `total`
        reflects that filtered count (EPMCDME-14628).

        `order_by` selects the SQL ORDER BY clause from `_ORDER_CLAUSES`;
        defaults to `submitted_asc` (oldest-first, specs D-53, P2-18).
        """
        if order_by is None:
            order_by = PendingOrderBy.submitted_asc
        offset = max(0, (page - 1) * page_size)
        pending = Assistant.marketplace_status == MarketplaceStatus.PENDING_REVIEW.value
        filters = [pending]
        if category is not None:
            filters.append(Assistant.categories.contains([category]))  # type: ignore[attr-defined]
        with get_session() as session:
            total = session.exec(select(func.count()).select_from(Assistant).where(*filters)).one()
            rows = session.exec(
                select(Assistant).where(*filters).order_by(*_ORDER_CLAUSES[order_by]).offset(offset).limit(page_size)
            ).all()
        return list(rows), int(total)

    def list_marketplace_assistants(
        self,
        statuses: list[str],
        page: int = 1,
        page_size: Optional[int] = 50,
    ) -> tuple[list[Assistant], int]:
        """One ordered page of marketplace assistants filtered by status list.

        `page_size=None` returns every matching row (no offset/limit).

        Used by the multi-status Marketplace Assistants tab (EPMCDME-14734).
        Unlike list_pending, which is fixed to PENDING_REVIEW, this method
        accepts any subset of {APPROVED, PENDING_REVIEW, REJECTED}.

        DRAFT and WITHDRAWN are never included — the caller must pass only
        status values from that permitted set.

        Ordering: newest submission first (marketplace_submitted_at DESC NULLS
        LAST, then id for a total order), consistent with list_pending.
        """
        with get_session() as session:
            total = session.exec(
                select(func.count()).select_from(Assistant).where(Assistant.marketplace_status.in_(statuses))  # type: ignore[union-attr]
            ).one()
            query = (
                select(Assistant)
                .where(Assistant.marketplace_status.in_(statuses))  # type: ignore[union-attr]
                .order_by(
                    Assistant.marketplace_submitted_at.desc().nullslast(),  # type: ignore[union-attr]
                    Assistant.id,
                )
            )
            if page_size is not None:
                query = query.offset(max(0, (page - 1) * page_size)).limit(page_size)
            rows = session.exec(query).all()
        return list(rows), int(total)

    def pending_stats(self) -> dict[str, Any]:
        """How big the review queue is and how long it has been waiting.

        EXECUTION-PLAN 7.3 (F-59). The Overview's "Pending review" tile needs
        three numbers, and they have to come from the same predicate
        `list_pending` pages over -- `marketplace_status = 'PENDING_REVIEW'` --
        or the dashboard and the queue it links to will disagree.

        `waiting_over_7d` and `avg_wait_days` are computed only over rows that
        carry a `marketplace_submitted_at`. Every row published before the
        approval gate was armed has NULL there (`s1u2b3m4t5d6`), and counting
        an unknown age as zero would report a stale queue as fresh. Those rows
        still count toward `count`: they are in the queue whether or not we know
        when they arrived.

        One statement, three aggregates -- the tile costs a single index scan
        over the partial `ix_assistants_marketplace_pending_queue`.
        """
        pending = Assistant.marketplace_status == MarketplaceStatus.PENDING_REVIEW.value
        submitted_at = Assistant.marketplace_submitted_at  # type: ignore[attr-defined]
        age_days = func.extract("epoch", func.now() - submitted_at) / 86400.0

        with get_session() as session:
            total, over_7d, avg_days = session.exec(
                select(
                    func.count(),
                    func.count().filter(age_days > 7),
                    func.avg(age_days),
                ).where(pending)
            ).one()

        return {
            "count": int(total or 0),
            "waiting_over_7d": int(over_7d or 0),
            "avg_wait_days": round(float(avg_days), 2) if avg_days is not None else 0.0,
        }

    def latest_submissions(self, entity_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """The newest SUBMIT decision per entity, as the reviewer needs to see it.

        The queue can only learn one thing from the decision ledger: **who
        submitted it** -- `assistants.created_by` is the *author*, and on a
        cloned or handed-over assistant the author is not the person who put
        it in front of a reviewer. The submitter is recorded on the decision
        that created the queue entry.

        This row's `result["gate_findings"]` (what the gates said at
        submission time, spec P1-21) is still returned here, but the
        pending-list handler no longer serves `PendingRow.gate_findings` from
        it (EPMCDME-14234): recomputing live at read time is exactly what
        that field needs, so a post-submit edit cannot leave the queue
        disagreeing with the detail endpoint about the current assistant.

        One query for the whole page, keyed by entity id; entities with no
        recorded submission (every row that predates the approval gate) are
        simply absent from the result and the caller falls back.
        """
        ids = [i for i in entity_ids if i]
        if not ids:
            return {}
        sql = text(
            """
            SELECT DISTINCT ON (entity_id)
                   entity_id, decided_by_email, decided_by_id, result, created_at
              FROM codemie.mm_review_decision
             WHERE decision = 'SUBMIT'
               AND entity_kind = :kind
               AND entity_id = ANY(CAST(:ids AS text[]))
             ORDER BY entity_id, created_at DESC
            """
        )
        with get_session() as session:
            rows = session.exec(sql, params={"kind": EntityKind.ASSISTANT.value, "ids": list(ids)}).mappings().all()

        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            result = dict(row["result"] or {})
            out[row["entity_id"]] = {
                "submitted_by": result.get("submitted_by") or row["decided_by_email"],
                "gate_findings": list(result.get("gate_findings") or []),
                "submitted_at": result.get("submitted_at"),
            }
        return out

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_key(
        *,
        entity_kind: EntityKind,
        entity_id: str,
        decision: ReviewDecision,
        expected_version: int,
        decided_by_id: str,
    ) -> str:
        """The server-side key when the client sent none (§4.1, layer 2).

        Deriving from the *observed version* rather than from wall-clock time is
        what makes a double-click idempotent: both clicks read the same version,
        so both derive the same key.
        """
        raw = f"{entity_kind.value}:{entity_id}:{decision.value}:{expected_version}:{decided_by_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _find_replay(session, idem_key: str) -> Optional[dict[str, Any]]:
        row = session.exec(
            select(MarketplaceReviewDecision).where(MarketplaceReviewDecision.idempotency_key == idem_key)
        ).first()
        if row is None:
            return None
        return {"id": row.id, "result": dict(row.result or {}), "to_version": row.to_version, "_assistant_name": None}

    def _resolve_integrity_error(
        self,
        exc: IntegrityError,
        *,
        entity_kind: EntityKind,
        entity_id: str,
        decision: ReviewDecision,
        rule: TransitionRule,
        observed: int,
        idem_key: str,
    ) -> DecisionOutcome:
        """Turn a uniqueness violation into the outcome it actually represents.

        `uq_mm_review_decision_idempotency` means we lost a race to our own
        retry -- the other attempt already recorded this exact decision, so this
        one is a replay. `uq_mm_review_decision_entity_version` (layer 3) means a
        different decision was taken from the same observed version, which is a
        conflict; it should be unreachable while the CAS above is correct, and
        it exists precisely so that a future mistake there cannot lose an update.
        """
        constraint = self._constraint_name(exc)
        if constraint == "uq_mm_review_decision_idempotency":
            with get_session() as session:
                replay = self._find_replay(session, idem_key)
            if replay is not None:
                return DecisionOutcome(
                    result=replay["result"],
                    replayed=True,
                    decision_id=replay["id"],
                    to_version=replay["to_version"],
                )
        if constraint == "uq_mm_review_decision_entity_version":
            # A different decision was committed from the same observed version.
            # Do not re-read current state here: no lock is held between this
            # point and any follow-up SELECT, so the values would be advisory
            # at best.  The caller must re-read the row themselves (CR-010).
            raise MarketplaceConflictError(
                entity_kind=entity_kind.value,
                entity_id=entity_id,
                decision=decision.value,
                expected_version=observed,
                allowed_from=rule.allowed_from,
                current_status=None,
                current_version=None,
                current_decided_by=None,
                current_decided_at=None,
            )
        raise exc

    @staticmethod
    def _constraint_name(exc: IntegrityError) -> Optional[str]:
        diag = getattr(getattr(exc, "orig", None), "diag", None)
        return getattr(diag, "constraint_name", None)

    @staticmethod
    def _result_body(
        *,
        entity_kind: EntityKind,
        entity_id: str,
        updated: Mapping[str, Any],
        override_notice: Optional[str],
        stamp_submitted: bool,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """The exact response body, stored so a replay is byte-identical."""
        body: dict[str, Any] = {
            "ok": True,
            "entity_kind": entity_kind.value,
            "entity_id": entity_id,
            "status": updated["marketplace_status"],
        }
        if override_notice is not None:
            body["override_notice"] = override_notice
        if stamp_submitted:
            stamped = updated["marketplace_submitted_at"]
            body["submitted_at"] = stamped.isoformat() if stamped is not None else None
        if extra:
            body.update(dict(extra))
        return body

    @staticmethod
    def _side_effects(
        rule: TransitionRule, assistant_id: str, assistant_name: Optional[str], *, was_public: bool = False
    ) -> Optional[dict[str, Any]]:
        """Publish to, or remove from, the marketplace search index.

        Outside the transaction on purpose (§4.1): both calls are idempotent
        single-document operations, so re-issuing one on a replay is harmless
        and self-healing if the first attempt failed. A failure here does not
        undo the decision -- the decision is the record of truth and the index
        is a projection of it.

        It does, however, get **reported**. This method used to end in a bare
        `except Exception: logger.error` and return None, so a decision whose
        projection never landed produced a response byte-identical to one that
        worked. That is the delivery mechanism behind F-87: 219 assistants carry
        `is_global = true` while the index holds 19 documents, and no response,
        metric or status anywhere ever said so. `marketplace_index_sync` retries
        the call and hands back what happened; `decide()` puts it in the body.

        Returns None when the transition has no index side effect at all
        (SUBMIT of a row that was never public), so "nothing to do" stays distinguishable from "it worked".
        """
        deindex = rule.deindex or (rule.deindex_if_was_public and was_public)
        if not (rule.reindex or deindex):
            return None

        from codemie.service.marketplace_management import marketplace_index_sync

        if rule.reindex:
            outcome = marketplace_index_sync.index_assistant(assistant_id, is_update=True)
        else:
            outcome = marketplace_index_sync.remove_assistant(assistant_id, assistant_name)
        # CR-018: log failures explicitly so a failed ES write is never silently
        # swallowed when the outcome is serialised into the response body without
        # an upstream logger.error check (unlike the router path which checks
        # outcome.ok itself).
        if not outcome.ok:
            logger.warning(
                "marketplace index %s for %s did not succeed (state=%s, attempts=%s): %s",
                outcome.action,
                assistant_id,
                outcome.state,
                outcome.attempts,
                outcome.error,
            )
        return outcome.as_dict()

    @staticmethod
    def _with_indexing(result: dict[str, Any], indexing: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Attach the index outcome to the response without storing it.

        `result` is what `mm_review_decision.result` holds and what a replay
        returns; the index projection is a per-attempt fact, not a property of
        the decision, so it is merged into the copy that leaves the service and
        never into the copy that was persisted.
        """
        if indexing is None:
            return result
        return {**result, "indexing": indexing}

    @staticmethod
    def _record_activity_event(
        session,
        *,
        rule: TransitionRule,
        entity_kind: EntityKind,
        entity_id: str,
        actor: str,
        actor_id: Optional[str],
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Append this decision to `activity_events`, inside the caller's transaction.

        `actor_id` is the authenticated user's id and FKs `users.id`, which is
        the substantive difference from the table this replaces: a free-text
        `actor` could say anything and could not be joined to a person. It is
        still optional -- a decision taken by a background job has no user --
        so the human-readable `actor` string is preserved alongside it in
        `attributes.actor`, where it is a *record* rather than the identity.

        Only entity kinds that have an `ActivityEntityType` are given one;
        `entity_type` and `entity_id` are a validated pair (`ActivityEventCreate`
        rejects one without the other), so an unknown kind drops both rather
        than raising on a moderation path.
        """
        payload: dict[str, Any] = {"actor": actor}
        for key, value in (attributes or {}).items():
            if value is not None:
                payload[key] = value

        entity_type = ActivityEntityType.ASSISTANT if entity_kind is EntityKind.ASSISTANT else None
        activity_event_repository.insert(
            ActivityEventCreate(
                domain=ActivityDomain.MARKETPLACE,
                event_type=rule.activity_event_type,
                entity_type=entity_type,
                entity_id=entity_id if entity_type else None,
                actor_id=actor_id,
                attributes=payload,
            ),
            session,
        )
