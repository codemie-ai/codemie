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

"""Unassigning a custom budget must leave the category default recorded in user_budget_assignments.

The row has to be written by the unassign request itself: the proxy only writes it on a later request,
and a proxy replica whose per-process assignment cache still holds the default skips that write.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cachetools import TTLCache

from codemie.rest_api.models.user_management import UserDB
from codemie.service.budget import budget_service as budget_service_module
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.budget_service import BudgetService

USER_ID = "user-123"
DEFAULT_PREMIUM = "codemie_premium_models"
CUSTOM_PREMIUM = "custom-premium"
_MODULE = "codemie.service.budget.budget_service"


@pytest.fixture(autouse=True)
def _restore_assignment_cache():
    original = budget_service_module._budget_assignment_cache
    yield
    budget_service_module._budget_assignment_cache = original


def _db_user(username: str = "testuser") -> MagicMock:
    db_user = MagicMock(spec=UserDB)
    db_user.id = USER_ID
    db_user.username = username
    return db_user


def _session_returning(db_user: MagicMock) -> AsyncMock:
    session = AsyncMock()
    scalars = MagicMock()
    scalars.first.return_value = db_user
    result = MagicMock()
    result.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result)
    return session


class _InMemoryAssignments:
    """Stands in for budget_repository over the one shared table every replica reads."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    async def get_user_category_budget_id(self, session, user_id, category):
        return self.rows.get((user_id, category.value))

    async def upsert_user_category_assignment(self, session, user_id, category, budget_id, assigned_by):
        self.rows[(user_id, category.value)] = budget_id

    async def delete_user_category_assignment(self, session, user_id, category):
        self.rows.pop((user_id, category.value), None)

    async def get_user_category_assignments(self, session, user_id):
        return [SimpleNamespace(category=c, budget_id=b) for (u, c), b in self.rows.items() if u == user_id]


@contextlib.asynccontextmanager
async def _fake_async_session():
    yield AsyncMock()


class TestUnassignRecordsDefault:
    @pytest.mark.asyncio
    @patch(f"{_MODULE}.activity_event_repository")
    @patch(f"{_MODULE}.get_active_provider")
    @patch(f"{_MODULE}.budget_repository")
    async def test_unassign_upserts_the_category_default_after_the_provider_re_points(
        self, mock_repo, mock_get_provider, mock_activity
    ):
        mock_activity.async_insert = AsyncMock()
        mock_repo.delete_user_category_assignment = AsyncMock()
        mock_repo.upsert_user_category_assignment = AsyncMock()
        order: list[str] = []
        provider = AsyncMock()
        provider.assign_user_budget = AsyncMock(side_effect=lambda **_: order.append("provider"))
        mock_repo.upsert_user_category_assignment.side_effect = lambda *_a, **_k: order.append("upsert")
        mock_get_provider.return_value = provider
        session = _session_returning(_db_user())

        with patch.object(BudgetService, "_default_budget_id_for_category", return_value=DEFAULT_PREMIUM):
            await BudgetService().assign_budget_to_user(
                session, USER_ID, {BudgetCategory.PREMIUM_MODELS: None}, actor_id="admin-1"
            )

        mock_repo.upsert_user_category_assignment.assert_awaited_once_with(
            session, USER_ID, BudgetCategory.PREMIUM_MODELS, DEFAULT_PREMIUM, assigned_by="system"
        )
        assert order == ["provider", "upsert"]

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.activity_event_repository")
    @patch(f"{_MODULE}.get_active_provider")
    @patch(f"{_MODULE}.budget_repository")
    async def test_unassign_records_no_default_when_the_provider_re_point_fails(
        self, mock_repo, mock_get_provider, mock_activity
    ):
        mock_activity.async_insert = AsyncMock()
        mock_repo.delete_user_category_assignment = AsyncMock()
        mock_repo.upsert_user_category_assignment = AsyncMock()
        provider = AsyncMock()
        provider.assign_user_budget = AsyncMock(side_effect=RuntimeError("litellm down"))
        mock_get_provider.return_value = provider

        with patch.object(BudgetService, "_default_budget_id_for_category", return_value=DEFAULT_PREMIUM):
            await BudgetService().assign_budget_to_user(
                _session_returning(_db_user()), USER_ID, {BudgetCategory.PREMIUM_MODELS: None}, actor_id="admin-1"
            )

        mock_repo.delete_user_category_assignment.assert_awaited_once()
        mock_repo.upsert_user_category_assignment.assert_not_awaited()

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.activity_event_repository")
    @patch(f"{_MODULE}.get_active_provider")
    @patch(f"{_MODULE}.budget_repository")
    async def test_unassign_records_nothing_when_the_category_has_no_default(
        self, mock_repo, mock_get_provider, mock_activity
    ):
        mock_activity.async_insert = AsyncMock()
        mock_repo.delete_user_category_assignment = AsyncMock()
        mock_repo.upsert_user_category_assignment = AsyncMock()
        provider = AsyncMock()
        mock_get_provider.return_value = provider

        with patch.object(BudgetService, "_default_budget_id_for_category", return_value=None):
            await BudgetService().assign_budget_to_user(
                _session_returning(_db_user()), USER_ID, {BudgetCategory.CLI: None}, actor_id="admin-1"
            )

        provider.clear_user_budget.assert_awaited_once()
        mock_repo.upsert_user_category_assignment.assert_not_awaited()


