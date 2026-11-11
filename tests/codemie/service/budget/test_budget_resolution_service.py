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
