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
from unittest.mock import AsyncMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.budget.budget_enums import SyncStatus
from codemie.service.budget.project_budget_service import ProjectBudgetService
from codemie.service.budget.provider import BudgetProviderMemberState, BudgetProviderState
from codemie.service.settings.settings import SettingsService


@pytest.mark.asyncio
async def test_resync_member_allocations_updates_shared_child_budget_for_equal_members():
    service = ProjectBudgetService()
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_duration="30d",
        budget_reset_at="2026-04-22T10:00:00Z",
    )
    allocation = SimpleNamespace(
        id="alloc-1",
        user_id="user-1",
        project_name="proj-a",
        allocated_max_budget=25.0,
        allocated_soft_budget=20.0,
        allocation_mode="equal",
    )
    provider = SimpleNamespace(
        sync_member_allocation=AsyncMock(
            return_value=BudgetProviderMemberState(
                provider="litellm",
                provider_member_ref="member-ref-1",
                provider_budget_id="member-budget-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
                metadata={"internal_budget": True},
            )
        )
    )

    with (
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.get_active_by_budget_id",
            new=AsyncMock(return_value=[allocation]),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_by_budget_id",
            new=AsyncMock(return_value=SimpleNamespace(project_name="proj-a")),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_allocation",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ) as mock_update_metadata,
        patch.object(service, "_ensure_shared_child_budget", new=AsyncMock()) as mock_ensure_shared_child_budget,
        patch.object(SettingsService, "get_enforce_member_spend_limits", return_value=True),
    ):
        await service._resync_member_allocations(
            session=session,
            budget_id="proj-budget-1",
            budget=budget,
            eff_max=25.0,
            eff_soft=20.0,
            provider=provider,
        )

    call_kwargs = provider.sync_member_allocation.await_args.kwargs
    assert call_kwargs["effective_max_budget"] is None
    mock_update_metadata.assert_not_awaited()
    mock_ensure_shared_child_budget.assert_awaited_once_with(
        session,
        main_budget=budget,
        project_name="proj-a",
        actor_id="system",
        per_member_soft_budget=20.0,
        per_member_max_budget=25.0,
    )


@pytest.mark.asyncio
async def test_sync_created_project_budget_uses_updated_budget_for_member_sync():
    service = ProjectBudgetService()
    session = AsyncMock()
    created_budget = SimpleNamespace(budget_id="proj-budget-1")
    updated_budget = SimpleNamespace(budget_id="proj-budget-1", budget_reset_at="2026-04-22T10:00:00Z")
    provider = SimpleNamespace(
        ensure_project_budget=AsyncMock(
            return_value=BudgetProviderState(
                provider="litellm",
                provider_budget_ref="provider-budget-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
                metadata={"source": "test"},
            )
        )
    )
    allocations = [SimpleNamespace(id="alloc-1")]

    with (
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.update",
            new=AsyncMock(return_value=updated_budget),
        ),
        patch.object(service, "_sync_created_member_allocations", new=AsyncMock()) as mock_sync_members,
    ):
        result = await service._sync_created_project_budget(
            session=session,
            provider=provider,
            created_budget=created_budget,
            budget_id="proj-budget-1",
            project_name="proj-a",
            budget_category=SimpleNamespace(value="cli"),
            soft_budget=20.0,
            max_budget=25.0,
            budget_duration="30d",
            models=["gpt-4.1"],
            allocations=allocations,
            enforce_limit=True,
        )

    assert result is updated_budget
    mock_sync_members.assert_awaited_once_with(
        session=session,
        provider=provider,
        budget=updated_budget,
        allocations=allocations,
        enforce_limit=True,
    )


def test_changed_project_budget_fields_includes_only_provided_values():
    request = SimpleNamespace(
        name="Updated",
        description=None,
        soft_budget=10.0,
        max_budget=None,
        budget_duration="30d",
    )

    fields = ProjectBudgetService._changed_project_budget_fields(request)

    assert fields == {"name": "Updated", "soft_budget": 10.0, "budget_duration": "30d"}


def test_effective_project_budget_values_fall_back_to_existing_budget_values():
    budget = SimpleNamespace(soft_budget=5.0, max_budget=20.0, budget_duration="7d")
    request = SimpleNamespace(soft_budget=None, max_budget=25.0, budget_duration=None)

    eff_soft, eff_max, eff_duration, amounts_changed = ProjectBudgetService._effective_project_budget_values(
        budget, request
    )

    assert (eff_soft, eff_max, eff_duration, amounts_changed) == (5.0, 25.0, "7d", True)


