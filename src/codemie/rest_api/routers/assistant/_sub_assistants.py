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

from typing import Optional, List

from fastapi import status

from codemie.configs import logger
from codemie.core.ability import Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import Assistant, SubAssistantPublishSettings
from codemie.rest_api.routers.assistant._shared import (
    _track_assistant_management_metric,
    _validate_remote_entities_and_raise,
)
from codemie.rest_api.routers.utils import check_user_can_access_assistant, get_assistant_by_id_or_raise
from codemie.rest_api.security.user import User
from codemie.service.assistant.category_service import category_service
from codemie.rest_api.routers.assistant._publish_support import (
    _DEFAULT_ADMIN_SKIP_QUEUE_NOTICE,
    _SUBMITTABLE_STATUSES,
    _force_publish_assistant,
    _publish_gate_findings,
    _submit_for_review,
)


def _validate_sub_assistant(sub_id: str, assistant: Assistant, user: User) -> tuple[str, Assistant]:
    """
    Validate a single sub-assistant for publishing.

    Args:
        sub_id: ID of the sub-assistant to validate
        assistant: The parent assistant
        user: The user performing the operation

    Returns:
        Tuple of (sub_id, sub_assistant)

    Raises:
        ExtendedHTTPException: If validation fails
    """
    try:
        sub_assistant = get_assistant_by_id_or_raise(sub_id)
        check_user_can_access_assistant(user, sub_assistant, "write", Action.WRITE)
        return (sub_id, sub_assistant)

    except Exception as e:
        logger.error(
            f"Failed to validate sub-assistant {sub_id} for publishing: {str(e)}",
            extra={"parent_assistant_id": assistant.id, "sub_assistant_id": sub_id},
            exc_info=True,
        )
        _track_assistant_management_metric(
            "publish_sub_assistant_to_marketplace",
            sub_assistant if 'sub_assistant' in locals() else None,
            user,
            False,
            {"error_class": e.__class__.__name__, "parent_assistant_id": assistant.id, "phase": "validation"},
        )
        sub_name = sub_assistant.name if 'sub_assistant' in locals() else sub_id
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=f"Failed to validate sub-assistant '{sub_name}' ({sub_id})",
            details=f"An error occurred while validating sub-assistant: {str(e)}",
            help="Please check that all sub-assistants exist and are accessible. "
            "Ensure you have write permissions for all sub-assistants.",
        ) from e


def _reject_unreviewed_sub_assistant_changes(
    sub_assistant: Assistant, settings: Optional[SubAssistantPublishSettings]
) -> None:
    """Refuse toolkit/MCP/category changes to a sub-assistant that is APPROVED or under review.

    Those edits would bypass moderation: an approved sub-assistant would stay Verified
    with different tools, and a pending one would be approved on content the reviewer
    never saw. The owner must edit the sub-assistant directly, which re-submits it.
    """
    if settings is None or sub_assistant.marketplace_status not in ("APPROVED", "PENDING_REVIEW"):
        return

    def dump(items):
        return [i.model_dump() if hasattr(i, 'model_dump') else i for i in items or []]

    changed = (
        (settings.toolkits is not None and dump(settings.toolkits) != dump(sub_assistant.toolkits))
        or (settings.mcp_servers is not None and dump(settings.mcp_servers) != dump(sub_assistant.mcp_servers))
        or (bool(settings.categories) and list(settings.categories) != list(sub_assistant.categories or []))
    )
    if changed:
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message=f"Sub-assistant '{sub_assistant.name}' ({sub_assistant.id}) is {sub_assistant.marketplace_status}",
            details="Toolkits, MCP servers and categories of a reviewed sub-assistant cannot be changed "
            "through the parent's publish settings.",
            help="Edit the sub-assistant directly; changes to an approved sub-assistant are sent for re-review.",
        )


