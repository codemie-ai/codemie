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

import threading
import time
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from codemie.enterprise.litellm.budget_provider_adapter import (
    LiteLLMBudgetEnforcementProvider,
    _normalize_budget_reset_at,
    _normalize_personal_budget_identifier,
)
from codemie.enterprise.litellm.project_member_sync_executor import ProjectMemberSyncExecutor
from codemie.service.budget.budget_enums import BudgetCategory, SyncStatus
from codemie.service.budget.provider import (
    BudgetProviderMemberState,
    BudgetProviderState,
    BudgetRuntimeContext,
    BudgetResetReconciliationTarget,
    MemberSyncItem,
)


def _member_sync_item(
    *,
    allocation_id: str = "alloc-1",
    project_budget_id: str = "proj-budget-1",
    project_name: str = "proj-a",
    budget_category: str = "cli",
    user_id: str = "user-1",
    allocation_mode: str = "shared",
    effective_budget_id: str | None = None,
    override_budget_id: str | None = None,
    shared_budget_id: str | None = None,
    allocated_max_budget: float = 25.0,
    allocated_soft_budget: float = 20.0,
    budget_duration: str = "30d",
    budget_reset_at: str | None = "2026-04-22T10:00:00Z",
    effective_max_budget: float | None = None,
) -> MemberSyncItem:
    return MemberSyncItem(
        allocation_id=allocation_id,
        project_budget_id=project_budget_id,
        project_name=project_name,
        budget_category=budget_category,
        user_id=user_id,
        allocation_mode=allocation_mode,
        effective_budget_id=effective_budget_id,
        override_budget_id=override_budget_id,
        shared_budget_id=shared_budget_id,
        allocated_max_budget=allocated_max_budget,
        allocated_soft_budget=allocated_soft_budget,
        budget_duration=budget_duration,
        budget_reset_at=budget_reset_at,
        effective_max_budget=effective_max_budget,
    )


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


def _legacy_budget_target(provider_budget_ref: str = "prtr-dmod") -> BudgetResetReconciliationTarget:
    return BudgetResetReconciliationTarget(
        entity_type="budget",
        budget_id="prtr-dmod-platform-43f987c9",
        provider_budget_ref=provider_budget_ref,
        budget_reset_at="2026-06-01T00:00:00Z",
        metadata={"provider": "litellm", "provider_budget_ref": provider_budget_ref, "sync_status": "ok"},
    )


@pytest.mark.asyncio
async def test_reconcile_legacy_budget_ref_falls_back_to_key_lookup():
    service = SimpleNamespace(
        get_budget_info_map=lambda budget_ids: {},
        get_all_keys_spending=lambda: [
            SimpleNamespace(key_alias="prtr-dmod", budget_reset_at="2026-08-01T00:00:00Z"),
        ],
    )
    adapter = LiteLLMBudgetEnforcementProvider(service=service)

    result = await adapter.reconcile_budget_reset_timestamps(targets=[_legacy_budget_target()])

    assert len(result.items) == 1
    item = result.items[0]
    assert item.error is None
    assert item.refreshed_budget_reset_at == "2026-08-01T00:00:00Z"


@pytest.mark.asyncio
async def test_reconcile_budget_map_hit_does_not_fetch_keys():
    def _fail_keys():
        raise AssertionError("get_all_keys_spending must not be called when budget_map resolves all targets")

    service = SimpleNamespace(
        get_budget_info_map=lambda budget_ids: {
            "child-budget-1:shared": SimpleNamespace(budget_reset_at="2026-08-01T00:00:00Z"),
        },
        get_all_keys_spending=_fail_keys,
    )
    adapter = LiteLLMBudgetEnforcementProvider(service=service)
    target = BudgetResetReconciliationTarget(
        entity_type="budget",
        budget_id="child-budget-1:shared",
        provider_budget_ref="child-budget-1:shared",
        budget_reset_at="2026-06-01T00:00:00Z",
        metadata={"provider": "litellm"},
    )

    result = await adapter.reconcile_budget_reset_timestamps(targets=[target])

    assert result.items[0].error is None
    assert result.items[0].refreshed_budget_reset_at == "2026-08-01T00:00:00Z"


@pytest.mark.asyncio
async def test_reconcile_reports_missing_when_ref_not_in_budgets_or_keys():
    service = SimpleNamespace(
        get_budget_info_map=lambda budget_ids: {},
        get_all_keys_spending=lambda: [],
    )
    adapter = LiteLLMBudgetEnforcementProvider(service=service)

    result = await adapter.reconcile_budget_reset_timestamps(targets=[_legacy_budget_target("ghost-ref")])

    assert result.items[0].error == "provider entity missing during reset reconciliation"


@pytest.mark.asyncio
@pytest.mark.parametrize("child_budget_id", ["proj-1:shared", "proj-1:user:alice"])
async def test_delete_override_budget_posts_to_endpoint(child_budget_id):
    service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=service)

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        new=AsyncMock(return_value=None),
    ) as mock_thread:
        await adapter.delete_override_budget(override_budget_id=child_budget_id)

    mock_thread.assert_awaited_once()
    call_args = mock_thread.call_args
    assert call_args.args[0] == service.api_client.post
    assert call_args.args[1] == "/budget/delete"
    assert call_args.kwargs == {"data": {"id": child_budget_id}}


@pytest.mark.asyncio
async def test_delete_override_budget_when_service_none():
    adapter = LiteLLMBudgetEnforcementProvider(service=None)
    await adapter.delete_override_budget(override_budget_id="proj-1:shared")


@pytest.mark.asyncio
async def test_delete_override_budget_refuses_non_child_id():
    service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=service)

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        new=AsyncMock(),
    ) as mock_thread:
        await adapter.delete_override_budget(override_budget_id="proj-1")

    mock_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_override_budget_treats_missing_budget_as_success():
    service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=service)
    not_found = Exception("missing")
    not_found.response = SimpleNamespace(status_code=404)

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        new=AsyncMock(side_effect=not_found),
    ):
        await adapter.delete_override_budget(override_budget_id="proj-1:shared")


@pytest.mark.asyncio
async def test_delete_override_budget_swallows_provider_error():
    service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=service)

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        new=AsyncMock(side_effect=RuntimeError("LiteLLM unavailable")),
    ):
        await adapter.delete_override_budget(override_budget_id="proj-1:shared")


