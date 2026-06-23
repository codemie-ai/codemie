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
from fastapi.routing import APIRoute

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.project_budget_router import (
    ProjectBudgetResponse,
    _build_project_budget_response,
    list_project_budgets,
    list_project_budget_members,
    router,
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


def test_build_project_budget_response_includes_member_budget_id():
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_category="cli",
        budget_type="project",
        name="CLI Budget",
        description=None,
        soft_budget=20.0,
        max_budget=25.0,
        budget_duration="30d",
        budget_reset_at="2026-04-22T10:00:00Z",
        provider_metadata={"provider": "litellm", "sync_status": "ok"},
        created_by="admin-1",
        created_at=datetime(2026, 4, 23, tzinfo=UTC),
        updated_at=None,
    )
    assignment = SimpleNamespace(project_name="proj-a", allocation_mode="equal")
    allocation = SimpleNamespace(
        user_id="user-1",
        allocation_mode="equal",
        allocated_soft_budget=20.0,
        allocated_max_budget=25.0,
        sync_status="ok",
        provider_metadata={"raw": {"provider_budget_id": "member-budget-1"}},
    )

    with patch(
        "codemie.rest_api.routers.project_budget_router.SettingsService.get_enforce_member_spend_limits",
        return_value=True,
    ):
        result = _build_project_budget_response(budget, assignment, [allocation])

    assert result.member_allocations[0].budget_id == "member-budget-1"


def test_build_project_budget_response_uses_full_budget_when_enforcement_disabled():
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_category="cli",
        budget_type="project",
        name="CLI Budget",
        description=None,
        soft_budget=0.0,
        max_budget=100.0,
        budget_duration="30d",
        budget_reset_at=None,
        provider_metadata={},
        created_by="admin-1",
        created_at=None,
        updated_at=None,
    )
    assignment = SimpleNamespace(project_name="proj-a", allocation_mode="equal")
    allocations = [
        SimpleNamespace(
            user_id="user-1",
            allocation_mode="equal",
            allocated_soft_budget=20.0,
            allocated_max_budget=50.0,
            sync_status="ok",
            provider_metadata={},
        ),
        SimpleNamespace(
            user_id="user-2",
            allocation_mode="equal",
            allocated_soft_budget=20.0,
            allocated_max_budget=50.0,
            sync_status="ok",
            provider_metadata={},
        ),
    ]

    with patch(
        "codemie.rest_api.routers.project_budget_router.SettingsService.get_enforce_member_spend_limits",
        return_value=False,
    ):
        result = _build_project_budget_response(budget, assignment, allocations)

    assert result.member_allocations[0].allocated_max_budget == 100.0
    assert result.member_allocations[1].allocated_max_budget == 100.0
    assert result.allocated_member_budget_total == 200.0


def test_build_project_budget_response_uses_allocated_budget_when_enforcement_enabled():
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_category="cli",
        budget_type="project",
        name="CLI Budget",
        description=None,
        soft_budget=0.0,
        max_budget=100.0,
        budget_duration="30d",
        budget_reset_at=None,
        provider_metadata={},
        created_by="admin-1",
        created_at=None,
        updated_at=None,
    )
    assignment = SimpleNamespace(project_name="proj-a", allocation_mode="equal")
    allocations = [
        SimpleNamespace(
            user_id="user-1",
            allocation_mode="equal",
            allocated_soft_budget=20.0,
            allocated_max_budget=50.0,
            sync_status="ok",
            provider_metadata={},
        ),
        SimpleNamespace(
            user_id="user-2",
            allocation_mode="equal",
            allocated_soft_budget=20.0,
            allocated_max_budget=50.0,
            sync_status="ok",
            provider_metadata={},
        ),
    ]

    with patch(
        "codemie.rest_api.routers.project_budget_router.SettingsService.get_enforce_member_spend_limits",
        return_value=True,
    ):
        result = _build_project_budget_response(budget, assignment, allocations)

    assert result.member_allocations[0].allocated_max_budget == 50.0
    assert result.member_allocations[1].allocated_max_budget == 50.0
    assert result.allocated_member_budget_total == 100.0


