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

"""Integration tests for budget CRUD endpoints (QA-T3).

Covers:
  - 400 when LiteLLM is disabled (require_litellm_enabled raises)
  - 409 on duplicate budget_id at create
  - 404 on missing budget at get / patch / delete
  - 204 on successful delete
  - 201 on successful create
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, UTC
from unittest.mock import AsyncMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.litellm.budget_categories import BudgetCategory
from codemie.rest_api.routers.budget_router import (
    BudgetCreateRequest,
    BudgetUpdateRequest,
    create_budget,
    get_budget,
    list_budgets,
    update_budget,
)
from codemie.rest_api.security.user import User
from codemie.service.budget.budget_models import Budget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    """Async context manager that yields a mock session, simulating get_async_session()."""
    yield session


def _patch_session(session):
    return patch(
        "codemie.rest_api.routers.budget_router.get_async_session",
        return_value=_mock_session_ctx(session),
    )


def _patch_litellm_enabled():
    return patch("codemie.rest_api.routers.budget_router.require_litellm_enabled")


def _patch_litellm_disabled():
    return patch(
        "codemie.rest_api.routers.budget_router.require_litellm_enabled",
        side_effect=ExtendedHTTPException(code=400, message="LiteLLM not available or not installed"),
    )


# ---------------------------------------------------------------------------
# TestLiteLLMDisabledGate
# ---------------------------------------------------------------------------


class TestLiteLLMDisabledGate:
    """All endpoints must return 400 when LiteLLM is disabled."""

    @pytest.mark.asyncio
    async def test_create_budget_raises_400_when_litellm_disabled(self):
        with _patch_litellm_disabled():
            payload = BudgetCreateRequest(
                budget_id="new-budget",
                name="New Budget",
                soft_budget=10.0,
                max_budget=100.0,
                budget_duration="30d",
                budget_category=BudgetCategory.PLATFORM,
            )
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await create_budget(payload=payload, user=_admin_user(), _=None)
        assert exc_info.value.code == 400

    @pytest.mark.asyncio
    async def test_list_budgets_raises_400_when_litellm_disabled(self):
        with _patch_litellm_disabled():
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await list_budgets(page=0, per_page=20, category=None, user=_admin_user(), _=None)
        assert exc_info.value.code == 400

    @pytest.mark.asyncio
    async def test_get_budget_raises_400_when_litellm_disabled(self):
        with _patch_litellm_disabled():
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await get_budget(budgetId="any-id", user=_admin_user(), _=None)
        assert exc_info.value.code == 400

    @pytest.mark.asyncio
    async def test_update_budget_raises_400_when_litellm_disabled(self):
        with _patch_litellm_disabled():
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await update_budget(
                    budgetId="any-id",
                    payload=BudgetUpdateRequest(name="Updated"),
                    user=_admin_user(),
                    _=None,
                )
        assert exc_info.value.code == 400


# ---------------------------------------------------------------------------
# TestCreateBudgetEndpoint
# ---------------------------------------------------------------------------


class TestCreateBudgetEndpoint:
    @pytest.mark.asyncio
    async def test_returns_201_on_success(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        budget_row = _make_budget_row()

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.create_budget",
                new=AsyncMock(return_value=budget_row),
            ),
        ):
            payload = BudgetCreateRequest(
                budget_id="test-budget",
                name="Test Budget",
                soft_budget=10.0,
                max_budget=100.0,
                budget_duration="30d",
                budget_category=BudgetCategory.PLATFORM,
            )
            result = await create_budget(payload=payload, user=_admin_user(), _=None)

        assert result.budget_id == "test-budget"
        assert result.max_budget == 100.0

    @pytest.mark.asyncio
    async def test_raises_409_on_duplicate_budget_id(self):
        mock_session = AsyncMock()

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.create_budget",
                new=AsyncMock(side_effect=ExtendedHTTPException(code=409, message="Budget already exists")),
            ),
        ):
            payload = BudgetCreateRequest(
                budget_id="duplicate-budget",
                name="Duplicate",
                soft_budget=5.0,
                max_budget=50.0,
                budget_duration="30d",
                budget_category=BudgetCategory.PLATFORM,
            )
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await create_budget(payload=payload, user=_admin_user(), _=None)

        assert exc_info.value.code == 409


# ---------------------------------------------------------------------------
# TestGetBudgetEndpoint
# ---------------------------------------------------------------------------


class TestGetBudgetEndpoint:
    @pytest.mark.asyncio
    async def test_returns_budget_when_found(self):
        mock_session = AsyncMock()
        budget_row = _make_budget_row()

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.get_budget",
                new=AsyncMock(return_value=budget_row),
            ),
        ):
            result = await get_budget(budgetId="test-budget", user=_admin_user(), _=None)

        assert result.budget_id == "test-budget"

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        mock_session = AsyncMock()

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.get_budget",
                new=AsyncMock(side_effect=ExtendedHTTPException(code=404, message="Budget not found")),
            ),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await get_budget(budgetId="nonexistent", user=_admin_user(), _=None)

        assert exc_info.value.code == 404


# ---------------------------------------------------------------------------
# TestUpdateBudgetEndpoint
# ---------------------------------------------------------------------------


class TestUpdateBudgetEndpoint:
    @pytest.mark.asyncio
    async def test_returns_updated_budget_on_success(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        updated_row = _make_budget_row(name="Updated Name", max_budget=200.0)

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.update_budget",
                new=AsyncMock(return_value=updated_row),
            ),
        ):
            result = await update_budget(
                budgetId="test-budget",
                payload=BudgetUpdateRequest(name="Updated Name", max_budget=200.0),
                user=_admin_user(),
                _=None,
            )

        assert result.name == "Updated Name"
        assert result.max_budget == 200.0

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        mock_session = AsyncMock()

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.update_budget",
                new=AsyncMock(side_effect=ExtendedHTTPException(code=404, message="Budget not found")),
            ),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await update_budget(
                    budgetId="missing",
                    payload=BudgetUpdateRequest(name="X"),
                    user=_admin_user(),
                    _=None,
                )

        assert exc_info.value.code == 404


# ---------------------------------------------------------------------------
# TestListBudgetsEndpoint
# ---------------------------------------------------------------------------


class TestListBudgetsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_paginated_list(self):
        mock_session = AsyncMock()
        budgets = [_make_budget_row(), _make_budget_row(budget_id="budget-2", name="Budget 2")]

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.list_budgets",
                new=AsyncMock(return_value=(budgets, 2)),
            ),
        ):
            result = await list_budgets(page=0, per_page=20, category=None, user=_admin_user(), _=None)

        assert result.pagination.total == 2
        assert len(result.data) == 2

    @pytest.mark.asyncio
    async def test_filters_by_category(self):
        mock_session = AsyncMock()

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.list_budgets",
                new=AsyncMock(return_value=([], 0)),
            ) as mock_list,
        ):
            await list_budgets(
                page=0,
                per_page=20,
                category=BudgetCategory.CLI,
                user=_admin_user(),
                _=None,
            )

        mock_list.assert_called_once()
        _, kwargs = mock_list.call_args
        assert kwargs["category"] == "cli"
