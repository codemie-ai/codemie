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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.enterprise.litellm.budget_provider_adapter import (
    LiteLLMBudgetEnforcementProvider,
    _normalize_budget_reset_at,
    _normalize_personal_budget_identifier,
)
from codemie.service.budget.budget_enums import BudgetCategory, SyncStatus
from codemie.service.budget.provider import MemberBudgetSpendSnapshot


@pytest.mark.asyncio
async def test_sync_member_allocation_returns_provider_budget_id():
    adapter = LiteLLMBudgetEnforcementProvider(service=SimpleNamespace(sync_project_member_budget_assignment=object()))
    allocation = SimpleNamespace(
        project_budget_id="proj-budget-1",
        project_name="proj-a",
        budget_category="cli",
        user_id="user-1",
        allocated_max_budget=25.0,
        allocated_soft_budget=20.0,
    )
    budget = SimpleNamespace(budget_duration="30d", budget_reset_at="2026-04-22T10:00:00Z")
    service_result = SimpleNamespace(
        provider_member_ref="codemie:project:proj-a:category:cli:user:user-1",
        budget_id="member-budget-1",
        budget_reset_at="2026-04-22T10:00:00Z",
        metadata={},
    )

    with patch("codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread", return_value=service_result):
        result = await adapter.sync_member_allocation(allocation=allocation, budget=budget)

    assert result.provider == "litellm"
    assert result.provider_member_ref == service_result.provider_member_ref
    assert result.provider_budget_id == "member-budget-1"
    assert result.budget_reset_at == "2026-04-22T10:00:00Z"
    assert result.sync_status == SyncStatus.OK
    assert result.metadata["internal_budget"] is True
    assert result.metadata["budget_scope"] == "project_member"
    assert result.metadata["provider_budget_id"] == "member-budget-1"


@pytest.mark.asyncio
async def test_list_global_budget_states_skips_internal_member_budgets():
    service = SimpleNamespace(
        list_managed_budgets=lambda: [
            SimpleNamespace(
                budget_id="platform",
                soft_budget=100.0,
                max_budget=200.0,
                budget_duration="30d",
                budget_reset_at="2026-04-01T00:00:00Z",
            ),
            SimpleNamespace(
                budget_id="member-budget-1",
                soft_budget=10.0,
                max_budget=20.0,
                budget_duration="30d",
                budget_reset_at="2026-04-01T00:00:00Z",
            ),
        ],
        get_customer_list=lambda: [
            SimpleNamespace(user_id="regular@example.com", budget_id="platform", litellm_budget_table=None),
            SimpleNamespace(
                user_id="codemie:project:proj-a:category:cli:user:user-1",
                budget_id="member-budget-1",
                litellm_budget_table=None,
            ),
        ],
    )
    adapter = LiteLLMBudgetEnforcementProvider(service=service)

    with patch(
        "codemie.enterprise.litellm.budget_helpers.list_budgets_from_litellm",
        return_value=service.list_managed_budgets(),
    ):
        result = await adapter.list_global_budget_states()

    assert result is not None
    assert [entry.budget_id for entry in result] == ["platform"]


@pytest.mark.asyncio
async def test_collect_member_budget_spend_for_refs_filters_to_requested_provider_refs():
    service = SimpleNamespace(
        get_customer_list=lambda: [
            SimpleNamespace(
                user_id="codemie:project:proj-a:category:cli:user:user-1",
                spend=Decimal("1.5"),
                budget_reset_at="2026-04-23T10:10:00Z",
            ),
            SimpleNamespace(
                user_id="codemie:project:proj-a:category:cli:user:user-2",
                spend=Decimal("9.0"),
                budget_reset_at="2026-04-23T10:10:00Z",
            ),
        ]
    )
    adapter = LiteLLMBudgetEnforcementProvider(service=service)
    session = AsyncMock()
    allocation = SimpleNamespace(
        project_name="proj-a",
        budget_category="cli",
        project_budget_id="budget-1",
        user_id="user-1",
        budget_reset_at="2026-04-23T10:10:00Z",
        provider_metadata={"provider_member_ref": "codemie:project:proj-a:category:cli:user:user-1"},
    )
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [allocation]
    session.execute = AsyncMock(return_value=result_mock)

    async def _session_ctx():
        return None

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("codemie.clients.postgres.get_async_session", return_value=session_cm),
        patch("codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread", side_effect=lambda fn: fn()),
    ):
        snapshots = await adapter.collect_member_budget_spend_for_refs(
            {"codemie:project:proj-a:category:cli:user:user-1"}
        )

    assert snapshots == [
        MemberBudgetSpendSnapshot(
            project_name="proj-a",
            budget_category=BudgetCategory.CLI,
            budget_id="budget-1",
            user_id="user-1",
            spend=Decimal("1.5"),
            budget_reset_at="2026-04-23T10:10:00Z",
            provider_subject_id="codemie:project:proj-a:category:cli:user:user-1",
        )
    ]


