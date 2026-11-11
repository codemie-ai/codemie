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
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.project import (
    ProjectAssignmentRequest,
    ProjectAssignmentUpdateRequest,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectDetailResponse,
    ProjectListItem,
    ProjectOwnershipUpdateRequest,
    ProjectUpdateRequest,
    PaginatedProjectListResponse,
    ProjectAssignedBudgetSummary,
    ProjectSpendingDetail,
    ProjectSpendingSummary,
    SpendingWidgetRow,
)
from codemie.rest_api.routers.projects import router as projects_router
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.routers.projects._common import _authorize_project_access, _raise_project_not_found
from codemie.service.project.project_spending_service import (
    attach_project_detail_spending,
    attach_project_spending_summaries,
)
from codemie.rest_api.routers.projects.management import delete_project, update_project, update_project_ownership
from codemie.rest_api.routers.projects.membership import (
    assign_user_to_project,
    remove_user_from_project,
    update_user_project_assignment,
)
from codemie.rest_api.routers.projects.crud import (
    _attach_assigned_budget_summaries,
    _build_project_detail_response,
    create_project,
    get_project_detail,
    list_projects,
)
from codemie.configs import config
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import ProjectHolder


@pytest.fixture(autouse=True)
def _no_budget_reset_refresh():
    """The reset reconciliation is a provider call; it moves nothing unless a test patches it itself."""
    with patch(
        "codemie.service.project.project_spending_service.daily_budget_reset_reconciliation_service.refresh_overdue",
        new_callable=AsyncMock,
        return_value=set(),
    ):
        yield


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
    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.project_service")
    @patch("codemie.rest_api.routers.projects.crud._resolve_cost_center_name")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.project_service")
    @patch("codemie.rest_api.routers.projects.crud._resolve_cost_center_name")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.project_service")
    @patch("codemie.rest_api.routers.projects.crud._resolve_cost_center_name")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.project_service")
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
    def test_list_projects_parses_repeated_cost_center_ids_and_rejects_invalid_uuid(self):
        selected = [
            UUID("11111111-1111-4111-8111-111111111111"),
            UUID("22222222-2222-4222-8222-222222222222"),
        ]
        app = FastAPI()
        app.include_router(projects_router)
        app.dependency_overrides[authenticate] = lambda: User(
            id="api-user", username="test", email="test@example.com", is_admin=False
        )
        with (
            patch("codemie.rest_api.routers.projects.crud._ensure_user_management_enabled"),
            patch("codemie.rest_api.routers.projects.crud._list_projects_sync", return_value=([], 0)) as list_sync,
        ):
            client = TestClient(app)
            response = client.get(
                "/v1/projects",
                params=[("cost_center_ids", str(value)) for value in selected],
            )
            assert response.status_code == 200
            assert response.json()["pagination"]["total"] == 0
            assert client.get("/v1/projects?cost_center_ids=bad").status_code == 422

        assert list_sync.call_args.kwargs["cost_center_ids"] == selected

    def test_list_projects_treats_empty_cost_center_ids_as_no_filter(self):
        app = FastAPI()
        app.include_router(projects_router)
        app.dependency_overrides[authenticate] = lambda: User(
            id="api-user", username="test", email="test@example.com", is_admin=False
        )
        with (
            patch("codemie.rest_api.routers.projects.crud._ensure_user_management_enabled"),
            patch("codemie.rest_api.routers.projects.crud._list_projects_sync", return_value=([], 0)) as list_sync,
        ):
            client = TestClient(app)
            response = client.get("/v1/projects?cost_center_ids=")
            assert response.status_code == 200

        assert list_sync.call_args.kwargs["cost_center_ids"] is None

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
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
            cost_center_ids=None,
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
            cost_center_ids=None,
        )

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
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
        "codemie.service.project.project_spending_service.project_budget_assignment_repository."
        "get_assigned_budget_summaries_for_projects",
        new_callable=AsyncMock,
    )
    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @patch("codemie.rest_api.routers.projects.crud._get_project_detail_sync")
    @pytest.mark.anyio
    async def test_get_project_detail_fetches_spending_for_project_admin(
        self,
        mock_get_project_detail_sync,
        mock_get_async_session,
        mock_project_budget_repo,
        mock_spend_service,
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
        mock_project_budget_repo.get_removed_budget_summaries_for_projects = AsyncMock(return_value={})
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects = AsyncMock(return_value={})
        mock_spend_service.consumed_for_holders = AsyncMock(return_value={})
        mock_get_async_session.side_effect = lambda: _mock_session_ctx(AsyncMock())
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
            spending_rows_limit=50,
            user=project_admin_user,
        )

        assert isinstance(result, ProjectDetailResponse)
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects.assert_awaited_once()

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.SettingsService.get_enforce_member_spend_limits")
    @patch("codemie.rest_api.routers.projects.crud._get_project_detail_sync")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
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
            cost_center_ids=None,
            user=regular_user,
        )

        assert result.pagination.page == 0
        assert result.pagination.per_page == 10
        assert result.pagination.total == 100
        assert len(result.data) == 10

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
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
            cost_center_ids=None,
            user=regular_user,
        )

        assert isinstance(result, PaginatedProjectListResponse)
        assert len(result.data) == 0
        assert result.pagination.total == 0
        assert result.pagination.page == 0

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
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
            cost_center_ids=None,
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
            cost_center_ids=None,
        )

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.config")
    @patch("codemie.rest_api.routers.projects.crud.get_active_provider")
    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
    @pytest.mark.anyio
    async def test_list_projects_attaches_assigned_budgets_and_skips_invalid_categories(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        mock_get_active_provider,
        mock_config,
        mock_common_config,
        regular_user,
    ):
        mock_common_config.ENABLE_USER_MANAGEMENT = True
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
        mock_project_budget_repo.get_removed_budget_summaries_for_projects = AsyncMock(return_value={})
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects = AsyncMock(
            return_value={
                "shared-proj": [
                    SimpleNamespace(
                        project_name="shared-proj",
                        budget_id="budget-1",
                        name="CLI Budget",
                        budget_category="cli",
                        soft_budget=10.0,
                        max_budget=20.0,
                        budget_duration="30d",
                        budget_reset_at="2099-04-23T10:10:00Z",
                        provider_sync_status="ok",
                        member_count=2,
                        allocated_member_budget_total=20.0,
                        budget_created_at=None,
                        budget_spend_reset_at=None,
                    ),
                    SimpleNamespace(
                        project_name="shared-proj",
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
                        budget_created_at=None,
                        budget_spend_reset_at=None,
                    ),
                ]
            }
        )
        mock_spend_service.consumed_for_holders = AsyncMock(
            return_value={
                ProjectHolder(project="shared-proj", category=BudgetCategory.CLI): SpendResult(figure=Decimal("5.0")),
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
            cost_center_ids=None,
        )

        assert len(result.data[0].budgets) == 1
        assert result.data[0].budgets[0].budget_id == "budget-1"
        assert result.data[0].budgets[0].budget_category == BudgetCategory.CLI
        assert result.data[0].budgets[0].current_spending == pytest.approx(5.0)

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
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


#: Inside the derived 2099-03-24T10:10Z..2099-04-23T10:10Z period, and before it, for the rows below.
_CREATED_MID_PERIOD = datetime(2099, 4, 2, 12, 2, 14, tzinfo=UTC)
_CREATED_BEFORE_PERIOD = datetime(2099, 1, 1, tzinfo=UTC)
#: A period-scoped project portion the ledger sizes (EPMCDME-15237): a budget figure must not show it.
_PROJECT_HOLE = Portion(
    spend=Decimal("133.97"),
    rows=12,
    sub_range=TimeRange(datetime(2099, 3, 24, 10, 10, tzinfo=UTC), datetime(2099, 4, 2, tzinfo=UTC)),
)


class TestAssignedBudgetSpendFigures:
    """The project-list budget summaries report exactly what the spend port says was consumed."""

    #: 2099-04-23T10:10:00Z minus the 30d duration - the period budget_period_range derives.
    PERIOD = TimeRange(datetime(2099, 3, 24, 10, 10, tzinfo=UTC), datetime(2099, 4, 23, 10, 10, tzinfo=UTC))
    HOLDER = ProjectHolder(project="proj-a", category=BudgetCategory.CLI)

    @staticmethod
    def _budget_row(**overrides) -> SimpleNamespace:
        fields = {
            "project_name": "proj-a",
            "budget_id": "budget-1",
            "name": "CLI Budget",
            "budget_category": "cli",
            "soft_budget": 10.0,
            "max_budget": 20.0,
            "budget_duration": "30d",
            "budget_reset_at": "2099-04-23T10:10:00Z",
            "provider_sync_status": "ok",
            "member_count": 0,
            "allocated_member_budget_total": 0.0,
            "budget_created_at": None,
            "budget_spend_reset_at": None,
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    @staticmethod
    def _item() -> ProjectListItem:
        return ProjectListItem(name="proj-a", project_type="shared", user_count=1, admin_count=1)

    @staticmethod
    def _stub_repository(mock_project_budget_repo, mock_async_session, row) -> None:
        mock_async_session.return_value = AsyncMock()
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects = AsyncMock(return_value={"proj-a": [row]})
        mock_project_budget_repo.get_removed_budget_summaries_for_projects = AsyncMock(return_value={})

    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_absent_figure_renders_as_no_spending(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
    ):
        """An absent figure must not become 0.0 - unknown consumption is not zero consumption."""
        self._stub_repository(mock_project_budget_repo, mock_async_session, self._budget_row())
        mock_spend_service.consumed_for_holders = AsyncMock(
            return_value={self.HOLDER: SpendResult.no_figure(self.PERIOD)}
        )
        items = [self._item()]

        await _attach_assigned_budget_summaries(items, budget_category=None)

        assert items[0].budgets[0].current_spending is None
        # The blank must say why it is blank: a missing number alone reads as "nothing was spent".
        assert items[0].budgets[0].spend_unavailable is True

    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_measured_zero_renders_as_zero(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
    ):
        """A measured zero is a real figure and must render as 0.0, not as an absent one."""
        self._stub_repository(mock_project_budget_repo, mock_async_session, self._budget_row())
        mock_spend_service.consumed_for_holders = AsyncMock(
            return_value={self.HOLDER: SpendResult(figure=Decimal("0"))}
        )
        items = [self._item()]

        await _attach_assigned_budget_summaries(items, budget_category=None)

        assert items[0].budgets[0].current_spending == 0.0
        mock_spend_service.consumed_for_holders.assert_awaited_once_with([self.HOLDER], self.PERIOD)

    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_budget_without_derivable_period_is_never_queried(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
    ):
        """No reset instant means no budget period, so there is no figure to ask the port for."""
        self._stub_repository(mock_project_budget_repo, mock_async_session, self._budget_row(budget_reset_at=None))
        mock_spend_service.consumed_for_holders = AsyncMock()
        items = [self._item()]

        await _attach_assigned_budget_summaries(items, budget_category=None)

        assert items[0].budgets[0].current_spending is None
        # Never asked is the same fact to a reader as no figure available, and is reported as such.
        assert items[0].budgets[0].spend_unavailable is True
        mock_spend_service.consumed_for_holders.assert_not_awaited()

    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_exact_figure_carries_no_qualification(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
    ):
        """The one state safe to render bare: a figure over the whole range, complete in every dimension."""
        self._stub_repository(mock_project_budget_repo, mock_async_session, self._budget_row())
        mock_spend_service.consumed_for_holders = AsyncMock(
            return_value={self.HOLDER: SpendResult(figure=Decimal("7.25"))}
        )
        items = [self._item()]

        await _attach_assigned_budget_summaries(items, budget_category=None)

        summary = items[0].budgets[0]
        assert summary.current_spending == pytest.approx(7.25)
        assert summary.spend_unavailable is False
        assert summary.spend_partial is False
        assert summary.spend_missing_amount is None

    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_truncated_figure_survives_and_is_flagged(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
    ):
        """A figure covering only part of the range is a subtotal, and must not render as a total."""
        self._stub_repository(mock_project_budget_repo, mock_async_session, self._budget_row())
        mock_spend_service.consumed_for_holders = AsyncMock(
            return_value={
                self.HOLDER: SpendResult(
                    figure=Decimal("5.0"),
                    unavailable=TimeRange(self.PERIOD.start, datetime(2099, 4, 1, tzinfo=UTC)),
                )
            }
        )
        items = [self._item()]

        await _attach_assigned_budget_summaries(items, budget_category=None)

        summary = items[0].budgets[0]
        assert summary.current_spending == pytest.approx(5.0)
        assert summary.spend_unavailable is True

    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_uncomputable_partial_portion_reports_an_unknown_missing_amount(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
    ):
        """An unresolvable alias set has no rows to sum, so the missing amount is unknown - not zero."""
        self._stub_repository(mock_project_budget_repo, mock_async_session, self._budget_row())
        mock_spend_service.consumed_for_holders = AsyncMock(
            return_value={
                self.HOLDER: SpendResult(
                    figure=Decimal("5.0"),
                    partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
                )
            }
        )
        items = [self._item()]

        await _attach_assigned_budget_summaries(items, budget_category=None)

        summary = items[0].budgets[0]
        assert summary.current_spending == pytest.approx(5.0)
        assert summary.spend_partial is True
        assert summary.spend_missing_amount is None
        assert summary.spend_unavailable is False

    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_a_budget_created_mid_period_is_asked_about_from_its_creation(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
    ):
        """The project key counts from the budget's creation, so the period must not start earlier."""
        self._stub_repository(
            mock_project_budget_repo, mock_async_session, self._budget_row(budget_created_at=_CREATED_MID_PERIOD)
        )
        mock_spend_service.consumed_for_holders = AsyncMock(
            return_value={self.HOLDER: SpendResult(figure=Decimal("1"))}
        )

        await _attach_assigned_budget_summaries([self._item()], budget_category=None)

        mock_spend_service.consumed_for_holders.assert_awaited_once_with(
            [self.HOLDER], TimeRange(_CREATED_MID_PERIOD, self.PERIOD.end)
        )

    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_a_budget_created_before_the_period_keeps_the_derived_period(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
    ):
        """The clamp only ever moves the start later: an older budget keeps the derived period."""
        self._stub_repository(
            mock_project_budget_repo, mock_async_session, self._budget_row(budget_created_at=_CREATED_BEFORE_PERIOD)
        )
        mock_spend_service.consumed_for_holders = AsyncMock(
            return_value={self.HOLDER: SpendResult(figure=Decimal("1"))}
        )

        await _attach_assigned_budget_summaries([self._item()], budget_category=None)

        mock_spend_service.consumed_for_holders.assert_awaited_once_with([self.HOLDER], self.PERIOD)

    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_a_project_hole_is_flagged_but_not_sized(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
    ):
        """The hole is sized over the whole project, not this budget, so its amount would mislead here."""
        self._stub_repository(mock_project_budget_repo, mock_async_session, self._budget_row())
        mock_spend_service.consumed_for_holders = AsyncMock(
            return_value={
                self.HOLDER: SpendResult(figure=Decimal("5.0"), partial={SpendDimension.PROJECT: _PROJECT_HOLE})
            }
        )
        items = [self._item()]

        await _attach_assigned_budget_summaries(items, budget_category=None)

        summary = items[0].budgets[0]
        assert summary.current_spending == pytest.approx(5.0)
        assert summary.spend_partial is True
        assert summary.spend_missing_amount is None

    @staticmethod
    def test_annotation_fields_are_additive():
        """Every other client of this model constructs it without the annotation and must keep working."""
        summary = ProjectAssignedBudgetSummary(
            budget_id="budget-1",
            name="CLI Budget",
            budget_category=BudgetCategory.CLI,
            soft_budget=10.0,
            max_budget=20.0,
            budget_duration="30d",
            member_count=0,
            allocated_member_budget_total=0.0,
        )

        assert (summary.spend_unavailable, summary.spend_partial, summary.spend_missing_amount) == (False, False, None)


class TestProjectTotalSpendFigures:
    """A project's total is the sum over its assigned budget categories, on every surface.

    The list summary, the detail summary and the widget derive from one row set and one port answer,
    so each case here asserts both summaries at once: three surfaces deriving one figure from three
    row sets is how they came to disagree, and a test that checks only one cannot see that again.

    There is no API-key term in the total. The traffic the old "key" pot reported now reaches the
    ledger carrying a budget category, so it is already inside the per-category figures.
    """

    #: 2099-04-23T10:10:00Z minus the 30d duration - the period budget_period_range derives.
    PERIOD = TimeRange(datetime(2099, 3, 24, 10, 10, tzinfo=UTC), datetime(2099, 4, 23, 10, 10, tzinfo=UTC))
    CLI_HOLDER = ProjectHolder(project="proj-a", category=BudgetCategory.CLI)
    PLATFORM_HOLDER = ProjectHolder(project="proj-a", category=BudgetCategory.PLATFORM)
    ENRICHED = [{"name": "proj-a", "is_project_admin": True, "project_type": "shared"}]

    @staticmethod
    def _budget_row(**overrides) -> SimpleNamespace:
        fields = {
            "project_name": "proj-a",
            "budget_id": "budget-1",
            "name": "CLI Budget",
            "budget_category": "cli",
            "soft_budget": 10.0,
            "max_budget": 20.0,
            "budget_duration": "30d",
            "budget_reset_at": "2099-04-23T10:10:00Z",
            "provider_sync_status": "ok",
            "member_count": 0,
            "allocated_member_budget_total": 0.0,
            "budget_created_at": None,
            "budget_spend_reset_at": None,
        }
        fields.update(overrides)
        return SimpleNamespace(**fields)

    @classmethod
    def _stub(cls, mock_project_budget_repo, mock_async_session, mock_spend_service, rows, spend_by_holder) -> None:
        mock_async_session.return_value = AsyncMock()
        mock_project_budget_repo.get_removed_budget_summaries_for_projects = AsyncMock(return_value={})
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects = AsyncMock(
            return_value={"proj-a": rows} if rows else {}
        )
        mock_spend_service.consumed_for_holders = AsyncMock(return_value=spend_by_holder)

    @classmethod
    async def _run_both_surfaces(cls, user, *, spending_rows_limit: int = 50) -> tuple:
        """Both project surfaces over the same stubbed rows, so a disagreement fails the test."""
        items = [ProjectListItem(name="proj-a", project_type="shared", user_count=1, admin_count=1)]
        await attach_project_spending_summaries(items, cls.ENRICHED, user)

        response = ProjectDetailResponse(name="proj-a", project_type="shared", user_count=1, admin_count=1, members=[])
        await attach_project_detail_spending(
            response=response, project_name="proj-a", spending_rows_limit=spending_rows_limit
        )
        return items[0].spending, response

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_exact_figure_carries_no_qualification(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """The one state safe to render bare: a figure over the whole period, complete in every dimension."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row()],
            {self.CLI_HOLDER: SpendResult(figure=Decimal("7.25"))},
        )

        summary, response = await self._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending):
            assert figure.current_spending == pytest.approx(7.25)
            assert (figure.spend_unavailable, figure.spend_partial, figure.spend_missing_amount) == (False, False, None)
        assert summary.total_percent == pytest.approx(36.25)
        assert response.spending.total == pytest.approx(36.25)
        assert response.spending.budget_reset_at == datetime(2099, 4, 23, 10, 10, tzinfo=UTC)

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_absent_figure_is_never_a_zero_total(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """No figure for any category means no total - 0.0 would claim nothing was spent."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row()],
            {self.CLI_HOLDER: SpendResult.no_figure(self.PERIOD)},
        )

        summary, response = await self._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending):
            assert figure.current_spending is None
            # The blank must say why it is blank: a missing number alone reads as "nothing was spent".
            assert figure.spend_unavailable is True
        # A percentage of an unknown figure is not 0% consumed; it is not computed at all.
        assert summary.total_percent is None
        assert response.spending.total is None

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_measured_zero_renders_as_zero(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """A measured zero is a real figure: it must render as 0.0, never as an absent one."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row()],
            {self.CLI_HOLDER: SpendResult(figure=Decimal("0"))},
        )

        summary, response = await self._run_both_surfaces(super_admin_user)

        assert summary.current_spending == 0.0
        assert response.spending.current_spending == 0.0
        assert summary.total_percent == 0.0
        mock_spend_service.consumed_for_holders.assert_awaited_with([self.CLI_HOLDER], self.PERIOD)

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_truncated_total_survives_and_is_flagged(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """A figure covering only part of the period is a subtotal and must not render as a total."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row()],
            {
                self.CLI_HOLDER: SpendResult(
                    figure=Decimal("5.0"),
                    unavailable=TimeRange(self.PERIOD.start, datetime(2099, 4, 1, tzinfo=UTC)),
                )
            },
        )

        summary, response = await self._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending):
            assert figure.current_spending == pytest.approx(5.0)
            assert figure.spend_unavailable is True

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_total_sums_every_assigned_category(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """Two categories, two figures, one total - and nothing qualifies it."""
        rows = [
            self._budget_row(),
            self._budget_row(budget_id="budget-2", name="Platform Budget", budget_category="platform"),
        ]
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            rows,
            {
                self.CLI_HOLDER: SpendResult(figure=Decimal("4.0")),
                self.PLATFORM_HOLDER: SpendResult(figure=Decimal("1.5")),
            },
        )

        summary, response = await self._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending):
            assert figure.current_spending == pytest.approx(5.5)
            assert figure.spend_partial is False
            assert figure.spend_unavailable is False

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_total_short_by_one_category_is_flagged_partial(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """One category answered and one did not: the total is short, and a short total must say so."""
        rows = [
            self._budget_row(),
            self._budget_row(budget_id="budget-2", name="Platform Budget", budget_category="platform"),
        ]
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            rows,
            {
                self.CLI_HOLDER: SpendResult(figure=Decimal("4.0")),
                self.PLATFORM_HOLDER: SpendResult.no_figure(self.PERIOD),
            },
        )

        summary, response = await self._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending):
            assert figure.current_spending == pytest.approx(4.0)
            assert figure.spend_partial is True
            assert figure.spend_unavailable is True

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_budget_without_derivable_period_is_never_queried(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """No reset instant means no budget period, so there is no figure to ask the port for."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row(budget_reset_at=None)],
            {},
        )

        summary, response = await self._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending):
            assert figure.current_spending is None
            # Never asked is the same fact to a reader as no figure available, and is reported as such.
            assert figure.spend_unavailable is True
        mock_spend_service.consumed_for_holders.assert_not_awaited()

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_project_without_assigned_budgets_has_no_spending(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """Unchanged behaviour: nothing to sum over means no spending block at all, not a zero one."""
        self._stub(mock_project_budget_repo, mock_async_session, mock_spend_service, [], {})

        summary, response = await self._run_both_surfaces(super_admin_user)

        assert summary is None
        assert response.spending is None
        assert response.spending_widget is None

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_widget_has_one_row_per_assigned_budget_and_no_key_row(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """The "Key" row is gone: its traffic is inside the per-category rows, so it would double-count."""
        rows = [
            self._budget_row(),
            self._budget_row(budget_id="budget-2", name="Platform Budget", budget_category="platform"),
        ]
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            rows,
            {
                self.CLI_HOLDER: SpendResult(figure=Decimal("4.0")),
                self.PLATFORM_HOLDER: SpendResult(figure=Decimal("1.5")),
            },
        )

        _, response = await self._run_both_surfaces(super_admin_user)

        widget_rows = response.spending_widget.data.rows
        assert [row.budget_id for row in widget_rows] == ["budget-1", "budget-2"]
        assert all(row.budget_id != "Key" for row in widget_rows)
        assert [row.current_spending for row in widget_rows] == [pytest.approx(4.0), pytest.approx(1.5)]

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_widget_row_without_a_figure_reports_no_percentage(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """A widget row with no figure shows no consumption share - not 0% of its limit."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row()],
            {self.CLI_HOLDER: SpendResult.no_figure(self.PERIOD)},
        )

        _, response = await self._run_both_surfaces(super_admin_user)

        widget_row = response.spending_widget.data.rows[0]
        assert widget_row.current_spending is None
        assert widget_row.total is None
        assert widget_row.budget_limit == pytest.approx(20.0)
        assert widget_row.spend_unavailable is True

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_uncomputable_partial_portion_reports_an_unknown_missing_amount(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """An unresolvable alias set has no rows to sum, so the missing amount is unknown - not zero."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row()],
            {
                self.CLI_HOLDER: SpendResult(
                    figure=Decimal("5.0"),
                    partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
                )
            },
        )

        summary, response = await self._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending):
            assert figure.current_spending == pytest.approx(5.0)
            assert figure.spend_partial is True
            assert figure.spend_missing_amount is None
            assert figure.spend_unavailable is False

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_a_budget_created_mid_period_is_asked_about_from_its_creation(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """The project key counts from the budget's creation, so neither surface's period starts earlier."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row(budget_created_at=_CREATED_MID_PERIOD)],
            {self.CLI_HOLDER: SpendResult(figure=Decimal("1"))},
        )

        await self._run_both_surfaces(super_admin_user)

        assert [call.args for call in mock_spend_service.consumed_for_holders.await_args_list] == [
            ([self.CLI_HOLDER], TimeRange(_CREATED_MID_PERIOD, self.PERIOD.end))
        ] * 2

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_a_budget_created_before_the_period_keeps_the_derived_period(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """The clamp only ever moves the start later: an older budget keeps the derived period."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row(budget_created_at=_CREATED_BEFORE_PERIOD)],
            {self.CLI_HOLDER: SpendResult(figure=Decimal("1"))},
        )

        await self._run_both_surfaces(super_admin_user)

        assert [call.args for call in mock_spend_service.consumed_for_holders.await_args_list] == [
            ([self.CLI_HOLDER], self.PERIOD)
        ] * 2

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_a_project_hole_is_flagged_but_not_sized_on_every_surface(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """The hole is sized over the whole project, not these budgets, so its amount would mislead here."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row()],
            {self.CLI_HOLDER: SpendResult(figure=Decimal("5.0"), partial={SpendDimension.PROJECT: _PROJECT_HOLE})},
        )

        summary, response = await self._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending, response.spending_widget.data.rows[0]):
            assert figure.current_spending == pytest.approx(5.0)
            assert figure.spend_partial is True
            assert figure.spend_missing_amount is None

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_a_sub_cent_figure_is_sent_unrounded(
        self,
        mock_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        super_admin_user,
    ):
        """Rounded to cents, a real sub-cent spend would read as nothing spent."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            [self._budget_row()],
            {self.CLI_HOLDER: SpendResult(figure=Decimal("0.0042"))},
        )

        summary, response = await self._run_both_surfaces(super_admin_user)

        assert summary.current_spending == pytest.approx(0.0042)
        assert response.spending.current_spending == pytest.approx(0.0042)
        # 0.021 unrounded, 0.0 if derived from the cent-rounded figure.
        assert summary.total_percent == 0.02
        assert response.spending.total == 0.02

    @staticmethod
    def test_annotation_fields_are_additive():
        """Every other client of these models constructs them without the annotation and must keep working."""
        models = [
            ProjectSpendingSummary(current_spending=1.0, budget_limit=2.0, total_percent=50.0),
            ProjectSpendingDetail(current_spending=1.0, budget_limit=2.0, total=50.0),
            SpendingWidgetRow(budget_id="budget-1", current_spending=1.0, budget_limit=2.0, total=50.0),
        ]

        for model in models:
            assert (model.spend_unavailable, model.spend_partial, model.spend_missing_amount) == (False, False, None)


class TestProjectAssignmentEndpoints:
    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.get_session")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.get_session")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.get_session")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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


class TestProjectOwnershipEndpoint:
    """Tests for PUT /v1/projects/{projectName}/ownership."""

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
    def test_update_project_ownership_success_returns_200(
        self,
        mock_project_service,
        mock_config,
    ):
        """Successful ownership transfer returns the new owner in the response."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        updated_project = MagicMock()
        updated_project.name = "my-project"
        updated_project.created_by = "new-owner"
        mock_project_service.update_project_ownership.return_value = updated_project

        authorized_project = MagicMock()
        authorized_project.name = "my-project"
        caller = MagicMock(id="user-1")

        response = update_project_ownership(
            payload=ProjectOwnershipUpdateRequest(user_id="new-owner"),
            request=MagicMock(
                method="PUT",
                url=SimpleNamespace(path="/v1/projects/my-project/ownership"),
                state=SimpleNamespace(user=caller),
            ),
            project_name="my-project",
            authorized_project=authorized_project,
        )

        assert response.name == "my-project"
        assert response.created_by == "new-owner"
        assert "new-owner" in response.message
        mock_project_service.update_project_ownership.assert_called_once_with(
            user=caller,
            project_name="my-project",
            new_owner_id="new-owner",
        )

    def test_ownership_route_uses_authorize_project_access_dependency(self):
        """Only the current owner, maintainers, project admins, or super admins may call this
        route — enforced by `_authorize_project_access`'s existing 404-on-deny behavior
        (exercised directly in TestCheckProjectAccess)."""
        app = FastAPI()
        app.include_router(projects_router)

        route_dependencies: dict[tuple[str, str], set[str]] = {}
        for route in app.routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    route_dependencies[(method, route.path)] = {
                        dependency.call.__name__ for dependency in route.dependant.dependencies
                    }

        assert "_authorize_project_access" in route_dependencies[("PUT", "/v1/projects/{projectName}/ownership")]

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
    def test_update_project_ownership_propagates_404_for_missing_target_user(
        self,
        mock_project_service,
        mock_config,
    ):
        """A nonexistent target user id propagates the service's 404, not a silent success."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_project_service.update_project_ownership.side_effect = ExtendedHTTPException(
            code=404, message="User not found"
        )

        authorized_project = MagicMock()
        authorized_project.name = "my-project"

        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_project_ownership(
                payload=ProjectOwnershipUpdateRequest(user_id="nonexistent-user"),
                request=MagicMock(
                    method="PUT",
                    url=SimpleNamespace(path="/v1/projects/my-project/ownership"),
                    state=SimpleNamespace(user=MagicMock(id="user-1")),
                ),
                project_name="my-project",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 404


class TestUpdateProjectEndpoint:
    """Tests for PATCH /v1/projects/{projectName}."""

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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
    @patch("codemie.rest_api.routers.projects._common.logger")
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

    @patch("codemie.rest_api.routers.projects._common.logger")
    def test_action_without_path(self, mock_logger):
        with pytest.raises(ExtendedHTTPException):
            _raise_project_not_found(user_id="user-456", action="DELETE")
        log_message = mock_logger.warning.call_args[0][0]
        assert "method=DELETE" in log_message

    @patch("codemie.rest_api.routers.projects._common.logger")
    def test_empty_action_defaults_to_unknown(self, mock_logger):
        with pytest.raises(ExtendedHTTPException):
            _raise_project_not_found(user_id="user-789", action="")
        log_message = mock_logger.warning.call_args[0][0]
        assert "method=UNKNOWN" in log_message


class TestCheckProjectAccess:
    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects._common.get_session")
    @patch("codemie.rest_api.routers.projects._common.application_repository")
    @patch("codemie.rest_api.routers.projects._common.Ability")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects._common.get_session")
    @patch("codemie.rest_api.routers.projects._common.application_repository")
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

    from codemie.rest_api.routers.projects.membership import validate_users_from_csv

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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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
        from codemie.rest_api.routers.projects.membership import validate_users_from_csv

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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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
        from codemie.rest_api.routers.projects.membership import validate_users_from_csv

        response = validate_users_from_csv(
            file=self._make_upload(content),
            project_name="my-project",
            authorized_project=authorized_project,
        )

        # Assert — still 200, not 422
        assert len(response.users) == 2
        assert response.users[0].error is None
        assert response.users[1].error == "Invalid email address: 'bad-email'"

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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
        from codemie.rest_api.routers.projects.membership import validate_users_from_csv

        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_users_from_csv(
                file=self._make_upload(oversized_content),
                project_name="my-project",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 413
        assert "too large" in exc_info.value.message.lower()
        mock_csv_service.validate_csv.assert_not_called()

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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
        from codemie.rest_api.routers.projects.membership import validate_users_from_csv

        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_users_from_csv(
                file=self._make_upload(content),
                project_name="my-project",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 422
        assert "decoding" in exc_info.value.message.lower()

    @patch("codemie.rest_api.routers.projects._common.config")
    def test_user_management_disabled_returns_400(self, mock_config):
        """ENABLE_USER_MANAGEMENT=False → 400 before any file is read or service called."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False
        authorized_project = self._make_project()

        # Act & Assert
        from codemie.rest_api.routers.projects.membership import validate_users_from_csv

        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_users_from_csv(
                file=self._make_upload(b"email,role\nalice@example.com,user\n"),
                project_name="my-project",
                authorized_project=authorized_project,
            )

        assert exc_info.value.code == 400
        assert "user management" in exc_info.value.message.lower()

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.membership.get_session")
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
        from codemie.rest_api.routers.projects.membership import validate_users_from_csv

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


