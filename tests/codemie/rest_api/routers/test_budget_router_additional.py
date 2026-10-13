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

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from codemie.enterprise.litellm.budget_categories import BudgetCategory
from codemie.rest_api.models.budget import BudgetAssignmentBackfillResult
from codemie.rest_api.routers.budget_router import (
    _build_budget_response,
    backfill_user_budget_assignments,
    sync_budgets,
)
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_models import Budget


def _admin_user() -> User:
    return User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)


def _make_budget_row(**kwargs) -> Budget:
    defaults = {
        "budget_id": "test-budget",
        "name": "Test Budget",
        "soft_budget": 10.0,
        "max_budget": 100.0,
        "budget_duration": "30d",
        "budget_category": BudgetCategory.PLATFORM.value,
        "created_by": "admin-1",
        "created_at": datetime(2026, 4, 1, tzinfo=UTC),
        "updated_at": None,
        "budget_reset_at": None,
        "description": None,
    }
    defaults.update(kwargs)
    return Budget(**defaults)


@asynccontextmanager
async def _mock_session_ctx(session):
    yield session


def _patch_session(session):
    return patch(
        "codemie.rest_api.routers.budget_router.get_async_session",
        return_value=_mock_session_ctx(session),
    )


def _patch_litellm_enabled():
    return patch("codemie.rest_api.routers.budget_router.require_litellm_enabled")


def test_build_budget_response_marks_preconfigured_budget():
    budget = _make_budget_row(budget_id="platform-default")

    with patch(
        "codemie.rest_api.routers.budget_router.budget_config.predefined_budgets",
        [SimpleNamespace(budget_id="platform-default")],
    ):
        result = _build_budget_response(budget)

    assert result.is_preconfigured is True


@pytest.mark.asyncio
async def test_sync_budgets_maps_service_summary_to_response():
    session = AsyncMock()
    budget = _make_budget_row(budget_id="platform-budget")
    sync_result = SimpleNamespace(
        created=1,
        updated=2,
        unchanged=3,
        deleted=4,
        total_in_litellm=10,
        budgets=[budget],
    )

    with (
        _patch_litellm_enabled(),
        _patch_session(session),
        patch(
            "codemie.rest_api.routers.budget_router.budget_service.sync_budgets_from_litellm",
            new=AsyncMock(return_value=sync_result),
        ),
    ):
        result = await sync_budgets(user=_admin_user(), _=None)

    assert result.created == 1
    assert result.updated == 2
    assert result.total_in_litellm == 10
    assert [item.budget_id for item in result.budgets] == ["platform-budget"]


@pytest.mark.asyncio
async def test_backfill_user_budget_assignments_returns_service_result():
    session = AsyncMock()
    backfill_result = BudgetAssignmentBackfillResult(
        imported=5,
        skipped_existing=2,
        skipped_missing_user=1,
        created_budgets=3,
        failed=0,
        total_in_litellm=8,
    )

    with (
        _patch_litellm_enabled(),
        _patch_session(session),
        patch(
            "codemie.rest_api.routers.budget_router.budget_service.backfill_user_budget_assignments",
            new=AsyncMock(return_value=backfill_result),
        ) as mock_backfill,
    ):
        result = await backfill_user_budget_assignments(user=_admin_user(), _=None)

    mock_backfill.assert_awaited_once_with(session, actor_id="admin-1")
    assert result.imported == 5
    assert result.created_budgets == 3
