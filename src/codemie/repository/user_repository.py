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

import itertools
import uuid
import asyncio
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Iterator, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import Result, Row, and_
from sqlmodel import Session, select, func, or_

from codemie.core.constants import MAX_POSTGRES_QUERY_ARGUMENTS
from codemie.core.db_utils import escape_like_wildcards
from codemie.rest_api.models.user_management import (
    UserDB,
    UserListFilters,
    PlatformRole,
    UserProject,
    UserKnowledgeBase,
)
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


@dataclass(frozen=True)
class _UserFields:
    id: str
    email: str
    username: str
    name: str | None


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

        Returns a dict mapping user_id to a list of
        (UserBudgetAssignment, Budget | None, current_spending | None) tuples.
        Budget details and the latest tracked spend are resolved via LEFT JOINs.
        """
        from codemie.service.budget.budget_models import Budget, UserBudgetAssignment

        if not user_ids:
            return {}

        user_emails = [
            email.lower()
            for email in session.exec(select(UserDB.email).where(UserDB.id.in_(user_ids))).all()  # type: ignore[attr-defined]
        ]

        latest_spend_dates_subq = (
            select(
                func.lower(ProjectSpendTracking.project_name).label("user_identifier"),
                ProjectSpendTracking.budget_id.label("budget_id"),
                ProjectSpendTracking.budget_category.label("budget_category"),
                func.max(ProjectSpendTracking.spend_date).label("max_spend_date"),
            )
            .where(func.lower(ProjectSpendTracking.project_name).in_(user_emails))
            .where(ProjectSpendTracking.spend_subject_type == "budget")
            .group_by(
                func.lower(ProjectSpendTracking.project_name),
                ProjectSpendTracking.budget_id,
                ProjectSpendTracking.budget_category,
            )
            .subquery()
        )

        latest_budget_spend_subq = (
            select(
                func.lower(ProjectSpendTracking.project_name).label("user_identifier"),
                ProjectSpendTracking.budget_id.label("budget_id"),
                ProjectSpendTracking.budget_category.label("budget_category"),
                ProjectSpendTracking.budget_period_spend.label("current_spending"),
            )
            .join(
                latest_spend_dates_subq,
                and_(
                    func.lower(ProjectSpendTracking.project_name) == latest_spend_dates_subq.c.user_identifier,
                    ProjectSpendTracking.budget_id == latest_spend_dates_subq.c.budget_id,
                    ProjectSpendTracking.budget_category == latest_spend_dates_subq.c.budget_category,
                    ProjectSpendTracking.spend_date == latest_spend_dates_subq.c.max_spend_date,
                ),
            )
            .where(ProjectSpendTracking.spend_subject_type == "budget")
            .subquery()
        )

        rows = session.exec(
            select(UserDB, UserBudgetAssignment, Budget, latest_budget_spend_subq.c.current_spending)
            .outerjoin(UserBudgetAssignment, UserDB.id == UserBudgetAssignment.user_id)
            .outerjoin(Budget, UserBudgetAssignment.budget_id == Budget.budget_id)
            .outerjoin(
                latest_budget_spend_subq,
                and_(
                    latest_budget_spend_subq.c.user_identifier == func.lower(UserDB.email),
                    latest_budget_spend_subq.c.budget_id == UserBudgetAssignment.budget_id,
                    latest_budget_spend_subq.c.budget_category == UserBudgetAssignment.category,
                ),
            )
            .where(UserDB.id.in_(user_ids))  # type: ignore[attr-defined]
        ).all()

        assignments_map: dict[str, list[tuple]] = {}
        for user_row, assignment_row, budget_row, current_spending in rows:
            if user_row.id not in assignments_map:
                assignments_map[user_row.id] = []
            if assignment_row:
                assignments_map[user_row.id].append(
                    (
                        assignment_row,
                        budget_row,
                        float(current_spending) if current_spending is not None else None,
                    )
                )
        return assignments_map

    def count_active_admins(self, session: Session) -> int:
        """Count active admin users

        Args:
            session: Database session

        Returns:
            Number of active admins
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

    async def query_users_by_uuid(self, session: AsyncSession, uuids: list[str]) -> Result:
        """Query users by UUID list.

        Args:
            session: Async database session
            uuids: List of user UUIDs to query

        Returns:
            SQLAlchemy Result with id, email, username, name columns
        """
        stmt = select(UserDB.id, UserDB.email, UserDB.username, UserDB.name).where(UserDB.id.in_(uuids))
        return await session.execute(stmt)

    async def query_users_by_uuid_batched(
        self, session: AsyncSession, uuids: set[str], batch_size: int
    ) -> Iterator[Row[tuple[str, str, str, str | None]]]:
        """Query users by UUID set in batches to avoid query size limits.

        Args:
            session: Async database session
            uuids: Set of user UUIDs to query
            batch_size: Max UUIDs per batch

        Returns:
            Chain of Result objects from all batches
        """
        uuid_list = list(uuids)
        batches = [uuid_list[i : i + batch_size] for i in range(0, len(uuid_list), batch_size)]
        return itertools.chain(
            *await asyncio.gather(*[self.query_users_by_uuid(session, uuid_batch) for uuid_batch in batches])
        )

    async def query_users_by_name(self, session: AsyncSession, names: list[str], lower_names: list[str]) -> Result:
        """Query users by username, display name, or email (case-insensitive).

        Args:
            session: Async database session
            names: List of exact names (username/display name) to match
            lower_names: List of lowercase emails to match

        Returns:
            SQLAlchemy Result with id, email, username, name columns
        """
        stmt = select(UserDB.id, UserDB.email, UserDB.username, UserDB.name).where(
            or_(
                UserDB.username.in_(names),
                UserDB.name.in_(names),
                func.lower(UserDB.email).in_(lower_names),
            )
        )
        return await session.execute(stmt)

    async def query_users_by_name_batched(
        self, session: AsyncSession, names: set[str], lower_names: set[str], batch_size: int
    ) -> Iterator[Row[tuple[str, str, str, str | None]]]:
        """Query users by name/email in batches to avoid query size limits.

        Args:
            session: Async database session
            names: Set of exact names (username/display name) to match
            lower_names: Set of lowercase emails to match
            batch_size: Max identifiers per batch

        Returns:
            Chain of Result objects from all batches
        """
        names_list = list(names)
        lower_names_list = list(lower_names)
        batches = [
            (names_list[i : i + batch_size], lower_names_list[i : i + batch_size])
            for i in range(0, max(len(names_list), len(lower_names_list)), batch_size)
        ]
        return itertools.chain(
            *await asyncio.gather(
                *[
                    self.query_users_by_name(session, names_batch, lower_names_batch)
                    for names_batch, lower_names_batch in batches
                ]
            )
        )

    async def afind_users_by_identifiers(
        self, session: AsyncSession, identifiers: set[str], batch_size: int = MAX_POSTGRES_QUERY_ARGUMENTS
    ) -> dict[str, "_UserFields"]:
        """Bulk-lookup full user records for a set of raw identifiers (UUIDs, usernames, display names).

        Returns a mapping from each input identifier to a _UserFields record.
        Identifiers that cannot be resolved are omitted from the result.
        """
        uuids = {v for v in identifiers if self._is_uuid(v)}
        names = identifiers - uuids
        mapping: dict[str, _UserFields] = {}

        if uuids:
            rows = await self.query_users_by_uuid_batched(session, uuids, batch_size)
            for r in rows:
                rec = _UserFields(id=r.id, email=r.email, username=r.username, name=r.name)
                mapping[r.id] = rec
        if names:
            lower_names = {n.lower() for n in names}
            # Each name batch produces 3× bind params (username IN, name IN, email IN),
            # so divide by 3 to stay under asyncpg's 32767 hard limit.
            name_batch_size = max(1, batch_size // 3)
            rows = await self.query_users_by_name_batched(session, names, lower_names, name_batch_size)
            for r in rows:
                rec = _UserFields(id=r.id, email=r.email, username=r.username, name=r.name)
                if r.username in names:
                    mapping[r.username] = rec
                if r.name in names:
                    mapping[r.name] = rec
                matched_email = next((n for n in names if n.lower() == r.email.lower()), None)
                if matched_email:
                    mapping[matched_email] = rec

        return mapping

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

    async def aquery_active_users(
        self,
        session: AsyncSession,
        search: Optional[str] = None,
        projects: Optional[list[str]] = None,
    ) -> list["UserDB"]:
        """Return all active, non-deleted users, optionally filtered by text search and/or projects.

        Args:
            session: Async database session
            search: Optional ILIKE pattern applied to email, username, and name
            projects: Optional list of project names to filter by

        Returns:
            List of UserDB instances ordered by name then username
        """
        from codemie.rest_api.models.user_management import UserProject

        stmt = select(UserDB).where(UserDB.is_active == True, UserDB.deleted_at.is_(None))  # noqa: E712
        if search:
            escaped = escape_like_wildcards(search)
            pattern = f"%{escaped}%"
            stmt = stmt.where(
                or_(
                    UserDB.email.ilike(pattern, escape="\\"),
                    UserDB.username.ilike(pattern, escape="\\"),
                    UserDB.name.ilike(pattern, escape="\\"),
                )
            )
        if projects:
            stmt = stmt.join(UserProject).where(UserProject.project_name.in_(projects)).distinct()
        stmt = stmt.order_by(UserDB.name, UserDB.username)
        result = await session.execute(stmt)
        return list(result.scalars().all())

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
        - ADMIN           → is_admin = true
        - PLATFORM_ADMIN  → NOT admin AND has at least one project-admin membership
        - USER            → NOT admin AND no project-admin membership
        """
        if role == PlatformRole.ADMIN:
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

    @staticmethod
    def _is_uuid(val: str) -> bool:
        try:
            uuid.UUID(val)
            return True
        except (ValueError, TypeError, AttributeError):
            return False


# Singleton instance
user_repository = UserRepository()
