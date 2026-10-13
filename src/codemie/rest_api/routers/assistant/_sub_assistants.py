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
) -> bool:
    """
    Publish a single sub-assistant with settings.

    Args:
        sub_id: ID of the sub-assistant
        sub_assistant: The sub-assistant to publish
        settings_map: Map of assistant IDs to settings
        assistant: The parent assistant
        user: The user performing the operation

    Returns:
        True if published to marketplace (is_global=True), False otherwise

    Raises:
        ExtendedHTTPException: If publishing fails
    """
    try:
        settings = settings_map.get(sub_id)
        is_global = _apply_sub_assistant_settings(sub_assistant, settings)

        # Set marketplace visibility
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
    assistant: Assistant, sub_assistants_settings: List[SubAssistantPublishSettings], user: User
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

    Returns:
        Number of sub-assistants successfully published to marketplace (is_global=True)
    """
    settings_map = {s.assistant_id: s for s in sub_assistants_settings}

    # Phase 1: Pre-validate all sub-assistants before publishing any
    sub_assistants_to_publish = [_validate_sub_assistant(sub_id, assistant, user) for sub_id in assistant.assistant_ids]

    # Phase 2: All validations passed, now publish all sub-assistants
    published_count = 0
    for sub_id, sub_assistant in sub_assistants_to_publish:
        is_global = _publish_single_sub_assistant(sub_id, sub_assistant, settings_map, assistant, user)
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
