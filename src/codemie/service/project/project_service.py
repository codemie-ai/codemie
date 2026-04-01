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
from types import SimpleNamespace
from typing import Final
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from codemie.clients.postgres import get_session
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.security.user import User
from codemie.service.cost_center_service import cost_center_service
from codemie.service.user.authentication_service import invalidate_user_from_cache


class ProjectService:
    """Service for shared project creation business rules."""

    MIN_PROJECT_NAME_LENGTH: Final[int] = 3
    MAX_PROJECT_NAME_LENGTH: Final[int] = 100
    MAX_PROJECT_DESCRIPTION_LENGTH: Final[int] = 500
    PROJECT_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
    RESERVED_PROJECT_NAMES: Final[set[str]] = {
        "admin",
        "system",
        "root",
        "api",
        "null",
        "undefined",
        "default",
        "test",
        "demo",
    }

    ERRORS: Final = SimpleNamespace(
        # Access / auth
        ACCESS_DENIED="Access denied",
        ACCOUNT_DEACTIVATED="Account is deactivated",
        # Project lookup
        PROJECT_NOT_FOUND="Project not found",
        # Name validation
        NAME_TOO_SHORT="Project name must be at least 3 characters",
        NAME_TOO_LONG="Project name cannot exceed 100 characters",
        NAME_INVALID=(
            "Invalid project name. Must be 3-100 lowercase alphanumeric characters (underscore/hyphen allowed), "
            "cannot contain uppercase letters, cannot start with special character, "
            "and cannot use reserved names (admin, system, root, api, null, undefined, default, test, demo)"
        ),
        NAME_RESERVED_TEMPLATE="Project name '{name}' is reserved and cannot be used",
        NAME_ASSIGNED_USERS="Project name cannot be changed while users are assigned to it",
        # Description validation
        DESC_REQUIRED="Project description is required",
        DESC_TOO_LONG="Project description cannot exceed 500 characters",
        # Project limits
        LIMIT_REACHED=(
            "Project creation limit reached ({count}/{limit}). Contact administrator to increase your limit."
        ),
        LIMIT_GRANDFATHERED=(
            "Project creation limit reached ({count}/{limit}). Delete {excess} or more projects to create new ones."
        ),
        LIMIT_INVALID_CONFIG="Invalid project limit configuration. Contact administrator.",
        # Project operations
        HAS_RESOURCES=(
            "Project '{name}' cannot be {action} because it has assigned resources. "
            "Remove all assistants, workflows, skills, datasources, and integrations first."
        ),
        HAS_ASSIGNED_USERS=(
            "Project '{name}' cannot be deleted because it has assigned users. Remove all users first."
        ),
        PERSONAL_DELETE="Cannot delete a personal project",
        PERSONAL_UPDATE="Cannot update a personal project",
    )

    @classmethod
    def create_shared_project(
        cls,
        user: User,
        project_name: str,
        description: str,
        cost_center_id: UUID | None = None,
    ) -> Application:
        """Create a shared project and grant creator project-admin membership."""
        validated_name = cls._validate_shared_project_name(project_name)
        validated_description = cls._validate_project_description(description)

        with get_session() as session:
            cls._enforce_project_limit(session, user)
            cost_center = cost_center_service.ensure_exists_for_project(session, cost_center_id)

            existing = application_repository.get_by_name_case_insensitive(session, validated_name)
            if existing:
                raise ExtendedHTTPException(
                    code=409,
                    message=cls._duplicate_project_message(existing.name),
                )

            try:
                create_kwargs = {
                    "session": session,
                    "name": validated_name,
                    "description": validated_description,
                    "project_type": Application.ProjectType.SHARED,
                    "created_by": user.id,
                }
                if cost_center:
                    create_kwargs["cost_center_id"] = cost_center.id
                project = application_repository.create(**create_kwargs)
                user_project_repository.add_project(
                    session=session,
                    user_id=user.id,
                    project_name=validated_name,
                    is_project_admin=True,
                )
                session.expunge(project)  # Expunge before commit to preserve loaded attributes for the caller
                session.commit()
                invalidate_user_from_cache(user.id)
                return project
            except IntegrityError as e:
                session.rollback()
                existing_after_error = application_repository.get_by_name_case_insensitive(session, validated_name)
                existing_name = existing_after_error.name if existing_after_error else validated_name
                raise ExtendedHTTPException(
                    code=409,
                    message=cls._duplicate_project_message(existing_name),
                ) from e

    @classmethod
    def update_project(
        cls,
        user: User,
        project_name: str,
        *,
        name: str | None = None,
        description: str | None = None,
        cost_center_id: UUID | None = None,
        clear_cost_center: bool = False,
    ) -> Application:
        with get_session() as session:
            project = application_repository.get_by_name(session, project_name)
            if not project or project.deleted_at is not None:
                raise ExtendedHTTPException(code=404, message=cls.ERRORS.PROJECT_NOT_FOUND)

            if not user.is_admin and not user_project_repository.is_admin(session, user.id, project_name):
                raise ExtendedHTTPException(code=403, message=cls.ERRORS.ACCESS_DENIED)

            validated_name: str | None = None
            if name is not None:
                validated_name = cls._validate_shared_project_name(name)
                existing = application_repository.get_by_name_case_insensitive(session, validated_name)
                if existing and existing.name.lower() != project_name.lower():
                    raise ExtendedHTTPException(
                        code=409,
                        message=cls._duplicate_project_message(existing.name),
                    )
                assigned = user_project_repository.get_by_project_name(session, project_name)
                if assigned:
                    raise ExtendedHTTPException(
                        code=409,
                        message=cls.ERRORS.NAME_ASSIGNED_USERS,
                    )

            validated_description: str | None = None
            if description is not None:
                validated_description = cls._validate_project_description(description)

            resolved_cost_center_id: UUID | None = project.cost_center_id
            if cost_center_id is not None:
                cost_center = cost_center_service.ensure_exists_for_project(session, cost_center_id)
                resolved_cost_center_id = cost_center.id
            elif clear_cost_center:
                resolved_cost_center_id = None

            project = application_repository.update_project(
                session,
                project,
                name=validated_name,
                description=validated_description,
                cost_center_id=resolved_cost_center_id,
            )
            session.commit()
            session.refresh(project)
            return project

    @classmethod
    def _validate_shared_project_name(cls, name: str) -> str:
        if len(name) < cls.MIN_PROJECT_NAME_LENGTH:
            raise ExtendedHTTPException(code=400, message=cls.ERRORS.NAME_TOO_SHORT)

        if len(name) > cls.MAX_PROJECT_NAME_LENGTH:
            raise ExtendedHTTPException(code=400, message=cls.ERRORS.NAME_TOO_LONG)

        if not cls.PROJECT_NAME_PATTERN.match(name):
            raise ExtendedHTTPException(code=400, message=cls.ERRORS.NAME_INVALID)

        if name.lower() in cls.RESERVED_PROJECT_NAMES:
            raise ExtendedHTTPException(code=400, message=cls.ERRORS.NAME_RESERVED_TEMPLATE.format(name=name))

        return name

    @classmethod
    def _validate_project_description(cls, description: str) -> str:
        if not description.strip():
            raise ExtendedHTTPException(code=400, message=cls.ERRORS.DESC_REQUIRED)

        if len(description) > cls.MAX_PROJECT_DESCRIPTION_LENGTH:
            raise ExtendedHTTPException(code=400, message=cls.ERRORS.DESC_TOO_LONG)

        return description

    @staticmethod
    def _enforce_project_limit(session: Session, user: User) -> None:
        if user.is_admin:
            return

        persisted_user = user_repository.get_active_by_id(session, user.id)
        if not persisted_user:
            # Aligns with current authentication flow semantics for inactive/soft-deleted accounts.
            raise ExtendedHTTPException(code=401, message=ProjectService.ERRORS.ACCOUNT_DEACTIVATED)

        if persisted_user.project_limit is None:
            # Fail closed: non-super-admin users must never bypass limits via NULL configuration.
            raise ExtendedHTTPException(
                code=403,
                message=ProjectService.ERRORS.LIMIT_INVALID_CONFIG,
            )

        created_count = application_repository.count_shared_projects_created_by_user(session, user.id)
        if created_count < persisted_user.project_limit:
            return

        if created_count > persisted_user.project_limit:
            excess_projects = created_count - persisted_user.project_limit
            raise ExtendedHTTPException(
                code=403,
                message=ProjectService.ERRORS.LIMIT_GRANDFATHERED.format(
                    count=created_count,
                    limit=persisted_user.project_limit,
                    excess=excess_projects,
                ),
            )

        raise ExtendedHTTPException(
            code=403,
            message=ProjectService.ERRORS.LIMIT_REACHED.format(
                count=created_count,
                limit=persisted_user.project_limit,
            ),
        )

    @staticmethod
    def _duplicate_project_message(project_name: str) -> str:
        return f"Project '{project_name}' already exists. Please choose a different name."

    @classmethod
    def _check_has_no_resources(cls, session: Session, project_name: str, action: str) -> None:
        """Raise 409 if the project has any assigned resources.

        Args:
            session: Database session
            project_name: Project name to check
            action: Human-readable action word for the error message ('deleted' or 'renamed')
        """
        entity_counts = application_repository.get_project_entity_counts_bulk(session, [project_name])
        counts = entity_counts.get(project_name, {})
        if sum(counts.values()) > 0:
            non_zero = {k: v for k, v in counts.items() if v > 0}
            raise ExtendedHTTPException(
                code=409,
                message=cls.ERRORS.HAS_RESOURCES.format(name=project_name, action=action),
                details=str(non_zero),
            )

    @classmethod
    def delete_project(
        cls,
        session: Session,
        project_name: str,
        project_type: str,
        actor_id: str,
        action: str,
        creator_id: str | None = None,
    ) -> None:
        """Hard-delete a project after validating it has no assigned resources.

        Args:
            session: Database session (caller commits/rolls back)
            project_name: Name of the project to delete
            project_type: Project type string (used for personal-project guard)
            actor_id: ID of the requesting user (for logging)
            action: HTTP method+path string for logging (e.g., "DELETE /v1/projects/foo")
            creator_id: ID of the project creator — excluded from the assigned-users check

        Raises:
            ExtendedHTTPException 403: If project is personal
            ExtendedHTTPException 409: If project has assigned users (all users must be removed first)
            ExtendedHTTPException 409: If project has assigned resources (assistants, workflows, etc.)
        """
        if project_type == Application.ProjectType.PERSONAL:
            http_method = action.split()[0] if action else "UNKNOWN"
            logger.warning(f"personal_project_delete_blocked: user_id={actor_id}, method={http_method}")
            raise ExtendedHTTPException(code=403, message=cls.ERRORS.PERSONAL_DELETE)

        assigned = [
            up for up in user_project_repository.get_by_project_name(session, project_name) if up.user_id != creator_id
        ]
        if assigned:
            logger.warning(
                f"project_delete_blocked_by_assigned_users: project={project_name}, "
                f"user_count={len(assigned)}, by={actor_id}"
            )
            raise ExtendedHTTPException(
                code=409,
                message=cls.ERRORS.HAS_ASSIGNED_USERS.format(name=project_name),
                details=f"Assigned users: {len(assigned)}",
            )

        cls._check_has_no_resources(session, project_name, "deleted")
        application_repository.delete_by_name(session, project_name)
        logger.info(f"project_deleted: project={project_name}, by={actor_id}")


project_service = ProjectService()
