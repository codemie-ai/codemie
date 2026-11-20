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

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from codemie.rest_api.models.user_model_override import UserModelOverride


class UserModelOverrideRepository:
    """Repository for UserModelOverride CRUD operations."""

    @staticmethod
    def get_by_user_and_project(session: Session, user_id: str, project_name: str) -> UserModelOverride | None:
        """Fetch override for a specific user in a project.

        Args:
            session: Database session
            user_id: User ID
            project_name: Project name

        Returns:
            UserModelOverride if found, None otherwise
        """
        statement = select(UserModelOverride).where(
            UserModelOverride.user_id == user_id,
            UserModelOverride.project_name == project_name,
        )
        result = session.execute(statement)
        return result.scalars().first()

    @staticmethod
    def get_by_project(session: Session, project_name: str, skip: int = 0, limit: int = 100) -> list[UserModelOverride]:
        """Fetch all overrides for a project with pagination.

        Args:
            session: Database session
            project_name: Project name
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            List of UserModelOverride records
        """
        statement = (
            select(UserModelOverride)
            .where(UserModelOverride.project_name == project_name)
            .order_by(UserModelOverride.user_id, UserModelOverride.id)
            .offset(skip)
            .limit(limit)
        )
        result = session.execute(statement)
        return result.scalars().all()

    @staticmethod
    def create(
        session: Session,
        user_id: str,
        project_name: str,
        allowed_models: list[str],
        default_model: str | None = None,
    ) -> UserModelOverride:
        """Create a new override.

        Args:
            session: Database session
            user_id: User ID
            project_name: Project name
            allowed_models: List of allowed model IDs
            default_model: Preferred default model for this user in this project

        Returns:
            Created UserModelOverride record
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        override = UserModelOverride(
            user_id=user_id,
            project_name=project_name,
            allowed_models=allowed_models,
            default_model=default_model,
            date=now,
            update_date=now,
        )
        session.add(override)
        session.flush()
        return override

    @staticmethod
    def update(
        session: Session,
        user_id: str,
        project_name: str,
        allowed_models: list[str],
        default_model: str | None = None,
    ) -> UserModelOverride:
        """Update existing override's allowed_models and default_model.

        Args:
            session: Database session
            user_id: User ID
            project_name: Project name
            allowed_models: New list of allowed model IDs
            default_model: New preferred default model, or None to clear it

        Returns:
            Updated UserModelOverride record, or None if not found
        """
        override = UserModelOverrideRepository.get_by_user_and_project(session, user_id, project_name)
        if override:
            override.allowed_models = allowed_models
            override.default_model = default_model
            override.update_date = datetime.now(UTC).replace(tzinfo=None)
            session.flush()
        return override

    @staticmethod
    def get_by_users_and_project(
        session: Session, user_ids: list[str], project_name: str
    ) -> dict[str, UserModelOverride]:
        """Fetch the overrides of several users in a project with one query, keyed by user ID."""
        statement = select(UserModelOverride).where(
            UserModelOverride.project_name == project_name,
            UserModelOverride.user_id.in_(user_ids),
        )
        result = session.execute(statement)
        return {override.user_id: override for override in result.scalars().all()}

    @staticmethod
    def upsert_many(  # noqa: PLR0913 - one argument per override field, no natural grouping
        session: Session,
        project_name: str,
        user_ids: list[str],
        allowed_models: list[str],
        default_model: str | None,
        existing: dict[str, UserModelOverride],
    ) -> list[UserModelOverride]:
        """Give every user the same override: update the rows in `existing`, create the rest, flush once.

        Returns the overrides in the order of `user_ids`.
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        results = []
        for user_id in user_ids:
            override = existing.get(user_id)
            if override:
                override.allowed_models = list(allowed_models)
                override.default_model = default_model
                override.update_date = now
            else:
                override = UserModelOverride(
                    user_id=user_id,
                    project_name=project_name,
                    allowed_models=list(allowed_models),
                    default_model=default_model,
                    date=now,
                    update_date=now,
                )
                session.add(override)
            results.append(override)
        session.flush()
        return results

    @staticmethod
    def delete_many(session: Session, overrides: list[UserModelOverride]) -> None:
        """Delete the given overrides with a single flush."""
        for override in overrides:
            session.delete(override)
        session.flush()

    @staticmethod
    def delete(session: Session, user_id: str, project_name: str) -> UserModelOverride | None:
        """Delete override for a user in a project.

        Args:
            session: Database session
            user_id: User ID
            project_name: Project name

        Returns:
            The deleted record, or None if there was nothing to delete
        """
        override = UserModelOverrideRepository.get_by_user_and_project(session, user_id, project_name)
        if override:
            session.delete(override)
            session.flush()
        return override

    @staticmethod
    def delete_all_by_project(session: Session, project_name: str) -> None:
        """Delete all overrides for a project (admin cleanup).

        Args:
            session: Database session
            project_name: Project name
        """
        statement = select(UserModelOverride).where(UserModelOverride.project_name == project_name)
        result = session.execute(statement)
        overrides = result.scalars().all()
        for override in overrides:
            session.delete(override)
        session.flush()
