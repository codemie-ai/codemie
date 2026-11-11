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

from typing import Literal, Optional, List

from pydantic import BaseModel, ConfigDict, Field

from fastapi import APIRouter, status, Request, Depends, BackgroundTasks

from codemie.configs import logger, config
from codemie.configs.logger import set_logging_info
from codemie.core.ability import Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
)
from codemie.rest_api.models.assistant import (
    Assistant,
    PublishValidationResponse,
    InlineCredential,
    SubAssistantPublishSettings,
    PublishValidationErrorResponse,
)
from codemie.service.assistant_generator_service import AssistantGeneratorService
from codemie.service.assistant.inline_credentials import validate_assistant_inline_integrations
from codemie.rest_api.routers.assistant._shared import (
    _index_marketplace_assistant,
    _raise_assistant_operation_error,
    _track_assistant_management_metric,
    _validate_remote_entities_and_raise,
)
from codemie.rest_api.routers.assistant._sub_assistants import _publish_sub_assistants, _validate_publish_marketplace
from codemie.rest_api.routers.utils import check_user_can_access_assistant, get_assistant_by_id_or_raise
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_user_interaction_service import assistant_user_interaction_service
import asyncio
from typing import Any
from fastapi.exceptions import RequestValidationError
from codemie.rest_api.routers.assistant._publish_support import (
    _DEFAULT_ADMIN_SKIP_QUEUE_NOTICE,
    _RELOAD_AND_RETRY_HELP,
    _SUBMITTABLE_STATUSES,
    _enforce_publish_gates,
    _force_publish_assistant,
    _publish_gate_findings,
    _submit_for_review,
    _validate_override_notice,
)
from codemie.rest_api.routers.assistant._shared import _remove_marketplace_assistant
from codemie.service.marketplace_management.gate_errors import critical_gate_blocked_error
from codemie.configs.marketplace_management import is_approval_gate_armed


router = APIRouter()


class ReactionRequest(BaseModel):
    """Request model for assistant reactions"""

    reaction: Literal["like", "dislike"]


class PublishToMarketplaceRequest(BaseModel):
    """Request model for publishing assistant to marketplace"""

    categories: list[str] = Field(
        min_length=1, max_length=3, description="Category IDs for marketplace classification (1-3 required)"
    )
    sub_assistants_settings: Optional[List[SubAssistantPublishSettings]] = Field(
        default_factory=list,
        description="Settings for each sub-assistant including marketplace visibility (is_global), "
        "toolkits, mcp_servers, and categories",
    )
    ignore_recommendations: bool = Field(
        default=False,
        description="If true, bypasses all validation recommendations and publishes anyway. "
        "This should only be set when user explicitly clicks 'Publish Anyway' button.",
    )
    force_publish: bool = Field(
        default=False,
        description="Administrators/maintainers only: bypass a CRITICAL gate block and publish "
        "straight to APPROVED/Verified, same as the marketplace-management queue's Force-Publish "
        "action. Only set when the user explicitly clicks 'Force-Publish'. Requires override_notice.",
    )
    override_notice: Optional[str] = Field(
        default=None,
        description="Justification for force_publish, persisted on the review-decision record. "
        "Required (20-2000 chars) when force_publish is true.",
    )


class GateFinding(BaseModel):
    """One deterministic gate finding, as the publish endpoint reports it.

    Mirrors `validator.models.Finding.to_dict()` field for field so the wire
    shape and the engine's shape cannot drift.
    """

    model_config = ConfigDict(extra="ignore")

    rule: str
    severity: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class PublishToMarketplaceResponse(BaseModel):
    """Result of a publish attempt.

    Under the approval gate a publish is a *submission*, so the caller needs
    more than a sentence: which state the assistant landed in, when it entered
    the queue, and what the deterministic gates found (OPTIONAL findings are
    advisory and are returned for acknowledgment rather than blocking).
    `message` keeps its existing meaning and position for existing clients.
    """

    message: str
    marketplace_status: Optional[str] = None
    submitted_at: Optional[str] = None
    gate_findings: list[GateFinding] = Field(default_factory=list)
    acknowledged: bool = False


