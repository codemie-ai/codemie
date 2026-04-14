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

from datetime import datetime, UTC
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, select, func, or_

from codemie.core.db_utils import escape_like_wildcards
from codemie.rest_api.models.user_management import (
    UserDB,
    UserListFilters,
    PlatformRole,
    UserProject,
    UserKnowledgeBase,
)


class UserRepository:
    """Repository for user CRUD operations (sync SQLModel)"""

    def get_by_id(self, session: Session, user_id: str) -> Optional[UserDB]:
        """Get user by ID

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            UserDB or None if not found
        """
        statement = select(UserDB).where(UserDB.id == user_id)
        return session.exec(statement).first()

    def get_by_email(self, session: Session, email: str) -> Optional[UserDB]:
        """Get user by email (case-insensitive)

        Args:
            session: Database session
            email: User email

        Returns:
            UserDB or None if not found
        """
        statement = select(UserDB).where(func.lower(UserDB.email) == email.lower())
        return session.exec(statement).first()

    def get_by_username(self, session: Session, username: str) -> Optional[UserDB]:
        """Get user by username (case-insensitive)

        Args:
            session: Database session
            username: Username

        Returns:
            UserDB or None if not found
        """
        statement = select(UserDB).where(func.lower(UserDB.username) == username.lower())
        return session.exec(statement).first()

    def get_active_by_id(self, session: Session, user_id: str) -> Optional[UserDB]:
        """Get active user by ID (not deactivated)

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            UserDB or None if not found or deactivated
        """
        statement = select(UserDB).where(UserDB.id == user_id, UserDB.is_active, UserDB.deleted_at.is_(None))
        return session.exec(statement).first()

    def create(self, session: Session, user: UserDB) -> UserDB:
        """Create a new user

        Args:
            session: Database session
            user: UserDB instance to create

        Returns:
            Created UserDB with ID
        """
        # Set timestamps if not already set
        now = datetime.now(UTC)
        if not user.date:
            user.date = now
        if not user.update_date:
            user.update_date = now

        session.add(user)
        session.flush()
        session.refresh(user)
        return user

    def update(self, session: Session, user_id: str, **fields) -> Optional[UserDB]:
        """Update user fields

        Args:
            session: Database session
            user_id: User UUID
            **fields: Fields to update

        Returns:
            Updated UserDB or None if not found
        """
        user = self.get_by_id(session, user_id)
        if not user:
            return None

        for field, value in fields.items():
            if hasattr(user, field):
                setattr(user, field, value)

        user.update_date = datetime.now(UTC)
        session.add(user)
        session.flush()
        session.refresh(user)
        return user

    def soft_delete(self, session: Session, user_id: str) -> bool:
        """Soft delete user (deactivate)

        Sets is_active=False AND deleted_at=NOW()

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            True if user was deactivated, False if not found
        """
        user = self.get_by_id(session, user_id)
        if not user:
            return False

        user.is_active = False
        user.deleted_at = datetime.now(UTC)
        user.update_date = datetime.now(UTC)
        session.add(user)
        session.flush()
        return True

    def count_users(
        self,
        session: Session,
        search: Optional[str] = None,
        filters: UserListFilters = UserListFilters(),
    ) -> int:
        """Count users matching search + filters (no pagination)."""
        query = self._apply_filters(select(UserDB), search, filters)
        return session.exec(select(func.count()).select_from(query.subquery())).one()

    def query_users(
        self,
        session: Session,
        search: Optional[str] = None,
        filters: UserListFilters = UserListFilters(),
        page: int = 0,
        per_page: int = 20,
    ) -> list[UserDB]:
        """Return a paginated page of users matching search + filters."""
        query = self._apply_filters(select(UserDB), search, filters)
        return list(session.exec(query.order_by(UserDB.date.desc()).offset(page * per_page).limit(per_page)).all())

    def fetch_projects_map(self, session: Session, user_ids: list[str]) -> dict[str, list[UserProject]]:
        """Bulk-load project memberships for a set of user IDs via a single LEFT JOIN query."""
        if not user_ids:
            return {}

        rows = session.exec(
            select(UserDB, UserProject)
            .outerjoin(UserProject, UserDB.id == UserProject.user_id)
            .where(UserDB.id.in_(user_ids))  # type: ignore[attr-defined]
            .order_by(UserDB.date.desc(), UserProject.project_name)
        ).all()

        projects_map: dict[str, list[UserProject]] = {}
        for user_row, project_row in rows:
            if user_row.id not in projects_map:
                projects_map[user_row.id] = []
            if project_row:
                projects_map[user_row.id].append(project_row)
        return projects_map

    def fetch_budget_assignments_map(self, session: Session, user_ids: list[str]) -> dict[str, list[tuple]]:
        """Bulk-load budget assignments with budget details for a set of user IDs.

        Returns a dict mapping user_id to a list of (UserBudgetAssignment, Budget | None) tuples.
        Budget details are resolved via a LEFT JOIN on the budgets table, mirroring the same
        budget lookup pattern used in the projects list endpoint.
        """
        from codemie.service.budget.budget_models import Budget, UserBudgetAssignment

        if not user_ids:
            return {}

        rows = session.exec(
            select(UserDB, UserBudgetAssignment, Budget)
            .outerjoin(UserBudgetAssignment, UserDB.id == UserBudgetAssignment.user_id)
            .outerjoin(Budget, UserBudgetAssignment.budget_id == Budget.budget_id)
            .where(UserDB.id.in_(user_ids))  # type: ignore[attr-defined]
        ).all()

        assignments_map: dict[str, list[tuple]] = {}
        for user_row, assignment_row, budget_row in rows:
            if user_row.id not in assignments_map:
                assignments_map[user_row.id] = []
            if assignment_row:
                assignments_map[user_row.id].append((assignment_row, budget_row))
        return assignments_map

    def count_active_superadmins(self, session: Session) -> int:
        """Count active SuperAdmin users

        Args:
            session: Database session

        Returns:
            Number of active SuperAdmins
        """
        statement = select(func.count(UserDB.id)).where(UserDB.is_admin, UserDB.is_active, UserDB.deleted_at.is_(None))
        return session.exec(statement).one()

    def update_last_login(self, session: Session, user_id: str) -> bool:
        """Update user's last login timestamp

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            True if updated, False if user not found
        """
        user = self.get_by_id(session, user_id)
        if not user:
            return False

        now = datetime.now(UTC)
        user.last_login_at = now
        user.update_date = now  # Update modification timestamp
        session.add(user)
        session.flush()
        return True

    def exists_by_email(self, session: Session, email: str) -> bool:
        """Check if user with email exists

        Args:
            session: Database session
            email: Email to check

        Returns:
            True if exists, False otherwise
        """
        return self.get_by_email(session, email) is not None

    def exists_by_username(self, session: Session, username: str) -> bool:
        """Check if user with username exists

        Args:
            session: Database session
            username: Username to check

        Returns:
            True if exists, False otherwise
        """
        return self.get_by_username(session, username) is not None

    def get_by_emails(self, session: Session, emails: list[str]) -> dict[str, "UserDB"]:
        """Bulk-fetch users by email (case-insensitive), returning a lower-email → UserDB map.

        Args:
            session: Database session
            emails: List of email addresses to look up

        Returns:
            Dict mapping lowercased email to UserDB for each found user
        """
        if not emails:
            return {}

        lower_emails = [e.lower() for e in emails]
        statement = select(UserDB).where(
            func.lower(UserDB.email).in_(lower_emails)  # type: ignore[attr-defined]
        )
        users = session.exec(statement).all()
        return {u.email.lower(): u for u in users}

    def get_existing_user_ids(self, session: Session, user_ids: list[str]) -> set[str]:
        """Check which user IDs exist in the database.

        Efficient bulk existence check using a single IN query.

        Args:
            session: Database session
            user_ids: List of user UUIDs to check

        Returns:
            Set of user IDs that exist in the database
        """
        if not user_ids:
            return set()

        statement = select(UserDB.id).where(
            UserDB.id.in_(user_ids)  # type: ignore[attr-defined]
        )
        return set(session.exec(statement).all())

    def get_user_projects(self, session: Session, user_id: str) -> list[UserProject]:
        """Get user's project access list

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            List of UserProject objects
        """
        statement = select(UserProject).where(UserProject.user_id == user_id).order_by(UserProject.project_name)
        return list(session.exec(statement).all())

    def get_user_knowledge_bases(self, session: Session, user_id: str) -> list[str]:
        """Get user's knowledge base access list

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            List of knowledge base names
        """
        statement = (
            select(UserKnowledgeBase).where(UserKnowledgeBase.user_id == user_id).order_by(UserKnowledgeBase.kb_name)
        )
        kbs = session.exec(statement).all()
        return [kb.kb_name for kb in kbs]

    def get_projects_for_users(self, session: Session, user_ids: list[str]) -> dict[str, list[UserProject]]:
        """Batch fetch projects for multiple users

        Args:
            session: Database session
            user_ids: List of user UUIDs

        Returns:
            Dict mapping user_id to list of UserProject objects
        """
        if not user_ids:
            return {}

        # Use SQLAlchemy's in_() operator for filtering
        statement = (
            select(UserProject)
            .where(UserProject.user_id.in_(user_ids))  # type: ignore[attr-defined]
            .order_by(UserProject.user_id, UserProject.project_name)
        )
        projects = session.exec(statement).all()

        # Group by user_id
        result: dict[str, list[UserProject]] = {}
        for project in projects:
            if project.user_id not in result:
                result[project.user_id] = []
            result[project.user_id].append(project)

        return result

    # ===========================================
    # Async methods (AsyncSession)
    # ===========================================

    async def aget_by_id(self, session: AsyncSession, user_id: str) -> Optional[UserDB]:
        """Get user by ID (async)"""
        statement = select(UserDB).where(UserDB.id == user_id)
        result = await session.execute(statement)
        return result.scalars().first()

    async def aget_by_email(self, session: AsyncSession, email: str) -> Optional[UserDB]:
        """Get user by email, case-insensitive (async)"""
        statement = select(UserDB).where(func.lower(UserDB.email) == email.lower())
        result = await session.execute(statement)
        return result.scalars().first()

    async def aget_active_by_id(self, session: AsyncSession, user_id: str) -> Optional[UserDB]:
        """Get active user by ID (async)"""
        statement = select(UserDB).where(UserDB.id == user_id, UserDB.is_active, UserDB.deleted_at.is_(None))
        result = await session.execute(statement)
        return result.scalars().first()

    async def acreate(self, session: AsyncSession, user: UserDB) -> UserDB:
        """Create a new user (async)"""
        now = datetime.now(UTC).replace(tzinfo=None)
        if not user.date:
            user.date = now
        if not user.update_date:
            user.update_date = now

        session.add(user)
        await session.flush()
        await session.refresh(user)
        return user

    async def aupdate(self, session: AsyncSession, user_id: str, **fields) -> Optional[UserDB]:
        """Update user fields (async)"""
        user = await self.aget_by_id(session, user_id)
        if not user:
            return None

        for field, value in fields.items():
            if hasattr(user, field):
                setattr(user, field, value)

        user.update_date = datetime.now(UTC).replace(tzinfo=None)
        session.add(user)
        await session.flush()
        await session.refresh(user)
        return user

    async def aupdate_last_login(self, session: AsyncSession, user_id: str) -> bool:
        """Update user's last login timestamp (async)"""
        user = await self.aget_by_id(session, user_id)
        if not user:
            return False

        now = datetime.now(UTC).replace(tzinfo=None)
        user.last_login_at = now
        user.update_date = now
        session.add(user)
        await session.flush()
        return True

    @staticmethod
    def _apply_filters(query, search: Optional[str], filters: UserListFilters):
        """Apply search text and structured filters to a UserDB SELECT query."""
        query = query.where(UserDB.deleted_at.is_(None))

        if search:
            escaped = escape_like_wildcards(search)
            pattern = f"%{escaped}%"
            query = query.where(
                or_(
                    UserDB.email.ilike(pattern, escape="\\"),
                    UserDB.username.ilike(pattern, escape="\\"),
                    UserDB.name.ilike(pattern, escape="\\"),
                )
            )

        if filters.user_type:
            query = query.where(UserDB.user_type == filters.user_type)

        if filters.is_active is not None:
            query = query.where(UserDB.is_active == filters.is_active)

        if filters.platform_role == PlatformRole.PLATFORM_ADMIN and filters.projects:
            return UserRepository._apply_platform_admin_project_filter(query, filters.projects)
        elif filters.platform_role == PlatformRole.USER and filters.projects:
            return UserRepository._apply_user_project_filter(query, filters.projects)

        if filters.platform_role:
            query = UserRepository._apply_platform_role_filter(query, filters.platform_role)

        if filters.projects:
            project_exists = (
                select(UserProject.id)
                .where(
                    UserProject.user_id == UserDB.id,
                    UserProject.project_name.in_(filters.projects),  # type: ignore[attr-defined]
                )
                .exists()
            )
            query = query.where(project_exists)

        if filters.budgets:
            from codemie.service.budget.budget_models import UserBudgetAssignment

            budget_exists = (
                select(UserBudgetAssignment.user_id)
                .where(
                    UserBudgetAssignment.user_id == UserDB.id,
                    UserBudgetAssignment.budget_id.in_(filters.budgets),  # type: ignore[attr-defined]
                )
                .exists()
            )
            query = query.where(budget_exists)

        return query

    @staticmethod
    def _apply_platform_role_filter(query, role: PlatformRole):
        """Return query with a WHERE clause matching the given platform role.

        Roles are mutually exclusive:
        - SUPER_ADMIN     → is_admin = true
        - PLATFORM_ADMIN  → NOT super admin AND has at least one project-admin membership
        - USER            → NOT super admin AND no project-admin membership
        """
        if role == PlatformRole.SUPER_ADMIN:
            return query.where(UserDB.is_admin)

        is_project_admin = (
            select(UserProject.id).where(UserProject.user_id == UserDB.id, UserProject.is_project_admin).exists()
        )
        if role == PlatformRole.PLATFORM_ADMIN:
            return query.where(~UserDB.is_admin, is_project_admin)

        return query.where(~UserDB.is_admin, ~is_project_admin)

    @staticmethod
    def _apply_platform_admin_project_filter(query, projects: list[str]):
        """Filter users who are project admin specifically on one of the given projects.

        Used when platform_role=platform_admin and a projects filter are both provided.
        Replaces the independent role + projects filters with a single combined subquery.
        """
        is_admin_on_projects = (
            select(UserProject.id)
            .where(
                UserProject.user_id == UserDB.id,
                UserProject.is_project_admin,
                UserProject.project_name.in_(projects),
            )
            .exists()
        )
        return query.where(is_admin_on_projects)

    @staticmethod
    def _apply_user_project_filter(query, projects: list[str]):
        """Filter users who are regular members (not project admins) in one of the given projects.

        Used when platform_role=user and a projects filter are both provided.
        Replaces the independent role + projects filters with a single combined subquery.
        """
        is_user_on_projects = (
            select(UserProject.id)
            .where(
                UserProject.user_id == UserDB.id,
                UserProject.is_project_admin.is_(False),
                UserProject.project_name.in_(projects),
            )
            .exists()
        )
        return query.where(is_user_on_projects)


# Singleton instance
user_repository = UserRepository()
