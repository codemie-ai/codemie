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

import time
import uuid
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import (
    authenticate,
    admin_access_only,
    admin_or_maintainer_access_only,
    get_bind_key,
    sign_internal_request,
    _verify_internal_request,
    maintainer_access_only,
    application_access_check,
    kb_access_check,
    project_admin_or_admin_user_detail_access,
)
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_context import (
    clear_current_auth_token,
    clear_current_client_access_token,
    get_current_auth_token,
    get_current_client_access_token,
)
from codemie.rest_api.security.idp.local import LocalIdp
from codemie.configs import config


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.skip(reason="Test needs refactoring for new authentication architecture")
@pytest.mark.anyio
async def test_authenticate_by_user_id(mocker):
    request = mocker.MagicMock()

    with patch('codemie.rest_api.security.authentication.get_idp_provider') as mock_get_idp:
        mock_get_idp.return_value = LocalIdp()
        user = await authenticate(request, user_id='1', keycloak_auth_header=None, oidc_auth_header=None)
        assert user.id == '1'


@pytest.mark.skip(reason="Test needs refactoring for new authentication architecture")
@pytest.mark.anyio
async def test_authenticate_no_auth(mocker):
    request = mocker.MagicMock()

    with pytest.raises(ExtendedHTTPException):
        await authenticate(request, user_id=None, keycloak_auth_header=None, oidc_auth_header=None)


async def _authenticate_with_user(mocker, user: User) -> User:
    request = mocker.MagicMock()
    provider = MagicMock()
    provider.authenticate_and_load_user = AsyncMock(return_value=user)

    with (
        patch("codemie.rest_api.security.authentication.get_user_provider", return_value=provider),
        patch("codemie.rest_api.security.authentication.IdpFactory"),
    ):
        return await authenticate(request, internal_user_id=None, bind_key=None)


@pytest.mark.anyio
async def test_authenticate_sets_client_access_token_in_context(mocker):
    """BFF flow: user without auth_token but with client_access_token populates only the client var."""
    clear_current_auth_token()
    clear_current_client_access_token()
    user = User(id="u1", username="u1", client_access_token="client-token")

    result = await _authenticate_with_user(mocker, user)

    assert result is user
    assert get_current_auth_token() is None
    assert get_current_client_access_token() == "client-token"

    clear_current_client_access_token()


@pytest.mark.anyio
async def test_authenticate_sets_both_tokens_in_context(mocker):
    clear_current_auth_token()
    clear_current_client_access_token()
    user = User(id="u1", username="u1", auth_token="user-token", client_access_token="client-token")

    await _authenticate_with_user(mocker, user)

    assert get_current_auth_token() == "user-token"
    assert get_current_client_access_token() == "client-token"

    clear_current_auth_token()
    clear_current_client_access_token()


