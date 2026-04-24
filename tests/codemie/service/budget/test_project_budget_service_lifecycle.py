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
from unittest.mock import AsyncMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.budget.budget_enums import AllocationMode, SyncStatus
from codemie.service.budget.project_budget_service import ProjectBudgetService
from codemie.service.budget.provider import BudgetProviderMemberState, BudgetProviderState


@pytest.mark.asyncio
async def test_reset_project_budget_uses_ensure_when_provider_budget_ref_missing():
    service = ProjectBudgetService()
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        budget_category="cli",
        budget_duration="30d",
        budget_reset_at="2026-04-22T10:00:00Z",
        provider_metadata={"provider": "litellm", "sync_status": "ok", "raw": {"models": ["gpt-4.1"]}},
        soft_budget=20.0,
        max_budget=25.0,
    )
    updated_budget = SimpleNamespace(**budget.__dict__)
    assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")
    allocation = SimpleNamespace(id="alloc-1", user_id="user-1")
    provider = SimpleNamespace(
        ensure_project_budget=AsyncMock(
            return_value=BudgetProviderState(
                provider="litellm",
                provider_budget_ref="provider-budget-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
                metadata={"models": ["gpt-4.1"]},
            )
        ),
        update_project_budget=AsyncMock(),
        sync_member_allocation=AsyncMock(
            return_value=BudgetProviderMemberState(
                provider="litellm",
                provider_member_ref="member-ref-1",
                provider_budget_id="member-budget-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
                metadata={},
            )
        ),
    )

    with (
        patch.object(service, "get_project_budget", new=AsyncMock(return_value=(budget, assignment, [allocation]))),
        patch("codemie.service.budget.project_budget_service.get_active_provider", return_value=provider),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.update",
            new=AsyncMock(return_value=updated_budget),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ) as mock_update_metadata,
        patch.object(service, "_persist_child_budget_provider_state", new=AsyncMock()),
    ):
        await service.reset_project_budget(session=session, budget_id="proj-budget-1", actor_id="actor-1")

    provider.ensure_project_budget.assert_awaited_once()
    provider.update_project_budget.assert_not_awaited()
    assert provider.ensure_project_budget.await_args.kwargs["models"] == ["gpt-4.1"]
    assert mock_update_metadata.await_args.kwargs["provider_metadata"]["raw"]["provider_budget_id"] == "member-budget-1"


@pytest.mark.asyncio
async def test_delete_project_budget_marks_deleted_and_clears_resolution_cache():
    service = ProjectBudgetService()
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        budget_category="cli",
        budget_reset_at="2026-04-22T10:00:00Z",
        provider_metadata={"provider": "litellm", "provider_budget_ref": "provider-budget-1", "sync_status": "ok"},
    )
    assignment = SimpleNamespace(id="assignment-1", project_name="proj-a", budget_category="cli")
    allocations = [
        SimpleNamespace(id="alloc-1", user_id="user-1"),
        SimpleNamespace(id="alloc-2", user_id="user-2"),
    ]
    provider = SimpleNamespace(
        delete_member_allocation=AsyncMock(),
        delete_project_budget=AsyncMock(),
    )

    from codemie.service.budget.budget_resolution_service import _resolution_cache

    _resolution_cache.clear()
    _resolution_cache[("proj-a", "cli", "user-1")] = "cached-1"
    _resolution_cache[("proj-a", "cli", "user-2")] = "cached-2"
    _resolution_cache[("proj-b", "cli", "user-9")] = "cached-3"

    with (
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.get_by_id",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_by_budget_id",
            new=AsyncMock(return_value=assignment),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.get_active_by_budget_id",
            new=AsyncMock(return_value=allocations),
        ),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.list_active_child_budgets",
            new=AsyncMock(return_value=[]),
        ),
        patch("codemie.service.budget.project_budget_service.get_active_provider", return_value=provider),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.soft_delete_all_by_budget_id",
            new=AsyncMock(),
        ) as mock_soft_delete_allocations,
        patch(
            "codemie.service.budget.project_budget_service.project_budget_assignment_repository.soft_delete",
            new=AsyncMock(),
        ) as mock_soft_delete_assignment,
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.update",
            new=AsyncMock(),
        ) as mock_update_budget,
    ):
        await service.delete_project_budget(session=session, budget_id="proj-budget-1", actor_id="actor-1")

    mock_soft_delete_allocations.assert_awaited_once_with(session, "proj-budget-1")
    mock_soft_delete_assignment.assert_awaited_once_with(session, "assignment-1")
    assert ("proj-a", "cli", "user-1") not in _resolution_cache
    assert ("proj-a", "cli", "user-2") not in _resolution_cache
    assert ("proj-b", "cli", "user-9") in _resolution_cache
    update_fields = mock_update_budget.await_args.args[2]
    assert update_fields["provider_metadata"]["sync_status"] == "deleted"
    assert isinstance(update_fields["deleted_at"], datetime)
    assert update_fields["deleted_at"].tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_override_member_allocation_raises_404_when_member_missing():
    service = ProjectBudgetService()
    session = AsyncMock()

    with (
        patch.object(
            service, "get_project_budget", new=AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace(), []))
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_member_override",
            new=AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await service.override_member_allocation(
                session=session,
                budget_id="proj-budget-1",
                user_id="missing-user",
                allocated_max_budget=5.0,
                allocated_soft_budget=4.0,
                override_reason=None,
                actor_id="actor-1",
            )

    assert exc_info.value.code == 404


@pytest.mark.asyncio
async def test_clear_member_override_raises_404_when_member_missing():
    service = ProjectBudgetService()
    session = AsyncMock()

    with (
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.clear_member_override",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.get_by_id",
            new=AsyncMock(return_value=SimpleNamespace(budget_type="project")),
        ),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await service.clear_member_override(
                session=session,
                budget_id="proj-budget-1",
                user_id="missing-user",
                actor_id="actor-1",
            )

    assert exc_info.value.code == 404


@pytest.mark.asyncio
async def test_resync_member_allocations_raises_400_when_fixed_overrides_exceed_project_budget():
    service = ProjectBudgetService()
    session = AsyncMock()
    fixed_allocation = SimpleNamespace(
        id="alloc-1",
        user_id="user-1",
        allocation_mode=AllocationMode.FIXED.value,
        allocated_soft_budget=21.0,
        allocated_max_budget=26.0,
    )

    with patch(
        "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.get_active_by_budget_id",
        new=AsyncMock(return_value=[fixed_allocation]),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await service._resync_member_allocations(
                session=session,
                budget_id="proj-budget-1",
                budget=SimpleNamespace(),
                eff_max=25.0,
                eff_soft=20.0,
                provider=SimpleNamespace(),
            )

    assert exc_info.value.code == 400
    assert exc_info.value.message == "fixed overrides exceed project budget"