class GateCheckRequest(BaseModel):
    """Request body for the gate-check dry-run endpoint.

    Same shape as PUT /v1/assistants/{id} but `categories` is required so the
    gate engine can evaluate category-dependent rules against unsaved values.
    All text fields are required so the gate evaluates the actual unsaved form
    values, not whatever happens to be in the DB.
    """

    categories: list[str] = Field(min_length=1, max_length=3)
    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None


class GateCheckResponse(BaseModel):
    """Result of a side-effect-free gate evaluation."""

    passed: bool
    gate_findings: list[GateFinding] = Field(default_factory=list)


@router.post(
    "/assistants/{assistant_id}/reactions",
    status_code=status.HTTP_200_OK,
)
def react_to_assistant(assistant_id: str, request: ReactionRequest, user: User = Depends(authenticate)):
    """
    React to an assistant with like or dislike.
    If the user already has the opposite reaction, it will be removed.
    """
    # Check if assistant exists
    get_assistant_by_id_or_raise(assistant_id)

    # Use service to handle the reaction
    return assistant_user_interaction_service.manage_reaction(assistant_id, user.id, request.reaction)


@router.delete(
    "/assistants/{assistant_id}/reactions",
    status_code=status.HTTP_200_OK,
)
def remove_assistant_reactions(assistant_id: str, user: User = Depends(authenticate)):
    """
    Remove all reactions (likes/dislikes) from an assistant for the current user.
    """
    # Check if assistant exists
    get_assistant_by_id_or_raise(assistant_id)

    return assistant_user_interaction_service.remove_reactions(assistant_id, user.id)


@router.post(
    "/assistants/{assistant_id}/marketplace/publish/validate",
    status_code=status.HTTP_200_OK,
    response_model=PublishValidationResponse,
)
def validate_assistant_for_marketplace(assistant_id: str, user: User = Depends(authenticate)):
    """
    Validate an assistant before publishing to the marketplace.
    Checks for inline integration credentials in the main assistant and all sub-assistants.
    Returns validation results and information about sub-assistants that will be published.
    """
    # Get the assistant
    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "write", Action.WRITE)
    _validate_remote_entities_and_raise(assistant)

    # Check for inline integration credentials in main assistant
    validation_result = validate_assistant_inline_integrations(assistant)
    all_inline_credentials = validation_result["inline_credentials"]
    prompt_variables = validation_result.get("prompt_variables", [])

    # Get sub-assistants information and check their inline credentials
    sub_assistants_info = []
    if assistant.assistant_ids:
        for sub_id in assistant.assistant_ids:
            sub_assistant = get_assistant_by_id_or_raise(sub_id)

            # Validate sub-assistant inline integrations
            sub_validation_result = validate_assistant_inline_integrations(sub_assistant)

            # Add sub-assistant name to each credential for better UI display
            for credential in sub_validation_result["inline_credentials"]:
                # Create a new credential with sub-assistant context
                credential_dict = credential.model_dump() if hasattr(credential, 'model_dump') else credential
                credential_dict["sub_assistant_name"] = sub_assistant.name
                credential_dict["sub_assistant_id"] = sub_assistant.id
                all_inline_credentials.append(InlineCredential(**credential_dict))

            sub_assistants_info.append(
                {
                    "id": sub_assistant.id,
                    "name": sub_assistant.name,
                    "description": sub_assistant.description,
                    "is_global": sub_assistant.is_global,
                    "icon_url": sub_assistant.icon_url,
                    "categories": sub_assistant.categories or [],
                }
            )

    # Determine if validation is valid (no inline credentials found)
    is_valid = len(all_inline_credentials) == 0

    if not is_valid:
        response = PublishValidationResponse(
            requires_confirmation=True,
            message="This assistant or its sub-assistants contain inline integration credentials that will be "
            "shared with all users who use this assistant. Please confirm that you want to publish it.",
            inline_credentials=all_inline_credentials,
            assistant_id=assistant_id,
            sub_assistants=sub_assistants_info,
            prompt_variables=prompt_variables,
        )
    else:
        message = f"Assistant {assistant_id} is ready to be published to marketplace"
        if sub_assistants_info:
            message += f" along with {len(sub_assistants_info)} sub-assistant(s)"
        response = PublishValidationResponse(
            requires_confirmation=False,
            message=message,
            inline_credentials=[],
            assistant_id=assistant_id,
            sub_assistants=sub_assistants_info,
            prompt_variables=prompt_variables,
        )

    return response


