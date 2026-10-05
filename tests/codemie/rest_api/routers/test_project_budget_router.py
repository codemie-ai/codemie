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

from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.routers.project_budget_router import (
    OverrideMemberAllocationRequest,
    ProjectBudgetResponse,
    _build_project_budget_response,
    _can_manage_project_budget_members,
    _ensure_can_manage_project_budget_members,
    clear_member_override,
    list_project_budgets,
    list_project_budget_members,
    override_member_allocation,
    router,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from tests.codemie.rest_api.routers.project_budget_helpers import (
    admin_user,
    auditor_user,
    maintainer_user,
    mock_session_ctx,
    project_admin_user,
    regular_user,
)


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
            return_value=mock_session_ctx(session),
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
        result = await list_project_budget_members("proj-budget-1", user=admin_user())

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
            return_value=mock_session_ctx(session),
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
        result = await list_project_budget_members("proj-budget-1", user=admin_user())

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
            return_value=mock_session_ctx(session),
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
            user=project_admin_user(["proj-a"]),
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
            user=project_admin_user(["proj-a"]),
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
            return_value=mock_session_ctx(session),
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
            user=project_admin_user(["proj-a"]),
        )

    assert result.data[0].user_id == "user-1"


def test_project_budget_write_routes_keep_maintainer_dependency():
    write_route_paths = {
        "/v1/admin/project-budgets",
        "/v1/admin/project-budgets/{budget_id}",
        "/v1/admin/project-budgets/{budget_id}/reset",
        "/v1/admin/project-budgets/{budget_id}/rebalance",
    }

    write_routes = [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path in write_route_paths and "GET" not in (route.methods or set())
    ]
    assert {route.path for route in write_routes} == write_route_paths
    for route in write_routes:
        dependency_calls = {dependency.call.__name__ for dependency in route.dependant.dependencies}
        assert "maintainer_access_only" in dependency_calls


def test_project_budget_member_override_routes_drop_maintainer_dependency():
    """EPMCDME-15234: member override routes are gated in the endpoint body by project write access."""
    member_route_paths = {
        "/v1/admin/project-budgets/{budget_id}/members/{user_id}",
        "/v1/admin/project-budgets/{budget_id}/members/{user_id}/override",
    }

    member_routes = [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path in member_route_paths and "GET" not in (route.methods or set())
    ]
    assert len(member_routes) == len(member_route_paths)
    for route in member_routes:
        dependency_calls = {dependency.call.__name__ for dependency in route.dependant.dependencies}
        assert "maintainer_access_only" not in dependency_calls


@pytest.mark.asyncio
async def test_project_budget_write_routes_deny_pure_auditor():
    """EPMCDME-10930 spec 5.2: pure-auditor 403 on POST/PUT/DELETE /v1/project-budgets.

    Every non-member write route in this router is gated by maintainer_access_only. This walks
    the actual wired dependency (not a copy) for each of those routes and confirms it rejects a
    pure auditor (is_admin=False, is_maintainer=False, is_auditor=True), tying the
    auditor-write-rejection guarantee directly to this router's routes rather than only to the
    generic guard-function test in test_authentication_auditor.py.

    The member override routes (EPMCDME-15234) are gated in the endpoint body by project write
    access instead; auditor denial there is covered by
    test_member_override_endpoints_deny_without_project_write_access.
    """
    write_route_paths = {
        "/v1/admin/project-budgets",
        "/v1/admin/project-budgets/{budget_id}",
        "/v1/admin/project-budgets/{budget_id}/reset",
        "/v1/admin/project-budgets/{budget_id}/rebalance",
    }

    request = AsyncMock()
    request.state.user = auditor_user()

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

    user = project_admin_user(["project-alpha"])
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

    user = project_admin_user(["project-alpha"])
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