@pytest.mark.asyncio
async def test_delete_global_budget_posts_to_endpoint():
    service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=service)

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        new=AsyncMock(return_value=None),
    ) as mock_thread:
        await adapter.delete_global_budget(budget_id="manual-budget")

    mock_thread.assert_awaited_once()
    call_args = mock_thread.call_args
    assert call_args.args[0] == service.api_client.post
    assert call_args.args[1] == "/budget/delete"
    assert call_args.kwargs == {"data": {"id": "manual-budget"}}


@pytest.mark.asyncio
async def test_delete_global_budget_when_service_none():
    adapter = LiteLLMBudgetEnforcementProvider(service=None)
    await adapter.delete_global_budget(budget_id="manual-budget")


@pytest.mark.asyncio
async def test_delete_global_budget_treats_missing_budget_as_success():
    service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=service)
    not_found = Exception("missing")
    not_found.response = SimpleNamespace(status_code=404)

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        new=AsyncMock(side_effect=not_found),
    ):
        await adapter.delete_global_budget(budget_id="manual-budget")


@pytest.mark.asyncio
async def test_delete_global_budget_propagates_provider_error():
    service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=service)

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        new=AsyncMock(side_effect=RuntimeError("LiteLLM unavailable")),
    ):
        with pytest.raises(RuntimeError, match="LiteLLM unavailable"):
            await adapter.delete_global_budget(budget_id="manual-budget")


# ── _carry_spend_forward tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_carry_spend_forward_patches_new_key_via_api_key_field():
    """api_key field in key_state is used as the token when key_hash/token are absent."""
    mock_service = MagicMock()
    mock_service.api_client.post = MagicMock(return_value={"updated": True})
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    key_state = {"api_key": "sk-test-abc123", "key_alias": "codemie:project:proj-a:category:cli"}

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs),
    ):
        await adapter._carry_spend_forward(
            service=mock_service,
            key_state=key_state,
            carry_spend=42.5,
            project_name="proj-a",
            budget_id="bud-1",
            budget_category=BudgetCategory.CLI,
            key_alias="codemie:project:proj-a:category:cli",
        )

    mock_service.api_client.post.assert_called_once_with("/key/update", data={"key": "sk-test-abc123", "spend": 42.5})
    assert key_state["spend"] == 42.5


@pytest.mark.asyncio
async def test_carry_spend_forward_prefers_key_hash_over_api_key():
    """key_hash takes precedence over api_key when both are present."""
    mock_service = MagicMock()
    mock_service.api_client.post = MagicMock(return_value={"updated": True})
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    key_state = {"key_hash": "hash-abc", "api_key": "sk-test-should-not-use", "key_alias": "alias"}

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs),
    ):
        await adapter._carry_spend_forward(
            service=mock_service,
            key_state=key_state,
            carry_spend=10.0,
            project_name="proj-a",
            budget_id="bud-1",
            budget_category=BudgetCategory.CLI,
            key_alias="alias",
        )

    mock_service.api_client.post.assert_called_once_with("/key/update", data={"key": "hash-abc", "spend": 10.0})


@pytest.mark.asyncio
async def test_carry_spend_forward_skips_api_call_when_no_token():
    """Skips the /key/update call and does not mutate key_state when no token fields are present."""
    mock_service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    key_state: dict = {}

    await adapter._carry_spend_forward(
        service=mock_service,
        key_state=key_state,
        carry_spend=5.0,
        project_name="proj-a",
        budget_id="bud-1",
        budget_category=BudgetCategory.CLI,
        key_alias="alias",
    )

    mock_service.api_client.post.assert_not_called()
    assert "spend" not in key_state


@pytest.mark.asyncio
async def test_carry_spend_forward_does_not_raise_on_api_error():
    """API failure during carry-forward is swallowed — no exception propagates and spend is not set."""
    mock_service = MagicMock()
    mock_service.api_client.post = MagicMock(side_effect=RuntimeError("LiteLLM 404"))
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    key_state = {"api_key": "sk-test-abc"}

    with patch(
        "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
        side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs),
    ):
        await adapter._carry_spend_forward(
            service=mock_service,
            key_state=key_state,
            carry_spend=7.0,
            project_name="proj-a",
            budget_id="bud-1",
            budget_category=BudgetCategory.CLI,
            key_alias="alias",
        )

    assert "spend" not in key_state


# ── _recreate_project_budget_key_alias carry-spend integration ───────────────


def _make_recreate_service(*, existing_key, new_key_state):
    """Build a mock LiteLLMService for _recreate_project_budget_key_alias tests.

    _get_project_key_by_alias is called twice: once to capture spend, once to
    refresh budget_reset_at.  The second call is only reached when new_key_state
    lacks 'budget_reset_at', so we always return existing_key to be safe.
    """
    mock_service = MagicMock()
    mock_service._get_project_key_by_alias = MagicMock(return_value=existing_key)
    mock_service._generate_project_key = MagicMock(return_value=new_key_state)
    return mock_service


