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

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.enterprise.litellm.project_member_runtime_sync import (
    ensure_project_member_runtime_ready,
    ensure_project_member_runtime_ready_sync,
    resync_project_member_allocations,
)
from codemie.service.budget.budget_enums import BudgetCategory, BudgetScope, SyncStatus
from codemie.service.budget.budget_resolution_service import (
    ResolvedBudgetContext,
    _resolution_cache,
    clear_budget_resolution_cache,
)
from codemie.service.budget.provider import BudgetProviderMemberState


USER_ID = "user-1"
USER_EMAIL = "user-1@example.com"
PROJECT_NAME = "project-a"
BUDGET_CATEGORY = BudgetCategory.CLI
BUDGET_ID = "budget-1"
ALLOCATION_ID = "allocation-1"


@pytest.fixture(autouse=True)
def clear_runtime_cache():
    clear_budget_resolution_cache()
    yield
    clear_budget_resolution_cache()


@asynccontextmanager
async def _mock_session_ctx(session):
    yield session


def _project_context(
    *,
    member_provider_metadata: dict | None = None,
    budget_id: str | None = BUDGET_ID,
) -> ResolvedBudgetContext:
    return ResolvedBudgetContext(
        scope=BudgetScope.PROJECT,
        project_name=PROJECT_NAME,
        budget_category=BUDGET_CATEGORY,
        budget_id=budget_id,
        member_allocation_id=ALLOCATION_ID,
        provider_metadata={},
        member_provider_metadata=member_provider_metadata or {},
    )


@pytest.mark.asyncio
async def test_syncs_member_allocation_and_persists_runtime_metadata():
    session = AsyncMock()
    resolved = _project_context(member_provider_metadata={})
    allocation = SimpleNamespace(id=ALLOCATION_ID, allocated_max_budget=50.0)
    budget = SimpleNamespace(
        budget_id=BUDGET_ID, budget_duration="30d", budget_reset_at="2026-04-22T10:00:00Z", max_budget=100.0
    )
    provider_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="member-ref-1",
        provider_budget_id="member-budget-1",
        budget_reset_at="2026-04-22T10:00:00Z",
        sync_status=SyncStatus.OK,
        metadata={"team_id": "team-1"},
    )
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=provider_state))
    cache_key = (PROJECT_NAME, BUDGET_CATEGORY.value, USER_ID)
    _resolution_cache[cache_key] = resolved

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ) as mock_update_provider_metadata,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
            return_value=provider,
        ),
    ):
        await ensure_project_member_runtime_ready(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            project_name=PROJECT_NAME,
            budget_category=BUDGET_CATEGORY,
        )

    provider.sync_member_allocation.assert_awaited_once_with(
        allocation=allocation, budget=budget, effective_max_budget=50.0
    )
    mock_update_provider_metadata.assert_awaited_once()
    update_call = mock_update_provider_metadata.await_args.kwargs
    assert update_call["allocation_id"] == ALLOCATION_ID
    assert update_call["sync_status"] == SyncStatus.OK
    assert update_call["budget_reset_at"] == "2026-04-22T10:00:00Z"
    persisted_metadata = update_call["provider_metadata"]
    assert persisted_metadata["provider"] == "litellm"
    assert persisted_metadata["sync_status"] == SyncStatus.OK
    assert persisted_metadata["raw"]["team_id"] == "team-1"
    assert persisted_metadata["raw"]["provider_member_ref"] == "member-ref-1"
    assert persisted_metadata["raw"]["provider_budget_id"] == "member-budget-1"
    assert "last_synced_at" in persisted_metadata
    assert cache_key not in _resolution_cache
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_sync_when_member_provider_ref_already_exists():
    session = AsyncMock()
    resolved = _project_context(
        member_provider_metadata={"provider_member_ref": "existing-ref", "provider_budget_id": "member-budget-1"}
    )

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(),
        ) as mock_get_allocation,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
        ) as mock_get_active_provider,
    ):
        await ensure_project_member_runtime_ready(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            project_name=PROJECT_NAME,
            budget_category=BUDGET_CATEGORY,
        )

    mock_get_allocation.assert_not_called()
    mock_get_active_provider.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_skips_sync_when_member_provider_ref_exists_in_raw_metadata():
    session = AsyncMock()
    resolved = _project_context(
        member_provider_metadata={
            "raw": {"provider_member_ref": "existing-ref", "provider_budget_id": "member-budget-1"}
        }
    )

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(),
        ) as mock_get_allocation,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
        ) as mock_get_active_provider,
    ):
        await ensure_project_member_runtime_ready(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            project_name=PROJECT_NAME,
            budget_category=BUDGET_CATEGORY,
        )

    mock_get_allocation.assert_not_called()
    mock_get_active_provider.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_resyncs_when_provider_member_ref_exists_but_budget_id_is_missing():
    session = AsyncMock()
    resolved = _project_context(member_provider_metadata={"provider_member_ref": "existing-ref"})
    allocation = SimpleNamespace(id=ALLOCATION_ID, allocated_max_budget=50.0)
    budget = SimpleNamespace(
        budget_id=BUDGET_ID, budget_duration="30d", budget_reset_at="2026-04-22T10:00:00Z", max_budget=100.0
    )
    provider_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="existing-ref",
        provider_budget_id="member-budget-1",
        budget_reset_at="2026-04-22T10:00:00Z",
        sync_status=SyncStatus.OK,
        metadata={},
    )
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=provider_state))

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
            return_value=provider,
        ),
    ):
        await ensure_project_member_runtime_ready(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            project_name=PROJECT_NAME,
            budget_category=BUDGET_CATEGORY,
        )

    provider.sync_member_allocation.assert_awaited_once_with(
        allocation=allocation, budget=budget, effective_max_budget=50.0
    )


