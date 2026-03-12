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

from datetime import UTC, datetime
from typing import Optional

from sqlmodel import Session

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository


class ProjectVisibilityService:
    """Service for project visibility and project-level authorization checks."""

    @staticmethod
    def _is_project_visible_to_user(
        project: Application,
        user_id: str,
        is_super_admin: bool,
        membership_is_project_admin: bool | None,
    ) -> bool:
        """Evaluate Story 11 visibility rules using pre-fetched context."""
        if is_super_admin:
            return True

        if project.project_type == "personal":
            return project.created_by == user_id

        return membership_is_project_admin is not None

    @staticmethod
    def raise_project_not_found(user_id: str, project_name: str, action: str) -> None:
        timestamp = datetime.now(UTC).isoformat()
        # Code Review R3: Do not log project_name OR action path (both may contain PII)
        # action format is "METHOD /path/with/project_name" - extract only HTTP method
        http_method = action.split()[0] if action else "UNKNOWN"
        log_details = ", ".join(
            [
                f"user_id={user_id}",
                f"method={http_method}",
                f"timestamp={timestamp}",
            ]
        )
        logger.warning(f"project_authorization_failed: {log_details}")
        raise ExtendedHTTPException(
            code=404,
            message="Project not found",
        )

    @staticmethod
    def list_visible_projects(
        session: Session,
        user_id: str,
        is_super_admin: bool,
        search: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Application]:
        """List projects visible to user with optional search."""
        return application_repository.list_visible_projects(
            session=session,
            user_id=user_id,
            is_super_admin=is_super_admin,
            search=search,
            limit=limit,
        )

    @staticmethod
    def list_visible_projects_paginated(
        session: Session,
        user_id: str,
        is_super_admin: bool,
        search: Optional[str] = None,
        page: int = 0,
        per_page: int = 20,
    ) -> tuple[list[dict], int]:
        """List projects visible to user with pagination and member counts.

        Story 16: Project list endpoint with pagination + counts.

        Returns:
            Tuple of (enriched_projects, total_count) where each project dict includes:
            - name, description, project_type, created_by, created_at (from Application)
            - user_count, admin_count (aggregated from user_projects)
        """
        projects, total_count = application_repository.list_visible_projects_paginated(
            session=session,
            user_id=user_id,
            is_super_admin=is_super_admin,
            search=search,
            page=page,
            per_page=per_page,
        )

        if not projects:
            return [], total_count

        # Bulk-load member counts for all projects
        project_names = [p.name for p in projects]
        member_counts = application_repository.get_project_member_counts_bulk(session, project_names)

        # Enrich projects with counts
        enriched = []
        for project in projects:
            user_count, admin_count = member_counts.get(project.name, (0, 0))
            enriched.append(
                {
                    "name": project.name,
                    "description": project.description,
                    "project_type": project.project_type,
                    "created_by": project.created_by,
                    "created_at": project.date,
                    "user_count": user_count,
                    "admin_count": admin_count,
                }
            )

        return enriched, total_count

    @staticmethod
    def get_visible_project_or_404(
        session: Session,
        project_name: str,
        user_id: str,
        is_super_admin: bool,
        action: str,
    ) -> Application:
        """Get project by name if visible, otherwise 404."""
        project = application_repository.get_visible_project(
            session=session,
            project_name=project_name,
            user_id=user_id,
            is_super_admin=is_super_admin,
        )
        if not project:
            ProjectVisibilityService.raise_project_not_found(user_id, project_name, action)
        return project

    @staticmethod
    def get_visible_project_with_members(
        session: Session,
        project_name: str,
        user_id: str,
        is_super_admin: bool,
        action: str,
    ) -> dict:
        """Get project detail with member list if visible, otherwise 404.

        Story 16: Project detail endpoint includes member list.

        Returns:
            Dict with project fields + members list
        """
        project = ProjectVisibilityService.get_visible_project_or_404(
            session, project_name, user_id, is_super_admin, action
        )

        # Get member counts
        member_counts = application_repository.get_project_member_counts_bulk(session, [project.name])
        user_count, admin_count = member_counts.get(project.name, (0, 0))

        # Get member list
        members = application_repository.get_project_members(session, project.name)
        member_list = [
            {
                "user_id": member.user_id,
                "is_project_admin": member.is_project_admin,
                "date": member.date,
            }
            for member in members
        ]

        return {
            "name": project.name,
            "description": project.description,
            "project_type": project.project_type,
            "created_by": project.created_by,
            "created_at": project.date,
            "user_count": user_count,
            "admin_count": admin_count,
            "members": member_list,
        }

    @staticmethod
    def ensure_project_admin_or_super_admin_or_404(
        session: Session,
        project_name: str,
        user_id: str,
        is_super_admin: bool,
        action: str,
    ) -> Application:
        """Require project visibility and project-admin/super-admin permissions."""
        authorization_context = application_repository.get_project_authorization_context(
            session=session,
            project_name=project_name,
            user_id=user_id,
        )
        if not authorization_context:
            ProjectVisibilityService.raise_project_not_found(user_id, project_name, action)

        project, membership_is_project_admin = authorization_context
        if not ProjectVisibilityService._is_project_visible_to_user(
            project,
            user_id,
            is_super_admin,
            membership_is_project_admin,
        ):
            ProjectVisibilityService.raise_project_not_found(user_id, project_name, action)

        if not is_super_admin and not bool(membership_is_project_admin):
            ProjectVisibilityService.raise_project_not_found(user_id, project_name, action)

        return project

    @staticmethod
    def authorize_project_admin_or_super_admin(
        project_name: str,
        user_id: str,
        is_super_admin: bool,
        action: str,
    ) -> Application:
        """Authorize project access via a service-layer DB session."""
        from codemie.clients.postgres import get_session

        with get_session() as session:
            return ProjectVisibilityService.ensure_project_admin_or_super_admin_or_404(
                session=session,
                project_name=project_name,
                user_id=user_id,
                is_super_admin=is_super_admin,
                action=action,
            )


project_visibility_service = ProjectVisibilityService()
