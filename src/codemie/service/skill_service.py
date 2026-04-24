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

"""
Service layer for skill management.
"""

import base64
import re
from datetime import UTC, datetime
from typing import Any, NamedTuple

import yaml

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.repository.skill_repository import SkillRepository
from codemie.rest_api.models.skill import (
    MarketplaceFilter,
    Skill,
    SkillCategory,
    SkillCompanionFileMetadata,
    SkillCompanionFileResponse,
    SkillCreateRequest,
    SkillDetailResponse,
    SkillListPaginatedResponse,
    SkillListResponse,
    SkillSortBy,
    SkillUpdateRequest,
    SkillVisibility,
)
from codemie.rest_api.security.user import User
from codemie.service.monitoring.skill_monitoring_service import SkillMonitoringService
from fastapi import status


class FrontmatterResult(NamedTuple):
    """Result from parsing YAML frontmatter in skill content"""

    name: str | None
    description: str | None
    content: str


class ExportResult(NamedTuple):
    """Result from exporting a skill"""

    content: str
    filename: str


class SkillErrors:
    """Centralized error messages for skill operations"""

    # Error messages (for 'message' parameter)
    MSG_SKILL_NOT_FOUND = "Skill not found"
    MSG_ASSISTANT_NOT_FOUND = "Assistant not found"

    # Skill errors (for 'details' parameter)
    SKILL_NOT_FOUND = "No skill found with ID '{skill_id}'"
    SKILL_NAME_EXISTS = "A skill named '{name}' already exists for this user in project '{project}'"
    SKILL_NAME_EXISTS_UPDATE = "A skill named '{name}' already exists"
    INVALID_NAME_FORMAT = "Name '{name}' is not valid kebab-case"

    # Frontmatter errors
    INVALID_SKILL_FORMAT = "Invalid skill format"
    INVALID_FRONTMATTER_START = "File must start with YAML frontmatter (---)"
    INVALID_FRONTMATTER_END = "Could not find closing YAML frontmatter delimiter (---)"
    INVALID_FRONTMATTER_PARSE = "Failed to parse YAML frontmatter: {error}"
    MISSING_FRONTMATTER_FIELDS = "Skill file must have 'name' and 'description' in YAML frontmatter"

    # Content errors
    CONTENT_TOO_SHORT = "Content is too short ({length} characters). Minimum is 100 characters."
    INVALID_FILE_ENCODING = "Failed to decode file: {error}"

    # Assistant errors (for 'details' parameter)
    ASSISTANT_NOT_FOUND = "No assistant found with ID '{assistant_id}'"

    # Permission errors
    PERMISSION_DENIED = "Permission denied"
    PERMISSION_DENIED_ASSISTANT = "User does not own this assistant"
    PERMISSION_DENIED_SKILL = "User does not have {action} access to skill '{skill_name}'"

    # Help messages
    HELP_VERIFY_SKILL_ID = "Verify the skill ID or list available skills"
    HELP_VERIFY_ASSISTANT_ID = "Verify the assistant ID"


