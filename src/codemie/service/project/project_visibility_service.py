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
from codemie.repository.cost_center_repository import cost_center_repository


class ProjectVisibilityService:
    """Service for project visibility and project-level authorization checks."""

    @staticmethod
    def list_visible_projects(
        session: Session,
        user_id: str,
        is_admin: bool,
        search: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Application]:
        """List projects visible to user with optional search."""
        return application_repository.list_visible_projects(
            session=session,
            user_id=user_id,
            is_admin=is_admin,
            search=search,
            limit=limit,
        )

    @staticmethod
    def list_visible_projects_paginated(
        session: Session,
        user_id: str,
        is_admin: bool,
        search: Optional[str] = None,
        page: int = 0,
        per_page: int = 20,
        include_counters: bool = True,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> tuple[list[dict], int]:
        """List projects visible to user with pagination and member counts.

        Story 16: Project list endpoint with pagination + counts.

        Returns:
            Tuple of (enriched_projects, total_count) where each project dict includes:
            - name, description, project_type, created_by, created_at (from Application)
            - user_count, admin_count (aggregated from user_projects)
            - counters (dict with resource counts, or None if include_counters=False)
        """
        projects, total_count = application_repository.list_visible_projects_paginated(
            session=session,
            user_id=user_id,
            is_admin=is_admin,
            search=search,
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        if not projects:
            return [], total_count

        # Bulk-load member counts for all projects
        project_names = [p.name for p in projects]
        member_counts = application_repository.get_project_member_counts_bulk(session, project_names)
        cost_center_map = cost_center_repository.get_by_ids(
            session, [project.cost_center_id for project in projects if project.cost_center_id]
        )

        # Bulk-load entity counts when requested
        entity_counts: dict[str, dict] = {}
        if include_counters:
            entity_counts = application_repository.get_project_entity_counts_bulk(session, project_names)

        # Enrich projects with counts
        enriched = []
        for project in projects:
            user_count, admin_count = member_counts.get(project.name, (0, 0))
            cost_center = cost_center_map.get(project.cost_center_id) if project.cost_center_id else None
            enriched.append(
                {
                    "name": project.name,
                    "description": project.description,
                    "project_type": project.project_type,
                    "created_by": project.created_by,
                    "created_at": project.date,
                    "user_count": user_count,
                    "admin_count": admin_count,
                    "counters": entity_counts.get(project.name) if include_counters else None,
                    "cost_center_id": project.cost_center_id,
                    "cost_center_name": cost_center.name if cost_center else None,
                }
            )

        return enriched, total_count

    @staticmethod
    def get_visible_project_or_404(
        session: Session,
        project_name: str,
        user_id: str,
        is_admin: bool,
        action: str,
    ) -> Application:
        """Get project by name if visible, otherwise 404."""
        project = application_repository.get_visible_project(
            session=session,
            project_name=project_name,
            user_id=user_id,
            is_admin=is_admin,
        )
        if not project:
            timestamp = datetime.now(UTC).isoformat()
            http_method = action.split()[0] if action else "UNKNOWN"
            logger.warning(
                f"project_authorization_failed: user_id={user_id}, method={http_method}, timestamp={timestamp}"
            )
            raise ExtendedHTTPException(code=404, message="Project not found")
        return project

    @staticmethod
    def get_visible_project_with_members(
        session: Session,
        project_name: str,
        user_id: str,
        is_admin: bool,
        action: str,
    ) -> dict:
        """Get project detail with member list if visible, otherwise 404.

        Story 16: Project detail endpoint includes member list.

        Returns:
            Dict with project fields + members list
        """
        project = ProjectVisibilityService.get_visible_project_or_404(session, project_name, user_id, is_admin, action)

        # Get member counts
        member_counts = application_repository.get_project_member_counts_bulk(session, [project.name])
        user_count, admin_count = member_counts.get(project.name, (0, 0))
        cost_center = (
            cost_center_repository.get_by_id(session, project.cost_center_id) if project.cost_center_id else None
        )

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
            "cost_center_id": project.cost_center_id,
            "cost_center_name": cost_center.name if cost_center else None,
            "members": member_list,
        }


project_visibility_service = ProjectVisibilityService()
