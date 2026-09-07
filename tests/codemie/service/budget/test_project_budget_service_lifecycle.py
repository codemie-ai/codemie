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
async def test_reset_project_budget_uses_provider_reset_when_provider_budget_ref_missing():
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
        reset_project_budget_spend=AsyncMock(
            return_value=BudgetProviderState(
                provider="litellm",
                provider_budget_ref="provider-budget-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
                metadata={"models": ["gpt-4.1"]},
            )
        ),
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

    provider.reset_project_budget_spend.assert_awaited_once()
    reset_kwargs = provider.reset_project_budget_spend.await_args.kwargs
    assert reset_kwargs["budget_state"].provider_budget_ref is None
    assert reset_kwargs["models"] == ["gpt-4.1"]
    assert reset_kwargs["changed_by"] == "actor-1"
    assert mock_update_metadata.await_args.kwargs["provider_metadata"]["raw"]["provider_budget_id"] == "member-budget-1"


@pytest.mark.asyncio
async def test_reset_project_budget_member_spend_failure_marks_sync_status_failed():
    service = ProjectBudgetService()
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        budget_category="cli",
        budget_duration="30d",
        budget_reset_at="2026-04-22T10:00:00Z",
        provider_metadata={"provider": "litellm", "sync_status": "ok"},
        soft_budget=20.0,
        max_budget=25.0,
    )
    updated_budget = SimpleNamespace(**budget.__dict__)
    assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")
    allocation = SimpleNamespace(id="alloc-1", user_id="user-1")
    provider = SimpleNamespace(
        reset_project_budget_spend=AsyncMock(
            return_value=BudgetProviderState(
                provider="litellm",
                provider_budget_ref=None,
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
            )
        ),
        sync_member_allocation=AsyncMock(
            return_value=BudgetProviderMemberState(
                provider="litellm",
                provider_member_ref="member-ref-1",
                provider_budget_id="member-budget-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
            )
        ),
        reset_project_member_spending=AsyncMock(side_effect=RuntimeError("provider failure")),
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

    update_kwargs = mock_update_metadata.await_args.kwargs
    assert update_kwargs["sync_status"] == SyncStatus.FAILED


@pytest.mark.asyncio
async def test_reset_project_budget_skips_spend_reset_when_no_provider_ref():
    service = ProjectBudgetService()
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        budget_category="cli",
        budget_duration="30d",
        budget_reset_at="2026-04-22T10:00:00Z",
        provider_metadata={"provider": "litellm", "sync_status": "ok"},
        soft_budget=20.0,
        max_budget=25.0,
    )
    updated_budget = SimpleNamespace(**budget.__dict__)
    assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")
    allocation = SimpleNamespace(id="alloc-1", user_id="user-1")
    provider = SimpleNamespace(
        reset_project_budget_spend=AsyncMock(
            return_value=BudgetProviderState(
                provider="litellm",
                provider_budget_ref=None,
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
            )
        ),
        sync_member_allocation=AsyncMock(
            return_value=BudgetProviderMemberState(
                provider="litellm",
                provider_member_ref=None,
                provider_budget_id=None,
                budget_reset_at=None,
                sync_status=SyncStatus.OK,
            )
        ),
        reset_project_member_spending=AsyncMock(),
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
        ),
        patch.object(service, "_persist_child_budget_provider_state", new=AsyncMock()),
    ):
        await service.reset_project_budget(session=session, budget_id="proj-budget-1", actor_id="actor-1")

    provider.reset_project_member_spending.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_project_budget_persist_failure_continues_remaining_allocations():
    service = ProjectBudgetService()
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        budget_category="cli",
        budget_duration="30d",
        budget_reset_at="2026-04-22T10:00:00Z",
        provider_metadata={"provider": "litellm", "sync_status": "ok"},
        soft_budget=20.0,
        max_budget=25.0,
    )
    updated_budget = SimpleNamespace(**budget.__dict__)
    assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")
    allocation_1 = SimpleNamespace(id="alloc-1", user_id="user-1")
    allocation_2 = SimpleNamespace(id="alloc-2", user_id="user-2")
    provider = SimpleNamespace(
        reset_project_budget_spend=AsyncMock(
            return_value=BudgetProviderState(
                provider="litellm",
                provider_budget_ref=None,
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
            )
        ),
        sync_member_allocation=AsyncMock(
            return_value=BudgetProviderMemberState(
                provider="litellm",
                provider_member_ref="member-ref-1",
                provider_budget_id="member-budget-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
            )
        ),
        reset_project_member_spending=AsyncMock(),
    )

    with (
        patch.object(
            service,
            "get_project_budget",
            new=AsyncMock(return_value=(budget, assignment, [allocation_1, allocation_2])),
        ),
        patch("codemie.service.budget.project_budget_service.get_active_provider", return_value=provider),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.update",
            new=AsyncMock(return_value=updated_budget),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ) as mock_update_metadata,
        patch.object(
            service,
            "_persist_child_budget_provider_state",
            new=AsyncMock(side_effect=[RuntimeError("db error"), None]),
        ),
    ):
        await service.reset_project_budget(session=session, budget_id="proj-budget-1", actor_id="actor-1")

    assert provider.sync_member_allocation.await_count == 2
    assert mock_update_metadata.await_count == 1


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
        delete_override_budget=AsyncMock(),
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
    provider.delete_override_budget.assert_awaited_once_with(override_budget_id="proj-budget-1:shared")


