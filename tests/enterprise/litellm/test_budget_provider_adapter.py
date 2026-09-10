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
from codemie.enterprise.litellm.constants import LITELLM_CUSTOMER_ID_HEADER
from codemie.service.budget.budget_enums import BudgetCategory, BudgetScope, SyncStatus
from codemie.service.budget.provider import (
    BudgetProviderState,
    BudgetResetReconciliationTarget,
    BudgetRuntimeContext,
    MemberBudgetSpendSnapshot,
)


def test_project_runtime_uses_customer_header_without_body_user():
    provider_member_ref = (
        "codemie:project:a-project-name-that-makes-the-id-too-long:category:cli:user:"
        "12345678-1234-1234-1234-123456789012"
    )
    context = BudgetRuntimeContext(
        scope=BudgetScope.PROJECT,
        project_name="a-project-name-that-makes-the-id-too-long",
        budget_category=BudgetCategory.CLI,
        budget_id="project-cli-budget",
        user_id="12345678-1234-1234-1234-123456789012",
        user_email="user@example.com",
        model="gpt-5.6-luna-2026-07-09",
        member_provider_metadata={"provider_member_ref": provider_member_ref},
    )

    settings_module = SimpleNamespace(SettingsService=MagicMock())
    with patch.dict("sys.modules", {"codemie.service.settings.settings": settings_module}):
        result = LiteLLMBudgetEnforcementProvider(service=MagicMock()).resolve_runtime_sync(context=context)

    assert len(provider_member_ref) > 64
    assert result.headers == {LITELLM_CUSTOMER_ID_HEADER: provider_member_ref}
    assert result.body_overrides == {}


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
