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

"""The shared project budget row: its amounts and contents have one owner (EPMCDME-17061)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.budget.budget_enums import AllocationMode, BudgetType
from codemie.service.budget.budget_models import (
    Budget,
    EqualMemberShare,
    ProjectMemberBudgetAssignment,
    build_shared_project_budget_row,
    shared_project_budget_amounts,
)
from codemie.service.budget.project_budget_service import ProjectBudgetService
from codemie.service.settings.settings import SettingsService

_REPOSITORY = "codemie.service.budget.project_budget_service.budget_repository"
_MEMBER_REPOSITORY = "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository"


def _main_budget() -> Budget:
    return Budget(
        budget_id="b1",
        budget_type=BudgetType.PROJECT.value,
        project_name="p",
        name="p-platform",
        description="Platform budget",
        soft_budget=80.0,
        max_budget=100.0,
        budget_duration="30d",
        budget_category="platform",
        created_by="admin",
    )


def _allocation(allocation_mode: str = AllocationMode.EQUAL.value) -> ProjectMemberBudgetAssignment:
    return ProjectMemberBudgetAssignment(
        project_name="p",
        budget_category="platform",
        project_budget_id="b1",
        user_id="u0",
        allocation_mode=allocation_mode,
        allocated_soft_budget=20.0,
        allocated_max_budget=25.0,
    )


def _row(actor_id: str | None = "actor-1") -> dict:
    return build_shared_project_budget_row(
        _main_budget(), project_name="p", actor_id=actor_id, soft_budget=20.0, max_budget=25.0
    )


def test_amounts_without_equal_share_are_the_main_budget():
    assert shared_project_budget_amounts(_main_budget(), None, enforce_limit=True) == (80.0, 100.0)


@pytest.mark.parametrize("equal_share", [_allocation(), EqualMemberShare(20.0, 25.0)], ids=["allocation", "share"])
@pytest.mark.parametrize(("enforce_limit", "expected"), [(True, (20.0, 25.0)), (False, (20.0, 100.0))])
def test_amounts_follow_the_member_hard_limit_rule(equal_share, enforce_limit, expected):
    assert shared_project_budget_amounts(_main_budget(), equal_share, enforce_limit) == expected


def test_amounts_apply_the_equal_rule_to_a_fixed_mode_sample():
    # The shared row serves equal members, so a fixed-mode sample row must not cap it when unenforced.
    assert shared_project_budget_amounts(_main_budget(), _allocation(AllocationMode.FIXED.value), False) == (
        20.0,
        100.0,
    )


def test_row_carries_the_main_budget_fields_and_the_actor():
    assert _row() == {
        "budget_id": "b1:shared",
        "created_by": "actor-1",
        "budget_type": BudgetType.PROJECT.value,
        "budget_origin_type": "shared_default",
        "parent_budget_id": "b1",
        "owner_user_id": None,
        "project_name": "p",
        "name": "p-platform [shared]",
        "description": "Platform budget",
        "soft_budget": 20.0,
        "max_budget": 25.0,
        "budget_duration": "30d",
        "budget_category": "platform",
        "detached_at": None,
        "deleted_at": None,
    }


def test_row_without_actor_is_created_by_the_main_budget_creator():
    assert _row(actor_id=None)["created_by"] == "admin"


async def _ensure_shared_child_budget(session) -> None:
    await ProjectBudgetService()._ensure_shared_child_budget(
        session,
        main_budget=_main_budget(),
        project_name="p",
        actor_id="actor-1",
        per_member_soft_budget=20.0,
        per_member_max_budget=25.0,
    )


@pytest.mark.asyncio
async def test_ensure_shared_child_budget_inserts_the_row_when_missing():
    session = MagicMock()
    with patch(_REPOSITORY) as repository:
        repository.get_by_id = AsyncMock(return_value=None)
        repository.insert = AsyncMock()
        await _ensure_shared_child_budget(session)

    inserted = repository.insert.await_args.args[1]
    assert isinstance(inserted, Budget)
    assert {key: getattr(inserted, key) for key in _row()} == _row()


@pytest.mark.asyncio
async def test_ensure_shared_child_budget_updates_an_existing_row_except_identity_and_creator():
    session = MagicMock()
    with patch(_REPOSITORY) as repository:
        repository.get_by_id = AsyncMock(return_value=MagicMock())
        repository.update = AsyncMock()
        await _ensure_shared_child_budget(session)

    expected = {key: value for key, value in _row().items() if key not in {"budget_id", "created_by"}}
    repository.update.assert_awaited_once_with(session, "b1:shared", expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enforce_limit", "resync_sample", "expected_sample"),
    [(False, (60.0, 20.0), (100.0, 20.0)), (True, (25.0, 20.0), (25.0, 20.0))],
    ids=["unenforced", "enforced"],
)
async def test_resync_applies_the_amount_rule_to_the_shared_sample(enforce_limit, resync_sample, expected_sample):
    # Samples are (max, soft); an unenforced shared row holds the main budget's max, not the resync's eff_max.
    service = ProjectBudgetService()
    session = MagicMock()
    budget = _main_budget()
    with (
        patch(f"{_MEMBER_REPOSITORY}.get_active_by_budget_id", new=AsyncMock(return_value=[_allocation()])),
        patch.object(SettingsService, "get_enforce_member_spend_limits", return_value=enforce_limit),
        patch.object(service, "_resync_member_allocation", new=AsyncMock(return_value=resync_sample)),
        patch.object(service, "_ensure_shared_child_budget_after_resync", new=AsyncMock()) as ensure_after_resync,
    ):
        await service._resync_member_allocations(
            session, "b1", budget, eff_max=60.0, eff_soft=50.0, provider=MagicMock()
        )

    ensure_after_resync.assert_awaited_once_with(session, budget_id="b1", budget=budget, shared_sample=expected_sample)