@pytest.mark.asyncio
async def test_delete_project_budget_shared_budget_failure_does_not_block_soft_delete():
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
    allocations = []
    provider = SimpleNamespace(
        delete_member_allocation=AsyncMock(),
        delete_project_budget=AsyncMock(),
        delete_override_budget=AsyncMock(side_effect=RuntimeError("LiteLLM unavailable")),
    )

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
        ),
    ):
        await service.delete_project_budget(session=session, budget_id="proj-budget-1", actor_id="actor-1")

    mock_soft_delete_allocations.assert_awaited_once_with(session, "proj-budget-1")
    mock_soft_delete_assignment.assert_awaited_once_with(session, "assignment-1")


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
async def test_override_member_allocation_logs_on_success():
    """override_member_allocation emits a logger.info containing 'member_allocation_override_completed' on success."""
    service = ProjectBudgetService()
    session = AsyncMock()

    allocation = SimpleNamespace(
        id="alloc-1",
        user_id="user-a",
        shared_budget_id="proj-budget-1:shared",
        override_budget_id=None,
    )
    budget = SimpleNamespace(budget_id="proj-budget-1", budget_type="project", budget_category="cli")
    assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")

    with (
        patch.object(service, "get_project_budget", new=AsyncMock(return_value=(budget, assignment, [allocation]))),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_member_override",
            new=AsyncMock(return_value=allocation),
        ),
        patch.object(
            service,
            "_ensure_override_child_budget",
            new=AsyncMock(return_value=SimpleNamespace(budget_id="override-bud-1")),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_member_budget_routing",
            new=AsyncMock(return_value=allocation),
        ),
        patch.object(service, "rebalance_project_budget", new=AsyncMock()),
        patch(
            "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
            new=AsyncMock(),
        ),
        patch("codemie.service.budget.project_budget_service.logger") as mock_logger,
    ):
        await service.override_member_allocation(
            session=session,
            budget_id="proj-budget-1",
            user_id="user-a",
            allocated_max_budget=20.0,
            allocated_soft_budget=15.0,
            override_reason="manual",
            actor_id="actor-1",
        )

    mock_logger.info.assert_called_once()
    message = mock_logger.info.call_args[0][0]
    assert "member_allocation_override_completed" in message
    assert "project_name='proj-a'" in message
    assert "budget_category='cli'" in message
    assert "allocated_max_budget=20.0" in message


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


