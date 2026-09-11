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

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.project_budget_router import (
    RebalanceProjectBudgetRequest,
    _member_budget_id,
    clear_member_override,
    get_project_budget,
    rebalance_project_budget,
)
from codemie.rest_api.security.user import User


def _admin_user() -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="admin-1", username="admin@example.com", email="admin@example.com", is_admin=True)


def _project_admin_user(projects: list[str]) -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(
            id="proj-admin-1",
            username="proj-admin@example.com",
            email="proj-admin@example.com",
            is_admin=False,
            admin_project_names=projects,
            project_names=projects,
        )


@asynccontextmanager
async def _mock_session_ctx(session):
    yield session


def test_member_budget_id_falls_back_to_top_level_provider_budget_id():
    assert _member_budget_id({"provider_budget_id": "member-budget-2"}) == "member-budget-2"


@pytest.mark.asyncio
async def test_project_admin_cannot_get_budget_for_unowned_project():
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_category="cli",
        budget_type="project",
        name="CLI Budget",
        description=None,
        soft_budget=20.0,
        max_budget=25.0,
        budget_duration="30d",
        budget_reset_at=None,
        provider_metadata={},
        created_by="admin-1",
        created_at=datetime(2026, 4, 23, tzinfo=UTC),
        updated_at=None,
    )
    assignment = SimpleNamespace(project_name="proj-a", allocation_mode="equal")

    with (
        patch(
            "codemie.rest_api.routers.project_budget_router.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.get_project_budget",
            new=AsyncMock(return_value=(budget, assignment, [])),
        ),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await get_project_budget("proj-budget-1", user=_project_admin_user(["proj-b"]))

    assert exc_info.value.code == 403


@pytest.mark.asyncio
async def test_rebalance_project_budget_rejects_deferred_execution():
    with pytest.raises(ExtendedHTTPException) as exc_info:
        await rebalance_project_budget(
            budget_id="proj-budget-1",
            payload=RebalanceProjectBudgetRequest(apply_immediately=False),
            user=_admin_user(),
            _=None,
        )

    assert exc_info.value.code == 400
    assert exc_info.value.message == "Only immediate rebalance is currently supported"


@pytest.mark.asyncio
async def test_clear_member_override_returns_reloaded_budget_response():
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_category="cli",
        budget_type="project",
        name="CLI Budget",
        description=None,
        soft_budget=20.0,
        max_budget=25.0,
        budget_duration="30d",
        budget_reset_at=None,
        is_active=True,
        provider_metadata={"provider": "litellm", "sync_status": "ok"},
        created_by="admin-1",
        created_at=datetime(2026, 4, 23, tzinfo=UTC),
        updated_at=None,
    )
    assignment = SimpleNamespace(project_name="proj-a", allocation_mode="equal")
    allocation = SimpleNamespace(
        user_id="user-1",
        allocation_mode="fixed",
        allocated_soft_budget=12.0,
        allocated_max_budget=15.0,
        sync_status="ok",
        provider_metadata={"provider_budget_id": "member-budget-7"},
    )

    with (
        patch(
            "codemie.rest_api.routers.project_budget_router.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.clear_member_override",
            new=AsyncMock(),
        ) as mock_clear_override,
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.get_project_budget",
            new=AsyncMock(return_value=(budget, assignment, [allocation])),
        ),
    ):
        result = await clear_member_override(
            budget_id="proj-budget-1",
            user_id="user-1",
            user=_admin_user(),
            _=None,
        )

    mock_clear_override.assert_awaited_once_with(
        session,
        budget_id="proj-budget-1",
        user_id="user-1",
        actor_id="admin-1",
    )
    assert result.member_allocations[0].budget_id == "member-budget-7"
    assert result.member_allocations[0].allocation_mode == "fixed"