@pytest.mark.asyncio
async def test_collect_member_budget_spend_for_refs_returns_empty_for_empty_ref_set():
    adapter = LiteLLMBudgetEnforcementProvider(service=SimpleNamespace(get_customer_list=lambda: []))

    result = await adapter.collect_member_budget_spend_for_refs(set())

    assert result == []


def test_normalize_personal_budget_identifier_strips_non_platform_suffix():
    assert (
        _normalize_personal_budget_identifier("user@example.com_codemie_cli", BudgetCategory.CLI) == "user@example.com"
    )


def test_normalize_budget_reset_at_falls_back_when_missing():
    assert _normalize_budget_reset_at(None, "2026-04-23T10:10:00Z") == "2026-04-23T10:10:00Z"


@pytest.mark.asyncio
async def test_list_personal_budget_assignments_normalizes_identifiers_and_skips_project_customers():
    budget_table = SimpleNamespace(
        budget_id="cli-budget",
        soft_budget=10.0,
        max_budget=20.0,
        budget_duration="30d",
        budget_reset_at="2026-04-23T10:10:00Z",
    )
    service = SimpleNamespace(
        get_customer_list=lambda: [
            SimpleNamespace(user_id="user@example.com_codemie_cli", litellm_budget_table=budget_table),
            SimpleNamespace(user_id="platform@example.com", litellm_budget_table=None),
            SimpleNamespace(
                user_id="codemie:project:proj-a:category:cli:user:user-1",
                litellm_budget_table=budget_table,
            ),
        ]
    )
    adapter = LiteLLMBudgetEnforcementProvider(service=service)

    with (
        patch("codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread", side_effect=lambda fn: fn()),
        patch(
            "codemie.enterprise.litellm.dependencies.get_litellm_service_or_none",
            return_value=service,
        ),
    ):
        entries = await adapter.list_personal_budget_assignments()

    assert entries is not None
    assert [entry.user_identifier for entry in entries] == ["user@example.com", "platform@example.com"]
    assert entries[0].budget_id == "cli-budget"
    assert entries[0].budget_category == BudgetCategory.CLI
    assert entries[1].budget_category == BudgetCategory.PLATFORM


@pytest.mark.asyncio
async def test_sync_member_allocation_uses_effective_max_budget_when_provided():
    allocation = SimpleNamespace(
        project_budget_id="proj-budget-1",
        project_name="proj-a",
        budget_category="cli",
        user_id="user-1",
        allocated_max_budget=100.0,
        allocated_soft_budget=80.0,
    )
    budget = SimpleNamespace(budget_duration="30d", budget_reset_at="2026-04-22T10:00:00Z")
    service_result = SimpleNamespace(
        provider_member_ref="codemie:project:proj-a:category:cli:user:user-1",
        budget_id="member-budget-1",
        budget_reset_at="2026-04-22T10:00:00Z",
        metadata={},
    )

    mock_service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        return_value=service_result,
    ) as mock_to_thread:
        result = await adapter.sync_member_allocation(allocation=allocation, budget=budget, effective_max_budget=500.0)

    assert result.sync_status == SyncStatus.OK
    _, call_kwargs = mock_to_thread.call_args
    assert call_kwargs.get("allocated_max_budget") == 500.0
