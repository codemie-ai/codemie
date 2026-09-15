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

"""Unit tests for /v1/projects visibility, creation, assignment, delete, and update endpoints."""

from contextlib import asynccontextmanager
from datetime import datetime, UTC
from datetime import date as date_type
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.projects import (
    router as projects_router,
    _authorize_project_access,
    _build_project_detail_response,
    _raise_project_not_found,
    ProjectAssignmentRequest,
    ProjectAssignmentUpdateRequest,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectDetailResponse,
    ProjectListItem,
    ProjectUpdateRequest,
    PaginatedProjectListResponse,
    assign_user_to_project,
    create_project,
    delete_project,
    get_project_detail,
    get_project_spends,
    list_projects,
    remove_user_from_project,
    update_project,
    update_user_project_assignment,
    PaginatedSpendsResponse,
)
from codemie.configs import config
from codemie.core.models import Application
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory


@asynccontextmanager
async def _mock_session_ctx(session):
    yield session


@pytest.fixture
def regular_user() -> User:
    with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
        return User(id="user-1", username="user1", email="user1@example.com", is_admin=False)


@pytest.fixture
def super_admin_user() -> User:
    with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
        return User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)


class TestProjectCreationEndpoint:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_create_project_calls_service_directly(
        self,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None
        project = SimpleNamespace(
            name="data-pipeline",
            display_name=None,
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
            chargeback_enabled=False,
            chargeback_attribution="project",
        )
        mock_project_service.create_shared_project.return_value = project

        response = create_project(
            payload=ProjectCreateRequest(name="data-pipeline", description="Analytics pipeline"),
            user=regular_user,
        )

        assert response.name == "data-pipeline"
        mock_project_service.create_shared_project.assert_called_once_with(
            user=regular_user,
            project_name="data-pipeline",
            description="Analytics pipeline",
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_create_project_success(
        self,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None
        mock_project_service.create_shared_project.return_value = SimpleNamespace(
            name="data-pipeline",
            display_name=None,
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
            chargeback_enabled=False,
            chargeback_attribution="project",
        )

        response = create_project(
            payload=ProjectCreateRequest(name="data-pipeline", description="Analytics pipeline"),
            user=regular_user,
        )

        assert response.name == "data-pipeline"
        assert response.description == "Analytics pipeline"
        assert response.project_type == "shared"
        assert response.created_by == "user-1"
        assert response.created_at == datetime(2026, 2, 10, tzinfo=UTC)

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_create_project_with_cost_center(
        self,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        cost_center_id = uuid4()
        mock_resolve_cost_center_name.return_value = "epm-cdme"
        mock_project_service.create_shared_project.return_value = SimpleNamespace(
            name="data-pipeline",
            display_name=None,
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
            cost_center_id=cost_center_id,
            chargeback_enabled=False,
            chargeback_attribution="project",
        )

        response = create_project(
            payload=ProjectCreateRequest(
                name="data-pipeline",
                description="Analytics pipeline",
                cost_center_id=cost_center_id,
            ),
            user=regular_user,
        )

        mock_project_service.create_shared_project.assert_called_once_with(
            user=regular_user,
            project_name="data-pipeline",
            description="Analytics pipeline",
            cost_center_id=cost_center_id,
        )
        assert response.cost_center_id == cost_center_id
        assert response.cost_center_name == "epm-cdme"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_create_project_propagates_service_error(
        self,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_project_service.create_shared_project.side_effect = ExtendedHTTPException(
            code=409,
            message="Project 'my-project' already exists. Please choose a different name.",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            create_project(
                payload=ProjectCreateRequest(name="my-project", description="desc"),
                user=regular_user,
            )

        assert exc_info.value.code == 409
        assert exc_info.value.message == "Project 'my-project' already exists. Please choose a different name."


class TestProjectsVisibilityEndpoints:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    @pytest.mark.anyio
    async def test_list_projects_uses_visibility_filtered_search(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """Story 16: List projects with pagination and search"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.list_visible_projects_paginated.return_value = (
            [
                {
                    "name": "shared-proj",
                    "description": "desc",
                    "project_type": "shared",
                    "created_by": "owner-1",
                    "created_at": datetime(2026, 2, 10, tzinfo=UTC),
                    "user_count": 3,
                    "admin_count": 1,
                }
            ],
            1,
        )

        result = await list_projects(
            search="shared",
            page=0,
            per_page=20,
            include_counters=True,
            include_spending=False,
            include_budgets=False,
            has_assigned_budgets=False,
            budget_category=None,
            sort_by=None,
            sort_order="asc",
            user=regular_user,
        )

        assert isinstance(result, PaginatedProjectListResponse)
        assert len(result.data) == 1
        assert result.data[0].name == "shared-proj"
        assert result.data[0].user_count == 3
        assert result.data[0].admin_count == 1
        assert result.pagination.total == 1
        assert result.pagination.page == 0
        assert result.pagination.per_page == 20
        mock_visibility_service.list_visible_projects_paginated.assert_called_once_with(
            session=mock_session,
            user_id="user-1",
            is_admin=False,
            search="shared",
            page=0,
            per_page=20,
            has_assigned_budgets=False,
            budget_category=None,
            include_counters=True,
            sort_by=None,
            sort_order="asc",
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    @pytest.mark.anyio
    async def test_get_project_detail_includes_created_at_and_members(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """Story 16: Project detail includes created_at and member list"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.get_visible_project_with_members.return_value = {
            "name": "shared-proj",
            "description": "desc",
            "project_type": "shared",
            "created_by": "owner-1",
            "created_at": datetime(2026, 2, 10, tzinfo=UTC),
            "user_count": 2,
            "admin_count": 1,
            "members": [
                {
                    "user_id": "user-1",
                    "is_project_admin": True,
                    "date": datetime(2026, 2, 10, tzinfo=UTC),
                },
                {
                    "user_id": "user-2",
                    "is_project_admin": False,
                    "date": datetime(2026, 2, 11, tzinfo=UTC),
                },
            ],
        }

        result = await get_project_detail(
            request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/shared-proj")),
            project_name="shared-proj",
            include_spending=False,
            user=regular_user,
        )

        assert isinstance(result, ProjectDetailResponse)
        assert result.created_at == datetime(2026, 2, 10, tzinfo=UTC)
        assert result.user_count == 2
        assert result.admin_count == 1
        assert len(result.members) == 2
        assert result.members[0].user_id == "user-1"
        assert result.members[0].is_project_admin is True

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    @pytest.mark.anyio
    async def test_get_project_detail_returns_404_when_invisible(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.get_visible_project_with_members.side_effect = ExtendedHTTPException(
            code=404, message="Project not found"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_project_detail(
                request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/hidden-proj")),
                project_name="hidden-proj",
                include_spending=False,
                user=regular_user,
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        mock_visibility_service.get_visible_project_with_members.assert_called_once_with(
            session=mock_session,
            project_name="hidden-proj",
            user_id="user-1",
            is_admin=False,
            action="GET /v1/projects/hidden-proj",
        )

    @patch(
        "codemie.rest_api.routers.projects.project_budget_assignment_repository."
        "get_assigned_budget_summaries_for_projects",
        new_callable=AsyncMock,
    )
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.budget_repository.get_all_keyed_by_id", new_callable=AsyncMock)
    @patch("codemie.rest_api.routers.projects._spend_repo.get_lifetime_spend", new_callable=AsyncMock)
    @patch("codemie.rest_api.routers.projects._spend_repo.get_latest_budget_rows_for_project", new_callable=AsyncMock)
    @patch("codemie.rest_api.routers.projects._spend_repo.get_latest_key_spending_for_project", new_callable=AsyncMock)
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects._get_project_detail_sync")
    @pytest.mark.anyio
    async def test_get_project_detail_fetches_spending_for_project_admin(
        self,
        mock_get_project_detail_sync,
        mock_get_async_session,
        mock_get_latest_key_spending,
        mock_get_latest_budget_rows,
        mock_get_lifetime_spend,
        mock_get_all_keyed_by_id,
        mock_config,
        mock_assigned_budgets,
    ):
        mock_assigned_budgets.return_value = {}
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_get_project_detail_sync.return_value = {
            "name": "proj-a",
            "description": "desc",
            "project_type": "shared",
            "created_by": "owner-1",
            "created_at": datetime(2026, 2, 10, tzinfo=UTC),
            "user_count": 2,
            "admin_count": 1,
            "members": [],
            "is_project_admin": True,
        }
        mock_get_latest_key_spending.return_value = None
        mock_get_latest_budget_rows.return_value = []
        mock_get_lifetime_spend.return_value = 0.0
        mock_get_all_keyed_by_id.return_value = {}
        mock_get_async_session.return_value = _mock_session_ctx(AsyncMock())
        project_admin_user = User(
            id="user-1",
            username="user1",
            email="user1@example.com",
            is_admin=False,
            admin_project_names=["proj-a"],
            project_names=["proj-a"],
        )

        result = await get_project_detail(
            request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/proj-a")),
            project_name="proj-a",
            include_spending=True,
            user=project_admin_user,
        )

        assert isinstance(result, ProjectDetailResponse)
        mock_get_latest_key_spending.assert_awaited_once()
        assert mock_get_latest_budget_rows.await_count == 2
        assert mock_get_latest_budget_rows.await_args_list[0].kwargs["spend_subject_type"] == "project_budget"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.SettingsService.get_enforce_member_spend_limits")
    @patch("codemie.rest_api.routers.projects._get_project_detail_sync")
    @pytest.mark.anyio
    async def test_get_project_detail_includes_project_member_budget_tracking_flag(
        self,
        mock_get_project_detail_sync,
        mock_get_tracking_enabled,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_get_tracking_enabled.return_value = True
        mock_get_project_detail_sync.return_value = {
            "name": "proj-a",
            "description": "desc",
            "project_type": "shared",
            "created_by": "owner-1",
            "created_at": datetime(2026, 2, 10, tzinfo=UTC),
            "user_count": 2,
            "admin_count": 1,
            "members": [],
            "is_project_admin": True,
            "cost_center_id": None,
            "cost_center_name": None,
        }

        result = await get_project_detail(
            request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/proj-a")),
            project_name="proj-a",
            include_spending=False,
            user=User(
                id="user-1",
                username="user1",
                email="user1@example.com",
                is_admin=False,
                admin_project_names=["proj-a"],
                project_names=["proj-a"],
            ),
        )

        assert isinstance(result, ProjectDetailResponse)
        assert result.enforce_member_spend_limits is True
        mock_get_tracking_enabled.assert_called_once_with("proj-a")

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    @pytest.mark.anyio
    async def test_list_projects_pagination_first_page(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.list_visible_projects_paginated.return_value = (
            [
                {
                    "name": f"proj-{i}",
                    "description": None,
                    "project_type": "shared",
                    "created_by": "owner-1",
                    "created_at": datetime(2026, 2, 10, tzinfo=UTC),
                    "user_count": 1,
                    "admin_count": 1,
                }
                for i in range(10)
            ],
            100,
        )

        result = await list_projects(
            search=None,
            page=0,
            per_page=10,
            include_spending=False,
            include_budgets=False,
            has_assigned_budgets=False,
            budget_category=None,
            user=regular_user,
        )

        assert result.pagination.page == 0
        assert result.pagination.per_page == 10
        assert result.pagination.total == 100
        assert len(result.data) == 10

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    @pytest.mark.anyio
    async def test_list_projects_empty_results(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """Story 16: Empty results return valid paginated response"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.list_visible_projects_paginated.return_value = ([], 0)

        result = await list_projects(
            search="nonexistent",
            page=0,
            per_page=20,
            include_spending=False,
            include_budgets=False,
            has_assigned_budgets=False,
            budget_category=None,
            user=regular_user,
        )

        assert isinstance(result, PaginatedProjectListResponse)
        assert len(result.data) == 0
        assert result.pagination.total == 0
        assert result.pagination.page == 0

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    @pytest.mark.anyio
    async def test_list_projects_super_admin_sees_all(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """Story 16: Super admin sees all projects including personal"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.list_visible_projects_paginated.return_value = (
            [
                {
                    "name": "personal-proj",
                    "description": None,
                    "project_type": "personal",
                    "created_by": "user-1",
                    "created_at": datetime(2026, 2, 10, tzinfo=UTC),
                    "user_count": 1,
                    "admin_count": 1,
                },
                {
                    "name": "shared-proj",
                    "description": None,
                    "project_type": "shared",
                    "created_by": "user-2",
                    "created_at": datetime(2026, 2, 11, tzinfo=UTC),
                    "user_count": 5,
                    "admin_count": 2,
                },
            ],
            2,
        )

        result = await list_projects(
            search=None,
            page=0,
            per_page=20,
            include_counters=True,
            include_spending=False,
            include_budgets=False,
            has_assigned_budgets=False,
            budget_category=None,
            sort_by=None,
            sort_order="asc",
            user=super_admin_user,
        )

        assert len(result.data) == 2
        assert result.data[0].project_type == "personal"
        assert result.data[1].project_type == "shared"
        mock_visibility_service.list_visible_projects_paginated.assert_called_once_with(
            session=mock_session,
            user_id="admin-1",
            is_admin=True,
            search=None,
            page=0,
            per_page=20,
            has_assigned_budgets=False,
            budget_category=None,
            include_counters=True,
            sort_by=None,
            sort_order="asc",
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_active_provider")
    @patch("codemie.rest_api.routers.projects.project_budget_assignment_repository")
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    @pytest.mark.anyio
    async def test_list_projects_attaches_assigned_budgets_and_skips_invalid_categories(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_async_session,
        mock_project_budget_repo,
        mock_get_active_provider,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.LLM_PROXY_BUDGET_CHECK_ENABLED = True
        mock_get_active_provider.return_value = SimpleNamespace(provider_name="litellm")
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.list_visible_projects_paginated.return_value = (
            [
                {
                    "name": "shared-proj",
                    "description": "desc",
                    "project_type": "shared",
                    "created_by": "owner-1",
                    "created_at": datetime(2026, 2, 10, tzinfo=UTC),
                    "user_count": 3,
                    "admin_count": 1,
                }
            ],
            1,
        )
        mock_async_session.return_value = AsyncMock()
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects = AsyncMock(
            return_value={
                "shared-proj": [
                    SimpleNamespace(
                        budget_id="budget-1",
                        name="CLI Budget",
                        budget_category="cli",
                        soft_budget=10.0,
                        max_budget=20.0,
                        budget_duration="30d",
                        budget_reset_at="2026-04-23T10:10:00Z",
                        provider_sync_status="ok",
                        member_count=2,
                        allocated_member_budget_total=20.0,
                        current_spending=5.0,
                    ),
                    SimpleNamespace(
                        budget_id="budget-2",
                        name="Broken Budget",
                        budget_category="not-a-category",
                        soft_budget=1.0,
                        max_budget=2.0,
                        budget_duration="30d",
                        budget_reset_at=None,
                        provider_sync_status="failed",
                        member_count=0,
                        allocated_member_budget_total=0.0,
                        current_spending=0.0,
                    ),
                ]
            }
        )

        result = await list_projects(
            search=None,
            page=0,
            per_page=20,
            include_counters=True,
            include_spending=False,
            include_budgets=True,
            has_assigned_budgets=False,
            budget_category=None,
            sort_by=None,
            sort_order="asc",
            user=regular_user,
        )

        assert len(result.data[0].budgets) == 1
        assert result.data[0].budgets[0].budget_id == "budget-1"
        assert result.data[0].budgets[0].budget_category == BudgetCategory.CLI

    @patch(
        "codemie.rest_api.routers.projects.project_budget_assignment_repository."
        "get_assigned_budget_summaries_for_projects",
        new_callable=AsyncMock,
    )
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.budget_repository")
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    @patch("codemie.rest_api.routers.projects._spend_repo")
    @pytest.mark.anyio
    async def test_list_projects_builds_spending_summary_from_latest_key_and_budget_rows(
        self,
        mock_spend_repo,
        mock_visibility_service,
        mock_get_session,
        mock_async_session,
        mock_budget_repository,
        mock_config,
        mock_assigned_budgets,
        super_admin_user,
    ):
        mock_assigned_budgets.return_value = {}
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.list_visible_projects_paginated.return_value = (
            [
                {
                    "name": "shared-proj",
                    "description": "desc",
                    "project_type": "shared",
                    "created_by": "owner-1",
                    "created_at": datetime(2026, 2, 10, tzinfo=UTC),
                    "user_count": 3,
                    "admin_count": 1,
                    "is_project_admin": True,
                }
            ],
            1,
        )
        mock_async_session.return_value = AsyncMock()
        latest_key_row = MagicMock(
            project_name="shared-proj",
            budget_id="key-budget",
            budget_period_spend=5.0,
            spend_date=datetime(2026, 4, 11, tzinfo=UTC),
        )
        stale_key_row = MagicMock(
            project_name="shared-proj",
            budget_id="key-budget",
            budget_period_spend=4.0,
            spend_date=datetime(2026, 4, 10, tzinfo=UTC),
        )
        latest_budget_row = MagicMock(
            project_name="shared-proj",
            budget_id="budget-1",
            budget_period_spend=3.0,
            spend_date=datetime(2026, 4, 11, tzinfo=UTC),
        )
        stale_budget_row = MagicMock(
            project_name="shared-proj",
            budget_id="budget-1",
            budget_period_spend=2.0,
            spend_date=datetime(2026, 4, 10, tzinfo=UTC),
        )
        mock_spend_repo.get_latest_spending_by_project = AsyncMock(
            side_effect=[[stale_key_row, latest_key_row], [stale_budget_row, latest_budget_row], []]
        )
        mock_budget = MagicMock()
        mock_budget.max_budget = 16.0
        mock_budget.budget_reset_at = None
        mock_budget.deleted_at = None
        mock_budget_repository.get_all_keyed_by_id = AsyncMock(return_value={"key-budget": mock_budget})

        result = await list_projects(
            search=None,
            page=0,
            per_page=20,
            include_counters=True,
            include_spending=True,
            include_budgets=False,
            has_assigned_budgets=False,
            budget_category=None,
            sort_by=None,
            sort_order="asc",
            user=super_admin_user,
        )

        assert result.data[0].spending is not None
        assert result.data[0].spending.current_spending == pytest.approx(8.0)
        assert result.data[0].spending.budget_limit == pytest.approx(16.0)
        assert result.data[0].spending.total_percent == pytest.approx(50.0)

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_visibility_service")
    @pytest.mark.anyio
    async def test_project_detail_includes_all_required_fields(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """Story 16: Project detail response includes all required fields"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_visibility_service.get_visible_project_with_members.return_value = {
            "name": "analytics-dashboard",
            "description": "Analytics project",
            "project_type": "shared",
            "created_by": "user-456",
            "created_at": datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC),
            "user_count": 5,
            "admin_count": 2,
            "members": [
                {"user_id": "user-1", "is_project_admin": True, "date": datetime(2026, 1, 15, tzinfo=UTC)},
                {"user_id": "user-2", "is_project_admin": False, "date": datetime(2026, 1, 16, tzinfo=UTC)},
            ],
        }

        result = await get_project_detail(
            request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/analytics-dashboard")),
            project_name="analytics-dashboard",
            include_spending=False,
            user=regular_user,
        )

        # Verify all Story 16 required fields
        assert result.name == "analytics-dashboard"
        assert result.description == "Analytics project"
        assert result.project_type == "shared"
        assert result.created_by == "user-456"
        assert result.user_count == 5
        assert result.admin_count == 2
        assert result.created_at == datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
        assert len(result.members) == 2


class TestProjectAssignmentEndpoints:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_assign_user_returns_404_for_personal_project(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.side_effect = ExtendedHTTPException(
            code=404, message="Project not found"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            assign_user_to_project(
                request=MagicMock(
                    method="POST",
                    url=SimpleNamespace(path="/v1/projects/owner@example.com/assignment"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                payload=ProjectAssignmentRequest(user_id="target-1", is_project_admin=False),
                project_name="owner@example.com",
                authorized_project=MagicMock(project_type="personal"),
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_assign_user_success(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.return_value = {
            "message": "User assigned to project successfully",
            "user_id": "target-1",
            "project_name": "shared-proj",
            "is_project_admin": True,
        }

        response = assign_user_to_project(
            request=MagicMock(
                method="POST",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment"),
                state=SimpleNamespace(user=super_admin_user),
            ),
            payload=ProjectAssignmentRequest(user_id="target-1", is_project_admin=True),
            project_name="shared-proj",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert response.message == "User assigned to project successfully"
        assert response.user_id == "target-1"
        assert response.project_name == "shared-proj"
        assert response.is_project_admin is True

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_update_assignment_returns_404_when_target_not_assigned(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.update_user_project_role.side_effect = ExtendedHTTPException(
            code=404, message="User is not assigned to this project"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_user_project_assignment(
                request=MagicMock(
                    method="PUT",
                    url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/target-1"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                payload=ProjectAssignmentUpdateRequest(is_project_admin=False),
                project_name="shared-proj",
                user_id="target-1",
                authorized_project=MagicMock(project_type="shared"),
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User is not assigned to this project"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_remove_assignment_success(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.remove_user_from_project.return_value = {
            "message": "User removed from project successfully",
            "user_id": "target-1",
            "project_name": "shared-proj",
        }

        response = remove_user_from_project(
            request=MagicMock(
                method="DELETE",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/target-1"),
                state=SimpleNamespace(user=super_admin_user),
            ),
            project_name="shared-proj",
            user_id="target-1",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert response.message == "User removed from project successfully"
        assert response.user_id == "target-1"
        assert response.project_name == "shared-proj"

    def test_assignment_routes_use_project_admin_or_super_admin_dependency(self):
        app = FastAPI()
        app.include_router(projects_router)

        route_dependencies: dict[tuple[str, str], set[str]] = {}
        for route in app.routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    route_dependencies[(method, route.path)] = {
                        dependency.call.__name__ for dependency in route.dependant.dependencies
                    }

        assert "_authorize_project_access" in route_dependencies[("POST", "/v1/projects/{projectName}/assignment")]
        assert (
            "_authorize_project_access" in route_dependencies[("PUT", "/v1/projects/{projectName}/assignment/{userId}")]
        )
        assert (
            "_authorize_project_access"
            in route_dependencies[("DELETE", "/v1/projects/{projectName}/assignment/{userId}")]
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_assign_user_returns_409_when_already_assigned(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: Assigning already-assigned user returns 409 Conflict"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.side_effect = ExtendedHTTPException(
            code=409, message="User already assigned to project"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            assign_user_to_project(
                request=MagicMock(
                    method="POST",
                    url=SimpleNamespace(path="/v1/projects/shared-proj/assignment"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                payload=ProjectAssignmentRequest(user_id="target-1", is_project_admin=False),
                project_name="shared-proj",
                authorized_project=MagicMock(project_type="shared"),
            )

        assert exc_info.value.code == 409
        assert exc_info.value.message == "User already assigned to project"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_assign_user_returns_404_when_target_user_not_found(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: Target user not found returns 404"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.side_effect = ExtendedHTTPException(
            code=404, message="User not found"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            assign_user_to_project(
                request=MagicMock(
                    method="POST",
                    url=SimpleNamespace(path="/v1/projects/shared-proj/assignment"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                payload=ProjectAssignmentRequest(user_id="nonexistent", is_project_admin=False),
                project_name="shared-proj",
                authorized_project=MagicMock(project_type="shared"),
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User not found"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_update_assignment_returns_404_for_personal_project(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: Attempting to modify personal project returns 404 (not 403)"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.update_user_project_role.side_effect = ExtendedHTTPException(
            code=404, message="Project not found"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_user_project_assignment(
                request=MagicMock(
                    method="PUT",
                    url=SimpleNamespace(path="/v1/projects/owner@example.com/assignment/target-1"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                payload=ProjectAssignmentUpdateRequest(is_project_admin=False),
                project_name="owner@example.com",
                user_id="target-1",
                authorized_project=MagicMock(project_type="personal"),
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_remove_assignment_returns_404_when_target_not_assigned(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: DELETE on non-member returns 404"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.remove_user_from_project.side_effect = ExtendedHTTPException(
            code=404, message="User is not assigned to this project"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            remove_user_from_project(
                request=MagicMock(
                    method="DELETE",
                    url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/target-1"),
                    state=SimpleNamespace(user=super_admin_user),
                ),
                project_name="shared-proj",
                user_id="target-1",
                authorized_project=MagicMock(project_type="shared"),
            )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User is not assigned to this project"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_project_admin_can_remove_themselves(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """AC: Project admin CAN remove themselves from a project"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.remove_user_from_project.return_value = {
            "message": "User removed from project successfully",
            "user_id": "user-1",
            "project_name": "shared-proj",
        }

        response = remove_user_from_project(
            request=MagicMock(
                method="DELETE",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/user-1"),
                state=SimpleNamespace(user=regular_user),
            ),
            project_name="shared-proj",
            user_id="user-1",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert response.message == "User removed from project successfully"
        assert response.user_id == "user-1"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_project_admin_can_demote_themselves(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """AC: Project admin CAN demote their own is_project_admin flag"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.update_user_project_role.return_value = {
            "message": "User role updated successfully",
            "user_id": "user-1",
            "project_name": "shared-proj",
            "is_project_admin": False,
        }

        response = update_user_project_assignment(
            request=MagicMock(
                method="PUT",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/user-1"),
                state=SimpleNamespace(user=regular_user),
            ),
            payload=ProjectAssignmentUpdateRequest(is_project_admin=False),
            project_name="shared-proj",
            user_id="user-1",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert response.message == "User role updated successfully"
        assert response.is_project_admin is False

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_removing_last_project_admin_succeeds(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: Removing last project admin succeeds (zero admins allowed)"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.remove_user_from_project.return_value = {
            "message": "User removed from project successfully",
            "user_id": "last-admin",
            "project_name": "shared-proj",
        }

        response = remove_user_from_project(
            request=MagicMock(
                method="DELETE",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment/last-admin"),
                state=SimpleNamespace(user=super_admin_user),
            ),
            project_name="shared-proj",
            user_id="last-admin",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert response.message == "User removed from project successfully"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_url_encoded_project_names_work_correctly(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: URL-encoded project names work correctly (e.g., john%40example.com)"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.return_value = {
            "message": "User assigned to project successfully",
            "user_id": "target-1",
            "project_name": "team-analytics",
            "is_project_admin": False,
        }

        response = assign_user_to_project(
            request=MagicMock(
                method="POST",
                url=SimpleNamespace(path="/v1/projects/team-analytics/assignment"),
                state=SimpleNamespace(user=super_admin_user),
            ),
            payload=ProjectAssignmentRequest(user_id="target-1", is_project_admin=False),
            project_name="team-analytics",
            authorized_project=MagicMock(project_type="shared", name="team-analytics"),
        )

        assert response.project_name == "team-analytics"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.project_assignment_service.project_assignment_service")
    def test_assignment_response_uses_snake_case(
        self,
        mock_assignment_service,
        mock_get_session,
        mock_config,
        super_admin_user,
    ):
        """AC: All JSON response fields use snake_case"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_assignment_service.assign_user_to_project.return_value = {
            "message": "User assigned to project successfully",
            "user_id": "target-1",
            "project_name": "shared-proj",
            "is_project_admin": True,
        }

        response = assign_user_to_project(
            request=MagicMock(
                method="POST",
                url=SimpleNamespace(path="/v1/projects/shared-proj/assignment"),
                state=SimpleNamespace(user=super_admin_user),
            ),
            payload=ProjectAssignmentRequest(user_id="target-1", is_project_admin=True),
            project_name="shared-proj",
            authorized_project=MagicMock(project_type="shared"),
        )

        assert hasattr(response, "user_id")
        assert hasattr(response, "project_name")
        assert hasattr(response, "is_project_admin")
        assert response.user_id == "target-1"
        assert response.project_name == "shared-proj"
        assert response.is_project_admin is True


class TestDeleteProjectEndpoint:
    """Tests for DELETE /v1/projects/{projectName}."""

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_delete_project_success_returns_200_with_message(
        self,
        mock_project_service,
        mock_get_session,
        mock_config,
    ):
        """Successful delete returns message and project name."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_project_service.delete_project.return_value = None

        authorized_project = MagicMock()
        authorized_project.name = "my-project"
        authorized_project.project_type = "shared"

        response = delete_project(
            request=MagicMock(
                method="DELETE",
                url=SimpleNamespace(path="/v1/projects/my-project"),
                state=SimpleNamespace(user=MagicMock(id="user-1")),
            ),
            project_name="my-project",
            authorized_project=authorized_project,
        )

        assert response.message == "Project 'my-project' deleted successfully"
        assert response.name == "my-project"
        mock_project_service.delete_project.assert_called_once_with(
            session=mock_session,
            project_name="my-project",
            project_type="shared",
            actor_id="user-1",
            action="DELETE /v1/projects/my-project",
            creator_id=authorized_project.created_by,
        )
        mock_session.commit.assert_called_once()

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_delete_project_propagates_403_from_service(
        self,
        mock_project_service,
        mock_get_session,
        mock_config,
    ):
        """delete_project propagates 403 when service raises it for personal project."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_project_service.delete_project.side_effect = ExtendedHTTPException(
            code=403, message="Cannot delete a personal project"
        )

        authorized_project = MagicMock()
        authorized_project.name = "user@example.com"
        authorized_project.project_type = "personal"

        with pytest.raises(ExtendedHTTPException) as exc_info:
            delete_project(
                request=MagicMock(
                    method="DELETE",
                    url=SimpleNamespace(path="/v1/projects/user@example.com"),
                    state=SimpleNamespace(user=MagicMock(id="user-1")),
                ),
                project_name="user@example.com",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 403
        assert exc_info.value.message == "Cannot delete a personal project"
        mock_session.commit.assert_not_called()

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_delete_project_propagates_409_from_service(
        self,
        mock_project_service,
        mock_get_session,
        mock_config,
    ):
        """delete_project propagates 409 when service raises resource conflict."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_project_service.delete_project.side_effect = ExtendedHTTPException(
            code=409,
            message="Project 'busy-project' cannot be deleted because it has assigned resources.",
        )

        authorized_project = MagicMock()
        authorized_project.name = "busy-project"
        authorized_project.project_type = "shared"

        with pytest.raises(ExtendedHTTPException) as exc_info:
            delete_project(
                request=MagicMock(
                    method="DELETE",
                    url=SimpleNamespace(path="/v1/projects/busy-project"),
                    state=SimpleNamespace(user=MagicMock(id="user-1")),
                ),
                project_name="busy-project",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 409
        mock_session.commit.assert_not_called()


class TestUpdateProjectEndpoint:
    """Tests for PATCH /v1/projects/{projectName}."""

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_description_returns_updated_response(
        self,
        mock_project_service,
        mock_config,
    ):
        """Successful PATCH with description returns ProjectCreateResponse."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = None
        updated_app.description = "new description"
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2024, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "project"
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        response = update_project(
            payload=ProjectUpdateRequest(description="new description"),
            project_name="my-project",
            user=user,
        )

        assert response.name == "my-project"
        assert response.description == "new description"
        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            display_name=None,
            clear_display_name=False,
            description="new description",
            clear_description=False,
            cost_center_id=None,
            clear_cost_center=False,
            enforce_member_spend_limits=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_cost_center_id_sets_update_cost_center_true(
        self,
        mock_project_service,
        mock_config,
    ):
        """PATCH with cost_center_id passes it through and sets update_cost_center=True."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = None
        updated_app.description = ""
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2024, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "project"
        cost_center_id = uuid4()
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        update_project(
            payload=ProjectUpdateRequest(cost_center_id=cost_center_id),
            project_name="my-project",
            user=user,
        )

        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            display_name=None,
            clear_display_name=False,
            clear_description=False,
            description=None,
            cost_center_id=cost_center_id,
            clear_cost_center=False,
            enforce_member_spend_limits=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_clear_cost_center_passes_none_to_service(
        self,
        mock_project_service,
        mock_config,
    ):
        """PATCH with clear_cost_center=True passes cost_center_id=None."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = None
        updated_app.description = ""
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2024, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "project"
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        update_project(
            payload=ProjectUpdateRequest(clear_cost_center=True),
            project_name="my-project",
            user=user,
        )

        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            display_name=None,
            clear_display_name=False,
            clear_description=False,
            description=None,
            cost_center_id=None,
            clear_cost_center=True,
            enforce_member_spend_limits=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_budget_tracking_flag_passes_value_to_service(
        self,
        mock_project_service,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = None
        updated_app.description = ""
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2024, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "project"
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        update_project(
            payload=ProjectUpdateRequest(enforce_member_spend_limits=True),
            project_name="my-project",
            user=user,
        )

        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            display_name=None,
            clear_display_name=False,
            clear_description=False,
            description=None,
            cost_center_id=None,
            clear_cost_center=False,
            enforce_member_spend_limits=True,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_project_propagates_exception_from_service(
        self,
        mock_project_service,
        mock_config,
    ):
        """update_project propagates exceptions raised by the service."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_project_service.update_project.side_effect = ExtendedHTTPException(code=404, message="Project not found")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_project(
                payload=ProjectUpdateRequest(description="new description"),
                project_name="missing-project",
                user=MagicMock(id="user-1"),
            )

        assert exc_info.value.code == 404

    def test_request_model_requires_at_least_one_field(self):
        """ProjectUpdateRequest raises ValueError when no mutable field is provided."""
        with pytest.raises(ValueError, match="At least one mutable field must be provided"):
            ProjectUpdateRequest()

    def test_request_model_rejects_cost_center_id_with_clear_flag(self):
        """ProjectUpdateRequest raises ValueError when both cost_center_id and clear_cost_center are set."""
        with pytest.raises(ValueError, match="Provide either cost_center_id or clear_cost_center"):
            ProjectUpdateRequest(cost_center_id=uuid4(), clear_cost_center=True)

    def test_request_model_rejects_display_name_with_clear_flag(self):
        """ProjectUpdateRequest raises ValueError when both display_name and clear_display_name are set."""
        with pytest.raises(ValueError, match="Provide either display_name or clear_display_name"):
            ProjectUpdateRequest(display_name="Name", clear_display_name=True)

    def test_request_model_accepts_clear_display_name_alone(self):
        """ProjectUpdateRequest treats clear_display_name=True as a valid mutable field on its own."""
        payload = ProjectUpdateRequest(clear_display_name=True)
        assert payload.clear_display_name is True

    def test_request_model_accepts_budget_tracking_flag_as_mutable_field(self):
        request = ProjectUpdateRequest(enforce_member_spend_limits=True)
        assert request.enforce_member_spend_limits is True


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestRaiseProjectNotFound:
    @patch("codemie.rest_api.routers.projects.logger")
    def test_extracts_http_method_from_action(self, mock_logger):
        action = "POST /v1/projects/test-project/assignment"
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _raise_project_not_found(user_id="user-123", action=action)
        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        log_message = mock_logger.warning.call_args[0][0]
        assert "user_id=user-123" in log_message
        assert "method=POST" in log_message
        assert "timestamp=" in log_message
        # PII: project_name must NOT appear in log
        assert "test-project" not in log_message

    @patch("codemie.rest_api.routers.projects.logger")
    def test_action_without_path(self, mock_logger):
        with pytest.raises(ExtendedHTTPException):
            _raise_project_not_found(user_id="user-456", action="DELETE")
        log_message = mock_logger.warning.call_args[0][0]
        assert "method=DELETE" in log_message

    @patch("codemie.rest_api.routers.projects.logger")
    def test_empty_action_defaults_to_unknown(self, mock_logger):
        with pytest.raises(ExtendedHTTPException):
            _raise_project_not_found(user_id="user-789", action="")
        log_message = mock_logger.warning.call_args[0][0]
        assert "method=UNKNOWN" in log_message


class TestCheckProjectAccess:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.projects.Ability")
    def test_resolves_authorized_project(self, mock_ability_class, mock_app_repo, mock_get_session, mock_config):
        mock_config.ENABLE_USER_MANAGEMENT = True

        request = MagicMock()
        request.method = "POST"
        request.url.path = "/v1/projects/shared-proj/assignment"

        from codemie.rest_api.security.user import User

        user = User(id="user-1", username="test", is_admin=False)
        project = MagicMock(spec=["name", "project_type", "deleted_at"])
        project.deleted_at = None

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_app_repo.get_by_name.return_value = project

        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability_class.return_value = mock_ability_instance

        result = _authorize_project_access(request=request, project_name="shared-proj", user=user)

        assert result is project
        mock_app_repo.get_by_name.assert_called_once_with(mock_session, "shared-proj")

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    def test_pure_auditor_denied_write_access(self, mock_app_repo, mock_get_session, mock_config):
        """EPMCDME-10930 spec 5.2: a pure auditor (read-only role) must not be granted
        WRITE access via _authorize_project_access, which gates project update/delete.

        Exercises the real (unmocked) Ability permission check to confirm is_auditor
        grants no exception to the existing owner/project-admin/super-admin gate.
        """
        mock_config.ENABLE_USER_MANAGEMENT = True

        request = MagicMock()
        request.method = "DELETE"
        request.url.path = "/v1/projects/shared-proj"

        from datetime import datetime

        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.core.models import Application
        from codemie.rest_api.security.user import User

        with patch.object(config, "ENV", "dev"):
            auditor = User(id="auditor-1", username="auditor", is_admin=False, is_maintainer=False, is_auditor=True)
        project = Application(
            id="shared-proj",
            name="shared-proj",
            description="Test project",
            project_type=Application.ProjectType.SHARED,
            created_by="someone-else",
            date=datetime.now(),
            update_date=datetime.now(),
        )

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_app_repo.get_by_name.return_value = project

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _authorize_project_access(request=request, project_name="shared-proj", user=auditor)

        assert exc_info.value.code == 404


# ---------------------------------------------------------------------------
# TestValidateUsersFromCsvEndpoint — POST /v1/projects/{projectName}/import-users/validate
# ---------------------------------------------------------------------------


class TestValidateUsersFromCsvEndpoint:
    """Unit tests for the validate_users_from_csv endpoint (dry-run CSV validation)."""

    from codemie.rest_api.routers.projects import validate_users_from_csv

    def _make_upload(self, content: bytes, filename: str = "users.csv") -> MagicMock:
        """Return a mock UploadFile whose .file.read() returns *content*."""
        mock_file = MagicMock()
        mock_file.file.read.return_value = content
        mock_file.filename = filename
        return mock_file

    def _make_project(self) -> MagicMock:
        proj = MagicMock()
        proj.name = "my-project"
        proj.deleted_at = None
        return proj

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.csv_import_service.csv_import_service")
    def test_valid_csv_returns_200_with_users_list(
        self,
        mock_csv_service,
        mock_get_session,
        mock_config,
    ):
        """Happy path — valid CSV → 200 with per-row {email, role, error: null}."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_csv_service.validate_csv.return_value = [
            {"email": "admin@example.com", "role": "project_admin", "error": None},
            {"email": "user@example.com", "role": "user", "error": None},
        ]

        content = b"email,role\nadmin@example.com,project_admin\nuser@example.com,user\n"
        authorized_project = self._make_project()

        # Act
        from codemie.rest_api.routers.projects import validate_users_from_csv

        response = validate_users_from_csv(
            file=self._make_upload(content),
            project_name="my-project",
            authorized_project=authorized_project,
        )

        # Assert
        assert len(response.users) == 2
        assert response.users[0].email == "admin@example.com"
        assert response.users[0].role == "project_admin"
        assert response.users[0].error is None
        assert response.users[1].email == "user@example.com"
        assert response.users[1].error is None
        mock_csv_service.validate_csv.assert_called_once_with(
            session=mock_session,
            content=content,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.csv_import_service.csv_import_service")
    def test_mixed_rows_returns_200_with_errors_populated(
        self,
        mock_csv_service,
        mock_get_session,
        mock_config,
    ):
        """Mixed valid/invalid rows → 200, invalid rows have error field populated."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_csv_service.validate_csv.return_value = [
            {"email": "ok@example.com", "role": "user", "error": None},
            {"email": "bad-email", "role": "user", "error": "Invalid email address: 'bad-email'"},
        ]

        content = b"email,role\nok@example.com,user\nbad-email,user\n"
        authorized_project = self._make_project()

        # Act
        from codemie.rest_api.routers.projects import validate_users_from_csv

        response = validate_users_from_csv(
            file=self._make_upload(content),
            project_name="my-project",
            authorized_project=authorized_project,
        )

        # Assert — still 200, not 422
        assert len(response.users) == 2
        assert response.users[0].error is None
        assert response.users[1].error == "Invalid email address: 'bad-email'"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.csv_import_service.csv_import_service")
    def test_file_exceeding_size_limit_raises_413(
        self,
        mock_csv_service,
        mock_get_session,
        mock_config,
    ):
        """Content longer than MAX_CSV_BYTES → 413 File too large, service not called."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        from codemie.service.project.csv_import_service import MAX_CSV_BYTES

        oversized_content = b"x" * (MAX_CSV_BYTES + 2)
        authorized_project = self._make_project()

        # Act & Assert
        from codemie.rest_api.routers.projects import validate_users_from_csv

        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_users_from_csv(
                file=self._make_upload(oversized_content),
                project_name="my-project",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 413
        assert "too large" in exc_info.value.message.lower()
        mock_csv_service.validate_csv.assert_not_called()

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.csv_import_service.csv_import_service")
    def test_structural_csv_error_propagates_422(
        self,
        mock_csv_service,
        mock_get_session,
        mock_config,
    ):
        """Structural CSV error from service (decode, missing column, etc.) → 422 propagated."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_csv_service.validate_csv.side_effect = ExtendedHTTPException(
            code=422,
            message="CSV decoding failed",
            details="File must be UTF-8 encoded",
        )

        content = b"\xff\xfe garbage bytes"
        authorized_project = self._make_project()

        # Act & Assert
        from codemie.rest_api.routers.projects import validate_users_from_csv

        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_users_from_csv(
                file=self._make_upload(content),
                project_name="my-project",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 422
        assert "decoding" in exc_info.value.message.lower()

    @patch("codemie.rest_api.routers.projects.config")
    def test_user_management_disabled_returns_400(self, mock_config):
        """ENABLE_USER_MANAGEMENT=False → 400 before any file is read or service called."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False
        authorized_project = self._make_project()

        # Act & Assert
        from codemie.rest_api.routers.projects import validate_users_from_csv

        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_users_from_csv(
                file=self._make_upload(b"email,role\nalice@example.com,user\n"),
                project_name="my-project",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 400
        assert "user management" in exc_info.value.message.lower()

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.csv_import_service.csv_import_service")
    def test_validate_route_uses_authorize_project_access_dependency(
        self,
        mock_csv_service,
        mock_get_session,
        mock_config,
    ):
        """The validate endpoint is registered with _authorize_project_access dependency."""
        from fastapi import FastAPI
        from fastapi.routing import APIRoute

        app = FastAPI()
        app.include_router(projects_router)

        route_deps: dict[tuple[str, str], set[str]] = {}
        for route in app.routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    route_deps[(method, route.path)] = {dep.call.__name__ for dep in route.dependant.dependencies}

        validate_key = ("POST", "/v1/projects/{projectName}/import-users/validate")
        assert validate_key in route_deps
        assert "_authorize_project_access" in route_deps[validate_key]

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.service.project.csv_import_service.csv_import_service")
    def test_file_read_requests_sentinel_byte_count(
        self,
        mock_csv_service,
        mock_get_session,
        mock_config,
    ):
        """Handler reads MAX_CSV_BYTES+1 bytes to detect oversized files efficiently."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_csv_service.validate_csv.return_value = []

        from codemie.service.project.csv_import_service import MAX_CSV_BYTES

        mock_upload = self._make_upload(b"email,role\n")
        # Return exactly MAX_CSV_BYTES bytes so size check passes
        mock_upload.file.read.return_value = b"x" * MAX_CSV_BYTES
        authorized_project = self._make_project()

        # Act
        from codemie.rest_api.routers.projects import validate_users_from_csv

        validate_users_from_csv(
            file=mock_upload,
            project_name="my-project",
            authorized_project=authorized_project,
        )

        # Assert — file.read was called with MAX_CSV_BYTES+1 sentinel value
        mock_upload.file.read.assert_called_once_with(MAX_CSV_BYTES + 1)


# ---------------------------------------------------------------------------
# Helpers shared by personal-project spending tests
# ---------------------------------------------------------------------------


def _make_budget_spend_row(
    project_name: str,
    budget_id: str = 'default',
    budget_period_spend: float = 3.50,
    cumulative_spend: float = 12.75,
    max_budget: float | None = 50.0,
    soft_budget: float | None = None,
    budget_reset_at: datetime | None = None,
) -> MagicMock:
    """Return a mock ProjectSpendTracking row with spend_subject_type='budget'."""
    row = MagicMock()
    row.project_name = project_name
    row.budget_id = budget_id
    row.spend_subject_type = 'budget'
    row.budget_period_spend = budget_period_spend
    row.cumulative_spend = cumulative_spend
    row.max_budget = max_budget
    row.soft_budget = soft_budget
    row.budget_duration = 'monthly'
    row.budget_reset_at = budget_reset_at
    row.spend_date = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
    return row


def _make_personal_project_detail(owner_id: str = 'user1@example.com') -> dict:
    """Return a mock project_detail dict for a personal project."""
    return {
        'name': owner_id,
        'description': None,
        'project_type': 'personal',
        'created_by': owner_id,
        'created_at': datetime(2026, 1, 1, tzinfo=UTC),
        'user_count': 1,
        'admin_count': 0,
        'cost_center_id': None,
        'cost_center_name': None,
        'members': [{'user_id': owner_id, 'is_project_admin': False, 'date': datetime(2026, 1, 1, tzinfo=UTC)}],
    }


class TestPersonalProjectSpending:
    """Personal project owners can see their own spending summary and widget."""

    @patch(
        "codemie.rest_api.routers.projects.project_budget_assignment_repository."
        "get_assigned_budget_summaries_for_projects",
        new_callable=AsyncMock,
    )
    @patch('codemie.rest_api.routers.projects.config')
    @patch('codemie.rest_api.routers.projects.budget_repository')
    @patch('codemie.rest_api.routers.projects.get_async_session')
    @patch('codemie.rest_api.routers.projects.get_session')
    @patch('codemie.rest_api.routers.projects.project_visibility_service')
    @patch('codemie.rest_api.routers.projects._spend_repo')
    @pytest.mark.anyio
    async def test_personal_project_owner_sees_spending_in_list(
        self,
        mock_spend_repo,
        mock_visibility_service,
        mock_get_session,
        mock_async_session,
        mock_budget_repository,
        mock_config,
        mock_assigned_budgets,
        regular_user,
    ):
        """Personal project appears in manageable_names even without is_project_admin."""
        mock_assigned_budgets.return_value = {}
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        personal_name = regular_user.id
        mock_visibility_service.list_visible_projects_paginated.return_value = (
            [
                {
                    'name': personal_name,
                    'description': None,
                    'project_type': 'personal',
                    'created_by': personal_name,
                    'created_at': datetime(2026, 1, 1, tzinfo=UTC),
                    'user_count': 1,
                    'admin_count': 0,
                    'counters': None,
                    'cost_center_id': None,
                    'cost_center_name': None,
                }
            ],
            1,
        )

        budget_row = _make_budget_spend_row(project_name=personal_name)
        mock_async_session.return_value = AsyncMock()
        mock_spend_repo.get_latest_spending_by_project = AsyncMock(
            side_effect=lambda session, names, spend_subject_type=None: (
                [budget_row] if spend_subject_type == 'budget' else []
            )
        )
        mock_budget_obj = MagicMock()
        mock_budget_obj.max_budget = 50.0
        mock_budget_obj.budget_reset_at = None
        mock_budget_obj.deleted_at = None
        mock_budget_repository.get_all_keyed_by_id = AsyncMock(return_value={'default': mock_budget_obj})

        result = await list_projects(
            search=None,
            page=0,
            per_page=20,
            include_counters=False,
            include_spending=True,
            include_budgets=False,
            has_assigned_budgets=False,
            budget_category=None,
            sort_by=None,
            sort_order='asc',
            user=regular_user,
        )

        assert len(result.data) == 1
        item = result.data[0]
        assert item.project_type == 'personal'
        assert item.spending is not None
        assert item.spending.current_spending == pytest.approx(3.50)
        assert item.spending.budget_limit == pytest.approx(50.0)

    @patch(
        "codemie.rest_api.routers.projects.project_budget_assignment_repository."
        "get_assigned_budget_summaries_for_projects",
        new_callable=AsyncMock,
    )
    @patch('codemie.rest_api.routers.projects.config')
    @patch('codemie.rest_api.routers.projects.budget_repository')
    @patch('codemie.rest_api.routers.projects.get_async_session')
    @patch('codemie.rest_api.routers.projects.get_session')
    @patch('codemie.rest_api.routers.projects.project_visibility_service')
    @patch('codemie.rest_api.routers.projects._spend_repo')
    @pytest.mark.anyio
    async def test_personal_project_detail_returns_cumulative_and_period_spend(
        self,
        mock_spend_repo,
        mock_visibility_service,
        mock_get_session,
        mock_async_session,
        mock_budget_repository,
        mock_config,
        mock_assigned_budgets,
        regular_user,
    ):
        """Personal project owner can fetch detail with cumulative_spend and current_spending."""
        mock_assigned_budgets.return_value = {}
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        personal_name = regular_user.id
        mock_visibility_service.get_visible_project_with_members.return_value = _make_personal_project_detail(
            owner_id=personal_name
        )

        budget_row = _make_budget_spend_row(
            project_name=personal_name,
            budget_id='default',
            budget_period_spend=3.50,
            cumulative_spend=12.75,
            max_budget=50.0,
        )
        mock_async_session.return_value = AsyncMock()
        mock_spend_repo.get_latest_key_spending_for_project = AsyncMock(return_value=None)
        mock_spend_repo.get_latest_budget_rows_for_project = AsyncMock(
            side_effect=lambda session, name, rows_limit=50, spend_subject_type='budget': (
                [budget_row] if spend_subject_type == 'budget' else []
            )
        )
        mock_spend_repo.get_lifetime_spend = AsyncMock(return_value=12.75)
        mock_default_budget = MagicMock()
        mock_default_budget.max_budget = 50.0
        mock_default_budget.budget_reset_at = None
        mock_default_budget.deleted_at = None
        mock_budget_repository.get_all_keyed_by_id = AsyncMock(return_value={'default': mock_default_budget})

        result = await get_project_detail(
            request=MagicMock(method='GET', url=SimpleNamespace(path=f'/v1/projects/{personal_name}')),
            project_name=personal_name,
            include_spending=True,
            spending_rows_limit=50,
            user=regular_user,
        )

        assert result.project_type == 'personal'
        assert result.spending is not None
        assert result.spending.current_spending == pytest.approx(3.50)
        assert result.spending.cumulative_spend == pytest.approx(12.75)
        assert result.spending.budget_limit == pytest.approx(50.0)

    @patch(
        "codemie.rest_api.routers.projects.project_budget_assignment_repository."
        "get_assigned_budget_summaries_for_projects",
        new_callable=AsyncMock,
    )
    @patch('codemie.rest_api.routers.projects.config')
    @patch('codemie.rest_api.routers.projects.budget_repository')
    @patch('codemie.rest_api.routers.projects.get_async_session')
    @patch('codemie.rest_api.routers.projects.get_session')
    @patch('codemie.rest_api.routers.projects.project_visibility_service')
    @patch('codemie.rest_api.routers.projects._spend_repo')
    @pytest.mark.anyio
    async def test_personal_project_detail_returns_spending_widget_with_budget_rows(
        self,
        mock_spend_repo,
        mock_visibility_service,
        mock_get_session,
        mock_async_session,
        mock_budget_repository,
        mock_config,
        mock_assigned_budgets,
        regular_user,
    ):
        """spending_widget includes a row per budget_id for personal project."""
        mock_assigned_budgets.return_value = {}
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        personal_name = regular_user.id
        mock_visibility_service.get_visible_project_with_members.return_value = _make_personal_project_detail(
            owner_id=personal_name
        )

        rows = [
            _make_budget_spend_row(
                personal_name, budget_id='default', budget_period_spend=3.50, cumulative_spend=12.75
            ),
            _make_budget_spend_row(
                personal_name, budget_id='premium', budget_period_spend=1.20, cumulative_spend=4.80, max_budget=20.0
            ),
        ]
        mock_async_session.return_value = AsyncMock()
        mock_spend_repo.get_latest_key_spending_for_project = AsyncMock(return_value=None)
        mock_spend_repo.get_latest_budget_rows_for_project = AsyncMock(
            side_effect=lambda session, name, rows_limit=50, spend_subject_type='budget': (
                rows if spend_subject_type == 'budget' else []
            )
        )
        mock_spend_repo.get_lifetime_spend = AsyncMock(return_value=17.55)
        mock_default_budget = MagicMock(max_budget=50.0, budget_reset_at='2026-09-01T00:00:00Z', deleted_at=None)
        mock_premium_budget = MagicMock(max_budget=20.0, budget_reset_at='2026-08-15T00:00:00Z', deleted_at=None)
        mock_budget_repository.get_all_keyed_by_id = AsyncMock(
            return_value={'default': mock_default_budget, 'premium': mock_premium_budget}
        )

        result = await get_project_detail(
            request=MagicMock(method='GET', url=SimpleNamespace(path=f'/v1/projects/{personal_name}')),
            project_name=personal_name,
            include_spending=True,
            spending_rows_limit=50,
            user=regular_user,
        )

        assert result.spending_widget is not None
        widget_rows = result.spending_widget.data.rows
        assert len(widget_rows) == 2
        budget_ids = {r.budget_id for r in widget_rows}
        assert budget_ids == {'default', 'premium'}
        total_period = sum(r.current_spending for r in widget_rows)
        assert total_period == pytest.approx(4.70)

        assert result.spending.budget_limit == pytest.approx(70.0)

    @patch('codemie.rest_api.routers.projects.config')
    @patch('codemie.rest_api.routers.projects.get_session')
    @patch('codemie.rest_api.routers.projects.project_visibility_service')
    @pytest.mark.anyio
    async def test_personal_project_detail_no_spending_when_include_spending_false(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_config,
        regular_user,
    ):
        """When include_spending=False, spending and spending_widget remain None."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        personal_name = regular_user.id
        mock_visibility_service.get_visible_project_with_members.return_value = _make_personal_project_detail(
            owner_id=personal_name
        )

        result = await get_project_detail(
            request=MagicMock(method='GET', url=SimpleNamespace(path=f'/v1/projects/{personal_name}')),
            project_name=personal_name,
            include_spending=False,
            spending_rows_limit=50,
            user=regular_user,
        )

        assert result.spending is None
        assert result.spending_widget is None


class TestGetProjectSpends:
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.projects._spend_repo")
    @pytest.mark.anyio
    async def test_returns_paginated_spends_for_project_admin(
        self,
        mock_spend_repo,
        mock_app_repo,
        mock_get_session,
        mock_get_async_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        project_admin = User(
            id="user-1",
            username="user1",
            email="user1@example.com",
            is_admin=False,
            admin_project_names=["proj-a"],
            project_names=["proj-a"],
        )
        mock_project = MagicMock()
        mock_project.deleted_at = None
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project
        mock_get_async_session.return_value = _mock_session_ctx(AsyncMock())
        spend_row = SimpleNamespace(
            spend_date=datetime(2026, 1, 1, tzinfo=UTC),
            daily_spend=5.5,
            cumulative_spend=10.0,
            budget_period_spend=5.5,
            budget_id="budget-1",
        )
        mock_spend_repo.get_spend_for_period = AsyncMock(return_value=(5.5, 1, [spend_row]))

        result = await get_project_spends(
            project_name="proj-a",
            period_from=date_type(2026, 1, 1),
            period_to=date_type(2026, 1, 31),
            page=0,
            per_page=20,
            user=project_admin,
        )

        assert isinstance(result, PaginatedSpendsResponse)
        assert result.total_spend == 5.5
        assert len(result.rows) == 1
        assert result.rows[0].daily_spend == 5.5
        assert result.rows[0].budget_id == "budget-1"
        assert result.pagination.total == 1
        assert result.pagination.page == 0
        assert result.pagination.per_page == 20

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.projects._spend_repo")
    @patch("codemie.rest_api.security.user.config")
    @pytest.mark.anyio
    async def test_returns_paginated_spends_for_global_admin(
        self,
        mock_user_config,
        mock_spend_repo,
        mock_app_repo,
        mock_get_session,
        mock_get_async_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_user_config.ENV = "production"
        mock_user_config.ENABLE_USER_MANAGEMENT = True
        admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
        mock_project = MagicMock()
        mock_project.deleted_at = None
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project
        mock_get_async_session.return_value = _mock_session_ctx(AsyncMock())
        mock_spend_repo.get_spend_for_period = AsyncMock(return_value=(0.0, 0, []))

        result = await get_project_spends(
            project_name="proj-a",
            period_from=date_type(2026, 1, 1),
            period_to=date_type(2026, 1, 31),
            page=0,
            per_page=20,
            user=admin,
        )

        assert isinstance(result, PaginatedSpendsResponse)
        assert result.total_spend == 0.0
        assert result.rows == []
        assert result.pagination.total == 0

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.projects._spend_repo")
    @pytest.mark.anyio
    async def test_returns_paginated_spends_for_global_maintainer(
        self,
        mock_spend_repo,
        mock_app_repo,
        mock_get_session,
        mock_get_async_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        maintainer = User(
            id="maintainer-1",
            username="maintainer",
            email="maintainer@example.com",
            is_admin=True,
            is_maintainer=True,
        )
        mock_project = MagicMock()
        mock_project.deleted_at = None
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project
        mock_get_async_session.return_value = _mock_session_ctx(AsyncMock())
        mock_spend_repo.get_spend_for_period = AsyncMock(return_value=(7.5, 1, []))

        result = await get_project_spends(
            project_name="proj-a",
            period_from=date_type(2026, 1, 1),
            period_to=date_type(2026, 1, 31),
            page=0,
            per_page=20,
            user=maintainer,
        )

        assert isinstance(result, PaginatedSpendsResponse)
        assert result.total_spend == 7.5

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @pytest.mark.anyio
    async def test_returns_404_when_project_not_found(
        self,
        mock_app_repo,
        mock_get_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_project_spends(
                project_name="nonexistent",
                period_from=date_type(2026, 1, 1),
                period_to=date_type(2026, 1, 31),
                page=0,
                per_page=20,
                user=admin,
            )

        assert exc_info.value.code == 404

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @pytest.mark.anyio
    async def test_returns_404_when_project_is_soft_deleted(
        self,
        mock_app_repo,
        mock_get_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
        mock_project = MagicMock()
        mock_project.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_project_spends(
                project_name="deleted-proj",
                period_from=date_type(2026, 1, 1),
                period_to=date_type(2026, 1, 31),
                page=0,
                per_page=20,
                user=admin,
            )

        assert exc_info.value.code == 404

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.security.user.config")
    @pytest.mark.anyio
    async def test_returns_404_when_user_is_not_project_admin(
        self,
        mock_user_config,
        mock_app_repo,
        mock_get_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_user_config.ENV = "production"
        mock_user_config.ENABLE_USER_MANAGEMENT = True
        non_admin = User(id="user-2", username="user2", email="user2@example.com", is_admin=False)
        mock_project = MagicMock()
        mock_project.deleted_at = None
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_project_spends(
                project_name="proj-a",
                period_from=date_type(2026, 1, 1),
                period_to=date_type(2026, 1, 31),
                page=0,
                per_page=20,
                user=non_admin,
            )

        assert exc_info.value.code == 404

    @patch("codemie.rest_api.routers.projects.config")
    @pytest.mark.anyio
    async def test_returns_400_when_period_from_after_period_to(self, mock_config):
        mock_config.ENABLE_USER_MANAGEMENT = True
        admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_project_spends(
                project_name="proj-a",
                period_from=date_type(2026, 2, 1),
                period_to=date_type(2026, 1, 1),
                page=0,
                per_page=20,
                user=admin,
            )

        assert exc_info.value.code == 400

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.projects._spend_repo")
    @pytest.mark.anyio
    async def test_returns_spends_for_personal_project_owner(
        self,
        mock_spend_repo,
        mock_app_repo,
        mock_get_session,
        mock_get_async_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        personal_project_owner = User(
            id="user-1",
            username="user1",
            email="user1@example.com",
            is_admin=False,
            admin_project_names=[],
            project_names=["my-personal-proj"],
        )
        mock_project = MagicMock()
        mock_project.deleted_at = None
        mock_project.project_type = Application.ProjectType.PERSONAL
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project
        mock_get_async_session.return_value = _mock_session_ctx(AsyncMock())
        mock_spend_repo.get_spend_for_period = AsyncMock(return_value=(12.5, 2, []))

        result = await get_project_spends(
            project_name="my-personal-proj",
            period_from=date_type(2026, 1, 1),
            period_to=date_type(2026, 1, 31),
            page=0,
            per_page=20,
            user=personal_project_owner,
        )

        assert isinstance(result, PaginatedSpendsResponse)
        assert result.total_spend == 12.5

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.security.user.config")
    @pytest.mark.anyio
    async def test_returns_404_for_non_member_of_personal_project(
        self,
        mock_user_config,
        mock_app_repo,
        mock_get_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_user_config.ENV = "production"
        mock_user_config.ENABLE_USER_MANAGEMENT = True
        non_member = User(
            id="user-2",
            username="user2",
            email="user2@example.com",
            is_admin=False,
            admin_project_names=[],
            project_names=[],
        )
        mock_project = MagicMock()
        mock_project.deleted_at = None
        mock_project.project_type = Application.ProjectType.PERSONAL
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_project_spends(
                project_name="someone-elses-personal-proj",
                period_from=date_type(2026, 1, 1),
                period_to=date_type(2026, 1, 31),
                page=0,
                per_page=20,
                user=non_member,
            )

        assert exc_info.value.code == 404

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.projects._spend_repo")
    @patch("codemie.rest_api.security.user.config")
    @pytest.mark.anyio
    async def test_budget_category_filter_forwarded_to_repo(
        self,
        mock_user_config,
        mock_spend_repo,
        mock_app_repo,
        mock_get_session,
        mock_get_async_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_user_config.ENV = "production"
        mock_user_config.ENABLE_USER_MANAGEMENT = True
        admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
        mock_project = MagicMock()
        mock_project.deleted_at = None
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project
        mock_get_async_session.return_value = _mock_session_ctx(AsyncMock())
        mock_spend_repo.get_spend_for_period = AsyncMock(return_value=(0.0, 0, []))

        await get_project_spends(
            project_name="proj-a",
            period_from=date_type(2026, 1, 1),
            period_to=date_type(2026, 1, 31),
            page=0,
            per_page=20,
            budget_category=BudgetCategory.CLI,
            spend_subject_type=None,
            user=admin,
        )

        assert mock_spend_repo.get_spend_for_period.call_args.kwargs["budget_category"] == BudgetCategory.CLI
        assert mock_spend_repo.get_spend_for_period.call_args.kwargs["spend_subject_type"] is None

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.projects._spend_repo")
    @patch("codemie.rest_api.security.user.config")
    @pytest.mark.anyio
    async def test_spend_subject_type_filter_forwarded_to_repo(
        self,
        mock_user_config,
        mock_spend_repo,
        mock_app_repo,
        mock_get_session,
        mock_get_async_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_user_config.ENV = "production"
        mock_user_config.ENABLE_USER_MANAGEMENT = True
        admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
        mock_project = MagicMock()
        mock_project.deleted_at = None
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project
        mock_get_async_session.return_value = _mock_session_ctx(AsyncMock())
        mock_spend_repo.get_spend_for_period = AsyncMock(return_value=(0.0, 0, []))

        await get_project_spends(
            project_name="proj-a",
            period_from=date_type(2026, 1, 1),
            period_to=date_type(2026, 1, 31),
            page=0,
            per_page=20,
            budget_category=None,
            spend_subject_type="member_budget",
            user=admin,
        )

        assert mock_spend_repo.get_spend_for_period.call_args.kwargs["spend_subject_type"] == "member_budget"
        assert mock_spend_repo.get_spend_for_period.call_args.kwargs["budget_category"] is None

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.projects._spend_repo")
    @patch("codemie.rest_api.security.user.config")
    @pytest.mark.anyio
    async def test_both_filters_forwarded_to_repo(
        self,
        mock_user_config,
        mock_spend_repo,
        mock_app_repo,
        mock_get_session,
        mock_get_async_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_user_config.ENV = "production"
        mock_user_config.ENABLE_USER_MANAGEMENT = True
        admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
        mock_project = MagicMock()
        mock_project.deleted_at = None
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project
        mock_get_async_session.return_value = _mock_session_ctx(AsyncMock())
        mock_spend_repo.get_spend_for_period = AsyncMock(return_value=(0.0, 0, []))

        await get_project_spends(
            project_name="proj-a",
            period_from=date_type(2026, 1, 1),
            period_to=date_type(2026, 1, 31),
            page=0,
            per_page=20,
            budget_category=BudgetCategory.PLATFORM,
            spend_subject_type="budget",
            user=admin,
        )

        assert mock_spend_repo.get_spend_for_period.call_args.kwargs["budget_category"] == BudgetCategory.PLATFORM
        assert mock_spend_repo.get_spend_for_period.call_args.kwargs["spend_subject_type"] == "budget"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.get_async_session")
    @patch("codemie.rest_api.routers.projects.get_session")
    @patch("codemie.rest_api.routers.projects.application_repository")
    @patch("codemie.rest_api.routers.projects._spend_repo")
    @patch("codemie.rest_api.security.user.config")
    @pytest.mark.anyio
    async def test_no_filters_passes_none_to_repo(
        self,
        mock_user_config,
        mock_spend_repo,
        mock_app_repo,
        mock_get_session,
        mock_get_async_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_user_config.ENV = "production"
        mock_user_config.ENABLE_USER_MANAGEMENT = True
        admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
        mock_project = MagicMock()
        mock_project.deleted_at = None
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_app_repo.get_by_name.return_value = mock_project
        mock_get_async_session.return_value = _mock_session_ctx(AsyncMock())
        mock_spend_repo.get_spend_for_period = AsyncMock(return_value=(0.0, 0, []))

        await get_project_spends(
            project_name="proj-a",
            period_from=date_type(2026, 1, 1),
            period_to=date_type(2026, 1, 31),
            page=0,
            per_page=20,
            budget_category=None,
            spend_subject_type=None,
            user=admin,
        )

        assert mock_spend_repo.get_spend_for_period.call_args.kwargs["budget_category"] is None
        assert mock_spend_repo.get_spend_for_period.call_args.kwargs["spend_subject_type"] is None


class TestProjectDisplayNameEndpoints:
    """Tests for display_name field in create and update project endpoints."""

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_create_project_with_display_name_passes_to_service(
        self,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        """display_name in create payload is forwarded to project_service."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None
        mock_project_service.create_shared_project.return_value = SimpleNamespace(
            name="my-team",
            display_name="My Team",
            description="desc",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 1, 1, tzinfo=UTC),
            chargeback_enabled=False,
            chargeback_attribution="project",
        )

        response = create_project(
            payload=ProjectCreateRequest(name="my-team", description="desc", display_name="My Team"),
            user=regular_user,
        )

        assert response.display_name == "My Team"
        mock_project_service.create_shared_project.assert_called_once_with(
            user=regular_user,
            project_name="my-team",
            description="desc",
            display_name="My Team",
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_create_project_without_display_name_omits_kwarg(
        self,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        """Omitting display_name does not pass the kwarg to project_service."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None
        mock_project_service.create_shared_project.return_value = SimpleNamespace(
            name="my-team",
            display_name=None,
            description="desc",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 1, 1, tzinfo=UTC),
            chargeback_enabled=False,
            chargeback_attribution="project",
        )

        create_project(
            payload=ProjectCreateRequest(name="my-team", description="desc"),
            user=regular_user,
        )

        call_kwargs = mock_project_service.create_shared_project.call_args.kwargs
        assert "display_name" not in call_kwargs

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_project_with_display_name_passes_to_service(
        self,
        mock_project_service,
        mock_config,
    ):
        """display_name in update payload is forwarded to project_service."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = "My Project"
        updated_app.description = "desc"
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2026, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "project"
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        response = update_project(
            payload=ProjectUpdateRequest(display_name="My Project"),
            project_name="my-project",
            user=user,
        )

        assert response.display_name == "My Project"
        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            display_name="My Project",
            clear_display_name=False,
            clear_description=False,
            description=None,
            cost_center_id=None,
            clear_cost_center=False,
            enforce_member_spend_limits=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_project_display_name_none_passes_through(
        self,
        mock_project_service,
        mock_config,
    ):
        """display_name=None (omitted) passes None + clear_display_name=False to
        project_service, which preserves the existing value (EPMCDME-13486)."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = None
        updated_app.description = "desc"
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2026, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "project"
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        update_project(
            payload=ProjectUpdateRequest(description="desc"),
            project_name="my-project",
            user=user,
        )

        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            display_name=None,
            clear_display_name=False,
            description="desc",
            clear_description=False,
            cost_center_id=None,
            clear_cost_center=False,
            enforce_member_spend_limits=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_clear_display_name_passes_true_to_service(
        self,
        mock_project_service,
        mock_config,
    ):
        """PATCH with clear_display_name=True passes clear_display_name=True (EPMCDME-13486)."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = None
        updated_app.description = "desc"
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2026, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "project"
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        update_project(
            payload=ProjectUpdateRequest(clear_display_name=True),
            project_name="my-project",
            user=user,
        )

        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            display_name=None,
            clear_display_name=True,
            clear_description=False,
            description=None,
            cost_center_id=None,
            clear_cost_center=False,
            enforce_member_spend_limits=None,
            chargeback_enabled=None,
            chargeback_attribution=None,
        )


class TestChargebackEnabledField:
    """Tests for chargeback_enabled propagation across API models and handlers."""

    def test_project_list_item_has_chargeback_enabled_field(self):
        item = ProjectListItem(
            name="proj",
            project_type="shared",
            user_count=0,
            admin_count=0,
        )
        assert item.chargeback_enabled is False

    def test_project_create_response_has_chargeback_enabled_field(self):
        from datetime import datetime, UTC

        resp = ProjectCreateResponse(
            name="proj",
            description="desc",
            project_type="shared",
            created_by="user-1",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert resp.chargeback_enabled is False

    def test_project_detail_response_has_chargeback_enabled_field(self):
        resp = ProjectDetailResponse(
            name="proj",
            project_type="shared",
            user_count=0,
            admin_count=0,
            members=[],
        )
        assert resp.chargeback_enabled is False

    def test_update_request_accepts_chargeback_enabled_alone(self):
        payload = ProjectUpdateRequest(chargeback_enabled=True)
        assert payload.chargeback_enabled is True

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_project_passes_chargeback_enabled_to_service(self, mock_project_service, mock_config):
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = None
        updated_app.description = ""
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2026, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "project"
        updated_app.chargeback_enabled = True
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        update_project(
            payload=ProjectUpdateRequest(chargeback_enabled=True),
            project_name="my-project",
            user=user,
        )

        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            display_name=None,
            clear_display_name=False,
            clear_description=False,
            description=None,
            cost_center_id=None,
            clear_cost_center=False,
            enforce_member_spend_limits=None,
            chargeback_enabled=True,
            chargeback_attribution=None,
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_project_response_includes_chargeback_enabled(self, mock_project_service, mock_config):
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = None
        updated_app.description = "desc"
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2026, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "project"
        updated_app.chargeback_enabled = True
        mock_project_service.update_project.return_value = updated_app

        response = update_project(
            payload=ProjectUpdateRequest(chargeback_enabled=True),
            project_name="my-project",
            user=MagicMock(id="user-1"),
        )

        assert response.chargeback_enabled is True


class TestChargebackAttributionField:
    """Tests for chargeback_attribution propagation across API models and handlers."""

    def test_project_list_item_has_chargeback_attribution_field(self):
        item = ProjectListItem(
            name="proj",
            project_type="shared",
            user_count=0,
            admin_count=0,
        )
        assert item.chargeback_attribution == "project"

    def test_project_create_response_has_chargeback_attribution_field(self):
        from datetime import datetime, UTC

        resp = ProjectCreateResponse(
            name="proj",
            description="desc",
            project_type="shared",
            created_by="user-1",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert resp.chargeback_attribution == "project"

    def test_project_detail_response_has_chargeback_attribution_field(self):
        resp = ProjectDetailResponse(
            name="proj",
            project_type="shared",
            user_count=0,
            admin_count=0,
            members=[],
        )
        assert resp.chargeback_attribution == "project"

    def test_update_request_accepts_chargeback_attribution_alone(self):
        payload = ProjectUpdateRequest(chargeback_attribution="cost_center")
        assert payload.chargeback_attribution == "cost_center"

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_project_passes_chargeback_attribution_to_service(self, mock_project_service, mock_config):
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = None
        updated_app.description = ""
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2026, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "cost_center"
        mock_project_service.update_project.return_value = updated_app

        user = MagicMock(id="user-1")
        update_project(
            payload=ProjectUpdateRequest(chargeback_attribution="cost_center"),
            project_name="my-project",
            user=user,
        )

        mock_project_service.update_project.assert_called_once_with(
            user=user,
            project_name="my-project",
            name=None,
            display_name=None,
            clear_display_name=False,
            description=None,
            clear_description=False,
            cost_center_id=None,
            clear_cost_center=False,
            enforce_member_spend_limits=None,
            chargeback_enabled=None,
            chargeback_attribution="cost_center",
        )

    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    def test_update_project_response_includes_chargeback_attribution(self, mock_project_service, mock_config):
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_app = MagicMock()
        updated_app.name = "my-project"
        updated_app.display_name = None
        updated_app.description = "desc"
        updated_app.project_type = "shared"
        updated_app.created_by = "user-1"
        updated_app.date = datetime(2026, 1, 1, tzinfo=UTC)
        updated_app.cost_center_id = None
        updated_app.chargeback_attribution = "cost_center"
        mock_project_service.update_project.return_value = updated_app

        response = update_project(
            payload=ProjectUpdateRequest(chargeback_attribution="cost_center"),
            project_name="my-project",
            user=MagicMock(id="user-1"),
        )

        assert response.chargeback_attribution == "cost_center"

    def test_build_project_detail_response_includes_chargeback_attribution(self):
        response = _build_project_detail_response(
            {
                "name": "proj",
                "description": "desc",
                "project_type": "shared",
                "created_by": "user-1",
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "user_count": 0,
                "admin_count": 0,
                "members": [],
                "chargeback_attribution": "cost_center",
            },
            "proj",
        )

        assert response.chargeback_attribution == "cost_center"


class TestProjectDescriptionOptional:
    """Tests for optional description (EPMCDME-14336)."""

    def _project(self, description=None):
        return SimpleNamespace(
            name="proj",
            display_name=None,
            description=description,
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 1, 1, tzinfo=UTC),
            chargeback_enabled=False,
            chargeback_attribution="project",
        )

    @patch("codemie.rest_api.routers.projects.SettingsService")
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_create_project_without_description_returns_201_null(
        self, mock_cc, mock_svc, mock_config, mock_settings, regular_user
    ):
        """POST /projects without description returns 201 with description=None."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_cc.return_value = None
        mock_settings.get_enforce_member_spend_limits.return_value = False
        mock_svc.create_shared_project.return_value = self._project(description=None)

        response = create_project(
            payload=ProjectCreateRequest(name="proj"),
            user=regular_user,
        )

        assert response.description is None

    @patch("codemie.rest_api.routers.projects.SettingsService")
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_project_response_null_description_not_coerced_to_empty(
        self, mock_cc, mock_svc, mock_config, mock_settings, super_admin_user
    ):
        """PATCH response with description=None must not be coerced to ''."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_cc.return_value = None
        mock_settings.get_enforce_member_spend_limits.return_value = False
        mock_svc.update_project.return_value = self._project(description=None)

        response = update_project(
            payload=ProjectUpdateRequest(clear_description=True),
            project_name="proj",
            user=super_admin_user,
        )

        assert response.description is None

    def test_update_project_description_and_clear_mutually_exclusive(self):
        """PATCH with description and clear_description=True must raise 422."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProjectUpdateRequest(description="text", clear_description=True)

    def test_update_project_clear_description_alone_passes_non_empty_validator(self):
        """clear_description=True alone must pass the validate_non_empty check."""
        req = ProjectUpdateRequest(clear_description=True)
        assert req.clear_description is True

    @patch("codemie.rest_api.routers.projects.SettingsService")
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_update_project_clear_description_returns_null(
        self, mock_cc, mock_svc, mock_config, mock_settings, super_admin_user
    ):
        """PATCH with clear_description=True calls service with clear_description=True."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_cc.return_value = None
        mock_settings.get_enforce_member_spend_limits.return_value = False
        mock_svc.update_project.return_value = self._project(description=None)

        response = update_project(
            payload=ProjectUpdateRequest(clear_description=True),
            project_name="proj",
            user=super_admin_user,
        )

        call_kwargs = mock_svc.update_project.call_args.kwargs
        assert call_kwargs.get("clear_description") is True
        assert response.description is None

    def test_missing_name_rejected_when_description_absent(self):
        """POST with no name and no description must reject with name validation error, not description."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            ProjectCreateRequest()

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("name",) for e in errors), "Expected name field error"
        assert not any("description" in str(e["loc"]) for e in errors), "Description error must not appear"
