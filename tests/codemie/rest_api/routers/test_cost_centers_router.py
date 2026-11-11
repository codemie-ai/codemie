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

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.configs.customer_config import CustomerConfig
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.cost_center import CostCenterCreateRequest
from codemie.rest_api.routers.cost_centers import (
    create_cost_center,
    get_cost_center_detail,
    list_cost_centers,
)
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.cost_center_budget_summary import (
    CostCenterAssignedBudgetSummary,
    CostCenterBudgetSummary,
)


def _super_admin() -> User:
    return User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)


class TestCostCenterRouter:
    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_create_cost_center(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_get_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_application_repository.count_active_projects_by_cost_center_id.return_value = 0
        cost_center = SimpleNamespace(
            id=uuid4(),
            name="epm-cdme",
            description="Core platform",
            created_by="admin-1",
            date=datetime(2026, 3, 19, tzinfo=UTC),
        )
        mock_cost_center_service.create.return_value = cost_center

        response = create_cost_center(
            payload=CostCenterCreateRequest(name="epm-cdme", description="Core platform"),
            user=_super_admin(),
            _=None,
        )

        mock_cost_center_service.create.assert_called_once()
        assert response.name == "epm-cdme"
        assert response.project_count == 0

    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_list_cost_centers(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_get_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_application_repository.count_active_projects_by_cost_center_id.return_value = 0
        cost_center = SimpleNamespace(
            id=uuid4(),
            name="epm-cdme",
            description="Core platform",
            created_by="admin-1",
            date=datetime(2026, 3, 19, tzinfo=UTC),
        )
        mock_cost_center_service.list_paginated.return_value = ([cost_center], 1)

        response = list_cost_centers(search="epm", page=0, per_page=20, user=_super_admin(), _=None)

        assert response.pagination.total == 1
        assert response.data[0].name == "epm-cdme"

    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_get_cost_center_detail(
        self,
        mock_cost_center_service,
        mock_application_repository,
        mock_get_session,
        mock_config,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        cost_center_id = uuid4()
        cost_center = SimpleNamespace(
            id=cost_center_id,
            name="epm-cdme",
            description="Core platform",
            created_by="admin-1",
            date=datetime(2026, 3, 19, tzinfo=UTC),
        )
        project = SimpleNamespace(
            name="data-pipeline",
            display_name=None,
            description="Analytics pipeline",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 3, 19, tzinfo=UTC),
            cost_center_id=cost_center_id,
        )
        mock_cost_center_service.get_or_404.return_value = cost_center
        mock_application_repository.list_projects_by_cost_center_id.return_value = [project]
        mock_application_repository.get_project_member_counts_bulk.return_value = {"data-pipeline": (3, 1)}
        mock_application_repository.count_active_projects_by_cost_center_id.return_value = 1

        response = get_cost_center_detail(cost_center_id=cost_center_id, user=_super_admin(), _=None)

        assert response.id == cost_center_id
        assert response.project_count == 1
        assert response.projects[0].cost_center_name == "epm-cdme"


class TestCostCenterBudgetRollup:
    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_cost_center_budget_summary")
    @patch("codemie.rest_api.routers.cost_centers.get_async_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_get_cost_center_budget_rollup_sums_linked_projects(
        self,
        mock_cost_center_service,
        mock_get_session,
        mock_application_repository,
        mock_get_async_session,
        mock_get_cost_center_budget_summary,
        mock_config,
    ):
        import asyncio

        from codemie.rest_api.routers.cost_centers import get_cost_center_budget_rollup

        mock_config.ENABLE_USER_MANAGEMENT = True
        cost_center_id = uuid4()
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_cost_center_service.get_or_404.return_value = SimpleNamespace(id=cost_center_id, name="epm-cdme")
        mock_get_async_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_get_async_session.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_application_repository.aget_project_names_by_cost_center_name = AsyncMock(
            return_value=["proj-a", "proj-b"]
        )
        expected = CostCenterBudgetSummary(
            current_spending=15.0,
            budget_limit=60.0,
            total_percent=25.0,
            budgets=[
                CostCenterAssignedBudgetSummary(
                    budget_category=BudgetCategory.PLATFORM,
                    soft_budget=25.0,
                    max_budget=50.0,
                    member_count=3,
                    allocated_member_budget_total=25.0,
                    current_spending=15.0,
                )
            ],
        )
        mock_get_cost_center_budget_summary.return_value = expected

        response = asyncio.run(
            get_cost_center_budget_rollup(cost_center_id=cost_center_id, user=_super_admin(), _=None)
        )

        mock_application_repository.aget_project_names_by_cost_center_name.assert_awaited_once_with(
            mock_get_async_session.return_value.__aenter__.return_value, "epm-cdme"
        )
        mock_get_cost_center_budget_summary.assert_called_once()
        assert response.budget_limit == 60.0
        assert response.budgets[0].budget_category == BudgetCategory.PLATFORM

    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_cost_center_budget_summary")
    @patch("codemie.rest_api.routers.cost_centers.get_async_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_get_cost_center_budget_rollup_zero_for_no_linked_projects(
        self,
        mock_cost_center_service,
        mock_get_session,
        mock_application_repository,
        mock_get_async_session,
        mock_get_cost_center_budget_summary,
        mock_config,
    ):
        import asyncio

        from codemie.rest_api.routers.cost_centers import get_cost_center_budget_rollup

        mock_config.ENABLE_USER_MANAGEMENT = True
        cost_center_id = uuid4()
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_cost_center_service.get_or_404.return_value = SimpleNamespace(id=cost_center_id, name="epm-cdme")
        mock_get_async_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_get_async_session.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_application_repository.aget_project_names_by_cost_center_name = AsyncMock(return_value=[])
        zero_summary = CostCenterBudgetSummary(current_spending=0.0, budget_limit=None, total_percent=0.0, budgets=[])
        mock_get_cost_center_budget_summary.return_value = zero_summary

        response = asyncio.run(
            get_cost_center_budget_rollup(cost_center_id=cost_center_id, user=_super_admin(), _=None)
        )

        assert response.budgets == []
        assert response.budget_limit is None

    @patch("codemie.rest_api.routers.cost_centers.config")
    @patch("codemie.rest_api.routers.cost_centers.get_async_session")
    @patch("codemie.rest_api.routers.cost_centers.application_repository")
    @patch("codemie.rest_api.routers.cost_centers.get_session")
    @patch("codemie.rest_api.routers.cost_centers.cost_center_service")
    def test_get_cost_center_budget_rollup_rejects_when_budget_management_disabled_but_cost_centers_enabled(
        self,
        mock_cost_center_service,
        mock_get_session,
        mock_application_repository,
        mock_get_async_session,
        mock_config,
    ):
        """AC2: costCenters being enabled is no longer sufficient — budgetManagement is its own
        gate, enforced by get_cost_center_budget_summary()'s own chokepoint (not mocked here)."""
        import asyncio

        from codemie.rest_api.routers.cost_centers import get_cost_center_budget_rollup

        mock_config.ENABLE_USER_MANAGEMENT = True
        cost_center_id = uuid4()
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        mock_cost_center_service.get_or_404.return_value = SimpleNamespace(id=cost_center_id, name="epm-cdme")
        mock_get_async_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_get_async_session.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_application_repository.aget_project_names_by_cost_center_name = AsyncMock(return_value=["proj-a"])

        def _is_feature_enabled(self, feature_key: str) -> bool:
            return feature_key == "costCenters"

        with patch.object(CustomerConfig, "is_feature_enabled", _is_feature_enabled):
            with pytest.raises(ExtendedHTTPException) as exc:
                asyncio.run(get_cost_center_budget_rollup(cost_center_id=cost_center_id, user=_super_admin(), _=None))

        assert exc.value.code == 403