class TestBulkUnassignRecordsDefault:
    @pytest.mark.asyncio
    @patch(f"{_MODULE}.get_active_provider")
    @patch(f"{_MODULE}.budget_repository")
    async def test_bulk_unassign_records_the_default_only_for_users_the_provider_re_pointed(
        self, mock_repo, mock_get_provider
    ):
        mock_repo.upsert_user_category_assignment = AsyncMock()
        provider = AsyncMock()

        async def assign(*, username, budget_category, budget_id):
            if username == "broken":
                raise RuntimeError("litellm down")

        provider.assign_user_budget = AsyncMock(side_effect=assign)
        mock_get_provider.return_value = provider
        session = AsyncMock()
        service = BudgetService()
        users = {"u-ok": _db_user("healthy"), "u-bad": _db_user("broken")}

        with (
            patch.object(service, "_load_bulk_budget_users", new=AsyncMock(return_value=users)),
            patch.object(service, "_persist_bulk_budget_assignments", new=AsyncMock()),
            patch.object(BudgetService, "_default_budget_id_for_category", return_value=DEFAULT_PREMIUM),
        ):
            await service.bulk_set_user_budgets(
                session, ["u-ok", "u-bad"], {BudgetCategory.PREMIUM_MODELS: None}, actor_id="admin-1"
            )

        mock_repo.upsert_user_category_assignment.assert_awaited_once_with(
            session, "u-ok", BudgetCategory.PREMIUM_MODELS, DEFAULT_PREMIUM, assigned_by="system"
        )


class TestUnassignAcrossReplicas:
    @pytest.mark.asyncio
    @patch(f"{_MODULE}.activity_event_repository")
    @patch(f"{_MODULE}.get_active_provider")
    async def test_default_row_exists_after_unassign_although_the_proxy_replica_cache_is_stale(
        self, mock_get_provider, mock_activity
    ):
        """The proxy replica cached the default on its first request and never sees the admin replica's pop."""
        mock_activity.async_insert = AsyncMock()
        mock_get_provider.return_value = AsyncMock()
        table = _InMemoryAssignments()
        proxy_cache = TTLCache(maxsize=100, ttl=60)
        admin_cache = TTLCache(maxsize=100, ttl=60)
        service = BudgetService()
        category = BudgetCategory.PREMIUM_MODELS

        with (
            patch(f"{_MODULE}.budget_repository", table),
            patch("codemie.clients.postgres.get_async_session", _fake_async_session),
            patch.object(BudgetService, "_default_budget_id_for_category", return_value=DEFAULT_PREMIUM),
        ):
            budget_service_module._budget_assignment_cache = proxy_cache
            await service.track_proxy_budget_assignment_for_request(USER_ID, category, DEFAULT_PREMIUM)
            table.rows[(USER_ID, category.value)] = CUSTOM_PREMIUM  # admin assigns the custom budget

            budget_service_module._budget_assignment_cache = admin_cache
            await service.assign_budget_to_user(
                _session_returning(_db_user()), USER_ID, {category: None}, actor_id="admin-1"
            )

            budget_service_module._budget_assignment_cache = proxy_cache
            await service.track_proxy_budget_assignment_for_request(USER_ID, category, DEFAULT_PREMIUM)

        assert table.rows == {(USER_ID, category.value): DEFAULT_PREMIUM}