@pytest.mark.asyncio
async def test_skips_sync_when_resolved_scope_is_not_project():
    session = AsyncMock()
    resolved = ResolvedBudgetContext(
        scope=BudgetScope.GLOBAL,
        project_name=None,
        budget_category=BUDGET_CATEGORY,
        budget_id=None,
    )

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
        ) as mock_get_active_provider,
    ):
        await ensure_project_member_runtime_ready(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            project_name=PROJECT_NAME,
            budget_category=BUDGET_CATEGORY,
        )

    mock_get_active_provider.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_raises_runtime_error_when_allocation_is_missing():
    session = AsyncMock()
    resolved = _project_context(member_provider_metadata={})

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(),
        ) as mock_budget_get,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
        ) as mock_get_active_provider,
    ):
        with pytest.raises(RuntimeError, match="allocation missing"):
            await ensure_project_member_runtime_ready(
                user_id=USER_ID,
                user_email=USER_EMAIL,
                project_name=PROJECT_NAME,
                budget_category=BUDGET_CATEGORY,
            )

    mock_budget_get.assert_not_called()
    mock_get_active_provider.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_raises_runtime_error_when_resolved_budget_id_is_missing():
    session = AsyncMock()
    resolved = _project_context(member_provider_metadata={}, budget_id=None)
    allocation = SimpleNamespace(id=ALLOCATION_ID)

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(),
        ) as mock_budget_get,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
        ) as mock_get_active_provider,
    ):
        with pytest.raises(RuntimeError, match="missing budget_id"):
            await ensure_project_member_runtime_ready(
                user_id=USER_ID,
                user_email=USER_EMAIL,
                project_name=PROJECT_NAME,
                budget_category=BUDGET_CATEGORY,
            )

    mock_budget_get.assert_not_called()
    mock_get_active_provider.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_raises_runtime_error_when_budget_is_missing():
    session = AsyncMock()
    resolved = _project_context(member_provider_metadata={})
    allocation = SimpleNamespace(id=ALLOCATION_ID)

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
        ) as mock_get_active_provider,
    ):
        with pytest.raises(RuntimeError, match="Budget not found"):
            await ensure_project_member_runtime_ready(
                user_id=USER_ID,
                user_email=USER_EMAIL,
                project_name=PROJECT_NAME,
                budget_category=BUDGET_CATEGORY,
            )

    mock_get_active_provider.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_raises_runtime_error_when_provider_sync_raises():
    session = AsyncMock()
    resolved = _project_context(member_provider_metadata={})
    allocation = SimpleNamespace(id=ALLOCATION_ID, allocated_max_budget=50.0)
    budget = SimpleNamespace(budget_id=BUDGET_ID, max_budget=100.0)
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(side_effect=RuntimeError("provider boom")))

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ) as mock_update_provider_metadata,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
            return_value=provider,
        ),
    ):
        with pytest.raises(RuntimeError, match="provider boom"):
            await ensure_project_member_runtime_ready(
                user_id=USER_ID,
                user_email=USER_EMAIL,
                project_name=PROJECT_NAME,
                budget_category=BUDGET_CATEGORY,
            )

    mock_update_provider_metadata.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_raises_runtime_error_when_provider_sync_status_is_not_ok_or_noop():
    session = AsyncMock()
    resolved = _project_context(member_provider_metadata={})
    allocation = SimpleNamespace(id=ALLOCATION_ID, allocated_max_budget=50.0)
    budget = SimpleNamespace(budget_id=BUDGET_ID, max_budget=100.0)
    provider_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="member-ref-1",
        sync_status=SyncStatus.FAILED,
        metadata={},
    )
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=provider_state))

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ) as mock_update_provider_metadata,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
            return_value=provider,
        ),
    ):
        with pytest.raises(RuntimeError, match="unexpected sync_status"):
            await ensure_project_member_runtime_ready(
                user_id=USER_ID,
                user_email=USER_EMAIL,
                project_name=PROJECT_NAME,
                budget_category=BUDGET_CATEGORY,
            )

    mock_update_provider_metadata.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_runtime_sync_proceeds_when_enforcement_disabled():
    """With enforcement OFF, sync should still run for tracking."""
    session = AsyncMock()
    resolved = _project_context(member_provider_metadata={})
    allocation = SimpleNamespace(id=ALLOCATION_ID, allocated_max_budget=50.0)
    budget = SimpleNamespace(
        budget_id=BUDGET_ID, budget_duration="30d", budget_reset_at="2026-04-22T10:00:00Z", max_budget=100.0
    )
    provider_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="member-ref-1",
        provider_budget_id="member-budget-1",
        budget_reset_at="2026-04-22T10:00:00Z",
        sync_status=SyncStatus.OK,
        metadata={},
    )
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=provider_state))

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=False,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
            return_value=provider,
        ),
    ):
        await ensure_project_member_runtime_ready(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            project_name=PROJECT_NAME,
            budget_category=BUDGET_CATEGORY,
        )

    provider.sync_member_allocation.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_sync_passes_full_project_budget_when_enforcement_disabled():
    """With enforcement OFF, effective_max_budget should equal budget.max_budget."""
    session = AsyncMock()
    resolved = _project_context(member_provider_metadata={})
    allocation = SimpleNamespace(id=ALLOCATION_ID, allocated_max_budget=50.0)
    budget = SimpleNamespace(
        budget_id=BUDGET_ID, budget_duration="30d", budget_reset_at="2026-04-22T10:00:00Z", max_budget=200.0
    )
    provider_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="member-ref-1",
        provider_budget_id="member-budget-1",
        budget_reset_at="2026-04-22T10:00:00Z",
        sync_status=SyncStatus.OK,
        metadata={},
    )
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=provider_state))

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.service.settings.settings.SettingsService.get_enforce_member_spend_limits",
            return_value=False,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_resolution_service.resolve",
            new=AsyncMock(return_value=resolved),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project_category_user",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
            return_value=provider,
        ),
    ):
        await ensure_project_member_runtime_ready(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            project_name=PROJECT_NAME,
            budget_category=BUDGET_CATEGORY,
        )

    provider.sync_member_allocation.assert_awaited_once_with(
        allocation=allocation, budget=budget, effective_max_budget=200.0
    )


