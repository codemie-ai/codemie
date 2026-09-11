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

from contextlib import ExitStack, asynccontextmanager
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
        is_active=True,
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
        is_active=True,
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
        is_active=True,
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
        is_active=True,
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


@pytest.mark.asyncio
async def test_project_budget_write_routes_deny_pure_auditor():
    """EPMCDME-10930 spec 5.2: pure-auditor 403 on POST/PUT/DELETE /v1/project-budgets.

    Every write route in this router is gated by maintainer_access_only. This walks the
    actual wired dependency (not a copy) for each write route and confirms it rejects a
    pure auditor (is_admin=False, is_maintainer=False, is_auditor=True), tying the
    auditor-write-rejection guarantee directly to this router's routes rather than only
    to the generic guard-function test in test_authentication_auditor.py.
    """
    from codemie.core.exceptions import ExtendedHTTPException

    write_route_paths = {
        "/v1/admin/project-budgets",
        "/v1/admin/project-budgets/{budget_id}",
        "/v1/admin/project-budgets/{budget_id}/reset",
        "/v1/admin/project-budgets/{budget_id}/rebalance",
        "/v1/admin/project-budgets/{budget_id}/members/{user_id}",
        "/v1/admin/project-budgets/{budget_id}/members/{user_id}/override",
    }

    request = AsyncMock()
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        request.state.user = User(
            id="auditor-1", username="auditor", is_admin=False, is_maintainer=False, is_auditor=True
        )

    write_routes = [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path in write_route_paths and "GET" not in (route.methods or set())
    ]
    assert len(write_routes) >= len(write_route_paths)

    checked_routes = 0
    for route in write_routes:
        for dependency in route.dependant.dependencies:
            if dependency.call.__name__ != "maintainer_access_only":
                continue
            checked_routes += 1
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await dependency.call(request)
            assert exc_info.value.code == 403

    assert checked_routes == len(write_routes)


# ---------------------------------------------------------------------------
# Task 2: project admin group update access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_group_project_admin_own_project_categories_only_succeeds():
    """Project admin can update categories for a group in their own project."""
    from codemie.rest_api.routers.project_budget_router import (
        update_project_budget_group,
        ProjectBudgetGroupUpdateRequest,
        CategoryBudgetSpecUpdate,
    )
    from unittest.mock import AsyncMock, patch
    from contextlib import asynccontextmanager

    user = _project_admin_user(["project-alpha"])
    group_id = "group-1"
    payload = ProjectBudgetGroupUpdateRequest(
        categories={"platform": CategoryBudgetSpecUpdate(pct=60.0), "cli": CategoryBudgetSpecUpdate(pct=40.0)}
    )

    group = SimpleNamespace(
        id=group_id,
        project_name="project-alpha",
        deleted_at=None,
        budget_duration="30d",
        created_by="creator-1",
        created_at=None,
        updated_at=None,
    )
    full_result = SimpleNamespace(group=group, categories=[], total_amount=100.0, budget_duration="30d")
    full_result.group.name = "Test Group"
    full_result.group.description = None

    mock_session = AsyncMock()

    @asynccontextmanager
    async def _session_ctx():
        yield mock_session

    with (
        patch("codemie.rest_api.routers.project_budget_router.get_async_session", return_value=_session_ctx()),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.update_project_budget_group",
            new_callable=AsyncMock,
            return_value=full_result,
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.get_project_budget_group",
            new_callable=AsyncMock,
            return_value=full_result,
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router._require_budgeting_enabled",
        ),
    ):
        result = await update_project_budget_group(group_id=group_id, payload=payload, user=user)

    assert result is not None


@pytest.mark.asyncio
async def test_update_group_project_admin_non_categories_field_raises_403():
    """Project admin is rejected when payload contains non-categories fields."""
    from codemie.rest_api.routers.project_budget_router import (
        update_project_budget_group,
        ProjectBudgetGroupUpdateRequest,
    )

    from unittest.mock import AsyncMock, patch
    from contextlib import asynccontextmanager

    user = _project_admin_user(["project-alpha"])
    payload = ProjectBudgetGroupUpdateRequest(name="new-name")

    group = SimpleNamespace(id="group-1", project_name="project-alpha", deleted_at=None)
    full_result = SimpleNamespace(group=group, categories=[], total_amount=100.0, budget_duration="30d")
    full_result.group.name = "Test Group"
    full_result.group.description = None

    mock_session = AsyncMock()

    @asynccontextmanager
    async def _session_ctx():
        yield mock_session

    with (
        patch("codemie.rest_api.routers.project_budget_router.get_async_session", return_value=_session_ctx()),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.get_project_budget_group",
            new_callable=AsyncMock,
            return_value=full_result,
        ),
        patch(
            "codemie.rest_api.routers.project_budget_router._require_budgeting_enabled",
        ),
        pytest.raises(ExtendedHTTPException) as exc_info,
    ):
        await update_project_budget_group(group_id="group-1", payload=payload, user=user)
    assert exc_info.value.code == 403