@pytest.mark.asyncio
async def test_recreate_carries_spend_from_existing_key():
    """When an existing key has spend > 0, _recreate calls _carry_spend_forward."""
    existing_key = {"key_alias": "codemie:project:proj-a:category:cli", "spend": 42.5}
    new_key_state = {
        "key_alias": "codemie:project:proj-a:category:cli",
        "api_key": "sk-new-key",
        "budget_reset_at": "2026-09-01T00:00:00Z",
    }

    mock_service = _make_recreate_service(existing_key=existing_key, new_key_state=new_key_state)
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    carry_called_with: list = []

    async def _mock_carry_forward(**kwargs):
        carry_called_with.append(kwargs)

    with (
        patch(
            "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ),
        patch.object(adapter, "_delete_project_provider_key_alias", new=AsyncMock()),
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_persist_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_carry_spend_forward", side_effect=_mock_carry_forward),
    ):
        state = await adapter._recreate_project_budget_key_alias(
            service=mock_service,
            project_name="proj-a",
            budget_category=BudgetCategory.CLI,
            budget_id="bud-1",
            max_budget=Decimal("100.0"),
            budget_duration="30d",
            models=None,
        )

    assert state.sync_status == SyncStatus.OK
    assert len(carry_called_with) == 1
    assert carry_called_with[0]["carry_spend"] == 42.5


@pytest.mark.asyncio
async def test_recreate_skips_carry_when_no_existing_key():
    """When there is no existing key (first-time create), carry-forward is not called."""
    new_key_state = {
        "key_alias": "codemie:project:proj-b:category:cli",
        "api_key": "sk-new-key",
        "budget_reset_at": "2026-09-01T00:00:00Z",
    }

    mock_service = _make_recreate_service(existing_key=None, new_key_state=new_key_state)
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    with (
        patch(
            "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ),
        patch.object(adapter, "_delete_project_provider_key_alias", new=AsyncMock()),
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_persist_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_carry_spend_forward", new=AsyncMock()) as mock_carry,
    ):
        state = await adapter._recreate_project_budget_key_alias(
            service=mock_service,
            project_name="proj-b",
            budget_category=BudgetCategory.CLI,
            budget_id="bud-2",
            max_budget=Decimal("50.0"),
            budget_duration="30d",
            models=None,
        )

    assert state.sync_status == SyncStatus.OK
    mock_carry.assert_not_called()


@pytest.mark.asyncio
async def test_recreate_skips_carry_when_existing_key_spend_is_zero():
    """When existing key has spend=0, no carry-forward should be attempted."""
    existing_key = {"key_alias": "codemie:project:proj-c:category:cli", "spend": 0.0}
    new_key_state = {
        "key_alias": "codemie:project:proj-c:category:cli",
        "api_key": "sk-new-key",
        "budget_reset_at": "2026-09-01T00:00:00Z",
    }

    mock_service = _make_recreate_service(existing_key=existing_key, new_key_state=new_key_state)
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    with (
        patch(
            "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ),
        patch.object(adapter, "_delete_project_provider_key_alias", new=AsyncMock()),
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_persist_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_carry_spend_forward", new=AsyncMock()) as mock_carry,
    ):
        await adapter._recreate_project_budget_key_alias(
            service=mock_service,
            project_name="proj-c",
            budget_category=BudgetCategory.CLI,
            budget_id="bud-3",
            max_budget=Decimal("50.0"),
            budget_duration="30d",
            models=None,
        )

    mock_carry.assert_not_called()


# ── legacy_key_alias fallback ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recreate_falls_back_to_legacy_alias_when_canonical_key_missing():
    """Legacy project: canonical lookup returns None, legacy alias has spend → carry-forward happens."""
    legacy_alias = "mdda-aiad"
    legacy_key = {"key_alias": legacy_alias, "spend": 15.75}
    new_key_state = {
        "key_alias": "codemie:project:mdda-aiad:category:cli",
        "api_key": "sk-new-key",
        "budget_reset_at": "2026-09-01T00:00:00Z",
    }

    mock_service = MagicMock()
    mock_service._get_project_key_by_alias = MagicMock(
        side_effect=lambda alias: None if alias.startswith("codemie:project:") else legacy_key
    )
    mock_service._generate_project_key = MagicMock(return_value=new_key_state)
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    carry_called_with: list = []

    async def _mock_carry_forward(**kwargs):
        carry_called_with.append(kwargs)

    with (
        patch(
            "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ),
        patch.object(adapter, "_delete_project_provider_key_alias", new=AsyncMock()),
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_persist_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_carry_spend_forward", side_effect=_mock_carry_forward),
    ):
        state = await adapter._recreate_project_budget_key_alias(
            service=mock_service,
            project_name="mdda-aiad",
            budget_category=BudgetCategory.CLI,
            budget_id="bud-legacy",
            max_budget=Decimal("100.0"),
            budget_duration="30d",
            models=None,
            legacy_key_alias=legacy_alias,
        )

    assert state.sync_status == SyncStatus.OK
    assert len(carry_called_with) == 1
    assert carry_called_with[0]["carry_spend"] == 15.75


@pytest.mark.asyncio
async def test_recreate_skips_carry_when_both_canonical_and_legacy_lookups_miss():
    """When neither canonical nor legacy key exists, carry-forward is not called."""
    new_key_state = {
        "key_alias": "codemie:project:proj-x:category:cli",
        "api_key": "sk-new-key",
        "budget_reset_at": "2026-09-01T00:00:00Z",
    }

    mock_service = _make_recreate_service(existing_key=None, new_key_state=new_key_state)
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    with (
        patch(
            "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ),
        patch.object(adapter, "_delete_project_provider_key_alias", new=AsyncMock()),
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_persist_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_carry_spend_forward", new=AsyncMock()) as mock_carry,
    ):
        await adapter._recreate_project_budget_key_alias(
            service=mock_service,
            project_name="proj-x",
            budget_category=BudgetCategory.CLI,
            budget_id="bud-x",
            max_budget=Decimal("50.0"),
            budget_duration="30d",
            models=None,
            legacy_key_alias="proj-x-old-alias",
        )

    mock_carry.assert_not_called()


@pytest.mark.asyncio
async def test_recreate_uses_canonical_spend_and_skips_legacy_lookup_when_canonical_found():
    """When canonical key already exists, legacy alias is never queried."""
    canonical_key = {"key_alias": "codemie:project:proj-y:category:cli", "spend": 8.0}
    new_key_state = {
        "key_alias": "codemie:project:proj-y:category:cli",
        "api_key": "sk-new-key",
        "budget_reset_at": "2026-09-01T00:00:00Z",
    }

    mock_service = _make_recreate_service(existing_key=canonical_key, new_key_state=new_key_state)
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    carry_called_with: list = []

    async def _mock_carry_forward(**kwargs):
        carry_called_with.append(kwargs)

    with (
        patch(
            "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
            side_effect=lambda fn, *a, **kw: fn(*a, **kw),
        ),
        patch.object(adapter, "_delete_project_provider_key_alias", new=AsyncMock()),
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_persist_project_api_key", new=AsyncMock()),
        patch.object(adapter, "_carry_spend_forward", side_effect=_mock_carry_forward),
    ):
        await adapter._recreate_project_budget_key_alias(
            service=mock_service,
            project_name="proj-y",
            budget_category=BudgetCategory.CLI,
            budget_id="bud-y",
            max_budget=Decimal("50.0"),
            budget_duration="30d",
            models=None,
            legacy_key_alias="proj-y-old",
        )

    # canonical lookup succeeded → _get_project_key_by_alias called once, not twice
    mock_service._get_project_key_by_alias.assert_called_once()
    assert len(carry_called_with) == 1
    assert carry_called_with[0]["carry_spend"] == 8.0


@pytest.mark.asyncio
async def test_update_project_budget_forwards_legacy_key_alias_from_metadata():
    """update_project_budget must forward metadata['legacy_key_alias'] to _recreate_project_budget_key_alias."""
    legacy_ref = "mdda-aiad"
    mock_service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    canonical_state = BudgetProviderState(
        provider="litellm",
        provider_budget_ref="codemie:project:mdda-aiad:category:cli",
        budget_reset_at="2026-09-01T00:00:00Z",
        sync_status=SyncStatus.OK,
    )

    with (
        patch.object(
            adapter, "_recreate_project_budget_key_alias", new=AsyncMock(return_value=canonical_state)
        ) as mock_recreate,
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()),
    ):
        await adapter.update_project_budget(
            budget_state=BudgetProviderState(
                provider="litellm",
                provider_budget_ref=legacy_ref,
                sync_status=SyncStatus.OK,
            ),
            project_name="mdda-aiad",
            budget_category=BudgetCategory.CLI,
            budget_id="bud-legacy",
            max_budget=Decimal("100.0"),
            budget_duration="30d",
            models=None,
            legacy_key_alias=legacy_ref,
        )

    _, kwargs = mock_recreate.call_args
    assert kwargs["legacy_key_alias"] == legacy_ref


@pytest.mark.asyncio
async def test_update_project_budget_no_legacy_key_alias_when_metadata_absent():
    """When metadata has no legacy_key_alias, None is forwarded so no carry happens."""
    mock_service = MagicMock()
    adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

    canonical_state = BudgetProviderState(
        provider="litellm",
        provider_budget_ref="codemie:project:mdda-aiad:category:cli",
        budget_reset_at="2026-09-01T00:00:00Z",
        sync_status=SyncStatus.OK,
    )

    with (
        patch.object(
            adapter, "_recreate_project_budget_key_alias", new=AsyncMock(return_value=canonical_state)
        ) as mock_recreate,
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()),
    ):
        await adapter.update_project_budget(
            budget_state=BudgetProviderState(
                provider="litellm",
                provider_budget_ref="mdda-aiad",
                sync_status=SyncStatus.OK,
            ),
            project_name="mdda-aiad",
            budget_category=BudgetCategory.CLI,
            budget_id="bud-legacy",
            max_budget=Decimal("100.0"),
            budget_duration="30d",
            models=None,
            legacy_key_alias=None,
        )

    _, kwargs = mock_recreate.call_args
    assert kwargs["legacy_key_alias"] is None


# ── Task 5: Adapter Verification Tests for models parameter ──


class TestAdapterModelsParameter:
    """Verify that adapter methods pass models parameter correctly."""

    @pytest.mark.asyncio
    async def test_build_project_budget_state_from_key_state_stores_models_in_metadata(self):
        """Verify _build_project_budget_state_from_key_state stores models in metadata."""
        key_state = {
            "key_hash": "key-hash-789",
            "key_alias": "codemie:project:proj-c:category:premium_models",
            "api_key": "sk-test-key-3",
            "budget_reset_at": "2026-09-04T12:00:00Z",
        }

        models = ["claude-3-opus", "claude-3-sonnet"]

        result = LiteLLMBudgetEnforcementProvider._build_project_budget_state_from_key_state(
            key_state=key_state,
            models=models,
        )

        assert result is not None
        assert result.provider == "litellm"
        assert result.provider_budget_ref == "codemie:project:proj-c:category:premium_models"
        assert result.metadata.get("models") == models
        assert result.sync_status == SyncStatus.OK

    @pytest.mark.asyncio
    async def test_build_project_budget_state_from_key_state_stores_none_models_in_metadata(self):
        """Verify _build_project_budget_state_from_key_state stores empty list when models is None."""
        key_state = {
            "key_hash": "key-hash-000",
            "key_alias": "codemie:project:proj-d:category:platform",
            "api_key": "sk-test-key-4",
            "budget_reset_at": "2026-09-04T12:00:00Z",
        }

        result = LiteLLMBudgetEnforcementProvider._build_project_budget_state_from_key_state(
            key_state=key_state,
            models=None,
        )

        assert result is not None
        assert result.provider == "litellm"
        assert result.metadata.get("models") == []
        assert result.sync_status == SyncStatus.OK

    @pytest.mark.asyncio
    async def test_recreate_project_budget_key_alias_passes_models_to_generate_project_key(self):
        """Verify _recreate_project_budget_key_alias passes models to _generate_project_key."""
        mock_service = SimpleNamespace(
            _generate_project_key=MagicMock(),
            _get_project_key_by_alias=MagicMock(),
        )
        adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

        key_state = {
            "key_hash": "key-hash-123",
            "key_alias": "codemie:project:proj-a:category:platform",
            "api_key": "sk-test-key",
            "budget_reset_at": "2026-09-04T00:00:00Z",
        }

        models = ["gpt-4", "gpt-3.5-turbo"]
        mock_service._generate_project_key.return_value = key_state
        mock_service._get_project_key_by_alias.return_value = None

        with (
            patch.object(adapter, "_delete_project_provider_key_alias", new=AsyncMock()),
            patch.object(adapter, "_delete_project_api_key", new=AsyncMock()),
            patch.object(adapter, "_persist_project_api_key", new=AsyncMock()),
            patch.object(adapter, "_carry_spend_forward", new=AsyncMock()),
            patch(
                "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
                side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs),
            ),
        ):
            result = await adapter._recreate_project_budget_key_alias(
                service=mock_service,
                project_name="proj-a",
                budget_category=BudgetCategory.PLATFORM,
                budget_id="budget-1",
                max_budget=Decimal("100.0"),
                budget_duration="30d",
                models=models,
            )

        assert result.provider == "litellm"
        assert result.metadata.get("models") == models
        mock_service._generate_project_key.assert_called_once()
        call_kwargs = mock_service._generate_project_key.call_args[1]
        assert call_kwargs["models"] == models

    @pytest.mark.asyncio
    async def test_recreate_project_budget_key_alias_passes_none_models_to_generate_project_key(self):
        """Verify _recreate_project_budget_key_alias passes models=None when not provided."""
        mock_service = SimpleNamespace(
            _generate_project_key=MagicMock(),
            _get_project_key_by_alias=MagicMock(),
        )
        adapter = LiteLLMBudgetEnforcementProvider(service=mock_service)

        key_state = {
            "key_hash": "key-hash-456",
            "key_alias": "codemie:project:proj-b:category:cli",
            "api_key": "sk-test-key-2",
            "budget_reset_at": "2026-09-04T00:00:00Z",
        }

        mock_service._generate_project_key.return_value = key_state
        mock_service._get_project_key_by_alias.return_value = None

        with (
            patch.object(adapter, "_delete_project_provider_key_alias", new=AsyncMock()),
            patch.object(adapter, "_delete_project_api_key", new=AsyncMock()),
            patch.object(adapter, "_persist_project_api_key", new=AsyncMock()),
            patch.object(adapter, "_carry_spend_forward", new=AsyncMock()),
            patch(
                "codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread",
                side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs),
            ),
        ):
            result = await adapter._recreate_project_budget_key_alias(
                service=mock_service,
                project_name="proj-b",
                budget_category=BudgetCategory.CLI,
                budget_id="budget-2",
                max_budget=Decimal("50.0"),
                budget_duration="30d",
                models=None,
            )

        assert result.provider == "litellm"
        assert result.metadata.get("models") == []
        mock_service._generate_project_key.assert_called_once()
        call_kwargs = mock_service._generate_project_key.call_args[1]
        assert call_kwargs["models"] is None


def _runtime_context(member_provider_metadata: dict) -> BudgetRuntimeContext:
    return BudgetRuntimeContext(
        scope="project",
        project_name="proj-a",
        budget_category=BudgetCategory.CLI,
        budget_id="budget-1",
        user_id="user-1",
        username="user@example.com",
        model="gpt-4.1",
        member_provider_metadata=member_provider_metadata,
    )


def test_resolve_runtime_core_sets_customer_header():
    adapter = LiteLLMBudgetEnforcementProvider(service=SimpleNamespace())
    ref = "codemie:project:proj-a:category:cli:user:user-1"

    result = adapter._resolve_runtime_core(context=_runtime_context({"provider_member_ref": ref}))

    assert result.headers == {"x-litellm-customer-id": ref}


def test_resolve_runtime_core_without_member_ref_has_no_headers():
    adapter = LiteLLMBudgetEnforcementProvider(service=SimpleNamespace())

    result = adapter._resolve_runtime_core(context=_runtime_context({}))

    assert result.headers == {}


class TestSyncMemberAllocationsBatch:
    """T3: sync_member_allocations entry point and the ordered fallback path."""

    @staticmethod
    def _service(**overrides: Any) -> SimpleNamespace:
        defaults = {
            "get_budget_info_map_strict": MagicMock(return_value={}),
            "sync_project_member_budget_assignment": MagicMock(),
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @pytest.mark.asyncio
    async def test_empty_input_makes_no_service_call(self):
        service = self._service()
        adapter = LiteLLMBudgetEnforcementProvider(service=service)

        result = await adapter.sync_member_allocations([])

        assert result == []
        service.get_budget_info_map_strict.assert_not_called()
        service.sync_project_member_budget_assignment.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_service_gives_failed_state_per_item(self):
        adapter = LiteLLMBudgetEnforcementProvider(service=self._service())
        items = [_member_sync_item(allocation_id="alloc-1"), _member_sync_item(allocation_id="alloc-2")]

        with patch.object(adapter, "_get_service", return_value=None):
            result = await adapter.sync_member_allocations(items)

        assert len(result) == 2
        for outcome in result:
            assert isinstance(outcome, BudgetProviderMemberState)
            assert outcome.sync_status == SyncStatus.FAILED

    @pytest.mark.asyncio
    async def test_duplicate_customer_skips_strict_lookup_and_runs_ordered_in_input_order(self):
        call_order: list[str] = []

        def sync_assignment(**kwargs):
            call_order.append(kwargs["user_id"])
            return SimpleNamespace(
                provider_member_ref=f"ref-{kwargs['user_id']}",
                budget_id=f"budget-{kwargs['user_id']}",
                budget_reset_at="2026-04-22T10:00:00Z",
                metadata={},
            )

        service = self._service(sync_project_member_budget_assignment=sync_assignment)
        adapter = LiteLLMBudgetEnforcementProvider(service=service)

        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1"),
            _member_sync_item(allocation_id="alloc-2", user_id="user-2"),
            _member_sync_item(allocation_id="alloc-3", user_id="user-1"),  # duplicate (project, category, user)
        ]

        executor = ProjectMemberSyncExecutor(workers=2)
        with patch(
            "codemie.enterprise.litellm.budget_provider_adapter.get_project_member_sync_executor",
            return_value=executor,
        ):
            result = await adapter.sync_member_allocations(items)
        await executor.shutdown()

        service.get_budget_info_map_strict.assert_not_called()
        assert call_order == ["user-1", "user-2", "user-1"]
        assert len(result) == 3
        for outcome in result:
            assert isinstance(outcome, BudgetProviderMemberState)
            assert outcome.sync_status == SyncStatus.OK

    @pytest.mark.parametrize(
        "lookup_error",
        [httpx.HTTPError("boom"), ValueError("invalid response")],
    )
    @pytest.mark.asyncio
    async def test_strict_lookup_failure_falls_back_to_ordered_calls_and_one_failure_does_not_stop_others(
        self, lookup_error
    ):
        call_order: list[str] = []

        def sync_assignment(**kwargs):
            call_order.append(kwargs["user_id"])
            if kwargs["user_id"] == "user-2":
                raise RuntimeError("provider exploded")
            return SimpleNamespace(
                provider_member_ref=f"ref-{kwargs['user_id']}",
                budget_id=f"budget-{kwargs['user_id']}",
                budget_reset_at="2026-04-22T10:00:00Z",
                metadata={},
            )

        service = self._service(
            get_budget_info_map_strict=MagicMock(side_effect=lookup_error),
            sync_project_member_budget_assignment=sync_assignment,
        )
        adapter = LiteLLMBudgetEnforcementProvider(service=service)

        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1"),
            _member_sync_item(allocation_id="alloc-2", user_id="user-2"),
            _member_sync_item(allocation_id="alloc-3", user_id="user-3"),
        ]

        executor = ProjectMemberSyncExecutor(workers=2)
        with patch(
            "codemie.enterprise.litellm.budget_provider_adapter.get_project_member_sync_executor",
            return_value=executor,
        ):
            result = await adapter.sync_member_allocations(items)
        await executor.shutdown()

        assert call_order == ["user-1", "user-2", "user-3"]
        assert len(result) == 3
        assert isinstance(result[0], BudgetProviderMemberState)
        assert result[0].sync_status == SyncStatus.OK
        assert isinstance(result[1], RuntimeError)
        assert isinstance(result[2], BudgetProviderMemberState)
        assert result[2].sync_status == SyncStatus.OK

    @pytest.mark.asyncio
    async def test_results_map_none_missing_budget_id_and_ok_with_metadata(self):
        def sync_assignment(**kwargs):
            if kwargs["user_id"] == "user-none":
                return None
            if kwargs["user_id"] == "user-missing":
                return SimpleNamespace(
                    provider_member_ref="ref-missing", budget_id=None, budget_reset_at=None, metadata={}
                )
            return SimpleNamespace(
                provider_member_ref="ref-ok",
                budget_id="budget-ok",
                budget_reset_at="2026-04-22T10:00:00Z",
                metadata={"foo": "bar"},
            )

        service = self._service(sync_project_member_budget_assignment=sync_assignment)
        adapter = LiteLLMBudgetEnforcementProvider(service=service)

        # budget_reset_at=None routes every item through the ordered single-member path.
        items = [
            _member_sync_item(allocation_id="alloc-none", user_id="user-none", budget_reset_at=None),
            _member_sync_item(allocation_id="alloc-missing", user_id="user-missing", budget_reset_at=None),
            _member_sync_item(allocation_id="alloc-ok", user_id="user-ok", budget_reset_at=None),
        ]

        executor = ProjectMemberSyncExecutor(workers=2)
        with patch(
            "codemie.enterprise.litellm.budget_provider_adapter.get_project_member_sync_executor",
            return_value=executor,
        ):
            result = await adapter.sync_member_allocations(items)
        await executor.shutdown()

        assert result[0].sync_status == SyncStatus.FAILED
        assert result[1].sync_status == SyncStatus.FAILED
        assert result[2].sync_status == SyncStatus.OK
        assert result[2].provider_budget_id == "budget-ok"
        assert result[2].metadata["foo"] == "bar"
        assert result[2].metadata["internal_budget"] is True
        assert result[2].metadata["budget_scope"] == "project_member"


