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
from sqlmodel import Session, select, func, or_

from codemie.core.db_utils import escape_like_wildcards
from codemie.rest_api.models.user_management import UserDB, UserProject, UserKnowledgeBase


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

    def list_users(
        self,
        session: Session,
        page: int = 0,
        per_page: int = 20,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        project_name: Optional[str] = None,
        user_type: Optional[str] = None,
    ) -> tuple[list[UserDB], dict[str, list[UserProject]], int]:
        """List users with filters and pagination (0-indexed)

        Uses LEFT JOIN to fetch users and their projects in a single optimized query
        to prevent N+1 query problems.

        Note: Shows ALL users including deactivated (deleted_at IS NOT NULL).
        Admin panel can manage both active and inactive users.

        Args:
            session: Database session
            page: Page number (0-indexed, page=0 is first page)
            per_page: Items per page
            search: Search term for email, username, or name
            is_active: Filter by active status
            project_name: Filter by project access
            user_type: Filter by user type ('regular' or 'external')

        Returns:
            Tuple of (users list, projects map, total count)
            projects_map: Dict mapping user_id to list of UserProject objects
        """
        # Base query: include ALL users (even deactivated)
        query = select(UserDB)

        # Search filter (email, username, name)
        # Security: Escape LIKE wildcards to prevent information leakage (Story 2, NFR-3.1)
        if search:
            escaped_search = escape_like_wildcards(search)
            search_pattern = f"%{escaped_search}%"
            # Explicit escape parameter ensures PostgreSQL interprets backslashes correctly
            query = query.where(
                or_(
                    UserDB.email.ilike(search_pattern, escape="\\"),
                    UserDB.username.ilike(search_pattern, escape="\\"),
                    UserDB.name.ilike(search_pattern, escape="\\"),
                )
            )

        # Active status filter (coupled with deleted_at per spec)
        # is_active=True means: is_active=True AND deleted_at IS NULL
        # is_active=False means: is_active=False OR deleted_at IS NOT NULL
        if is_active is not None:
            if is_active:
                query = query.where(UserDB.is_active, UserDB.deleted_at.is_(None))
            else:
                query = query.where(or_(~UserDB.is_active, UserDB.deleted_at.isnot(None)))

        # User type filter (Story 7)
        if user_type:
            query = query.where(UserDB.user_type == user_type)

        # Project filter (Story 7: use EXISTS to avoid JOIN cardinality issues)
        if project_name:
            # Use WHERE EXISTS subquery instead of JOIN to keep user-only cardinality
            # This prevents JOIN from affecting count/pagination row counts
            project_exists = (
                select(UserProject.id)
                .where(UserProject.user_id == UserDB.id, UserProject.project_name == project_name)
                .exists()
            )
            query = query.where(project_exists)

        # Get total count BEFORE pagination
        count_query = select(func.count()).select_from(query.subquery())
        total = session.exec(count_query).one()

        # Apply ordering BEFORE pagination (clarity: order → offset → limit)
        query = query.order_by(UserDB.date.desc())

        # Apply pagination to users (0-indexed: page=0 is first page)
        query = query.offset(page * per_page).limit(per_page)

        # Execute paginated user query
        users = list(session.exec(query).all())

        # If no users found, return early with empty projects map
        if not users:
            return users, {}, total

        # Story 7: Optimized JOIN strategy for N+1 prevention
        # ----------------------------------------------------------
        # Goal: Fetch all projects for paginated users in a single query
        # Strategy: LEFT OUTER JOIN between users and user_projects tables
        # Why LEFT JOIN: Ensures users without projects still appear in results
        # Why separate query: Pagination is applied to users first, then JOIN fetches
        #                     projects only for the paginated user subset
        #
        # Query breakdown:
        # 1. Count query: Total users matching filters (for pagination metadata)
        # 2. User query: Paginated users (OFFSET/LIMIT on user rows only)
        # 3. JOIN query: Fetch user+project pairs for paginated user IDs
        #
        # This avoids:
        # - N+1 queries: No per-user project fetches in a loop
        # - JOIN pagination issues: Pagination on users, not on joined rows
        # - Cartesian explosion: Only join paginated users' projects
        user_ids = [u.id for u in users]
        join_query = (
            select(UserDB, UserProject)
            .outerjoin(UserProject, UserDB.id == UserProject.user_id)
            .where(UserDB.id.in_(user_ids))  # type: ignore[attr-defined]
            .order_by(UserDB.date.desc(), UserProject.project_name)
        )
        join_results = session.exec(join_query).all()

        # Group projects by user_id in application layer (Story 7 requirement)
        # Python grouping is fast and keeps SQL simple
        projects_map: dict[str, list[UserProject]] = {}
        for user_row, project_row in join_results:
            if user_row.id not in projects_map:
                projects_map[user_row.id] = []
            if project_row:  # LEFT JOIN returns None for users without projects
                projects_map[user_row.id].append(project_row)

        return users, projects_map, total

    def count_active_superadmins(self, session: Session) -> int:
        """Count active SuperAdmin users

        Args:
            session: Database session

        Returns:
            Number of active SuperAdmins
        """
        statement = select(func.count(UserDB.id)).where(
            UserDB.is_super_admin, UserDB.is_active, UserDB.deleted_at.is_(None)
        )
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


# Singleton instance
user_repository = UserRepository()