def test_validate_allocation_mode_rejects_invalid_value():
    with pytest.raises(ExtendedHTTPException) as exc_info:
        ProjectBudgetService._validate_allocation_mode("weighted")

    assert "allocation_mode must be one of" in exc_info.value.message


@pytest.mark.asyncio
async def test_sync_created_member_allocations_marks_failures():
    service = ProjectBudgetService()
    session = AsyncMock()
    allocation = SimpleNamespace(id="alloc-1", user_id="user-1", allocated_max_budget=25.0)
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(side_effect=RuntimeError("sync failed")))

    with patch(
        "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
        new=AsyncMock(),
    ) as mock_update_metadata:
        await service._sync_created_member_allocations(
            session=session,
            provider=provider,
            budget=SimpleNamespace(max_budget=25.0),
            allocations=[allocation],
            enforce_limit=True,
        )

    assert mock_update_metadata.await_args.kwargs["sync_status"] == SyncStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enforce_limit, expected_effective_max, expected_per_member_max",
    [
        (True, None, 500.0),
        (False, 2000.0, 2000.0),
    ],
)
async def test_resync_member_allocations_respects_enforce_limit(
    enforce_limit, expected_effective_max, expected_per_member_max
):
    """Bug scenario: 4-member project, parent=2000, per-member slice=500.
    enforce_limit=True  → provider and :shared DB record both receive 500.
    enforce_limit=False → provider and :shared DB record both receive 2000.
    """
    service = ProjectBudgetService()
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        max_budget=2000.0,
        budget_duration="30d",
        budget_reset_at="2026-04-22T10:00:00Z",
        created_by="system",
    )
    allocation = SimpleNamespace(
        id="alloc-1",
        user_id="user-1",
        project_name="proj-a",
        allocated_max_budget=500.0,
        allocated_soft_budget=400.0,
        allocation_mode="equal",
    )
    provider = SimpleNamespace(
        sync_member_allocation=AsyncMock(
            return_value=BudgetProviderMemberState(
                provider="litellm",
                provider_member_ref="member-ref-1",
                provider_budget_id="member-budget-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
                metadata={"internal_budget": True},
            )
        )
    )

    with (
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.get_active_by_budget_id",
            new=AsyncMock(return_value=[allocation]),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_by_budget_id",
            new=AsyncMock(return_value=SimpleNamespace(project_name="proj-a")),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_allocation",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ),
        patch.object(service, "_ensure_shared_child_budget", new=AsyncMock()) as mock_ensure_shared,
        patch.object(SettingsService, "get_enforce_member_spend_limits", return_value=enforce_limit),
    ):
        await service._resync_member_allocations(
            session=session,
            budget_id="proj-budget-1",
            budget=budget,
            eff_max=2000.0,
            eff_soft=1600.0,
            provider=provider,
        )

    call_kwargs = provider.sync_member_allocation.await_args.kwargs
    assert call_kwargs["effective_max_budget"] == expected_effective_max
    mock_ensure_shared.assert_awaited_once()
    ensure_kwargs = mock_ensure_shared.await_args.kwargs
    assert ensure_kwargs["per_member_max_budget"] == expected_per_member_max


@pytest.mark.asyncio
async def test_sync_created_member_allocations_uses_full_budget_when_enforce_limit_false():
    """When enforce_limit=False, sync_member_allocation must receive budget.max_budget, not the per-member slice."""
    service = ProjectBudgetService()
    session = AsyncMock()
    allocation = SimpleNamespace(
        id="alloc-1",
        user_id="user-1",
        allocated_max_budget=500.0,
    )
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        max_budget=2000.0,
        budget_duration="30d",
        budget_reset_at=None,
    )
    member_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="ref-1",
        provider_budget_id="bud-1",
        sync_status=SyncStatus.OK,
    )
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=member_state))

    with (
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ),
        patch.object(service, "_persist_child_budget_provider_state", new=AsyncMock()),
    ):
        await service._sync_created_member_allocations(
            session=session,
            provider=provider,
            budget=budget,
            allocations=[allocation],
            enforce_limit=False,
        )

    call_kwargs = provider.sync_member_allocation.await_args.kwargs
    assert call_kwargs["effective_max_budget"] == 2000.0


