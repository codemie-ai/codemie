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
  - 204 on successful delete (maintainer only)
  - 201 on successful create
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.configs.customer_config import CustomerConfig
from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.litellm.budget_categories import BudgetCategory
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.models.budget import BudgetCreateRequest, BudgetUpdateRequest
from codemie.rest_api.routers.budget_router import (
    router,
    create_budget,
    delete_budget,
    get_budget,
    list_budgets,
    sync_budgets,
    update_budget,
)
from codemie.rest_api.security.authentication import authenticate, maintainer_access_only
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
# TestDeleteBudgetEndpoint (EPMCDME-15633)
# ---------------------------------------------------------------------------


class TestDeleteBudgetEndpoint:
    @pytest.mark.asyncio
    async def test_maintainer_deletes_budget_and_commits(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_delete = AsyncMock(return_value=None)

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch("codemie.rest_api.routers.budget_router.budget_service.delete_budget", new=mock_delete),
        ):
            result = await delete_budget(budget_id="test-budget", user=_maintainer_user(), _=None)

        assert result is None
        mock_delete.assert_awaited_once_with(
            mock_session, "test-budget", actor_id="maintainer-1", actor_name="maintainer"
        )
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [403, 404, 409])
    async def test_service_errors_propagate_without_commit(self, code):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.delete_budget",
                new=AsyncMock(side_effect=ExtendedHTTPException(code=code, message="rejected")),
            ),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await delete_budget(budget_id="test-budget", user=_maintainer_user(), _=None)

        assert exc_info.value.code == code
        mock_session.commit.assert_not_called()

    def test_delete_route_is_maintainer_only_and_returns_204(self):
        route = next(
            route
            for route in router.routes
            if isinstance(route, APIRoute)
            and route.path == "/v1/admin/budgets/{budgetId}"
            and "DELETE" in (route.methods or set())
        )

        dependency_calls = {dependency.call.__name__ for dependency in route.dependant.dependencies}
        assert "maintainer_access_only" in dependency_calls
        assert route.status_code == 204

    @pytest.mark.asyncio
    async def test_maintainer_access_only_allows_maintainer(self):
        request = SimpleNamespace(state=SimpleNamespace(user=_maintainer_user()))

        assert await maintainer_access_only(request) is None

    @pytest.mark.asyncio
    async def test_maintainer_access_only_rejects_plain_admin(self):
        with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
            plain_admin = User(id="admin-1", username="admin", is_admin=True, is_maintainer=False)
        request = SimpleNamespace(state=SimpleNamespace(user=plain_admin))

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await maintainer_access_only(request)

        assert exc_info.value.code == 403


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
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.get_user_counts",
                new=AsyncMock(return_value={"test-budget": 4, "budget-2": 0}),
            ),
        ):
            result = await list_budgets(page=0, per_page=20, category=None, user=_admin_user(), _=None)

        assert result.pagination.total == 2
        assert len(result.data) == 2
        assert [item.user_count for item in result.data] == [4, 0]

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
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.get_user_counts",
                new=AsyncMock(return_value={}),
            ),
        ):
            await list_budgets(
                page=2,
                per_page=5,
                category=BudgetCategory.CLI,
                user=_admin_user(),
                _=None,
            )

        mock_list.assert_called_once()
        _, kwargs = mock_list.call_args
        assert kwargs["category"] == "cli"
        assert kwargs["page"] == 2
        assert kwargs["per_page"] == 5

    @pytest.mark.asyncio
    async def test_get_user_counts_called_with_page_budget_ids(self):
        budgets = [_make_budget_row(), _make_budget_row(budget_id="budget-2", name="Budget 2")]

        with (
            _patch_litellm_enabled(),
            _patch_session(AsyncMock()),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.list_budgets",
                new=AsyncMock(return_value=(budgets, 2)),
            ),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.get_user_counts",
                new=AsyncMock(return_value={"test-budget": 1, "budget-2": 1}),
            ) as mock_counts,
        ):
            await list_budgets(page=0, per_page=20, category=None, user=_admin_user(), _=None)

        assert mock_counts.call_args.args[1] == ["test-budget", "budget-2"]

    @pytest.mark.asyncio
    async def test_create_response_user_count_is_none(self):
        mock_session = AsyncMock()
        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.create_budget",
                new=AsyncMock(return_value=_make_budget_row()),
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

        assert result.user_count is None


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


# ---------------------------------------------------------------------------
# TestCreatedByName (EPMCDME-15628)
# ---------------------------------------------------------------------------


def _patch_resolve_creator_names(mapping: dict):
    return patch(
        "codemie.rest_api.routers.budget_router.budget_service.resolve_creator_names",
        new=AsyncMock(return_value=mapping),
    )


def _patch_user_counts(mapping: dict):
    return patch(
        "codemie.rest_api.routers.budget_router.budget_service.get_user_counts",
        new=AsyncMock(return_value=mapping),
    )