@pytest.mark.asyncio
async def test_clear_member_override_syncs_litellm_with_shared_child_value():
    """When shared child budget exists, provider receives its max_budget — not the stale override value."""
    service = ProjectBudgetService()
    session = AsyncMock()

    # Allocation returned by repo after mode is switched to equal.
    # allocated_max_budget is intentionally stale (50.0 — the old override value).
    allocation = SimpleNamespace(
        id="alloc-1",
        user_id="user-a",
        project_budget_id="proj-budget-1",
        project_name="proj-a",
        budget_category="cli",
        override_budget_id="proj-budget-1:user:user-a",
        shared_budget_id="proj-budget-1:shared",
        effective_budget_id="proj-budget-1:shared",
        allocation_mode=AllocationMode.EQUAL.value,
        allocated_max_budget=50.0,  # stale override value
        allocated_soft_budget=40.0,  # stale override value
        override_reason=None,
    )

    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        budget_category="cli",
        budget_duration="30d",
        budget_reset_at=None,
        max_budget=100.0,
        soft_budget=80.0,
    )
    assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")

    # Shared child budget holds the correct equal-split value from last rebalance.
    existing_shared_child = SimpleNamespace(
        budget_id="proj-budget-1:shared",
        max_budget=25.0,  # correct equal-split
        soft_budget=20.0,
    )

    shared_budget_result = SimpleNamespace(budget_id="proj-budget-1:shared")

    member_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="ref-1",
        provider_budget_id="provider-bud-1",
        sync_status=SyncStatus.OK,
    )

    mock_provider = SimpleNamespace(
        sync_member_allocation=AsyncMock(return_value=member_state),
        delete_override_budget=AsyncMock(),
    )

    with (
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.clear_member_override",
            new=AsyncMock(return_value=allocation),
        ),
        patch.object(
            service,
            "get_project_budget",
            new=AsyncMock(return_value=(budget, assignment, [])),
        ),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.get_by_id",
            new=AsyncMock(return_value=existing_shared_child),
        ),
        patch.object(
            service,
            "_ensure_shared_child_budget",
            new=AsyncMock(return_value=shared_budget_result),
        ) as mock_ensure_shared,
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.detach_budget",
            new=AsyncMock(),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_member_budget_routing",
            new=AsyncMock(return_value=allocation),
        ) as mock_routing,
        patch(
            "codemie.service.budget.project_budget_service.get_active_provider",
            return_value=mock_provider,
        ),
        patch.object(service, "_persist_child_budget_provider_state", new=AsyncMock()),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ),
        patch.object(service, "rebalance_project_budget", new=AsyncMock()) as mock_rebalance,
        patch(
            "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
            new=AsyncMock(),
        ),
        patch("codemie.service.budget.project_budget_service.logger") as mock_logger,
    ):
        await service.clear_member_override(
            session=session,
            budget_id="proj-budget-1",
            user_id="user-a",
            actor_id="actor-1",
        )

    mock_logger.info.assert_called_once()
    assert "member_allocation_override_cleared" in mock_logger.info.call_args[0][0]

    # Provider must receive the shared child value (25.0), NOT the stale override (50.0).
    mock_provider.sync_member_allocation.assert_awaited_once()
    call_kwargs = mock_provider.sync_member_allocation.await_args.kwargs
    assert (
        call_kwargs["effective_max_budget"] == 25.0
    ), f"expected effective_max_budget=25.0 (shared child value), got {call_kwargs.get('effective_max_budget')}"

    # _ensure_shared_child_budget must also use the correct value.
    mock_ensure_shared.assert_awaited_once()
    ensure_kwargs = mock_ensure_shared.await_args.kwargs
    assert ensure_kwargs["per_member_max_budget"] == 25.0
    assert ensure_kwargs["per_member_soft_budget"] == 20.0

    # override_budget_id must be cleared — not carried over after clearing.
    routing_kwargs = mock_routing.await_args.kwargs
    assert routing_kwargs["override_budget_id"] is None

    # override budget must be deleted from LiteLLM.
    mock_provider.delete_override_budget.assert_awaited_once_with(override_budget_id="proj-budget-1:user:user-a")

    # rebalance must be called to reset the cleared user's allocated_max_budget.
    mock_rebalance.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_member_override_falls_back_to_allocation_value_when_no_shared_child():
    """When no shared child budget exists yet, provider receives allocation.allocated_max_budget as fallback."""
    service = ProjectBudgetService()
    session = AsyncMock()

    allocation = SimpleNamespace(
        id="alloc-1",
        user_id="user-a",
        project_budget_id="proj-budget-1",
        project_name="proj-a",
        budget_category="cli",
        override_budget_id="proj-budget-1:user:user-a",
        shared_budget_id=None,
        effective_budget_id=None,
        allocation_mode=AllocationMode.EQUAL.value,
        allocated_max_budget=50.0,
        allocated_soft_budget=40.0,
        override_reason=None,
    )

    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        budget_category="cli",
        budget_duration="30d",
        budget_reset_at=None,
        max_budget=100.0,
        soft_budget=80.0,
    )
    assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")
    shared_budget_result = SimpleNamespace(budget_id="proj-budget-1:shared")

    member_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="ref-1",
        provider_budget_id="provider-bud-1",
        sync_status=SyncStatus.OK,
    )
    mock_provider = SimpleNamespace(
        sync_member_allocation=AsyncMock(return_value=member_state),
        delete_override_budget=AsyncMock(),
    )

    with (
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.clear_member_override",
            new=AsyncMock(return_value=allocation),
        ),
        patch.object(
            service,
            "get_project_budget",
            new=AsyncMock(return_value=(budget, assignment, [])),
        ),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.get_by_id",
            new=AsyncMock(return_value=None),  # no shared child exists
        ),
        patch.object(
            service,
            "_ensure_shared_child_budget",
            new=AsyncMock(return_value=shared_budget_result),
        ),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.detach_budget",
            new=AsyncMock(),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_member_budget_routing",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.service.budget.project_budget_service.get_active_provider",
            return_value=mock_provider,
        ),
        patch.object(service, "_persist_child_budget_provider_state", new=AsyncMock()),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ),
        patch.object(service, "rebalance_project_budget", new=AsyncMock()),
    ):
        await service.clear_member_override(
            session=session,
            budget_id="proj-budget-1",
            user_id="user-a",
            actor_id="actor-1",
        )

    # Fallback: must use allocation.allocated_max_budget when no shared child exists.
    call_kwargs = mock_provider.sync_member_allocation.await_args.kwargs
    assert call_kwargs["effective_max_budget"] == 50.0