#: 2099-04-23T10:10:00Z minus the 30d duration - the period budget_period_range derives for the rows below.
_PERSONAL_PERIOD = TimeRange(datetime(2099, 3, 24, 10, 10, tzinfo=UTC), datetime(2099, 4, 23, 10, 10, tzinfo=UTC))


def _make_assigned_budget_row(
    project_name: str,
    budget_id: str = 'default',
    budget_category: str = 'platform',
    max_budget: float | None = 50.0,
    budget_reset_at: str | None = '2099-04-23T10:10:00Z',
) -> SimpleNamespace:
    """Return an assigned project budget row in the shape the budget repository yields."""
    return SimpleNamespace(
        project_name=project_name,
        budget_id=budget_id,
        name=f'{budget_id} budget',
        budget_category=budget_category,
        soft_budget=10.0,
        max_budget=max_budget,
        budget_duration='30d',
        budget_reset_at=budget_reset_at,
        provider_sync_status='ok',
        member_count=0,
        allocated_member_budget_total=0.0,
        budget_created_at=None,
        budget_spend_reset_at=None,
    )


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
        "codemie.service.project.project_spending_service.project_budget_assignment_repository."
        "get_assigned_budget_summaries_for_projects",
        new_callable=AsyncMock,
    )
    @patch('codemie.rest_api.routers.projects._common.config')
    @patch('codemie.service.project.project_spending_service.spend_service')
    @patch('codemie.service.project.project_spending_service.get_async_session')
    @patch('codemie.rest_api.routers.projects.crud.get_session')
    @patch('codemie.rest_api.routers.projects.crud.project_visibility_service')
    @patch('codemie.service.project.project_spending_service.project_budget_assignment_repository')
    @pytest.mark.anyio
    async def test_personal_project_owner_sees_spending_in_list(
        self,
        mock_project_budget_repo,
        mock_visibility_service,
        mock_get_session,
        mock_async_session,
        mock_spend_service,
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

        budget_row = _make_assigned_budget_row(project_name=personal_name)
        mock_async_session.return_value = AsyncMock()
        mock_project_budget_repo.get_removed_budget_summaries_for_projects = AsyncMock(return_value={})
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects = AsyncMock(
            return_value={personal_name: [budget_row]}
        )
        holder = ProjectHolder(project=personal_name, category=BudgetCategory.PLATFORM)
        mock_spend_service.consumed_for_holders = AsyncMock(return_value={holder: SpendResult(figure=Decimal('3.50'))})

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
            cost_center_ids=None,
            user=regular_user,
        )

        assert len(result.data) == 1
        item = result.data[0]
        assert item.project_type == 'personal'
        assert item.spending is not None
        assert item.spending.current_spending == pytest.approx(3.50)
        assert item.spending.budget_limit == pytest.approx(50.0)

    @patch(
        "codemie.service.project.project_spending_service.project_budget_assignment_repository."
        "get_assigned_budget_summaries_for_projects",
        new_callable=AsyncMock,
    )
    @patch('codemie.rest_api.routers.projects._common.config')
    @patch('codemie.service.project.project_spending_service.spend_service')
    @patch('codemie.service.project.project_spending_service.get_async_session')
    @patch('codemie.rest_api.routers.projects.crud.get_session')
    @patch('codemie.rest_api.routers.projects.crud.project_visibility_service')
    @patch('codemie.service.project.project_spending_service.project_budget_assignment_repository')
    @pytest.mark.anyio
    async def test_personal_project_detail_returns_period_spend_and_budget_limit(
        self,
        mock_project_budget_repo,
        mock_visibility_service,
        mock_get_session,
        mock_async_session,
        mock_spend_service,
        mock_config,
        mock_assigned_budgets,
        regular_user,
    ):
        """Personal project owner can fetch detail with current_spending and budget_limit."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        personal_name = regular_user.id
        mock_visibility_service.get_visible_project_with_members.return_value = _make_personal_project_detail(
            owner_id=personal_name
        )

        budget_row = _make_assigned_budget_row(project_name=personal_name, budget_id='default', max_budget=50.0)
        mock_async_session.return_value = AsyncMock()
        mock_project_budget_repo.get_removed_budget_summaries_for_projects = AsyncMock(return_value={})
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects = AsyncMock(
            return_value={personal_name: [budget_row]}
        )
        holder = ProjectHolder(project=personal_name, category=BudgetCategory.PLATFORM)
        mock_spend_service.consumed_for_holders = AsyncMock(return_value={holder: SpendResult(figure=Decimal('3.50'))})

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
        assert result.spending.budget_limit == pytest.approx(50.0)

    @patch(
        "codemie.service.project.project_spending_service.project_budget_assignment_repository."
        "get_assigned_budget_summaries_for_projects",
        new_callable=AsyncMock,
    )
    @patch('codemie.rest_api.routers.projects._common.config')
    @patch('codemie.service.project.project_spending_service.spend_service')
    @patch('codemie.service.project.project_spending_service.get_async_session')
    @patch('codemie.rest_api.routers.projects.crud.get_session')
    @patch('codemie.rest_api.routers.projects.crud.project_visibility_service')
    @patch('codemie.service.project.project_spending_service.project_budget_assignment_repository')
    @pytest.mark.anyio
    async def test_personal_project_detail_returns_spending_widget_with_budget_rows(
        self,
        mock_project_budget_repo,
        mock_visibility_service,
        mock_get_session,
        mock_async_session,
        mock_spend_service,
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
            _make_assigned_budget_row(personal_name, budget_id='default', budget_category='platform'),
            _make_assigned_budget_row(
                personal_name, budget_id='premium', budget_category='premium_models', max_budget=20.0
            ),
        ]
        mock_async_session.return_value = AsyncMock()
        mock_project_budget_repo.get_removed_budget_summaries_for_projects = AsyncMock(return_value={})
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects = AsyncMock(
            return_value={personal_name: rows}
        )
        mock_spend_service.consumed_for_holders = AsyncMock(
            return_value={
                ProjectHolder(project=personal_name, category=BudgetCategory.PLATFORM): SpendResult(
                    figure=Decimal('3.50')
                ),
                ProjectHolder(project=personal_name, category=BudgetCategory.PREMIUM_MODELS): SpendResult(
                    figure=Decimal('1.20')
                ),
            }
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

    @patch('codemie.rest_api.routers.projects._common.config')
    @patch('codemie.rest_api.routers.projects.crud.get_session')
    @patch('codemie.rest_api.routers.projects.crud.project_visibility_service')
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


class TestProjectDisplayNameEndpoints:
    """Tests for display_name field in create and update project endpoints."""

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.project_service")
    @patch("codemie.rest_api.routers.projects.crud._resolve_cost_center_name")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.project_service")
    @patch("codemie.rest_api.routers.projects.crud._resolve_cost_center_name")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
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


class TestAllowedModelsEndpoint:
    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
    @patch("codemie.rest_api.routers.projects.management._resolve_cost_center_name")
    @patch("codemie.rest_api.routers.projects._common.application_repository")
    @patch("codemie.rest_api.routers.projects.management.get_session")
    def test_update_allowed_models_success(
        self,
        mock_get_session,
        mock_app_repo,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        regular_user,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None

        project = MagicMock()
        project.name = "my-project"
        project.display_name = None
        project.description = "Project"
        project.project_type = "shared"
        project.created_by = "user-1"
        project.date = datetime(2026, 2, 10, tzinfo=UTC)
        project.cost_center_id = None
        project.allowed_models = ["gpt-4", "claude-3-sonnet"]
        project.default_model = None
        mock_project_service.update_allowed_models.return_value = project

        from codemie.rest_api.models.project import AllowedModelsUpdateRequest
        from codemie.rest_api.routers.projects.management import update_allowed_models

        response = update_allowed_models(
            payload=AllowedModelsUpdateRequest(allowed_models=["gpt-4", "claude-3-sonnet"]),
            project_name="my-project",
            user=regular_user,
        )

        assert response.name == "my-project"
        assert response.allowed_models == ["gpt-4", "claude-3-sonnet"]
        mock_project_service.update_allowed_models.assert_called_once_with(
            regular_user, "my-project", ["gpt-4", "claude-3-sonnet"], None
        )

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management._resolve_cost_center_name")
    @patch("codemie.rest_api.routers.projects._common.application_repository")
    @patch("codemie.rest_api.routers.projects.management.get_session")
    def test_update_allowed_models_rejects_empty_string_default_model(
        self,
        mock_get_session,
        mock_app_repo,
        mock_resolve_cost_center_name,
        mock_config,
        regular_user,
    ):
        """Regression for CR-014: `if payload.default_model:` was a falsy check, so an
        explicit empty-string default_model skipped validate_default_model entirely and
        was then persisted as-is."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None

        project = MagicMock()
        project.id = "proj-id-1"
        project.default_model = "gpt-4"
        mock_app_repo.get_by_name_case_insensitive.return_value = project

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        from codemie.rest_api.models.project import AllowedModelsUpdateRequest
        from codemie.rest_api.routers.projects.management import update_allowed_models

        with (
            patch(
                "codemie.rest_api.routers.projects.management.project_service.validate_allowed_models",
                return_value=["gpt-4", "claude-3-sonnet"],
            ),
            patch("codemie.rest_api.routers.projects.management.project_service.ensure_chat_model_allowed"),
            patch("codemie.rest_api.routers.projects.management.project_service._check_allowed_models_authorization"),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                update_allowed_models(
                    payload=AllowedModelsUpdateRequest(allowed_models=["gpt-4", "claude-3-sonnet"], default_model=""),
                    project_name="my-project",
                    user=regular_user,
                )

        assert exc_info.value.code == 400

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management._resolve_cost_center_name")
    @patch("codemie.rest_api.routers.projects._common.application_repository")
    @patch("codemie.rest_api.routers.projects.management.get_session")
    def test_update_allowed_models_rejects_stale_default_not_in_narrowed_list(
        self,
        mock_get_session,
        mock_app_repo,
        mock_resolve_cost_center_name,
        mock_config,
        regular_user,
    ):
        """Regression for CR-014: narrowing allowed_models while omitting default_model
        used to leave the project's existing (now stale) default_model unchecked against
        the new, narrower list."""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None

        project = MagicMock()
        project.id = "proj-id-1"
        project.default_model = "claude-3-sonnet"  # no longer in the narrowed list below
        mock_app_repo.get_by_name_case_insensitive.return_value = project

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        from codemie.rest_api.models.project import AllowedModelsUpdateRequest
        from codemie.rest_api.routers.projects.management import update_allowed_models

        with (
            patch(
                "codemie.rest_api.routers.projects.management.project_service.validate_allowed_models",
                return_value=["gpt-4"],
            ),
            patch("codemie.rest_api.routers.projects.management.project_service.ensure_chat_model_allowed"),
            patch("codemie.rest_api.routers.projects.management.project_service._check_allowed_models_authorization"),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                update_allowed_models(
                    payload=AllowedModelsUpdateRequest(allowed_models=["gpt-4"]),
                    project_name="my-project",
                    user=regular_user,
                )

        assert exc_info.value.code == 400

    @patch("codemie.rest_api.routers.projects.management.project_service")
    def test_validate_allowed_models_rejects_empty_list(self, mock_project_service):
        from codemie.service.project.project_service import ProjectService

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.validate_allowed_models([])

        assert exc_info.value.code == 400
        assert "At least one chat model is required" in exc_info.value.message

    def test_validate_allowed_models_accepts_none(self):
        from codemie.service.project.project_service import ProjectService

        result = ProjectService.validate_allowed_models(None)
        assert result is None

    def test_validate_allowed_models_accepts_single_model(self):
        from codemie.service.project.project_service import ProjectService

        result = ProjectService.validate_allowed_models(["gpt-4"])
        assert result == ["gpt-4"]

    def test_validate_allowed_models_preserves_order(self):
        from codemie.service.project.project_service import ProjectService

        models = ["gpt-4", "claude-3-sonnet", "gpt-3.5-turbo"]
        result = ProjectService.validate_allowed_models(models)
        assert result == models

    def test_validate_allowed_models_accepts_vertex_version_suffix(self):
        from codemie.service.project.project_service import ProjectService

        models = ["claude-opus-4-5@20251101-websearch"]
        assert ProjectService.validate_allowed_models(models) == models

    @pytest.mark.parametrize("model_id", ["gpt 4", "gpt/4", "gpt-4;drop"])
    def test_validate_allowed_models_rejects_invalid_characters(self, model_id):
        from codemie.service.project.project_service import ProjectService

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.validate_allowed_models([model_id])

        assert exc_info.value.code == 400
        assert model_id in exc_info.value.message

    @patch("codemie.service.llm_service.llm_service.llm_service")
    def test_validate_at_least_one_chat_model_accepts_none(self, mock_llm_service):
        """Test that None (all models allowed) is always valid."""
        from codemie.service.project.project_service import ProjectService

        # Should not raise any exception
        ProjectService.ensure_chat_model_allowed(None)
        # llm_service should not be called when allowed_models is None
        mock_llm_service.get_all_llm_model_info.assert_not_called()

    @patch("codemie.service.llm_service.llm_service.llm_service")
    def test_validate_at_least_one_chat_model_accepts_valid_models(self, mock_llm_service):
        """Test that a list with valid chat models is accepted.

        Regression for CR-017: validated against llm_service.get_all_llm_model_info()
        (the platform's declared catalog) directly, not via a synthetic-user
        get_allowed_chat_models call.
        """
        from codemie.service.project.project_service import ProjectService
        from codemie.configs.llm_config import LLMModel

        # Mock the platform's declared catalog
        mock_llm_service.get_all_llm_model_info.return_value = [
            LLMModel(base_name="gpt-4", deployment_name="gpt-4", enabled=True),
            LLMModel(base_name="claude-3-sonnet", deployment_name="claude-3-sonnet", enabled=True),
        ]

        # Should not raise any exception
        ProjectService.ensure_chat_model_allowed(["gpt-4"])
        mock_llm_service.get_all_llm_model_info.assert_called_once()

    @patch("codemie.service.llm_service.llm_service.llm_service")
    def test_validate_at_least_one_chat_model_rejects_no_chat_models(self, mock_llm_service):
        """Test that a list with no chat models is rejected."""
        from codemie.service.project.project_service import ProjectService
        from codemie.configs.llm_config import LLMModel
        from codemie.core.exceptions import ExtendedHTTPException

        # Mock catalog (only embedding models)
        mock_llm_service.get_all_llm_model_info.return_value = [
            LLMModel(base_name="text-embedding-ada-002", deployment_name="text-embedding-ada-002", enabled=True),
        ]

        # Should raise exception when trying to select only non-chat models
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectService.ensure_chat_model_allowed(["non-existent-model"])

        assert exc_info.value.code == 400
        assert "At least one chat model is required" in exc_info.value.message


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

    @patch("codemie.rest_api.routers.projects.crud.SettingsService")
    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.project_service")
    @patch("codemie.rest_api.routers.projects.crud._resolve_cost_center_name")
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

    @patch("codemie.rest_api.routers.projects.management.SettingsService")
    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
    @patch("codemie.rest_api.routers.projects.management._resolve_cost_center_name")
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

    @patch("codemie.rest_api.routers.projects.management.SettingsService")
    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.management.project_service")
    @patch("codemie.rest_api.routers.projects.management._resolve_cost_center_name")
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


class TestAssignedBudgetRowsAreReconciledBeforeUse:
    """Every period on the project screens is derived from the stored reset instant, so an overdue
    one is reconciled first and the rows read again - otherwise last period's spend shows as current."""

    @staticmethod
    def _rows(reset_at: str) -> dict:
        return {"proj-a": [SimpleNamespace(budget_id="b-1", budget_reset_at=reset_at, provider_metadata={})]}

    @staticmethod
    def _session():
        @asynccontextmanager
        async def _session():
            yield AsyncMock()

        return _session

    @pytest.mark.anyio
    async def test_rows_are_read_again_when_a_reset_moved(self) -> None:
        from codemie.service.project.project_spending_service import assigned_budget_rows

        stale, current = self._rows("2026-09-01T00:00:00Z"), self._rows("2026-10-01T00:00:00Z")
        read = AsyncMock(side_effect=[stale, current])
        refresh = AsyncMock(return_value={"b-1"})
        with (
            patch(
                "codemie.service.project.project_spending_service.project_budget_assignment_repository."
                "get_assigned_budget_summaries_for_projects",
                read,
            ),
            patch(
                "codemie.service.project.project_spending_service.daily_budget_reset_reconciliation_service.refresh_overdue",
                refresh,
            ),
            patch("codemie.service.project.project_spending_service.get_async_session", self._session()),
        ):
            assert await assigned_budget_rows(["proj-a"], "cli") is current

        assert list(refresh.await_args.args[0]) == stale["proj-a"]
        assert read.await_args.kwargs == {"budget_category": "cli"}

    @pytest.mark.anyio
    async def test_rows_are_read_once_when_nothing_moved(self) -> None:
        from codemie.service.project.project_spending_service import assigned_budget_rows

        rows = self._rows("2026-11-01T00:00:00Z")
        read = AsyncMock(return_value=rows)
        with (
            patch(
                "codemie.service.project.project_spending_service.project_budget_assignment_repository."
                "get_assigned_budget_summaries_for_projects",
                read,
            ),
            patch(
                "codemie.service.project.project_spending_service.daily_budget_reset_reconciliation_service.refresh_overdue",
                AsyncMock(return_value=set()),
            ),
            patch("codemie.service.project.project_spending_service.get_async_session", self._session()),
        ):
            assert await assigned_budget_rows(["proj-a"]) is rows

        read.assert_awaited_once()


class TestEveryProjectScreenReadsItsRowsThroughTheReconciledSeam:
    """A screen reading its rows from the repository directly would derive its periods from an overdue
    reset instant again, so each of the three is pinned to `assigned_budget_rows`."""

    SEAM = "codemie.service.project.project_spending_service.assigned_budget_rows"

    @pytest.mark.anyio
    async def test_the_list_budget_summaries(self) -> None:
        from codemie.rest_api.routers.projects.crud import _attach_assigned_budget_summaries

        with (
            patch("codemie.rest_api.routers.projects.crud.assigned_budget_rows", AsyncMock(return_value={})) as seam,
            patch("codemie.rest_api.routers.projects.crud.removed_budget_rows", AsyncMock(return_value={})),
        ):
            await _attach_assigned_budget_summaries([SimpleNamespace(name="proj-a", budgets=None)], BudgetCategory.CLI)

        seam.assert_awaited_once_with(["proj-a"], "cli")

    @pytest.mark.anyio
    async def test_the_list_spending_summaries(self) -> None:
        from codemie.service.project.project_spending_service import attach_project_spending_summaries

        with (
            patch(self.SEAM, AsyncMock(return_value={})) as seam,
            patch("codemie.service.project.project_spending_service.removed_budget_rows", AsyncMock(return_value={})),
            patch("codemie.service.project.project_spending_service._removed_budget_spend", AsyncMock(return_value={})),
        ):
            await attach_project_spending_summaries(
                [SimpleNamespace(name="proj-a", spending=None)],
                [{"name": "proj-a"}],
                SimpleNamespace(is_admin=True),
            )

        seam.assert_awaited_once_with(["proj-a"])

    @pytest.mark.anyio
    async def test_the_project_detail_spending(self) -> None:
        from codemie.service.project.project_spending_service import attach_project_detail_spending

        with (
            patch(self.SEAM, AsyncMock(return_value={})) as seam,
            patch("codemie.service.project.project_spending_service.removed_budget_rows", AsyncMock(return_value={})),
            patch("codemie.service.project.project_spending_service._removed_budget_spend", AsyncMock(return_value={})),
        ):
            await attach_project_detail_spending(response=MagicMock(), project_name="proj-a", spending_rows_limit=5)

        seam.assert_awaited_once_with(["proj-a"])


class TestRemovedCategorySpend:
    """A category removed during a budget period that is still open keeps counting (EPMCDME-15048).

    Removing a category ends its budget, not the period its spend belongs to, so until the removed
    budget's reset instant its spend stays in the project total and shows as an unassigned widget row.
    Each case runs both surfaces over one stub, like `TestProjectTotalSpendFigures`.
    """

    TOTALS = TestProjectTotalSpendFigures
    PLATFORM_HOLDER = TestProjectTotalSpendFigures.PLATFORM_HOLDER
    CLI_HOLDER = TestProjectTotalSpendFigures.CLI_HOLDER

    @classmethod
    def _removed_row(cls, **overrides) -> SimpleNamespace:
        fields = {"budget_id": "budget-removed", "name": "Platform Budget", "budget_category": "platform"}
        fields["max_budget"] = 100.0
        fields.update(overrides)
        return cls.TOTALS._budget_row(**fields)

    @classmethod
    def _stub(cls, repo, session, spend_service, *, assigned, removed, spend_by_holder) -> None:
        cls.TOTALS._stub(repo, session, spend_service, assigned, {})
        repo.get_removed_budget_summaries_for_projects = AsyncMock(return_value={"proj-a": removed} if removed else {})
        spend_service.consumed_for_holders = AsyncMock(
            side_effect=lambda holders, period: {holder: spend_by_holder[holder] for holder in holders}
        )

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_removed_category_spend_counts_and_is_flagged_unassigned(
        self, mock_async_session, mock_project_budget_repo, mock_spend_service, super_admin_user
    ):
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            assigned=[self.TOTALS._budget_row()],
            removed=[self._removed_row()],
            spend_by_holder={
                self.CLI_HOLDER: SpendResult(figure=Decimal("7.25")),
                self.PLATFORM_HOLDER: SpendResult(figure=Decimal("2.75")),
            },
        )

        summary, response = await self.TOTALS._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending):
            assert figure.current_spending == pytest.approx(10.0)
            # The limit stays the sum of the assigned budgets: the removed one's limit ended with it.
            assert figure.budget_limit == pytest.approx(20.0)
        assert summary.total_percent == pytest.approx(50.0)
        rows = {row.budget_category: row for row in response.spending_widget.data.rows}
        assert (rows["cli"].is_assigned, rows["cli"].budget_limit) == (True, 20.0)
        removed = rows["platform"]
        assert (removed.budget_id, removed.is_assigned, removed.current_spending) == ("budget-removed", False, 2.75)
        assert (removed.budget_limit, removed.total) == (None, None)

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_removed_category_is_not_read_after_its_period_ended(
        self, mock_async_session, mock_project_budget_repo, mock_spend_service, super_admin_user
    ):
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            assigned=[self.TOTALS._budget_row()],
            removed=[self._removed_row(budget_reset_at="2020-01-01T00:00:00Z")],
            spend_by_holder={self.CLI_HOLDER: SpendResult(figure=Decimal("7.25"))},
        )

        summary, response = await self.TOTALS._run_both_surfaces(super_admin_user)

        assert summary.current_spending == pytest.approx(7.25)
        assert [row.budget_category for row in response.spending_widget.data.rows] == ["cli"]
        for call in mock_spend_service.consumed_for_holders.await_args_list:
            assert self.PLATFORM_HOLDER not in call.args[0]

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_removed_category_with_a_known_zero_is_left_out(
        self, mock_async_session, mock_project_budget_repo, mock_spend_service, super_admin_user
    ):
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            assigned=[self.TOTALS._budget_row()],
            removed=[self._removed_row()],
            spend_by_holder={
                self.CLI_HOLDER: SpendResult(figure=Decimal("7.25")),
                self.PLATFORM_HOLDER: SpendResult(figure=Decimal("0")),
            },
        )

        _, response = await self.TOTALS._run_both_surfaces(super_admin_user)

        assert [row.budget_category for row in response.spending_widget.data.rows] == ["cli"]

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_removed_category_with_an_absent_figure_is_kept_and_flags_the_total(
        self, mock_async_session, mock_project_budget_repo, mock_spend_service, super_admin_user
    ):
        """An absent figure may hide spend: leaving it out would shorten the total with nothing saying so."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            assigned=[self.TOTALS._budget_row()],
            removed=[self._removed_row()],
            spend_by_holder={
                self.CLI_HOLDER: SpendResult(figure=Decimal("7.25")),
                self.PLATFORM_HOLDER: SpendResult.no_figure(self.TOTALS.PERIOD),
            },
        )

        summary, response = await self.TOTALS._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending):
            assert (figure.current_spending, figure.spend_unavailable) == (pytest.approx(7.25), True)
        removed = next(row for row in response.spending_widget.data.rows if not row.is_assigned)
        assert (removed.current_spending, removed.spend_unavailable) == (None, True)

    #: Creations inside `PERIOD` (2099-03-24T10:10Z .. 2099-04-23T10:10Z): two removed budgets, then the active one.
    FIRST_CREATED = datetime(2099, 4, 1, tzinfo=UTC)
    SECOND_CREATED = datetime(2099, 4, 5, tzinfo=UTC)
    ACTIVE_CREATED = datetime(2099, 4, 10, tzinfo=UTC)

    @staticmethod
    def _asked_periods(spend_service) -> set[TimeRange]:
        return {call.args[1] for call in spend_service.consumed_for_holders.await_args_list}

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_category_assigned_again_counts_from_its_first_budget_this_period(
        self, mock_async_session, mock_project_budget_repo, mock_spend_service, super_admin_user
    ):
        """Removing a category keeps its project key, whose counter runs on to the reset: re-adding it within
        the period is answered by the active budget alone, over the spend since the period's first budget of
        the category - not since the re-add, which would show LiteLLM's counted spend as never spent."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            assigned=[self.TOTALS._budget_row(budget_id="budget-new", budget_created_at=self.ACTIVE_CREATED)],
            removed=[
                self._removed_row(budget_category="cli", budget_created_at=self.SECOND_CREATED),
                self._removed_row(
                    budget_id="budget-first", budget_category="cli", budget_created_at=self.FIRST_CREATED
                ),
            ],
            spend_by_holder={self.CLI_HOLDER: SpendResult(figure=Decimal("7.25"))},
        )

        summary, response = await self.TOTALS._run_both_surfaces(super_admin_user)

        (row,) = response.spending_widget.data.rows
        assert (row.budget_id, row.is_assigned, row.budget_limit) == ("budget-new", True, 20.0)
        assert summary.current_spending == pytest.approx(7.25)
        assert self._asked_periods(mock_spend_service) == {TimeRange(self.FIRST_CREATED, self.TOTALS.PERIOD.end)}

    @patch("codemie.rest_api.routers.projects.crud.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_the_list_budget_column_counts_a_category_assigned_again_from_its_first_budget(
        self, mock_async_session, mock_project_budget_repo, mock_spend_service
    ):
        """The projects list's per-budget figures derive their period by the same rule as the detail page."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            assigned=[self.TOTALS._budget_row(budget_id="budget-new", budget_created_at=self.ACTIVE_CREATED)],
            removed=[self._removed_row(budget_category="cli", budget_created_at=self.FIRST_CREATED)],
            spend_by_holder={self.CLI_HOLDER: SpendResult(figure=Decimal("7.25"))},
        )
        item = SimpleNamespace(name="proj-a", budgets=None)

        await _attach_assigned_budget_summaries([item], None)

        (budget,) = item.budgets
        assert (budget.budget_id, budget.current_spending) == ("budget-new", pytest.approx(7.25))
        assert self._asked_periods(mock_spend_service) == {TimeRange(self.FIRST_CREATED, self.TOTALS.PERIOD.end)}

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_another_categorys_removal_leaves_an_active_budgets_period_alone(
        self, mock_async_session, mock_project_budget_repo, mock_spend_service, super_admin_user
    ):
        """Each category has its own project key: a removed platform budget says nothing about the cli one."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            assigned=[self.TOTALS._budget_row(budget_created_at=self.ACTIVE_CREATED)],
            removed=[self._removed_row(budget_created_at=self.FIRST_CREATED)],
            spend_by_holder={
                self.CLI_HOLDER: SpendResult(figure=Decimal("7.25")),
                self.PLATFORM_HOLDER: SpendResult(figure=Decimal("2.75")),
            },
        )

        await self.TOTALS._run_both_surfaces(super_admin_user)

        period_by_holder = {
            holder: call.args[1]
            for call in mock_spend_service.consumed_for_holders.await_args_list
            for holder in call.args[0]
        }
        assert period_by_holder == {
            self.CLI_HOLDER: TimeRange(self.ACTIVE_CREATED, self.TOTALS.PERIOD.end),
            self.PLATFORM_HOLDER: TimeRange(self.FIRST_CREATED, self.TOTALS.PERIOD.end),
        }

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_a_removal_from_an_earlier_period_does_not_reach_into_this_one(
        self, mock_async_session, mock_project_budget_repo, mock_spend_service, super_admin_user
    ):
        """Only a removal sharing the active budget's reset instant belongs to its period."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            assigned=[self.TOTALS._budget_row(budget_id="budget-new", budget_created_at=self.ACTIVE_CREATED)],
            removed=[
                self._removed_row(
                    budget_category="cli", budget_created_at=self.FIRST_CREATED, budget_reset_at="2099-03-24T10:10:00Z"
                )
            ],
            spend_by_holder={self.CLI_HOLDER: SpendResult(figure=Decimal("7.25"))},
        )

        await self.TOTALS._run_both_surfaces(super_admin_user)

        assert self._asked_periods(mock_spend_service) == {TimeRange(self.ACTIVE_CREATED, self.TOTALS.PERIOD.end)}

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_a_category_removed_twice_counts_from_its_first_budget(
        self, mock_async_session, mock_project_budget_repo, mock_spend_service, super_admin_user
    ):
        """The latest removal stands for the category; its spend reaches back to the period's first budget."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            assigned=[],
            removed=[
                self._removed_row(budget_created_at=self.SECOND_CREATED),
                self._removed_row(budget_id="budget-first", budget_created_at=self.FIRST_CREATED),
            ],
            spend_by_holder={self.PLATFORM_HOLDER: SpendResult(figure=Decimal("2.75"))},
        )

        summary, response = await self.TOTALS._run_both_surfaces(super_admin_user)

        (row,) = response.spending_widget.data.rows
        assert (row.budget_id, row.is_assigned, row.current_spending) == ("budget-removed", False, pytest.approx(2.75))
        assert summary.current_spending == pytest.approx(2.75)
        assert self._asked_periods(mock_spend_service) == {TimeRange(self.FIRST_CREATED, self.TOTALS.PERIOD.end)}

    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @pytest.mark.anyio
    async def test_project_with_only_removed_categories_still_reports_their_spend(
        self, mock_async_session, mock_project_budget_repo, mock_spend_service, super_admin_user
    ):
        """Deleting the whole budget group is removing every category at once."""
        self._stub(
            mock_project_budget_repo,
            mock_async_session,
            mock_spend_service,
            assigned=[],
            removed=[self._removed_row()],
            spend_by_holder={self.PLATFORM_HOLDER: SpendResult(figure=Decimal("2.75"))},
        )

        summary, response = await self.TOTALS._run_both_surfaces(super_admin_user)

        for figure in (summary, response.spending):
            assert figure.current_spending == pytest.approx(2.75)
            assert figure.budget_limit is None
        assert summary.total_percent is None
        (row,) = response.spending_widget.data.rows
        assert row.is_assigned is False
