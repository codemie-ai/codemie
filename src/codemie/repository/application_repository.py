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
from uuid import UUID

from sqlalchemy import literal, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import BooleanClauseList
from sqlalchemy.sql.selectable import Select
from sqlmodel import Session, and_, case, func, or_, select

from codemie.configs.logger import logger
from codemie.core.db_utils import escape_like_wildcards
from codemie.core.models import Application
from codemie.rest_api.models.user_management import UserProject


class ApplicationRepository:
    """Repository for application/project management (sync SQLModel)"""

    @staticmethod
    def _build_visibility_condition(user_id: str) -> BooleanClauseList:
        """Build SQL condition for non-super-admin project visibility.

        Visibility source of truth:
        - Personal project visibility is based on `applications.created_by`.
        - Shared project visibility is based on membership in `user_projects`.
        - Soft-deleted projects (deleted_at IS NOT NULL) are excluded.

        Args:
            user_id: User ID to build visibility condition for

        Returns:
            SQLAlchemy BooleanClauseList (AND clause)
        """
        user_memberships = select(UserProject.project_name).where(UserProject.user_id == user_id)
        return and_(
            Application.deleted_at.is_(None),
            or_(
                and_(Application.project_type == "personal", Application.created_by == user_id),
                and_(Application.project_type == "shared", Application.name.in_(user_memberships)),
            ),
        )

    @staticmethod
    def _apply_search_filters(
        statement: Select[tuple[Application]], search: Optional[str]
    ) -> Select[tuple[Application]]:
        """Apply search WHERE conditions without ordering.

        Code Review R4: Separated from _apply_search to allow count queries without ORDER BY.

        Searches both name (exact + partial) and description (partial, case-insensitive).

        Args:
            statement: Base SELECT statement
            search: Optional search string (substring match on name and description)

        Returns:
            Modified SELECT statement with search filters only (no ordering)
        """
        if not search:
            return statement

        escaped_query = escape_like_wildcards(search)
        return statement.where(
            or_(
                Application.name == search,
                Application.name.ilike(f"%{escaped_query}%", escape="\\"),
                Application.description.ilike(f"%{escaped_query}%", escape="\\"),
            )
        )

    @staticmethod
    def _apply_search(statement: Select[tuple[Application]], search: Optional[str]) -> Select[tuple[Application]]:
        """Apply exact-first + wildcard-safe search conditions with ordering.

        Code Review R4: Now delegates to _apply_search_filters + adds ordering.
        Code Review R1: Ordering is composed to ensure stability:
        - When search is provided: exact match first, then fuzzy matches
        - Stable secondary ordering (date desc, name asc) applied by caller

        Args:
            statement: Base SELECT statement
            search: Optional search string (substring match on name)

        Returns:
            Modified SELECT statement with search filters and ordering
        """
        statement = ApplicationRepository._apply_search_filters(statement, search)
        if not search:
            return statement

        return statement.order_by(case((Application.name == search, 1), else_=2))

    def get_by_name(self, session: Session, name: str) -> Optional[Application]:
        """Get application by name

        Args:
            session: Database session
            name: Application/project name

        Returns:
            Application or None
        """
        statement = select(Application).where(Application.name == name)
        return session.exec(statement).first()

    def get_by_name_case_insensitive(self, session: Session, name: str) -> Optional[Application]:
        """Get application by name using case-insensitive lookup."""
        statement = select(Application).where(func.lower(Application.name) == name.lower())
        return session.exec(statement).first()

    def exists_by_name(self, session: Session, name: str) -> bool:
        """Check if application exists

        Args:
            session: Database session
            name: Application/project name

        Returns:
            True if exists, False otherwise
        """
        return self.get_by_name(session, name) is not None

    def exists_by_name_case_insensitive(self, session: Session, name: str) -> bool:
        """Check if application exists using case-insensitive lookup."""
        return self.get_by_name_case_insensitive(session, name) is not None

    def create(
        self,
        session: Session,
        name: str,
        description: Optional[str] = None,
        project_type: str = "shared",
        created_by: Optional[str] = None,
        cost_center_id: UUID | None = None,
    ) -> Application:
        """Create new application

        Args:
            session: Database session
            name: Application/project name
            description: Project description
            project_type: Project type ('shared' or 'personal')
            created_by: Creator user ID

        Returns:
            Created Application record

        Note:
            Caller should handle duplicate check or rely on DB unique constraint
            This method flushes/refreshed entity but does not commit; caller controls
            transaction boundaries and must commit/rollback atomically with related writes.
        """
        now = datetime.now()
        application = Application(
            id=name,
            name=name,
            description=description,
            project_type=project_type,
            created_by=created_by,
            cost_center_id=cost_center_id,
            date=now,
            update_date=now,
        )
        session.add(application)
        session.flush()
        session.refresh(application)
        logger.debug("Created application: " f"name={name}, project_type={project_type}, created_by={created_by}")
        return application

    def get_or_create(self, session: Session, name: str) -> Application:
        """Get existing application or create if doesn't exist

        Uses case-insensitive lookup to prevent creating duplicate applications
        that differ only in casing. Returns existing record if found (regardless
        of casing), only creates a new record when no match exists at all.

        Handles race condition where another process creates the same application
        between the check and the create operation.

        Args:
            session: Database session
            name: Application/project name

        Returns:
            Application record (existing or newly created)

        Note:
            Thread-safe implementation that handles concurrent creation attempts
        """
        # First attempt: check if exists (case-insensitive to prevent duplicate casings)
        existing = self.get_by_name_case_insensitive(session, name)
        if existing:
            return existing

        # Try to create - may fail if another process creates it concurrently
        try:
            return self.create(session, name)
        except IntegrityError as e:
            # Unique constraint violation - another process created it
            logger.debug(f"Application already exists (created by another process): name={name}")
            session.rollback()  # Rollback failed transaction

            # Retry get operation - should now exist
            existing = self.get_by_name(session, name)
            if existing:
                return existing

            # Still not found - unexpected error, re-raise original
            logger.error(f"Failed to create or retrieve application: name={name}, error={e}", exc_info=True)
            raise

    def delete_by_name(self, session: Session, name: str) -> bool:
        """Delete application by name

        Args:
            session: Database session
            name: Application/project name

        Returns:
            True if deleted, False if not found
        """
        application = self.get_by_name(session, name)
        if not application:
            return False

        session.delete(application)
        session.flush()
        logger.debug(f"Deleted application: name={name}")
        return True

    def is_personal_project(self, session: Session, project_name: str) -> bool:
        """Check if project is personal type

        Args:
            session: Database session
            project_name: Project name

        Returns:
            True if project_type='personal', False otherwise (including non-existent projects)
        """
        application = self.get_by_name(session, project_name)
        return application is not None and application.project_type == "personal"

    def list_visible_projects(
        self,
        session: Session,
        user_id: str,
        is_admin: bool,
        search: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Application]:
        """List projects visible to a user, with optional search.

        - Super admins: all non-deleted projects.
        - Non-super-admins:
          - Personal projects created by the user (non-deleted).
          - Shared projects where user has membership in `user_projects` (non-deleted).
        """
        statement = select(Application)
        statement = self._apply_search(statement, search)

        if is_admin:
            statement = statement.where(Application.deleted_at.is_(None))
        else:
            statement = statement.where(self._build_visibility_condition(user_id))

        if limit is not None:
            statement = statement.limit(limit)

        return list(session.exec(statement).all())

    _SORT_COLUMN_MAP: dict = {
        "name": Application.name,
        "created_at": Application.date,
    }

    def list_visible_projects_paginated(
        self,
        session: Session,
        user_id: str,
        is_admin: bool,
        search: Optional[str] = None,
        page: int = 0,
        per_page: int = 20,
        sort_by: str | None = None,
        sort_order: str | None = None,
    ) -> tuple[list[Application], int]:
        """List projects visible to a user with pagination.

        Story 16: Project list endpoint with pagination support.

        Code Review R4: Count query uses filter-only path (no ORDER BY at all).
        Code Review R2: Ordering separated from count query for performance.
        Code Review R1: Deterministic ordering applied for stable pagination:
        - Search results: exact match first, then fuzzy matches (via _apply_search); sort_by ignored.
        - Explicit sort: sort_by/sort_order applied when no search is active.
        - Default: date desc (newest first), name asc (alphabetical tiebreaker).

        Args:
            session: Database session
            user_id: Requesting user ID
            is_admin: Whether user is super admin
            search: Optional search query (substring match on name)
            page: Page number (0-indexed)
            per_page: Items per page
            sort_by: Column to sort by — 'name' or 'created_at'; ignored when search is active
            sort_order: Sort direction — 'asc' or 'desc' (default 'asc')

        Returns:
            Tuple of (projects, total_count) where total_count reflects filtered results
        """
        # Build count statement: filters only, NO ordering (performance optimization)
        count_base_statement = select(Application)
        count_base_statement = self._apply_search_filters(count_base_statement, search)

        if is_admin:
            count_base_statement = count_base_statement.where(Application.deleted_at.is_(None))
        else:
            visibility_condition = self._build_visibility_condition(user_id)
            count_base_statement = count_base_statement.where(visibility_condition)

        # Count total (with filters applied, without ANY ordering for performance)
        count_statement = select(func.count()).select_from(count_base_statement.subquery())
        total_count = session.exec(count_statement).one()

        # Build data statement: filters + ordering
        data_statement = select(Application)
        data_statement = self._apply_search_filters(data_statement, search)

        if is_admin:
            data_statement = data_statement.where(Application.deleted_at.is_(None))
        else:
            visibility_condition = self._build_visibility_condition(user_id)
            data_statement = data_statement.where(visibility_condition)

        if sort_by and sort_by in self._SORT_COLUMN_MAP:
            col = self._SORT_COLUMN_MAP[sort_by]
            order_expr = col.desc() if sort_order == "desc" else col.asc()
            data_statement = data_statement.order_by(order_expr, Application.name.asc())
        elif search:
            # Relevance ordering takes precedence over caller-provided sort when search is active
            data_statement = data_statement.order_by(
                case((Application.name == search, 1), else_=2),
                Application.date.desc(),
                Application.name.asc(),
            )
        else:
            data_statement = data_statement.order_by(Application.date.desc(), Application.name.asc())

        # Paginate results
        offset = page * per_page
        paginated_statement = data_statement.offset(offset).limit(per_page)
        projects = list(session.exec(paginated_statement).all())

        return projects, int(total_count)

    def get_project_member_counts_bulk(self, session: Session, project_names: list[str]) -> dict[str, tuple[int, int]]:
        """Get user_count and admin_count for multiple projects in single query.

        Story 16: Aggregate member counts for project list response.

        Args:
            session: Database session
            project_names: List of project names to query

        Returns:
            Dict mapping project_name -> (user_count, admin_count)
        """
        if not project_names:
            return {}

        statement = (
            select(
                UserProject.project_name,
                func.count(UserProject.id).label("user_count"),
                func.sum(case((UserProject.is_project_admin, 1), else_=0)).label("admin_count"),
            )
            .where(UserProject.project_name.in_(project_names))
            .group_by(UserProject.project_name)
        )

        results = session.exec(statement).all()

        return {project_name: (int(user_count), int(admin_count)) for project_name, user_count, admin_count in results}

    def get_visible_project(
        self,
        session: Session,
        project_name: str,
        user_id: str,
        is_admin: bool,
    ) -> Optional[Application]:
        """Get a visible project by exact name for a user."""
        statement = select(Application).where(Application.name == project_name)

        if is_admin:
            statement = statement.where(Application.deleted_at.is_(None))
        else:
            statement = statement.where(self._build_visibility_condition(user_id))

        return session.exec(statement).first()

    def get_project_owner(self, session: Session, project_name: str) -> Optional[str]:
        """Get project owner (created_by field)

        Args:
            session: Database session
            project_name: Project name

        Returns:
            User ID of project creator, or None if project doesn't exist
        """
        application = self.get_by_name(session, project_name)
        return application.created_by if application else None

    def can_user_see_project(self, session: Session, project_name: str, user_id: str, is_admin: bool) -> bool:
        """Check if user can see project based on visibility rules

        Story 11 Visibility Rules:
        - Super admin can see all projects
        - Personal projects visible only to creator
        - Shared projects visible only to members

        Args:
            session: Database session
            project_name: Project name
            user_id: User ID
            is_admin: Whether user is super admin

        Returns:
            True if user can see project, False otherwise
        """
        return self.get_visible_project(session, project_name, user_id, is_admin) is not None

    def get_project_types_bulk(self, session: Session, project_names: list[str]) -> dict[str, tuple[str, str | None]]:
        """Get project types and creators for multiple projects in single query

        Story 10: Optimization to avoid N+1 queries in visibility filtering.

        Args:
            session: Database session
            project_names: List of project names

        Returns:
            Dict mapping project_name -> (project_type, created_by)
            Missing projects are excluded from result
        """
        if not project_names:
            return {}

        statement = select(Application.name, Application.project_type, Application.created_by).where(
            Application.name.in_(project_names)
        )
        results = session.exec(statement).all()

        return {name: (project_type, created_by) for name, project_type, created_by in results}

    def count_shared_projects_created_by_user(self, session: Session, user_id: str) -> int:
        """Count shared projects created by a user for project_limit enforcement.

        Args:
            session: Database session
            user_id: User ID (creator)

        Returns:
            Count of user-created shared projects.
        """
        statement = (
            select(func.count(Application.id))
            .where(Application.created_by == user_id)
            .where(Application.project_type == "shared")
            .where(Application.deleted_at.is_(None))
        )

        result = session.exec(statement).one()
        return int(result)

    def update_fields(
        self,
        session: Session,
        project_name: str,
        new_name: str | None = None,
        new_description: str | None = None,
    ) -> Application:
        """Update mutable fields of an Application record.

        Args:
            session: Database session
            project_name: Current project name (used to load the record)
            new_name: If provided, sets Application.name (not the PK id column)
            new_description: If provided, sets Application.description

        Returns:
            Updated Application instance (flushed, not committed)

        Note:
            For renames, caller must cascade UserProject.project_name before calling this.
        """
        project = self.get_by_name(session, project_name)
        if new_name is not None:
            project.name = new_name
        if new_description is not None:
            project.description = new_description
        project.update_date = datetime.now(UTC)
        session.add(project)
        session.flush()
        session.refresh(project)
        logger.debug(f"Updated application fields: name={project.name}")
        return project

    def get_project_entity_counts_bulk(self, session: Session, project_names: list[str]) -> dict[str, dict]:
        """Bulk-count assistants, workflows, integrations, datasources, and skills per project.

        Runs a single UNION ALL query across all entity types instead of 5 sequential queries,
        reducing DB round-trips from 5 to 1. Lazy imports are used to avoid circular-import risk.

        Args:
            session: Database session
            project_names: List of project names to query

        Returns:
            Dict mapping project_name -> {"assistants_count": N, "workflows_count": N,
            "integrations_count": N, "datasources_count": N, "skills_count": N}
        """
        if not project_names:
            return {}

        from codemie.core.workflow_models.workflow_config import WorkflowConfig
        from codemie.rest_api.models.assistant import Assistant
        from codemie.rest_api.models.index import IndexInfo
        from codemie.rest_api.models.settings import Settings
        from codemie.rest_api.models.skill import Skill

        result: dict[str, dict] = {
            name: {
                "assistants_count": 0,
                "workflows_count": 0,
                "integrations_count": 0,
                "datasources_count": 0,
                "skills_count": 0,
            }
            for name in project_names
        }

        assistants_q = (
            select(
                Assistant.project.label("proj"),
                literal("assistants").label("entity_type"),
                func.count(Assistant.id).label("cnt"),
            )
            .where(Assistant.project.in_(project_names))
            .group_by(Assistant.project)
        )
        workflows_q = (
            select(
                WorkflowConfig.project.label("proj"),
                literal("workflows").label("entity_type"),
                func.count(WorkflowConfig.id).label("cnt"),
            )
            .where(WorkflowConfig.project.in_(project_names))
            .group_by(WorkflowConfig.project)
        )
        skills_q = (
            select(
                Skill.project.label("proj"),
                literal("skills").label("entity_type"),
                func.count(Skill.id).label("cnt"),
            )
            .where(Skill.project.in_(project_names))
            .group_by(Skill.project)
        )
        datasources_q = (
            select(
                IndexInfo.project_name.label("proj"),
                literal("datasources").label("entity_type"),
                func.count(IndexInfo.id).label("cnt"),
            )
            .where(IndexInfo.project_name.in_(project_names))
            .group_by(IndexInfo.project_name)
        )
        integrations_q = (
            select(
                Settings.project_name.label("proj"),
                literal("integrations").label("entity_type"),
                func.count(Settings.id).label("cnt"),
            )
            .where(Settings.project_name.in_(project_names))
            .group_by(Settings.project_name)
        )

        combined = union_all(assistants_q, workflows_q, skills_q, datasources_q, integrations_q)
        for proj, entity_type, cnt in session.exec(combined).all():  # type: ignore[call-overload]
            if proj in result:
                result[proj][f"{entity_type}_count"] = int(cnt)

        return result

    def get_project_members(self, session: Session, project_name: str) -> list[UserProject]:
        """Get all members for a project.

        Story 16: Project detail endpoint includes member list.

        Args:
            session: Database session
            project_name: Project name

        Returns:
            List of UserProject records for the project
        """
        statement = select(UserProject).where(UserProject.project_name == project_name)
        return list(session.exec(statement).all())

    def update_project(
        self,
        session: Session,
        application: Application,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        cost_center_id: UUID | None = None,
    ) -> Application:
        """Update mutable project fields."""
        if name is not None:
            application.id = name
            application.name = name
        if description is not None:
            application.description = description
        application.cost_center_id = cost_center_id
        application.update_date = datetime.now()
        session.add(application)
        session.flush()
        session.refresh(application)
        return application

    def list_projects_by_cost_center_id(self, session: Session, cost_center_id: UUID) -> list[Application]:
        """Return active projects linked to the given cost center."""
        statement = (
            select(Application)
            .where(Application.cost_center_id == cost_center_id)
            .where(Application.deleted_at.is_(None))
            .order_by(Application.date.desc(), Application.name.asc())
        )
        return list(session.exec(statement).all())

    def count_active_projects_by_cost_center_id(self, session: Session, cost_center_id: UUID) -> int:
        """Count active projects linked to the given cost center."""
        statement = (
            select(func.count(Application.id))
            .where(Application.cost_center_id == cost_center_id)
            .where(Application.deleted_at.is_(None))
        )
        return int(session.exec(statement).one())

    # ===========================================
    # Async methods (AsyncSession)
    # ===========================================

    async def aget_by_name(self, session: AsyncSession, name: str) -> Optional[Application]:
        """Get application by name (async)"""
        statement = select(Application).where(Application.name == name)
        result = await session.execute(statement)
        return result.scalars().first()

    async def aget_by_name_case_insensitive(self, session: AsyncSession, name: str) -> Optional[Application]:
        """Get application by name using case-insensitive lookup (async)"""
        statement = select(Application).where(func.lower(Application.name) == name.lower())
        result = await session.execute(statement)
        return result.scalars().first()

    async def acreate(
        self,
        session: AsyncSession,
        name: str,
        description: Optional[str] = None,
        project_type: str = "shared",
        created_by: Optional[str] = None,
        cost_center_id: UUID | None = None,
    ) -> Application:
        """Create new application (async)"""
        now = datetime.now()
        application = Application(
            id=name,
            name=name,
            description=description,
            project_type=project_type,
            created_by=created_by,
            cost_center_id=cost_center_id,
            date=now,
            update_date=now,
        )
        session.add(application)
        await session.flush()
        await session.refresh(application)
        logger.debug("Created application: " f"name={name}, project_type={project_type}, created_by={created_by}")
        return application

    async def aget_all_non_deleted(self, session: AsyncSession) -> list[Application]:
        """Return all applications where deleted_at IS NULL.

        Used by background jobs that have no user context.

        Args:
            session: Async database session

        Returns:
            List of all non-deleted Application records
        """
        statement = select(Application).where(Application.deleted_at.is_(None))
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def aget_or_create(self, session: AsyncSession, name: str) -> Application:
        """Get existing application or create if doesn't exist (async)

        Uses case-insensitive lookup to prevent creating duplicate applications
        that differ only in casing. Returns existing record if found (regardless
        of casing), only creates a new record when no match exists at all.

        Handles race condition where another process creates the same application
        between the check and the create operation.
        """
        existing = await self.aget_by_name_case_insensitive(session, name)
        if existing:
            return existing

        try:
            return await self.acreate(session, name)
        except IntegrityError as e:
            logger.debug(f"Application already exists (created by another process): name={name}")
            await session.rollback()

            existing = await self.aget_by_name(session, name)
            if existing:
                return existing

            logger.error(f"Failed to create or retrieve application: name={name}, error={e}", exc_info=True)
            raise


# Singleton instance
application_repository = ApplicationRepository()
