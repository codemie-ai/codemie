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

from typing import Optional


from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import (
    Assistant,
)
from codemie.rest_api.security.user import User
from typing import Any
from codemie.service.marketplace_management.gate_errors import critical_gate_blocked_error


def _publish_gate_findings(assistant: Assistant) -> list[dict[str, Any]]:
    """Run the deterministic gates against the assistant being published.

    Deliberately reuses `Config.from_marketplace_management` and
    `entity_adapter.assistant_to_record`, the same two the batch auditor uses,
    so the publish gate and the audit cannot drift into disagreeing about the
    same assistant. Imported lazily: the validator package pulls in the
    similarity stack, which this module has no other reason to load.
    """
    from codemie.service.marketplace_management.entity_adapter import assistant_to_record
    from codemie.service.marketplace_management.validator import gates
    from codemie.service.marketplace_management.validator.config import Config as _GateConfig

    cfg = _GateConfig.for_assistant_gates()
    return [finding.to_dict() for finding in gates.run_all(assistant_to_record(assistant), cfg)]


def _enforce_publish_gates(
    assistant: Assistant, user: User, is_bypassing: bool, force_publish: bool = False
) -> list[dict[str, Any]]:
    """Block on CRITICAL, scope the waiver, and return what the reviewer sees.

    Four rules, in order:

    1. The gates always run. `ignore_recommendations` cannot skip them — that is
       F-83, where the flag meant "skip validation entirely" for anybody who
       could publish.
    2. OPTIONAL findings are advisory and anyone may waive them — that is the
       whole point of "advisory". Only waiving a CRITICAL finding is
       restricted: a non-admin who sends `ignore_recommendations` against a
       CRITICAL finding gets a 403 that still names the failing rules, so the
       refusal is actionable rather than merely a refusal.
    3. A CRITICAL finding is a 422 for everyone, waiver or not — *unless*
       `force_publish` is set. That flag is the one deliberate exception: the
       same override an admin/maintainer already has from the
       marketplace-management queue's Force-Publish action, offered here too
       so a blocked publish does not require a trip to the admin queue.
    4. Only OPTIONAL findings are advisory on the non-forced path; they are
       returned for acknowledgment either way.
    """
    findings = _publish_gate_findings(assistant)
    critical = [f for f in findings if f.get("severity") == "CRITICAL"]
    failing_rules = [f.get("rule") for f in critical]

    if is_bypassing and critical and not user.is_admin_or_maintainer:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=(
                "Publishing while ignoring a critical validation finding is restricted to "
                "administrators and maintainers."
            ),
            details={
                "assistant_id": assistant.id,
                "gate_findings": findings,
                "failed_rules": failing_rules,
            },
            help="Resolve the findings listed in details.gate_findings, or ask an administrator to review it.",
        )

    if critical and not force_publish:
        raise critical_gate_blocked_error(assistant.id, findings, critical)

    return findings


# The SUBMIT transition accepts DRAFT, REJECTED and WITHDRAWN
# (design-persistence.md §3.3).
_SUBMITTABLE_STATUSES = ("DRAFT", "REJECTED", "WITHDRAWN")


def _submit_for_review(
    assistant: Assistant,
    user: User,
    gate_findings: list[dict[str, Any]],
    request_id: Optional[str],
) -> Optional[str]:
    """Put the assistant in the moderation queue through the SUBMIT decision path.

    Not a field assignment: the same compare-and-set + `mm_review_decision`
    insert every other review decision goes through, so a submission is in the
    ledger, carries its gate findings, and emits a SUBMITTED event.

    `assistant.marketplace_status == "APPROVED"` is routed through RE_SUBMIT
    instead -- SUBMIT's `allowed_from` never included APPROVED, so re-publishing
    an already-approved assistant (e.g. to change categories) always CAS-conflicted
    with a 409 here, even though RE_SUBMIT already exists for exactly this
    APPROVED -> PENDING_REVIEW transition (used by the edit-triggered re-review path).
    """
    from codemie.service.marketplace_management.queue_service import (
        MarketplaceConflictError,
        MarketplaceQueueService,
    )

    try:
        if assistant.marketplace_status == "APPROVED":
            outcome = MarketplaceQueueService().re_submit(
                assistant_id=assistant.id,
                actor=user.name or user.username or user.id,
                decided_by_id=user.id,
                decided_by_email=user.email or user.username,
                request_id=request_id,
                gate_findings=gate_findings,
            )
            submitted_at = outcome.result.get("submitted_at")
            return submitted_at if isinstance(submitted_at, str) else None
        submitted_at = MarketplaceQueueService().submit(
            assistant_id=assistant.id,
            actor=user.name or user.username or user.id,
            decided_by_id=user.id,
            decided_by_email=user.email or user.username,
            request_id=request_id,
            gate_findings=gate_findings,
            submitted_by=user.name or user.username,
        )
    except MarketplaceConflictError as conflict:
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message=str(conflict),
            details=conflict.to_detail(),
            help=_RELOAD_AND_RETRY_HELP,
        )
    return submitted_at.isoformat() if submitted_at else None


_DEFAULT_ADMIN_SKIP_QUEUE_NOTICE = (
    "Published directly by an administrator or maintainer; administrators and maintainers skip the moderation queue."
)

_RELOAD_AND_RETRY_HELP = "Reload the assistant and try again."


def _validate_override_notice(override_notice: Optional[str]) -> str:
    """Delegates to the marketplace-management queue's one override-reason rule.

    This entry point calls the same queue-service transition Force-Publish
    does, so it has to apply the same rule. It used to re-implement it, and the
    copies had already drifted: the identical length check answered 422 here and
    400 from the admin queue (MR !4070 review comment #12). Imported lazily to
    keep the two routers free of an import-time dependency on each other.
    """
    from codemie.rest_api.routers.marketplace_management import _validate_reason

    return _validate_reason(override_notice or "", field="override_notice")


def _force_publish_assistant(assistant: Assistant, user: User, override_notice: str, request_id: Optional[str]) -> None:
    """Publish straight to APPROVED, over the review gate.

    Routes through `MarketplaceQueueService.force_publish` — the exact
    transition the marketplace-management queue's Force-Publish action uses —
    so both entry points share one CAS update, one audit trail
    (`mm_review_decision` + activity event), and one indexing path
    (queue_service `TransitionRule.reindex`) instead of two implementations
    that could drift.
    """
    from codemie.service.marketplace_management.queue_service import (
        MarketplaceConflictError,
        MarketplaceQueueService,
    )

    try:
        MarketplaceQueueService().force_publish(
            assistant_id=assistant.id,
            actor=user.name or user.username or user.id,
            override_notice=override_notice,
            decided_by_id=user.id,
            decided_by_email=user.email or user.username,
            request_id=request_id,
        )
    except MarketplaceConflictError as conflict:
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message=str(conflict),
            details=conflict.to_detail(),
            help=_RELOAD_AND_RETRY_HELP,
        )
