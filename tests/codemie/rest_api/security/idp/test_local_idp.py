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
    mocker.patch("codemie.rest_api.security.idp.local.config.ENV", "dev")

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
    mock_db_user.is_auditor = False
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
async def test_authenticate_db_success_populates_default_project(mocker):
    """default_project is derived from the UserProject row with is_default=True."""
    mocker.patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", True)
    mocker.patch("codemie.rest_api.security.idp.local.config.ENV", "not-local")

    idp = LocalIdp()
    test_user_id = "db_user"

    mock_project = MagicMock(project_name="proj-1", is_project_admin=True, is_default=True)
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
    mock_db_user.is_auditor = False
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

    assert user.default_project == "proj-1"


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
    mock_db_user.is_auditor = False
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


class _EmptyAsyncSession:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *_):
        pass


def _patch_owner_lookup(mocker, *, active, existing):
    mocker.patch("codemie.rest_api.security.idp.local.config.ENV", "dev")
    mocker.patch("codemie.clients.postgres.get_async_session", return_value=_EmptyAsyncSession())
    mocker.patch(
        "codemie.repository.user_repository.user_repository.aget_active_by_id",
        new=AsyncMock(return_value=active),
    )
    mocker.patch(
        "codemie.repository.user_repository.user_repository.aget_by_id",
        new=AsyncMock(return_value=existing),
    )


@pytest.mark.asyncio
async def test_authenticate_refuses_deactivated_user(mocker):
    """A deactivated user gets 401, also on the signed trigger callback path (EPMCDME-13928)."""
    from codemie.core.exceptions import ExtendedHTTPException

    _patch_owner_lookup(mocker, active=None, existing=MagicMock())

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await LocalIdp().authenticate(_make_request("deactivated_user"))

    assert exc_info.value.code == 401
    assert exc_info.value.details == "User is not active"


@pytest.mark.asyncio
async def test_authenticate_keeps_stub_for_user_missing_from_db(mocker):
    """A user that is not in the database still gets the stub user, as before."""
    _patch_owner_lookup(mocker, active=None, existing=None)

    user = await LocalIdp().authenticate(_make_request("legacy_user"))

    assert user.id == "legacy_user"
    assert user.username == "legacy_user"
    # AR-3: the stand-in has no memberships, so nothing is billed to the model's "demo" default
    assert user.project_names == []


@pytest.mark.asyncio
async def test_internal_call_refuses_user_missing_from_db(mocker):
    """Signed internal callbacks get no stand-in outside ENV local (EPMCDME-13928)."""
    from codemie.core.exceptions import ExtendedHTTPException

    _patch_owner_lookup(mocker, active=None, existing=None)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await LocalIdp().authenticate(_make_request("legacy_user"), allow_stand_in=False)

    assert exc_info.value.code == 401
    assert exc_info.value.details == "User is not active"


@pytest.mark.asyncio
async def test_internal_call_refuses_when_lookup_fails(mocker):
    from codemie.core.exceptions import ExtendedHTTPException

    mocker.patch("codemie.rest_api.security.idp.local.config.ENV", "dev")
    mocker.patch("codemie.clients.postgres.get_async_session", side_effect=RuntimeError("db down"))

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await LocalIdp().authenticate(_make_request("some_user"), allow_stand_in=False)

    assert exc_info.value.code == 401


@pytest.mark.asyncio
async def test_internal_call_keeps_stand_in_on_local(mocker):
    mocker.patch("codemie.rest_api.security.idp.local.config.ENV", "local")

    user = await LocalIdp().authenticate(_make_request("local_user"), allow_stand_in=False)

    assert user.id == "local_user"