# ---------------------------------------------------------------------------
# group update write access
# ---------------------------------------------------------------------------


def _maintainer_user() -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="maint-1", username="maint@example.com", email="maint@example.com", is_maintainer=True)


def _regular_user_for_group_write() -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="other-1", username="other@example.com", email="other@example.com")


def test_ensure_allowed_budget_group_update_fields_allows_categories_for_project_admin():
    from codemie.rest_api.routers.project_budget_router import (
        _ensure_allowed_budget_group_update_fields,
        ProjectBudgetGroupUpdateRequest,
        CategoryBudgetSpecUpdate,
    )

    payload = ProjectBudgetGroupUpdateRequest(categories={"platform": CategoryBudgetSpecUpdate(pct=100.0)})
    _ensure_allowed_budget_group_update_fields(_project_admin_user(["project-alpha"]), payload)


@pytest.mark.parametrize(
    "field,value", [("name", "x"), ("total_amount", 5.0), ("budget_duration", "30d"), ("description", "d")]
)
def test_ensure_allowed_budget_group_update_fields_rejects_restricted_fields(field, value):
    from codemie.rest_api.routers.project_budget_router import (
        _ensure_allowed_budget_group_update_fields,
        ProjectBudgetGroupUpdateRequest,
    )

    payload = ProjectBudgetGroupUpdateRequest(**{field: value})
    with pytest.raises(ExtendedHTTPException) as exc_info:
        _ensure_allowed_budget_group_update_fields(_project_admin_user(["project-alpha"]), payload)
    assert exc_info.value.code == 403


def test_ensure_allowed_budget_group_update_fields_allows_any_field_for_maintainer():
    from codemie.rest_api.routers.project_budget_router import (
        _ensure_allowed_budget_group_update_fields,
        ProjectBudgetGroupUpdateRequest,
    )

    payload = ProjectBudgetGroupUpdateRequest(name="new-name", total_amount=10.0)
    _ensure_allowed_budget_group_update_fields(_maintainer_user(), payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_factory,allowed",
    [
        (_maintainer_user, True),
        (_admin_user, True),
        (lambda: _project_admin_user(["project-alpha"]), True),
        (lambda: _project_admin_user(["project-beta"]), False),
        (_regular_user_for_group_write, False),
    ],
    ids=["maintainer", "admin", "project_admin_own", "project_admin_other", "regular_user"],
)
async def test_update_group_write_access(user_factory, allowed):
    """Only maintainers, admins, and the group project's own admins may update it."""
    from codemie.rest_api.routers.project_budget_router import (
        update_project_budget_group,
        ProjectBudgetGroupUpdateRequest,
        CategoryBudgetSpecUpdate,
    )
    from unittest.mock import AsyncMock, patch as _patch
    from contextlib import asynccontextmanager

    payload = ProjectBudgetGroupUpdateRequest(categories={"platform": CategoryBudgetSpecUpdate(pct=100.0)})
    group = SimpleNamespace(id="group-1", project_name="project-alpha", deleted_at=None)
    full_result = SimpleNamespace(group=group, categories=[], total_amount=100.0)

    mock_session = AsyncMock()

    @asynccontextmanager
    async def _session_ctx():
        yield mock_session

    patches = (
        _patch("codemie.rest_api.routers.project_budget_router.get_async_session", return_value=_session_ctx()),
        _patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.get_project_budget_group",
            new_callable=AsyncMock,
            return_value=full_result,
        ),
        _patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.update_project_budget_group",
            new_callable=AsyncMock,
        ),
        _patch("codemie.rest_api.routers.project_budget_router._require_budgeting_enabled"),
        _patch("codemie.rest_api.routers.project_budget_router._build_project_budget_group_response"),
    )

    if allowed:
        with ExitStack() as stack:
            for pat in patches:
                stack.enter_context(pat)
            assert (
                await update_project_budget_group(group_id="group-1", payload=payload, user=user_factory()) is not None
            )
        return

    with ExitStack() as stack:
        for pat in patches:
            stack.enter_context(pat)
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await update_project_budget_group(group_id="group-1", payload=payload, user=user_factory())
    assert exc_info.value.code == 403