@pytest.mark.asyncio
async def test_reset_project_budget_persists_provider_budget_id_for_each_member():
    service = ProjectBudgetService()
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        budget_category="cli",
        budget_duration="30d",
        budget_reset_at="2026-04-22T10:00:00Z",
        provider_metadata={"provider": "litellm", "provider_budget_ref": "key-alias-1", "sync_status": "ok"},
        soft_budget=20.0,
        max_budget=25.0,
    )
    assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")
    allocation = SimpleNamespace(id="alloc-1", user_id="user-1")
    provider = SimpleNamespace(
        reset_project_budget_spend=AsyncMock(
            return_value=SimpleNamespace(
                provider="litellm",
                provider_budget_ref="key-alias-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
                metadata={},
            )
        ),
        sync_member_allocation=AsyncMock(
            return_value=BudgetProviderMemberState(
                provider="litellm",
                provider_member_ref="member-ref-1",
                provider_budget_id="member-budget-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
                metadata={"internal_budget": True},
            )
        ),
    )

    with (
        patch.object(service, "get_project_budget", new=AsyncMock(return_value=(budget, assignment, [allocation]))),
        patch("codemie.service.budget.project_budget_service.get_active_provider", return_value=provider),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.update",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ) as mock_update_metadata,
        patch.object(service, "_persist_child_budget_provider_state", new=AsyncMock()),
    ):
        await service.reset_project_budget(session=session, budget_id="proj-budget-1", actor_id="actor-1")

    provider.reset_project_budget_spend.assert_awaited_once()
    update_call = mock_update_metadata.await_args.kwargs
    assert update_call["provider_metadata"]["raw"]["provider_budget_id"] == "member-budget-1"


@pytest.mark.asyncio
async def test_reset_project_budget_resets_member_spend_for_each_member():
    service = ProjectBudgetService()
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        budget_category="cli",
        budget_duration="30d",
        budget_reset_at="2026-04-22T10:00:00Z",
        provider_metadata={"provider": "litellm", "provider_budget_ref": "key-alias-1", "sync_status": "ok"},
        soft_budget=20.0,
        max_budget=25.0,
    )
    assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")
    allocation1 = SimpleNamespace(id="alloc-1", user_id="user-1")
    allocation2 = SimpleNamespace(id="alloc-2", user_id="user-2")
    member_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="member-ref-1",
        provider_budget_id="member-budget-1",
        budget_reset_at="2026-04-22T10:00:00Z",
        sync_status=SyncStatus.OK,
        metadata={},
    )
    provider = SimpleNamespace(
        reset_project_budget_spend=AsyncMock(
            return_value=SimpleNamespace(
                provider="litellm",
                provider_budget_ref="key-alias-1",
                budget_reset_at="2026-04-22T10:00:00Z",
                sync_status=SyncStatus.OK,
                metadata={},
            )
        ),
        sync_member_allocation=AsyncMock(return_value=member_state),
        reset_project_member_spending=AsyncMock(),
    )

    with (
        patch.object(
            service,
            "get_project_budget",
            new=AsyncMock(return_value=(budget, assignment, [allocation1, allocation2])),
        ),
        patch("codemie.service.budget.project_budget_service.get_active_provider", return_value=provider),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.update",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ),
        patch.object(service, "_persist_child_budget_provider_state", new=AsyncMock()),
    ):
        await service.reset_project_budget(session=session, budget_id="proj-budget-1", actor_id="actor-1")

    assert provider.reset_project_member_spending.await_count == 2
    provider.reset_project_member_spending.assert_any_await(
        user_id="member-ref-1",
        budget_id="proj-budget-1:shared",
    )


# ── _legacy_carry_alias ─────────────────────────────────────────────────────


class FakeAssignment(SimpleNamespace):
    pass


class FakeBudget(SimpleNamespace):
    pass


def _make_assignment(budget_category: str, budget_id: str | None = None) -> FakeAssignment:
    return FakeAssignment(
        project_name="test-proj",
        budget_category=budget_category,
        budget_id=budget_id or f"bud-{budget_category}",
    )


def _make_budget(budget_id: str, provider_budget_ref: str | None) -> FakeBudget:
    meta = {"provider_budget_ref": provider_budget_ref} if provider_budget_ref else {}
    return FakeBudget(budget_id=budget_id, provider_metadata=meta)


