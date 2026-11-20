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

"""UserModelOverrideService - manages user-level LLM model overrides within projects.

Provides orchestration for creating, updating, deleting, and batch-managing user model
overrides within projects.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from codemie.core.models import Application
from codemie.rest_api.models.user_model_override import UserModelOverride
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.repository.user_model_override_repository import UserModelOverrideRepository
from codemie.core.exceptions import ModelNotSelectableError, ModelNotWhitelistedException, NotFoundException
from codemie.service.activity.activity_attributes import build_change_attributes
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    UserManagementEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.llm.model_availability_service import is_known_model, model_aliases
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.llm.user_override_cache import clear_request_override_cache

_TRACKED_FIELDS = ("allowed_models", "default_model")


class BatchValidationError(Exception):
    """A batch operation failed for one user; the whole batch is rolled back by the caller."""

    def __init__(self, failed_user_id: str | None, reason: str):
        super().__init__(reason)
        self.failed_user_id = failed_user_id
        self.reason = reason


class UserModelOverrideService:
    """Service for managing user-level model overrides.

    Validates that:
    - User is a member of the project
    - Project exists
    - override models are non-empty and non-blank
    - default_model, if provided, is one of the override's allowed_models

    Override models are not restricted to the project's own allowed_models.
    """

    @staticmethod
    def set_override(  # noqa: PLR0913 - one argument per override field plus session and actor
        session: Session,
        user_id: str,
        project_name: str,
        allowed_models: list[str],
        default_model: str | None = None,
        *,
        actor_id: str,
        actor_email: str,
    ) -> UserModelOverride:
        """Create or update override for a user in a project.

        Validates all constraints before creating or updating, and records an activity event
        in the same transaction when the stored override changes.

        Args:
            session: Database session
            user_id: User ID
            project_name: Project name
            allowed_models: List of allowed model IDs
            default_model: Preferred default model; must be one of allowed_models if provided
            actor_id: ID of the admin making the change
            actor_email: Email of the admin making the change

        Returns:
            Created or updated UserModelOverride

        Raises:
            NotFoundException: If project or user not found
            ValueError: If user is not a project member, allowed_models is empty or contains blank ids,
                or a model the override does not already list is deprecated
            ModelNotWhitelistedException: If default_model is not in allowed_models
        """
        UserModelOverrideService._require_project(session, project_name)

        user_result = session.execute(select(UserDB).where(UserDB.id == str(user_id)))
        if not user_result.scalars().first():
            raise NotFoundException("User not found")

        statement = select(UserProject).where(
            UserProject.user_id == str(user_id),
            UserProject.project_name == project_name,
        )
        result = session.execute(statement)
        user_project = result.scalars().first()
        if not user_project:
            raise ValueError(f"User {user_id} is not a member of project {project_name}")

        # Validate models
        UserModelOverrideService._validate_override_models(session, project_name, allowed_models)
        UserModelOverrideService._validate_default_model(project_name, allowed_models, default_model)
        existing = UserModelOverrideRepository.get_by_user_and_project(session, user_id, project_name)
        UserModelOverrideService._reject_new_deprecated_models(
            allowed_models, existing.allowed_models if existing else []
        )

        override, event = UserModelOverrideService._upsert(
            session, user_id, project_name, allowed_models, default_model, actor_id, actor_email, existing
        )
        if event:
            activity_event_repository.insert_strict(event, session)
        clear_request_override_cache()
        return override

    @staticmethod
    def get_override(session: Session, user_id: str, project_name: str) -> UserModelOverride | None:
        """Fetch override for a user in a project.

        Args:
            session: Database session
            user_id: User ID
            project_name: Project name

        Returns:
            UserModelOverride if exists, None otherwise
        """
        return UserModelOverrideRepository.get_by_user_and_project(session, user_id, project_name)

    @staticmethod
    def delete_override(
        session: Session, user_id: str, project_name: str, *, actor_id: str, actor_email: str
    ) -> UserModelOverride | None:
        """Delete override for a user in a project, recording an activity event when one was removed.

        Args:
            session: Database session
            user_id: User ID
            project_name: Project name
            actor_id: ID of the admin making the change
            actor_email: Email of the admin making the change

        Returns:
            The deleted override, or None if there was none
        """
        deleted = UserModelOverrideRepository.delete(session, user_id, project_name)
        if deleted:
            activity_event_repository.insert_strict(
                UserModelOverrideService._removed_event(deleted, actor_id, actor_email), session
            )
        clear_request_override_cache()
        return deleted

    @staticmethod
    def get_overrides_by_project(
        session: Session, project_name: str, skip: int = 0, limit: int = 100
    ) -> list[UserModelOverride]:
        """List all overrides for a project with pagination.

        Args:
            session: Database session
            project_name: Project name
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            List of UserModelOverride records

        Raises:
            NotFoundException: If project not found
        """
        UserModelOverrideService._require_project(session, project_name)
        return UserModelOverrideRepository.get_by_project(session, project_name, skip, limit)

    @staticmethod
    def batch_set_overrides(  # noqa: PLR0913 - one argument per override field plus session and actor
        session: Session,
        project_name: str,
        user_ids: list[str],
        operation: str,
        allowed_models: list[str] | None = None,
        default_model: str | None = None,
        *,
        actor_id: str,
        actor_email: str,
    ) -> list[UserModelOverride]:
        """Apply or revoke override for multiple users (atomic transaction).

        Validates models once before applying to all users, rolling back on first error.

        Args:
            session: Database session
            project_name: Project name
            user_ids: List of user IDs
            operation: 'grant' (create/update) or 'revoke' (delete)
            allowed_models: Required for 'grant', ignored for 'revoke'
            default_model: Preferred default model applied to every user; must be one of
                allowed_models if provided; ignored for 'revoke'
            actor_id: ID of the admin making the change; recorded on one activity event per changed user
            actor_email: Email of the admin making the change

        Returns:
            List of modified overrides (created/updated for grant, deleted for revoke)

        Raises:
            ValueError: If grant has no allowed_models, allowed_models contains blank ids, or a model
                that not every user's override already lists is deprecated
            BatchValidationError: If any user is not in the project
            NotFoundException: If project or any user not found
            ModelNotWhitelistedException: If default_model is not in allowed_models
        """
        if operation not in ("grant", "revoke"):
            raise ValueError(f"Unknown batch operation: {operation!r}")

        UserModelOverrideService._require_project(session, project_name)

        if operation == "grant":
            changed = UserModelOverrideService._batch_grant(
                session, project_name, user_ids, allowed_models, default_model, actor_id, actor_email
            )
        else:
            changed = UserModelOverrideService._batch_revoke(session, project_name, user_ids, actor_id, actor_email)
        clear_request_override_cache()
        return changed

    @staticmethod
    def _batch_grant(  # noqa: PLR0913 - one argument per override field plus session and actor
        session: Session,
        project_name: str,
        user_ids: list[str],
        allowed_models: list[str] | None,
        default_model: str | None,
        actor_id: str,
        actor_email: str,
    ) -> list[UserModelOverride]:
        """Validate models once, then create or update the override for each user.

        Users, memberships and existing overrides are read with one query each, and the rows are
        written with one flush, so the cost does not grow with the number of users.
        """
        if not allowed_models:
            raise ValueError("allowed_models required for grant operation")

        UserModelOverrideService._validate_override_models(session, project_name, allowed_models)
        UserModelOverrideService._validate_default_model(project_name, allowed_models, default_model)
        UserModelOverrideService._require_project_members(session, project_name, user_ids)

        existing = UserModelOverrideRepository.get_by_users_and_project(session, user_ids, project_name)
        # A model counts as kept only when every user it is granted to already lists it, under any of its
        # names: one user's override may store the base_name and another's the deployment_name.
        kept_sets = [
            {alias for model in existing[u].allowed_models for alias in model_aliases(model)}
            if u in existing
            else set()
            for u in user_ids
        ]
        kept_by_all = set.intersection(*kept_sets) if kept_sets else set()
        UserModelOverrideService._reject_new_deprecated_models(allowed_models, kept_by_all)
        previous = {
            user_id: {"allowed_models": list(o.allowed_models), "default_model": o.default_model}
            for user_id, o in existing.items()
        }
        results = UserModelOverrideRepository.upsert_many(
            session, project_name, user_ids, allowed_models, default_model, existing
        )
        events = [
            event
            for user_id in user_ids
            if (
                event := UserModelOverrideService._set_event(
                    user_id,
                    project_name,
                    previous.get(user_id, {}),
                    allowed_models,
                    default_model,
                    actor_id,
                    actor_email,
                )
            )
        ]
        activity_event_repository.insert_many_strict(events, session)
        return results

    @staticmethod
    def _upsert(  # noqa: PLR0913 - one argument per override field plus session and actor
        session: Session,
        user_id: str,
        project_name: str,
        allowed_models: list[str],
        default_model: str | None,
        actor_id: str,
        actor_email: str,
        existing: UserModelOverride | None,
    ) -> tuple[UserModelOverride, ActivityEventCreate | None]:
        """Create or update the override; the event is None when the stored override did not change."""
        previous = (
            {"allowed_models": list(existing.allowed_models), "default_model": existing.default_model}
            if existing
            else {}
        )
        if existing:
            override = UserModelOverrideRepository.update(session, user_id, project_name, allowed_models, default_model)
        else:
            override = UserModelOverrideRepository.create(session, user_id, project_name, allowed_models, default_model)
        event = UserModelOverrideService._set_event(
            user_id, project_name, previous, allowed_models, default_model, actor_id, actor_email
        )
        return override, event

    @staticmethod
    def _set_event(  # noqa: PLR0913 - one argument per override field plus session and actor
        user_id: str,
        project_name: str,
        previous: dict,
        allowed_models: list[str],
        default_model: str | None,
        actor_id: str,
        actor_email: str,
    ) -> ActivityEventCreate | None:
        """The event for a change to the stored override, or None when nothing changed."""
        changes = build_change_attributes(
            previous,
            {"allowed_models": allowed_models, "default_model": default_model},
            value_fields=_TRACKED_FIELDS,
        )
        if not changes["updated_fields"]:
            return None
        return ActivityEventCreate(
            domain=ActivityDomain.USER_MANAGEMENT,
            event_type=UserManagementEvent.USER_MODEL_OVERRIDE_SET,
            entity_type=ActivityEntityType.USER,
            entity_id=str(user_id),
            entity_label=str(user_id),
            actor_id=actor_id,
            actor_email=actor_email,
            project_name=project_name,
            attributes={"project_name": project_name, **changes},
        )

    @staticmethod
    def _removed_event(override: UserModelOverride, actor_id: str, actor_email: str) -> ActivityEventCreate:
        return ActivityEventCreate(
            domain=ActivityDomain.USER_MANAGEMENT,
            event_type=UserManagementEvent.USER_MODEL_OVERRIDE_REMOVED,
            entity_type=ActivityEntityType.USER,
            entity_id=str(override.user_id),
            entity_label=str(override.user_id),
            actor_id=actor_id,
            actor_email=actor_email,
            project_name=override.project_name,
            attributes={
                "project_name": override.project_name,
                "previous_allowed_models": list(override.allowed_models),
                "previous_default_model": override.default_model,
            },
        )

    @staticmethod
    def _require_project_members(session: Session, project_name: str, user_ids: list[str]) -> None:
        """Raise unless every user exists and is a member of the project; the first failing user in order is named."""
        found = session.execute(select(UserDB.id).where(UserDB.id.in_(user_ids)))
        existing_users = set(found.scalars().all())
        members = session.execute(
            select(UserProject.user_id).where(
                UserProject.project_name == project_name,
                UserProject.user_id.in_(user_ids),
            )
        )
        member_ids = set(members.scalars().all())
        for user_id in user_ids:
            if user_id not in existing_users:
                raise NotFoundException("User not found")
            if user_id not in member_ids:
                raise BatchValidationError(user_id, "User not a member of this project")

    @staticmethod
    def _batch_revoke(
        session: Session, project_name: str, user_ids: list[str], actor_id: str, actor_email: str
    ) -> list[UserModelOverride]:
        """Remove the override from each user, collecting what was actually deleted."""
        existing = UserModelOverrideRepository.get_by_users_and_project(session, user_ids, project_name)
        results = [existing[user_id] for user_id in user_ids if user_id in existing]
        UserModelOverrideRepository.delete_many(session, results)
        activity_event_repository.insert_many_strict(
            [UserModelOverrideService._removed_event(o, actor_id, actor_email) for o in results], session
        )
        return results

    @staticmethod
    def _validate_override_models(session: Session, project_name: str, allowed_models: list[str]) -> None:
        """Validate override models independently of the project whitelist.

        Checks:
        1. allowed_models is not empty
        2. Every id names a catalog model or an enabled router that is not disabled at platform level
        3. Project exists

        Override models are no longer required to be a subset of the project's
        allowed_models: admins may grant a user access to any platform model
        regardless of the project's own whitelist (platform-disabled models remain
        out of scope; see Section 8 of the spec).

        Args:
            session: Database session
            project_name: Project name
            allowed_models: Models to validate

        Raises:
            ValueError: If allowed_models is empty, or contains a blank, unknown or platform-disabled model id
            NotFoundException: If project not found
        """
        if not allowed_models or any(not m or not m.strip() for m in allowed_models):
            raise ValueError("allowed_models must be a non-empty list of non-blank model ids")

        unknown = [m for m in allowed_models if not is_known_model(m)]
        if unknown:
            raise ValueError(f"Unknown or platform-disabled model ids: {unknown}")

        UserModelOverrideService._require_project(session, project_name)

    @staticmethod
    def _reject_new_deprecated_models(allowed_models: list[str], kept_models: Iterable[str]) -> None:
        """Reject a deprecated model the override does not already list.

        Platform model management lets a deprecated model keep working where it is already selected,
        but never be selected anew; assert_model_selectable is that rule for every other save path
        (project default, assistant, workflow, conversation). A model the stored override already lists,
        by any of its names, is kept so an admin can edit an override after one of its models was
        deprecated. default_model is one of allowed_models, so checking the list covers it.

        Raises:
            ValueError: If a model not already in the override is deprecated
        """
        kept = set(kept_models)
        deprecated = []
        for model in allowed_models:
            if model_aliases(model) & kept:
                continue
            try:
                llm_service.assert_model_selectable(model)
            except ModelNotSelectableError:
                deprecated.append(model)
        if deprecated:
            raise ValueError(f"Deprecated model ids cannot be added to an override: {deprecated}")

    @staticmethod
    def _require_project(session: Session, project_name: str) -> None:
        result = session.execute(select(Application).where(Application.id == project_name))
        if not result.scalars().first():
            raise NotFoundException(f"Project {project_name} not found")

    @staticmethod
    def _validate_default_model(project_name: str, allowed_models: list[str], default_model: str | None) -> None:
        """Validate that default_model, if provided, is one of the override's allowed_models.

        Args:
            project_name: Project name
            allowed_models: Models allowed by this override
            default_model: Preferred default model, or None

        Raises:
            ModelNotWhitelistedException: If default_model is not in allowed_models
        """
        if default_model and default_model not in allowed_models:
            raise ModelNotWhitelistedException(
                model_id=default_model,
                project_name=project_name,
                details=f"default_model must be one of the override's allowed_models: {allowed_models}",
            )
