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

"""Tests for auditor access to budget router endpoints (EPMCDME-10930)."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from codemie.configs import config
from codemie.rest_api.security.user import User


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


class TestBudgetRouterAuditor:
    @patch("codemie.rest_api.routers.budget_router.require_litellm_enabled")
    @patch("codemie.rest_api.routers.budget_router.get_async_session")
    @patch("codemie.rest_api.routers.budget_router.budget_service")
    @pytest.mark.anyio
    async def test_auditor_can_list_budgets(self, mock_service, mock_session_ctx, mock_litellm):
        from codemie.rest_api.routers.budget_router import list_budgets

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_ctx.return_value = mock_session
        mock_service.list_budgets = AsyncMock(return_value=([], 0))
        mock_service.resolve_creator_names = AsyncMock(return_value={})

        auditor = _make_auditor()
        result = await list_budgets(page=0, per_page=20, category=None, user=auditor)
        assert result is not None

    @patch("codemie.rest_api.routers.budget_router.require_litellm_enabled")
    @patch("codemie.rest_api.routers.budget_router.get_async_session")
    @patch("codemie.rest_api.routers.budget_router.budget_service")
    @pytest.mark.anyio
    async def test_auditor_can_get_budget(self, mock_service, mock_session_ctx, mock_litellm):
        from codemie.rest_api.routers.budget_router import get_budget

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_ctx.return_value = mock_session
        from datetime import datetime
        from codemie.service.budget.budget_enums import BudgetCategory

        fake_budget = MagicMock()
        fake_budget.budget_id = "b1"
        fake_budget.name = "B1"
        fake_budget.description = "test"
        fake_budget.soft_budget = 50.0
        fake_budget.max_budget = 100.0
        fake_budget.budget_duration = "30d"
        fake_budget.budget_category = BudgetCategory.PLATFORM
        fake_budget.budget_reset_at = None
        fake_budget.created_by = "admin"
        fake_budget.created_at = datetime(2026, 1, 1)
        fake_budget.updated_at = None
        # BudgetResponse is built with from_attributes, so every response field
        # must be set explicitly: an unset attribute on a MagicMock resolves to
        # a child mock and fails validation.
        fake_budget.notification_owner_email = None
        fake_budget.created_by_name = None
        mock_service.get_budget = AsyncMock(return_value=fake_budget)
        mock_service.resolve_creator_names = AsyncMock(return_value={})

        auditor = _make_auditor()
        result = await get_budget(budgetId="b1", user=auditor)
        assert result is not None


class TestProjectBudgetRouterAuditor:
    def test_can_read_project_budget_returns_true_for_auditor(self):
        from codemie.rest_api.routers.project_budget_router import _can_read_project_budget

        auditor = _make_auditor()
        assert _can_read_project_budget(auditor, "some-project") is True

    @patch("codemie.rest_api.routers.project_budget_router.get_async_session")
    @patch("codemie.rest_api.routers.project_budget_router.project_budget_service")
    @pytest.mark.anyio
    async def test_auditor_can_list_project_budgets(self, mock_service, mock_session_ctx):
        from codemie.rest_api.routers.project_budget_router import list_project_budgets

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_ctx.return_value = mock_session
        mock_service.list_project_budgets = AsyncMock(return_value=([], 0))

        auditor = _make_auditor()
        result = await list_project_budgets(project_name=None, category=None, page=0, per_page=20, user=auditor)
        assert result is not None