class _FastPathService:
    """Thread-safe service double for the batch fast path (ensure + assign) and the ordered path."""

    def __init__(
        self,
        *,
        snapshot: dict[str, Any] | None = None,
        ensure_behaviour: dict[str, str] | None = None,
        failing_assign_users: set[str] | None = None,
        assign_hook: Any = None,
    ) -> None:
        self._lock = threading.Lock()
        self.snapshot = snapshot or {}
        self.ensure_behaviour = ensure_behaviour or {}
        self.failing_assign_users = failing_assign_users or set()
        self.assign_hook = assign_hook
        self.lookup_calls: list[dict[str, Any]] = []
        self.ensure_calls: list[dict[str, Any]] = []
        self.ensure_results: list[Any] = []
        self.assign_calls: list[dict[str, Any]] = []
        self.ordered_calls: list[dict[str, Any]] = []
        self.events: list[str] = []

    def get_budget_info_map_strict(self, *, budget_ids):
        with self._lock:
            self.lookup_calls.append({"budget_ids": list(budget_ids)})
        return {budget_id: budget for budget_id, budget in self.snapshot.items() if budget_id in budget_ids}

    def ensure_project_member_budget(self, **kwargs):
        with self._lock:
            self.ensure_calls.append(kwargs)
            sequence = len(self.ensure_calls)
        behaviour = self.ensure_behaviour.get(kwargs["budget_id"], "ok")
        if behaviour == "raise":
            raise RuntimeError(f"ensure exploded for {kwargs['budget_id']}")
        if behaviour == "failed_create":
            result = SimpleNamespace(budget=None, write_succeeded=False)
        elif behaviour == "failed_update":
            result = SimpleNamespace(budget=kwargs["existing_budget"], write_succeeded=False)
        else:
            budget = SimpleNamespace(
                budget_id=kwargs["budget_id"], budget_reset_at=kwargs["budget_reset_at"], version=sequence
            )
            result = SimpleNamespace(budget=budget, write_succeeded=True)
        with self._lock:
            self.ensure_results.append(result)
        return result

    def assign_project_member_budget(self, **kwargs):
        user_id = kwargs["user_id"]
        with self._lock:
            self.assign_calls.append(kwargs)
            self.events.append(f"start:{user_id}")
        try:
            if self.assign_hook is not None:
                self.assign_hook(kwargs)
            if user_id in self.failing_assign_users:
                raise RuntimeError(f"assign exploded for {user_id}")
            return SimpleNamespace(
                provider_member_ref=f"ref-{user_id}",
                budget_id=kwargs["budget_id"],
                budget_reset_at=kwargs["budget_reset_at"],
                metadata={"assigned_with": kwargs["budget"]},
            )
        finally:
            with self._lock:
                self.events.append(f"end:{user_id}")

    def sync_project_member_budget_assignment(self, **kwargs):
        with self._lock:
            self.ordered_calls.append(kwargs)
        return SimpleNamespace(
            provider_member_ref=f"ordered-{kwargs['user_id']}",
            budget_id=kwargs["effective_budget_id"],
            budget_reset_at=kwargs["budget_reset_at"],
            metadata={},
        )


