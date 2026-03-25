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

"""
Repository for skill database operations.
"""

import json
import math
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, and_, delete, literal_column, cast, String, case
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import Session, select

from codemie.rest_api.models.skill import MarketplaceFilter, Skill, SkillSortBy, SkillVisibility, SkillCategory
from codemie.rest_api.models.usage.skill_user_interaction import SkillUserInteraction


@dataclass
class SkillListResult:
    """Result of listing skills with pagination and metadata"""

    skills: list[Skill]
    assistants_count_map: dict[str, int]
    page: int
    per_page: int
    total: int
    pages: int


class SkillRepository:
    """Repository for all skill-related data operations"""

    # ============================================================================
    # Access Control Helper Methods
    # ============================================================================

    @staticmethod
    def _build_owner_condition_for_project(user_id: str, project: str):
        """Build condition for user's own skills in a specific project."""
        return and_(
            Skill.created_by["id"].astext == user_id,
            Skill.project == project,
        )

    @staticmethod
    def _build_visibility_condition_for_project(visibility: SkillVisibility, project: str):
        """Build condition for skills with specific visibility in a project."""
        return and_(
            Skill.visibility == visibility,
            Skill.project == project,
        )

    @staticmethod
    def _build_visibility_condition_for_projects(visibility: SkillVisibility, projects: list[str]):
        """Build condition for skills with specific visibility in multiple projects."""
        return and_(
            Skill.visibility == visibility,
            Skill.project.in_(projects),
        )

    @staticmethod
    def _build_specific_project_conditions(
        user_id: str,
        specific_project: str,
        has_project_access: bool,
        is_admin_of_project: bool,
    ) -> list:
        """
        Build access conditions when filtering by a specific project.

        When specific_project is provided:
        - User's own skills IN that project (any visibility)
        - PROJECT visibility skills IN that project (if user has access)
        - PRIVATE visibility skills IN that project (if user is admin)
        - PUBLIC skills excluded here (inclusion controlled by caller via marketplace_filter)
        """
        conditions = []

        # Always show user's own skills in this project (any visibility)
        conditions.append(SkillRepository._build_owner_condition_for_project(user_id, specific_project))

        if is_admin_of_project:
            # Admin: Show ALL PROJECT and PRIVATE skills in this project
            conditions.append(
                SkillRepository._build_visibility_condition_for_project(SkillVisibility.PROJECT, specific_project)
            )
            conditions.append(
                SkillRepository._build_visibility_condition_for_project(SkillVisibility.PRIVATE, specific_project)
            )
        elif has_project_access:
            # Non-admin with project access: Show PROJECT skills only
            conditions.append(
                SkillRepository._build_visibility_condition_for_project(SkillVisibility.PROJECT, specific_project)
            )

        return conditions

    @staticmethod
    def _build_global_admin_conditions() -> list:
        """Build conditions for global admin (sees ALL PROJECT and PRIVATE skills)."""
        return [
            Skill.visibility == SkillVisibility.PROJECT,
            Skill.visibility == SkillVisibility.PRIVATE,
        ]

    @staticmethod
    def _build_project_admin_conditions(
        user_admin_projects: list[str],
        user_applications: list[str],
    ) -> list:
        """
        Build conditions for project admin.

        Project admin sees:
        - ALL PROJECT + PRIVATE skills from projects they're admin of
        - PROJECT skills from other accessible projects
        """
        conditions = []

        # Show ALL skills from projects they're admin of
        conditions.append(
            SkillRepository._build_visibility_condition_for_projects(SkillVisibility.PROJECT, user_admin_projects)
        )
        conditions.append(
            SkillRepository._build_visibility_condition_for_projects(SkillVisibility.PRIVATE, user_admin_projects)
        )

        # Also show PROJECT skills from other accessible projects
        other_accessible_projects = [p for p in user_applications if p not in user_admin_projects]
        if other_accessible_projects:
            conditions.append(
                SkillRepository._build_visibility_condition_for_projects(
                    SkillVisibility.PROJECT, other_accessible_projects
                )
            )

        return conditions

    @staticmethod
    def _build_regular_user_conditions(user_applications: list[str]) -> list:
        """Build conditions for regular user (sees PROJECT skills from accessible projects)."""
        if not user_applications:
            return []
        return [SkillRepository._build_visibility_condition_for_projects(SkillVisibility.PROJECT, user_applications)]

    @staticmethod
    def _build_global_access_conditions(
        user_id: str,
        user_applications: list[str],
        user_is_global_admin: bool,
        user_admin_projects: list[str],
    ) -> list:
        """
        Build access conditions when no specific project filter.

        Access rules:
        - User's own skills (any visibility, any project)
        - Public skills (marketplace - any project)
        - If GLOBAL ADMIN: ALL PROJECT + PRIVATE skills from ALL projects
        - If PROJECT ADMIN: ALL from admin projects + PROJECT from other accessible
        - If NOT admin: PROJECT skills only from user's accessible projects
        """
        # Base conditions: own skills and public/marketplace skills
        conditions = [
            Skill.created_by["id"].astext == user_id,
            Skill.visibility == SkillVisibility.PUBLIC,
        ]

        # Add role-specific conditions
        if user_is_global_admin:
            conditions.extend(SkillRepository._build_global_admin_conditions())
        elif user_admin_projects:
            conditions.extend(SkillRepository._build_project_admin_conditions(user_admin_projects, user_applications))
        elif user_applications:
            conditions.extend(SkillRepository._build_regular_user_conditions(user_applications))

        return conditions

    @staticmethod
    def _build_access_conditions(
        user_id: str,
        user_applications: list[str],
        user_is_global_admin: bool = False,
        user_admin_projects: list[str] | None = None,
        specific_project: list[str] | str | None = None,
        marketplace_filter: MarketplaceFilter = MarketplaceFilter.DEFAULT,
    ) -> list:
        """
        Build access control conditions for skill queries.

        Args:
            user_id: Current user ID
            user_applications: Projects user has access to
            user_is_global_admin: Whether user is global admin (sees all from all projects)
            user_admin_projects: Projects where user is admin (sees all skills in these projects)
            specific_project: If provided, restricts access to these project(s) only
                (excludes PUBLIC skills unless marketplace_filter is INCLUDE).
                Accepts a single string or a list of strings.
            marketplace_filter: Controls marketplace skill inclusion (DEFAULT/EXCLUDE/INCLUDE)

        Returns:
            List of SQLAlchemy conditions to be combined with OR
        """
        user_admin_projects = user_admin_projects or []

        # Normalize to list
        if isinstance(specific_project, str):
            specific_project = [specific_project]

        if specific_project:
            # Build conditions for each project and combine
            all_conditions = []
            for proj in specific_project:
                has_project_access = proj in user_applications
                is_admin_of_project = user_is_global_admin or proj in user_admin_projects

                all_conditions.extend(
                    SkillRepository._build_specific_project_conditions(
                        user_id=user_id,
                        specific_project=proj,
                        has_project_access=has_project_access,
                        is_admin_of_project=is_admin_of_project,
                    )
                )

            # Include marketplace (PUBLIC) skills alongside project skills
            if marketplace_filter == MarketplaceFilter.INCLUDE:
                all_conditions.append(Skill.visibility == SkillVisibility.PUBLIC)

            return all_conditions

        return SkillRepository._build_global_access_conditions(
            user_id=user_id,
            user_applications=user_applications,
            user_is_global_admin=user_is_global_admin,
            user_admin_projects=user_admin_projects,
        )

    # ============================================================================
    # Query Filter Helper Methods
    # ============================================================================

    @staticmethod
    def _apply_skill_filters(
        query,
        project: list[str] | None = None,
        visibility: SkillVisibility | None = None,
        categories: list[SkillCategory] | None = None,
        search_query: str | None = None,
        created_by: str | None = None,
    ):
        """
        Apply optional filters to skill query.

        Args:
            query: Base SQLAlchemy query to filter
            project: Filter by project(s) - supports multiple projects
            visibility: Filter by visibility level
            categories: Filter by categories (any match)
            search_query: Case-insensitive name search
            created_by: Filter by creator user name

        Returns:
            Filtered query
        """
        if project:
            if len(project) == 1:
                query = query.where(Skill.project == project[0])
            else:
                query = query.where(Skill.project.in_(project))

        if visibility:
            query = query.where(Skill.visibility == visibility)

        if categories:
            # Filter skills matching ANY of the provided categories using @> (contains)
            # Same approach as AssistantFilter.compose_json_array_filter
            conditions = [Skill.categories.op("@>")(json.dumps([c.value])) for c in categories]
            query = query.where(or_(*conditions))

        if search_query:
            # Case-insensitive search on skill name
            query = query.where(func.lower(Skill.name).contains(func.lower(search_query)))

        if created_by:
            # Filter by creator user name (consistent with AssistantFilter)
            query = query.where(Skill.created_by["name"].astext == created_by)

        return query

    @staticmethod
    def _get_assistants_count_for_skills(session: Session, skill_ids: list[str]) -> dict[str, int]:
        """
        Get count of assistants using each skill.

        Uses database aggregation with jsonb_array_elements_text to efficiently
        count how many assistants reference each skill.

        Args:
            session: Database session
            skill_ids: List of skill IDs to count

        Returns:
            Dictionary mapping skill_id to count of assistants using it
        """
        if not skill_ids:
            return {}

        from codemie.rest_api.models.assistant import Assistant

        # Use PostgreSQL's jsonb_array_elements_text to flatten JSONB arrays and count by skill_id
        # Note: skill_ids is a JSONB column, so we use jsonb_array_elements_text
        # The ?| operator requires jsonb ?| text[], so we cast our list to ARRAY(String)
        stmt = (
            select(
                func.jsonb_array_elements_text(Assistant.skill_ids).label('skill_id'),
                func.count().label('count'),
            )
            .where(Assistant.skill_ids.op('?|')(cast(skill_ids, ARRAY(String))))
            .group_by(literal_column('skill_id'))
        )

        results = session.exec(stmt).all()

        # Filter to only requested skill_ids (in case assistant has other skills)
        return {row.skill_id: row.count for row in results if row.skill_id in skill_ids}

    # ============================================================================
    # Core CRUD Operations
    # ============================================================================

    @staticmethod
    def create(skill_data: dict[str, Any]) -> Skill:
        """Create new skill record"""
        skill = Skill(**skill_data)
        if not skill.id:
            skill.id = str(uuid4())
        if not skill.created_date:
            skill.created_date = datetime.now(UTC)

        with Session(Skill.get_engine()) as session:
            session.add(skill)
            session.commit()
            session.refresh(skill)
            return skill

    @staticmethod
    def get_by_id(skill_id: str) -> Skill | None:
        """Retrieve skill by ID"""
        with Session(Skill.get_engine()) as session:
            return session.get(Skill, skill_id)

    @staticmethod
    def get_by_name_author_project(
        name: str,
        author_id: str,
        project: str,
    ) -> Skill | None:
        """Retrieve skill by name, author, and project (unique constraint check)"""
        with Session(Skill.get_engine()) as session:
            statement = select(Skill).where(
                and_(
                    func.lower(Skill.name) == func.lower(name),
                    Skill.created_by["id"].astext == author_id,
                    Skill.project == project,
                )
            )
            return session.exec(statement).first()

    @staticmethod
    def get_by_ids(skill_ids: list[str]) -> list[Skill]:
        """Retrieve multiple skills by IDs"""
        if not skill_ids:
            return []
        with Session(Skill.get_engine()) as session:
            statement = select(Skill).where(Skill.id.in_(skill_ids))
            return list(session.exec(statement).all())

    @staticmethod
    def update(skill_id: str, updates: dict[str, Any]) -> Skill | None:
        """Update skill fields"""
        with Session(Skill.get_engine()) as session:
            skill = session.get(Skill, skill_id)
            if not skill:
                return None

            for key, value in updates.items():
                if hasattr(skill, key):
                    setattr(skill, key, value)

            skill.updated_date = datetime.now(UTC)
            session.add(skill)
            session.commit()
            session.refresh(skill)
            return skill

    @staticmethod
    def delete(skill_id: str) -> bool:
        """Delete skill and cascade delete related interactions"""
        with Session(Skill.get_engine()) as session:
            skill = session.get(Skill, skill_id)
            if not skill:
                return False

            # Bulk delete related interactions in a single database operation
            session.exec(delete(SkillUserInteraction).where(SkillUserInteraction.skill_id == skill_id))

            session.delete(skill)
            session.commit()
            return True

    # ============================================================================
    # Query Operations
    # ============================================================================

    @staticmethod
    def list_accessible_to_user(
        user_id: str,
        user_applications: list[str],
        user_is_global_admin: bool = False,
        user_admin_projects: list[str] | None = None,
        project: list[str] | None = None,
        visibility: SkillVisibility | None = None,
        categories: list[SkillCategory] | None = None,
        search_query: str | None = None,
        created_by: str | None = None,
        page: int = 0,
        per_page: int = 20,
        marketplace_filter: MarketplaceFilter = MarketplaceFilter.DEFAULT,
        sort_by: SkillSortBy = SkillSortBy.CREATED_DATE,
    ) -> SkillListResult:
        """
        List skills user can access with pagination.

        Access rules (when no specific project):
        - Owned by user (any visibility, any project)
        - Public visibility (marketplace skills)
        - If GLOBAL ADMIN: ALL PROJECT + PRIVATE from ALL projects
        - If PROJECT ADMIN: ALL from admin projects + PROJECT from other accessible projects
        - If NOT admin: PROJECT skills from accessible projects only

        When filtering by specific project:
        - Owned by user IN that project (any visibility)
        - PROJECT visibility IN that project (if user has access)
        - PRIVATE visibility IN that project (if user is global/project admin of that project)
        - PUBLIC skills are EXCLUDED unless marketplace_filter is INCLUDE

        Args:
            user_id: Current user ID
            user_applications: Projects user has access to
            user_is_global_admin: Whether user is global admin
            user_admin_projects: Projects where user is admin
            project: Optional project(s) filter
            visibility: Optional visibility filter
            categories: Optional categories filter (any match)
            search_query: Optional name search (case-insensitive)
            created_by: Optional creator user name filter
            page: Page number (0-indexed)
            per_page: Items per page
            marketplace_filter: Controls marketplace skill inclusion (DEFAULT/EXCLUDE/INCLUDE)
            sort_by: Sort field (CREATED_DATE, ASSISTANTS_COUNT, or RELEVANCE)
                Context-aware 4-priority sorting for RELEVANCE:
                - WITHOUT project filter: 1) User non-PUBLIC, 2) User PUBLIC,
                  3) Others non-PUBLIC (all accessible), 4) Others PUBLIC
                - WITH project filter: 1) User non-PUBLIC, 2) User PUBLIC,
                  3) Others non-PUBLIC (from filtered project), 4) Others PUBLIC

        Returns:
            SkillListResult with skills, counts, and pagination metadata
        """
        with Session(Skill.get_engine()) as session:
            # Build base query with access control
            # If project is specified, pass it to build access conditions restricted to that project
            access_conditions = SkillRepository._build_access_conditions(
                user_id,
                user_applications,
                user_is_global_admin=user_is_global_admin,
                user_admin_projects=user_admin_projects,
                specific_project=project,
                marketplace_filter=marketplace_filter,
            )
            base_query = select(Skill).where(or_(*access_conditions))

            # Exclude marketplace skills if requested
            if marketplace_filter == MarketplaceFilter.EXCLUDE:
                base_query = base_query.where(Skill.visibility != SkillVisibility.PUBLIC)

            # Apply optional filters (excluding project since it's already in access conditions)
            filtered_query = SkillRepository._apply_skill_filters(
                base_query,
                project=None,  # Don't apply project filter again - it's in access conditions
                visibility=visibility,
                categories=categories,
                search_query=search_query,
                created_by=created_by,
            )

            # Get total count before pagination
            count_query = select(func.count()).select_from(filtered_query.subquery())
            total = session.exec(count_query).one()

            # Apply ordering based on sort_by parameter
            if sort_by == SkillSortBy.RELEVANCE:
                # Build CASE expression for relevance priority sorting
                # Priority 1: User's own non-marketplace skills (any project)
                # Priority 2: User's own marketplace skills (PUBLIC)
                # Priority 3: Other users' non-marketplace skills (filtered by project if provided)
                # Priority 4: Other users' marketplace skills (PUBLIC)

                # Determine if we should filter Priority 3 by project
                # project filter = filters.project (from request query parameters)
                # If project filter exists and contains exactly one project:
                #   Priority 3 = other users' non-PUBLIC skills from THAT project only
                # Otherwise: Priority 3 = other users' non-PUBLIC skills from ALL accessible projects
                if project is not None and len(project) == 1:
                    priority_3_condition = and_(
                        Skill.created_by["id"].astext != user_id,
                        Skill.project == project[0],
                        Skill.visibility != SkillVisibility.PUBLIC,
                    )
                else:
                    priority_3_condition = and_(
                        Skill.created_by["id"].astext != user_id,
                        Skill.visibility != SkillVisibility.PUBLIC,
                    )

                relevance_priority = case(
                    # Priority 1: User's non-marketplace skills (any project)
                    (
                        and_(
                            Skill.created_by["id"].astext == user_id,
                            Skill.visibility != SkillVisibility.PUBLIC,
                        ),
                        1,
                    ),
                    # Priority 2: User's marketplace skills (PUBLIC, any project)
                    (
                        and_(
                            Skill.created_by["id"].astext == user_id,
                            Skill.visibility == SkillVisibility.PUBLIC,
                        ),
                        2,
                    ),
                    # Priority 3: Other users' non-marketplace skills (context-aware)
                    (priority_3_condition, 3),
                    # Priority 4: Other users' marketplace skills (PUBLIC)
                    (
                        and_(
                            Skill.created_by["id"].astext != user_id,
                            Skill.visibility == SkillVisibility.PUBLIC,
                        ),
                        4,
                    ),
                    # Fallback (should not happen)
                    else_=5,
                )

                paginated_query = (
                    filtered_query.order_by(
                        relevance_priority.asc(),  # Primary: relevance priority
                        Skill.created_date.desc(),  # Secondary: newest first within each priority
                    )
                    .offset(page * per_page)
                    .limit(per_page)
                )

            elif sort_by == SkillSortBy.ASSISTANTS_COUNT:
                # Create subquery to count assistants for each skill
                from codemie.rest_api.models.assistant import Assistant

                # Subquery: Count assistants per skill_id
                assistants_count_subquery = (
                    select(
                        func.jsonb_array_elements_text(Assistant.skill_ids).label('skill_id'),
                        func.count().label('assistants_count'),
                    )
                    .group_by(literal_column('skill_id'))
                    .subquery()
                )

                # Join with filtered skills and order by assistants_count
                paginated_query = (
                    filtered_query.outerjoin(
                        assistants_count_subquery, Skill.id == assistants_count_subquery.c.skill_id
                    )
                    .order_by(
                        func.coalesce(assistants_count_subquery.c.assistants_count, 0).desc(), Skill.created_date.desc()
                    )
                    .offset(page * per_page)
                    .limit(per_page)
                )
            else:
                # Default ordering by created_date (SkillSortBy.CREATED_DATE)
                paginated_query = (
                    filtered_query.order_by(Skill.created_date.desc()).offset(page * per_page).limit(per_page)
                )

            # Fetch paginated skills
            skills = list(session.exec(paginated_query).all())

            # Get assistants count for fetched skills
            skill_ids = [skill.id for skill in skills]
            assistants_count_map = SkillRepository._get_assistants_count_for_skills(session, skill_ids)

            # Calculate total pages
            pages = math.ceil(total / per_page) if per_page > 0 else 1

            return SkillListResult(
                skills=skills,
                assistants_count_map=assistants_count_map,
                page=page,
                per_page=per_page,
                total=total,
                pages=pages,
            )

    @staticmethod
    def count_by_author(author_id: str) -> int:
        """Count skills created by author"""
        with Session(Skill.get_engine()) as session:
            statement = select(func.count()).select_from(Skill).where(Skill.created_by["id"].astext == author_id)
            return session.exec(statement).one()

    @staticmethod
    def count_assistants_using_skill(skill_id: str) -> int:
        """
        Count assistants using skill.
        Queries Assistant table where skill_id in skill_ids array.
        """
        from codemie.rest_api.models.assistant import Assistant

        with Session(Skill.get_engine()) as session:
            # Use JSONB contains operator to check if skill_id is in skill_ids array
            statement = select(func.count()).select_from(Assistant).where(Assistant.skill_ids.contains([skill_id]))
            return session.exec(statement).one()

    @staticmethod
    def get_assistants_using_skill(skill_id: str) -> list:
        """
        Get all assistants that use the specified skill.

        Args:
            skill_id: The skill ID to query

        Returns:
            List of Assistant objects that have the skill_id in their skill_ids array
        """
        from codemie.rest_api.models.assistant import Assistant

        with Session(Skill.get_engine()) as session:
            # Use JSONB contains operator to check if skill_id is in skill_ids array
            statement = select(Assistant).where(Assistant.skill_ids.contains([skill_id]))
            return list(session.exec(statement).all())

    # ============================================================================
    # Reaction Operations
    # ============================================================================

    @staticmethod
    def update_reaction_counts(skill_id: str, like_count: int, dislike_count: int) -> bool:
        """Update skill reaction counts"""
        with Session(Skill.get_engine()) as session:
            skill = session.get(Skill, skill_id)
            if not skill:
                return False

            skill.unique_likes_count = like_count
            skill.unique_dislikes_count = dislike_count
            session.add(skill)
            session.commit()
            return True

    @staticmethod
    def remove_skill_from_all_assistants(skill_id: str) -> int:
        """
        Remove skill ID from all assistants that reference it.

        Args:
            skill_id: The skill ID to remove from assistants

        Returns:
            Number of assistants updated
        """
        from codemie.rest_api.models.assistant import Assistant

        with Session(Skill.get_engine()) as session:
            statement = select(Assistant).where(Assistant.skill_ids.contains([skill_id]))
            assistants = session.exec(statement).all()

            for assistant in assistants:
                if skill_id in assistant.skill_ids:
                    assistant.skill_ids = [sid for sid in assistant.skill_ids if sid != skill_id]
                    assistant.updated_date = datetime.now(UTC)
                    session.add(assistant)

            session.commit()
            return len(assistants)

    @staticmethod
    def get_skill_authors(
        user_id: str,
        user_applications: list[str],
        user_is_global_admin: bool = False,
        user_admin_projects: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """
        Get distinct authors of skills accessible to user.

        Access rules (same as list_accessible_to_user):
        - Owned by user (any visibility)
        - Public visibility (includes marketplace skills)
        - If GLOBAL ADMIN: ALL skills from ALL projects
        - If PROJECT ADMIN: ALL from admin projects + PROJECT from other accessible
        - If NOT admin: PROJECT skills from accessible projects only

        Returns:
            List of dicts with id, name, username (matching CreatedByUser schema)
        """
        with Session(Skill.get_engine()) as session:
            # Build access conditions using helper method for consistency
            access_conditions = SkillRepository._build_access_conditions(
                user_id,
                user_applications,
                user_is_global_admin=user_is_global_admin,
                user_admin_projects=user_admin_projects,
            )

            # Query distinct authors by created_by field
            query = select(Skill.created_by).where(or_(*access_conditions)).distinct()

            result = session.exec(query).all()

            # Convert to list of dicts matching CreatedByUser schema, filtering out None
            return [
                {
                    "id": created_by.id,
                    "name": created_by.name,
                    "username": created_by.username,
                }
                for created_by in result
                if created_by and created_by.id
            ]
