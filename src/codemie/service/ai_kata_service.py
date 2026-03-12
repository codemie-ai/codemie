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

from __future__ import annotations

import re
from typing import Optional, List

from pydantic import BaseModel, Field

from fastapi import status

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.ai_kata_repository import AIKataRepository, SQLAIKataRepository
from codemie.repository.user_kata_progress_repository import (
    UserKataProgressRepository,
    SQLUserKataProgressRepository,
)
from codemie.repository.kata_user_interaction_repository import (
    KataUserInteractionRepository,
    KataUsageRepositoryImpl,
)
from codemie.rest_api.models.ai_kata import (
    AIKata,
    AIKataRequest,
    AIKataResponse,
    AIKataListResponse,
    KataStatus,
    AIKataPaginatedResponse,
    get_valid_kata_tag_ids,
    get_valid_kata_role_ids,
)
from codemie.rest_api.models.base import PaginationData
from codemie.rest_api.models.user_kata_progress import UserKataProgressResponse, KataProgressStatus
from codemie.rest_api.security.user import User
from codemie.service.permission.permission_exceptions import PermissionAccessDenied
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes, KATA_MANAGEMENT_METRIC


# Constants for error messages
MESSAGE_KATA_NOT_FOUND = "Kata not found"
HELP_VERIFY_KATA_ID = "Please verify the kata ID and try again."


# Constants for kata validation and preview
class KataConstants:
    """Constants for kata validation and preview generation"""

    PREVIEW_TEXT_LENGTH = 500  # Characters to show in preview when no step structure found
    PREVIEW_STEP_COUNT = 3  # Number of steps to show in preview for non-enrolled users
    MAX_TAGS_PER_KATA = 10  # Maximum number of tags allowed per kata
    MAX_ROLES_PER_KATA = 10  # Maximum number of roles allowed per kata
    MIN_DURATION_MINUTES = 5  # Minimum kata duration in minutes
    MAX_DURATION_MINUTES = 240  # Maximum kata duration in minutes (4 hours)


# Compile regex pattern at module level for performance
STEP_HEADER_PATTERN = re.compile(r'^(#{1,6})\s+(?:Step\s+)?(\d+)', re.IGNORECASE)


