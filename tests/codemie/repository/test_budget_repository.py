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

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from codemie.repository.budget_repository import BudgetRepository
from codemie.service.budget.budget_enums import BudgetCategory


@pytest.fixture
def repository() -> BudgetRepository:
    return BudgetRepository()


@pytest.mark.asyncio
async def test_get_all_keyed_by_id_returns_budget_map(repository: BudgetRepository):
    session = AsyncMock()
    budgets = [SimpleNamespace(budget_id="budget-1"), SimpleNamespace(budget_id="budget-2")]
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = budgets
    session.execute.return_value = execute_result

    result = await repository.get_all_keyed_by_id(session)

    assert result == {"budget-1": budgets[0], "budget-2": budgets[1]}


@pytest.mark.asyncio
async def test_get_user_id_by_identifier_returns_matching_id(repository: BudgetRepository):
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.first.return_value = "user-1"
    session.execute.return_value = execute_result

    result = await repository.get_user_id_by_identifier(session, "user@example.com")

    assert result == "user-1"


@pytest.mark.asyncio
async def test_get_active_by_name_looks_up_only_active_budgets(repository: BudgetRepository):
    session = AsyncMock()
    budget = SimpleNamespace(budget_id="budget-1")
    execute_result = MagicMock()
    execute_result.scalars.return_value.first.return_value = budget
    session.execute.return_value = execute_result

    result = await repository.get_active_by_name(session, "proj-a/cli")

    stmt = session.execute.await_args.args[0]
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    sql_text = " ".join(str(compiled).lower().split())
    assert result is budget
    assert "budgets.name = 'proj-a/cli'" in sql_text
    assert "budgets.deleted_at is null" in sql_text


@pytest.mark.asyncio
async def test_count_project_assignments_returns_count(repository: BudgetRepository):
    session = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one.return_value = 3
    session.execute.return_value = execute_result

    result = await repository.count_project_assignments(session, "budget-1")

    assert result == 3


@pytest.mark.asyncio
async def test_list_paginated_supports_budget_type_filter(repository: BudgetRepository):
    session = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = 1
    data_result = MagicMock()
    data_result.scalars.return_value.all.return_value = [SimpleNamespace(budget_id="budget-1")]
    session.execute.side_effect = [count_result, data_result]

    rows, total = await repository.list_paginated(
        session=session,
        page=0,
        per_page=20,
        category="cli",
        budget_type="project",
    )

    assert total == 1
    assert [row.budget_id for row in rows] == ["budget-1"]


@pytest.mark.asyncio
async def test_upsert_from_provider_updates_provider_metadata(repository: BudgetRepository):
    session = AsyncMock()
    existing = SimpleNamespace(
        budget_id="budget-1",
        soft_budget=10.0,
        max_budget=20.0,
        budget_duration="30d",
        budget_reset_at=None,
        provider_metadata={"sync_status": "old"},
    )

    fields = {
        "soft_budget": 10.0,
        "max_budget": 20.0,
        "budget_duration": "30d",
        "budget_reset_at": None,
        "provider_metadata": {"sync_status": "ok"},
    }

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(repository, "get_by_id", AsyncMock(return_value=existing))
        result, status = await repository.upsert_from_provider(session, "budget-1", fields)

    assert status == "updated"
    assert result.provider_metadata == {"sync_status": "ok"}
    session.add.assert_called_once_with(existing)
    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once_with(existing)


@pytest.mark.asyncio
async def test_get_assignments_for_users_returns_empty_for_empty_input(repository: BudgetRepository):
    session = AsyncMock()

    result = await repository.get_assignments_for_users(session, [])

    assert result == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_assignments_for_users_groups_rows_by_user(repository: BudgetRepository):
    session = AsyncMock()
    rows = [
        SimpleNamespace(user_id="user-1", category=BudgetCategory.CLI.value),
        SimpleNamespace(user_id="user-1", category=BudgetCategory.PLATFORM.value),
        SimpleNamespace(user_id="user-2", category=BudgetCategory.CLI.value),
    ]
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = rows
    session.execute.return_value = execute_result

    result = await repository.get_assignments_for_users(session, ["user-1", "user-2"])

    assert result == {
        "user-1": [rows[0], rows[1]],
        "user-2": [rows[2]],
    }


def _sync_session(selected_ids: list[str]) -> MagicMock:
    session = MagicMock()
    session.exec.return_value.all.return_value = selected_ids
    return session


def test_clear_project_on_deleted_budgets_returns_detached_ids(repository: BudgetRepository):
    session = _sync_session(["budget-1", "budget-2"])

    result = repository.clear_project_on_deleted_budgets(session, "my-project")

    assert result == ["budget-1", "budget-2"]


def test_clear_project_on_deleted_budgets_skips_update_when_nothing_matches(repository: BudgetRepository):
    session = _sync_session([])

    result = repository.clear_project_on_deleted_budgets(session, "my-project")

    assert result == []
    session.execute.assert_not_called()
    session.flush.assert_not_called()


def test_clear_project_on_deleted_budgets_only_selects_soft_deleted_rows(repository: BudgetRepository):
    session = _sync_session(["budget-1"])

    repository.clear_project_on_deleted_budgets(session, "my-project")

    stmt = session.exec.call_args[0][0]
    sql_text = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "budgets" in sql_text
    assert "deleted_at is not null" in sql_text


def test_clear_project_on_deleted_budgets_nulls_project_name(repository: BudgetRepository):
    session = _sync_session(["budget-1"])

    repository.clear_project_on_deleted_budgets(session, "my-project")

    stmt = session.execute.call_args[0][0]
    sql_text = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "update budgets" in sql_text
    assert "project_name=null" in sql_text.replace(" ", "")


def test_clear_project_on_deleted_budgets_flushes_without_commit(repository: BudgetRepository):
    session = _sync_session(["budget-1"])

    repository.clear_project_on_deleted_budgets(session, "my-project")

    session.flush.assert_called_once()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_mark_user_category_spend_reset_updates_only_the_users_row_for_that_category(
    repository: BudgetRepository,
):
    session = AsyncMock()
    reset_at = datetime(2026, 11, 2, 10, 30, 0, tzinfo=timezone.utc)

    await repository.mark_user_category_spend_reset(session, "user-1", BudgetCategory.CLI, reset_at)

    stmt = session.execute.call_args[0][0]
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    sql_text = " ".join(str(compiled).lower().split())
    assert sql_text.startswith("update user_budget_assignments set spend_reset_at=")
    assert "2026-11-02 10:30:00" in sql_text
    assert "user_budget_assignments.user_id = 'user-1'" in sql_text
    assert f"user_budget_assignments.category = '{BudgetCategory.CLI.value}'" in sql_text


@pytest.mark.asyncio
async def test_mark_user_category_spend_reset_flushes_without_commit(repository: BudgetRepository):
    session = AsyncMock()

    await repository.mark_user_category_spend_reset(
        session, "user-1", BudgetCategory.CLI, datetime(2026, 11, 2, tzinfo=timezone.utc)
    )

    session.flush.assert_awaited_once()
    session.commit.assert_not_awaited()