def _budgets_by_id(*budgets: FakeBudget) -> dict:
    return {b.budget_id: b for b in budgets}


@pytest.mark.asyncio
async def test_legacy_carry_alias_no_assignments_returns_none():
    """No active assignments → no carry."""
    svc = ProjectBudgetService.__new__(ProjectBudgetService)
    session = AsyncMock()
    with patch(
        "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_for_project",
        new=AsyncMock(return_value=[]),
    ):
        result = await svc._legacy_carry_alias(session=session, project_name="proj", budget_category="platform")
    assert result is None


@pytest.mark.asyncio
async def test_legacy_carry_alias_all_canonical_returns_none():
    """All project budgets have canonical refs → no legacy key → no carry."""
    svc = ProjectBudgetService.__new__(ProjectBudgetService)
    session = AsyncMock()
    assignment = _make_assignment("platform", "bud-platform")
    budget = _make_budget("bud-platform", "codemie:project:proj:category:platform")
    with (
        patch(
            "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_for_project",
            new=AsyncMock(return_value=[assignment]),
        ),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.get_by_ids",
            new=AsyncMock(return_value=_budgets_by_id(budget)),
        ),
    ):
        result = await svc._legacy_carry_alias(session=session, project_name="proj", budget_category="platform")
    assert result is None


@pytest.mark.asyncio
async def test_legacy_carry_alias_single_category_matches():
    """Legacy ref, only CLI active → carry goes to CLI."""
    svc = ProjectBudgetService.__new__(ProjectBudgetService)
    session = AsyncMock()
    assignment = _make_assignment("cli", "bud-cli")
    budget = _make_budget("bud-cli", "proj-legacy-alias")
    with (
        patch(
            "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_for_project",
            new=AsyncMock(return_value=[assignment]),
        ),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.get_by_ids",
            new=AsyncMock(return_value=_budgets_by_id(budget)),
        ),
    ):
        result = await svc._legacy_carry_alias(session=session, project_name="proj", budget_category="cli")
    assert result == "proj-legacy-alias"


@pytest.mark.asyncio
async def test_legacy_carry_alias_platform_wins_over_cli():
    """Platform and CLI both active; legacy ref is on CLI budget → carry goes to platform."""
    svc = ProjectBudgetService.__new__(ProjectBudgetService)
    session = AsyncMock()
    assignments = [_make_assignment("platform", "bud-platform"), _make_assignment("cli", "bud-cli")]
    budgets = _budgets_by_id(
        _make_budget("bud-platform", "codemie:project:proj:category:platform"),
        _make_budget("bud-cli", "proj-old"),
    )
    with (
        patch(
            "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_for_project",
            new=AsyncMock(return_value=assignments),
        ),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.get_by_ids",
            new=AsyncMock(return_value=budgets),
        ),
    ):
        platform_result = await svc._legacy_carry_alias(
            session=session, project_name="proj", budget_category="platform"
        )
        cli_result = await svc._legacy_carry_alias(session=session, project_name="proj", budget_category="cli")
    assert platform_result == "proj-old"
    assert cli_result is None


@pytest.mark.asyncio
async def test_legacy_carry_alias_no_active_categories():
    """No active assignments → no carry."""
    svc = ProjectBudgetService.__new__(ProjectBudgetService)
    session = AsyncMock()
    with patch(
        "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_for_project",
        new=AsyncMock(return_value=[]),
    ):
        result = await svc._legacy_carry_alias(session=session, project_name="proj", budget_category="platform")
    assert result is None


@pytest.mark.asyncio
async def test_legacy_carry_alias_premium_fallback():
    """Only premium_models active with legacy ref → carry goes to premium_models."""
    svc = ProjectBudgetService.__new__(ProjectBudgetService)
    session = AsyncMock()
    assignment = _make_assignment("premium_models", "bud-pm")
    budget = _make_budget("bud-pm", "proj-old")
    with (
        patch(
            "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_for_project",
            new=AsyncMock(return_value=[assignment]),
        ),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.get_by_ids",
            new=AsyncMock(return_value=_budgets_by_id(budget)),
        ),
    ):
        result = await svc._legacy_carry_alias(session=session, project_name="proj", budget_category="premium_models")
    assert result == "proj-old"
