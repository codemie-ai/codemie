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

from datetime import datetime, UTC
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, func, select

from codemie.rest_api.models.user_management import UserProject


class UserProjectRepository:
    """Repository for user-project access management (sync SQLModel)"""

    def get_by_user_id(self, session: Session, user_id: str) -> list[UserProject]:
        """Get all projects for a user

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            List of UserProject records
        """
        statement = select(UserProject).where(UserProject.user_id == user_id)
        return list(session.exec(statement).all())

    def get_by_id(self, session: Session, project_id: str) -> Optional[UserProject]:
        """Get project access record by ID

        Args:
            session: Database session
            project_id: UserProject UUID

        Returns:
            UserProject or None
        """
        statement = select(UserProject).where(UserProject.id == project_id)
        return session.exec(statement).first()

    def get_by_user_and_project(self, session: Session, user_id: str, project_name: str) -> Optional[UserProject]:
        """Get specific project access for a user

        Args:
            session: Database session
            user_id: User UUID
            project_name: Project name

        Returns:
            UserProject or None
        """
        statement = select(UserProject).where(UserProject.user_id == user_id, UserProject.project_name == project_name)
        return session.exec(statement).first()

    def get_by_project_name(self, session: Session, project_name: str) -> list[UserProject]:
        """Get all users with access to a project

        Args:
            session: Database session
            project_name: Project name

        Returns:
            List of UserProject records for all users with access
        """
        statement = select(UserProject).where(UserProject.project_name == project_name)
        return list(session.exec(statement).all())

    def add_project(
        self, session: Session, user_id: str, project_name: str, is_project_admin: bool = False
    ) -> UserProject:
        """Grant project access to user

        Args:
            session: Database session
            user_id: User UUID
            project_name: Project name
            is_project_admin: Whether user is admin for this project

        Returns:
            Created UserProject record

        Note:
            Caller should handle duplicate check or rely on DB unique constraint
        """
        now = datetime.now(UTC)
        user_project = UserProject(
            user_id=user_id, project_name=project_name, is_project_admin=is_project_admin, date=now, update_date=now
        )
        session.add(user_project)
        session.flush()
        session.refresh(user_project)
        return user_project

    def remove_project(self, session: Session, user_id: str, project_name: str) -> bool:
        """Revoke project access from user

        Args:
            session: Database session
            user_id: User UUID
            project_name: Project name

        Returns:
            True if removed, False if not found
        """
        user_project = self.get_by_user_and_project(session, user_id, project_name)
        if not user_project:
            return False

        session.delete(user_project)
        session.flush()
        return True

    def update_admin_status(
        self, session: Session, user_id: str, project_name: str, is_project_admin: bool
    ) -> Optional[UserProject]:
        """Update project admin status for user

        Args:
            session: Database session
            user_id: User UUID
            project_name: Project name
            is_project_admin: New admin status

        Returns:
            Updated UserProject or None if not found
        """
        user_project = self.get_by_user_and_project(session, user_id, project_name)
        if not user_project:
            return None

        user_project.is_project_admin = is_project_admin
        user_project.update_date = datetime.now(UTC)
        session.add(user_project)
        session.flush()
        session.refresh(user_project)
        return user_project

    def get_admin_projects(self, session: Session, user_id: str) -> list[UserProject]:
        """Get projects where user is admin

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            List of UserProject records where is_project_admin=True
        """
        statement = select(UserProject).where(UserProject.user_id == user_id, UserProject.is_project_admin)
        return list(session.exec(statement).all())

    def has_access(self, session: Session, user_id: str, project_name: str) -> bool:
        """Check if user has access to project

        Args:
            session: Database session
            user_id: User UUID
            project_name: Project name

        Returns:
            True if user has access, False otherwise
        """
        return self.get_by_user_and_project(session, user_id, project_name) is not None

    def get_project_names_for_user(self, session: Session, user_id: str) -> set[str]:
        """Get project names for a user.

        Membership in `user_projects` is the authoritative source of truth
        for shared-project visibility decisions.

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            Set of project names the user belongs to
        """
        statement = select(UserProject.project_name).where(UserProject.user_id == user_id)
        return set(session.exec(statement).all())

    def is_admin(self, session: Session, user_id: str, project_name: str) -> bool:
        """Check if user is admin for project

        Args:
            session: Database session
            user_id: User UUID
            project_name: Project name

        Returns:
            True if user is project admin, False otherwise
        """
        user_project = self.get_by_user_and_project(session, user_id, project_name)
        return user_project is not None and user_project.is_project_admin

    def delete_all_for_user(self, session: Session, user_id: str) -> int:
        """Delete all project access for a user (used during user deletion)

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            Number of records deleted
        """
        projects = self.get_by_user_id(session, user_id)
        count = len(projects)
        for project in projects:
            session.delete(project)
        session.flush()
        return count

    def get_visible_projects_for_user(
        self, session: Session, target_user_id: str, requesting_user_id: str, is_super_admin: bool
    ) -> list[UserProject]:
        """Get projects visible to requesting user for target user

        Story 11 Visibility Rules:
        - Personal projects visible only to creator + super admin
        - Shared projects visible only to members + super admin

        Story 10 Code Review: Optimized to avoid N+1 queries by bulk-loading project types.

        Args:
            session: Database session
            target_user_id: User whose projects to retrieve
            requesting_user_id: User requesting the list
            is_super_admin: Whether requesting user is super admin

        Returns:
            List of UserProject records visible to requesting user
        """
        from codemie.repository.application_repository import application_repository

        # Get all projects for target user
        all_projects = self.get_by_user_id(session, target_user_id)

        if not all_projects:
            return []

        # Bulk load project types to avoid N+1 queries
        project_names = [p.project_name for p in all_projects]
        project_info = application_repository.get_project_types_bulk(session, project_names)

        requesting_user_project_names: set[str] | None = None
        if not is_super_admin:
            # Reuse target list for self-views to avoid an additional query.
            if requesting_user_id == target_user_id:
                requesting_user_project_names = {project.project_name for project in all_projects}
            else:
                requesting_user_project_names = self.get_project_names_for_user(session, requesting_user_id)

        # Filter by visibility
        return self._filter_projects_by_visibility(
            all_projects,
            project_info,
            requesting_user_id,
            is_super_admin,
            requesting_user_project_names,
        )

    def filter_visible_projects_from_map(
        self,
        session: Session,
        projects_map: dict[str, list[UserProject]],
        requesting_user_id: str,
        is_super_admin: bool,
    ) -> dict[str, list[UserProject]]:
        """Filter pre-fetched projects by visibility rules (for bulk operations)

        Story 10 Code Review R2: Prevents per-user re-querying in list_users() flow.
        Uses already-fetched projects_map from Story 7 JOIN optimization.

        Args:
            session: Database session
            projects_map: Dict mapping user_id -> list of UserProject (from JOIN query)
            requesting_user_id: User requesting the list
            is_super_admin: Whether requesting user is super admin

        Returns:
            Dict mapping user_id -> filtered list of visible UserProject records
        """
        from codemie.repository.application_repository import application_repository

        if not projects_map:
            return {}

        # Collect all unique project names across all users
        all_project_names = set()
        for user_projects in projects_map.values():
            all_project_names.update(p.project_name for p in user_projects)

        # Bulk load project types for all projects in single query
        project_info = application_repository.get_project_types_bulk(session, list(all_project_names))

        # Filter projects for each user
        filtered_map = {}
        requesting_user_project_names: set[str] | None = None
        if not is_super_admin:
            if requesting_user_id in projects_map:
                requesting_user_project_names = {project.project_name for project in projects_map[requesting_user_id]}
            else:
                requesting_user_project_names = self.get_project_names_for_user(session, requesting_user_id)

        for user_id, user_projects in projects_map.items():
            if not user_projects:
                filtered_map[user_id] = []
                continue

            visible_projects = self._filter_projects_by_visibility(
                user_projects,
                project_info,
                requesting_user_id,
                is_super_admin,
                requesting_user_project_names,
            )
            filtered_map[user_id] = visible_projects

        return filtered_map

    def _filter_projects_by_visibility(
        self,
        projects: list[UserProject],
        project_info: dict[str, tuple[str, str | None]],
        requesting_user_id: str,
        is_super_admin: bool,
        requesting_user_project_names: set[str] | None,
    ) -> list[UserProject]:
        """Internal helper to filter projects by visibility rules

        Story 11: Shared visibility is membership-based.

        Args:
            projects: List of UserProject records to filter
            project_info: Dict mapping project_name -> (project_type, created_by)
            requesting_user_id: User requesting the list
            is_super_admin: Whether requesting user is super admin
            requesting_user_project_names: Projects requester belongs to

        Returns:
            Filtered list of visible UserProject records
        """
        visible_projects = []
        for project in projects:
            # Get project type and creator from bulk-loaded data
            info = project_info.get(project.project_name)
            if not info:
                # Project doesn't exist in applications table - skip
                continue

            project_type, created_by = info
            is_personal = project_type == "personal"

            if is_super_admin:
                visible_projects.append(project)
                continue

            if is_personal:
                # Personal projects: creator only (super admin handled above)
                if created_by == requesting_user_id:
                    visible_projects.append(project)
                continue

            # Shared projects: requester must be a member
            if requesting_user_project_names and project.project_name in requesting_user_project_names:
                visible_projects.append(project)

        return visible_projects

    def get_by_users_and_project(
        self, session: Session, user_ids: list[str], project_name: str
    ) -> dict[str, UserProject]:
        """Get existing project assignments for multiple users in a single query.

        Args:
            session: Database session
            user_ids: List of user UUIDs
            project_name: Project name

        Returns:
            Dict mapping user_id to UserProject record (only for users that have assignments)
        """
        if not user_ids:
            return {}

        statement = select(UserProject).where(
            UserProject.user_id.in_(user_ids),  # type: ignore[attr-defined]
            UserProject.project_name == project_name,
        )
        results = session.exec(statement).all()
        return {up.user_id: up for up in results}

    def remove_projects_for_users(self, session: Session, user_ids: list[str], project_name: str) -> int:
        """Remove project access for multiple users in a single operation.

        Args:
            session: Database session
            user_ids: List of user UUIDs
            project_name: Project name

        Returns:
            Number of records deleted
        """
        if not user_ids:
            return 0

        statement = select(UserProject).where(
            UserProject.user_id.in_(user_ids),  # type: ignore[attr-defined]
            UserProject.project_name == project_name,
        )
        records = list(session.exec(statement).all())
        for record in records:
            session.delete(record)
        session.flush()
        return len(records)

    def can_project_admin_view_user(self, session: Session, admin_user_id: str, target_user_id: str) -> bool:
        """Check if project admin can view target user's details

        Story 18: Project admin authorization for user detail endpoint.

        Authorization logic:
        - Project admin can view user IF target user exists in ANY project where admin is admin
        - Uses single efficient query with IN subquery to check membership overlap

        Args:
            session: Database session
            admin_user_id: Project admin user ID
            target_user_id: Target user ID to check access for

        Returns:
            True if admin can view target user, False otherwise
        """
        # Check if target user exists in any project where admin is project admin
        # Single efficient query using IN subquery for membership overlap check
        target_projects_subquery = select(UserProject.project_name).where(UserProject.user_id == target_user_id)

        statement = (
            select(func.count())
            .select_from(UserProject)
            .where(
                UserProject.user_id == admin_user_id,
                UserProject.is_project_admin,
                UserProject.project_name.in_(target_projects_subquery),
            )
        )

        count = session.exec(statement).one()
        return int(count) > 0

    def get_admin_visible_projects_for_user(
        self, session: Session, target_user_id: str, admin_user_id: str
    ) -> list[UserProject]:
        """Get projects visible to project admin for target user

        Story 18: Response filtering for project admins.

        Filtering logic:
        - Returns ONLY projects where admin is also a MEMBER (not necessarily admin)
        - Admin's membership (not admin status) determines visibility
        - knowledge_bases array shown in full (no filtering)

        Args:
            session: Database session
            target_user_id: User whose projects to retrieve
            admin_user_id: Project admin requesting the data

        Returns:
            List of UserProject records visible to project admin
        """
        # Get all projects for target user
        target_user_projects = self.get_by_user_id(session, target_user_id)

        if not target_user_projects:
            return []

        # Get projects where admin is a member (for filtering)
        admin_project_names = self.get_project_names_for_user(session, admin_user_id)

        # Filter to only projects where admin is also a member
        visible_projects = [project for project in target_user_projects if project.project_name in admin_project_names]

        return visible_projects

    # ===========================================
    # Async methods (AsyncSession)
    # ===========================================

    async def aget_by_user_id(self, session: AsyncSession, user_id: str) -> list[UserProject]:
        """Get all projects for a user (async)"""
        statement = select(UserProject).where(UserProject.user_id == user_id)
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def aget_by_user_and_project(
        self, session: AsyncSession, user_id: str, project_name: str
    ) -> Optional[UserProject]:
        """Get specific project access for a user (async)"""
        statement = select(UserProject).where(UserProject.user_id == user_id, UserProject.project_name == project_name)
        result = await session.execute(statement)
        return result.scalars().first()

    async def aget_by_project_name(self, session: AsyncSession, project_name: str) -> list[UserProject]:
        """Get all users with access to a project (async)"""
        statement = select(UserProject).where(UserProject.project_name == project_name)
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def aadd_project(
        self, session: AsyncSession, user_id: str, project_name: str, is_project_admin: bool = False
    ) -> UserProject:
        """Grant project access to user (async)"""
        now = datetime.now(UTC).replace(tzinfo=None)
        user_project = UserProject(
            user_id=user_id, project_name=project_name, is_project_admin=is_project_admin, date=now, update_date=now
        )
        session.add(user_project)
        await session.flush()
        await session.refresh(user_project)
        return user_project

    async def aremove_project(self, session: AsyncSession, user_id: str, project_name: str) -> bool:
        """Revoke project access from user (async)"""
        user_project = await self.aget_by_user_and_project(session, user_id, project_name)
        if not user_project:
            return False

        await session.delete(user_project)
        await session.flush()
        return True


# Singleton instance
user_project_repository = UserProjectRepository()
