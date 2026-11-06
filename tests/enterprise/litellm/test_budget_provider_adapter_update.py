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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.enterprise.litellm.budget_provider_adapter import LiteLLMBudgetEnforcementProvider
from codemie.service.budget.budget_enums import BudgetCategory, SyncStatus
from codemie.service.budget.provider import BudgetProviderState

_PROJECT_NAME = "proj-a"
_RECREATED_REF = "codemie:project:proj-a:category:cli"


def _recreated_state() -> BudgetProviderState:
    return BudgetProviderState(
        provider="litellm",
        provider_budget_ref=_RECREATED_REF,
        budget_reset_at="2026-09-01T00:00:00Z",
        sync_status=SyncStatus.OK,
    )


async def _update(adapter: LiteLLMBudgetEnforcementProvider, provider_budget_ref: str | None) -> BudgetProviderState:
    return await adapter.update_project_budget(
        budget_state=BudgetProviderState(
            provider="litellm", provider_budget_ref=provider_budget_ref, sync_status=SyncStatus.FAILED
        ),
        project_name=_PROJECT_NAME,
        budget_category=BudgetCategory.CLI,
        budget_id="bud-1",
        max_budget=Decimal("100.0"),
        budget_duration="30d",
        models=None,
    )


@pytest.mark.asyncio
async def test_update_without_provider_ref_recreates_key():
    adapter = LiteLLMBudgetEnforcementProvider(service=MagicMock())

    with (
        patch.object(
            adapter, "_recreate_project_budget_key_alias", new=AsyncMock(return_value=_recreated_state())
        ) as recreate,
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()) as delete_api_key,
    ):
        state = await _update(adapter, provider_budget_ref=None)

    recreate.assert_awaited_once()
    assert recreate.await_args.kwargs["project_name"] == _PROJECT_NAME
    assert recreate.await_args.kwargs["budget_category"] == BudgetCategory.CLI
    assert state.provider_budget_ref == _RECREATED_REF
    assert state.sync_status == SyncStatus.OK
    delete_api_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_with_replaced_provider_ref_deletes_old_key():
    adapter = LiteLLMBudgetEnforcementProvider(service=MagicMock())

    with (
        patch.object(adapter, "_recreate_project_budget_key_alias", new=AsyncMock(return_value=_recreated_state())),
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()) as delete_api_key,
    ):
        await _update(adapter, provider_budget_ref="legacy-alias")

    delete_api_key.assert_awaited_once_with(project_name=_PROJECT_NAME, key_alias="legacy-alias")


@pytest.mark.asyncio
async def test_update_without_provider_service_fails_without_touching_keys():
    adapter = LiteLLMBudgetEnforcementProvider()

    with (
        patch.object(adapter, "_get_service", return_value=None),
        patch.object(adapter, "_recreate_project_budget_key_alias", new=AsyncMock()) as recreate,
        patch.object(adapter, "_delete_project_api_key", new=AsyncMock()) as delete_api_key,
    ):
        state = await _update(adapter, provider_budget_ref="legacy-alias")

    assert state.sync_status == SyncStatus.FAILED
    recreate.assert_not_awaited()
    delete_api_key.assert_not_awaited()