class SkillService:
    """Business logic for skill management"""

    # Validation constants
    MIN_CONTENT_LENGTH = 100
    MIN_INSTRUCTION_LENGTH = 500

    # ============================================================================
    # Access Control Helpers
    # ============================================================================

    @staticmethod
    def get_user_abilities(user: User, skill: Skill) -> list[str]:
        """Get list of actions user can perform on skill using Ability framework"""
        ability = Ability(user)
        actions = ability.list(skill)
        return [action.value for action in actions]

    @staticmethod
    def _raise_if_no_access(user: User, skill: Skill, action: Action) -> None:
        """Raise exception if user doesn't have access"""
        ability = Ability(user)
        if not ability.can(action, skill):
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=SkillErrors.PERMISSION_DENIED,
                details=f"User does not have {action.value} access to skill '{skill.name}'",
                help="Contact the skill owner or project manager to request access",
            )

    @staticmethod
    def _get_skill_or_raise(skill_id: str) -> Skill:
        """Load a skill or raise a standard not-found error."""
        skill = SkillRepository.get_by_id(skill_id)
        if not skill:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_SKILL_NOT_FOUND,
                details=SkillErrors.SKILL_NOT_FOUND.format(skill_id=skill_id),
                help=SkillErrors.HELP_VERIFY_SKILL_ID,
            )
        return skill

    @staticmethod
    def _normalize_companion_file_path(path: str) -> str:
        """Normalize a bundle-relative companion file path."""
        normalized_path = path.strip().replace("\\", "/").lstrip("/")
        if not normalized_path:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid companion file path",
                details="Companion file path must not be empty",
                help="Provide a relative bundle path such as 'references/foo.md'",
            )
        return normalized_path

    # ============================================================================
    # CRUD Operations
    # ============================================================================

    @staticmethod
    def list_skills(
        user: User,
        project: list[str] | None = None,
        visibility: SkillVisibility | None = None,
        categories: list[SkillCategory] | None = None,
        search_query: str | None = None,
        created_by: str | None = None,
        page: int = 0,
        per_page: int = 20,
        assistant_id: str | None = None,
        marketplace_filter: MarketplaceFilter = MarketplaceFilter.DEFAULT,
        sort_by: SkillSortBy = SkillSortBy.CREATED_DATE,
    ) -> SkillListPaginatedResponse:
        """
        List skills accessible to user with optional filters.

        Args:
            user: Current user
            project: Filter by project(s)
            visibility: Filter by visibility level
            categories: Filter by categories
            search_query: Search by skill name
            created_by: Filter by creator user name
            page: Page number (0-indexed)
            per_page: Items per page
            assistant_id: If provided, marks which skills are attached to this assistant
            marketplace_filter: Controls marketplace skill inclusion (DEFAULT/EXCLUDE/INCLUDE)
            sort_by: Sort field (CREATED_DATE, ASSISTANTS_COUNT, or RELEVANCE)

        Returns:
            Paginated list of skills with metadata
        """
        # Determine admin status
        # - user_is_global_admin: Can see ALL skills from ALL projects
        # - user_admin_projects: Projects where user is admin (can see ALL skills in these projects)
        user_is_global_admin = user.is_admin
        user_admin_projects = user.applications_admin if hasattr(user, "applications_admin") else []

        result = SkillRepository.list_accessible_to_user(
            user_id=user.id,
            user_applications=user.project_names,
            user_is_global_admin=user_is_global_admin,
            user_admin_projects=user_admin_projects,
            project=project,
            visibility=visibility,
            categories=categories,
            search_query=search_query,
            created_by=created_by,
            page=page,
            per_page=per_page,
            marketplace_filter=marketplace_filter,
            sort_by=sort_by,
        )

        # Get attached skill IDs if assistant_id provided
        attached_skill_ids = set()
        if assistant_id:
            from codemie.rest_api.models.assistant import Assistant

            assistant = Assistant.find_by_id(assistant_id)
            if assistant and assistant.skill_ids:
                attached_skill_ids = set(assistant.skill_ids)

        # Convert to response models
        skills_response = [
            skill.to_list_response(
                is_attached=skill.id in attached_skill_ids,
                assistants_count=result.assistants_count_map.get(skill.id, 0),
                user_abilities=SkillService.get_user_abilities(user, skill),
            )
            for skill in result.skills
        ]

        return SkillListPaginatedResponse(
            skills=skills_response,
            page=result.page,
            per_page=result.per_page,
            total=result.total,
            pages=result.pages,
        )

    @staticmethod
    def get_skill_by_id(skill_id: str, user: User) -> SkillDetailResponse:
        """
        Get skill details by ID.
        Validates user has read access based on visibility.
        """
        skill = SkillService._get_skill_or_raise(skill_id)

        SkillService._raise_if_no_access(user, skill, Action.READ)

        # Get additional counts
        assistants_count = SkillRepository.count_assistants_using_skill(skill_id)
        user_abilities = SkillService.get_user_abilities(user, skill)

        return skill.to_detail_response(
            assistants_count=assistants_count,
            user_abilities=user_abilities,
        )

    @staticmethod
    def list_companion_files(skill_id: str, user: User) -> list[SkillCompanionFileMetadata]:
        """List bundle companion files for a skill without returning payload content."""
        skill = SkillService._get_skill_or_raise(skill_id)
        SkillService._raise_if_no_access(user, skill, Action.READ)
        return skill.get_companion_file_metadata()

    @staticmethod
    def get_companion_file(skill_id: str, path: str, user: User) -> SkillCompanionFileResponse:
        """Return a single companion file payload for a skill."""
        skill = SkillService._get_skill_or_raise(skill_id)
        SkillService._raise_if_no_access(user, skill, Action.READ)

        normalized_path = SkillService._normalize_companion_file_path(path)
        for file_data in skill.companion_files or []:
            if file_data.get("path") == normalized_path:
                return SkillCompanionFileResponse.model_validate(file_data)

        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Companion file not found",
            details=f"No companion file found at path '{normalized_path}' for skill '{skill.name}'",
            help="List available companion files for this skill and retry with one of those paths",
        )

    @staticmethod
    def get_skills_by_ids(skill_ids: list[str], user: User) -> list[Skill]:
        """
        Get multiple skills by IDs.
        Used by SkillTool to load attached skills.
        Filters out skills user doesn't have access to.
        """
        if not skill_ids:
            return []

        skills = SkillRepository.get_by_ids(skill_ids)

        # Filter by access using Ability framework
        ability = Ability(user)
        accessible_skills = [skill for skill in skills if ability.can(Action.READ, skill)]

        return accessible_skills

    @staticmethod
    def create_skill(request: SkillCreateRequest, user: User) -> SkillDetailResponse:
        """
        Create new skill.
        - Validates name format (kebab-case)
        - Checks for duplicate names per user per project
        - Sets author_id, author_name, author_username from user
        - Sets version to "1.0.0"
        """
        # Check for duplicate name
        existing = SkillRepository.get_by_name_author_project(
            name=request.name,
            author_id=user.id,
            project=request.project,
        )
        if existing:
            raise ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message="Skill name already exists",
                details=f"A skill named '{request.name}' already exists for this user in project '{request.project}'",
                help="Choose a different name or update the existing skill",
            )

        # Create skill
        skill_data = {
            "name": request.name,
            "description": request.description,
            "content": request.content,
            "project": request.project,
            "visibility": request.visibility,
            "categories": [c.value for c in request.categories],
            "toolkits": request.toolkits,
            "mcp_servers": request.mcp_servers,
            "created_by": CreatedByUser(
                id=user.id,
                name=user.name or user.username,
                username=user.username,
            ),
        }

        skill = SkillRepository.create(skill_data)
        logger.info(f"Created skill '{skill.name}' (ID: {skill.id}) by user '{user.id}'")

        # Send success metric
        SkillMonitoringService.send_skill_management_metric(
            metric_name="create",
            skill=skill,
            success=True,
            user=user,
        )

        return skill.to_detail_response(
            assistants_count=0,
            user_abilities=["read", "write", "delete"],
        )

    @staticmethod
    def _validate_project_change(request: SkillUpdateRequest, skill: Skill, user: User) -> str | None:
        """
        Validate project change and return new project if valid.

        Returns:
            New project value if change is valid, None if no change requested.

        Raises:
            ExtendedHTTPException: If user doesn't have access to target project.
        """
        if request.project is None or request.project == skill.project:
            return None

        user_admin_projects = user.applications_admin if hasattr(user, "applications_admin") else []
        is_admin_of_target = user.is_admin or request.project in user_admin_projects

        if not is_admin_of_target and request.project not in user.applications:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=SkillErrors.PERMISSION_DENIED,
                details=f"You don't have access to project '{request.project}'",
                help="Only admins or project members can move skills to a project",
            )
        return request.project

    @staticmethod
    def _check_duplicate_name(skill_id: str, target_name: str, target_project: str, author_id: str) -> None:
        """
        Check if skill name already exists in target project.

        Raises:
            ExtendedHTTPException: If duplicate name found.
        """
        existing = SkillRepository.get_by_name_author_project(
            name=target_name,
            author_id=author_id,
            project=target_project,
        )
        if existing and existing.id != skill_id:
            raise ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message="Skill name already exists",
                details=f"A skill named '{target_name}' already exists in project '{target_project}'",
                help="Choose a different name or project",
            )

    @staticmethod
    def _build_skill_updates(request: SkillUpdateRequest, skill: Skill) -> dict:
        """Build updates dict from request, excluding project (handled separately)."""
        updates = {}

        if request.name is not None and request.name != skill.name:
            updates["name"] = request.name
        if request.description is not None:
            updates["description"] = request.description
        if request.content is not None and request.content != skill.content:
            updates["content"] = request.content
        if request.visibility is not None:
            updates["visibility"] = request.visibility
        if request.categories is not None:
            updates["categories"] = [c.value for c in request.categories]
        if request.toolkits is not None:
            updates["toolkits"] = request.toolkits
        if request.mcp_servers is not None:
            updates["mcp_servers"] = request.mcp_servers

        return updates

    @staticmethod
    def update_skill(
        skill_id: str,
        request: SkillUpdateRequest,
        user: User,
    ) -> SkillDetailResponse:
        """
        Update existing skill.
        Only skill owner can update.
        Auto-increments version on content changes.
        """
        skill = SkillRepository.get_by_id(skill_id)
        if not skill:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_SKILL_NOT_FOUND,
                details=SkillErrors.SKILL_NOT_FOUND.format(skill_id=skill_id),
                help=SkillErrors.HELP_VERIFY_SKILL_ID,
            )

        SkillService._raise_if_no_access(user, skill, Action.WRITE)

        # Validate and apply project change
        updates = {}
        new_project = SkillService._validate_project_change(request, skill, user)
        if new_project:
            updates["project"] = new_project

        # Check for duplicate name if name or project changed
        target_project = new_project or skill.project
        target_name = request.name if request.name is not None else skill.name
        name_changed = request.name is not None and request.name != skill.name

        if name_changed or new_project:
            author_id = skill.created_by.id if skill.created_by else ""
            SkillService._check_duplicate_name(skill_id, target_name, target_project, author_id)

        # Build remaining updates
        updates.update(SkillService._build_skill_updates(request, skill))

        if updates:
            skill = SkillRepository.update(skill_id, updates)
            logger.info(f"Updated skill '{skill.name}' (ID: {skill_id}) by user '{user.id}'")

            # Send success metric
            SkillMonitoringService.send_skill_management_metric(
                metric_name="update",
                skill=skill,
                success=True,
                user=user,
            )

        assistants_count = SkillRepository.count_assistants_using_skill(skill_id)
        user_abilities = SkillService.get_user_abilities(user, skill)

        return skill.to_detail_response(
            assistants_count=assistants_count,
            user_abilities=user_abilities,
        )

    @staticmethod
    def _parse_frontmatter(content: str) -> FrontmatterResult:
        """
        Parse YAML frontmatter from markdown content.

        Args:
            content: Full markdown content with optional YAML frontmatter

        Returns:
            FrontmatterResult with name, description, and content
        """
        # Check if content starts with frontmatter delimiter
        if not content.startswith("---"):
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message=SkillErrors.INVALID_SKILL_FORMAT,
                details=SkillErrors.INVALID_FRONTMATTER_START,
                help="Add YAML frontmatter with 'name' and 'description' fields",
            )

        # Find the closing frontmatter delimiter
        lines = content.split("\n")
        end_index = None

        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_index = i
                break

        if end_index is None:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message=SkillErrors.INVALID_SKILL_FORMAT,
                details=SkillErrors.INVALID_FRONTMATTER_END,
                help="Ensure YAML frontmatter is properly delimited with --- at start and end",
            )

        # Extract and parse frontmatter
        frontmatter_lines = lines[1:end_index]
        frontmatter_text = "\n".join(frontmatter_lines)

        try:
            metadata = yaml.safe_load(frontmatter_text) or {}
        except yaml.YAMLError as e:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message=SkillErrors.INVALID_SKILL_FORMAT,
                details=SkillErrors.INVALID_FRONTMATTER_PARSE.format(error=str(e)),
                help="Ensure the YAML frontmatter is valid",
            )

        # Extract body content (everything after frontmatter)
        body_lines = lines[end_index + 1 :]
        body_content = "\n".join(body_lines).strip()

        name = metadata.get("name")
        description = metadata.get("description")

        return FrontmatterResult(name=name, description=description, content=body_content)

    @staticmethod
    def delete_skill(skill_id: str, user: User) -> None:
        """
        Delete skill.
        Only skill owner can delete.
        Also removes skill from any assistants that reference it.
        """
        skill = SkillRepository.get_by_id(skill_id)
        if not skill:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_SKILL_NOT_FOUND,
                details=SkillErrors.SKILL_NOT_FOUND.format(skill_id=skill_id),
                help=SkillErrors.HELP_VERIFY_SKILL_ID,
            )

        SkillService._raise_if_no_access(user, skill, Action.DELETE)

        # Remove skill from all assistants that reference it
        SkillRepository.remove_skill_from_all_assistants(skill_id)

        # Delete the skill
        SkillRepository.delete(skill_id)
        logger.info(f"Deleted skill '{skill.name}' (ID: {skill_id}) by user '{user.id}'")

        # Send success metric
        SkillMonitoringService.send_skill_management_metric(
            metric_name="delete",
            skill=skill,
            success=True,
            user=user,
        )

    # ============================================================================
    # Import/Export Operations
    # ============================================================================

    @staticmethod
    def import_skill(
        file_content: str,
        filename: str,
        project: str,
        user: User,
        visibility: SkillVisibility,
    ) -> SkillDetailResponse:
        """
        Import skill from .md file.
        - Decodes base64 content
        - Parses YAML frontmatter (extracts name, description ONLY)
        - Rest of content becomes skill content
        - Sets version to "1.0.0", categories empty
        """
        try:
            # Decode base64
            decoded = base64.b64decode(file_content).decode("utf-8")
        except Exception as e:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid file encoding",
                details=f"Failed to decode file: {str(e)}",
                help="Ensure the file is base64-encoded",
            )

        # Parse YAML frontmatter manually
        result = SkillService._parse_frontmatter(decoded)

        if not result.name or not result.description:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message=SkillErrors.INVALID_SKILL_FORMAT,
                details=SkillErrors.MISSING_FRONTMATTER_FIELDS,
                help="Add 'name' and 'description' fields to the YAML frontmatter",
            )

        # Validate name format
        if not re.match(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$", result.name):
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid skill name",
                details=SkillErrors.INVALID_NAME_FORMAT.format(name=result.name),
                help="Name must be lowercase letters, numbers, and hyphens only.",
            )

        if len(result.content) < SkillService.MIN_CONTENT_LENGTH:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid skill content",
                details=SkillErrors.CONTENT_TOO_SHORT.format(length=len(result.content)),
                help="Add more content to the skill",
            )

        # Create skill with defaults
        return SkillService.create_skill(
            SkillCreateRequest(
                name=result.name,
                description=result.description,
                content=result.content,
                project=project,
                visibility=visibility,
                categories=[],  # Categories empty on import
            ),
            user,
        )

    @staticmethod
    def export_skill(skill_id: str, user: User) -> ExportResult:
        """
        Export skill as .md file.
        - Frontmatter contains ONLY name and description
        - Rest is skill content
        Returns ExportResult with formatted markdown string and filename.
        """
        skill = SkillRepository.get_by_id(skill_id)
        if not skill:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_SKILL_NOT_FOUND,
                details=SkillErrors.SKILL_NOT_FOUND.format(skill_id=skill_id),
                help=SkillErrors.HELP_VERIFY_SKILL_ID,
            )

        SkillService._raise_if_no_access(user, skill, Action.READ)

        # Send success metric
        SkillMonitoringService.send_skill_exported_metric(
            skill=skill,
            user=user,
            success=True,
        )

        # Format with ONLY name and description in frontmatter
        markdown = f"""---
name: {skill.name}
description: {skill.description}
---

{skill.content}
"""
        return ExportResult(content=markdown, filename=f"{skill.name}.md")

    # ============================================================================
    # Assistant-Skill Association Operations
    # ============================================================================

    @staticmethod
    def attach_skill_to_assistant(
        assistant_id: str,
        skill_id: str,
        user: User,
    ) -> None:
        """
        Associate skill with assistant.
        Adds skill_id to assistant.skill_ids array.
        Validates user has write permission on assistant and read access to skill.
        """
        from codemie.rest_api.models.assistant import Assistant

        # Validate assistant
        assistant = Assistant.find_by_id(assistant_id)
        if not assistant:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_ASSISTANT_NOT_FOUND,
                details=SkillErrors.ASSISTANT_NOT_FOUND.format(assistant_id=assistant_id),
                help=SkillErrors.HELP_VERIFY_ASSISTANT_ID,
            )

        # Check user has write permission on assistant
        ability = Ability(user)
        if not ability.can(Action.WRITE, assistant):
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=SkillErrors.PERMISSION_DENIED,
                details=SkillErrors.PERMISSION_DENIED_ASSISTANT,
                help="You do not have permission to modify this assistant's skill attachments",
            )

        # Validate skill
        skill = SkillRepository.get_by_id(skill_id)
        if not skill:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_SKILL_NOT_FOUND,
                details=SkillErrors.SKILL_NOT_FOUND.format(skill_id=skill_id),
                help=SkillErrors.HELP_VERIFY_SKILL_ID,
            )

        SkillService._raise_if_no_access(user, skill, Action.READ)

        # Add skill if not already attached
        current_skill_ids = assistant.skill_ids or []
        if skill_id not in current_skill_ids:
            # Create a new list to trigger SQLModel change detection for JSONB field
            assistant.skill_ids = [*current_skill_ids, skill_id]
            assistant.updated_date = datetime.now(UTC)
            assistant.update(validate=False)
            logger.info(f"Attached skill '{skill_id}' to assistant '{assistant_id}'")

            # Send success metric
            SkillMonitoringService.send_skill_attached_metric(
                skill=skill,
                assistant_id=assistant_id,
                assistant_name=assistant.name,
                user=user,
                success=True,
                operation="attach",
            )

    @staticmethod
    def bulk_attach_skill_to_assistants(
        skill_id: str,
        assistant_ids: list[str],
        user: User,
    ) -> dict[str, Any]:
        """
        Attach skill to multiple assistants in bulk.

        Args:
            skill_id: ID of skill to attach
            assistant_ids: List of assistant IDs to attach skill to
            user: User performing the action

        Returns:
            Dictionary with success count and list of failures

        Raises:
            ExtendedHTTPException: If skill not found or user lacks permission
        """
        from codemie.rest_api.models.assistant import Assistant

        # Validate skill exists and user has read access
        skill = SkillRepository.get_by_id(skill_id)
        if not skill:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_SKILL_NOT_FOUND,
                details=SkillErrors.SKILL_NOT_FOUND.format(skill_id=skill_id),
                help=SkillErrors.HELP_VERIFY_SKILL_ID,
            )

        SkillService._raise_if_no_access(user, skill, Action.READ)

        # Track results
        success_count = 0
        failures = []

        # Process each assistant
        ability = Ability(user)
        for assistant_id in assistant_ids:
            try:
                # Validate assistant exists
                assistant = Assistant.find_by_id(assistant_id)
                if not assistant:
                    failures.append(
                        {"assistant_id": assistant_id, "reason": f"Assistant not found with ID '{assistant_id}'"}
                    )
                    continue

                # Check user has write permission on assistant
                if not ability.can(Action.WRITE, assistant):
                    failures.append(
                        {"assistant_id": assistant_id, "reason": "You do not have permission to modify this assistant"}
                    )
                    continue

                # Add skill if not already attached
                current_skill_ids = assistant.skill_ids or []
                if skill_id not in current_skill_ids:
                    # Create a new list to trigger SQLModel change detection for JSONB field
                    assistant.skill_ids = [*current_skill_ids, skill_id]
                    assistant.updated_date = datetime.now(UTC)
                    assistant.update(validate=False)

                    # Send success metric
                    SkillMonitoringService.send_skill_attached_metric(
                        skill=skill,
                        assistant_id=assistant_id,
                        assistant_name=assistant.name,
                        user=user,
                        success=True,
                        operation="attach",
                    )

                    success_count += 1
                    logger.info(f"Attached skill '{skill_id}' to assistant '{assistant_id}'")

            except Exception as e:
                logger.error(f"Failed to attach skill '{skill_id}' to assistant '{assistant_id}': {str(e)}")
                failures.append({"assistant_id": assistant_id, "reason": str(e)})

        return {"success_count": success_count, "total_requested": len(assistant_ids), "failures": failures}

    @staticmethod
    def detach_skill_from_assistant(
        assistant_id: str,
        skill_id: str,
        user: User,
    ) -> None:
        """
        Remove skill association from assistant.
        Removes skill_id from assistant.skill_ids array.
        Validates user has write permission on assistant.
        """
        from codemie.rest_api.models.assistant import Assistant

        # Validate assistant
        assistant = Assistant.find_by_id(assistant_id)
        if not assistant:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_ASSISTANT_NOT_FOUND,
                details=SkillErrors.ASSISTANT_NOT_FOUND.format(assistant_id=assistant_id),
                help=SkillErrors.HELP_VERIFY_ASSISTANT_ID,
            )

        # Check user has write permission on assistant
        ability = Ability(user)
        if not ability.can(Action.WRITE, assistant):
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=SkillErrors.PERMISSION_DENIED,
                details=SkillErrors.PERMISSION_DENIED_ASSISTANT,
                help="You do not have permission to modify this assistant's skill attachments",
            )

        # Remove skill
        current_skill_ids = assistant.skill_ids or []
        if skill_id in current_skill_ids:
            # Get skill for metrics before detaching
            skill = SkillRepository.get_by_id(skill_id)

            # Create a new list to trigger SQLModel change detection for JSONB field
            assistant.skill_ids = [sid for sid in current_skill_ids if sid != skill_id]
            assistant.updated_date = datetime.now(UTC)
            assistant.update(validate=False)
            logger.info(f"Detached skill '{skill_id}' from assistant '{assistant_id}'")

            # Send success metric
            if skill:
                SkillMonitoringService.send_skill_attached_metric(
                    skill=skill,
                    assistant_id=assistant_id,
                    assistant_name=assistant.name,
                    user=user,
                    success=True,
                    operation="detach",
                )

    @staticmethod
    def get_skills_for_assistant(
        assistant_id: str,
        user: User,
    ) -> list[SkillListResponse]:
        """
        List all skills attached to assistant.
        Reads assistant.skill_ids and fetches skill details.
        Filters by user's read access.
        """
        from codemie.rest_api.models.assistant import Assistant

        assistant = Assistant.find_by_id(assistant_id)
        if not assistant:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_ASSISTANT_NOT_FOUND,
                details=SkillErrors.ASSISTANT_NOT_FOUND.format(assistant_id=assistant_id),
                help=SkillErrors.HELP_VERIFY_ASSISTANT_ID,
            )

        skill_ids = assistant.skill_ids or []
        if not skill_ids:
            return []

        # Get all skills and filter by access using Ability framework
        skills = SkillRepository.get_by_ids(skill_ids)
        ability = Ability(user)
        accessible_skills = [
            skill.to_list_response(
                is_attached=True,
                user_abilities=SkillService.get_user_abilities(user, skill),
            )
            for skill in skills
            if ability.can(Action.READ, skill)
        ]

        return accessible_skills

    @staticmethod
    def get_assistants_for_skill(
        skill_id: str,
        user: User,
    ) -> list:
        """
        List all assistants that use the specified skill.

        Args:
            skill_id: ID of the skill
            user: User requesting the list

        Returns:
            List of AssistantListResponse objects that use this skill

        Raises:
            ExtendedHTTPException: If skill not found or user lacks read access
        """
        from codemie.rest_api.models.assistant import AssistantListResponse

        # Validate skill exists and user has read access
        skill = SkillRepository.get_by_id(skill_id)
        if not skill:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_SKILL_NOT_FOUND,
                details=SkillErrors.SKILL_NOT_FOUND.format(skill_id=skill_id),
                help=SkillErrors.HELP_VERIFY_SKILL_ID,
            )

        SkillService._raise_if_no_access(user, skill, Action.READ)

        # Get all assistants using this skill
        assistants = SkillRepository.get_assistants_using_skill(skill_id)
        if not assistants:
            return []

        # Filter by user's read access using Ability framework
        ability = Ability(user)
        accessible_assistants = [
            AssistantListResponse(
                id=assistant.id,
                name=assistant.name,
                slug=assistant.slug,
                type=assistant.type,
                description=assistant.description,
                icon_url=assistant.icon_url,
                created_by=assistant.created_by,
                user_abilities=[action.value for action in Action if ability.can(action, assistant)],
                unique_users_count=assistant.unique_users_count,
                unique_likes_count=assistant.unique_likes_count,
                unique_dislikes_count=assistant.unique_dislikes_count,
                categories=assistant.categories,
                is_global=assistant.is_global,
                shared=assistant.shared,
                origin=assistant.origin,
            )
            for assistant in assistants
            if ability.can(Action.READ, assistant)
        ]

        logger.info(
            f"Retrieved {len(accessible_assistants)} accessible assistants for skill '{skill_id}' "
            f"(out of {len(assistants)} total)"
        )

        return accessible_assistants

    # ============================================================================
    # Usage Tracking
    # ============================================================================

    @staticmethod
    def record_skill_usage(skill_id: str, user_id: str, project: str | None = None) -> None:
        """
        Record skill usage (called when SkillTool loads a skill).
        Updates skill_user_interaction table (upsert).
        """
        from codemie.service.skill_user_interaction_service import skill_user_interaction_service

        skill_user_interaction_service.record_usage(skill_id, user_id, project)

    # ============================================================================
    # User Listing
    # ============================================================================

    @staticmethod
    def get_skill_users(user: User) -> list[dict[str, str]]:
        """
        Get list of users who created skills accessible to the current user.

        Args:
            user: Current user making the request

        Returns:
            List of dicts with id, name, username (matching CreatedByUser schema)
        """

        authors = SkillRepository.get_skill_authors(
            user_id=user.id,
            user_applications=user.project_names,
            user_is_global_admin=user.is_admin,
            user_admin_projects=user.admin_project_names,
        )

        # Repository returns data in CreatedByUser-compatible format
        return authors

    # ============================================================================
    # Marketplace Operations
    # ============================================================================

    @staticmethod
    def publish_to_marketplace(skill_id: str, user: User, categories: list[str] | None = None) -> None:
        """
        Publish skill to marketplace by setting visibility=PUBLIC.
        Only skill owner or admin can publish.

        Args:
            skill_id: ID of skill to publish
            user: User performing the action
            categories: Optional categories to update during publish

        Raises:
            ExtendedHTTPException: If skill not found, user lacks permission, or operation fails
        """
        skill = SkillRepository.get_by_id(skill_id)
        if not skill:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_SKILL_NOT_FOUND,
                details=SkillErrors.SKILL_NOT_FOUND.format(skill_id=skill_id),
                help=SkillErrors.HELP_VERIFY_SKILL_ID,
            )

        # Check if user is owner or admin
        is_owner = skill.created_by and skill.created_by.id == user.id
        if not is_owner and not user.is_admin:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=SkillErrors.PERMISSION_DENIED,
                details="Only the skill owner or administrators can publish skills to marketplace",
                help="Contact an administrator or the skill owner",
            )

        # Validate business rules for already-public skills
        if skill.visibility == SkillVisibility.PUBLIC and categories and categories != skill.categories:
            logger.warning(
                f"Updating categories for already-public skill {skill_id}: old={skill.categories}, new={categories}"
            )

        # Build updates - set visibility to PUBLIC for marketplace
        updates: dict[str, Any] = {"visibility": SkillVisibility.PUBLIC}
        if categories is not None:
            updates["categories"] = categories

        # Update skill
        SkillRepository.update(skill_id, updates)
        logger.info(f"Published skill '{skill.name}' (ID: {skill_id}) to marketplace by user '{user.id}'")

    @staticmethod
    def unpublish_from_marketplace(skill_id: str, user: User) -> None:
        """
        Unpublish skill from marketplace by setting visibility=PRIVATE.
        Only skill owner or admin can unpublish.

        Args:
            skill_id: ID of skill to unpublish
            user: User performing the action

        Raises:
            ExtendedHTTPException: If skill not found, user lacks permission, or operation fails
        """
        skill = SkillRepository.get_by_id(skill_id)
        if not skill:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message=SkillErrors.MSG_SKILL_NOT_FOUND,
                details=SkillErrors.SKILL_NOT_FOUND.format(skill_id=skill_id),
                help=SkillErrors.HELP_VERIFY_SKILL_ID,
            )

        # Check if user is owner or admin
        is_owner = skill.created_by and skill.created_by.id == user.id
        if not is_owner and not user.is_admin:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=SkillErrors.PERMISSION_DENIED,
                details="Only the skill owner or administrators can unpublish skills from marketplace",
                help="Contact an administrator or the skill owner",
            )

        # Update skill - set visibility to PRIVATE
        SkillRepository.update(skill_id, {"visibility": SkillVisibility.PRIVATE})
        logger.info(f"Unpublished skill '{skill.name}' (ID: {skill_id}) from marketplace by user '{user.id}'")

    # ============================================================================
    # AI-Powered Instruction Generation
    # ============================================================================

    @staticmethod
    def _validate_instruction_format(instructions: str) -> None:
        """
        Validate generated instruction format.

        Args:
            instructions: Generated instructions to validate

        Raises:
            ExtendedHTTPException: If format is invalid
        """
        # Check minimum length
        if len(instructions) < SkillService.MIN_INSTRUCTION_LENGTH:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Generated instructions too short",
                details=(
                    f"Instructions must be at least {SkillService.MIN_INSTRUCTION_LENGTH} "
                    f"characters (got {len(instructions)})"
                ),
                help="Try providing more detailed description",
            )

        # Check for required sections
        required_sections = ["overview", "instructions", "examples"]
        instructions_lower = instructions.lower()
        missing_sections = [section for section in required_sections if section not in instructions_lower]

        if missing_sections:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Generated instructions missing required sections",
                details=f"Missing sections: {', '.join(missing_sections)}",
                help="Try regenerating with more specific requirements",
            )

        # Check for placeholders/incomplete content
        from codemie.templates.skills.skill_instruction_generator_prompt import FORBIDDEN_PLACEHOLDERS

        found_placeholders = [p for p in FORBIDDEN_PLACEHOLDERS if p in instructions]

        if found_placeholders:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Generated instructions contain placeholders",
                details=f"Found incomplete content: {', '.join(found_placeholders)}",
                help="Try regenerating for complete instructions",
            )

    @staticmethod
    def _normalize_string_input(value: str | None) -> str | None:
        """Normalize string input by converting empty strings to None."""
        return None if value is not None and not value.strip() else value

    @staticmethod
    def _prepare_refine_prompt(description: str | None, existing_content: str):
        """Prepare prompt and variables for refine mode."""
        from codemie.templates.skills.skill_instruction_generator_prompt import (
            SKILL_INSTRUCTION_GENERATOR_SYSTEM_PROMPT,
            SKILL_INSTRUCTION_REFINE_PROMPT,
            USER_REFINE_INSTRUCTIONS,
            AUTOMATIC_QUALITY_REVIEW_INSTRUCTIONS,
            FORBIDDEN_PLACEHOLDERS,
        )
        from langchain_core.prompts import ChatPromptTemplate

        refinement_instructions = (
            USER_REFINE_INSTRUCTIONS.format(description=description)
            if description and description.strip()
            else AUTOMATIC_QUALITY_REVIEW_INSTRUCTIONS
        )

        # Format forbidden placeholders as a bulleted list
        forbidden_placeholders_text = "\n".join(f"- `{placeholder}`" for placeholder in FORBIDDEN_PLACEHOLDERS)

        # Inject forbidden placeholders into system prompt
        system_prompt = SKILL_INSTRUCTION_GENERATOR_SYSTEM_PROMPT.format(
            forbidden_placeholders=forbidden_placeholders_text
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                ("human", SKILL_INSTRUCTION_REFINE_PROMPT),
            ]
        )
        variables = {
            "existing_instructions": existing_content,
            "refinement_mode_instructions": refinement_instructions,
        }
        return prompt, variables

    @staticmethod
    def _prepare_generate_prompt(description: str | None, existing_content: str | None, skill_name: str | None):
        """Prepare prompt and variables for generate mode."""
        from codemie.templates.skills.skill_instruction_generator_prompt import (
            SKILL_INSTRUCTION_GENERATOR_SYSTEM_PROMPT,
            SKILL_INSTRUCTION_GENERATOR_USER_PROMPT,
            FORBIDDEN_PLACEHOLDERS,
        )
        from langchain_core.prompts import ChatPromptTemplate

        user_instructions = (
            f"**Skill Description**: {description}" if description else "No specific description provided"
        )

        existing_instructions_context = (
            f"\n**Existing Instructions to Improve**:\n```markdown\n{existing_content}\n```\n"
            if existing_content
            else ""
        )

        skill_name_context = f"\n**Skill Name**: {skill_name}\n" if skill_name else ""

        # Format forbidden placeholders as a bulleted list
        forbidden_placeholders_text = "\n".join(f"- `{placeholder}`" for placeholder in FORBIDDEN_PLACEHOLDERS)

        # Inject forbidden placeholders into system prompt
        system_prompt = SKILL_INSTRUCTION_GENERATOR_SYSTEM_PROMPT.format(
            forbidden_placeholders=forbidden_placeholders_text
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                ("human", SKILL_INSTRUCTION_GENERATOR_USER_PROMPT),
            ]
        )
        variables = {
            "user_instructions": user_instructions,
            "existing_instructions_context": existing_instructions_context,
            "skill_name_context": skill_name_context,
        }
        return prompt, variables

    @staticmethod
    def _invoke_llm_and_validate(prompt, variables, structured_llm):
        """Invoke LLM chain and validate the generated instructions."""
        chain = prompt | structured_llm
        logger.debug(f"Invoking LLM for instruction generation. Variables: {variables.keys()}")

        result = chain.invoke(variables)

        # Assemble structured sections into final markdown content
        sections = []

        # Add overview section
        sections.append(f"## Overview\n\n{result.overview}")

        # Add important section if provided
        if result.important:
            sections.append(f"\n## Important\n\n{result.important}")

        # Add instructions section
        sections.append(f"\n## Instructions\n\n{result.instructions}")

        # Add examples section
        sections.append(f"\n## Examples\n\n{result.examples}")

        # Add troubleshooting section if provided
        if result.troubleshooting:
            sections.append(f"\n## Troubleshooting\n\n{result.troubleshooting}")

        # Join all sections
        assembled_instructions = "\n".join(sections)

        logger.debug(f"Assembled instructions length: {len(assembled_instructions)} characters")

        # Validate the assembled content (redundant but provides extra safety)
        SkillService._validate_instruction_format(assembled_instructions)

        return assembled_instructions

    @staticmethod
    def _send_error_metric_and_raise(user: User, mode: str, model: str, error: Exception):
        """Send error metric and raise HTTP exception."""
        logger.error(f"Failed to generate skill instructions: {str(error)}", exc_info=True)

        SkillMonitoringService.send_skill_instruction_generation_metric(
            success=False,
            user=user,
            mode=mode,
            model=model,
            error=str(error),
        )

        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to generate instructions",
            details=f"An error occurred while generating instructions: {str(error)}",
            help="Try refining your description or using a different model.",
        )

    @staticmethod
    def generate_instructions(
        description: str | None,
        user: User,
        existing_content: str | None = None,
        skill_name: str | None = None,
        llm_model: str | None = None,
        request_id: str | None = None,
    ):
        """
        Generate skill instructions using AI.

        Mode is automatically determined:
        - If existing_content is provided → refine mode
        - Otherwise → generate mode

        Args:
            description: User description/instructions (required for generate, optional for refine)
            user: Current user (for LLM context/permissions)
            existing_content: Optional existing instructions to refine
            skill_name: Optional skill name for context
            llm_model: Optional specific LLM model (defaults to system default)
            request_id: Optional request ID for tracking/logging

        Returns:
            SkillInstructionsGenerateResponse with generated instructions

        Raises:
            ExtendedHTTPException: On validation or generation failure
        """
        # Initialize variables with defaults for error handling
        mode = "unknown"
        model_to_use = llm_model or "unknown"

        from codemie.core.dependecies import get_llm_by_credentials
        from codemie.service.llm_service.llm_service import llm_service
        from pydantic import BaseModel, Field as PydanticField

        try:
            # Normalize inputs
            description = SkillService._normalize_string_input(description)
            existing_content = SkillService._normalize_string_input(existing_content)
            skill_name = SkillService._normalize_string_input(skill_name)

            # Auto-determine mode
            mode = "refine" if existing_content else "generate"

            # Validate refine mode requirements
            if mode == "refine" and not existing_content:
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Existing content required for refine mode",
                    details="Mode is 'refine' but no existing_content provided",
                    help="Provide existing_content or use mode='generate'",
                )

            # Setup model and logging
            model_to_use = llm_model or llm_service.default_llm_model
            logger.info(
                f"Generating skill instructions. Mode={mode}, Model={model_to_use}, "
                f"User={user.id}, RequestID={request_id}"
            )

            # Initialize LLM with structured output
            llm = get_llm_by_credentials(llm_model=model_to_use, temperature=0.7, streaming=False)

            class SkillInstructionsStructured(BaseModel):
                """Structured sections for skill instructions - validated independently."""

                overview: str = PydanticField(
                    description="One sentence overview of what this skill enables. Must be concise.",
                    min_length=20,
                    max_length=300,
                )

                important: str | None = PydanticField(
                    description=(
                        "Critical rules or must-follow guidelines. Only include if truly critical. Use bullet points."
                    ),
                    default=None,
                    min_length=50,
                    max_length=2000,
                )

                instructions: str = PydanticField(
                    description=(
                        "Step-by-step instructions with clear actions. Format as markdown with:\n"
                        "### Step 1: [Action Name]\n"
                        "[Explanation]\n\n"
                        "**Example:** [Concrete example]\n\n"
                        "**Expected result:** [What success looks like]"
                    ),
                    min_length=200,
                    max_length=10000,
                )

                examples: str = PydanticField(
                    description=(
                        "At least 2 concrete usage examples. Format as markdown with:\n"
                        "### Example 1: [Scenario]\n"
                        "**User says:** \"[Trigger phrase]\"\n\n"
                        "**Actions:**\n"
                        "1. [Step]\n"
                        "2. [Step]\n\n"
                        "**Result:** [Outcome]"
                    ),
                    min_length=200,
                    max_length=10000,
                )

                troubleshooting: str | None = PydanticField(
                    description=(
                        "Troubleshooting section for common errors. Format as:\n"
                        "### Error: \"[Error message]\"\n"
                        "**Cause:** [Why it happens]\n"
                        "**Solution:** [How to fix]"
                    ),
                    default=None,
                    min_length=100,
                    max_length=5000,
                )

            structured_llm = llm.with_structured_output(SkillInstructionsStructured)

            # Prepare prompt based on mode
            prompt, variables = (
                SkillService._prepare_refine_prompt(description, existing_content)
                if mode == "refine"
                else SkillService._prepare_generate_prompt(description, existing_content, skill_name)
            )

            # Invoke LLM and validate
            instructions = SkillService._invoke_llm_and_validate(prompt, variables, structured_llm)

            # Send success metric
            SkillMonitoringService.send_skill_instruction_generation_metric(
                success=True,
                user=user,
                mode=mode,
                model=model_to_use,
            )

            logger.info(f"Successfully generated skill instructions. Mode={mode}, Length={len(instructions)}")

            # Return response
            from codemie.rest_api.models.skill import SkillInstructionsGenerateResponse

            return SkillInstructionsGenerateResponse(
                instructions=instructions,
                metadata={
                    "model": model_to_use,
                    "mode": mode,
                    "request_id": request_id,
                    "length": len(instructions),
                },
            )

        except ExtendedHTTPException:
            raise
        except Exception as e:
            SkillService._send_error_metric_and_raise(user=user, mode=mode, model=model_to_use, error=e)


# Create singleton instance for sync access from tools
skill_service = SkillService()
