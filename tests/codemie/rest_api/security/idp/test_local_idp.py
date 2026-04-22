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

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import Request
from codemie.rest_api.security.idp.local import LocalIdp, USER_ID_HEADER
from codemie.rest_api.security.user import User


def _make_request(user_id: str) -> Request:
    headers = {USER_ID_HEADER: user_id}
    return Request(
        scope={"type": "http", "method": "GET", "headers": [[k.encode(), v.encode()] for k, v in headers.items()]}
    )


@pytest.fixture
def local_idp():
    return LocalIdp()


def test_get_session_cookie(local_idp):
    """Test that get_session_cookie returns empty string"""
    assert local_idp.get_session_cookie() == ""


@pytest.mark.asyncio
async def test_authenticate_with_header():
    """Test authentication with user-id header"""
    idp = LocalIdp()
    test_user_id = "test_user"

    user = await idp.authenticate(_make_request(test_user_id))
    assert isinstance(user, User)
    assert user.id == test_user_id
    assert user.username == test_user_id
    assert user.name == test_user_id


@pytest.mark.asyncio
async def test_authenticate_with_explicit_user_id():
    """Test authentication with explicitly provided user_id"""
    idp = LocalIdp()
    test_user_id = "explicit_user"

    user = await idp.authenticate(_make_request(test_user_id))
    assert isinstance(user, User)
    assert user.id == test_user_id
    assert user.username == test_user_id
    assert user.name == test_user_id


@pytest.mark.asyncio
async def test_authenticate_db_success_with_user_management_enabled(mocker):
    """Test authentication with ENABLE_USER_MANAGEMENT=True preserves DB is_admin."""
    mocker.patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", True)

    idp = LocalIdp()
    test_user_id = "db_user"

    mock_project = MagicMock(project_name="proj-1", is_project_admin=True)
    mock_kb = MagicMock(kb_name="kb-1")
    mock_db_user = MagicMock()
    mock_db_user.id = test_user_id
    mock_db_user.username = "db_username"
    mock_db_user.name = "DB Name"
    mock_db_user.email = "db@example.com"
    mock_db_user.picture = "http://pic"
    mock_db_user.user_type = None
    mock_db_user.is_admin = True
    mock_db_user.is_maintainer = False
    mock_db_user.project_limit = 10

    class _AsyncSessionCtx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *_):
            pass

    mocker.patch("codemie.clients.postgres.get_async_session", return_value=_AsyncSessionCtx())
    mocker.patch(
        "codemie.repository.user_repository.user_repository.aget_active_by_id",
        new=AsyncMock(return_value=mock_db_user),
    )
    mocker.patch(
        "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
        new=AsyncMock(return_value=[mock_project]),
    )
    mocker.patch(
        "codemie.repository.user_kb_repository.user_kb_repository.aget_by_user_id",
        new=AsyncMock(return_value=[mock_kb]),
    )

    user = await idp.authenticate(_make_request(test_user_id))

    assert user.id == test_user_id
    assert user.username == "db_username"
    assert user.name == "DB Name"
    assert user.email == "db@example.com"
    assert user.is_admin is True
    assert user.project_names == ["proj-1"]
    assert user.admin_project_names == ["proj-1"]
    assert user.knowledge_bases == ["kb-1"]


@pytest.mark.asyncio
async def test_authenticate_db_success_with_user_management_disabled(mocker):
    """Test authentication with ENABLE_USER_MANAGEMENT=False uses role-based is_admin."""
    mocker.patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", False)
    mocker.patch("codemie.rest_api.security.user.config.ADMIN_ROLE_NAME", "admin")
    mocker.patch("codemie.rest_api.security.user.config.ENV", "dev")

    idp = LocalIdp()
    test_user_id = "db_user"

    mock_project = MagicMock(project_name="proj-1", is_project_admin=True)
    mock_kb = MagicMock(kb_name="kb-1")
    mock_db_user = MagicMock()
    mock_db_user.id = test_user_id
    mock_db_user.username = "db_username"
    mock_db_user.name = "DB Name"
    mock_db_user.email = "db@example.com"
    mock_db_user.picture = "http://pic"
    mock_db_user.user_type = None
    mock_db_user.is_admin = False  # DB says False
    mock_db_user.is_maintainer = False
    mock_db_user.project_limit = 10

    class _AsyncSessionCtx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *_):
            pass

    mocker.patch("codemie.clients.postgres.get_async_session", return_value=_AsyncSessionCtx())
    mocker.patch(
        "codemie.repository.user_repository.user_repository.aget_active_by_id",
        new=AsyncMock(return_value=mock_db_user),
    )
    mocker.patch(
        "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
        new=AsyncMock(return_value=[mock_project]),
    )
    mocker.patch(
        "codemie.repository.user_kb_repository.user_kb_repository.aget_by_user_id",
        new=AsyncMock(return_value=[mock_kb]),
    )

    # Test with admin role in roles
    user = await idp.authenticate(_make_request(test_user_id))
    # LocalIdp doesn't populate roles, so is_admin should stay False
    assert user.is_admin is False

    # Now test by setting ADMIN_USER_ID
    mocker.patch("codemie.rest_api.security.user.config.ADMIN_USER_ID", test_user_id)
    user = await idp.authenticate(_make_request(test_user_id))
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_authenticate_db_failure_fallback(mocker):
    """Test authentication falls back to stub user when DB is unavailable."""
    idp = LocalIdp()
    test_user_id = "some_user"

    mocker.patch("codemie.clients.postgres.get_async_session", side_effect=Exception("DB unavailable"))

    user = await idp.authenticate(_make_request(test_user_id))

    assert isinstance(user, User)
    assert user.id == test_user_id
    assert user.username == test_user_id
    assert user.name == test_user_id