@pytest.mark.asyncio
async def test_list_project_budget_members_returns_nullable_budget_id():
    session = AsyncMock()
    allocation = SimpleNamespace(
        user_id="user-1",
        allocation_mode="equal",
        allocated_soft_budget=20.0,
        allocated_max_budget=25.0,
        sync_status="ok",
        provider_metadata={"raw": {"provider_budget_id": "member-budget-1"}},
    )

    with (
        patch(
            "codemie.rest_api.routers.project_budget_router.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.get_project_budget",
            new=AsyncMock(
                return_value=(SimpleNamespace(max_budget=25.0), SimpleNamespace(project_name="proj-a"), [allocation])
            ),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
    ):
        result = await list_project_budget_members("proj-budget-1", user=_admin_user())

    assert result.data[0].budget_id == "member-budget-1"


@pytest.mark.asyncio
async def test_list_project_budget_members_returns_effective_budget_when_enforcement_disabled():
    session = AsyncMock()
    budget = SimpleNamespace(max_budget=100.0)
    assignment = SimpleNamespace(project_name="proj-a")
    allocation = SimpleNamespace(
        user_id="user-1",
        allocation_mode="equal",
        allocated_soft_budget=20.0,
        allocated_max_budget=50.0,
        sync_status="ok",
        provider_metadata={},
    )

    with (
        patch(
            "codemie.rest_api.routers.project_budget_router.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.get_project_budget",
            new=AsyncMock(return_value=(budget, assignment, [allocation])),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.SettingsService.get_enforce_member_spend_limits",
            return_value=False,
        ),
    ):
        result = await list_project_budget_members("proj-budget-1", user=_admin_user())

    assert result.data[0].allocated_max_budget == 100.0


@pytest.mark.asyncio
async def test_project_admin_can_list_budgets_for_owned_project():
    session = AsyncMock()
    budget = SimpleNamespace(budget_id="proj-budget-1")
    response = ProjectBudgetResponse(
        budget_id="proj-budget-1",
        project_name="proj-a",
        budget_category="cli",
        budget_type="project",
        name="CLI Budget",
        description=None,
        soft_budget=20.0,
        max_budget=25.0,
        budget_duration="30d",
        allocation_mode="equal",
        budget_reset_at=None,
        member_count=1,
        allocated_member_budget_total=25.0,
        provider="litellm",
        provider_sync_status="ok",
        provider_last_synced_at=None,
        created_by="admin-1",
        created_at=None,
        updated_at=None,
        member_allocations=[],
    )

    with (
        patch(
            "codemie.rest_api.routers.project_budget_router.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.list_project_budgets",
            new=AsyncMock(return_value=([budget], 1)),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router._load_and_build_response",
            new=AsyncMock(return_value=response),
        ),
    ):
        result = await list_project_budgets(
            project_name="proj-a",
            category=None,
            page=0,
            per_page=20,
            user=_project_admin_user(["proj-a"]),
        )

    assert result.total == 1
    assert result.items[0].budget_id == "proj-budget-1"


@pytest.mark.asyncio
async def test_project_admin_cannot_list_budgets_for_other_project():
    with pytest.raises(ExtendedHTTPException) as exc_info:
        await list_project_budgets(
            project_name="proj-b",
            category=None,
            page=0,
            per_page=20,
            user=_project_admin_user(["proj-a"]),
        )

    assert exc_info.value.code == 403


@pytest.mark.asyncio
async def test_project_admin_can_read_budget_members_for_owned_project():
    session = AsyncMock()
    assignment = SimpleNamespace(project_name="proj-a")
    allocation = SimpleNamespace(
        user_id="user-1",
        allocation_mode="equal",
        allocated_soft_budget=20.0,
        allocated_max_budget=25.0,
        sync_status="ok",
        provider_metadata={},
    )

    with (
        patch(
            "codemie.rest_api.routers.project_budget_router.get_async_session",
            return_value=_mock_session_ctx(session),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.get_project_budget",
            new=AsyncMock(return_value=(SimpleNamespace(max_budget=25.0), assignment, [allocation])),
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.SettingsService.get_enforce_member_spend_limits",
            return_value=True,
        ),
    ):
        result = await list_project_budget_members(
            "proj-budget-1",
            user=_project_admin_user(["proj-a"]),
        )

    assert result.data[0].user_id == "user-1"


def test_project_budget_write_routes_keep_maintainer_dependency():
    write_route_paths = {
        "/v1/admin/project-budgets",
        "/v1/admin/project-budgets/{budget_id}",
        "/v1/admin/project-budgets/{budget_id}/reset",
        "/v1/admin/project-budgets/{budget_id}/rebalance",
        "/v1/admin/project-budgets/{budget_id}/members/{user_id}",
        "/v1/admin/project-budgets/{budget_id}/members/{user_id}/override",
    }

    for route in router.routes:
        if not isinstance(route, APIRoute) or route.path not in write_route_paths or "GET" in (route.methods or set()):
            continue
        dependency_calls = {dependency.call.__name__ for dependency in route.dependant.dependencies}
        assert "maintainer_access_only" in dependency_calls