# ---------------------------------------------------------------------------
# Publish-time approval gate (EXECUTION-PLAN 7.1, 7.6, 7.7 — F-01, F-02, F-82, F-83)
# ---------------------------------------------------------------------------
#
# The eight deterministic gates in
# `service/marketplace_management/validator/gates.py` were correct and had
# exactly one caller: the batch auditor. Spec P1-01 puts them *at publish*, and
# P1-05 makes a CRITICAL finding a block with an actionable message. Everything
# below is that wiring, plus the submission it gates.


def _check_force_publish_allowed(
    assistant: Assistant, user: User, force_publish: bool, override_notice: Optional[str]
) -> None:
    """Guard `force_publish`: admin/maintainer only, and a valid reason."""
    if not force_publish:
        return
    if not user.is_admin_or_maintainer:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Force-publishing over the review gate is restricted to administrators and maintainers.",
            details={"assistant_id": assistant.id},
            help="Ask an administrator or maintainer to force-publish, or resolve the gate findings and publish again.",
        )
    _validate_override_notice(override_notice)


def _run_admin_skip_queue_publish(
    assistant: Assistant,
    user: User,
    force_publish: bool,
    override_notice: Optional[str],
    request_id: Optional[str],
    background_tasks: BackgroundTasks,
) -> None:
    """The `admin_skip_queue` branch of the publish handler, pulled out to keep
    that handler's branching readable: persist the in-memory changes made
    before the transaction (categories), force-publish, and reflect the
    outcome back onto `assistant` for the response body."""
    # The queue-service CAS only touches the marketplace columns, not
    # `categories`, so persist that separately first. Refresh the marketplace
    # columns from the DB first so this merge can't revert a concurrent
    # moderation decision with the stale values `assistant` was loaded with.
    assistant.refresh_marketplace_columns_from_db()

    # FORCE_PUBLISH's `allowed_from` never included APPROVED -- it is a
    # transition *into* APPROVED, not a no-op on an assistant already there.
    # An admin/maintainer re-publishing an already-approved assistant (e.g. to
    # change categories) has nothing to transition: persist the metadata
    # change and reindex directly, the same way the gate-disabled path does,
    # instead of CAS-conflicting with a 409 against FORCE_PUBLISH.
    if assistant.marketplace_status == "APPROVED":
        assistant.update(refresh=True)
        background_tasks.add_task(_index_marketplace_assistant, assistant_id=assistant.id, user=user, is_update=True)
        logger.info(
            "Scheduled background re-indexing after metadata change (already APPROVED, admin/maintainer)",
            extra={"assistant_id": assistant.id},
        )
        return

    assistant.update(refresh=True)

    notice = _validate_override_notice(override_notice) if force_publish else _DEFAULT_ADMIN_SKIP_QUEUE_NOTICE
    _force_publish_assistant(assistant, user, notice, request_id)
    assistant.marketplace_status = "APPROVED"
    assistant.is_global = True

    logger.info(
        f"Assistant {assistant.id} force-published to marketplace (admin/maintainer skip-queue)",
        extra={"assistant_id": assistant.id, "forced": force_publish},
    )


