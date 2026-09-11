# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.budget.inactive_project_budget_stop_service import (
    InactiveProjectBudgetStopService,
)
from codemie.service.activity.activity_models import BudgetManagementEvent


def test_project_budget_stopped_event_constant_exists():
    assert hasattr(BudgetManagementEvent, "PROJECT_BUDGET_STOPPED")
    assert BudgetManagementEvent.PROJECT_BUDGET_STOPPED == "budget.project_budget.stopped"


@pytest.mark.asyncio
async def test_run_stops_budgets_for_inactive_projects():
    session = AsyncMock()
    session.flush = AsyncMock()

    inactive_ids = ["proj-a", "proj-b"]
    budget_a = SimpleNamespace(budget_id="b-1", project_name="proj-a", is_active=True)
    budget_b = SimpleNamespace(budget_id="b-2", project_name="proj-b", is_active=True)

    mock_enrichment_repo = AsyncMock()
    mock_enrichment_repo.get_inactive_application_ids.return_value = inactive_ids

    mock_budget_repo = AsyncMock()
    mock_budget_repo.list_active_project_budgets.side_effect = lambda s, pid: (
        [budget_a] if pid == "proj-a" else [budget_b]
    )

    admin_email = "admin@example.com"
    mock_admin_result = MagicMock()
    mock_admin_result.scalars.return_value.all.return_value = [admin_email]
    session.execute.return_value = mock_admin_result

    mock_activity_repo = AsyncMock()
    mock_email_service = AsyncMock()
    mock_email_service.send_email.return_value = True

    with (
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.project_enrichment_repository",
            mock_enrichment_repo,
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.budget_repository",
            mock_budget_repo,
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.activity_event_repository",
            mock_activity_repo,
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.clear_budget_resolution_cache"
        ) as mock_clear_cache,
        patch(
            "codemie.service.email_service.email_service",
            mock_email_service,
        ),
    ):
        service = InactiveProjectBudgetStopService()
        await service.run(session)

    assert budget_a.is_active is False
    assert budget_b.is_active is False
    mock_clear_cache.assert_called_once()
    assert mock_activity_repo.async_insert.call_count == 2
    assert mock_email_service.send_email.call_count >= 1


@pytest.mark.asyncio
async def test_run_does_nothing_when_no_inactive_projects():
    session = AsyncMock()

    mock_enrichment_repo = AsyncMock()
    mock_enrichment_repo.get_inactive_application_ids.return_value = []

    mock_budget_repo = AsyncMock()
    mock_activity_repo = AsyncMock()
    mock_clear_cache = MagicMock()
    mock_email_service = AsyncMock()

    with (
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.project_enrichment_repository",
            mock_enrichment_repo,
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.budget_repository",
            mock_budget_repo,
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.activity_event_repository",
            mock_activity_repo,
        ),
        patch(
            "codemie.service.budget.inactive_project_budget_stop_service.clear_budget_resolution_cache",
            mock_clear_cache,
        ),
        patch(
            "codemie.service.email_service.email_service",
            mock_email_service,
        ),
    ):
        service = InactiveProjectBudgetStopService()
        await service.run(session)

    mock_budget_repo.list_active_project_budgets.assert_not_called()
    mock_clear_cache.assert_not_called()
    mock_activity_repo.async_insert.assert_not_called()
    mock_email_service.send_email.assert_not_called()