@pytest.mark.asyncio
async def test_resync_member_allocation_syncs_litellm_for_equal_mode_members():
    """Equal-mode members must trigger provider.sync_member_allocation during rebalance (Gap B fix)."""
    service = ProjectBudgetService()
    session = AsyncMock()

    equal_alloc = SimpleNamespace(
        id="alloc-1",
        user_id="user-a",
        project_budget_id="proj-budget-1",
        project_name="proj-a",
        budget_category="cli",
        allocation_mode=AllocationMode.EQUAL.value,
        allocated_max_budget=25.0,
        allocated_soft_budget=20.0,
        effective_budget_id="proj-budget-1:shared",
        shared_budget_id="proj-budget-1:shared",
        override_budget_id=None,
    )

    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_duration="30d",
        budget_reset_at=None,
    )

    member_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="ref-1",
        provider_budget_id="provider-bud-1",
        sync_status=SyncStatus.OK,
    )
    mock_provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=member_state))

    with patch(
        "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_allocation",
        new=AsyncMock(return_value=equal_alloc),
    ):
        result = await service._resync_member_allocation(
            session=session,
            budget_id="proj-budget-1",
            budget=budget,
            alloc=equal_alloc,
            new_amounts=(25.0, 20.0),
            effective_max_budget=None,
            provider=mock_provider,
        )

    # Provider must be called for equal-mode members.
    mock_provider.sync_member_allocation.assert_awaited_once()
    call_kwargs = mock_provider.sync_member_allocation.await_args.kwargs
    assert call_kwargs["allocation"] is equal_alloc
    assert call_kwargs["budget"] is budget
    assert call_kwargs["effective_max_budget"] is None

    # Must still return amounts so _ensure_shared_child_budget_after_resync can run.
    assert result == (25.0, 20.0)


@pytest.mark.asyncio
async def test_update_project_budget_group_logs_on_scalar_only_edit():
    """A rename/duration-only group edit returns early but must still emit a completion log."""
    service = ProjectBudgetService()
    session = AsyncMock()

    group = SimpleNamespace(
        id="group-1",
        project_name="proj-a",
        deleted_at=None,
        budget_duration="30d",
    )
    data = SimpleNamespace(
        name="renamed-group",
        description=None,
        budget_duration="60d",
        total_amount=None,
        categories=None,
    )

    with (
        patch(
            "codemie.service.budget.project_budget_service.project_budget_group_repository.get_by_id",
            new=AsyncMock(return_value=group),
        ),
        patch.object(service, "_update_group_scalar_fields", new=AsyncMock()),
        patch.object(service, "_load_group_full_result", new=AsyncMock(return_value=SimpleNamespace())),
        patch("codemie.service.budget.project_budget_service.logger") as mock_logger,
    ):
        await service.update_project_budget_group(
            session=session,
            group_id="group-1",
            data=data,
            actor_id="actor-1",
        )

    mock_logger.info.assert_called_once()
    message = mock_logger.info.call_args[0][0]
    assert "project_budget_group_update_completed" in message
    assert "project_name='proj-a'" in message
    assert "updated_fields=['budget_duration', 'name']" in message