@pytest.mark.anyio
async def test_admin_access_only_success(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', roles=['admin'])

    result = await admin_access_only(request)
    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_admin_access_only_allows_maintainer(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', roles=[], is_admin=False, is_maintainer=True)

    result = await admin_access_only(request)
    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_admin_access_only_failure(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', roles=[], is_admin=False)

    with pytest.raises(ExtendedHTTPException):
        await admin_access_only(request)


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_maintainer_access_only_failure_for_regular_admin(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', roles=[], is_admin=True, is_maintainer=False)

    with pytest.raises(ExtendedHTTPException):
        await maintainer_access_only(request)


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_maintainer_access_only_success(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', roles=[], is_admin=True, is_maintainer=True)

    result = await maintainer_access_only(request)
    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_admin_or_maintainer_access_only_success_for_maintainer(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', roles=[], is_admin=False, is_maintainer=True)

    result = await admin_or_maintainer_access_only(request)

    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_admin_or_maintainer_access_only_failure_for_regular_user(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', roles=[], is_admin=False, is_maintainer=False)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await admin_or_maintainer_access_only(request)

    assert exc_info.value.code == 403


def test_get_bind_key_returns_non_empty_string():
    bind_key = get_bind_key()

    assert isinstance(bind_key, str)
    assert bind_key


def test_sign_internal_request_returns_all_required_headers():
    result = sign_internal_request("user-123")

    assert "X-Bind-Key" in result
    assert "X-Bind-Nonce" in result
    assert "X-Bind-Timestamp" in result
    assert "user-id" in result
    assert result["user-id"] == "user-123"


def test_sign_internal_request_nonce_is_uuid():
    result = sign_internal_request("user-123")

    uuid.UUID(result["X-Bind-Nonce"])  # raises if not valid UUID


def test_sign_internal_request_timestamp_within_one_second():
    before = int(time.time())
    result = sign_internal_request("user-123")
    after = int(time.time())

    ts = int(result["X-Bind-Timestamp"])
    assert before <= ts <= after + 1


def test_verify_internal_request_valid_signature():
    headers = sign_internal_request("user-abc")

    _verify_internal_request(
        sig=headers["X-Bind-Key"],
        nonce=headers["X-Bind-Nonce"],
        timestamp=headers["X-Bind-Timestamp"],
        user_id="user-abc",
    )


def test_verify_internal_request_expired_timestamp():
    headers = sign_internal_request("user-abc")
    old_ts = str(int(time.time()) - 60)

    with pytest.raises(ExtendedHTTPException) as exc:
        _verify_internal_request(
            sig=headers["X-Bind-Key"],
            nonce=headers["X-Bind-Nonce"],
            timestamp=old_ts,
            user_id="user-abc",
        )
    assert exc.value.code == 401


def test_verify_internal_request_tampered_user_id():
    headers = sign_internal_request("user-abc")

    with pytest.raises(ExtendedHTTPException) as exc:
        _verify_internal_request(
            sig=headers["X-Bind-Key"],
            nonce=headers["X-Bind-Nonce"],
            timestamp=headers["X-Bind-Timestamp"],
            user_id="user-ATTACKER",
        )
    assert exc.value.code == 401


def test_verify_internal_request_invalid_timestamp_string():
    headers = sign_internal_request("user-abc")

    with pytest.raises(ExtendedHTTPException) as exc:
        _verify_internal_request(
            sig=headers["X-Bind-Key"],
            nonce=headers["X-Bind-Nonce"],
            timestamp="not-a-number",
            user_id="user-abc",
        )
    assert exc.value.code == 401


@pytest.mark.anyio
@patch.object(config, 'ENV', 'local')
async def test_authenticate_with_valid_hmac_signature(mocker):
    user_id = "test-user-hmac"
    headers = sign_internal_request(user_id)

    header_map = {
        "X-Bind-Key": headers["X-Bind-Key"],
        "X-Bind-Nonce": headers["X-Bind-Nonce"],
        "X-Bind-Timestamp": headers["X-Bind-Timestamp"],
        "user-id": user_id,
    }
    mock_request = MagicMock()
    mock_request.headers.get = lambda key, default="": header_map.get(key, default)
    mock_request.state = MagicMock()

    mock_user = User(id=user_id, username=user_id, name=user_id)
    with patch.object(LocalIdp, 'authenticate', new_callable=AsyncMock, return_value=mock_user):
        user = await authenticate(mock_request, internal_user_id=user_id, bind_key=headers["X-Bind-Key"])

    assert user.id == user_id


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_user_detail_access_maintainer():
    request = MagicMock()
    request.state.user = User(id="maintainer-1", username="maintainer", is_admin=False, is_maintainer=True, roles=[])
    request.path_params = {"user_id": "target-user-123"}

    result = await project_admin_or_admin_user_detail_access(request)

    assert result is None


def test_application_access_check_success(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', project_names=['app1'])

    result = application_access_check(request, 'app1')
    assert result is None


@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
def test_application_access_check_failure(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', project_names=['app1'], is_admin=False)

    with pytest.raises(ExtendedHTTPException):
        application_access_check(request, 'app2')


def test_kb_access_check_success(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', knowledge_bases=['kb1'])

    result = kb_access_check(request, 'kb1')
    assert result is None


@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
def test_kb_access_check_failure(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', knowledge_bases=['kb1'], is_admin=False)

    with pytest.raises(ExtendedHTTPException):
        kb_access_check(request, 'kb2')


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_user_detail_access_admin():
    """Test that admins can access any user detail endpoint (Story 18)"""
    # Arrange
    request = MagicMock()
    request.state.user = User(id="admin-1", username="admin", is_admin=True, roles=["admin"])
    request.path_params = {"user_id": "target-user-123"}

    # Act
    result = await project_admin_or_admin_user_detail_access(request)

    # Assert
    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_user_detail_access_auditor():
    """EPMCDME-10930 spec 5.2: pure auditor gets access to GET /v1/admin/users/{id}
    via the auditor fast-path in project_admin_or_admin_user_detail_access.
    """
    request = MagicMock()
    request.state.user = User(
        id="auditor-1", username="auditor", is_admin=False, is_maintainer=False, is_auditor=True, roles=[]
    )
    request.path_params = {"user_id": "target-user-123"}

    result = await project_admin_or_admin_user_detail_access(request)

    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
@patch('codemie.repository.user_repository.user_repository')
async def test_user_detail_access_auditor_skips_db_query(mock_user_repo):
    """EPMCDME-10930 spec 3.2: the auditor fast-path must be checked before the
    is_applications_admin branch, so an auditor never triggers the project-admin
    DB lookup (user_repository.get_by_id / can_project_admin_view_user).
    """
    request = MagicMock()
    request.state.user = User(
        id="auditor-1", username="auditor", is_admin=False, is_maintainer=False, is_auditor=True, roles=[]
    )
    request.path_params = {"user_id": "target-user-123"}

    result = await project_admin_or_admin_user_detail_access(request)

    assert result is None
    mock_user_repo.get_by_id.assert_not_called()


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
@patch('codemie.clients.postgres.get_session')
@patch('codemie.repository.user_repository.user_repository')
@patch('codemie.repository.user_project_repository.user_project_repository')
async def test_user_detail_access_project_admin_can_view(
    mock_user_project_repo,
    mock_user_repo,
    mock_get_session,
):
    """Test that project admin can view user details when they share a project (Story 18)"""
    # Arrange
    request = MagicMock()
    request.state.user = User(
        id="proj-admin-1",
        username="project_admin",
        is_admin=False,
        admin_project_names=["shared-project"],
    )
    request.path_params = {"user_id": "target-user-123"}

    # Mock session context manager
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Mock target user exists and project admin can view
    target_user_mock = MagicMock(id="target-user-123")
    mock_user_repo.get_by_id.return_value = target_user_mock
    mock_user_project_repo.can_project_admin_view_user.return_value = True

    # Act
    result = await project_admin_or_admin_user_detail_access(request)

    # Assert
    assert result is None
    mock_user_repo.get_by_id.assert_called_once_with(mock_session, "target-user-123")
    mock_user_project_repo.can_project_admin_view_user.assert_called_once_with(
        mock_session, "proj-admin-1", "target-user-123"
    )


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
@patch('codemie.clients.postgres.get_session')
@patch('codemie.repository.user_repository.user_repository')
@patch('codemie.repository.user_project_repository.user_project_repository')
async def test_user_detail_access_project_admin_cannot_view(
    mock_user_project_repo,
    mock_user_repo,
    mock_get_session,
):
    """Test that project admin cannot view user outside their projects (Story 18)"""
    # Arrange
    request = MagicMock()
    request.state.user = User(
        id="proj-admin-1",
        username="project_admin",
        is_admin=False,
        admin_project_names=["project-a"],
    )
    request.path_params = {"user_id": "other-user-456"}

    # Mock session context manager
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Mock target user exists but project admin cannot view (not in shared projects)
    target_user_mock = MagicMock(id="other-user-456")
    mock_user_repo.get_by_id.return_value = target_user_mock
    mock_user_project_repo.can_project_admin_view_user.return_value = False

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        await project_admin_or_admin_user_detail_access(request)

    assert exc_info.value.code == 403
    assert "User not found in your projects" in exc_info.value.message
    assert "only view details for users who are members of projects you administer" in exc_info.value.details


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_user_detail_access_regular_user_denied():
    """Test that regular users are denied access to user detail endpoint (Story 18)"""
    # Arrange
    request = MagicMock()
    request.state.user = User(
        id="user-1",
        username="regular_user",
        is_admin=False,
        admin_project_names=[],
        project_names=["project1"],
    )
    request.path_params = {"user_id": "target-user-123"}

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        await project_admin_or_admin_user_detail_access(request)

    assert exc_info.value.code == 403
    assert "Access denied" in exc_info.value.message
    assert "administrator or project administrator privileges" in exc_info.value.details


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_user_detail_access_project_admin_missing_user_id():
    """Test that project admin gets 403 when user_id is missing from path params (Story 18)"""
    # Arrange
    request = MagicMock()
    request.state.user = User(
        id="proj-admin-1",
        username="project_admin",
        is_admin=False,
        admin_project_names=["project1"],
    )
    request.path_params = {}  # Missing user_id

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        await project_admin_or_admin_user_detail_access(request)

    assert exc_info.value.code == 403
    assert "Access denied" in exc_info.value.message


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
@patch('codemie.clients.postgres.get_session')
@patch('codemie.repository.user_repository.user_repository')
async def test_user_detail_access_project_admin_user_not_exists(
    mock_user_repo,
    mock_get_session,
):
    """Test that project admin can proceed when target user doesn't exist (404 from service) (Story 18)"""
    # Arrange
    request = MagicMock()
    request.state.user = User(
        id="proj-admin-1",
        username="project_admin",
        is_admin=False,
        admin_project_names=["project1"],
    )
    request.path_params = {"user_id": "nonexistent-user"}

    # Mock session context manager
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Mock target user does not exist
    mock_user_repo.get_by_id.return_value = None

    # Act
    result = await project_admin_or_admin_user_detail_access(request)

    # Assert - should return None to let service layer handle 404
    assert result is None
    mock_user_repo.get_by_id.assert_called_once_with(mock_session, "nonexistent-user")


class TestAuthenticateTeamsSenderBranch:
    async def _authenticate_as(self, mocker, caller: User, headers: dict):
        request = mocker.MagicMock()
        request.headers = headers
        provider = MagicMock()
        provider.authenticate_and_load_user = AsyncMock(return_value=caller)
        with (
            patch("codemie.rest_api.security.authentication.get_user_provider", return_value=provider),
            patch("codemie.rest_api.security.authentication.IdpFactory"),
        ):
            return await authenticate(request, internal_user_id=None, bind_key=None)

    @pytest.mark.anyio
    @patch("codemie.rest_api.security.teams_authentication_resolver.impersonate_teams_bot_request")
    @patch("codemie.rest_api.security.teams_authentication_resolver.is_teams_bot_request", return_value=True)
    async def test_teams_header_swaps_resolved_user(self, mock_is_teams_bot, mock_impersonate, mocker):
        caller = User(id="codemie-teams-bot", username="bot", user_type="service_account", project_names=[])
        resolved = User(id="end-user-1", username="enduser", project_names=[])
        mock_impersonate.return_value = resolved

        result = await self._authenticate_as(mocker, caller, {"X-Teams-Sender-Email": "enduser@example.com"})

        assert result is resolved
        mock_impersonate.assert_awaited_once()

    @pytest.mark.anyio
    @patch("codemie.rest_api.security.teams_authentication_resolver.impersonate_teams_bot_request")
    @patch("codemie.rest_api.security.teams_authentication_resolver.is_teams_bot_request", return_value=False)
    async def test_no_header_returns_caller_unchanged(self, mock_is_teams_bot, mock_impersonate, mocker):
        caller = User(id="u1", username="regular_user", project_names=[])

        result = await self._authenticate_as(mocker, caller, {})

        assert result is caller
        mock_impersonate.assert_not_called()

    @pytest.mark.anyio
    @patch("codemie.rest_api.security.teams_authentication_resolver.impersonate_teams_bot_request")
    @patch("codemie.rest_api.security.teams_authentication_resolver.is_teams_bot_request", return_value=True)
    async def test_impersonate_teams_bot_request_401_propagates(self, mock_is_teams_bot, mock_impersonate, mocker):
        caller = User(id="codemie-teams-bot", username="bot", user_type="service_account", project_names=[])
        mock_impersonate.side_effect = ExtendedHTTPException(code=401, message="Authentication failed")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await self._authenticate_as(mocker, caller, {"X-Teams-Sender-Email": "unknown@example.com"})

        assert exc_info.value.code == 401

    @pytest.mark.anyio
    @patch.object(config, 'ENV', 'local')
    @patch("codemie.rest_api.security.teams_authentication_resolver.impersonate_teams_bot_request")
    @patch("codemie.rest_api.security.teams_authentication_resolver.is_teams_bot_request")
    async def test_internal_bind_key_branch_never_calls_teams_sender_swap(
        self, mock_is_teams_bot, mock_impersonate, mocker
    ):
        """CR-001: the HMAC-signed internal bind-key branch must never fall through into
        the unsigned Teams-header swap, even when internal_user_id happens to match the
        allow-listed Teams service account id and the header is present on the request.
        """
        user_id = "codemie-teams-bot"
        headers = sign_internal_request(user_id)

        header_map = {
            "X-Bind-Key": headers["X-Bind-Key"],
            "X-Bind-Nonce": headers["X-Bind-Nonce"],
            "X-Bind-Timestamp": headers["X-Bind-Timestamp"],
            "user-id": user_id,
            "X-Teams-Sender-Email": "enduser@example.com",
        }
        mock_request = MagicMock()
        mock_request.headers.get = lambda key, default="": header_map.get(key, default)
        mock_request.state = MagicMock()

        mock_user = User(id=user_id, username=user_id, name=user_id, user_type="service_account")
        with patch.object(LocalIdp, 'authenticate', new_callable=AsyncMock, return_value=mock_user):
            user = await authenticate(mock_request, internal_user_id=user_id, bind_key=headers["X-Bind-Key"])

        assert user.id == user_id
        mock_is_teams_bot.assert_not_called()
        mock_impersonate.assert_not_called()


class TestTeamsSenderSwapReachesNonChatRouter:
    """CR-002: the Teams-header swap moved from a dependency wired into 3
    assistant-chat routes into the shared authenticate(), so it now runs for
    every one of the ~50 routers depending on authenticate(). These tests wire
    authenticate() into a router that is not one of those 3 chat routes and
    drive it end-to-end through FastAPI's real dependency injection (not a
    direct function call), to confirm the swap reaches that wider surface too.
    """

    @staticmethod
    def _build_app():
        from fastapi import FastAPI

        app = FastAPI()

        @app.middleware("http")
        async def _set_request_uuid(request, call_next):
            request.state.uuid = "test-request-uuid"
            return await call_next(request)

        non_chat_router = APIRouter(prefix="/v1/reports")

        @non_chat_router.get("/whoami")
        async def whoami(user: User = Depends(authenticate)):
            return {"id": user.id, "email": user.email}

        app.include_router(non_chat_router)
        return app

    @patch("codemie.rest_api.security.teams_authentication_resolver.authentication_service")
    @patch("codemie.rest_api.security.teams_authentication_resolver.config")
    @patch("codemie.rest_api.security.teams_authentication_resolver.customer_config")
    def test_service_account_caller_is_swapped_on_non_chat_router(
        self, mock_customer_config, mock_config, mock_auth_service
    ):
        mock_customer_config.is_feature_enabled.return_value = True
        mock_config.TEAMS_SERVICE_ACCOUNT_ID = "codemie-teams-bot"
        caller = User(id="codemie-teams-bot", username="bot", user_type="service_account", project_names=[])
        resolved = User(id="end-user-1", username="enduser", email="enduser@example.com", project_names=[])
        mock_auth_service.authenticate_teams_sender = AsyncMock(return_value=resolved)

        provider = MagicMock()
        provider.authenticate_and_load_user = AsyncMock(return_value=caller)
        with (
            patch("codemie.rest_api.security.authentication.get_user_provider", return_value=provider),
            patch("codemie.rest_api.security.authentication.IdpFactory"),
        ):
            client = TestClient(self._build_app())
            response = client.get("/v1/reports/whoami", headers={"X-Teams-Sender-Email": "enduser@example.com"})

        assert response.status_code == 200
        assert response.json()["id"] == "end-user-1"
        mock_auth_service.authenticate_teams_sender.assert_awaited_once_with("enduser@example.com")

    @patch("codemie.rest_api.security.teams_authentication_resolver.customer_config")
    def test_non_service_account_caller_is_unaffected_on_non_chat_router(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = True
        caller = User(id="regular-user-1", username="someone", email="someone@example.com", project_names=[])

        provider = MagicMock()
        provider.authenticate_and_load_user = AsyncMock(return_value=caller)
        with (
            patch("codemie.rest_api.security.authentication.get_user_provider", return_value=provider),
            patch("codemie.rest_api.security.authentication.IdpFactory"),
        ):
            client = TestClient(self._build_app())
            response = client.get("/v1/reports/whoami", headers={"X-Teams-Sender-Email": "enduser@example.com"})

        assert response.status_code == 200
        assert response.json()["id"] == "regular-user-1"
