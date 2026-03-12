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

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from codemie.clients.postgres import get_session
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository


class ApplicationService:
    """Service for application/project creation business rules."""

    @staticmethod
    def create_application(name: str) -> Application:
        """Create a new application, enforcing case-insensitive name uniqueness.

        Raises ExtendedHTTPException(409) if an application with the same name
        (case-insensitive) already exists.

        Args:
            name: Application name (used as both id and name).

        Returns:
            The newly created Application record.
        """
        with get_session() as session:
            existing = application_repository.get_by_name_case_insensitive(session, name)
            if existing:
                raise ExtendedHTTPException(
                    code=409,
                    message=f"Application '{existing.name}' already exists. Please choose a different name.",
                )

            try:
                app = application_repository.create(session=session, name=name)
                # Expunge before commit: create() calls flush+refresh so attributes are loaded.
                # Committing first would expire them (expire_on_commit=True), causing
                # DetachedInstanceError when app.name is accessed after the session closes.
                session.expunge(app)
                session.commit()
                return app
            except IntegrityError:
                session.rollback()
                raise ExtendedHTTPException(
                    code=409,
                    message=f"Application '{name}' already exists. Please choose a different name.",
                )

    @staticmethod
    def ensure_application_exists(project_name: str) -> None:
        """Ensure an Application record exists for the given project name.

        Idempotent: returns silently when an application with the same name
        already exists (case-insensitive match), preventing case-variant duplicates.

        Args:
            project_name: The project/application name to ensure exists.
        """
        if not project_name:
            return

        with get_session() as session:
            existing = application_repository.get_by_name_case_insensitive(session, project_name)
            if existing:
                return

            try:
                application_repository.create(session=session, name=project_name)
                session.commit()
                logger.info(f"Auto-created application for project: {project_name}")
            except IntegrityError:
                session.rollback()
                logger.debug(f"Application already exists (race condition): {project_name}")
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to create application for project {project_name}: {e}", exc_info=True)


application_service = ApplicationService()
