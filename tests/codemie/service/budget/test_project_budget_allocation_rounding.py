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

"""Regression tests for equal member budget splits with small budgets and large member counts."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.models.project_budget import ProjectBudgetCreateRequest
from codemie.service.budget.budget_enums import AllocationMode, BudgetCategory, SyncStatus
from codemie.service.budget.budget_models import Budget
from codemie.service.budget.project_budget_service import ProjectBudgetService
from codemie.service.budget.provider import BudgetProviderMemberState
from codemie.service.settings.settings import SettingsService

SERVICE_MODULE = "codemie.service.budget.project_budget_service"


def _allocation(user_id: str, mode: AllocationMode, soft: float, max_budget: float) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        allocation_mode=mode.value,
        allocated_soft_budget=soft,
        allocated_max_budget=max_budget,
    )


def _allocate(max_budget: float, soft_budget: float, member_count: int):
    return ProjectBudgetService._allocate_equal(
        user_ids=[f"u{i:04d}" for i in range(member_count)],
        max_budget=max_budget,
        soft_budget=soft_budget,
        budget_id="budget-1",
        project_name="project-1",
        budget_category="platform",
        allocation_mode=AllocationMode.EQUAL.value,
        assigned_by="admin",
    )


def _total(values) -> Decimal:
    return sum((Decimal(str(value)) for value in values), Decimal("0"))


@pytest.mark.parametrize(
    ("max_budget", "soft_budget", "member_count", "expected_max", "expected_soft"),
    [
        (1.0, 0.8, 70, 0.01, 0.01),
        (0.99, 0.79, 65, 0.01, 0.01),
        (1.09, 0.87, 70, 0.01, 0.01),
        (0.30, 0.24, 70, 0.0, 0.0),
        (100.0, 80.0, 7, 14.28, 11.42),
        (200.0, 160.0, 3, 66.66, 53.33),
        (1000.0, 800.0, 1000, 1.0, 0.8),
        (0.99, 0.79, 1, 0.99, 0.79),
    ],
)
def test_allocate_equal_gives_every_member_the_same_floored_share(
    max_budget, soft_budget, member_count, expected_max, expected_soft
):
    rows = _allocate(max_budget, soft_budget, member_count)

    assert len(rows) == member_count
    assert {row.allocated_max_budget for row in rows} == {expected_max}
    assert {row.allocated_soft_budget for row in rows} == {expected_soft}
    assert all(row.allocated_max_budget >= 0 and row.allocated_soft_budget >= 0 for row in rows)
    assert all(row.allocated_soft_budget <= row.allocated_max_budget for row in rows)
    assert _total(row.allocated_max_budget for row in rows) <= Decimal(str(max_budget))
    assert _total(row.allocated_soft_budget for row in rows) <= Decimal(str(soft_budget))


def test_allocate_equal_floors_sub_cent_amount():
    rows = _allocate(max_budget=3.3 * 30 / 100, soft_budget=0.5, member_count=2)

    assert [row.allocated_max_budget for row in rows] == [0.49, 0.49]


def test_allocate_equal_caps_soft_at_max():
    rows = _allocate(max_budget=0.5, soft_budget=5.0, member_count=1)

    assert rows[0].allocated_max_budget == 0.5
    assert rows[0].allocated_soft_budget == 0.5


def test_equal_amounts_uniform_with_fixed_overrides_leaving_less_than_a_cent_each():
    allocations = [_allocation("fixed-user", AllocationMode.FIXED, soft=5.0, max_budget=10.0)] + [
        _allocation(f"equal-{i}", AllocationMode.EQUAL, soft=0.0, max_budget=0.0) for i in range(3)
    ]

    result = ProjectBudgetService._equal_amounts_with_fixed_overrides(allocations, max_budget=10.01, soft_budget=8.0)

    assert result["fixed-user"] == (10.0, 5.0)
    assert {result[f"equal-{i}"] for i in range(3)} == {(0.0, 0.0)}


def test_equal_amounts_uniform_remaining_split_with_large_member_count():
    allocations = [_allocation("fixed-user", AllocationMode.FIXED, soft=0.5, max_budget=1.0)] + [
        _allocation(f"equal-{i:03d}", AllocationMode.EQUAL, soft=0.0, max_budget=0.0) for i in range(70)
    ]

    result = ProjectBudgetService._equal_amounts_with_fixed_overrides(allocations, max_budget=2.0, soft_budget=1.5)

    assert {result[f"equal-{i:03d}"] for i in range(70)} == {(0.01, 0.01)}
    assert _total(max_value for max_value, _ in result.values()) <= Decimal("2.0")
    assert _total(soft_value for _, soft_value in result.values()) <= Decimal("1.5")


def test_equal_amounts_soft_capped_at_max_with_fixed_overrides():
    allocations = [
        _allocation("fixed-user", AllocationMode.FIXED, soft=0.0, max_budget=9.0),
        _allocation("equal-user", AllocationMode.EQUAL, soft=0.0, max_budget=0.0),
    ]

    result = ProjectBudgetService._equal_amounts_with_fixed_overrides(allocations, max_budget=10.0, soft_budget=8.0)

    assert result["equal-user"] == (1.0, 1.0)


def _budget(**kwargs) -> Budget:
    defaults = {
        "budget_id": "proj-budget",
        "name": "Project Budget",
        "soft_budget": 0.8,
        "max_budget": 1.0,
        "budget_duration": "30d",
        "budget_category": BudgetCategory.PLATFORM.value,
        "created_by": "admin",
    }
    defaults.update(kwargs)
    return Budget(**defaults)


def _warning_messages(mock_logger) -> list[str]:
    return [str(call.args[0]) for call in mock_logger.warning.call_args_list]


async def _create_budget_with_members(max_budget: float, soft_budget: float, member_count: int):
    service = ProjectBudgetService()
    session = AsyncMock()
    created_budget = _budget(max_budget=max_budget, soft_budget=soft_budget)
    data = ProjectBudgetCreateRequest(
        budget_id="proj-budget",
        project_name="my-project",
        budget_category=BudgetCategory.PLATFORM,
        name="Project Budget",
        soft_budget=soft_budget,
        max_budget=max_budget,
        budget_duration="30d",
    )
    with (
        patch(f"{SERVICE_MODULE}.activity_event_repository") as mock_activity,
        patch(f"{SERVICE_MODULE}.get_active_provider", return_value=AsyncMock(provider_name="litellm")),
        patch(f"{SERVICE_MODULE}.project_member_budget_assignment_repository") as mock_member_repo,
        patch(f"{SERVICE_MODULE}.project_budget_assignment_repository") as mock_assignment_repo,
        patch(f"{SERVICE_MODULE}.budget_repository") as mock_budget_repo,
        patch(f"{SERVICE_MODULE}.logger") as mock_logger,
        patch.object(SettingsService, "get_enforce_member_spend_limits", return_value=True),
        patch.object(service, "_ensure_project_budget_eligible", AsyncMock()),
        patch.object(
            service,
            "_get_active_member_user_ids",
            AsyncMock(return_value=[f"u{i:04d}" for i in range(member_count)]),
        ),
        patch.object(
            service, "_ensure_shared_child_budget", AsyncMock(return_value=_budget(budget_id="shared-child"))
        ) as mock_shared_child,
        patch.object(service, "_sync_created_project_budget", AsyncMock(return_value=created_budget)),
    ):
        mock_activity.async_insert = AsyncMock()
        mock_budget_repo.get_by_name = AsyncMock(return_value=None)
        mock_budget_repo.insert = AsyncMock(return_value=created_budget)
        mock_budget_repo.update = AsyncMock(return_value=created_budget)
        mock_assignment_repo.get_active_by_project_category = AsyncMock(return_value=None)
        mock_assignment_repo.insert = AsyncMock(return_value=MagicMock(project_name="my-project"))
        mock_member_repo.insert_many = AsyncMock(side_effect=lambda _session, rows: rows)
        await service.create_project_budget(session, data, actor_id="admin")
    return mock_shared_child, mock_logger


@pytest.mark.asyncio
async def test_create_project_budget_seeds_shared_child_with_uniform_share():
    mock_shared_child, mock_logger = await _create_budget_with_members(1.0, 0.8, 70)

    kwargs = mock_shared_child.await_args.kwargs
    assert kwargs["per_member_max_budget"] == 0.01
    assert kwargs["per_member_soft_budget"] == 0.01
    assert not any("member_share_below_minimum" in message for message in _warning_messages(mock_logger))


@pytest.mark.asyncio
async def test_create_project_budget_logs_warning_when_equal_share_is_zero():
    mock_shared_child, mock_logger = await _create_budget_with_members(0.30, 0.24, 70)

    assert mock_shared_child.await_args.kwargs["per_member_max_budget"] == 0.0
    warnings = [message for message in _warning_messages(mock_logger) if "member_share_below_minimum" in message]
    assert len(warnings) == 1
    assert "zero_limit_member_count=70" in warnings[0]


def _apply_update(allocations):
    by_id = {allocation.id: allocation for allocation in allocations}

    async def _update(_session, allocation_id, allocated_max_budget, allocated_soft_budget):
        row = by_id[allocation_id]
        row.allocated_max_budget = allocated_max_budget
        row.allocated_soft_budget = allocated_soft_budget
        return row

    return _update


@pytest.mark.asyncio
async def test_resync_logs_warning_when_equal_share_is_zero():
    service = ProjectBudgetService()
    budget = SimpleNamespace(budget_id="proj-budget", budget_duration="30d", budget_reset_at=None)
    allocations = [
        SimpleNamespace(
            id=f"alloc-{i}",
            user_id=f"u{i:04d}",
            project_name="my-project",
            allocated_max_budget=0.01,
            allocated_soft_budget=0.01,
            allocation_mode=AllocationMode.EQUAL.value,
        )
        for i in range(70)
    ]
    provider = SimpleNamespace(
        sync_member_allocation=AsyncMock(
            return_value=BudgetProviderMemberState(
                provider="litellm",
                provider_member_ref="member-ref",
                provider_budget_id="member-budget",
                budget_reset_at=None,
                sync_status=SyncStatus.OK,
                metadata={},
            )
        )
    )
    with (
        patch(
            f"{SERVICE_MODULE}.project_member_budget_assignment_repository.get_active_by_budget_id",
            new=AsyncMock(return_value=allocations),
        ),
        patch(
            f"{SERVICE_MODULE}.project_budget_assignment_repository.get_active_by_budget_id",
            new=AsyncMock(return_value=SimpleNamespace(project_name="my-project")),
        ),
        patch(
            f"{SERVICE_MODULE}.project_member_budget_assignment_repository.update_allocation",
            new=AsyncMock(side_effect=_apply_update(allocations)),
        ),
        patch(
            f"{SERVICE_MODULE}.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ),
        patch.object(service, "_ensure_shared_child_budget", new=AsyncMock()) as mock_shared_child,
        patch.object(SettingsService, "get_enforce_member_spend_limits", return_value=True),
        patch(f"{SERVICE_MODULE}.logger") as mock_logger,
    ):
        await service._resync_member_allocations(
            session=AsyncMock(),
            budget_id="proj-budget",
            budget=budget,
            eff_max=0.30,
            eff_soft=0.24,
            provider=provider,
        )

    warnings = [message for message in _warning_messages(mock_logger) if "member_share_below_minimum" in message]
    assert len(warnings) == 1
    assert "zero_limit_member_count=70" in warnings[0]
    seeded = mock_shared_child.await_args.kwargs
    assert seeded["per_member_max_budget"] == 0.0
    assert seeded["per_member_soft_budget"] == 0.0


@pytest.mark.asyncio
async def test_backfilled_project_budget_logs_warning_when_equal_share_is_zero():
    from codemie.service.budget.budget_service import BudgetService

    project_service = ProjectBudgetService()
    service = BudgetService()
    session = MagicMock()
    session.begin_nested.return_value.__aenter__ = AsyncMock(return_value=None)
    session.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)
    state = SimpleNamespace(max_budget=0.30, soft_budget=0.24, budget_duration="30d", budget_reset_at=None)
    member_repo = SimpleNamespace(insert_many=AsyncMock(side_effect=lambda _session, rows: rows))
    with (
        patch(
            "codemie.service.budget.budget_service.budget_repository.insert", new=AsyncMock(side_effect=lambda _s, b: b)
        ),
        patch.object(SettingsService, "get_enforce_member_spend_limits", return_value=True),
        patch.object(
            project_service,
            "_get_active_member_user_ids",
            AsyncMock(return_value=[f"u{i:04d}" for i in range(70)]),
        ),
        patch.object(
            project_service, "_ensure_shared_child_budget", AsyncMock(return_value=_budget(budget_id="shared-child"))
        ) as mock_shared_child,
        patch.object(service, "_sync_backfilled_member_allocations", AsyncMock()),
        patch(f"{SERVICE_MODULE}.logger") as mock_logger,
    ):
        _, member_count = await service._create_backfilled_project_budget(
            session=session,
            provider=SimpleNamespace(provider_name="litellm"),
            project_name="my-project",
            category=BudgetCategory.PLATFORM.value,
            budget_ref="legacy-ref",
            state=state,
            actor_id="admin",
            project_budget_assignment_repository=SimpleNamespace(insert=AsyncMock()),
            project_member_budget_assignment_repository=member_repo,
            project_budget_service=project_service,
            project_budget_assignment_model=lambda **kwargs: SimpleNamespace(**kwargs),
        )

    assert member_count == 70
    assert mock_shared_child.await_args.kwargs["per_member_max_budget"] == 0.0
    warnings = [message for message in _warning_messages(mock_logger) if "member_share_below_minimum" in message]
    assert len(warnings) == 1
    assert "zero_limit_member_count=70" in warnings[0]


def _stored_allocation(user_id: str, mode: AllocationMode, max_budget: float, soft: float, budget_id: str):
    return SimpleNamespace(
        id=f"alloc-{user_id}",
        user_id=user_id,
        project_name="my-project",
        project_budget_id=budget_id,
        allocation_mode=mode.value,
        allocated_max_budget=max_budget,
        allocated_soft_budget=soft,
    )


async def _recompute(allocations, budget):
    service = ProjectBudgetService()
    session = AsyncMock()
    with (
        patch(
            f"{SERVICE_MODULE}.project_member_budget_assignment_repository.get_active_by_project",
            new=AsyncMock(return_value=allocations),
        ),
        patch(
            f"{SERVICE_MODULE}.project_member_budget_assignment_repository.update_allocation",
            new=AsyncMock(),
        ) as mock_update,
        patch(f"{SERVICE_MODULE}.budget_repository.get_by_id", new=AsyncMock(return_value=budget)),
        patch.object(service, "_ensure_shared_child_budget_after_resync", new=AsyncMock()) as mock_shared,
        patch(f"{SERVICE_MODULE}.logger") as mock_logger,
    ):
        await service.recompute_equal_shares(session, "my-project")
    return session, mock_update, mock_shared, mock_logger


@pytest.mark.asyncio
async def test_recompute_equal_shares_heals_legacy_remainder_rows():
    budget = _budget(max_budget=0.99, soft_budget=0.79)
    allocations = [
        _stored_allocation(f"u{i:03d}", AllocationMode.EQUAL, 0.02, 0.02, budget.budget_id) for i in range(64)
    ] + [_stored_allocation("u064", AllocationMode.EQUAL, -0.31, -0.49, budget.budget_id)]

    session, mock_update, mock_shared, _ = await _recompute(allocations, budget)

    assert mock_update.await_count == 65
    assert {
        (call.kwargs["allocated_max_budget"], call.kwargs["allocated_soft_budget"])
        for call in mock_update.await_args_list
    } == {(0.01, 0.01)}
    mock_shared.assert_awaited_once_with(session, budget_id=budget.budget_id, budget=budget, shared_sample=(0.01, 0.01))


@pytest.mark.asyncio
async def test_recompute_equal_shares_noop_when_shares_already_uniform():
    budget = _budget(max_budget=1.0, soft_budget=0.8)
    allocations = [
        _stored_allocation(f"u{i:03d}", AllocationMode.EQUAL, 0.01, 0.01, budget.budget_id) for i in range(70)
    ]

    _, mock_update, _, _ = await _recompute(allocations, budget)

    mock_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_recompute_equal_shares_skips_budget_when_fixed_overrides_exceed_it():
    budget = _budget(max_budget=1.0, soft_budget=0.8)
    allocations = [
        _stored_allocation("fixed", AllocationMode.FIXED, 2.0, 0.5, budget.budget_id),
        _stored_allocation("equal", AllocationMode.EQUAL, 0.5, 0.5, budget.budget_id),
    ]

    _, mock_update, mock_shared, mock_logger = await _recompute(allocations, budget)

    mock_update.assert_not_awaited()
    mock_shared.assert_not_awaited()
    assert any("member_share_recompute_skipped" in message for message in _warning_messages(mock_logger))
