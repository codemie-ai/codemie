# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Handler-level wiring of the project spending helpers: who may see spending and which projects are queried.

The tests name no module that holds the spending helpers: they patch the budget repository singleton, the reset
reconciliation, the spend port and the database session at their defining modules, so they stay valid wherever the
helpers live.
"""

from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs import config
from codemie.rest_api.security.user import User
from codemie.service.spend.coverage import SpendResult

# Differs from the route default (50) so a handler that ignores the query parameter is caught.
NON_DEFAULT_ROWS_LIMIT = 37


def _user(user_id: str, *, is_admin: bool = False, **fields) -> User:
    """A real user; ``User`` would otherwise make everybody an admin under the test environment."""
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id=user_id, username=user_id, email=f"{user_id}@example.com", is_admin=is_admin, **fields)


def _project(name: str, **fields) -> dict:
    return {
        "name": name,
        "description": "",
        "project_type": "shared",
        "created_by": "owner-1",
        "created_at": datetime(2026, 2, 10, tzinfo=timezone.utc),
        "user_count": 2,
        "admin_count": 1,
        "members": [],
        **fields,
    }


def _budget_row(project: str, max_budget: float = 6.0) -> SimpleNamespace:
    return SimpleNamespace(
        project_name=project,
        budget_id="budget-1",
        name="Platform Budget",
        budget_category="platform",
        soft_budget=max_budget / 2,
        max_budget=max_budget,
        budget_duration="30d",
        budget_reset_at="2099-04-23T10:10:00Z",
        provider_sync_status="ok",
        member_count=0,
        allocated_member_budget_total=0.0,
        budget_created_at=None,
        budget_spend_reset_at=None,
    )


@pytest.fixture
def spending_io():
    """The database session and every read of the spending helpers, replaced at their defining modules."""
    session = MagicMock(name="session")
    session_scope = MagicMock(name="session scope")
    session_scope.__aenter__.return_value = session
    session_scope.__aexit__.return_value = False  # a truthy value would swallow an assertion error raised inside
    with ExitStack() as stack:
        stack.enter_context(patch("codemie.clients.postgres.PostgresClient.get_async_engine"))
        session_class = stack.enter_context(patch("codemie.clients.postgres.AsyncSession", return_value=session_scope))
        repository = "codemie.repository.project_budget_repository.project_budget_assignment_repository"
        assigned = stack.enter_context(
            patch(f"{repository}.get_assigned_budget_summaries_for_projects", new_callable=AsyncMock, return_value={})
        )
        removed = stack.enter_context(
            patch(f"{repository}.get_removed_budget_summaries_for_projects", new_callable=AsyncMock, return_value={})
        )
        refresh = stack.enter_context(
            patch(
                "codemie.service.budget.daily_reset_reconciliation_service."
                "daily_budget_reset_reconciliation_service.refresh_overdue",
                new_callable=AsyncMock,
                return_value=set(),
            )
        )
        consumed = stack.enter_context(
            patch(
                "codemie.service.spend.spend_service.spend_service.consumed_for_holders",
                new_callable=AsyncMock,
                side_effect=lambda holders, period: {holder: SpendResult(figure=Decimal("1.5")) for holder in holders},
            )
        )
        yield SimpleNamespace(
            session=session,
            session_class=session_class,
            assigned=assigned,
            removed=removed,
            refresh=refresh,
            consumed=consumed,
            every_read=[assigned, removed, refresh, consumed],
        )


def _assert_nothing_was_read(spending_io) -> None:
    spending_io.session_class.assert_not_called()
    for read in spending_io.every_read:
        read.assert_not_awaited()


async def _get_project_detail(project: dict, user: User, include_spending: bool = True):
    from codemie.rest_api.routers.projects.crud import get_project_detail

    with (
        patch("codemie.rest_api.routers.projects._common.config") as mock_config,
        patch("codemie.rest_api.routers.projects.crud._get_project_detail_sync", return_value=project),
        patch("codemie.rest_api.routers.projects.crud.SettingsService.get_enforce_member_spend_limits"),
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        return await get_project_detail(
            request=MagicMock(method="GET", url=SimpleNamespace(path=f"/v1/projects/{project['name']}")),
            project_name=project["name"],
            include_spending=include_spending,
            spending_rows_limit=NON_DEFAULT_ROWS_LIMIT,
            user=user,
        )


async def _list_projects(projects: list[dict], user: User):
    from codemie.rest_api.routers.projects.crud import list_projects

    with (
        patch("codemie.rest_api.routers.projects._common.config") as mock_config,
        patch("codemie.rest_api.routers.projects.crud._list_projects_sync", return_value=(projects, len(projects))),
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        return await list_projects(
            search=None,
            page=0,
            per_page=20,
            include_counters=True,
            include_spending=True,
            include_budgets=False,
            has_assigned_budgets=False,
            budget_category=None,
            sort_by=None,
            sort_order="asc",
            user=user,
        )


@pytest.mark.anyio
class TestProjectDetailSpending:
    async def test_global_admin_gets_spending_read_through_the_session(self, spending_io):
        spending_io.assigned.return_value = {"proj-a": [_budget_row("proj-a")]}

        response = await _get_project_detail(_project("proj-a"), _user("admin-1", is_admin=True))

        spending_io.assigned.assert_awaited_once_with(spending_io.session, ["proj-a"], budget_category=None)
        spending_io.removed.assert_awaited_once_with(spending_io.session, ["proj-a"])
        assert response.spending.current_spending == 1.5
        assert response.spending.budget_limit == 6.0
        assert response.spending.total == 25.0
        assert [row.budget_id for row in response.spending_widget.data.rows] == ["budget-1"]

    async def test_project_admin_gets_spending(self, spending_io):
        spending_io.assigned.return_value = {"proj-a": [_budget_row("proj-a")]}

        response = await _get_project_detail(_project("proj-a", is_project_admin=True), _user("user-1"))

        assert response.spending is not None
        spending_io.session_class.assert_called()

    async def test_member_who_is_not_a_project_admin_gets_no_spending_and_nothing_is_read(self, spending_io):
        member = _user("user-1", project_names=["proj-a"])

        response = await _get_project_detail(_project("proj-a", is_project_admin=False), member)

        assert response.spending is None
        assert response.spending_widget is None
        _assert_nothing_was_read(spending_io)

    async def test_member_without_the_project_admin_flag_in_the_detail_gets_no_spending(self, spending_io):
        response = await _get_project_detail(_project("proj-a"), _user("user-1", project_names=["proj-a"]))

        assert (response.spending, response.spending_widget) == (None, None)
        _assert_nothing_was_read(spending_io)

    async def test_spending_is_not_read_when_not_requested(self, spending_io):
        response = await _get_project_detail(
            _project("proj-a", is_project_admin=True), _user("user-1"), include_spending=False
        )

        assert response.spending is None
        _assert_nothing_was_read(spending_io)


@pytest.mark.anyio
class TestProjectListSpending:
    async def test_only_the_project_the_user_administers_is_queried_and_summarized(self, spending_io):
        projects = [_project("proj-admin", is_project_admin=True), _project("proj-member", is_project_admin=False)]
        user = _user("user-1", project_names=["proj-admin", "proj-member"], admin_project_names=["proj-admin"])
        spending_io.assigned.return_value = {"proj-admin": [_budget_row("proj-admin", max_budget=6.0)]}

        response = await _list_projects(projects, user)

        spending_io.assigned.assert_awaited_once_with(spending_io.session, ["proj-admin"], budget_category=None)
        spending_io.removed.assert_awaited_once_with(spending_io.session, ["proj-admin"])
        assert response.data[0].spending.model_dump() == {
            "current_spending": 1.5,
            "budget_limit": 6.0,
            "total_percent": 25.0,
            "spend_unavailable": False,
            "spend_partial": False,
            "spend_missing_amount": None,
        }
        assert response.data[1].spending is None

    async def test_nothing_manageable_queries_nothing_and_leaves_every_spending_unset(self, spending_io):
        projects = [_project("proj-a", is_project_admin=False), _project("proj-b")]
        user = _user("user-1", project_names=["proj-a", "proj-b"])

        response = await _list_projects(projects, user)

        assert [item.spending for item in response.data] == [None, None]
        _assert_nothing_was_read(spending_io)

    async def test_global_admin_has_every_project_queried(self, spending_io):
        projects = [_project("proj-a"), _project("proj-b")]

        await _list_projects(projects, _user("admin-1", is_admin=True))

        spending_io.assigned.assert_awaited_once_with(spending_io.session, ["proj-a", "proj-b"], budget_category=None)
