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

"""Tests for BudgetResolutionService caching behaviour."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from codemie.service.budget.budget_enums import BudgetCategory, BudgetScope
from codemie.service.budget.budget_resolution_service import (
    BudgetResolutionService,
    _resolution_cache,
    clear_budget_resolution_cache,
)


def setup_function():
    clear_budget_resolution_cache()


@pytest.mark.asyncio
async def test_resolve_returns_global_context_when_no_project():
    """resolve() returns GLOBAL scope immediately when project_name is None."""
    svc = BudgetResolutionService()
    session = AsyncMock()
    result = await svc.resolve(session, user_id="u1", project_name=None, budget_category=BudgetCategory.PLATFORM)
    assert result.scope == BudgetScope.GLOBAL
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_caches_result_on_first_call():
    """resolve() populates _resolution_cache on a DB hit."""
    from codemie.repository.project_budget_repository import ProjectBudgetContext

    svc = BudgetResolutionService()
    session = AsyncMock()

    ctx = ProjectBudgetContext(
        budget_id="b1",
        allocation_id="alloc-1",
        budget_provider_metadata={"provider": "litellm"},
        member_provider_metadata={"key": "val"},
    )
    with patch(
        "codemie.service.budget.budget_resolution_service"
        ".project_budget_assignment_repository.get_project_budget_context",
        new=AsyncMock(return_value=ctx),
    ):
        result = await svc.resolve(session, user_id="u1", project_name="proj", budget_category=BudgetCategory.PLATFORM)

    assert result.scope == BudgetScope.PROJECT
    assert result.budget_id == "b1"
    cache_key = ("proj", BudgetCategory.PLATFORM.value, "u1")
    assert cache_key in _resolution_cache


@pytest.mark.asyncio
async def test_resolve_uses_cache_on_second_call():
    """resolve() skips DB entirely on a warm cache hit."""
    from codemie.service.budget.budget_resolution_service import ResolvedBudgetContext

    svc = BudgetResolutionService()
    session = AsyncMock()
    cache_key = ("proj", BudgetCategory.PLATFORM.value, "u1")
    _resolution_cache[cache_key] = ResolvedBudgetContext(
        scope=BudgetScope.PROJECT,
        project_name="proj",
        budget_category=BudgetCategory.PLATFORM,
        budget_id="cached-budget",
        member_allocation_id="cached-alloc",
        provider_metadata={},
        member_provider_metadata={},
    )

    with patch(
        "codemie.service.budget.budget_resolution_service"
        ".project_budget_assignment_repository.get_project_budget_context",
    ) as mock_db:
        result = await svc.resolve(session, user_id="u1", project_name="proj", budget_category=BudgetCategory.PLATFORM)
        mock_db.assert_not_called()

    assert result.budget_id == "cached-budget"


@pytest.mark.asyncio
async def test_resolve_caches_none_on_db_miss():
    """resolve() caches None (GLOBAL fallback) when no project budget exists."""
    svc = BudgetResolutionService()
    session = AsyncMock()
    with patch(
        "codemie.service.budget.budget_resolution_service"
        ".project_budget_assignment_repository.get_project_budget_context",
        new=AsyncMock(return_value=None),
    ):
        result = await svc.resolve(session, user_id="u1", project_name="proj", budget_category=BudgetCategory.PLATFORM)

    assert result.scope == BudgetScope.GLOBAL
    cache_key = ("proj", BudgetCategory.PLATFORM.value, "u1")
    assert cache_key in _resolution_cache
    assert _resolution_cache[cache_key] is None


# --- shared funded probe (EPMCDME-16625) -------------------------------------------------------------------------


def _row(budget_id: str = "b1") -> dict:
    return {
        "budget_id": budget_id,
        "allocation_id": "alloc-1",
        "effective_budget_id": None,
        "shared_budget_id": None,
        "override_budget_id": None,
        "budget_meta": {},
        "member_meta": {},
    }


_LOADER_SYNC = "codemie.service.budget.budget_resolution_service._load_project_budget_rows_sync"
_LOADER_ASYNC = "codemie.service.budget.budget_resolution_service._load_project_budget_rows"


def test_probe_returns_scopes_with_active_budget_and_allocation():
    from codemie.service.budget.budget_resolution_service import probe_project_budget_scopes_sync

    with patch(_LOADER_SYNC, return_value={BudgetCategory.PLATFORM.value: _row()}):
        scopes = probe_project_budget_scopes_sync("team-a", "u1")

    assert scopes == {BudgetCategory.PLATFORM}
    assert _resolution_cache[("team-a", BudgetCategory.PLATFORM.value, "u1")].budget_id == "b1"


def test_probe_empty_when_budget_without_allocation_for_user():
    from codemie.service.budget.budget_resolution_service import probe_project_budget_scopes_sync

    with patch(_LOADER_SYNC, return_value={}):
        assert probe_project_budget_scopes_sync("team-a", "u1") == set()


def test_probe_caches_negative_result():
    from codemie.service.budget.budget_resolution_service import probe_project_budget_scopes_sync

    with patch(_LOADER_SYNC, return_value={}) as loader:
        probe_project_budget_scopes_sync("team-a", "u1")
        probe_project_budget_scopes_sync("team-a", "u1")

    assert loader.call_count == 1
    assert _resolution_cache[("team-a", BudgetCategory.CLI.value, "u1")] is None


def test_probe_falsy_project_is_empty_without_query():
    from codemie.service.budget.budget_resolution_service import probe_project_budget_scopes_sync

    with patch(_LOADER_SYNC) as loader:
        assert probe_project_budget_scopes_sync(None, "u1") == set()
    loader.assert_not_called()


@pytest.mark.asyncio
async def test_probe_sync_and_async_agree_and_share_cache():
    from codemie.service.budget.budget_resolution_service import (
        probe_project_budget_scopes,
        probe_project_budget_scopes_sync,
    )

    with patch(_LOADER_SYNC, return_value={BudgetCategory.CLI.value: _row()}):
        sync_scopes = probe_project_budget_scopes_sync("team-a", "u1")
    with patch(_LOADER_ASYNC, new=AsyncMock()) as async_loader:
        async_scopes = await probe_project_budget_scopes("team-a", "u1")

    assert sync_scopes == async_scopes == {BudgetCategory.CLI}
    async_loader.assert_not_called()


def test_probe_treats_an_entry_expiring_mid_read_as_a_miss():
    """CR-009: a TTL entry can expire between the membership check and the read."""
    from codemie.service.budget import budget_resolution_service as service

    class ExpiringCache(dict):
        def __contains__(self, key):
            return True

        def __getitem__(self, key):
            raise KeyError(key)

        def get(self, key, default=None):
            return default

    with (
        patch.object(service, "_resolution_cache", ExpiringCache()),
        patch(_LOADER_SYNC, return_value={BudgetCategory.PLATFORM.value: _row()}) as loader,
    ):
        assert service.probe_project_budget_scopes_sync("team-a", "u1") == {BudgetCategory.PLATFORM}

    loader.assert_called_once()


def test_resolve_sync_uses_the_shared_funded_query():
    """CR-010: one definition of funded; resolve_sync must not keep its own SQL copy."""
    from codemie.service.budget.budget_resolution_service import BudgetResolutionService

    with patch(_LOADER_SYNC, return_value={BudgetCategory.CLI.value: _row("b-cli")}) as loader:
        resolved = BudgetResolutionService().resolve_sync(
            user_id="u1", project_name="team-a", budget_category=BudgetCategory.CLI
        )

    loader.assert_called_once_with("team-a", "u1", categories=[BudgetCategory.CLI.value])
    assert resolved.budget_id == "b-cli"