async def _run_batch(service: Any, items: list[MemberSyncItem], *, workers: int = 8) -> list[Any]:
    adapter = LiteLLMBudgetEnforcementProvider(service=service)
    executor = ProjectMemberSyncExecutor(workers=workers)
    try:
        with patch(
            "codemie.enterprise.litellm.budget_provider_adapter.get_project_member_sync_executor",
            return_value=executor,
        ):
            return await adapter.sync_member_allocations(items)
    finally:
        await executor.shutdown()


def _existing_budget(budget_id: str) -> SimpleNamespace:
    return SimpleNamespace(budget_id=budget_id, budget_reset_at="2026-04-22T10:00:00Z", version=0)


class TestSyncMemberAllocationsFastPath:
    """T4: parallel fast path (runs per effective ID, parallel IDs, per-ID fallbacks)."""

    @pytest.mark.asyncio
    async def test_shared_category_with_remainder_runs_two_ensures_and_waits_for_run_one_assigns(self):
        shared_id = "proj-budget-1:shared"
        run_one_users = ["user-1", "user-2", "user-3", "user-4"]
        started_run_one: list[str] = []
        all_run_one_started = threading.Event()
        counter_lock = threading.Lock()

        def assign_hook(kwargs):
            if kwargs["user_id"] not in run_one_users:
                return
            with counter_lock:
                started_run_one.append(kwargs["user_id"])
                if len(started_run_one) == len(run_one_users):
                    all_run_one_started.set()
            # Every run-1 assignment is in flight at once (parallel), then holds briefly so a
            # run-2 assignment that does not wait would visibly start before these end.
            assert all_run_one_started.wait(timeout=5)
            time.sleep(0.05)

        existing = _existing_budget(shared_id)
        service = _FastPathService(snapshot={shared_id: existing}, assign_hook=assign_hook)
        items = [
            _member_sync_item(allocation_id=f"alloc-{n}", user_id=f"user-{n}", shared_budget_id=shared_id)
            for n in range(1, 5)
        ]
        items.append(
            _member_sync_item(
                allocation_id="alloc-5", user_id="user-5", shared_budget_id=shared_id, effective_max_budget=5.0
            )
        )

        result = await _run_batch(service, items)

        assert service.lookup_calls == [{"budget_ids": [shared_id]}]
        assert len(service.ensure_calls) == 2
        assert service.ensure_calls[0]["existing_budget"] is existing
        assert service.ensure_calls[0]["max_budget"] == 25.0
        assert service.ensure_calls[1]["existing_budget"] is service.ensure_results[0].budget
        assert service.ensure_calls[1]["max_budget"] == 5.0
        assert len(service.assign_calls) == 5
        assert service.ordered_calls == []

        run_two_start = service.events.index("start:user-5")
        for user_id in run_one_users:
            assert service.events.index(f"end:{user_id}") < run_two_start

        for n, outcome in enumerate(result, start=1):
            assert isinstance(outcome, BudgetProviderMemberState)
            assert outcome.sync_status == SyncStatus.OK
            assert outcome.provider_member_ref == f"ref-user-{n}"
            assert outcome.provider_budget_id == shared_id
        assert result[0].metadata["assigned_with"] is service.ensure_results[0].budget
        assert result[4].metadata["assigned_with"] is service.ensure_results[1].budget

    @pytest.mark.asyncio
    async def test_interleaved_ids_map_outcomes_by_index(self):
        service = _FastPathService(snapshot={"budget-a": _existing_budget("budget-a")})
        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1", effective_budget_id="budget-a"),
            _member_sync_item(allocation_id="alloc-2", user_id="user-2", effective_budget_id="budget-b"),
            _member_sync_item(allocation_id="alloc-3", user_id="user-3", effective_budget_id="budget-a"),
        ]

        result = await _run_batch(service, items)

        assert [outcome.provider_member_ref for outcome in result] == ["ref-user-1", "ref-user-2", "ref-user-3"]
        assert [outcome.provider_budget_id for outcome in result] == ["budget-a", "budget-b", "budget-a"]
        assert sorted(call["budget_id"] for call in service.ensure_calls) == ["budget-a", "budget-b"]
        assert service.ordered_calls == []

    @pytest.mark.asyncio
    async def test_id_missing_from_strict_map_ensures_with_no_existing_budget(self):
        service = _FastPathService(snapshot={"budget-a": _existing_budget("budget-a")})
        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1", effective_budget_id="budget-a"),
            _member_sync_item(allocation_id="alloc-2", user_id="user-2", effective_budget_id="budget-b"),
        ]

        await _run_batch(service, items)

        ensure_by_id = {call["budget_id"]: call for call in service.ensure_calls}
        assert ensure_by_id["budget-a"]["existing_budget"] is service.snapshot["budget-a"]
        assert ensure_by_id["budget-b"]["existing_budget"] is None

    @pytest.mark.asyncio
    async def test_reset_none_run_and_later_runs_go_ordered_while_other_id_stays_fast(self):
        service = _FastPathService(
            snapshot={"budget-x": _existing_budget("budget-x"), "budget-y": _existing_budget("budget-y")}
        )
        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1", effective_budget_id="budget-x"),
            _member_sync_item(allocation_id="alloc-2", user_id="user-2", effective_budget_id="budget-x"),
            _member_sync_item(
                allocation_id="alloc-3", user_id="user-3", effective_budget_id="budget-x", budget_reset_at=None
            ),
            _member_sync_item(allocation_id="alloc-4", user_id="user-4", effective_budget_id="budget-x"),
            _member_sync_item(allocation_id="alloc-5", user_id="user-5", effective_budget_id="budget-y"),
        ]

        result = await _run_batch(service, items)

        assert [call["user_id"] for call in service.ordered_calls] == ["user-3", "user-4"]
        assert sorted(call["user_id"] for call in service.assign_calls) == ["user-1", "user-2", "user-5"]
        assert [call["budget_id"] for call in service.ensure_calls].count("budget-x") == 1
        assert [outcome.provider_member_ref for outcome in result] == [
            "ref-user-1",
            "ref-user-2",
            "ordered-user-3",
            "ordered-user-4",
            "ref-user-5",
        ]

    @pytest.mark.asyncio
    async def test_failed_update_assigns_first_member_with_stale_budget_and_rest_go_ordered(self):
        stale = _existing_budget("budget-x")
        service = _FastPathService(
            snapshot={"budget-x": stale, "budget-y": _existing_budget("budget-y")},
            ensure_behaviour={"budget-x": "failed_update"},
        )
        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1", effective_budget_id="budget-x"),
            _member_sync_item(allocation_id="alloc-2", user_id="user-2", effective_budget_id="budget-x"),
            _member_sync_item(allocation_id="alloc-3", user_id="user-3", effective_budget_id="budget-y"),
            _member_sync_item(
                allocation_id="alloc-4", user_id="user-4", effective_budget_id="budget-x", effective_max_budget=5.0
            ),
        ]

        result = await _run_batch(service, items)

        assign_by_user = {call["user_id"]: call for call in service.assign_calls}
        assert sorted(assign_by_user) == ["user-1", "user-3"]
        assert assign_by_user["user-1"]["budget"] is stale
        assert [call["user_id"] for call in service.ordered_calls] == ["user-2", "user-4"]
        assert [call["budget_id"] for call in service.ensure_calls].count("budget-x") == 1
        assert [outcome.provider_member_ref for outcome in result] == [
            "ref-user-1",
            "ordered-user-2",
            "ref-user-3",
            "ordered-user-4",
        ]
        assert result[0].sync_status == SyncStatus.OK

    @pytest.mark.asyncio
    async def test_failed_create_records_empty_result_and_rest_go_ordered(self):
        service = _FastPathService(
            snapshot={"budget-y": _existing_budget("budget-y")},
            ensure_behaviour={"budget-x": "failed_create"},
        )
        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1", effective_budget_id="budget-x"),
            _member_sync_item(allocation_id="alloc-2", user_id="user-2", effective_budget_id="budget-x"),
            _member_sync_item(allocation_id="alloc-3", user_id="user-3", effective_budget_id="budget-y"),
        ]

        result = await _run_batch(service, items)

        assert [call["user_id"] for call in service.assign_calls] == ["user-3"]
        assert [call["user_id"] for call in service.ordered_calls] == ["user-2"]
        assert isinstance(result[0], BudgetProviderMemberState)
        assert result[0].sync_status == SyncStatus.FAILED
        assert result[0].provider_budget_id is None
        assert result[1].provider_member_ref == "ordered-user-2"
        assert result[2].provider_member_ref == "ref-user-3"

    @pytest.mark.asyncio
    async def test_ensure_raising_is_first_member_outcome_without_assign_and_rest_go_ordered(self):
        service = _FastPathService(
            snapshot={"budget-x": _existing_budget("budget-x"), "budget-y": _existing_budget("budget-y")},
            ensure_behaviour={"budget-x": "raise"},
        )
        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1", effective_budget_id="budget-x"),
            _member_sync_item(allocation_id="alloc-2", user_id="user-2", effective_budget_id="budget-y"),
            _member_sync_item(allocation_id="alloc-3", user_id="user-3", effective_budget_id="budget-x"),
            _member_sync_item(
                allocation_id="alloc-4", user_id="user-4", effective_budget_id="budget-x", effective_max_budget=5.0
            ),
        ]

        result = await _run_batch(service, items)

        assert isinstance(result[0], RuntimeError)
        assert [call["user_id"] for call in service.assign_calls] == ["user-2"]
        assert [call["user_id"] for call in service.ordered_calls] == ["user-3", "user-4"]
        assert [call["budget_id"] for call in service.ensure_calls].count("budget-x") == 1
        assert result[1].provider_member_ref == "ref-user-2"
        assert result[2].provider_member_ref == "ordered-user-3"
        assert result[3].provider_member_ref == "ordered-user-4"

    @pytest.mark.asyncio
    async def test_one_assignment_raising_leaves_siblings_and_later_runs_processed_without_retry(self):
        shared_id = "proj-budget-1:shared"
        service = _FastPathService(
            snapshot={shared_id: _existing_budget(shared_id)},
            failing_assign_users={"user-2"},
        )
        items = [
            _member_sync_item(allocation_id=f"alloc-{n}", user_id=f"user-{n}", shared_budget_id=shared_id)
            for n in range(1, 4)
        ]
        items.append(
            _member_sync_item(
                allocation_id="alloc-4", user_id="user-4", shared_budget_id=shared_id, effective_max_budget=5.0
            )
        )

        result = await _run_batch(service, items)

        assert sorted(call["user_id"] for call in service.assign_calls) == ["user-1", "user-2", "user-3", "user-4"]
        assert len(service.ensure_calls) == 2
        assert service.ordered_calls == []
        assert result[0].sync_status == SyncStatus.OK
        assert isinstance(result[1], RuntimeError)
        assert result[2].sync_status == SyncStatus.OK
        assert result[3].sync_status == SyncStatus.OK