# EPMCDME-14999: side-effect-free gate evaluation against unsaved form values
@router.post(
    "/assistants/{assistant_id}/marketplace/publish/gate-check",
    status_code=status.HTTP_200_OK,
    response_model=GateCheckResponse,
)
def gate_check_assistant_for_marketplace(
    assistant_id: str,
    request: GateCheckRequest,
    raw_request: Request,
    user: User = Depends(authenticate),
) -> GateCheckResponse:
    """Run the full publish validation pipeline against unsaved request body values.

    Mirrors the publish endpoint's validation order exactly — deterministic gates
    first, then LLM quality assessment — so callers get a true preview of whether
    publish will succeed. No side effects: does not persist, queue, or change
    marketplace_status.
    """
    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "write", Action.WRITE)

    # Always override gate-relevant fields with the unsaved form values so the
    # gate engine evaluates what the user is about to save, not what is in the DB.
    assistant.name = request.name
    assistant.description = request.description
    assistant.system_prompt = request.system_prompt
    assistant.categories = request.categories

    if is_approval_gate_armed():
        findings = _publish_gate_findings(assistant)
        critical = [f for f in findings if f.get("severity") == "CRITICAL"]

        if critical:
            # Same error the real publish raises, built by the same helper, so
            # this preflight cannot disagree with the endpoint it previews.
            raise critical_gate_blocked_error(assistant.id, findings, critical)
    else:
        findings = []

    if config.MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED:
        # Dry-run: no bypass is allowed — always validate as a regular user would.
        _llm_validate_on_marketplace_publish(raw_request, assistant, user, is_bypassing=False)

    return GateCheckResponse(passed=True, gate_findings=[GateFinding(**f) for f in findings])


def _execute_publish(
    assistant: Assistant,
    user: User,
    background_tasks: BackgroundTasks,
    *,
    admin_skip_queue: bool,
    gate_armed: bool,
    force_publish: bool,
    override_notice: Optional[str],
    request_id: Optional[str],
    gate_findings: list[dict[str, Any]],
    ignored_recommendations: bool,
) -> Optional[str]:
    """Apply the publish (direct, queued or ungated) and track it. Returns `submitted_at` if queued."""
    submitted_at: Optional[str] = None
    try:
        if admin_skip_queue:
            _run_admin_skip_queue_publish(assistant, user, force_publish, override_notice, request_id, background_tasks)
        elif gate_armed:
            submitted_at = _submit_assistant_for_review(assistant, user, gate_findings, request_id)
        else:
            _publish_with_gate_disabled(assistant, user, background_tasks)

        # Track metrics with bypass flag as attribute
        _track_assistant_management_metric(
            "publish_to_marketplace",
            assistant,
            user,
            True,
            {"published_with_ignored_recommendations": ignored_recommendations},
        )
    except ExtendedHTTPException:
        raise
    except Exception as e:
        _track_assistant_management_metric(
            "publish_to_marketplace", assistant, user, False, {"error_class": e.__class__.__name__}
        )
        _raise_assistant_operation_error("publish to marketplace", e)
    return submitted_at


def _require_publish_body(request: Optional[PublishToMarketplaceRequest]) -> None:
    """F-86: the body is optional in the signature but mandatory in fact — the
    first thing the handler does is read `request.categories`. Say so as a
    422 naming the missing field instead of an AttributeError and a 500."""
    if request is None:
        raise RequestValidationError(
            [
                {
                    "type": "missing",
                    "loc": ("body", "categories"),
                    "msg": "Field required",
                    "input": None,
                }
            ]
        )


