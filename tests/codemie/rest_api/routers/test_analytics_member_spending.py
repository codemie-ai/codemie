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

"""Tests for the member spend analytics endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User

PAGE_ARGS = {"page": 0, "per_page": config.ANALYTICS_DEFAULT_PAGE_SIZE}

USER_COLUMNS = [{"id": "project_name", "label": "Project", "type": "string", "format": None}]
MEMBER_COLUMNS = [{"id": "user_id", "label": "User", "type": "string", "format": None}]


def _admin_user() -> User:
    """A global admin caller. ENV patched so is_admin is honoured, not recomputed from roles."""
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="caller-admin", username="admin", email="admin@corp.com", is_admin=True)


def _project_admin_user(projects: list[str]) -> User:
    """A caller who is admin of `projects` only. ENV patched so is_admin is not forced True."""
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(
            id="caller-pa",
            username="pa",
            email="pa@corp.com",
            is_admin=False,
            project_names=projects,
            admin_project_names=projects,
        )


def _plain_user() -> User:
    """A caller who administers nothing."""
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(
            id="caller-plain",
            username="plain",
            email="plain@corp.com",
            is_admin=False,
            project_names=["atlas-core"],
            admin_project_names=[],
        )


def _mock_session_ctx() -> MagicMock:
    """Return a mock async context manager yielding a session."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _patch_lookup(db_user, project_names):
    """Patch the sync user/membership lookup the route runs via asyncio.to_thread."""
    return patch(
        "codemie.rest_api.routers.analytics.spending.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=(db_user, project_names),
    )


def _target_user(user_id: str = "target-uuid"):
    target = MagicMock()
    target.id = user_id
    target.username = "target"
    target.email = "target@corp.com"
    return target


def _declared_default(func, param_name: str):
    """Return the default a FastAPI Query() parameter declares.

    These routes are called directly in tests, which bypasses FastAPI's dependency
    resolution, so `page`/`per_page` are always passed explicitly below. This helper
    covers the defaults that only a real HTTP request would apply.
    """
    import inspect

    return inspect.signature(func).parameters[param_name].default.default


class TestPaginationDefaults:
    """The declared pagination defaults are part of the public contract."""

    def test_user_project_spending_defaults(self):
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        assert _declared_default(get_user_project_spending, "page") == 0
        assert _declared_default(get_user_project_spending, "per_page") == config.ANALYTICS_DEFAULT_PAGE_SIZE

    def test_project_member_spending_defaults(self):
        from codemie.rest_api.routers.analytics.spending import get_project_member_spending

        assert _declared_default(get_project_member_spending, "page") == 0
        assert _declared_default(get_project_member_spending, "per_page") == config.ANALYTICS_DEFAULT_PAGE_SIZE