def _apply_sub_assistant_settings(sub_assistant: Assistant, settings: Optional[SubAssistantPublishSettings]) -> bool:
    """
    Apply settings to a sub-assistant.

    Args:
        sub_assistant: The sub-assistant to update
        settings: Settings to apply (can be None)

    Returns:
        The is_global value to use (True by default)
    """
    is_global = True  # Default: make sub-assistant visible in marketplace

    if settings is None:
        return is_global

    # Override is_global if explicitly provided in settings
    is_global = settings.is_global

    # Update toolkits if provided
    if settings.toolkits is not None:
        sub_assistant.toolkits = settings.toolkits

    # Update MCP servers if provided
    if settings.mcp_servers is not None:
        sub_assistant.mcp_servers = settings.mcp_servers

    # Update categories if provided
    if settings.categories:
        category_service.validate_category_ids(settings.categories, required=True)
        sub_assistant.categories = settings.categories

    return is_global


def _publish_single_sub_assistant(
    sub_id: str,
    sub_assistant: Assistant,
    settings_map: dict,
    assistant: Assistant,
    user: User,
    gate_armed: bool,
    admin_skip_queue: bool,
    request_id: Optional[str],
) -> bool:
    """
    Publish a single sub-assistant with settings.

    Args:
        sub_id: ID of the sub-assistant
        sub_assistant: The sub-assistant to publish
        settings_map: Map of assistant IDs to settings
        assistant: The parent assistant
        user: The user performing the operation
        gate_armed: `is_approval_gate_armed()`, as resolved for the parent publish
        admin_skip_queue: Whether the parent is skipping the moderation queue (admin/maintainer)
        request_id: Correlation id forwarded to the queue-service decision, for the audit trail

    Returns:
        True if published to marketplace (is_global=True), False otherwise

    Raises:
        ExtendedHTTPException: If publishing fails
    """
    try:
        settings = settings_map.get(sub_id)
        is_global = _apply_sub_assistant_settings(sub_assistant, settings)

        if is_global and sub_assistant.marketplace_status in _SUBMITTABLE_STATUSES:
            # Move marketplace_status with is_global, mirroring the parent's
            # own admin_skip_queue/gate_armed decision (assistant.py:1871-1910)
            # instead of writing only `is_global` (previously left
            # marketplace_status at 'DRAFT': public but unreviewed, and
            # unreachable from the moderation queue since WITHDRAW never
            # accepts DRAFT). Refresh the marketplace columns first so the
            # save below can't revert a concurrent moderation decision made
            # since `sub_assistant` was loaded -- and so nothing set after the
            # refresh is overwritten by it.
            sub_assistant.refresh_marketplace_columns_from_db()
            if not gate_armed:
                # Gate disabled: there is no queue to skip, so this is not a
                # force-publish decision -- mirror the parent's
                # `_publish_with_gate_disabled` and approve directly with no
                # MarketplaceReviewDecision row, instead of recording an
                # admin force-publish (and a Verified badge) for an ordinary
                # user's unreviewed publish. The status and visibility must be
                # set BEFORE the save: nothing writes this row afterwards, so
                # setting them after it left the sub-assistant private while
                # the response said it was published.
                sub_assistant.marketplace_status = "APPROVED"
                sub_assistant.is_global = True
                sub_assistant.update(refresh=True)
            else:
                # Persist settings first -- the CAS below writes marketplace
                # columns directly, so a later `.update()` would clobber it
                # with the stale in-memory value.
                sub_assistant.update(refresh=True)
                if not admin_skip_queue:
                    _submit_for_review(
                        sub_assistant, user, gate_findings=_publish_gate_findings(sub_assistant), request_id=request_id
                    )
                    sub_assistant.marketplace_status = "PENDING_REVIEW"
                    is_global = False
                else:
                    # Gate armed and the parent is an admin/maintainer skipping the
                    # queue -- this is a genuine force-publish decision, so record
                    # it the same way the parent's own admin_skip_queue path does.
                    _force_publish_assistant(sub_assistant, user, _DEFAULT_ADMIN_SKIP_QUEUE_NOTICE, request_id)
                    sub_assistant.marketplace_status = "APPROVED"
            sub_assistant.is_global = is_global
        else:
            # Either kept private, or already mid-review/decided
            # (PENDING_REVIEW/APPROVED) — no marketplace-status transition is
            # applicable here. A sub-assistant mid-review must never go live
            # directly: only the moderation decision (approve) may promote it,
            # so force is_global False here regardless of what was requested,
            # even if the caller asked for True. APPROVED can still flip the
            # visibility flag directly since it was already reviewed.
            if sub_assistant.marketplace_status == "PENDING_REVIEW":
                is_global = False
            sub_assistant.refresh_marketplace_columns_from_db()
            sub_assistant.is_global = is_global
            sub_assistant.update(refresh=True)

        _track_assistant_management_metric(
            "publish_sub_assistant_to_marketplace",
            sub_assistant,
            user,
            True,
            {"parent_assistant_id": assistant.id, "is_global": is_global},
        )

        visibility_status = "published to marketplace" if is_global else "kept private (not visible in marketplace)"
        logger.info(
            f"Sub-assistant '{sub_assistant.name}' ({sub_id}) {visibility_status}",
            extra={"parent_assistant_id": assistant.id, "sub_assistant_id": sub_id, "is_global": is_global},
        )

        return is_global

    except ExtendedHTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to publish sub-assistant '{sub_assistant.name}' ({sub_id}) to marketplace: {str(e)}",
            extra={"parent_assistant_id": assistant.id, "sub_assistant_id": sub_id},
            exc_info=True,
        )
        _track_assistant_management_metric(
            "publish_sub_assistant_to_marketplace",
            sub_assistant,
            user,
            False,
            {"error_class": e.__class__.__name__, "parent_assistant_id": assistant.id, "phase": "publishing"},
        )
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=f"Failed to publish sub-assistant '{sub_assistant.name}' ({sub_id})",
            details=f"An error occurred while publishing sub-assistant: {str(e)}",
            help="Please check that all sub-assistants are valid and accessible. "
            "Ensure you have write permissions for all sub-assistants.",
        ) from e


