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
from codemie.configs.customer_config import customer_config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.budget_repository import budget_repository
from codemie.repository.cost_center_repository import cost_center_repository
from codemie.repository.project_budget_repository import project_budget_group_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    ProjectManagementEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.security.user import User
from codemie.service.cost_center_service import cost_center_service
from codemie.service.settings.settings import SettingsService
from codemie.service.project.project_assignment_service import ProjectAssignmentService
from codemie.service.user.authentication_service import invalidate_user_from_cache


class ProjectService:
    """Service for shared project creation business rules."""

    MIN_PROJECT_NAME_LENGTH: Final[int] = 3
    MAX_PROJECT_NAME_LENGTH: Final[int] = 100
    MAX_PROJECT_DESCRIPTION_LENGTH: Final[int] = 500
    MAX_DISPLAY_NAME_LENGTH: Final[int] = 150
    VALID_CHARGEBACK_ATTRIBUTIONS: Final[frozenset[str]] = frozenset({"project", "cost_center"})
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
        # Display name validation
        DISPLAY_NAME_TOO_LONG="Project display name cannot exceed 150 characters",
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
            "Remove all assistants, workflows, skills, datasources, integrations, and budgets first."
        ),
        HAS_ASSIGNED_USERS=(
            "Project '{name}' cannot be deleted because it has assigned users. Remove all users first."
        ),
        PERSONAL_DELETE="Cannot delete a personal project",
        PERSONAL_UPDATE="Cannot update a personal project",
        # Chargeback validation
        CHARGEBACK_ATTRIBUTION_INVALID="chargeback_attribution must be one of: 'project', 'cost_center'",
        CHARGEBACK_ATTRIBUTION_NO_COST_CENTER="Cost center attribution requires a linked, active cost center",
    )

    @classmethod
    def create_shared_project(
        cls,
        user: User,
        project_name: str,
        description: str | None = None,
        display_name: str | None = None,
        cost_center_id: UUID | None = None,
    ) -> Application:
        """Create a shared project and grant creator project-admin membership."""
        validated_name = cls._validate_shared_project_name(project_name)
        validated_description = cls._validate_project_description(description) if description is not None else None
        validated_display_name = cls._validate_display_name(display_name) if display_name is not None else None

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
                    "display_name": validated_display_name,
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
                activity_event_repository.insert(
                    ActivityEventCreate(
                        domain=ActivityDomain.PROJECT_MANAGEMENT,
                        event_type=ProjectManagementEvent.PROJECT_CREATED,
                        entity_type=ActivityEntityType.PROJECT,
                        entity_id=validated_name,
                        actor_id=user.id,
                    ),
                    session,
                )
                session.expunge(project)  # Expunge before commit to preserve loaded attributes for the caller
                session.commit()
                invalidate_user_from_cache(user.id)
                logger.info(f"project_created: project={validated_name!r}, by={user.id}, domain=project_management")
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
        display_name: str | None = None,
        clear_display_name: bool = False,
        description: str | None = None,
        clear_description: bool = False,
        cost_center_id: UUID | None = None,
        clear_cost_center: bool = False,
        enforce_member_spend_limits: bool | None = None,
        chargeback_enabled: bool | None = None,
        chargeback_attribution: str | None = None,
    ) -> Application:
        with get_session() as session:
            project = cls._get_project_for_update(session, user, project_name)

            if (
                chargeback_enabled is not None or chargeback_attribution is not None
            ) and not customer_config.is_feature_enabled("projectChargeback"):
                raise ExtendedHTTPException(
                    code=403,
                    message="Feature not available",
                    details="Project chargeback configuration is not enabled for this customer.",
                )

            validated_name = cls._resolve_updated_name(session, project_name, name)
            resolved_description, resolved_clear_description = cls._resolve_updated_description(
                description, clear_description
            )
            resolved_display_name = cls._resolve_updated_display_name(project, display_name, clear_display_name)
            resolved_cost_center_id = cls._resolve_updated_cost_center_id(
                session, project, cost_center_id, clear_cost_center
            )
            if chargeback_attribution is not None or cost_center_id is not None or clear_cost_center:
                effective_chargeback_attribution = (
                    chargeback_attribution if chargeback_attribution is not None else project.chargeback_attribution
                )
                cls._validate_chargeback_attribution(session, effective_chargeback_attribution, resolved_cost_center_id)

            project = application_repository.update_project(
                session,
                project,
                name=validated_name,
                display_name=resolved_display_name,
                description=resolved_description,
                clear_description=resolved_clear_description,
                cost_center_id=resolved_cost_center_id,
                chargeback_enabled=chargeback_enabled,
                chargeback_attribution=chargeback_attribution,
            )
            if enforce_member_spend_limits is not None:
                SettingsService.set_enforce_member_spend_limits(
                    project.name,
                    enforce_member_spend_limits,
                )
            activity_event_repository.insert(
                ActivityEventCreate(
                    domain=ActivityDomain.PROJECT_MANAGEMENT,
                    event_type=ProjectManagementEvent.PROJECT_UPDATED,
                    entity_type=ActivityEntityType.PROJECT,
                    entity_id=project.name,
                    actor_id=user.id,
                ),
                session,
            )
            session.commit()
            session.refresh(project)
            logger.info(f"project_updated: project={project.name!r}, by={user.id}, domain=project_management")
            cls._resync_member_allocations_if_needed(project.name, enforce_member_spend_limits)
            return project

    @classmethod
    def _get_project_for_update(cls, session: Session, user: User, project_name: str) -> Application:
        project = application_repository.get_by_name(session, project_name)
        if not project or project.deleted_at is not None:
            raise ExtendedHTTPException(code=404, message=cls.ERRORS.PROJECT_NOT_FOUND)

        is_project_admin = user_project_repository.is_admin(session, user.id, project_name)
        if not user.is_admin_or_maintainer and not is_project_admin:
            raise ExtendedHTTPException(code=403, message=cls.ERRORS.ACCESS_DENIED)

        return project

    @classmethod
    def _resolve_updated_name(cls, session: Session, project_name: str, name: str | None) -> str | None:
        if name is None:
            return None

        validated_name = cls._validate_shared_project_name(name)
        existing = application_repository.get_by_name_case_insensitive(session, validated_name)
        if existing and existing.name.lower() != project_name.lower():
            raise ExtendedHTTPException(
                code=409,
                message=cls._duplicate_project_message(existing.name),
            )

        assigned = user_project_repository.get_by_project_name(session, project_name)
        if assigned:
            raise ExtendedHTTPException(code=409, message=cls.ERRORS.NAME_ASSIGNED_USERS)

        return validated_name

    @classmethod
    def _resolve_updated_display_name(
        cls, project: Application, display_name: str | None, clear_display_name: bool
    ) -> str | None:
        if display_name is not None:
            return cls._validate_display_name(display_name) or None
        if clear_display_name:
            return None
        return project.display_name

    @classmethod
    def _resolve_updated_description(cls, description: str | None, clear_description: bool) -> tuple[str | None, bool]:
        """Return (normalized_description, clear_description) for repository forwarding."""
        if clear_description:
            return None, True
        if description is not None:
            normalized = cls._validate_project_description(description)
            # whitespace-only normalizes to None → treat as an explicit clear
            if normalized is None:
                return None, True
            return normalized, False
        return None, False

    @classmethod
    def _resolve_updated_cost_center_id(
        cls,
        session: Session,
        project: Application,
        cost_center_id: UUID | None,
        clear_cost_center: bool,
    ) -> UUID | None:
        if cost_center_id is not None:
            cost_center = cost_center_service.ensure_exists_for_project(session, cost_center_id)
            return cost_center.id
        if clear_cost_center:
            return None
        return project.cost_center_id

    @classmethod
    def _validate_chargeback_attribution(
        cls, session: Session, chargeback_attribution: str | None, resolved_cost_center_id: UUID | None
    ) -> None:
        if chargeback_attribution is None:
            return
        if chargeback_attribution not in cls.VALID_CHARGEBACK_ATTRIBUTIONS:
            raise ExtendedHTTPException(code=400, message=cls.ERRORS.CHARGEBACK_ATTRIBUTION_INVALID)
        if chargeback_attribution != "cost_center":
            return
        if not customer_config.is_feature_enabled("costCenters"):
            raise ExtendedHTTPException(
                code=403,
                message="Feature not available",
                details="Cost centers are not enabled for this customer.",
            )
        active = (
            cost_center_repository.get_active_by_id(session, resolved_cost_center_id)
            if resolved_cost_center_id is not None
            else None
        )
        if active is None:
            raise ExtendedHTTPException(code=400, message=cls.ERRORS.CHARGEBACK_ATTRIBUTION_NO_COST_CENTER)

    @classmethod
    def _resync_member_allocations_if_needed(
        cls,
        project_name: str,
        enforce_member_spend_limits: bool | None,
    ) -> None:
        if enforce_member_spend_limits is None:
            return
        from codemie.enterprise.litellm.project_member_runtime_sync import (  # noqa: PLC0415
            resync_project_member_allocations_sync,
        )

        try:
            resync_project_member_allocations_sync(
                project_name=project_name,
                enforce_limit=enforce_member_spend_limits,
            )
        except Exception as exc:
            logger.warning(
                f"budget_event=member_resync_on_toggle_failed component=project_service "
                f"project_name={project_name!r} enforce_limit={enforce_member_spend_limits} error={exc}"
            )

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
    def _validate_project_description(cls, description: str) -> str | None:
        stripped = description.strip()
        if not stripped:
            return None

        if len(stripped) > cls.MAX_PROJECT_DESCRIPTION_LENGTH:
            raise ExtendedHTTPException(code=400, message=cls.ERRORS.DESC_TOO_LONG)

        return stripped

    @classmethod
    def _validate_display_name(cls, display_name: str) -> str:
        stripped = display_name.strip()
        if len(stripped) > cls.MAX_DISPLAY_NAME_LENGTH:
            raise ExtendedHTTPException(code=400, message=cls.ERRORS.DISPLAY_NAME_TOO_LONG)
        return stripped

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

        Budgets and budget groups are detached first, since both hold a foreign key to the project.

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

        affected_budget_ids = budget_repository.clear_project_on_deleted_budgets(session, project_name)
        affected_group_ids = project_budget_group_repository.clear_project_on_deleted_groups(session, project_name)

        application_repository.delete_by_name(session, project_name)
        if creator_id is not None:
            user_project_repository.remove_project(session, creator_id, project_name)
            ProjectAssignmentService._sync_project_budget_member_removed(session, project_name, creator_id)
            invalidate_user_from_cache(creator_id)
        activity_event_repository.insert(
            ActivityEventCreate(
                domain=ActivityDomain.PROJECT_MANAGEMENT,
                event_type=ProjectManagementEvent.PROJECT_DELETED,
                entity_type=ActivityEntityType.PROJECT,
                entity_id=project_name,
                actor_id=actor_id,
                attributes={
                    "affected_budgets": affected_budget_ids,
                    "affected_budget_groups": affected_group_ids,
                },
            ),
            session,
        )
        logger.info(f"project_deleted: project={project_name}, by={actor_id}")


project_service = ProjectService()