class TestUserProjectSpendingEndpoint:
    """Tests for GET /v1/analytics/user-project-spending."""

    @pytest.mark.asyncio
    async def test_returns_200_with_empty_rows_when_user_has_no_projects(self):
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        with (
            _patch_lookup(_target_user(), set()),
            patch("codemie.clients.postgres.get_async_session", return_value=_mock_session_ctx()),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_user_project_spend",
                new=AsyncMock(return_value=(USER_COLUMNS, [])),
            ),
        ):
            response = await get_user_project_spending(user=_admin_user(), users="nobody@example.com", **PAGE_ARGS)

        assert response["data"]["rows"] == []
        assert response["data"]["columns"][0]["id"] == "project_name"

    @pytest.mark.asyncio
    async def test_includes_pagination_block(self):
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        rows = [{"project_name": "atlas-core", "platform": 0, "cli": 0, "premium_models": 0}]
        with (
            _patch_lookup(_target_user(), {"atlas-core"}),
            patch("codemie.clients.postgres.get_async_session", return_value=_mock_session_ctx()),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_user_project_spend",
                new=AsyncMock(return_value=(USER_COLUMNS, rows)),
            ),
        ):
            response = await get_user_project_spending(user=_admin_user(), users="someone@example.com", **PAGE_ARGS)

        assert response["pagination"]["total_count"] == 1
        assert response["pagination"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_passes_resolved_user_id_and_trimmed_email(self):
        """The service is keyed by the resolved user id, and only the first email is used."""
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        spend = AsyncMock(return_value=(USER_COLUMNS, []))
        with (
            _patch_lookup(_target_user("resolved-id"), {"atlas-core"}),
            patch("codemie.clients.postgres.get_async_session", return_value=_mock_session_ctx()),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_user_project_spend",
                new=spend,
            ),
        ):
            response = await get_user_project_spending(
                user=_admin_user(), users=" someone@example.com , other@example.com ", **PAGE_ARGS
            )

        assert spend.await_args.args[1] == "resolved-id"
        assert response["metadata"]["filters_applied"] == {"users": "someone@example.com"}

    @pytest.mark.asyncio
    async def test_unknown_target_user_is_404(self):
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        with _patch_lookup(None, set()):
            with pytest.raises(ExtendedHTTPException) as exc:
                await get_user_project_spending(user=_admin_user(), users="ghost@example.com", **PAGE_ARGS)

        assert exc.value.code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_project_admin_sharing_a_project_with_the_target_is_allowed(self):
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        with (
            _patch_lookup(_target_user(), {"atlas-core", "other-project"}),
            patch("codemie.clients.postgres.get_async_session", return_value=_mock_session_ctx()),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_user_project_spend",
                new=AsyncMock(return_value=(USER_COLUMNS, [])),
            ),
        ):
            response = await get_user_project_spending(
                user=_project_admin_user(["atlas-core"]), users="someone@example.com", **PAGE_ARGS
            )

        assert response["data"]["rows"] == []

    @pytest.mark.asyncio
    async def test_non_admin_caller_is_forbidden(self):
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        with _patch_lookup(_target_user(), {"other-project"}):
            with pytest.raises(ExtendedHTTPException) as exc:
                await get_user_project_spending(user=_plain_user(), users="other@example.com", **PAGE_ARGS)

        assert exc.value.code == status.HTTP_403_FORBIDDEN


class TestProjectMemberSpendingEndpoint:
    """Tests for GET /v1/analytics/project-member-spending."""

    @pytest.mark.asyncio
    async def test_returns_200_with_empty_rows_for_project_with_no_members(self):
        from codemie.rest_api.routers.analytics.spending import get_project_member_spending

        with (
            patch("codemie.clients.postgres.get_async_session", return_value=_mock_session_ctx()),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_project_member_spend",
                new=AsyncMock(return_value=(MEMBER_COLUMNS, [])),
            ),
        ):
            response = await get_project_member_spending(user=_admin_user(), projects="empty-project", **PAGE_ARGS)

        assert response["data"]["rows"] == []
        assert response["data"]["columns"][0]["id"] == "user_id"

    @pytest.mark.asyncio
    async def test_paginates_rows(self):
        from codemie.rest_api.routers.analytics.spending import get_project_member_spending

        rows = [{"user_id": f"u-{i}", "platform": 0, "cli": 0, "premium_models": 0} for i in range(5)]
        with (
            patch("codemie.clients.postgres.get_async_session", return_value=_mock_session_ctx()),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_project_member_spend",
                new=AsyncMock(return_value=(MEMBER_COLUMNS, rows)),
            ),
        ):
            response = await get_project_member_spending(user=_admin_user(), projects="atlas-core", page=0, per_page=2)

        assert [r["user_id"] for r in response["data"]["rows"]] == ["u-0", "u-1"]
        assert response["pagination"]["total_count"] == 5
        assert response["pagination"]["has_more"] is True

    @pytest.mark.asyncio
    async def test_second_page_returns_the_next_slice(self):
        from codemie.rest_api.routers.analytics.spending import get_project_member_spending

        rows = [{"user_id": f"u-{i}"} for i in range(5)]
        with (
            patch("codemie.clients.postgres.get_async_session", return_value=_mock_session_ctx()),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_project_member_spend",
                new=AsyncMock(return_value=(MEMBER_COLUMNS, rows)),
            ),
        ):
            response = await get_project_member_spending(user=_admin_user(), projects="atlas-core", page=2, per_page=2)

        assert [r["user_id"] for r in response["data"]["rows"]] == ["u-4"]
        assert response["pagination"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_project_admin_of_the_requested_project_is_allowed(self):
        from codemie.rest_api.routers.analytics.spending import get_project_member_spending

        with (
            patch("codemie.clients.postgres.get_async_session", return_value=_mock_session_ctx()),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_project_member_spend",
                new=AsyncMock(return_value=(MEMBER_COLUMNS, [])),
            ),
        ):
            response = await get_project_member_spending(
                user=_project_admin_user(["atlas-core"]), projects="atlas-core", **PAGE_ARGS
            )

        assert response["data"]["rows"] == []

    @pytest.mark.asyncio
    async def test_admin_of_a_different_project_is_forbidden(self):
        from codemie.rest_api.routers.analytics.spending import get_project_member_spending

        with pytest.raises(ExtendedHTTPException) as exc:
            await get_project_member_spending(
                user=_project_admin_user(["other-project"]), projects="atlas-core", **PAGE_ARGS
            )

        assert exc.value.code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_non_admin_caller_is_forbidden(self):
        from codemie.rest_api.routers.analytics.spending import get_project_member_spending

        with pytest.raises(ExtendedHTTPException) as exc:
            await get_project_member_spending(user=_plain_user(), projects="atlas-core", **PAGE_ARGS)

        assert exc.value.code == status.HTTP_403_FORBIDDEN


