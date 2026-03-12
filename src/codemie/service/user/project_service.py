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
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from codemie.clients.postgres import get_session
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.security.user import User


class ProjectService:
    """Service for shared project creation business rules."""

    MIN_PROJECT_NAME_LENGTH: Final[int] = 3
    MAX_PROJECT_NAME_LENGTH: Final[int] = 100
    MAX_PROJECT_DESCRIPTION_LENGTH: Final[int] = 500
    PROJECT_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
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
    INVALID_PROJECT_NAME_MESSAGE: Final[str] = (
        "Invalid project name. Must be 3-100 alphanumeric characters (underscore/hyphen allowed), "
        "cannot start with special character, and cannot use reserved names "
        "(admin, system, root, api, null, undefined, default, test, demo)"
    )
    LIMIT_REACHED_MESSAGE_TEMPLATE: Final[str] = (
        "Project creation limit reached ({count}/{limit}). Contact administrator to increase your limit."
    )
    GRANDFATHERED_LIMIT_MESSAGE_TEMPLATE: Final[str] = (
        "Project creation limit reached ({count}/{limit}). Delete {excess} or more projects to create new ones."
    )
    INVALID_LIMIT_CONFIGURATION_MESSAGE: Final[str] = "Invalid project limit configuration. Contact administrator."

    @classmethod
    def create_shared_project(cls, user: User, project_name: str, description: str) -> Application:
        """Create a shared project and grant creator project-admin membership."""
        validated_name = cls._validate_shared_project_name(project_name)
        validated_description = cls._validate_project_description(description)

        with get_session() as session:
            cls._enforce_project_limit(session, user)

            existing = application_repository.get_by_name_case_insensitive(session, validated_name)
            if existing:
                raise ExtendedHTTPException(
                    code=409,
                    message=cls._duplicate_project_message(existing.name),
                )

            try:
                project = application_repository.create(
                    session=session,
                    name=validated_name,
                    description=validated_description,
                    project_type="shared",
                    created_by=user.id,
                )
                user_project_repository.add_project(
                    session=session,
                    user_id=user.id,
                    project_name=validated_name,
                    is_project_admin=True,
                )
                # Expunge before commit to preserve loaded attributes for the caller
                # It is legitimate, because for project flush/refresh is called inside create() method
                session.expunge(project)
                # create() uses flush/refresh, but both inserts remain in one transaction and are committed atomically.
                session.commit()
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
    def _validate_shared_project_name(cls, name: str) -> str:
        if len(name) < cls.MIN_PROJECT_NAME_LENGTH:
            raise ExtendedHTTPException(code=400, message="Project name must be at least 3 characters")

        if len(name) > cls.MAX_PROJECT_NAME_LENGTH:
            raise ExtendedHTTPException(code=400, message="Project name cannot exceed 100 characters")

        if not cls.PROJECT_NAME_PATTERN.match(name):
            raise ExtendedHTTPException(code=400, message=cls.INVALID_PROJECT_NAME_MESSAGE)

        if name.lower() in cls.RESERVED_PROJECT_NAMES:
            raise ExtendedHTTPException(code=400, message=f"Project name '{name}' is reserved and cannot be used")

        return name

    @classmethod
    def _validate_project_description(cls, description: str) -> str:
        if not description.strip():
            raise ExtendedHTTPException(code=400, message="Project description is required")

        if len(description) > cls.MAX_PROJECT_DESCRIPTION_LENGTH:
            raise ExtendedHTTPException(code=400, message="Project description cannot exceed 500 characters")

        return description

    @staticmethod
    def _enforce_project_limit(session: Session, user: User) -> None:
        if user.is_super_admin:
            return

        persisted_user = user_repository.get_active_by_id(session, user.id)
        if not persisted_user:
            # Aligns with current authentication flow semantics for inactive/soft-deleted accounts.
            raise ExtendedHTTPException(code=401, message="Account is deactivated")

        if persisted_user.project_limit is None:
            # Fail closed: non-super-admin users must never bypass limits via NULL configuration.
            raise ExtendedHTTPException(
                code=403,
                message=ProjectService.INVALID_LIMIT_CONFIGURATION_MESSAGE,
            )

        created_count = application_repository.count_shared_projects_created_by_user(session, user.id)
        if created_count < persisted_user.project_limit:
            return

        if created_count > persisted_user.project_limit:
            excess_projects = created_count - persisted_user.project_limit
            raise ExtendedHTTPException(
                code=403,
                message=ProjectService.GRANDFATHERED_LIMIT_MESSAGE_TEMPLATE.format(
                    count=created_count,
                    limit=persisted_user.project_limit,
                    excess=excess_projects,
                ),
            )

        raise ExtendedHTTPException(
            code=403,
            message=ProjectService.LIMIT_REACHED_MESSAGE_TEMPLATE.format(
                count=created_count,
                limit=persisted_user.project_limit,
            ),
        )

    @staticmethod
    def _duplicate_project_message(project_name: str) -> str:
        return f"Project '{project_name}' already exists. Please choose a different name."


project_service = ProjectService()
