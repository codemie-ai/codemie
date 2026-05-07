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

from decimal import Decimal
from types import SimpleNamespace

import pytest

from codemie.service.budget.budget_enums import BudgetCategory, SyncStatus
from codemie.service.budget.provider_registry import get_active_provider, register_budget_enforcement_provider


@pytest.mark.asyncio
async def test_noop_budget_provider_accepts_all_protocol_arguments(monkeypatch):
    import codemie.service.budget.provider_registry as provider_registry

    monkeypatch.setattr(provider_registry, "_active_provider", None)
    provider = get_active_provider()

    global_state = await provider.ensure_global_budget(
        budget_id="global-budget",
        budget_category=BudgetCategory.CLI,
        soft_budget=10.0,
        max_budget=20.0,
        budget_duration="30d",
    )
    project_state = await provider.ensure_project_budget(
        project_name="proj-a",
        budget_category=BudgetCategory.CLI,
        budget_id="project-budget",
        max_budget=Decimal("20"),
        budget_duration="30d",
        models=["gpt-4.1"],
        metadata={"scope": "test"},
    )
    member_state = await provider.sync_member_allocation(
        allocation=SimpleNamespace(id="alloc-1"),
        budget=SimpleNamespace(budget_id="project-budget"),
    )

    await provider.delete_global_budget(budget_id="global-budget")
    await provider.assign_user_budget(
        user_email="user@example.com",
        budget_category=BudgetCategory.CLI,
        budget_id="global-budget",
    )
    await provider.clear_user_budget(
        user_email="user@example.com",
        budget_category=BudgetCategory.CLI,
    )
    await provider.reset_user_budget_spending(
        user_email="user@example.com",
        budget_category=BudgetCategory.CLI,
        budget_id="global-budget",
    )
    await provider.provision_global_user(user_id="user-1", user_email="user@example.com")
    await provider.delete_project_budget(budget_state=project_state, project_name="proj-a")
    await provider.delete_member_allocation(allocation=SimpleNamespace(id="alloc-1"))

    runtime_result = await provider.resolve_runtime(context=SimpleNamespace(user_id="user-1"))
    runtime_sync_result = provider.resolve_runtime_sync(context=SimpleNamespace(user_id="user-1"))

    assert global_state.sync_status == SyncStatus.NOOP
    assert project_state.sync_status == SyncStatus.NOOP
    assert member_state.sync_status == SyncStatus.NOOP
    assert runtime_result.provider == "noop"
    assert runtime_sync_result.provider == "noop"
    assert await provider.list_global_budget_states() == []
    assert await provider.list_personal_budget_assignments() == []
    assert await provider.collect_project_budget_spend() == []
    assert await provider.collect_member_budget_spend() == []
    assert await provider.collect_member_budget_spend_for_refs({"ref-1"}) == []
    assert await provider.collect_personal_spend() == []
    assert await provider.get_project_budget_state_by_ref(provider_budget_ref="ref-1") is None


def test_register_budget_enforcement_provider_overrides_noop(monkeypatch):
    import codemie.service.budget.provider_registry as provider_registry

    monkeypatch.setattr(provider_registry, "_active_provider", None)
    custom_provider = SimpleNamespace(provider_name="custom")

    register_budget_enforcement_provider(custom_provider)

    assert get_active_provider() is custom_provider
