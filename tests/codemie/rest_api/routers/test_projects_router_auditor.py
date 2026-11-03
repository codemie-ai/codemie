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

"""Tests for auditor access to projects router (EPMCDME-10930)."""

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs import config
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import SpendResult
from codemie.service.spend.ledger import ProjectHolder


@asynccontextmanager
async def _mock_session_ctx(session):
    yield session


def _make_auditor():
    with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
        return User(
            id="aud-1",
            email="aud@example.com",
            username="aud",
            is_admin=False,
            is_maintainer=False,
            is_auditor=True,
            admin_project_names=[],
        )


class TestProjectsRouterAuditor:
    def test_manageable_project_names_includes_auditor(self):
        """Auditor should see all projects as manageable (for spending summaries)."""
        from codemie.service.project.project_spending_service import manageable_project_names

        auditor = _make_auditor()
        enriched = [
            {"name": "p1", "is_project_admin": False, "project_type": "shared"},
            {"name": "p2", "is_project_admin": True, "project_type": "shared"},
        ]
        result = manageable_project_names(enriched, auditor)
        assert "p1" in result
        assert "p2" in result

    def test_can_see_project_spending_true_for_auditor(self):
        """Auditor should be able to see project spending."""
        from codemie.service.project.project_spending_service import can_see_project_spending

        auditor = _make_auditor()
        project_detail = {"project_type": "shared", "is_project_admin": False}
        assert can_see_project_spending(auditor, project_detail) is True

    def test_can_see_project_spending_false_for_regular_user(self):
        """Regular user without project admin should not see spending."""
        from codemie.service.project.project_spending_service import can_see_project_spending

        with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
            regular = User(
                id="reg-1",
                username="reg",
                is_admin=False,
                is_maintainer=False,
                is_auditor=False,
                admin_project_names=[],
            )
        project_detail = {"project_type": "shared", "is_project_admin": False}
        assert can_see_project_spending(regular, project_detail) is False


class TestProjectsRouterAuditorRouteLevel:
    """EPMCDME-10930 spec 5.2: router-level auditor coverage for list_projects and
    get_project_detail (the helper-level tests above only exercise the pure
    functions, not the routes that actually call asyncio.to_thread(..., is_admin=...)
    or evaluate the inline can_access disjunction).
    """

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
    @pytest.mark.anyio
    async def test_list_projects_passes_is_admin_true_for_auditor(
        self, mock_visibility_service, mock_get_session, mock_config
    ):
        from codemie.rest_api.routers.projects.crud import list_projects

        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_visibility_service.list_visible_projects_paginated.return_value = ([], 0)

        await list_projects(
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
            user=_make_auditor(),
        )

        call_kwargs = mock_visibility_service.list_visible_projects_paginated.call_args.kwargs
        assert call_kwargs["is_admin"] is True

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
    @pytest.mark.anyio
    async def test_get_project_detail_passes_is_admin_true_for_auditor(
        self, mock_visibility_service, mock_get_session, mock_config
    ):
        from codemie.rest_api.routers.projects.crud import get_project_detail

        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_visibility_service.get_visible_project_with_members.return_value = {
            "name": "some-project",
            "description": None,
            "project_type": "shared",
            "created_by": "owner-1",
            "created_at": None,
            "user_count": 0,
            "admin_count": 0,
            "members": [],
        }

        await get_project_detail(
            request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/some-project")),
            project_name="some-project",
            include_spending=False,
            user=_make_auditor(),
        )

        call_kwargs = mock_visibility_service.get_visible_project_with_members.call_args.kwargs
        assert call_kwargs["is_admin"] is True

    @patch("codemie.rest_api.routers.projects._common.config")
    @patch("codemie.service.project.project_spending_service.spend_service")
    @patch("codemie.service.project.project_spending_service.project_budget_assignment_repository")
    @patch("codemie.service.project.project_spending_service.get_async_session")
    @patch("codemie.rest_api.routers.projects.crud.get_session")
    @patch("codemie.rest_api.routers.projects.crud.project_visibility_service")
    @pytest.mark.anyio
    async def test_get_project_detail_include_spending_true_includes_spending_for_auditor(
        self,
        mock_visibility_service,
        mock_get_session,
        mock_get_async_session,
        mock_project_budget_repo,
        mock_spend_service,
        mock_config,
    ):
        """EPMCDME-10930 spec 5.2: GET /v1/projects/{name}?include_spending=true actually
        returns spending data for a pure auditor (not just is_admin=True passed downstream).
        """
        from codemie.rest_api.routers.projects.crud import get_project_detail

        mock_project_budget_repo.get_removed_budget_summaries_for_projects = AsyncMock(return_value={})
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects = AsyncMock(return_value={})

        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_visibility_service.get_visible_project_with_members.return_value = {
            "name": "some-project",
            "description": None,
            "project_type": "shared",
            "created_by": "owner-1",
            "created_at": None,
            "user_count": 0,
            "admin_count": 0,
            "members": [],
        }
        mock_get_async_session.side_effect = lambda: _mock_session_ctx(AsyncMock())

        budget_row = SimpleNamespace(
            project_name="some-project",
            budget_id="budget-1",
            name="Platform Budget",
            budget_category="platform",
            soft_budget=50.0,
            max_budget=200.0,
            budget_duration="30d",
            budget_reset_at="2099-04-23T10:10:00Z",
            provider_sync_status="ok",
            member_count=0,
            allocated_member_budget_total=0.0,
            budget_created_at=None,
            budget_spend_reset_at=None,
        )
        mock_project_budget_repo.get_assigned_budget_summaries_for_projects = AsyncMock(
            return_value={"some-project": [budget_row]}
        )
        holder = ProjectHolder(project="some-project", category=BudgetCategory.PLATFORM)
        mock_spend_service.consumed_for_holders = AsyncMock(return_value={holder: SpendResult(figure=Decimal("100.0"))})

        response = await get_project_detail(
            request=MagicMock(method="GET", url=SimpleNamespace(path="/v1/projects/some-project")),
            project_name="some-project",
            include_spending=True,
            spending_rows_limit=50,
            user=_make_auditor(),
        )

        assert response.spending is not None
        assert response.spending.current_spending == 100.0