def _publish_sub_assistants(
    assistant: Assistant,
    sub_assistants_settings: List[SubAssistantPublishSettings],
    user: User,
    gate_armed: bool,
    admin_skip_queue: bool,
    request_id: Optional[str],
) -> int:
    """
    Publish all sub-assistants with their specific settings and marketplace visibility.

    This function handles both public and private sub-assistants:
    - Public sub-assistants (is_global=True): Visible in marketplace and usable by orchestrator
    - Private sub-assistants (is_global=False): Not visible in marketplace but still usable by orchestrator

    The function uses a two-phase approach:
    1. Pre-validate all sub-assistants (fetch and check access)
    2. Publish all sub-assistants (only if all validations pass)

    This prevents partial publish states where some sub-assistants are published
    but others fail validation.

    Args:
        assistant: The parent assistant containing sub-assistants
        sub_assistants_settings: List of settings to apply to each sub-assistant (including is_global flag)
        user: The user performing the operation
        gate_armed: `is_approval_gate_armed()`, as resolved for the parent publish
        admin_skip_queue: Whether the parent is skipping the moderation queue (admin/maintainer)
        request_id: Correlation id forwarded to the queue-service decision, for the audit trail

    Returns:
        Number of sub-assistants successfully published to marketplace (is_global=True)
    """
    settings_map = {s.assistant_id: s for s in sub_assistants_settings}

    # Phase 1: Pre-validate all sub-assistants before publishing any
    sub_assistants_to_publish = [_validate_sub_assistant(sub_id, assistant, user) for sub_id in assistant.assistant_ids]

    # Reviewed sub-assistants cannot be reconfigured through the parent's publish
    if gate_armed:
        for sub_id, sub_assistant in sub_assistants_to_publish:
            _reject_unreviewed_sub_assistant_changes(sub_assistant, settings_map.get(sub_id))

    # Phase 2: All validations passed, now publish all sub-assistants
    published_count = 0
    for sub_id, sub_assistant in sub_assistants_to_publish:
        is_global = _publish_single_sub_assistant(
            sub_id, sub_assistant, settings_map, assistant, user, gate_armed, admin_skip_queue, request_id
        )
        if is_global:
            published_count += 1

    return published_count


def _validate_publish_marketplace(assistant, user, categories: list[str]):
    """
    Validate that an assistant can be published to the marketplace.

    This function checks:
    - User has write access to the assistant
    - Remote entities (e.g., Bedrock agents) exist if applicable
    - Categories are valid and exist in the database
    """
    check_user_can_access_assistant(user, assistant, "write", Action.WRITE)
    _validate_remote_entities_and_raise(assistant)
    category_service.validate_category_ids(categories, required=True)
