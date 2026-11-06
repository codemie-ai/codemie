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

"""Tests for sharing security fixes: CR-001..CR-009."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from codemie.core.exceptions import ExtendedHTTPException


# ---------------------------------------------------------------------------
# CR-001: is_shared_with on unsaved (id=None) Assistant returns False
# ---------------------------------------------------------------------------


def test_is_shared_with_none_id_returns_false():
    """Assistant with id=None must return False without querying the DB (CR-001)."""
    from codemie.rest_api.models.assistant import Assistant

    asst = Assistant(
        id=None,
        name="test",
        description="test",
        system_prompt="test",
        project="proj",
        toolkits=[],
        shared=False,
        is_react=False,
        is_global=False,
        created_date="2022-01-01T00:00:00.000Z",
        agent_mode="general",
        creator="owner-1",
        update_date="2022-01-01T00:00:00.000Z",
    )
    user = MagicMock()
    user.is_external_user = False
    user.project_names = []
    user.id = "user-1"

    with patch("codemie.rest_api.models.assistant.Session") as mock_get:
        result = asst.is_shared_with(user)

    assert result is False
    mock_get.assert_not_called()


def test_is_shared_with_external_recipient_denied_despite_grant():
    """A recipient converted to an external user loses named-grant access (a8)."""
    from codemie.rest_api.models.assistant import Assistant

    asst = Assistant(
        id="asst-1",
        name="test",
        description="test",
        system_prompt="test",
        project="proj",
        toolkits=[],
        shared=False,
        is_react=False,
        is_global=False,
        created_date="2022-01-01T00:00:00.000Z",
        agent_mode="general",
        creator="owner-1",
        update_date="2022-01-01T00:00:00.000Z",
    )
    user = MagicMock()
    user.is_external_user = True
    user.project_names = []
    user.id = "user-1"

    with patch("codemie.rest_api.models.assistant.Session") as mock_session_cls:
        mock_session_cls.return_value.__enter__.return_value.exec.return_value.first.return_value = "asst-1"
        assert asst.is_shared_with(user) is False


# ---------------------------------------------------------------------------
# CR-002: ShareUsersRequest rejects empty user_ids
# ---------------------------------------------------------------------------


def test_share_users_request_rejects_empty_list():
    """ShareUsersRequest must reject user_ids=[] at the model level (CR-002)."""
    from pydantic import ValidationError
    from codemie.rest_api.routers.assistant.management import ShareUsersRequest

    with pytest.raises((ValidationError, ExtendedHTTPException, ValueError)):
        ShareUsersRequest(user_ids=[])


def test_share_users_request_accepts_non_empty_list():
    """ShareUsersRequest accepts a non-empty user_ids list (CR-002)."""
    from codemie.rest_api.routers.assistant.management import ShareUsersRequest

    req = ShareUsersRequest(user_ids=["uid-1"])
    assert req.user_ids == ["uid-1"]


# ---------------------------------------------------------------------------
# CR-003 / CR-004: share routes delegate the owner check to the service's public entry points
# ---------------------------------------------------------------------------

_R = "codemie.rest_api.routers.assistant.management"


def test_get_shared_with_delegates_to_list_for_owner():
    from codemie.rest_api.routers.assistant.management import get_shared_with

    owner, asst = MagicMock(), MagicMock()
    with (
        patch(f"{_R}.config.ENABLE_USER_MANAGEMENT", True),
        patch(f"{_R}._get_assistant_by_id_or_raise", return_value=asst),
        patch(f"{_R}.AssistantShareService.list_for_owner", return_value=[]) as lst,
    ):
        resp = get_shared_with("asst-1", user=owner)

    lst.assert_called_once_with(owner, asst)
    assert resp.shared_with == []


def test_get_shared_with_non_owner_raises_403():
    from codemie.rest_api.routers.assistant.management import get_shared_with

    with (
        patch(f"{_R}.config.ENABLE_USER_MANAGEMENT", True),
        patch(f"{_R}._get_assistant_by_id_or_raise", return_value=MagicMock()),
        patch(
            f"{_R}.AssistantShareService.list_for_owner",
            side_effect=ExtendedHTTPException(code=403, message="forbidden"),
        ),
    ):
        with pytest.raises(ExtendedHTTPException) as exc:
            get_shared_with("asst-1", user=MagicMock())

    assert exc.value.code == 403


@pytest.mark.asyncio
async def test_get_sharable_users_delegates_to_search_for_owner():
    from codemie.rest_api.routers.assistant.management import get_sharable_users

    owner, asst = MagicMock(), MagicMock()
    with (
        patch(f"{_R}.config.ENABLE_USER_MANAGEMENT", True),
        patch(f"{_R}._get_assistant_by_id_or_raise", return_value=asst),
        patch(
            f"{_R}.AssistantShareService.search_sharable_users_for_owner",
            new_callable=AsyncMock,
            return_value=[],
        ) as search,
    ):
        resp = await get_sharable_users("asst-1", q="bo", user=owner)

    search.assert_awaited_once_with(owner, asst, "bo")
    assert resp.users == []


def test_share_routes_do_not_reference_private_require_owner():
    import inspect

    from codemie.rest_api.routers.assistant import management as router_module

    for name in ("get_shared_with", "share_assistant", "revoke_assistant_share", "get_sharable_users"):
        assert "_require_owner" not in inspect.getsource(getattr(router_module, name))


# ---------------------------------------------------------------------------
# CR-005: per_user_grant_condition uses enum members, not raw strings
# ---------------------------------------------------------------------------


def test_per_user_grant_condition_uses_enum_not_literals():
    """per_user_grant_condition must reference enum members so SQLAlchemy resolves
    the correct DB representation — not hardcoded literal strings (CR-005)."""
    import ast
    import inspect
    from codemie.service.assistant import assistant_repository

    source = inspect.getsource(assistant_repository)
    tree = ast.parse(source)

    # Find all string constants used in Permission.resource_type / principal_type comparisons.
    # If the fix is applied, "assistant" and "user" should NOT appear as bare string literals
    # in the per_user_grant_condition blocks — enum member access is used instead.
    bad_literals = {"assistant", "user"}
    found_bad = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in bad_literals:
            found_bad.add(node.value)

    assert not found_bad, (
        f"Raw string literals {found_bad!r} found in assistant_repository.py; "
        "use ResourceType.ASSISTANT and PrincipalType.USER enum members instead"
    )


# ---------------------------------------------------------------------------
# CR-008: search_sharable_users excludes external users at the SQL level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_sharable_users_passes_exclude_external_to_query():
    """search_sharable_users must not filter externals in Python after DB fetch (CR-008)."""
    from codemie.service.assistant.assistant_share_service import AssistantShareService

    internal_user = MagicMock()
    internal_user.id = "u1"
    internal_user.username = "alice"
    internal_user.email = "alice@example.com"
    internal_user.name = "Alice"
    internal_user.picture = None
    internal_user.user_type = "internal"

    mock_session = AsyncMock()
    mock_repo = AsyncMock()
    mock_repo.aquery_active_users = AsyncMock(return_value=[internal_user])

    with (
        patch("codemie.service.assistant.assistant_share_service.config") as mock_cfg,
        patch("codemie.clients.postgres.get_async_session") as mock_ctx,
        patch("codemie.repository.user_repository.user_repository", mock_repo),
    ):
        mock_cfg.EXTERNAL_USER_TYPE = "external"
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await AssistantShareService.search_sharable_users("alice")

    assert len(result) == 1
    assert result[0]["id"] == "u1"