def _submit_assistant_for_review(
    assistant: Assistant, user: User, gate_findings: list[dict[str, Any]], request_id: Optional[str]
) -> Optional[str]:
    """Gate armed, non-admin publish: submit to the moderation queue. Returns `submitted_at`."""
    # A row that has never been published but still carries the legacy
    # `marketplace_status` value of 'APPROVED' (F-53) is, in truth, a
    # draft; SUBMIT would otherwise 409 against its own transition table
    # on the very first publish.
    #
    # This is now a *legacy-row* guard, not the normal path: 7.2
    # (`p7r2i3v4b5f6`) backfilled the stored data and 12.3
    # (`d3r4a5f6t7d8`) fixed the default that kept producing it, so a
    # newly created assistant arrives here already DRAFT. It is kept
    # because the 12.3 backfill is deliberately narrow -- it claims only
    # rows with `is_global IS FALSE` and no submission or decision
    # history -- and any private APPROVED row outside that predicate
    # would otherwise be permanently unpublishable.
    # Refresh first so the two fields set below are the only marketplace
    # columns this merge changes — otherwise the merge would also revert any
    # concurrent moderation decision to the stale values `assistant` was
    # loaded with (e.g. version/decided_at/decided_by/reason).
    assistant.refresh_marketplace_columns_from_db()
    if not assistant.is_global and assistant.marketplace_status not in _SUBMITTABLE_STATUSES:
        assistant.marketplace_status = "DRAFT"
    # `is_global` is deliberately NOT cleared here: SUBMIT's compare-and-set makes the
    # row private and de-indexes it if it was public, atomically. Clearing it first
    # would hide a grandfathered public draft before the CAS, leave it in the index
    # (the queue service could no longer see it was public), and strand it private
    # if the SUBMIT then 409ed.
    assistant.update(refresh=True)

    submitted_at = _submit_for_review(assistant, user, gate_findings, request_id)
    # Reflect what the compare-and-set wrote, for the response body.
    assistant.marketplace_status = "PENDING_REVIEW"
    assistant.is_global = False

    # No marketplace indexing here, on purpose: an unapproved assistant
    # that is findable in marketplace search is a published assistant.
    # `approve` indexes it (queue_service `TransitionRule.reindex`).
    logger.info(
        f"Assistant {assistant.id} submitted for marketplace review",
        extra={"assistant_id": assistant.id},
    )
    return submitted_at


def _publish_with_gate_disabled(assistant: Assistant, user: User, background_tasks: BackgroundTasks) -> None:
    """Gate disabled: publish straight to APPROVED and index in the background."""
    # CR-013: gate is disabled — no MarketplaceReviewDecision row is
    # written on this path (the queue service's CAS relies on the gate
    # being armed to own the state machine).  Emit a structured warning
    # so the bypass is visible in logs and audit tooling.
    _actor = getattr(user, "email", None) or getattr(user, "name", None) or str(getattr(user, "id", "unknown"))
    logger.warning(
        "marketplace gate bypassed — no audit row written",
        extra={
            "assistant_id": assistant.id,
            "actor": _actor,
            "reason": "gate disabled by MARKETPLACE_APPROVAL_GATE_ENABLED=False",
        },
    )
    assistant.refresh_marketplace_columns_from_db()
    assistant.is_global = True
    assistant.marketplace_status = "APPROVED"
    assistant.update(refresh=True)

    # Schedule background task for indexing into marketplace
    background_tasks.add_task(
        _index_marketplace_assistant,
        assistant_id=assistant.id,
        user=user,
    )
    logger.info(
        f"Scheduled background indexing for published assistant {assistant.id}",
        extra={"assistant_id": assistant.id},
    )


def _publish_success_message(
    assistant_id: str, *, submitted_for_review: bool, total_sub_count: int, published_sub_count: int
) -> str:
    if submitted_for_review:
        message = f"Assistant {assistant_id} submitted for marketplace review"
    else:
        message = f"Assistant {assistant_id} published to marketplace successfully"
    if total_sub_count <= 0:
        return message
    private_sub_count = total_sub_count - published_sub_count
    if published_sub_count > 0 and private_sub_count > 0:
        return (
            f"{message} along with {published_sub_count} sub-assistant(s) published to marketplace "
            f"and {private_sub_count} kept private"
        )
    if published_sub_count > 0:
        return f"{message} along with {published_sub_count} sub-assistant(s) published to marketplace"
    return f"{message} with {total_sub_count} sub-assistant(s) kept private"