def test_sync_wrapper_uses_asyncio_run_when_no_running_event_loop():
    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.asyncio.get_running_loop",
            side_effect=RuntimeError("no running loop"),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.asyncio.run",
            return_value=None,
        ) as mock_run,
    ):
        ensure_project_member_runtime_ready_sync(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            project_name=PROJECT_NAME,
            budget_category=BUDGET_CATEGORY,
        )

    mock_run.assert_called_once()
    scheduled_coro = mock_run.call_args.args[0]
    assert asyncio.iscoroutine(scheduled_coro)
    scheduled_coro.close()


def test_sync_wrapper_uses_helper_thread_when_loop_exists():
    mock_loop = MagicMock()

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.asyncio.get_running_loop",
            return_value=mock_loop,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.threading.Thread",
        ) as mock_thread,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.asyncio.run",
            return_value=None,
        ) as mock_run,
    ):
        mock_thread.return_value.start.side_effect = lambda: mock_thread.call_args.kwargs["target"]()
        ensure_project_member_runtime_ready_sync(
            user_id=USER_ID,
            user_email=USER_EMAIL,
            project_name=PROJECT_NAME,
            budget_category=BUDGET_CATEGORY,
        )

    mock_thread.assert_called_once()
    mock_thread.return_value.start.assert_called_once()
    mock_thread.return_value.join.assert_called_once()
    mock_run.assert_called_once()
    scheduled_coro = mock_run.call_args.args[0]
    assert asyncio.iscoroutine(scheduled_coro)
    scheduled_coro.close()


