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
from fastapi.routing import APIRoute

from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.litellm.budget_categories import BudgetCategory
from codemie.rest_api.models.budget import BudgetCreateRequest, BudgetUpdateRequest
from codemie.rest_api.routers.budget_router import (
    router,
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


def _maintainer_user() -> User:
    return User(
        id="maintainer-1",
        username="maintainer",
        email="maintainer@example.com",
        is_admin=True,
        is_maintainer=True,
    )


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


def test_budget_read_routes_use_admin_or_maintainer_dependency():
    route_map = {
        (route.path, tuple(sorted(route.methods or []))): route
        for route in router.routes
        if isinstance(route, APIRoute)
    }

    list_route = route_map[("/v1/admin/budgets", ("GET",))]
    detail_route = route_map[("/v1/admin/budgets/{budgetId}", ("GET",))]

    for route in (list_route, detail_route):
        dependency_calls = {dependency.call.__name__ for dependency in route.dependant.dependencies}
        # EPMCDME-10930: widened to admin_or_maintainer_or_auditor_access for auditor read access
        assert "admin_or_maintainer_or_auditor_access" in dependency_calls
        assert "maintainer_access_only" not in dependency_calls


def test_budget_write_routes_keep_maintainer_dependency():
    write_routes = [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path in {"/v1/admin/budgets", "/v1/admin/budgets/sync", "/v1/admin/budgets/assignments/backfill"}
        and "GET" not in (route.methods or set())
    ]

    for route in write_routes:
        dependency_calls = {dependency.call.__name__ for dependency in route.dependant.dependencies}
        assert "maintainer_access_only" in dependency_calls


# ---------------------------------------------------------------------------
# TestBudgetNotificationOwnerEmail (EPMCDME-13959)
# ---------------------------------------------------------------------------


class TestBudgetNotificationOwnerEmail:
    @pytest.mark.asyncio
    async def test_create_accepts_valid_notification_owner_email(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        budget_row = _make_budget_row(notification_owner_email="owner@example.com")
        create_mock = AsyncMock(return_value=budget_row)

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.create_budget",
                new=create_mock,
            ),
        ):
            payload = BudgetCreateRequest(
                budget_id="notify-1",
                name="Notify 1",
                soft_budget=10.0,
                max_budget=100.0,
                budget_duration="30d",
                budget_category=BudgetCategory.PLATFORM,
                notification_owner_email="owner@example.com",
            )
            result = await create_budget(payload=payload, user=_admin_user(), _=None)

        assert result.notification_owner_email == "owner@example.com"
        # Router forwards the parsed payload to the service.
        called_payload = create_mock.await_args.kwargs.get("data") or create_mock.await_args.args[1]
        assert called_payload.notification_owner_email == "owner@example.com"

    def test_create_rejects_invalid_email_format(self):
        with pytest.raises(ValueError):
            BudgetCreateRequest(
                budget_id="notify-2",
                name="Notify 2",
                soft_budget=10.0,
                max_budget=100.0,
                budget_duration="30d",
                budget_category=BudgetCategory.PLATFORM,
                notification_owner_email="not-an-email",
            )

    def test_create_defaults_notification_owner_email_to_none(self):
        payload = BudgetCreateRequest(
            budget_id="notify-3",
            name="Notify 3",
            soft_budget=10.0,
            max_budget=100.0,
            budget_duration="30d",
            budget_category=BudgetCategory.PLATFORM,
        )
        assert payload.notification_owner_email is None

    @pytest.mark.asyncio
    async def test_update_can_set_and_clear_notification_owner_email(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        set_row = _make_budget_row(notification_owner_email="team@example.com")
        cleared_row = _make_budget_row(notification_owner_email=None)

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.update_budget",
                new=AsyncMock(return_value=set_row),
            ),
        ):
            result_set = await update_budget(
                budgetId="test-budget",
                payload=BudgetUpdateRequest(notification_owner_email="team@example.com"),
                user=_admin_user(),
                _=None,
            )
        assert result_set.notification_owner_email == "team@example.com"

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.update_budget",
                new=AsyncMock(return_value=cleared_row),
            ),
        ):
            result_cleared = await update_budget(
                budgetId="test-budget",
                payload=BudgetUpdateRequest(notification_owner_email=None),
                user=_admin_user(),
                _=None,
            )
        assert result_cleared.notification_owner_email is None
