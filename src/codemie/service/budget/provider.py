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

"""Provider-neutral budget enforcement protocol and state models.

Core project budget services depend only on this protocol.  All
LiteLLM-specific (or other provider-specific) logic lives in the
enterprise package and must not leak into this module.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from codemie.service.budget.budget_enums import BudgetCategory, BudgetScope, SyncStatus

if TYPE_CHECKING:
    from codemie.service.budget.budget_models import Budget, ProjectMemberBudgetAssignment


class PersonalSpendEntry(BaseModel):
    """Personal (non-project-scoped) spend entry returned by the provider.

    ``user_identifier`` is the normalized email with any provider-specific suffixes
    already stripped.  ``budget_category`` is pre-derived by the provider so the
    caller never needs to import provider-specific suffix logic.
    """

    user_identifier: str
    budget_id: str
    budget_category: str
    spend: Decimal
    budget_reset_at: str | None = None


class BudgetProviderState(BaseModel):
    """Opaque provider state returned after budget provisioning."""

    provider: str
    provider_budget_ref: str | None = None
    budget_reset_at: str | None = None
    sync_status: str = SyncStatus.OK
    metadata: dict[str, Any] = Field(default_factory=dict)


class BudgetProviderMemberState(BaseModel):
    """Opaque provider state returned after member allocation sync."""

    provider: str
    provider_member_ref: str | None = None
    provider_budget_id: str | None = None
    budget_reset_at: str | None = None
    sync_status: str = SyncStatus.OK
    metadata: dict[str, Any] = Field(default_factory=dict)


class BudgetRuntimeContext(BaseModel):
    """Resolved context for a single LLM request."""

    scope: BudgetScope
    project_name: str | None
    budget_category: BudgetCategory
    budget_id: str | None
    user_id: str
    user_email: str | None
    model: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    member_provider_metadata: dict[str, Any] = Field(default_factory=dict)


class BudgetRuntimeProviderResult(BaseModel):
    """Provider result that modifies how a request is forwarded."""

    provider: str
    api_key: str | None = None
    base_url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body_overrides: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectBudgetSpendSnapshot(BaseModel):
    """Authoritative project/category spend snapshot from the provider."""

    project_name: str
    budget_category: BudgetCategory
    budget_id: str
    spend: Decimal
    cumulative_spend: Decimal | None = None
    budget_reset_at: str | None = None
    provider_subject_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemberBudgetSpendSnapshot(BaseModel):
    """Member-level breakdown spend snapshot from the provider."""

    project_name: str
    budget_category: BudgetCategory
    budget_id: str
    user_id: str
    spend: Decimal
    cumulative_spend: Decimal | None = None
    budget_reset_at: str | None = None
    provider_subject_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GlobalBudgetState(BaseModel):
    """Budget state returned by the provider for a global/personal budget."""

    budget_id: str
    provider: str
    soft_budget: float = 0.0
    max_budget: float = 0.0
    budget_duration: str = "30d"
    budget_reset_at: str | None = None
    sync_status: str = SyncStatus.OK


class ProjectBudgetState(BaseModel):
    """Budget limits and metadata for an existing project budget, as reported by the provider."""

    max_budget: float = 0.0
    soft_budget: float = 0.0
    budget_duration: str = "30d"
    budget_reset_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BudgetResetReconciliationTarget(BaseModel):
    """Provider-neutral reconciliation target for one reset timestamp entity."""

    entity_type: str  # "budget" | "member_allocation"
    budget_id: str
    provider_budget_ref: str | None = None
    provider_member_ref: str | None = None
    project_budget_id: str | None = None
    budget_reset_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BudgetResetReconciliationItem(BaseModel):
    """One reconciliation outcome returned by the provider."""

    entity_type: str
    budget_id: str
    refreshed_budget_reset_at: str | None = None
    provider_budget_ref: str | None = None
    provider_member_ref: str | None = None
    error: str | None = None


class BudgetResetReconciliationResult(BaseModel):
    """Bulk reconciliation response returned by the provider."""

    items: list[BudgetResetReconciliationItem] = Field(default_factory=list)


class PersonalBudgetEntry(BaseModel):
    """A user's personal (non-project-scoped) budget assignment as reported by the provider.

    ``user_identifier`` is the email (or other Codemie-resolvable identifier)
    with any provider-specific suffixes already stripped.  Core never sees
    raw provider-internal user identifiers.
    """

    user_identifier: str
    budget_category: BudgetCategory
    budget_id: str | None = None
    soft_budget: float | None = None
    max_budget: float | None = None
    budget_duration: str | None = None
    budget_reset_at: str | None = None


@runtime_checkable
class BudgetEnforcementProvider(Protocol):
    """Provider-neutral protocol for budget enforcement.

    Core services call only these methods.  The active implementation is
    resolved via the provider registry and may be a noop in non-enterprise
    mode or a full LiteLLM implementation in enterprise mode.

    Global/user budget methods (Phase 3 — Release A):
      ensure_global_budget, update_global_budget, delete_global_budget,
      assign_user_budget, clear_user_budget, reset_user_budget_spending,
      list_global_budget_states, list_personal_budget_assignments,
      provision_global_user.

    Project budget methods (Phase 8 — Release B):
      ensure_project_budget, update_project_budget, delete_project_budget,
      get_project_budget_state_by_ref, sync_member_allocation, delete_member_allocation,
      resolve_runtime, collect_project_budget_spend, collect_member_budget_spend.

    Spend collection methods:
      collect_personal_spend, collect_project_budget_spend, collect_member_budget_spend.

    Runtime helpers:
      resolve_runtime_sync, provider_name.
    """

    @property
    def provider_name(self) -> str: ...

    # ── Global / user budget methods ────────────────────────────────────

    async def ensure_global_budget(
        self,
        *,
        budget_id: str,
        budget_category: BudgetCategory,
        soft_budget: float,
        max_budget: float,
        budget_duration: str,
    ) -> "BudgetProviderState": ...

    async def update_global_budget(
        self,
        *,
        budget_id: str,
        soft_budget: float,
        max_budget: float,
        budget_duration: str,
        budget_reset_at: str | None = None,
    ) -> "BudgetProviderState": ...

    async def delete_global_budget(
        self,
        *,
        budget_id: str,
    ) -> None: ...

    async def assign_user_budget(
        self,
        *,
        username: str,
        budget_category: BudgetCategory,
        budget_id: str,
    ) -> None: ...

    async def clear_user_budget(
        self,
        *,
        username: str,
        budget_category: BudgetCategory,
    ) -> None: ...

    async def reset_user_budget_spending(
        self,
        *,
        username: str,
        budget_category: BudgetCategory,
        budget_id: str,
    ) -> None: ...

    async def list_global_budget_states(self) -> list[GlobalBudgetState] | None: ...

    async def list_personal_budget_assignments(self) -> list[PersonalBudgetEntry] | None: ...

    async def provision_global_user(
        self,
        *,
        user_id: str,
        username: str,
    ) -> None: ...

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
    ) -> BudgetProviderState: ...

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
    ) -> BudgetProviderState: ...

    async def delete_project_budget(
        self,
        *,
        budget_state: BudgetProviderState,
        project_name: str | None = None,
    ) -> None: ...

    async def reset_project_budget_spend(
        self,
        *,
        budget_state: BudgetProviderState,
        project_name: str,
        budget_category: BudgetCategory,
        budget_id: str,
        changed_by: str | None = None,
        models: list[str] | None = None,
    ) -> BudgetProviderState: ...

    async def get_project_budget_state_by_ref(
        self,
        *,
        provider_budget_ref: str,
    ) -> "ProjectBudgetState | None":
        """Return the current budget limits for a project budget identified by its
        provider reference, or None if the budget does not exist in the provider."""
        ...

    async def sync_member_allocation(
        self,
        *,
        allocation: "ProjectMemberBudgetAssignment",
        budget: "Budget",
        effective_max_budget: float | None = None,
    ) -> BudgetProviderMemberState: ...

    async def delete_member_allocation(
        self,
        *,
        allocation: "ProjectMemberBudgetAssignment",
    ) -> None: ...

    async def resolve_runtime(
        self,
        *,
        context: BudgetRuntimeContext,
    ) -> BudgetRuntimeProviderResult: ...

    def resolve_runtime_sync(
        self,
        *,
        context: BudgetRuntimeContext,
    ) -> BudgetRuntimeProviderResult: ...

    async def collect_project_budget_spend(self) -> list[ProjectBudgetSpendSnapshot]: ...

    async def collect_member_budget_spend(self) -> list[MemberBudgetSpendSnapshot]: ...

    async def collect_member_budget_spend_for_refs(
        self,
        provider_member_refs: set[str],
    ) -> list[MemberBudgetSpendSnapshot]: ...

    async def reconcile_budget_reset_timestamps(
        self,
        *,
        targets: list[BudgetResetReconciliationTarget],
    ) -> BudgetResetReconciliationResult: ...

    async def collect_personal_spend(self) -> "list[PersonalSpendEntry] | None": ...