class TestSyncMemberAllocationsReviewFixes:
    """CR-003 started log per item; CR-004 conversion errors stay per item."""

    @pytest.mark.asyncio
    async def test_batch_emits_sync_started_log_per_item(self):
        service = _FastPathService(snapshot={"budget-a": _existing_budget("budget-a")})
        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1", effective_budget_id="budget-a"),
            _member_sync_item(
                allocation_id="alloc-2", user_id="user-2", effective_budget_id="budget-b", effective_max_budget=7.0
            ),
        ]

        with patch("codemie.enterprise.litellm.budget_provider_adapter.logger") as mock_logger:
            await _run_batch(service, items)

        started = [
            call.args[0]
            for call in mock_logger.debug.call_args_list
            if "budget_event=provider_member_budget_sync_started" in call.args[0]
        ]
        assert len(started) == 2
        assert "effective_budget_id='budget-a'" in started[0]
        assert "allocation_id='alloc-1'" in started[0]
        assert "user_id='user-1'" in started[0]
        assert "resolved_max_budget=25.0" in started[0]
        assert "enforcement_override=False" in started[0]
        assert "allocated_soft_budget=20.0" in started[0]
        assert "effective_budget_id='budget-b'" in started[1]
        assert "resolved_max_budget=7.0" in started[1]
        assert "enforcement_override=True" in started[1]

    @pytest.mark.asyncio
    async def test_ordered_path_conversion_error_is_item_outcome(self):
        service = _FastPathService()
        good = service.sync_project_member_budget_assignment

        def sync_assignment(**kwargs):
            if kwargs["user_id"] == "user-1":
                return SimpleNamespace(provider_member_ref="ref", budget_id="bud", budget_reset_at=None, metadata=None)
            return good(**kwargs)

        service.sync_project_member_budget_assignment = sync_assignment
        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1"),
            _member_sync_item(allocation_id="alloc-2", user_id="user-2"),
            _member_sync_item(allocation_id="alloc-3", user_id="user-1"),  # duplicate -> ordered path
        ]

        result = await _run_batch(service, items)

        assert isinstance(result[0], TypeError)
        assert result[1].sync_status == SyncStatus.OK
        assert isinstance(result[2], TypeError)

    @pytest.mark.asyncio
    async def test_malformed_ensure_result_falls_back_without_aborting_batch(self):
        service = _FastPathService(snapshot={"budget-a": _existing_budget("budget-a")})
        original_ensure = service.ensure_project_member_budget

        def ensure(**kwargs):
            if kwargs["budget_id"] == "budget-a":
                return object()  # no write_succeeded / budget attributes
            return original_ensure(**kwargs)

        service.ensure_project_member_budget = ensure
        items = [
            _member_sync_item(allocation_id="alloc-1", user_id="user-1", effective_budget_id="budget-a"),
            _member_sync_item(allocation_id="alloc-2", user_id="user-2", effective_budget_id="budget-a"),
            _member_sync_item(allocation_id="alloc-3", user_id="user-3", effective_budget_id="budget-b"),
        ]

        result = await _run_batch(service, items)

        assert isinstance(result[0], AttributeError)
        assert result[1].sync_status == SyncStatus.OK
        assert result[1].provider_member_ref == "ordered-user-2"
        assert result[2].sync_status == SyncStatus.OK
        assert result[2].provider_member_ref == "ref-user-3"
