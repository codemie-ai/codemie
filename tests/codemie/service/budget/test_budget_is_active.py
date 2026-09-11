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
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.budget_repository import BudgetRepository
from codemie.service.budget.budget_models import Budget


def test_budget_model_has_is_active_field():
    b = Budget(
        budget_id="b-1",
        budget_type="project",
        budget_origin_type="main",
        name="test",
        soft_budget=10.0,
        max_budget=20.0,
        budget_duration="30d",
        budget_category="global",
        created_by="user-1",
    )
    assert b.is_active is True


def test_budget_model_is_active_can_be_set_false():
    b = Budget(
        budget_id="b-2",
        budget_type="project",
        budget_origin_type="main",
        name="inactive",
        soft_budget=10.0,
        max_budget=20.0,
        budget_duration="30d",
        budget_category="global",
        created_by="user-1",
        is_active=False,
    )
    assert b.is_active is False


@pytest.mark.asyncio
async def test_list_active_project_budgets_returns_budgets():
    session = AsyncMock()
    budgets = [
        SimpleNamespace(budget_id="b-1", project_name="proj-a", is_active=True),
        SimpleNamespace(budget_id="b-2", project_name="proj-a", is_active=True),
    ]
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = budgets
    session.execute.return_value = execute_result

    repo = BudgetRepository()
    result = await repo.list_active_project_budgets(session, "proj-a")

    assert result == budgets


@pytest.mark.asyncio
async def test_list_active_project_budgets_returns_empty():
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    session.execute.return_value = execute_result

    repo = BudgetRepository()
    result = await repo.list_active_project_budgets(session, "proj-b")

    assert result == []
