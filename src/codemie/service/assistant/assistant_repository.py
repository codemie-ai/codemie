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

import math
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlmodel import select, or_, and_, Session, func, case

from codemie.configs import config
from codemie.core.ability import Ability
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import Assistant, AssistantListResponse, AssistantRequest
from codemie.rest_api.security.user import User
from codemie.service.filter.filter_services import AssistantFilter, AssistantNameFilter


class AssistantScope(str, Enum):
    """Defines the scope of assistants to query."""

    PROJECT_WITH_MARKETPLACE = "project_with_marketplace"
    VISIBLE_TO_USER = "visible_to_user"
    CREATED_BY_USER = "created_by_user"
    MARKETPLACE = "marketplace"
    ALL = "all"


class AssistantRepository:
    """
    Repository for managing Assistant entities.

    This class provides CRUD operations and query capabilities for Assistant objects,
    supporting both local and remote assistants.
    """

    DEFAULT_PER_PAGE = 10_000

    _NAME_FIELD = "name.keyword"
    _PROJECT_FIELD = "project.keyword"
    _SHARED_FIELD = "shared"
    _CREATOR_FIELD = "creator.keyword"
    _IS_GLOBAL_FIELD = "is_global"
    _CREATED_BY_FIELD = "created_by.id.keyword"
    _UPDATE_DATE_FIELD = "update_date"
    _DATE_ORDER = "desc"

    def query(
        self,
        user: User,
        scope: AssistantScope = AssistantScope.VISIBLE_TO_USER,
        filters: Dict[str, Any] = None,
        page: int = 0,
        per_page: int = DEFAULT_PER_PAGE,
        minimal_response: bool = False,
        apply_scope: bool = True,
    ) -> Dict[str, Any]:
        """
        Query assistants based on specified criteria.

        Args:
            user: The user making the request
            scope: The scope of assistants to retrieve (visible to user or created by user)
            filters: Optional filters to apply to the query
            page: Page number for pagination
            per_page: Number of items per page
            minimal_response: Whether to return minimal response data
            apply_scope: Whether to apply scope filter, default is True

        Returns:
            Dictionary containing assistants data and pagination metadata
        """
        with Session(Assistant.get_engine()) as session:
            if scope == AssistantScope.PROJECT_WITH_MARKETPLACE:
                # Special handling for project + marketplace scope
                # Note: filters are already applied inside _build_project_with_marketplace_query
                # because it needs to apply different filters to non-global vs global assistants
                query = self._build_project_with_marketplace_query(user, filters)
            else:
                query = select(Assistant)

                # Apply scope-based filters
                if apply_scope:
                    query = self._apply_scope_filters(query, user, scope)

                # Apply custom filters if any
                if filters:
                    query = AssistantFilter.add_sql_filters(
                        query, model_class=Assistant, raw_filters=filters, is_admin=user.is_admin
                    )

            # Apply sorting
            if scope == AssistantScope.MARKETPLACE:
                # For marketplace, sort by unique users count in descending order
                query = query.order_by(
                    Assistant.unique_users_count.desc().nullslast(),
                    Assistant.update_date.desc().nullslast(),  # Secondary sort by name and id for stable pagination
                    Assistant.id.asc(),
                )
            elif scope == AssistantScope.PROJECT_WITH_MARKETPLACE:
                # Ordering strategy for PROJECT_WITH_MARKETPLACE:
                # 1. Show non-global assistants first (is_global.asc())
                # 2. Within global assistants, sort by popularity (unique_users_count)
                # 3. Within non-global assistants, sort by update_date
                # 4. Use update_date as secondary sort for both groups
                # 5. Use id for stable pagination
                query = query.order_by(
                    Assistant.is_global.asc(),  # Non-global (False=0) first, then global (True=1)
                    case(
                        (Assistant.is_global == True, Assistant.unique_users_count),  # noqa
                        else_=0,  # Non-global gets 0 to keep them grouped by update_date
                    )
                    .desc()
                    .nullslast(),
                    Assistant.update_date.desc().nullslast(),  # Secondary sort for both groups
                    Assistant.id.asc(),  # Stable pagination
                )
            else:
                # For other scopes, maintain the original sorting by update date
                query = query.order_by(Assistant.update_date.desc().nullslast())

            # Apply pagination
            total = session.exec(select(func.count()).select_from(query.subquery())).one()
            query = query.offset(page * per_page).limit(per_page)

            # Execute query
            assistants = session.exec(query).all()

        pages = math.ceil(total / per_page)

        meta = {"page": page, "per_page": per_page, "total": total, "pages": pages}

        # Add user abilities to each result
        for item in assistants:
            item.user_abilities = Ability(user).list(item)

        # Convert to response model if needed
        response_wrapper = AssistantListResponse if minimal_response else Assistant
        if response_wrapper != Assistant:
            assistants = [response_wrapper(**entry.model_dump()) for entry in assistants]

        return {"data": assistants, "pagination": meta}

    @staticmethod
    def update(assistant: Assistant, assistant_request: AssistantRequest, user: User) -> Optional[Assistant]:
        """
        Update an assistant with new data.

        Args:
            assistant: The assistant to update
            assistant_request: The new assistant data
            user: The user making the request

        Returns:
            The updated assistant
        """
        assistant.update_assistant(assistant_request, user)
        return assistant

    @staticmethod
    def enrich_system_prompt_history(assistant: Assistant) -> Assistant:
        """
        Populate system_prompt_history from AssistantConfiguration versions.

        This method builds the system_prompt_history list from version configurations,
        making it the single source of truth for prompt changes. Only versions where
        the system_prompt actually changed are included in the history.

        Args:
            assistant: The assistant to enrich

        Returns:
            The assistant with populated system_prompt_history
        """
        from codemie.rest_api.models.assistant import AssistantConfiguration, SystemPromptHistory

        # Skip if assistant doesn't have an ID (not saved yet)
        if not assistant.id:
            return assistant

        try:
            # Get all version configurations for this assistant
            configs = AssistantConfiguration.get_version_history(
                assistant_id=assistant.id,
                page=0,
                per_page=1000,  # Get all versions
            )

            # Build history from configurations, only including versions where system_prompt changed
            history = []
            last_system_prompt = assistant.system_prompt

            for config in configs:
                # Skip if this is the current version (it's already in assistant.system_prompt)
                if config.version_number == getattr(assistant, 'version_count', 1):
                    continue

                # Only add to history if the system_prompt is different from the last one we checked
                if config.system_prompt != last_system_prompt:
                    history.append(
                        SystemPromptHistory(
                            system_prompt=config.system_prompt,
                            date=config.created_date,
                            created_by=config.created_by,
                        )
                    )

                # Update last_system_prompt for next iteration
                last_system_prompt = config.system_prompt

            # Set the enriched history
            assistant.system_prompt_history = history

        except Exception as e:
            # If anything goes wrong, keep existing history (backward compatibility)
            from codemie.configs.logger import logger

            logger.warning(f"Failed to enrich system_prompt_history for assistant {assistant.id}: {e}")

        return assistant

    @staticmethod
    def increment_usage_count(assistant: Assistant, count: Optional[int] = None) -> Assistant:
        """
        Update the unique users count for an assistant without updating the update_date.

        Args:
            assistant: The assistant to update
            count: The new count of unique users (if None, increments by 1)

        Returns:
            The updated assistant
        """
        # Fetch fresh assistant from database to ensure it's session-tracked
        fresh_assistant = Assistant.find_by_id(assistant.id)
        if not fresh_assistant:
            from codemie.configs.logger import logger

            logger.warning(
                f"Unable to fetch assistant {assistant.id} for usage count update. "
                "This may indicate the assistant was deleted or is detached from the session."
            )
            # Return original assistant unchanged
            return assistant

        # Calculate the new count
        current_count = fresh_assistant.unique_users_count or 0
        new_count = count if count is not None else current_count + 1

        # Update the unique_users_count field
        fresh_assistant.unique_users_count = new_count
        fresh_assistant.save()
        return fresh_assistant

    @staticmethod
    def update_reaction_counts(assistant: Assistant, likes_count: int, dislikes_count: int) -> Assistant:
        """
        Update both like and dislike counts for an assistant in a single database operation.

        Args:
            assistant: The assistant to update
            likes_count: The new count of unique likes
            dislikes_count: The new count of unique dislikes

        Returns:
            The updated assistant
        """
        # Fetch fresh assistant from database to ensure it's session-tracked
        fresh_assistant = Assistant.find_by_id(assistant.id)
        if not fresh_assistant:
            from codemie.configs.logger import logger

            logger.warning(
                f"Unable to fetch assistant {assistant.id} for reaction count update. "
                "This may indicate the assistant was deleted or is detached from the session."
            )
            # Return original assistant unchanged
            return assistant

        # Update the counts
        fresh_assistant.unique_likes_count = likes_count
        fresh_assistant.unique_dislikes_count = dislikes_count

        fresh_assistant.save()
        return fresh_assistant

    def get_users(self, user: User, scope: AssistantScope = AssistantScope.VISIBLE_TO_USER) -> list[CreatedByUser]:
        """
        Get list of users who created assistants

        Args:
            user: The user making the request
            scope: The scope of assistants to consider

        Returns:
            List of unique users who created assistants within the specified scope,
            excluding None values and users with empty names
        """
        with Session(Assistant.get_engine()) as session:
            # Use select expression with distinct to get unique created_by values
            query = select(Assistant.created_by).distinct()

            # Apply scope-based filters
            query = self._apply_scope_filters(query, user, scope)

            # Execute the query and get results
            result = session.exec(query).all()

            # Filter out None values and users with empty names
            return [user for user in result if user and user.name]

    def delete(self, id_: str) -> None:
        """
        Delete an assistant by ID.

        Args:
            id_: The ID of the assistant to delete
        """
        Assistant.delete_assistant(assistant_id=id_)

    @staticmethod
    def _merge_assistants(local_assistants: List[Assistant], remote_assistants: List[Assistant]) -> List[Assistant]:
        """Merge local and remote assistants, ensuring proper ordering by update_date"""
        all_assistants = local_assistants + remote_assistants
        # Sort by update_date in descending order
        return sorted(all_assistants, key=lambda x: x.update_date if x.update_date else datetime.min, reverse=True)

    def _apply_scope_filters(self, query, user: User, scope: AssistantScope):
        """
        Apply filters based on scope and user permissions.

        Args:
            query: The base query to modify
            user: The user making the request
            scope: The scope of assistants to retrieve

        Returns:
            Modified query with scope filters applied
        """
        scope_filter_map = {
            AssistantScope.VISIBLE_TO_USER: self._apply_visible_to_user_filter,
            AssistantScope.ALL: self._apply_all_available_filter,
            AssistantScope.CREATED_BY_USER: self._apply_created_by_user_filter,
            AssistantScope.MARKETPLACE: self._apply_marketplace_filter,
        }

        filter_func = scope_filter_map.get(scope)
        if filter_func:
            return filter_func(query, user)
        return query

    def _apply_visible_to_user_filter(self, query, user: User):
        """
        Apply filters for assistants visible to a user.

        Args:
            query: The base query to modify
            user: The user making the request

        Returns:
            Modified query with visibility filters applied
        """
        if user.is_admin:
            # For admins: show all non-global assistants
            return query.where(Assistant.is_global == False)  # noqa
        else:
            # For regular users: show assistants they can access
            return self._filter_for_regular_user_visibility(query, user)

    def _apply_all_available_filter(self, query, user: User):
        """
        Apply filters for all available assistants.

        For external users: Show marketplace assistants from allowed projects + user's own applications
        For internal users (admins): Show all assistants
        For internal users (regular): Show assistants they have access to including all marketplace

        Args:
            query: The base query to modify
            user: The user making the request

        Returns:
            Modified query with filters applied
        """
        if user.is_admin:
            return query
        else:
            # For regular users: show assistants they can access
            return self._filter_for_regular_user_visibility(query, user, include_global=True)

    def _apply_created_by_user_filter(self, query, user: User):
        """
        Apply filters for assistants created by a user.

        Args:
            query: The base query to modify
            user: The user making the request

        Returns:
            Modified query with creator filters applied
        """
        return self._filter_for_user_created(query, user)

    def _get_marketplace_condition(self, user: User):
        """
        Get the marketplace filter condition (reusable helper).

        For external users: Global assistants from allowed projects + user's own applications
        For internal users: All global assistants

        Args:
            user: The user making the request

        Returns:
            SQL condition for marketplace assistants
        """
        # Base filter: only global assistants
        base_filter = Assistant.is_global == True  # noqa

        # Additional filter for external users
        if user.is_external_user:
            # External users can see marketplace assistants from allowed projects + their own applications
            allowed_projects = list(set(config.EXTERNAL_USER_ALLOWED_PROJECTS + user.project_names))
            return and_(base_filter, Assistant.project.in_(allowed_projects))

        # Internal users see all marketplace assistants
        return base_filter

    def _apply_marketplace_filter(self, query, user: User):
        """
        Apply filters for marketplace assistants.

        TODO: need to clarify
        For external users: Show global assistants from allowed projects (codemie, epm-cdme) + user's own applications
        For internal users: Show all global assistants

        Args:
            query: The base query to modify
            user: The user making the request

        Returns:
            Modified query with marketplace filters applied
        """
        return query.where(self._get_marketplace_condition(user))

    def _get_non_global_base_condition(self, user: User, project: str | None = None):
        """
        Get base condition for non-global assistants based on user permissions.

        Args:
            user: The user making the request

        Returns:
            SQL condition for non-global assistants
        """
        if user.is_admin:
            # Admins see all non-global assistants from the project (no visibility restrictions)
            return Assistant.is_global == False  # noqa

        user_guard_expr = or_(
            # Shared assistants in user's applications
            and_(Assistant.project.in_(user.project_names), Assistant.shared == True),  # noqa
            # Assistants in projects user administers
            Assistant.project.in_(user.admin_project_names),
            # Assistants created by user
            self._created_by_user_condition(user),
        )

        if project:
            # If project is specified, include it in the base condition
            return and_(
                Assistant.is_global == False,  # noqa
                Assistant.project == project,
                user_guard_expr,
            )

        # Regular users see only non-global assistants they have access to
        return and_(
            Assistant.is_global == False,  # noqa
            user_guard_expr,
        )

    def _build_project_with_marketplace_query(self, user: User, filters: Dict[str, Any] = None):
        """
        Build query for project assistants combined with marketplace assistants.

        Shows:
        - Non-global assistants from the specified project (with project filtering)
        - All global assistants (without project filtering)

        Args:
            user: The user making the request
            filters: Filters to apply, must include 'project' key

        Returns:
            SQLModel query with combined conditions and sorting
        """
        project = filters.get("project") if filters else None

        # Remove project filter for marketplace condition (global assistants ignore project)
        marketplace_filters = {k: v for k, v in filters.items() if k != 'project'}

        # Build base condition for non-global assistants
        non_global_base_condition = self._get_non_global_base_condition(user, project=project)

        # Build non-global query: apply ALL filters (including project)
        non_global_query = select(Assistant).where(non_global_base_condition)
        non_global_query = AssistantNameFilter.add_sql_filters(
            non_global_query, model_class=Assistant, raw_filters=filters, is_admin=user.is_admin
        )
        non_global_condition = non_global_query.whereclause  # Extract WHERE clause for combining

        # Build marketplace query: apply filters EXCEPT project (global assistants ignore project filter)
        marketplace_base_condition = self._get_marketplace_condition(user)
        if marketplace_filters:
            marketplace_query = select(Assistant).where(marketplace_base_condition)
            marketplace_query = AssistantNameFilter.add_sql_filters(
                marketplace_query, model_class=Assistant, raw_filters=marketplace_filters, is_admin=user.is_admin
            )
            marketplace_condition = marketplace_query.whereclause  # Extract WHERE clause for combining
        else:
            marketplace_condition = marketplace_base_condition

        # Combine both conditions: (non-global with all filters) OR (global with filters except project)
        query = select(Assistant).where(or_(non_global_condition, marketplace_condition))

        return query

    def _filter_for_regular_user_visibility(self, query, user: User, include_global: bool = False):
        """
        Filter assistants visible to a regular user.

        Args:
            query: The base query to modify
            user: The user making the request

        Returns:
            Modified query with regular user visibility filters applied
        """
        if include_global:
            return query.where(
                or_(
                    and_(Assistant.project.in_(user.project_names), Assistant.shared),
                    Assistant.project.in_(user.admin_project_names),
                    Assistant.creator == user.id,
                    Assistant.created_by['id'].astext == user.id,
                    Assistant.is_global,
                )
            )
        return query.where(
            and_(
                Assistant.is_global == False,  # noqa
                or_(
                    # Shared assistants in user's applications
                    and_(Assistant.project.in_(user.project_names), Assistant.shared),
                    # Assistants in projects user administers
                    Assistant.project.in_(user.admin_project_names),
                    # Assistants created by user
                    self._created_by_user_condition(user),
                ),
            )
        )

    def _filter_for_user_created(self, query, user: User):
        """
        Filter assistants created by a specific user.

        Args:
            query: The base query to modify
            user: The user making the request

        Returns:
            Modified query with creator filters applied
        """
        return query.where(self._created_by_user_condition(user))

    def _created_by_user_condition(self, user: User):
        """
        Condition to check if an assistant was created by the user.

        Args:
            user: The user to check against

        Returns:
            SQL condition for assistants created by the user
        """
        return or_(Assistant.creator == user.id, Assistant.created_by['id'].astext == user.id)
