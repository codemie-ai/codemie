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

from math import ceil

from codemie.core.workflow_models.workflow_config import WorkflowConfigBase
from codemie.rest_api.security.user import User
from codemie.core.workflow_models import WorkflowConfig, WorkflowMode, WorkflowConfigListResponse
from codemie.core.ability import Ability
from typing import Any, Dict, Optional, List
from codemie.core.workflow_models import WorkflowListResponse
from codemie.service.filter.filter_services import WorkflowFilter
from codemie.rest_api.models.assistant import CreatedByUser
from sqlmodel import select, or_, and_, Session, func
from sqlalchemy.orm import defer

from abc import ABC, abstractmethod


class QueryModifier(ABC):
    @abstractmethod
    def modify_query(self, query):
        pass


class VisibleToUserModifierPostgres(QueryModifier):
    def __init__(self, user: User, filter_by_user: bool):
        self.user = user
        self.filter_by_user = filter_by_user

    def modify_query(self, query):
        if self.filter_by_user:
            query = query.where(WorkflowConfig.created_by['user_id'] == self.user.id)
        elif not self.user.is_admin:
            query = query.where(
                or_(
                    and_(WorkflowConfig.project.in_(self.user.project_names), WorkflowConfig.shared),
                    WorkflowConfig.project.in_(self.user.admin_project_names),
                    WorkflowConfig.created_by['user_id'].astext == self.user.id,
                )
            )
        return query


class ExcludeAutonomousWorkflowsModifier(QueryModifier):
    """Filter to exclude autonomous workflows and keep only sequential ones"""

    def modify_query(self, query):
        # Filter out autonomous workflows - keep only sequential workflows
        query = query.where(WorkflowConfig.mode == WorkflowMode.SEQUENTIAL)
        return query


class WorkflowConfigIndexService:
    PROJECT_KEY = "project.keyword"
    SORT_FIELD = "update_date"
    SORT_ORDER = "desc"

    @classmethod
    def run(
        cls,
        user: User,
        filter_by_user: bool,
        page: int,
        per_page: int,
        filters: Optional[Dict[str, Any]] = None,
        minimal_response: bool = False,
    ) -> WorkflowListResponse:
        items, total = cls._query_postgres(
            page=page,
            per_page=per_page,
            filters=filters,
            query_modifiers=[
                VisibleToUserModifierPostgres(user, filter_by_user),
                ExcludeAutonomousWorkflowsModifier(),
            ],
            minimal_response=minimal_response,
        )

        pages = ceil(total / per_page)

        for entry in items:
            entry.user_abilities = Ability(user).list(entry)

        # Convert to minimal response model if needed
        if minimal_response:
            # Only include fields defined in WorkflowConfigListResponse
            minimal_items = []
            for entry in items:
                minimal_items.append(
                    WorkflowConfigListResponse(
                        id=entry.id,
                        name=entry.name,
                        description=entry.description,
                        icon_url=entry.icon_url,
                        created_by=entry.created_by,
                        updated_by=entry.updated_by,
                        project=entry.project,
                        shared=entry.shared,
                        mode=entry.mode,
                        type=entry.type,
                        schema_url=entry.schema_url,
                        date=entry.date,
                        update_date=entry.update_date,
                        user_abilities=entry.user_abilities,
                    )
                )
            items = minimal_items

        return WorkflowListResponse(
            data=items,
            pagination=WorkflowListResponse.Pagination(page=page, pages=pages, total=total, per_page=per_page),
        )

    @classmethod
    def find_workflows_by_filters(
        cls,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[WorkflowConfigBase]:
        items, total = cls._query_postgres(
            page=0,
            per_page=100,
            filters=filters,
            query_modifiers=[ExcludeAutonomousWorkflowsModifier()],
        )
        return items

    @classmethod
    def get_users(cls, user: User) -> list[CreatedByUser]:
        """
        Get list of users who created workflows

        Args:
            user: The user making the request

        Returns:
            List of unique users who created workflows,
            excluding None values and users with empty names
        """
        with Session(WorkflowConfig.get_engine()) as session:
            # Use select expression with distinct to get unique created_by values
            query = select(WorkflowConfig.created_by).distinct()

            # Apply visibility filters (same as regular workflow listing)
            if not user.is_admin:
                query = query.where(
                    or_(
                        and_(WorkflowConfig.project.in_(user.project_names), WorkflowConfig.shared),
                        WorkflowConfig.project.in_(user.admin_project_names),
                        WorkflowConfig.created_by['user_id'].astext == user.id,
                    )
                )

            # Exclude autonomous workflows
            query = query.where(WorkflowConfig.mode == WorkflowMode.SEQUENTIAL)

            # Execute the query and get results
            result = session.exec(query).all()

            # Filter out None values and users with empty names, then convert to CreatedByUser
            return [
                CreatedByUser(id=creator.user_id, username=creator.username, name=creator.name)
                for creator in result
                if creator and creator.name
            ]

    @classmethod
    def _query_postgres(
        cls,
        page: int,
        per_page: int,
        filters: Optional[Dict[str, Any]] = None,
        query_modifiers: Optional[List[QueryModifier]] = None,
        minimal_response: bool = False,
    ):
        # PostgreSQL implementation
        with Session(WorkflowConfig.get_engine()) as session:
            query = select(WorkflowConfig)

            # Defer loading of heavy fields when minimal_response is True
            if minimal_response:
                query = query.options(
                    defer(WorkflowConfig.yaml_config),
                    defer(WorkflowConfig.yaml_config_history),
                    defer(WorkflowConfig.assistants),
                    defer(WorkflowConfig.states),
                    defer(WorkflowConfig.custom_nodes),
                    defer(WorkflowConfig.tools),
                )

            if query_modifiers is None:
                query_modifiers = []

            # Apply query modifiers before filters
            for query_modifier in query_modifiers:
                query = query_modifier.modify_query(query)

            if filters:
                query = WorkflowFilter.add_sql_filters(query, model_class=WorkflowConfig, raw_filters=filters)

            # Apply sorting
            query = query.order_by(WorkflowConfig.update_date.desc().nullslast())

            # Apply pagination
            total = session.exec(select(func.count()).select_from(query.subquery())).one()
            query = query.offset(page * per_page).limit(per_page)

            # Execute query
            results = session.exec(query).all()

        return results, total
