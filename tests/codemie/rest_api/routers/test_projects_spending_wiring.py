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

The tests name no module that holds the spending helpers: they patch the repository class, the repository
singletons and the database session at their defining modules, so they stay valid wherever the helpers live.
"""

from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs import config
from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
from codemie.rest_api.security.user import User

SPEND_DATE = datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)
SPEND_READS = (
    "get_latest_spending_by_project",
    "get_latest_project_budget_rows_by_category",
    "get_latest_key_spending_for_project",
    "get_latest_budget_rows_for_project",
    "get_lifetime_spend",
)
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


def _spend_row(project: str, budget_id: str, spend: str) -> SimpleNamespace:
    return SimpleNamespace(
        project_name=project,
        budget_id=budget_id,
        budget_category=None,
        budget_period_spend=Decimal(spend),
        spend_date=SPEND_DATE,
    )


def _budget(max_budget: float) -> SimpleNamespace:
    return SimpleNamespace(max_budget=max_budget, budget_reset_at=None, deleted_at=None)


@pytest.fixture
def spending_io():
    """The database session and every repository read of the spending helpers, replaced at their defining modules."""
    session = MagicMock(name="session")
    session_scope = MagicMock(name="session scope")
    session_scope.__aenter__.return_value = session
    session_scope.__aexit__.return_value = False  # a truthy value would swallow an assertion error raised inside
    with ExitStack() as stack:
        stack.enter_context(patch("codemie.clients.postgres.PostgresClient.get_async_engine"))
        session_class = stack.enter_context(patch("codemie.clients.postgres.AsyncSession", return_value=session_scope))
        reads = {
            name: stack.enter_context(patch.object(ProjectSpendTrackingRepository, name, new_callable=AsyncMock))
            for name in SPEND_READS
        }
        budgets = stack.enter_context(
            patch(
                "codemie.repository.budget_repository.budget_repository.get_all_keyed_by_id",
                new_callable=AsyncMock,
                return_value={},
            )
        )
        assigned = stack.enter_context(
            patch(
                "codemie.repository.project_budget_repository.project_budget_assignment_repository."
                "get_assigned_budget_summaries_for_projects",
                new_callable=AsyncMock,
                return_value={},
            )
        )
        for name in ("get_latest_spending_by_project", "get_latest_project_budget_rows_by_category"):
            reads[name].return_value = []  # an unset AsyncMock result is a truthy MagicMock
        reads["get_latest_budget_rows_for_project"].return_value = []
        reads["get_latest_key_spending_for_project"].return_value = None
        reads["get_lifetime_spend"].return_value = 0.0
        yield SimpleNamespace(
            session=session,
            session_class=session_class,
            reads=reads,
            budgets=budgets,
            assigned=assigned,
            every_read=[*reads.values(), budgets, assigned],
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
        spending_io.reads["get_latest_key_spending_for_project"].return_value = _spend_row("proj-a", "bk", "1.5")
        spending_io.reads["get_lifetime_spend"].return_value = 3.0
        spending_io.budgets.return_value = {"bk": _budget(6.0)}

        response = await _get_project_detail(_project("proj-a"), _user("admin-1", is_admin=True))

        spending_io.reads["get_latest_key_spending_for_project"].assert_awaited_once_with(spending_io.session, "proj-a")
        spending_io.reads["get_latest_project_budget_rows_by_category"].assert_awaited_once_with(
            spending_io.session, ["proj-a"]
        )
        spending_io.reads["get_latest_budget_rows_for_project"].assert_awaited_once_with(
            spending_io.session, "proj-a", rows_limit=NON_DEFAULT_ROWS_LIMIT
        )
        spending_io.reads["get_lifetime_spend"].assert_awaited_once_with(
            spending_io.session, "proj-a", spend_subject_type="key"
        )
        spending_io.budgets.assert_awaited_once_with(spending_io.session)
        spending_io.assigned.assert_awaited_once_with(spending_io.session, ["proj-a"])
        assert response.spending.model_dump() == {
            "current_spending": 1.5,
            "cumulative_spend": 3.0,
            "budget_reset_at": None,
            "time_until_reset": None,
            "budget_limit": 6.0,
            "total": 25.0,
        }
        assert [row.budget_id for row in response.spending_widget.data.rows] == ["Key"]

    async def test_project_admin_gets_spending(self, spending_io):
        spending_io.reads["get_latest_key_spending_for_project"].return_value = _spend_row("proj-a", "bk", "1.5")

        response = await _get_project_detail(_project("proj-a", is_project_admin=True), _user("user-1"))

        assert response.spending is not None
        spending_io.session_class.assert_called_once()

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
        key_row = _spend_row("proj-admin", "bk", "2.5")
        budget_row = _spend_row("proj-admin", "b1", "1.0")
        spending_io.reads["get_latest_spending_by_project"].side_effect = (
            lambda _session, _names, spend_subject_type: [key_row] if spend_subject_type == "key" else [budget_row]
        )
        spending_io.budgets.return_value = {"bk": _budget(10.0), "b1": _budget(5.0)}

        response = await _list_projects(projects, user)

        queried = {
            call.kwargs["spend_subject_type"]: call.args[1]
            for call in spending_io.reads["get_latest_spending_by_project"].await_args_list
        }
        assert queried == {"key": ["proj-admin"], "budget": ["proj-admin"]}
        spending_io.reads["get_latest_project_budget_rows_by_category"].assert_awaited_once_with(
            spending_io.session, ["proj-admin"]
        )
        spending_io.assigned.assert_awaited_once_with(spending_io.session, ["proj-admin"])
        assert response.data[0].spending.model_dump() == {
            "current_spending": 3.5,
            "budget_limit": 15.0,
            "total_percent": 23.33,
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

        spending_io.reads["get_latest_project_budget_rows_by_category"].assert_awaited_once_with(
            spending_io.session, ["proj-a", "proj-b"]
        )