@router.post(
    "/assistants/{assistant_id}/marketplace/publish",
    status_code=status.HTTP_200_OK,
    response_model=PublishToMarketplaceResponse,
)
def publish_assistant_to_marketplace(
    assistant_id: str,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    request: Optional[PublishToMarketplaceRequest] = None,
    user: User = Depends(authenticate),
):
    """
    Publish an assistant to the marketplace, including any sub-assistants.

    When an assistant has sub-assistants, this endpoint will:
    1. Process all sub-assistants with their individual settings (toolkits, mcp_servers, categories, is_global)
    2. Publish sub-assistants to marketplace or keep them private based on is_global flag:
       - is_global=True: Sub-assistant visible in marketplace and usable by orchestrator
       - is_global=False: Sub-assistant kept private (not in marketplace) but still usable by orchestrator
    3. Finally publish the orchestrator assistant to the marketplace

    Anyone with write access to the assistant may publish it; this is not an
    administrator-only endpoint. (The docstring used to claim it was, while
    `_validate_publish_marketplace` only ever checked write access — F-02.)

    When the approval gate is armed (`is_approval_gate_armed()`), publishing is a *submission*:
    the deterministic gates run first, a CRITICAL finding is a 422, and the
    assistant lands in the moderation queue as PENDING_REVIEW and private until
    a moderator approves it.
    """

    _require_publish_body(request)

    # Get the assistant
    assistant = get_assistant_by_id_or_raise(assistant_id)
    _validate_publish_marketplace(assistant, user, request.categories)

    # Set LiteLLM context with user's credentials
    from codemie.service.llm_service.utils import set_llm_context

    set_llm_context(assistant, None, user)

    # The categories being published with are part of what the gates judge
    # (`at_least_one_category`), so apply them before anything validates.
    if request.categories:
        assistant.categories = request.categories

    gate_armed = is_approval_gate_armed()
    is_bypassing = bool(request.ignore_recommendations)
    force_publish = bool(request.force_publish)
    _check_force_publish_allowed(assistant, user, force_publish, request.override_notice)

    # Admins/maintainers skip the moderation queue entirely: their own publish
    # lands straight on APPROVED/Verified, the same outcome Force-Publish
    # produces for a blocked one. `force_publish` additionally overrides a
    # CRITICAL gate block; a plain admin/maintainer publish still respects it.
    admin_skip_queue = gate_armed and user.is_admin_or_maintainer

    # `force_publish` is already admin/maintainer-only (`_check_force_publish_allowed`
    # above), so by this point `force_publish=True` implies an admin/maintainer caller.
    # OPTIONAL/advisory findings are waivable by anyone (comment #7) — only overriding a
    # CRITICAL deterministic-gate finding without `force_publish` is admin-restricted,
    # and `_enforce_publish_gates` enforces that on its own.
    waiver_applies = is_bypassing or force_publish
    gate_findings: list[dict[str, Any]] = []
    acknowledged = False

    if gate_armed:
        gate_findings = _enforce_publish_gates(assistant, user, is_bypassing, force_publish=force_publish)
        acknowledged = bool(gate_findings) and waiver_applies

    # Track if user bypassed recommendations (for ES analytics)
    published_with_bypass = False
    if config.MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED:
        # The LLM quality check is advisory, like the OPTIONAL deterministic
        # gate findings above — anyone requesting `ignore_recommendations` (or
        # whom `force_publish` already proved is an admin/maintainer) may
        # waive it, not just admins/maintainers waiving on their own.
        published_with_bypass = _llm_validate_on_marketplace_publish(raw_request, assistant, user, waiver_applies)

    # Handle sub-assistants publishing
    total_sub_count = len(assistant.assistant_ids or [])
    published_sub_count = 0
    if total_sub_count:
        published_sub_count = _publish_sub_assistants(
            assistant=assistant,
            sub_assistants_settings=request.sub_assistants_settings,
            user=user,
            gate_armed=gate_armed,
            admin_skip_queue=admin_skip_queue,
            request_id=getattr(raw_request.state, "uuid", None),
        )

    submitted_at = _execute_publish(
        assistant,
        user,
        background_tasks,
        admin_skip_queue=admin_skip_queue,
        gate_armed=gate_armed,
        force_publish=force_publish,
        override_notice=request.override_notice,
        request_id=getattr(raw_request.state, "uuid", None),
        gate_findings=gate_findings,
        ignored_recommendations=published_with_bypass or acknowledged,
    )

    message = _publish_success_message(
        assistant_id,
        submitted_for_review=gate_armed and not admin_skip_queue,
        total_sub_count=total_sub_count,
        published_sub_count=published_sub_count,
    )

    return PublishToMarketplaceResponse(
        message=message,
        marketplace_status=assistant.marketplace_status,
        submitted_at=submitted_at,
        gate_findings=gate_findings,
        acknowledged=acknowledged,
    )