class TestCreatedByName:
    @pytest.mark.asyncio
    async def test_list_budgets_returns_created_by_name_for_human_creator(self):
        mock_session = AsyncMock()
        budgets = [_make_budget_row(created_by="user-1")]

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.list_budgets",
                new=AsyncMock(return_value=(budgets, 1)),
            ),
            _patch_resolve_creator_names({"user-1": "Alice Smith"}),
            _patch_user_counts({}),
        ):
            result = await list_budgets(page=0, per_page=20, category=None, user=_admin_user(), _=None)

        assert result.data[0].created_by_name == "Alice Smith"
        assert result.data[0].created_by == "user-1"

    @pytest.mark.asyncio
    async def test_list_budgets_system_creator_returns_null(self):
        mock_session = AsyncMock()
        budgets = [_make_budget_row(created_by="system")]

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.list_budgets",
                new=AsyncMock(return_value=(budgets, 1)),
            ),
            _patch_resolve_creator_names({}),
            _patch_user_counts({}),
        ):
            result = await list_budgets(page=0, per_page=20, category=None, user=_admin_user(), _=None)

        assert result.data[0].created_by_name is None
        assert result.data[0].created_by == "system"

    @pytest.mark.asyncio
    async def test_list_budgets_unknown_creator_returns_null(self):
        mock_session = AsyncMock()
        budgets = [_make_budget_row(created_by="user-ghost")]

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.list_budgets",
                new=AsyncMock(return_value=(budgets, 1)),
            ),
            _patch_resolve_creator_names({}),
            _patch_user_counts({}),
        ):
            result = await list_budgets(page=0, per_page=20, category=None, user=_admin_user(), _=None)

        assert result.data[0].created_by_name is None

    @pytest.mark.asyncio
    async def test_list_budgets_resolves_creator_names_once_per_request(self):
        mock_session = AsyncMock()
        budgets = [
            _make_budget_row(budget_id="b1", created_by="user-1"),
            _make_budget_row(budget_id="b2", created_by="user-1"),
        ]
        resolve_mock = AsyncMock(return_value={"user-1": "Alice Smith"})

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.list_budgets",
                new=AsyncMock(return_value=(budgets, 2)),
            ),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.resolve_creator_names",
                new=resolve_mock,
            ),
            _patch_user_counts({}),
        ):
            result = await list_budgets(page=0, per_page=20, category=None, user=_admin_user(), _=None)

        resolve_mock.assert_called_once()
        assert all(b.created_by_name == "Alice Smith" for b in result.data)

    @pytest.mark.asyncio
    async def test_get_budget_includes_created_by_name(self):
        mock_session = AsyncMock()
        budget_row = _make_budget_row(created_by="user-1")

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.get_budget",
                new=AsyncMock(return_value=budget_row),
            ),
            _patch_resolve_creator_names({"user-1": "Alice Smith"}),
        ):
            result = await get_budget(budgetId="test-budget", user=_admin_user(), _=None)

        assert result.created_by_name == "Alice Smith"
        assert result.created_by == "user-1"

    @pytest.mark.asyncio
    async def test_create_budget_includes_created_by_name(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        budget_row = _make_budget_row(created_by="admin-1")

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.create_budget",
                new=AsyncMock(return_value=budget_row),
            ),
            _patch_resolve_creator_names({"admin-1": "Admin User"}),
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

        assert result.created_by_name == "Admin User"
        assert result.created_by == "admin-1"

    @pytest.mark.asyncio
    async def test_update_budget_includes_created_by_name(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        updated_row = _make_budget_row(created_by="user-1", name="Updated Name")

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.update_budget",
                new=AsyncMock(return_value=updated_row),
            ),
            _patch_resolve_creator_names({"user-1": "Alice Smith"}),
        ):
            result = await update_budget(
                budgetId="test-budget",
                payload=BudgetUpdateRequest(name="Updated Name"),
                user=_admin_user(),
                _=None,
            )

        assert result.created_by_name == "Alice Smith"
        assert result.created_by == "user-1"

    @pytest.mark.asyncio
    async def test_sync_budgets_includes_created_by_name(self):
        from codemie.rest_api.models.budget import BudgetSyncResult

        mock_session = AsyncMock()
        budget_row = _make_budget_row(created_by="user-1")
        sync_result = BudgetSyncResult(
            created=1,
            updated=0,
            unchanged=0,
            deleted=0,
            total_in_litellm=1,
            budgets=[budget_row],
        )

        with (
            _patch_litellm_enabled(),
            _patch_session(mock_session),
            patch(
                "codemie.rest_api.routers.budget_router.budget_service.sync_budgets_from_litellm",
                new=AsyncMock(return_value=sync_result),
            ),
            _patch_resolve_creator_names({"user-1": "Alice Smith"}),
        ):
            result = await sync_budgets(user=_admin_user(), _=None)

        assert result.budgets[0].created_by_name == "Alice Smith"
        assert result.budgets[0].created_by == "user-1"


# ---------------------------------------------------------------------------
# budgetManagement router-level guard (EPMCDME-14536, AR-12)
# ---------------------------------------------------------------------------


@pytest.fixture
def budget_router_app():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    return TestClient(app)


def _override_identity(app: FastAPI, user: User) -> None:
    """Inject *user* the way `authenticate` does: `maintainer_access_only` and
    `admin_or_maintainer_or_auditor_access` both read `request.state.user` directly."""

    async def _fake_authenticate(request: Request) -> User:
        request.state.user = user
        return user

    app.dependency_overrides[authenticate] = _fake_authenticate


def test_create_budget_rejects_when_budget_management_disabled(budget_router_app):
    """A user who would otherwise pass `maintainer_access_only`, so a 403 here can only
    come from the budgetManagement guard, never from the endpoint's own authorization."""
    client = budget_router_app
    _override_identity(client.app, _maintainer_user())

    with patch.object(CustomerConfig, "is_feature_enabled", return_value=False):
        response = client.post(
            "/v1/admin/budgets",
            json={
                "budget_id": "new-budget",
                "name": "New Budget",
                "soft_budget": 10.0,
                "max_budget": 100.0,
                "budget_duration": "30d",
                "budget_category": "platform",
            },
        )

    assert response.status_code == 403


def test_endpoint_requires_auth_before_budget_management_check(budget_router_app):
    """No `authenticate` override — the real auth stack must answer 401, never 403."""
    client = budget_router_app

    with patch.object(CustomerConfig, "is_feature_enabled", return_value=False):
        response = client.get("/v1/admin/budgets")

    assert response.status_code == 401