def test_ensure_allowed_budget_group_update_fields_allows_categories_for_project_admin():
    from codemie.rest_api.routers.project_budget_router import (
        _ensure_allowed_budget_group_update_fields,
        ProjectBudgetGroupUpdateRequest,
        CategoryBudgetSpecUpdate,
    )

    payload = ProjectBudgetGroupUpdateRequest(categories={"platform": CategoryBudgetSpecUpdate(pct=100.0)})
    _ensure_allowed_budget_group_update_fields(project_admin_user(["project-alpha"]), payload)


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
        _ensure_allowed_budget_group_update_fields(project_admin_user(["project-alpha"]), payload)
    assert exc_info.value.code == 403


def test_ensure_allowed_budget_group_update_fields_allows_any_field_for_maintainer():
    from codemie.rest_api.routers.project_budget_router import (
        _ensure_allowed_budget_group_update_fields,
        ProjectBudgetGroupUpdateRequest,
    )

    payload = ProjectBudgetGroupUpdateRequest(name="new-name", total_amount=10.0)
    _ensure_allowed_budget_group_update_fields(maintainer_user(), payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_factory,allowed",
    [
        (maintainer_user, True),
        (admin_user, True),
        (lambda: project_admin_user(["project-alpha"]), True),
        (lambda: project_admin_user(["project-beta"]), False),
        (regular_user, False),
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


# ---------------------------------------------------------------------------
# member override write access (EPMCDME-15234)
# ---------------------------------------------------------------------------

_ROUTER_PATH = "codemie.rest_api.routers.project_budget_router"
_SERVICE_PATH = f"{_ROUTER_PATH}.project_budget_service"
_ACTIVE_ASSIGNMENT_LOOKUP_PATH = (
    "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_by_budget_id"
)
_BUDGET_ID = "proj-budget-1"
_MEMBER_ID = "member-1"
_OVERRIDE_PAYLOAD = {"allocated_max_budget": 10.0, "allocated_soft_budget": 8.0}
_DENIED_DETAILS = "You do not have permission to manage member budgets for 'P'."
_NOT_FOUND_MESSAGE = f"Project budget not found: {_BUDGET_ID}"


@dataclass(frozen=True)
class _MemberEndpoint:
    service_method: str
    invoke: Callable[[User, str], Awaitable[Any]]
    http_method: str
    http_path: str
    http_json: dict | None = None


_OVERRIDE = _MemberEndpoint(
    service_method="override_member_allocation",
    invoke=lambda user, user_id: override_member_allocation(
        budget_id=_BUDGET_ID,
        user_id=user_id,
        payload=OverrideMemberAllocationRequest(**_OVERRIDE_PAYLOAD),
        user=user,
    ),
    http_method="PATCH",
    http_path=f"/v1/admin/project-budgets/{_BUDGET_ID}/members/{_MEMBER_ID}",
    http_json=_OVERRIDE_PAYLOAD,
)
_CLEAR = _MemberEndpoint(
    service_method="clear_member_override",
    invoke=lambda user, user_id: clear_member_override(budget_id=_BUDGET_ID, user_id=user_id, user=user),
    http_method="DELETE",
    http_path=f"/v1/admin/project-budgets/{_BUDGET_ID}/members/{_MEMBER_ID}/override",
)
_MEMBER_ENDPOINTS = pytest.mark.parametrize("endpoint", [_OVERRIDE, _CLEAR], ids=["override", "clear"])

_ALLOWED_USERS = pytest.mark.parametrize(
    "user_factory",
    [lambda: project_admin_user(["P"]), maintainer_user],
    ids=["project-admin-of-p", "maintainer"],
)
_DENIED_USERS = pytest.mark.parametrize(
    "user_factory",
    [lambda: project_admin_user(["Q"]), admin_user, auditor_user, regular_user],
    ids=["project-admin-of-q", "plain-admin", "auditor", "regular-user"],
)


def _budget_lookup() -> AsyncMock:
    budget = SimpleNamespace(
        budget_id=_BUDGET_ID,
        budget_category="cli",
        budget_type="project",
        name="CLI Budget",
        description=None,
        soft_budget=20.0,
        max_budget=25.0,
        budget_duration="30d",
        budget_reset_at=None,
        is_active=True,
        provider_metadata={},
        created_by="admin-1",
        created_at=datetime(2026, 4, 23, tzinfo=UTC),
        updated_at=None,
    )
    assignment = SimpleNamespace(project_name="P", allocation_mode="equal")
    return AsyncMock(return_value=(budget, assignment, []))


def _patch_member_endpoint(
    endpoint: _MemberEndpoint,
    service_mock: AsyncMock,
    *,
    budget_exists: bool = True,
    budget_lookup: AsyncMock | None = None,
):
    active_assignment = SimpleNamespace(project_name="P") if budget_exists else None
    stack = ExitStack()
    stack.enter_context(
        patch(f"{_ROUTER_PATH}.get_async_session", side_effect=lambda: mock_session_ctx(AsyncMock())),
    )
    stack.enter_context(
        patch(_ACTIVE_ASSIGNMENT_LOOKUP_PATH, new=AsyncMock(return_value=active_assignment)),
    )
    stack.enter_context(patch(f"{_SERVICE_PATH}.get_project_budget", new=budget_lookup or _budget_lookup()))
    stack.enter_context(patch(f"{_SERVICE_PATH}.{endpoint.service_method}", new=service_mock))
    stack.enter_context(
        patch(f"{_ROUTER_PATH}.SettingsService.get_enforce_member_spend_limits", return_value=True),
    )
    return stack


async def _call_member_endpoint(
    endpoint: _MemberEndpoint,
    user: User,
    service_mock: AsyncMock,
    *,
    user_id: str = _MEMBER_ID,
    budget_exists: bool = True,
    budget_lookup: AsyncMock | None = None,
):
    with _patch_member_endpoint(endpoint, service_mock, budget_exists=budget_exists, budget_lookup=budget_lookup):
        return await endpoint.invoke(user, user_id)


@pytest.mark.parametrize(
    ("user_factory", "expected"),
    [
        (maintainer_user, True),
        (lambda: project_admin_user(["P"]), True),
        (lambda: project_admin_user(["Q"]), False),
        (admin_user, False),
        (auditor_user, False),
        (lambda: auditor_user(["P"]), True),
        (regular_user, False),
    ],
    ids=[
        "maintainer",
        "project-admin-of-p",
        "project-admin-of-q",
        "plain-admin",
        "auditor",
        "auditor-project-admin-of-p",
        "regular-user",
    ],
)
def test_can_manage_project_budget_members(user_factory, expected):
    assert _can_manage_project_budget_members(user_factory(), "P") is expected


def test_ensure_can_manage_project_budget_members_raises_403():
    user = project_admin_user(["Q"])
    with (
        patch(f"{_ROUTER_PATH}.logger") as mock_logger,
        pytest.raises(ExtendedHTTPException) as exc_info,
    ):
        _ensure_can_manage_project_budget_members(user, "P", _BUDGET_ID)

    assert exc_info.value.code == 403
    assert exc_info.value.message == "Access denied"
    assert exc_info.value.details == _DENIED_DETAILS
    mock_logger.warning.assert_called_once_with(
        f"access_denied_project_budget_members_manage: actor_user_id={user.id}, resource=P, "
        f"budget_id={_BUDGET_ID}, domain=project_budget"
    )


@pytest.mark.asyncio
@_MEMBER_ENDPOINTS
@_ALLOWED_USERS
async def test_member_override_endpoints_allow_project_admin_and_maintainer(endpoint, user_factory):
    user = user_factory()
    service_mock = AsyncMock()

    result = await _call_member_endpoint(endpoint, user, service_mock)

    assert result.budget_id == _BUDGET_ID
    service_mock.assert_awaited_once()
    assert service_mock.await_args.kwargs["actor_id"] == user.id


@pytest.mark.asyncio
@_MEMBER_ENDPOINTS
async def test_member_override_endpoints_allow_project_admin_targeting_self(endpoint):
    user = project_admin_user(["P"])
    service_mock = AsyncMock()

    await _call_member_endpoint(endpoint, user, service_mock, user_id=user.id)

    service_mock.assert_awaited_once()
    assert service_mock.await_args.kwargs["user_id"] == user.id


@pytest.mark.asyncio
@_MEMBER_ENDPOINTS
@_DENIED_USERS
async def test_member_override_endpoints_deny_without_project_write_access(endpoint, user_factory):
    service_mock = AsyncMock()

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await _call_member_endpoint(endpoint, user_factory(), service_mock)

    assert exc_info.value.code == 403
    assert exc_info.value.message == "Access denied"
    assert exc_info.value.details == _DENIED_DETAILS
    service_mock.assert_not_awaited()


@pytest.mark.asyncio
@_MEMBER_ENDPOINTS
@pytest.mark.parametrize(
    "user_factory",
    [maintainer_user, lambda: project_admin_user(["P"]), lambda: project_admin_user(["Q"]), admin_user, regular_user],
    ids=["maintainer", "project-admin-of-p", "project-admin-of-q", "plain-admin", "regular-user"],
)
async def test_member_override_endpoints_return_404_without_active_project_assignment(endpoint, user_factory):
    """Unknown budgets and non-project budgets both lack an active project assignment."""
    service_mock = AsyncMock()

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await _call_member_endpoint(endpoint, user_factory(), service_mock, budget_exists=False)

    assert exc_info.value.code == 404
    assert exc_info.value.message == _NOT_FOUND_MESSAGE
    service_mock.assert_not_awaited()


@pytest.mark.asyncio
@_MEMBER_ENDPOINTS
async def test_member_override_endpoints_load_full_budget_once_per_request(endpoint):
    budget_lookup = _budget_lookup()

    await _call_member_endpoint(endpoint, maintainer_user(), AsyncMock(), budget_lookup=budget_lookup)

    budget_lookup.assert_awaited_once()


@pytest.fixture
def send_member_request():
    """Send a member override request through the real router wiring and error handler."""
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    client = TestClient(app)

    def send(endpoint: _MemberEndpoint, user: User):
        app.dependency_overrides[authenticate] = lambda: user
        return client.request(endpoint.http_method, endpoint.http_path, json=endpoint.http_json)

    return send


@_MEMBER_ENDPOINTS
@_ALLOWED_USERS
def test_member_override_http_returns_refreshed_budget(send_member_request, endpoint, user_factory):
    service_mock = AsyncMock()

    with _patch_member_endpoint(endpoint, service_mock):
        response = send_member_request(endpoint, user_factory())

    assert response.status_code == 200
    assert response.json()["budget_id"] == _BUDGET_ID
    assert response.json()["project_name"] == "P"
    service_mock.assert_awaited_once()


@_MEMBER_ENDPOINTS
@_DENIED_USERS
def test_member_override_http_returns_403_without_project_write_access(send_member_request, endpoint, user_factory):
    service_mock = AsyncMock()

    with _patch_member_endpoint(endpoint, service_mock):
        response = send_member_request(endpoint, user_factory())

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Access denied"
    assert response.json()["error"]["details"] == _DENIED_DETAILS
    service_mock.assert_not_awaited()


@_MEMBER_ENDPOINTS
def test_member_override_http_returns_404_for_unknown_budget(send_member_request, endpoint):
    service_mock = AsyncMock()

    with _patch_member_endpoint(endpoint, service_mock, budget_exists=False):
        response = send_member_request(endpoint, project_admin_user(["P"]))

    assert response.status_code == 404
    assert response.json()["error"]["message"] == _NOT_FOUND_MESSAGE
    service_mock.assert_not_awaited()