def _session_without_db_user(mocker, snapshot):
    class _AsyncSessionCtx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *_):
            pass

    mocker.patch("codemie.rest_api.security.idp.local.config.ENV", "dev")
    mocker.patch("codemie.clients.postgres.get_async_session", return_value=_AsyncSessionCtx())
    mocker.patch(
        "codemie.repository.user_repository.user_repository.aget_active_by_id", new=AsyncMock(return_value=None)
    )
    mocker.patch("codemie.repository.user_repository.user_repository.aget_by_id", new=AsyncMock(return_value=None))
    return mocker.patch(
        "codemie.repository.idp_user_snapshot_repository.idp_user_snapshot_repository.aget",
        new=AsyncMock(return_value=snapshot),
    )


def _snapshot(hours_ago: float = 1):
    from datetime import UTC, datetime, timedelta

    from codemie.rest_api.models.user_management import IdpUserSnapshot

    return IdpUserSnapshot(
        update_date=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours_ago),
        id="owner-1",
        username="carol",
        email="carol@example.com",
        name="Carol",
        project_names=["zeta", "carol@example.com"],
        admin_project_names=["zeta"],
    )


@pytest.mark.asyncio
async def test_idp_claims_loopback_user_comes_from_the_last_login(mocker):
    """A trigger's signed loopback call in IDP-claims mode keeps the owner's memberships (EPMCDME-16625 D7), but
    never their project-admin rights (AR-5)."""
    mocker.patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", False)
    _session_without_db_user(mocker, _snapshot())

    user = await LocalIdp().authenticate(_make_request("owner-1"), signed_loopback=True)

    assert user.username == "carol"
    assert user.email == "carol@example.com"
    assert user.project_names == ["zeta", "carol@example.com"]
    assert user.admin_project_names == []
    assert not user.is_application_admin("zeta")
    assert user.auth_token


@pytest.mark.asyncio
async def test_header_only_request_never_reads_the_snapshot(mocker):
    """AR-5: a bare user-id header is not authenticated, so it must not receive the named person's claims."""
    mocker.patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", False)
    lookup = _session_without_db_user(mocker, _snapshot())

    user = await LocalIdp().authenticate(_make_request("owner-1"))

    lookup.assert_not_awaited()
    assert (user.id, user.username) == ("owner-1", "owner-1")
    assert user.project_names == []
    assert user.admin_project_names == []


@pytest.mark.asyncio
async def test_missing_user_stays_a_stub_with_user_management(mocker):
    mocker.patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", True)
    lookup = _session_without_db_user(mocker, _snapshot())

    user = await LocalIdp().authenticate(_make_request("owner-1"))

    lookup.assert_not_awaited()
    assert user.username == "owner-1"
    assert "zeta" not in user.project_names


@pytest.mark.asyncio
async def test_an_old_snapshot_no_longer_grants_the_owner_claims(mocker):
    """Access the owner may have lost must not survive in background runs (bounded by the max age); their identity
    does, so the run stays charged to their own personal budget (final review AR-1)."""
    mocker.patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", False)
    mocker.patch("codemie.rest_api.security.user.config.IDP_USER_SNAPSHOT_MAX_AGE_HOURS", 24)
    mocker.patch("codemie.rest_api.security.user.config.IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS", 30)
    _session_without_db_user(mocker, _snapshot(hours_ago=25))

    user = await LocalIdp().authenticate(_make_request("owner-1"), signed_loopback=True)

    assert user.username == "carol"
    assert user.project_names == []
    assert user.admin_project_names == []


@pytest.mark.asyncio
async def test_a_snapshot_past_the_identity_retention_gives_a_bare_id(mocker):
    mocker.patch("codemie.rest_api.security.user.config.ENABLE_USER_MANAGEMENT", False)
    mocker.patch("codemie.rest_api.security.user.config.IDP_USER_SNAPSHOT_MAX_AGE_HOURS", 24)
    mocker.patch("codemie.rest_api.security.user.config.IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS", 30)
    _session_without_db_user(mocker, _snapshot(hours_ago=24 * 31))

    user = await LocalIdp().authenticate(_make_request("owner-1"), signed_loopback=True)

    assert user.username == "owner-1"
    assert "zeta" not in user.project_names
