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

"""Budget enforcement provider registry.

Enterprise code registers a provider implementation at startup via
``register_budget_enforcement_provider``.  Core services call
``get_active_provider`` to obtain the active provider (or the noop fallback).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from codemie.service.budget.budget_enums import BudgetCategory, SyncStatus
from codemie.service.budget.provider import (
    BudgetEnforcementProvider,
    BudgetResetReconciliationItem,
    BudgetResetReconciliationResult,
    BudgetResetReconciliationTarget,
    BudgetProviderMemberState,
    BudgetProviderState,
    BudgetRuntimeContext,
    BudgetRuntimeProviderResult,
    PersonalBudgetEntry,
    GlobalBudgetState,
    MemberBudgetSpendSnapshot,
    PersonalSpendEntry,
    ProjectBudgetSpendSnapshot,
    ProjectBudgetState,
)

if TYPE_CHECKING:
    from codemie.service.budget.budget_models import Budget, ProjectMemberBudgetAssignment

_NOOP_PROVIDER_NAME = "noop"

_active_provider: BudgetEnforcementProvider | None = None


def register_budget_enforcement_provider(provider: BudgetEnforcementProvider) -> None:
    """Register the active enforcement provider. Called once at startup by enterprise code."""
    global _active_provider
    _active_provider = provider


def get_active_provider() -> BudgetEnforcementProvider:
    """Return the registered provider, or the noop fallback if none is registered."""
    if _active_provider is not None:
        return _active_provider
    return _NOOP_PROVIDER


class _NoopBudgetEnforcementProvider:
    """Noop provider used in non-enterprise mode.

    All methods succeed without side effects and return provider="noop" state.
    This keeps budget APIs usable without enforcement, making sync status
    explicitly visible as noop in the API response.
    """

    provider_name: str = _NOOP_PROVIDER_NAME

    @staticmethod
    def _consume_args(*args: object) -> None:
        _ = args

    @classmethod
    def _noop_result(cls, *args: object) -> None:
        cls._consume_args(*args)
        return None

    @classmethod
    def _noop_runtime_result(cls, context: BudgetRuntimeContext) -> BudgetRuntimeProviderResult:
        cls._consume_args(context)
        return BudgetRuntimeProviderResult(provider=_NOOP_PROVIDER_NAME)

    # ── Global / user budget methods ────────────────────────────────────

    async def ensure_global_budget(
        self,
        *,
        budget_id: str,
        budget_category: BudgetCategory,
        soft_budget: float,
        max_budget: float,
        budget_duration: str,
    ) -> BudgetProviderState:
        _ = (budget_id, budget_category, soft_budget, max_budget, budget_duration)
        return BudgetProviderState(provider=_NOOP_PROVIDER_NAME, sync_status=SyncStatus.NOOP)

    async def update_global_budget(
        self,
        *,
        budget_id: str,
        soft_budget: float,
        max_budget: float,
        budget_duration: str,
        budget_reset_at: str | None = None,
    ) -> BudgetProviderState:
        _ = (budget_id, soft_budget, max_budget, budget_duration, budget_reset_at)
        return BudgetProviderState(provider=_NOOP_PROVIDER_NAME, sync_status=SyncStatus.NOOP)

    async def delete_global_budget(self, *, budget_id: str) -> None:
        return self._noop_result(budget_id)

    async def assign_user_budget(
        self,
        *,
        username: str,
        budget_category: BudgetCategory,
        budget_id: str,
    ) -> None:
        return self._noop_result(username, budget_category, budget_id)

    async def clear_user_budget(
        self,
        *,
        username: str,
        budget_category: BudgetCategory,
    ) -> None:
        return self._noop_result(username, budget_category)

    async def reset_user_budget_spending(
        self,
        *,
        username: str,
        budget_category: BudgetCategory,
        budget_id: str,
    ) -> None:
        return await self.assign_user_budget(
            username=username,
            budget_category=budget_category,
            budget_id=budget_id,
        )

    async def list_global_budget_states(self) -> list[GlobalBudgetState] | None:
        return []

    async def list_personal_budget_assignments(self) -> list[PersonalBudgetEntry] | None:
        return []

    async def provision_global_user(self, *, user_id: str, username: str) -> None:
        return self._noop_result(user_id, username)

    # ── Project budget methods ───────────────────────────────────────────

    async def ensure_project_budget(
        self,
        *,
        project_name: str,
        budget_category: BudgetCategory,
        budget_id: str,
        max_budget: Decimal,
        budget_duration: str,
        models: list[str] | None,
        metadata: dict[str, Any] | None = None,
    ) -> BudgetProviderState:
        _ = (project_name, budget_category, budget_id, max_budget, budget_duration, models, metadata)
        return BudgetProviderState(provider=_NOOP_PROVIDER_NAME, sync_status=SyncStatus.NOOP)

    async def update_project_budget(
        self,
        *,
        budget_state: BudgetProviderState,
        project_name: str,
        budget_category: BudgetCategory,
        budget_id: str,
        max_budget: Decimal,
        budget_duration: str,
        models: list[str] | None,
        metadata: dict[str, Any] | None = None,
    ) -> BudgetProviderState:
        _ = (
            budget_state,
            project_name,
            budget_category,
            budget_id,
            max_budget,
            budget_duration,
            models,
            metadata,
        )
        return BudgetProviderState(provider=_NOOP_PROVIDER_NAME, sync_status=SyncStatus.NOOP)

    async def delete_project_budget(
        self,
        *,
        budget_state: BudgetProviderState,
        project_name: str | None = None,
    ) -> None:
        return self._noop_result(budget_state, project_name)

    async def reset_project_budget_spend(
        self,
        *,
        budget_state: BudgetProviderState,
        project_name: str,
        budget_category: BudgetCategory,
        budget_id: str,
        changed_by: str | None = None,
        models: list[str] | None = None,
    ) -> BudgetProviderState:
        _ = (budget_state, project_name, budget_category, budget_id, changed_by, models)
        return BudgetProviderState(provider=_NOOP_PROVIDER_NAME, sync_status=SyncStatus.NOOP)

    async def sync_member_allocation(
        self,
        *,
        allocation: "ProjectMemberBudgetAssignment",
        budget: "Budget",
    ) -> BudgetProviderMemberState:
        _ = (allocation, budget)
        return BudgetProviderMemberState(provider=_NOOP_PROVIDER_NAME, sync_status=SyncStatus.NOOP)

    async def delete_member_allocation(
        self,
        *,
        allocation: "ProjectMemberBudgetAssignment",
    ) -> None:
        return self._noop_result(allocation)

    async def resolve_runtime(
        self,
        *,
        context: BudgetRuntimeContext,
    ) -> BudgetRuntimeProviderResult:
        return self.resolve_runtime_sync(context=context)

    def resolve_runtime_sync(
        self,
        *,
        context: BudgetRuntimeContext,
    ) -> BudgetRuntimeProviderResult:
        return self._noop_runtime_result(context)

    async def collect_project_budget_spend(self) -> list[ProjectBudgetSpendSnapshot]:
        return []

    async def collect_member_budget_spend(self) -> list[MemberBudgetSpendSnapshot]:
        return []

    async def collect_member_budget_spend_for_refs(
        self,
        provider_member_refs: set[str],
    ) -> list[MemberBudgetSpendSnapshot]:
        _ = provider_member_refs
        return []

    async def reconcile_budget_reset_timestamps(
        self,
        *,
        targets: list[BudgetResetReconciliationTarget],
    ) -> BudgetResetReconciliationResult:
        return BudgetResetReconciliationResult(
            items=[
                BudgetResetReconciliationItem(
                    entity_type=target.entity_type,
                    budget_id=target.budget_id,
                    provider_budget_ref=target.provider_budget_ref,
                    provider_member_ref=target.provider_member_ref,
                    error="budget enforcement provider unavailable",
                )
                for target in targets
            ]
        )

    async def collect_personal_spend(self) -> list[PersonalSpendEntry]:
        return []

    async def get_project_budget_state_by_ref(
        self,
        *,
        provider_budget_ref: str,
    ) -> "ProjectBudgetState | None":
        _ = provider_budget_ref
        return None


_NOOP_PROVIDER: BudgetEnforcementProvider = _NoopBudgetEnforcementProvider()  # type: ignore[assignment]

# Public alias for testing
NoopBudgetProvider = _NoopBudgetEnforcementProvider