class TestUserProjectSpendingLookupClosure:
    """Exercise the real _lookup closure instead of patching asyncio.to_thread wholesale.

    The other authorization tests hand _authorize_admin_budget_view its own input, so they
    cannot catch a lookup that resolves the wrong subject (e.g. the caller's projects
    instead of the target's) or returns an empty set unconditionally.
    """

    @pytest.mark.asyncio
    async def test_authorizes_on_the_target_users_projects_not_the_callers(self):
        """A project admin of 'shared' may read a target who is also in 'shared'."""
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        target = MagicMock()
        target.id = "target-1"
        caller = _project_admin_user(["shared"])
        spend = AsyncMock(return_value=(USER_COLUMNS, []))

        with (
            patch(
                "codemie.service.user.user_management_service.UserManagementService.get_user_by_email",
                return_value=target,
            ),
            patch(
                "codemie.repository.user_project_repository.user_project_repository.get_project_names_for_user",
                return_value={"shared", "other"},
            ) as get_projects,
            patch("codemie.clients.postgres.get_session"),
            patch("codemie.rest_api.routers.analytics.spending.get_async_session"),
            patch(
                "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_user_project_spend",
                new=spend,
            ),
        ):
            response = await get_user_project_spending(user=caller, users="target@corp.com", **PAGE_ARGS)

        assert response["data"]["rows"] == []
        # The membership lookup must be keyed on the TARGET, never the caller.
        assert get_projects.call_args[0][1] == "target-1"
        assert spend.await_args.args[1] == "target-1"

    @pytest.mark.asyncio
    async def test_project_admin_denied_when_target_shares_no_admin_project(self):
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        target = MagicMock()
        target.id = "target-1"
        caller = _project_admin_user(["mine"])

        with (
            patch(
                "codemie.service.user.user_management_service.UserManagementService.get_user_by_email",
                return_value=target,
            ),
            patch(
                "codemie.repository.user_project_repository.user_project_repository.get_project_names_for_user",
                return_value={"theirs"},
            ),
            patch("codemie.clients.postgres.get_session"),
        ):
            with pytest.raises(ExtendedHTTPException) as exc:
                await get_user_project_spending(user=caller, users="target@corp.com", **PAGE_ARGS)

        assert exc.value.code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_unknown_user_is_403_not_404_for_non_admins(self):
        """Otherwise the endpoint is an email-keyed oracle for who is a registered user."""
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        caller = _project_admin_user(["mine"])

        with (
            patch(
                "codemie.service.user.user_management_service.UserManagementService.get_user_by_email",
                return_value=None,
            ),
            patch("codemie.clients.postgres.get_session"),
        ):
            with pytest.raises(ExtendedHTTPException) as exc:
                await get_user_project_spending(user=caller, users="ghost@corp.com", **PAGE_ARGS)

        assert exc.value.code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_unknown_user_is_still_404_for_global_admins(self):
        from codemie.rest_api.routers.analytics.spending import get_user_project_spending

        with (
            patch(
                "codemie.service.user.user_management_service.UserManagementService.get_user_by_email",
                return_value=None,
            ),
            patch("codemie.clients.postgres.get_session"),
        ):
            with pytest.raises(ExtendedHTTPException) as exc:
                await get_user_project_spending(user=_admin_user(), users="ghost@corp.com", **PAGE_ARGS)

        assert exc.value.code == status.HTTP_404_NOT_FOUND