@router.post(
    "/assistants/{assistant_id}/marketplace/unpublish",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def unpublish_assistant_from_marketplace(
    assistant_id: str, background_tasks: BackgroundTasks, user: User = Depends(authenticate)
):
    """
    Unpublish an assistant from the marketplace through the WITHDRAW decision path.

    Not a field assignment: WITHDRAW's `allowed_from` is restricted to
    `(PENDING_REVIEW, APPROVED)`, plus `DRAFT` while `is_global` (assistants
    that were public before the migration), so a private `DRAFT` or a
    `REJECTED` assistant (never public, nothing to unpublish) 409s here instead of silently becoming
    `WITHDRAWN`. The CAS also bumps `marketplace_version`, clears
    `is_verified`, and writes the same `mm_review_decision` ledger row and
    `ASSISTANT_WITHDRAWN` event every other moderation decision does, so a
    user-initiated unpublish is visible in the audit trail and a gate-off
    re-publish afterwards no longer silently regains the Verified badge.
    """
    from codemie.service.marketplace_management.queue_service import (
        MarketplaceConflictError,
        MarketplaceQueueService,
    )

    # Get the assistant
    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "write", Action.WRITE)

    try:
        MarketplaceQueueService().withdraw(
            assistant_id=assistant.id,
            actor=user.name or user.username or user.id,
            decided_by_id=user.id,
            decided_by_email=user.email or user.username,
        )

        # Schedule background task for removing from marketplace
        background_tasks.add_task(
            _remove_marketplace_assistant,
            assistant_id=assistant_id,
            assistant_name=assistant.name,
            user=user,
        )
        logger.info(
            f"Scheduled background removal for unpublished assistant {assistant_id}",
            extra={"assistant_id": assistant_id},
        )

        _track_assistant_management_metric("unpublish_from_marketplace", assistant, user, True)
    except MarketplaceConflictError as conflict:
        _track_assistant_management_metric(
            "unpublish_from_marketplace", assistant, user, False, {"error_class": "MarketplaceConflictError"}
        )
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message=str(conflict),
            details=conflict.to_detail(),
            help=_RELOAD_AND_RETRY_HELP,
        )
    except Exception as e:
        _track_assistant_management_metric(
            "unpublish_from_marketplace", assistant, user, False, {"error_class": e.__class__.__name__}
        )
        _raise_assistant_operation_error("unpublish from marketplace", e)

    return BaseResponse(message=f"Assistant {assistant_id} unpublished from marketplace successfully")


# The failure modes of the advisory publish-time quality check that are the
# *upstream's* fault rather than this service's (F-22). Deliberately a named,
# narrow tuple: catching `Exception` here would turn a real bug in the
# validation code into a soothing "try again later" that nobody investigates.
_LLM_UNAVAILABLE_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,
    asyncio.TimeoutError,
    ConnectionError,
)