class AIKataService(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    repository: AIKataRepository = Field(default_factory=SQLAIKataRepository)
    progress_repository: UserKataProgressRepository = Field(default_factory=SQLUserKataProgressRepository)
    interaction_repository: KataUserInteractionRepository = Field(default_factory=KataUsageRepositoryImpl)

    def create_kata(self, request: AIKataRequest, user: User) -> str:
        """
        Create new kata with validation.

        Args:
            request: Kata creation request
            user: Current user (must be admin)

        Returns:
            ID of the created kata

        Raises:
            PermissionAccessDenied: If user is not admin
            ExtendedHTTPException: If validation fails
        """
        # Admin check
        if not user.is_admin:
            logger.warning(f"Non-admin user {user.id} attempted to create kata")
            raise PermissionAccessDenied("Only administrators can create katas")

        logger.info(f"Creating new kata '{request.title}' for creator {user.id}")

        # Validate content
        validation_error = self.validate_kata_content(request)
        if validation_error:
            logger.error(f"Validation failed for kata creation: {validation_error}")
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Validation error",
                details=validation_error,
                help="Please check the kata content and try again.",
            )

        # Create kata entity
        kata = AIKata(
            title=request.title,
            description=request.description,
            steps=request.steps,
            level=request.level,
            creator_id=user.id,
            creator_name=user.name,
            creator_username=user.username,
            duration_minutes=request.duration_minutes,
            tags=request.tags,
            roles=request.roles,
            links=request.links or [],
            references=request.references or [],
            status=KataStatus.DRAFT,
            image_url=request.image_url,
        )

        kata_id = self.repository.create(kata)
        logger.info(f"Successfully created kata {kata_id}")

        # Track metrics
        self._track_kata_management_metric(
            operation="create_kata",
            kata_title=request.title,
            user=user,
            success=True,
        )

        return kata_id

    def get_kata(self, kata_id: str, user_id: Optional[str] = None, is_admin: bool = False) -> Optional[AIKataResponse]:
        """
        Get kata by ID, convert to response model with user progress info.

        Args:
            kata_id: ID of the kata
            user_id: Optional user ID for progress info
            is_admin: Whether the user is an admin (admins see full content)

        Returns:
            Kata response if found, None otherwise
        """
        kata = self.repository.get_by_id(kata_id)
        if not kata:
            return None

        # Get user progress - always return a UserKataProgressResponse
        progress = self.progress_repository.get_user_progress(user_id, kata_id) if user_id else None

        # Get user's reaction
        user_reaction = None
        if user_id:
            interaction = self.interaction_repository.get_by_kata_and_user(kata_id, user_id)
            if interaction:
                user_reaction = interaction.reaction

        if progress:
            user_progress = UserKataProgressResponse(
                id=progress.id,
                user_id=progress.user_id,
                kata_id=progress.kata_id,
                status=progress.status,
                started_at=progress.started_at,
                completed_at=progress.completed_at,
                user_reaction=user_reaction,
            )
            is_enrolled = True
        else:
            # Not enrolled - return NOT_STARTED status
            user_progress = UserKataProgressResponse(
                id=None,
                user_id=user_id or "",
                kata_id=kata_id,
                status=KataProgressStatus.NOT_STARTED,
                started_at=None,
                completed_at=None,
                user_reaction=user_reaction,
            )
            is_enrolled = False

        # Use denormalized enrollment_count from kata model
        enrollment_count = kata.enrollment_count

        # Filter steps based on enrollment (admins always see full content)
        filtered_steps = self.filter_steps_for_user(kata.steps, is_enrolled or is_admin)

        return self._to_response(
            kata,
            user_progress=user_progress,
            enrollment_count=enrollment_count,
            filtered_steps=filtered_steps,
            is_admin=is_admin,
        )

    def list_katas(
        self,
        page: int = 1,
        per_page: int = 20,
        filters: Optional[dict] = None,
        user_id: Optional[str] = None,
        is_admin: bool = False,
    ) -> AIKataPaginatedResponse:
        """
        List katas with filtering and pagination, including user progress info.

        Args:
            page: Page number (1-indexed)
            per_page: Items per page
            filters: Optional filter dictionary with keys:
                - search: text to search in title/description
                - level: KataLevel enum value
                - tags: list of tag IDs
                - roles: list of role IDs
                - status: KataStatus enum value
                - author: creator user ID
                - progress_status: KataProgressStatus enum value (not_started/in_progress/completed)
            user_id: Optional user ID for progress info
            is_admin: Whether the user is an admin (admins see full content)

        Returns:
            Paginated list of katas with progress info
        """
        filters = filters or {}
        logger.debug(
            f"Listing katas: page={page}, per_page={per_page}, filters={filters}, "
            f"user_id={user_id}, is_admin={is_admin}"
        )

        # Validate and query
        page, per_page = self._validate_pagination(page, per_page)
        katas, total = self.repository.list_with_filters(page=page, per_page=per_page, filters=filters, user_id=user_id)

        # Fetch user data
        kata_ids = [kata.id for kata in katas]
        user_progress_map, user_reaction_map = self._fetch_user_data(user_id, kata_ids)

        # Build response list
        kata_list = [
            self._build_kata_list_item(kata, user_id, user_progress_map, user_reaction_map, is_admin) for kata in katas
        ]

        # Calculate pagination
        total_pages = (total + per_page - 1) // per_page if per_page > 0 else 0

        return AIKataPaginatedResponse(
            data=kata_list,
            pagination=PaginationData(page=page, per_page=per_page, total=total, pages=total_pages),
        )

    def _validate_pagination(self, page: int, per_page: int) -> tuple[int, int]:
        """Validate and normalize pagination parameters."""
        validated_page = page if page >= 1 else 1
        validated_per_page = per_page if 1 <= per_page <= 100 else 20
        return validated_page, validated_per_page

    def _fetch_user_data(self, user_id: Optional[str], kata_ids: List[str]) -> tuple[dict, dict]:
        """Fetch user progress and reactions in bulk."""
        user_progress_map = {}
        user_reaction_map = {}

        if user_id and kata_ids:
            user_progress_map = self.progress_repository.bulk_get_user_progress(user_id, kata_ids)
            user_reaction_map = self.interaction_repository.bulk_get_user_reactions(user_id, kata_ids)

        return user_progress_map, user_reaction_map

    def _build_kata_list_item(
        self,
        kata: AIKata,
        user_id: Optional[str],
        user_progress_map: dict,
        user_reaction_map: dict,
        is_admin: bool,
    ) -> AIKataListResponse:
        """Build a single kata list response item with user progress."""
        user_reaction = user_reaction_map.get(kata.id) if user_id else None
        user_progress = self._create_user_progress_response(kata, user_id, user_progress_map, user_reaction)

        return self._to_list_response(
            kata, user_progress=user_progress, enrollment_count=kata.enrollment_count, is_admin=is_admin
        )

    def _create_user_progress_response(
        self,
        kata: AIKata,
        user_id: Optional[str],
        user_progress_map: dict,
        user_reaction: Optional[str],
    ) -> UserKataProgressResponse:
        """Create user progress response based on enrollment status."""
        if kata.id in user_progress_map:
            progress = user_progress_map[kata.id]
            return UserKataProgressResponse(
                id=progress.id,
                user_id=progress.user_id,
                kata_id=progress.kata_id,
                status=progress.status,
                started_at=progress.started_at,
                completed_at=progress.completed_at,
                user_reaction=user_reaction,
            )

        # Not enrolled - return NOT_STARTED status
        return UserKataProgressResponse(
            id=None,
            user_id=user_id or "",
            kata_id=kata.id,
            status=KataProgressStatus.NOT_STARTED,
            started_at=None,
            completed_at=None,
            user_reaction=user_reaction,
        )

    def update_kata(self, kata_id: str, request: AIKataRequest, user: User) -> bool:
        """
        Update kata (admin only).

        Args:
            kata_id: ID of the kata to update
            request: Update request
            user: Current user (must be admin)

        Returns:
            True if update was successful

        Raises:
            PermissionAccessDenied: If user is not admin
            ExtendedHTTPException: If kata not found or validation fails
        """
        # Admin check
        if not user.is_admin:
            logger.warning(f"Non-admin user {user.id} attempted to update kata {kata_id}")
            raise PermissionAccessDenied("Only administrators can update katas")

        logger.info(f"Updating kata {kata_id} by user {user.id}")

        # Check if kata exists
        kata = self.repository.get_by_id(kata_id)
        if not kata:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=MESSAGE_KATA_NOT_FOUND,
                details=f"Kata with ID {kata_id} not found",
                help=HELP_VERIFY_KATA_ID,
            )

        # Validate content
        validation_error = self.validate_kata_content(request)
        if validation_error:
            logger.error(f"Validation failed for kata update: {validation_error}")
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Validation error",
                details=validation_error,
                help="Please check the kata content and try again.",
            )

        # Update fields
        updates = {
            "title": request.title,
            "description": request.description,
            "steps": request.steps,
            "level": request.level,
            "duration_minutes": request.duration_minutes,
            "tags": request.tags,
            "roles": request.roles,
            "links": request.links or [],
            "references": request.references or [],
            "image_url": request.image_url,
        }

        success = self.repository.update(kata_id, updates)
        if success:
            logger.info(f"Successfully updated kata {kata_id}")
            # Track metrics
            self._track_kata_management_metric(
                operation="update_kata",
                kata_title=request.title,
                user=user,
                success=True,
            )
        return success

    def publish_kata(self, kata_id: str, user: User) -> bool:
        """
        Publish kata (admin only).

        Args:
            kata_id: ID of the kata to publish
            user: Current user (must be admin)

        Returns:
            True if publish was successful

        Raises:
            PermissionAccessDenied: If user is not admin
            ExtendedHTTPException: If kata not found
        """
        # Admin check
        if not user.is_admin:
            logger.warning(f"Non-admin user {user.id} attempted to publish kata {kata_id}")
            raise PermissionAccessDenied("Only administrators can publish katas")

        logger.info(f"Publishing kata {kata_id} by user {user.id}")

        # Check if kata exists
        kata = self.repository.get_by_id(kata_id)
        if not kata:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=MESSAGE_KATA_NOT_FOUND,
                details=f"Kata with ID {kata_id} not found",
                help=HELP_VERIFY_KATA_ID,
            )

        success = self.repository.publish(kata_id)
        if success:
            logger.info(f"Successfully published kata {kata_id}")
            # Track metrics
            self._track_kata_management_metric(
                operation="publish_kata",
                kata_title=kata.title,
                user=user,
                success=True,
                additional_attributes={MetricsAttributes.KATA_STATUS: "published"},
            )
        return success

    def unpublish_kata(self, kata_id: str, user: User) -> bool:
        """
        Unpublish kata (move from published to draft, admin only).

        Args:
            kata_id: ID of the kata to unpublish
            user: Current user (must be admin)

        Returns:
            True if unpublish was successful

        Raises:
            PermissionAccessDenied: If user is not admin
            ExtendedHTTPException: If kata not found
        """
        # Admin check
        if not user.is_admin:
            logger.warning(f"Non-admin user {user.id} attempted to unpublish kata {kata_id}")
            raise PermissionAccessDenied("Only administrators can unpublish katas")

        logger.info(f"Unpublishing kata {kata_id} by user {user.id}")

        # Check if kata exists
        kata = self.repository.get_by_id(kata_id)
        if not kata:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=MESSAGE_KATA_NOT_FOUND,
                details=f"Kata with ID {kata_id} not found",
                help=HELP_VERIFY_KATA_ID,
            )

        success = self.repository.unpublish(kata_id)
        if success:
            logger.info(f"Successfully unpublished kata {kata_id}")
            # Track metrics
            self._track_kata_management_metric(
                operation="unpublish_kata",
                kata_title=kata.title,
                user=user,
                success=True,
                additional_attributes={MetricsAttributes.KATA_STATUS: "draft"},
            )
        return success

    def archive_kata(self, kata_id: str, user: User) -> bool:
        """
        Archive kata (admin only).

        Args:
            kata_id: ID of the kata to archive
            user: Current user (must be admin)

        Returns:
            True if archive was successful

        Raises:
            PermissionAccessDenied: If user is not admin
            ExtendedHTTPException: If kata not found
        """
        # Admin check
        if not user.is_admin:
            logger.warning(f"Non-admin user {user.id} attempted to archive kata {kata_id}")
            raise PermissionAccessDenied("Only administrators can archive katas")

        logger.info(f"Archiving kata {kata_id} by user {user.id}")

        # Check if kata exists
        kata = self.repository.get_by_id(kata_id)
        if not kata:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=MESSAGE_KATA_NOT_FOUND,
                details=f"Kata with ID {kata_id} not found",
                help=HELP_VERIFY_KATA_ID,
            )

        success = self.repository.archive(kata_id)
        if success:
            logger.info(f"Successfully archived kata {kata_id}")
            # Track metrics
            self._track_kata_management_metric(
                operation="archive_kata",
                kata_title=kata.title,
                user=user,
                success=True,
                additional_attributes={MetricsAttributes.KATA_STATUS: "archived"},
            )
        return success

    def delete_kata(self, kata_id: str, user: User) -> bool:
        """
        Delete kata (admin only).

        Args:
            kata_id: ID of the kata to delete
            user: Current user (must be admin)

        Returns:
            True if delete was successful

        Raises:
            PermissionAccessDenied: If user is not admin
            ExtendedHTTPException: If kata not found
        """
        # Admin check
        if not user.is_admin:
            logger.warning(f"Non-admin user {user.id} attempted to delete kata {kata_id}")
            raise PermissionAccessDenied("Only administrators can delete katas")

        logger.info(f"Deleting kata {kata_id} by user {user.id}")

        # Check if kata exists
        kata = self.repository.get_by_id(kata_id)
        if not kata:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=MESSAGE_KATA_NOT_FOUND,
                details=f"Kata with ID {kata_id} not found",
                help=HELP_VERIFY_KATA_ID,
            )

        # Store kata title before deletion
        kata_title = kata.title

        success = self.repository.delete(kata_id)
        if success:
            logger.info(f"Successfully deleted kata {kata_id}")
            # Track metrics
            self._track_kata_management_metric(
                operation="delete_kata",
                kata_title=kata_title,
                user=user,
                success=True,
            )
        return success

    def _validate_basic_fields(self, request: AIKataRequest) -> str:
        """Validate basic kata fields (title, description, steps, duration)."""
        if not request.title or len(request.title.strip()) == 0:
            return "Title cannot be empty"
        if not request.description or len(request.description.strip()) == 0:
            return "Description cannot be empty"
        if not request.steps or len(request.steps.strip()) == 0:
            return "Steps cannot be empty"
        if (
            request.duration_minutes < KataConstants.MIN_DURATION_MINUTES
            or request.duration_minutes > KataConstants.MAX_DURATION_MINUTES
        ):
            return (
                f"Duration must be between {KataConstants.MIN_DURATION_MINUTES} "
                f"and {KataConstants.MAX_DURATION_MINUTES} minutes"
            )
        return ""

    def _validate_list_field(self, items: List[str], valid_ids: List[str], field_name: str, max_count: int) -> str:
        """
        Generic validation for list fields like tags and roles.

        Args:
            items: List of items to validate
            valid_ids: List of valid IDs
            field_name: Name of the field (for error messages, e.g., "tags")
            max_count: Maximum number of items allowed

        Returns:
            Empty string if valid, error message otherwise
        """
        if len(items) > max_count:
            return f"Maximum {max_count} {field_name} allowed"

        field_singular = field_name.rstrip('s')  # tags -> tag, roles -> role
        for item in items:
            if not item or len(item.strip()) == 0:
                return f"{field_name.capitalize()} cannot be empty"
            if item not in valid_ids:
                return f"Invalid {field_singular} '{item}'. Must be one of: {', '.join(valid_ids)}"

        return ""

    def _validate_tags(self, tags: List[str]) -> str:
        """Validate kata tags."""
        return self._validate_list_field(tags, get_valid_kata_tag_ids(), "tags", KataConstants.MAX_TAGS_PER_KATA)

    def _validate_roles(self, roles: List[str]) -> str:
        """Validate kata roles."""
        return self._validate_list_field(roles, get_valid_kata_role_ids(), "roles", KataConstants.MAX_ROLES_PER_KATA)

    def _validate_links(self, links: Optional[List]) -> str:
        """Validate kata links."""
        if not links:
            return ""
        for link in links:
            if not link.title or len(link.title.strip()) == 0:
                return "Link title cannot be empty"
            if not link.url or len(link.url.strip()) == 0:
                return "Link URL cannot be empty"
            if not link.type or len(link.type.strip()) == 0:
                return "Link type cannot be empty"
        return ""

    def validate_kata_content(self, request: AIKataRequest) -> str:
        """
        Validate kata content.

        Args:
            request: Kata request to validate

        Returns:
            Empty string if valid, error message otherwise
        """
        # Validate basic fields
        if error := self._validate_basic_fields(request):
            return error

        # Validate tags
        if error := self._validate_tags(request.tags):
            return error

        # Validate roles
        if error := self._validate_roles(request.roles):
            return error

        # Validate links
        if error := self._validate_links(request.links):
            return error

        return ""

    def filter_steps_for_user(self, steps: str, is_enrolled: bool) -> str:
        """
        Filter steps markdown based on enrollment.

        Args:
            steps: Full steps markdown
            is_enrolled: Whether user is enrolled

        Returns:
            Full steps if enrolled, first 2-3 steps preview if not
        """
        if is_enrolled:
            return steps

        # Parse markdown by step headers (# Step, ## Step, ### Step, etc.)
        # Match headers like: # Step 1, ## Step 1:, ### 1., #### Step 1 -, etc.
        lines = steps.split('\n')

        # Find all step header positions using pre-compiled regex
        step_positions = [i for i, line in enumerate(lines) if STEP_HEADER_PATTERN.match(line)]

        # If no clear step structure found, return first N characters
        if len(step_positions) < 2:
            if len(steps) <= KataConstants.PREVIEW_TEXT_LENGTH:
                return steps
            return steps[: KataConstants.PREVIEW_TEXT_LENGTH] + "\n\n... [Enroll to see full steps and materials]"

        # Return first N steps
        if len(step_positions) <= KataConstants.PREVIEW_STEP_COUNT:
            # Less than N steps total, return all but add enrollment message
            return steps + "\n\n... [Enroll to see all materials and references]"

        # Find the end position of the Nth step (start of N+1 step)
        cutoff_position = (
            step_positions[KataConstants.PREVIEW_STEP_COUNT]
            if len(step_positions) > KataConstants.PREVIEW_STEP_COUNT
            else len(lines)
        )

        # Extract preview lines
        preview_lines = lines[:cutoff_position]
        preview_text = '\n'.join(preview_lines)

        return preview_text + "\n\n... [Enroll to see remaining steps and materials]"

    def _to_response(
        self,
        kata: AIKata,
        user_progress: UserKataProgressResponse,
        enrollment_count: int = 0,
        filtered_steps: str | None = None,
        is_admin: bool = False,
    ) -> AIKataResponse:
        """
        Convert AIKata to AIKataResponse with progress info.

        Args:
            kata: AIKata entity
            user_progress: User progress response (never None, uses NOT_STARTED if not enrolled)
            enrollment_count: Total enrollment count
            filtered_steps: Optional filtered steps (if None, use kata.steps)
            is_admin: Whether the user is an admin (admins see full content)
        """
        # Hide links and references for non-enrolled users (admins always see everything)
        is_enrolled = user_progress.status != KataProgressStatus.NOT_STARTED
        links = kata.links if (is_enrolled or is_admin) else None
        references = kata.references if (is_enrolled or is_admin) else None

        return AIKataResponse(
            id=kata.id,
            title=kata.title,
            description=kata.description,
            steps=filtered_steps if filtered_steps is not None else kata.steps,
            level=kata.level,
            creator_id=kata.creator_id,
            creator_name=kata.creator_name,
            creator_username=kata.creator_username,
            duration_minutes=kata.duration_minutes,
            tags=kata.tags,
            roles=kata.roles,
            links=links,
            references=references,
            status=kata.status,
            date=kata.date,
            update_date=kata.update_date,
            image_url=kata.image_url,
            user_progress=user_progress,
            enrollment_count=enrollment_count,
            unique_likes_count=kata.unique_likes_count,
            unique_dislikes_count=kata.unique_dislikes_count,
        )

    def _to_list_response(
        self,
        kata: AIKata,
        user_progress: UserKataProgressResponse,
        enrollment_count: int = 0,
        is_admin: bool = False,
    ) -> AIKataListResponse:
        """
        Convert AIKata to AIKataListResponse with progress info.

        Args:
            kata: AIKata entity
            user_progress: User progress response (never None, uses NOT_STARTED if not enrolled)
            enrollment_count: Total enrollment count
            is_admin: Whether the user is an admin (for future use if needed)
        """
        return AIKataListResponse(
            id=kata.id,
            title=kata.title,
            description=kata.description,
            level=kata.level,
            creator_name=kata.creator_name,
            creator_username=kata.creator_username,
            duration_minutes=kata.duration_minutes,
            tags=kata.tags,
            roles=kata.roles,
            status=kata.status,
            date=kata.date,
            image_url=kata.image_url,
            user_progress=user_progress,
            enrollment_count=enrollment_count,
            unique_likes_count=kata.unique_likes_count,
            unique_dislikes_count=kata.unique_dislikes_count,
        )

    def _track_kata_management_metric(
        self, operation: str, kata_title: str, user: User, success: bool, additional_attributes: dict | None = None
    ):
        """
        Tracks metrics for kata management operations.

        Args:
            operation: The operation being performed (create_kata, update_kata, etc.)
            kata_title: Title of the kata being managed
            user: User performing the operation
            success: Whether the operation succeeded
            additional_attributes: Additional attributes to include in the metric
        """
        try:
            attributes = {
                MetricsAttributes.OPERATION: operation,
                MetricsAttributes.KATA_TITLE: kata_title,
                MetricsAttributes.USER_ID: user.id,
                MetricsAttributes.USER_NAME: user.name,
                MetricsAttributes.USER_EMAIL: user.username,
            }

            if additional_attributes:
                attributes.update(additional_attributes)

            metric_name = KATA_MANAGEMENT_METRIC if success else f"{KATA_MANAGEMENT_METRIC}_error"
            BaseMonitoringService.send_count_metric(name=metric_name, attributes=attributes)

        except Exception as e:
            logger.warning(
                f"Failed to track kata management metric '{operation}': {e}",
                exc_info=True,
            )