def test_sync_wrapper_propagates_exception_from_helper_thread():
    mock_loop = MagicMock()

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.asyncio.get_running_loop",
            return_value=mock_loop,
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.ensure_project_member_runtime_ready",
            new=AsyncMock(side_effect=RuntimeError("thread boom")),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.threading.Thread",
        ) as mock_thread,
    ):
        mock_thread.return_value.start.side_effect = lambda: mock_thread.call_args.kwargs["target"]()
        with pytest.raises(RuntimeError, match="thread boom"):
            ensure_project_member_runtime_ready_sync(
                user_id=USER_ID,
                user_email=USER_EMAIL,
                project_name=PROJECT_NAME,
                budget_category=BUDGET_CATEGORY,
            )

    mock_thread.assert_called_once()
    mock_thread.return_value.start.assert_called_once()
    mock_thread.return_value.join.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for resync_project_member_allocations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resync_skips_allocations_without_provider_member_ref():
    """Unsynced allocations (no provider_member_ref) are skipped."""
    session = AsyncMock()
    allocation_no_ref = SimpleNamespace(
        id="alloc-1",
        user_id="user-1",
        project_budget_id="budget-1",
        allocated_max_budget=50.0,
        provider_metadata={},
    )

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project",
            new=AsyncMock(return_value=[allocation_no_ref]),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(),
        ) as mock_budget_get,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
        ) as mock_get_provider,
    ):
        await resync_project_member_allocations(project_name=PROJECT_NAME, enforce_limit=True)

    mock_budget_get.assert_not_called()
    mock_get_provider.assert_called_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_resync_updates_synced_allocations_with_enforcement_on():
    """Synced allocations get max_budget = allocation.allocated_max_budget when enforce=True."""
    session = AsyncMock()
    allocation = SimpleNamespace(
        id="alloc-1",
        user_id="user-1",
        project_budget_id=BUDGET_ID,
        allocated_max_budget=50.0,
        provider_metadata={"raw": {"provider_member_ref": "member-ref-1"}},
    )
    budget = SimpleNamespace(budget_id=BUDGET_ID, max_budget=200.0, budget_reset_at="2026-04-22T10:00:00Z")
    provider_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="member-ref-1",
        provider_budget_id="member-budget-1",
        budget_reset_at="2026-04-22T10:00:00Z",
        sync_status=SyncStatus.OK,
        metadata={},
    )
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=provider_state))

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project",
            new=AsyncMock(return_value=[allocation]),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ) as mock_update,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
            return_value=provider,
        ),
    ):
        await resync_project_member_allocations(project_name=PROJECT_NAME, enforce_limit=True)

    provider.sync_member_allocation.assert_awaited_once_with(
        allocation=allocation, budget=budget, effective_max_budget=50.0
    )
    mock_update.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_resync_updates_synced_allocations_with_enforcement_off():
    """Synced allocations get max_budget = budget.max_budget when enforce=False."""
    session = AsyncMock()
    allocation = SimpleNamespace(
        id="alloc-1",
        user_id="user-1",
        project_budget_id=BUDGET_ID,
        allocated_max_budget=50.0,
        provider_metadata={"raw": {"provider_member_ref": "member-ref-1"}},
    )
    budget = SimpleNamespace(budget_id=BUDGET_ID, max_budget=200.0, budget_reset_at="2026-04-22T10:00:00Z")
    provider_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="member-ref-1",
        provider_budget_id="member-budget-1",
        budget_reset_at="2026-04-22T10:00:00Z",
        sync_status=SyncStatus.OK,
        metadata={},
    )
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=provider_state))

    with (
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.get_active_by_project",
            new=AsyncMock(return_value=[allocation]),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.budget_repository.get_by_id",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync"
            ".project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ) as mock_update,
        patch(
            "codemie.enterprise.litellm.project_member_runtime_sync.get_active_provider",
            return_value=provider,
        ),
    ):
        await resync_project_member_allocations(project_name=PROJECT_NAME, enforce_limit=False)

    provider.sync_member_allocation.assert_awaited_once_with(
        allocation=allocation, budget=budget, effective_max_budget=200.0
    )
    mock_update.assert_awaited_once()
    session.commit.assert_awaited_once()