try:  # pragma: no cover - httpx is always installed; the guard is for slim builds
    import httpx as _httpx

    _LLM_UNAVAILABLE_ERRORS = _LLM_UNAVAILABLE_ERRORS + (
        _httpx.TimeoutException,
        _httpx.TransportError,
    )
except Exception:  # noqa: BLE001
    pass


def _llm_validate_on_marketplace_publish(
    raw_request: Request, assistant: Assistant, user: User, is_bypassing: bool
) -> bool:
    """
    Validates if an assistant can be published to the marketplace.
    Handles bypass logic if user chose to ignore recommendations.
    Raises exception if validation fails and user didn't bypass.

    Args:
        raw_request: The FastAPI request object
        assistant: The assistant to validate
        user: The user publishing the assistant
        is_bypassing: True if user chose to bypass validation recommendations

    Returns:
        True if user bypassed recommendations (for metrics tracking), False otherwise
    """
    # If user already chose to bypass, skip validation entirely
    if is_bypassing:
        logger.info(f"User {user.id} bypassed recommendations for assistant {assistant.id}")
        return True

    request_id = raw_request.state.uuid if hasattr(raw_request.state, "uuid") else None

    # Set user context for LLM budget tracking during validation workflow
    username = user.username or user.email or None
    set_logging_info(
        uuid=request_id or "unknown",
        user_id=user.id,
        conversation_id=request_id or "unknown",
        username=username,
    )
    logger.debug(
        f"Set user context for assistant validation: "
        f"username={username}, assistant_id={assistant.id}, request_id={request_id}"
    )

    try:
        quality_validation_result = AssistantGeneratorService.validate_assistant_for_publish(
            assistant=assistant, user=user, request_id=request_id
        )
    except _LLM_UNAVAILABLE_ERRORS as exc:
        # F-22: the advisory quality check is an *upstream dependency*. When it
        # times out or drops the stream, that is not a defect in this service
        # and the caller must be told which it is: a 504/503 is retryable and
        # actionable, a 500 is neither and used to arrive carrying a stack
        # trace. Nothing has been written at this point in the handler, so the
        # publish is refused whole rather than half-applied.
        timed_out = isinstance(exc, (TimeoutError, asyncio.TimeoutError))
        logger.warning(
            f"Assistant quality validation is unavailable for {assistant.id}: {exc.__class__.__name__}: {exc}",
            extra={"assistant_id": assistant.id},
        )
        raise ExtendedHTTPException(
            code=(status.HTTP_504_GATEWAY_TIMEOUT if timed_out else status.HTTP_503_SERVICE_UNAVAILABLE),
            message=(
                "The assistant quality check timed out and the assistant was not published."
                if timed_out
                else "The assistant quality check is temporarily unavailable and the assistant was not published."
            ),
            details={"assistant_id": assistant.id, "reason": exc.__class__.__name__},
            help=(
                "Nothing was changed. Try publishing again in a moment, or publish with "
                "'Publish Anyway' if you are an administrator and accept the advisory findings."
            ),
        ) from exc

    recommendations = quality_validation_result.recommendations
    has_optional_findings = bool(
        recommendations and (recommendations.fields or recommendations.toolkits or recommendations.context)
    )
    if quality_validation_result.decision == "accept" and not has_optional_findings:
        return False

    # Validation failed, or accepted with OPTIONAL findings the user has to acknowledge
    logger.info(
        f"Assistant publication blocked due to quality validation failure. "
        f"Assistant ID: {assistant.id}, User ID: {user.id}, Username: {username}, "
        f"Decision: {quality_validation_result.decision}, "
    )
    raise ExtendedHTTPException(
        code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message="Assistant quality validation failed. Please improve the assistant before publishing.",
        details=PublishValidationErrorResponse(
            requires_confirmation=True,
            assistant_id=assistant.id,
            quality_validation=quality_validation_result,
        ).model_dump(),
        help=(
            "We recommend using the 'Refine with AI' feature to enhance your assistant's quality. "
            "This will help resolve the validation failures and allow for successful publishing."
        ),
    )
