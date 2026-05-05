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

"""Tests for ProjectBudgetAssignmentRepository.get_project_budget_context."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.project_budget_repository import (
    ProjectBudgetAssignmentRepository,
    ProjectAssignedBudgetSummaryRow,
    ProjectMemberBudgetAssignmentRepository,
    ResetWindowMemberAllocationRow,
)


@pytest.mark.asyncio
async def test_get_project_budget_context_returns_none_when_no_rows():
    """Returns None when no matching assignment+allocation+budget exists."""
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.mappings.return_value.first.return_value = None
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_project_budget_context(
        session, project_name="proj", budget_category="platform", user_id="u1"
    )
    assert result is None


@pytest.mark.asyncio
async def test_get_project_budget_context_returns_context_when_row_found():
    """Returns ProjectBudgetContext populated from query row."""
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    row = {
        "budget_id": "budget-1",
        "allocation_id": "alloc-1",
        "budget_meta": {"provider": "litellm"},
        "member_meta": {"key": "val"},
    }
    result_mock = MagicMock()
    result_mock.mappings.return_value.first.return_value = row
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_project_budget_context(
        session, project_name="proj", budget_category="platform", user_id="u1"
    )
    assert result is not None
    assert result.budget_id == "budget-1"
    assert result.allocation_id == "alloc-1"
    assert result.budget_provider_metadata == {"provider": "litellm"}
    assert result.member_provider_metadata == {"key": "val"}


@pytest.mark.asyncio
async def test_get_project_budget_categories_batch_returns_rows_for_requested_categories():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.mappings.return_value.all.return_value = [
        {
            "budget_category": "platform",
            "budget_id": "budget-platform",
            "allocation_id": "alloc-platform",
            "effective_budget_id": "budget-platform",
            "shared_budget_id": "budget-platform",
            "override_budget_id": None,
            "budget_meta": {"provider": "litellm"},
            "member_meta": {"provider_budget_id": "budget-platform"},
        },
        {
            "budget_category": "cli",
            "budget_id": "budget-cli",
            "allocation_id": "alloc-cli",
            "effective_budget_id": "budget-cli",
            "shared_budget_id": "budget-cli",
            "override_budget_id": None,
            "budget_meta": {"provider": "litellm"},
            "member_meta": {"provider_budget_id": "budget-cli"},
        },
    ]
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_project_budget_categories_batch(
        session=session,
        project_name="proj-a",
        user_id="u1",
        categories=["platform", "cli", "premium_models"],
    )

    assert set(result.keys()) == {"platform", "cli"}
    assert result["platform"].budget_id == "budget-platform"
    assert result["cli"].allocation_id == "alloc-cli"


@pytest.mark.asyncio
async def test_get_active_for_projects_returns_empty_for_empty_input():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()

    result = await repo.get_active_for_projects(session, [])

    assert result == []
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_active_for_projects_returns_rows_for_projects():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    rows = [SimpleNamespace(id="assignment-1"), SimpleNamespace(id="assignment-2")]
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_active_for_projects(session, ["proj-a", "proj-b"])

    assert result == rows


@pytest.mark.asyncio
async def test_get_assigned_budget_summaries_for_projects_returns_empty_for_empty_input():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()

    result = await repo.get_assigned_budget_summaries_for_projects(session, [])

    assert result == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_assigned_budget_summaries_for_projects_groups_rows_by_project():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.mappings.return_value.all.return_value = [
        {
            "project_name": "proj-a",
            "budget_id": "budget-1",
            "name": "CLI Budget",
            "budget_category": "cli",
            "soft_budget": 20.0,
            "max_budget": 25.0,
            "budget_duration": "30d",
            "budget_reset_at": "2026-04-23T10:10:00Z",
            "provider_metadata": {"sync_status": "ok"},
            "current_spending": 7.5,
            "member_count": 2,
            "allocated_member_budget_total": 50.0,
        }
    ]
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_assigned_budget_summaries_for_projects(session, ["proj-a"], budget_category="cli")

    assert result == {
        "proj-a": [
            ProjectAssignedBudgetSummaryRow(
                project_name="proj-a",
                budget_id="budget-1",
                name="CLI Budget",
                budget_category="cli",
                soft_budget=20.0,
                max_budget=25.0,
                budget_duration="30d",
                budget_reset_at="2026-04-23T10:10:00Z",
                provider_sync_status="ok",
                member_count=2,
                allocated_member_budget_total=50.0,
                current_spending=7.5,
            )
        ]
    }


@pytest.mark.asyncio
async def test_insert_returns_inserted_assignment():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    assignment = SimpleNamespace(id="assignment-1")

    result = await repo.insert(session, assignment)

    assert result is assignment
    session.add.assert_called_once_with(assignment)
    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once_with(assignment)


@pytest.mark.asyncio
async def test_get_active_by_project_category_returns_first_row():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    assignment = SimpleNamespace(id="assignment-1")
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = assignment
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_active_by_project_category(session, "proj-a", "cli")

    assert result is assignment


@pytest.mark.asyncio
async def test_get_active_by_budget_id_returns_first_row():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    assignment = SimpleNamespace(id="assignment-1")
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = assignment
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_active_by_budget_id(session, "budget-1")

    assert result is assignment


@pytest.mark.asyncio
async def test_get_active_for_project_returns_all_rows():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    assignments = [SimpleNamespace(id="assignment-1"), SimpleNamespace(id="assignment-2")]
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = assignments
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_active_for_project(session, "proj-a")

    assert result == assignments


@pytest.mark.asyncio
async def test_soft_delete_by_user_marks_all_matching_allocations_deleted():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    rows = [MagicMock(deleted_at=None), MagicMock(deleted_at=None)]
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result_mock)

    deleted_count = await repo.soft_delete_by_user(session, "proj-a", "cli", "user-1")

    assert deleted_count == 2
    assert rows[0].deleted_at is not None
    assert rows[1].deleted_at is not None
    assert session.add.call_count == 2
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_soft_delete_marks_assignment_deleted_when_found():
    repo = ProjectBudgetAssignmentRepository()
    session = AsyncMock()
    row = SimpleNamespace(deleted_at=None)
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = row
    session.execute = AsyncMock(return_value=result_mock)

    await repo.soft_delete(session, "assignment-1")

    assert row.deleted_at is not None
    session.add.assert_called_once_with(row)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_insert_many_returns_rows():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    rows = [SimpleNamespace(id="row-1"), SimpleNamespace(id="row-2")]

    result = await repo.insert_many(session, rows)

    assert result == rows
    assert session.add.call_count == 2
    assert session.refresh.await_count == 2


@pytest.mark.asyncio
async def test_get_active_member_rows_for_project_category_returns_rows():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    rows = [SimpleNamespace(user_id="user-1")]
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_active_by_project_category(session, "proj-a", "cli")

    assert result == rows


@pytest.mark.asyncio
async def test_get_active_by_project_category_user_returns_row():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    row = SimpleNamespace(user_id="user-1")
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = row
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_active_by_project_category_user(session, "proj-a", "cli", "user-1")

    assert result is row


@pytest.mark.asyncio
async def test_get_active_member_rows_by_budget_id_returns_rows():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    rows = [SimpleNamespace(user_id="user-1")]
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.get_active_by_budget_id(session, "budget-1")

    assert result == rows


@pytest.mark.asyncio
async def test_update_provider_metadata_updates_row_and_budget_reset_at():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    row = SimpleNamespace(provider_metadata={}, sync_status=None, budget_reset_at=None)
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = row
    session.execute = AsyncMock(return_value=result_mock)

    await repo.update_provider_metadata(
        session,
        allocation_id="alloc-1",
        provider_metadata={"provider": "litellm"},
        sync_status="ok",
        budget_reset_at="2026-04-23T10:10:00Z",
    )

    assert row.provider_metadata == {"provider": "litellm"}
    assert row.sync_status == "ok"
    assert row.budget_reset_at == "2026-04-23T10:10:00Z"
    session.add.assert_called_once_with(row)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_allocation_returns_updated_row():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    row = SimpleNamespace(allocated_max_budget=0.0, allocated_soft_budget=0.0)
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = row
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.update_allocation(session, "alloc-1", 25.0, 20.0)

    assert result is row
    assert row.allocated_max_budget == 25.0
    assert row.allocated_soft_budget == 20.0
    session.refresh.assert_awaited_once_with(row)


@pytest.mark.asyncio
async def test_update_member_override_returns_none_when_missing():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = None
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.update_member_override(session, "budget-1", "user-1", 25.0, 20.0, None, "admin-1")

    assert result is None


@pytest.mark.asyncio
async def test_update_member_override_sets_fixed_mode():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    row = SimpleNamespace(
        allocation_mode="equal",
        allocated_max_budget=0.0,
        allocated_soft_budget=0.0,
        override_reason=None,
        assigned_by=None,
    )
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = row
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.update_member_override(
        session,
        "budget-1",
        "user-1",
        25.0,
        20.0,
        "manual override",
        "admin-1",
    )

    assert result is row
    assert row.allocation_mode == "fixed"
    assert row.override_reason == "manual override"
    assert row.assigned_by == "admin-1"


@pytest.mark.asyncio
async def test_clear_member_override_returns_none_when_missing():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = None
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.clear_member_override(session, "budget-1", "user-1")

    assert result is None


@pytest.mark.asyncio
async def test_clear_member_override_restores_equal_mode():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    row = SimpleNamespace(allocation_mode="fixed", override_reason="manual")
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = row
    session.execute = AsyncMock(return_value=result_mock)

    result = await repo.clear_member_override(session, "budget-1", "user-1")

    assert result is row
    assert row.allocation_mode == "equal"
    assert row.override_reason is None


@pytest.mark.asyncio
async def test_soft_delete_missing_members_soft_deletes_only_removed_users():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    retained = SimpleNamespace(user_id="user-1", deleted_at=None)
    removed = SimpleNamespace(user_id="user-2", deleted_at=None)
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [retained, removed]
    session.execute = AsyncMock(return_value=result_mock)

    deleted_count = await repo.soft_delete_missing_members(session, "proj-a", "cli", ["user-1"])

    assert deleted_count == 1
    assert retained.deleted_at is None
    assert removed.deleted_at is not None
    session.add.assert_called_once_with(removed)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_soft_delete_all_by_budget_id_marks_all_rows_deleted():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    rows = [SimpleNamespace(deleted_at=None), SimpleNamespace(deleted_at=None)]
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result_mock)

    deleted_count = await repo.soft_delete_all_by_budget_id(session, "budget-1")

    assert deleted_count == 2
    assert rows[0].deleted_at is not None
    assert rows[1].deleted_at is not None
    assert session.add.call_count == 2
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_allocations_resetting_within_window_returns_typed_rows():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.mappings.return_value.all.return_value = [
        {
            "allocation_id": "alloc-1",
            "project_name": "proj-a",
            "budget_id": "budget-1",
            "budget_category": "cli",
            "user_id": "user-1",
            "provider_metadata": {"provider_member_ref": "member-ref-1"},
            "budget_reset_at": "2026-04-23T10:10:00Z",
        }
    ]
    session.execute = AsyncMock(return_value=result_mock)

    rows = await repo.get_allocations_resetting_within_window(
        session=session,
        window_start=datetime(2026, 4, 23, 10, 0, tzinfo=UTC),
        window_end=datetime(2026, 4, 23, 10, 15, tzinfo=UTC),
    )

    assert rows == [
        ResetWindowMemberAllocationRow(
            allocation_id="alloc-1",
            project_name="proj-a",
            budget_id="budget-1",
            budget_category="cli",
            user_id="user-1",
            provider_metadata={"provider_member_ref": "member-ref-1"},
            budget_reset_at="2026-04-23T10:10:00Z",
        )
    ]


@pytest.mark.asyncio
async def test_get_allocations_resetting_within_window_uses_window_bounds():
    repo = ProjectMemberBudgetAssignmentRepository()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.mappings.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result_mock)
    window_start = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)
    window_end = datetime(2026, 4, 23, 10, 15, tzinfo=UTC)

    await repo.get_allocations_resetting_within_window(
        session=session,
        window_start=window_start,
        window_end=window_end,
    )

    statement = session.execute.call_args.args[0]
    params = session.execute.call_args.args[1]
    sql_text = str(statement)
    assert "project_member_budget_assignments" in sql_text
    assert "budgets" in sql_text
    assert "budget_reset_at" in sql_text
    assert params == {"window_start": window_start, "window_end": window_end}
