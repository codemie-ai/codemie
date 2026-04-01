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

import pytest
from unittest.mock import patch, MagicMock

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import (
    authenticate,
    admin_access_only,
    application_access_check,
    kb_access_check,
    project_admin_or_super_admin_user_detail_access,
)
from codemie.rest_api.security.user import User
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


@pytest.mark.anyio
async def test_admin_access_only_success(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='test', roles=['admin'])

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
async def test_user_detail_access_super_admin():
    """Test that super admins can access any user detail endpoint (Story 18)"""
    # Arrange
    request = MagicMock()
    request.state.user = User(id="admin-1", username="admin", is_admin=True, roles=["admin"])
    request.path_params = {"user_id": "target-user-123"}

    # Act
    result = await project_admin_or_super_admin_user_detail_access(request)

    # Assert
    assert result is None


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
    result = await project_admin_or_super_admin_user_detail_access(request)

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
        await project_admin_or_super_admin_user_detail_access(request)

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
        await project_admin_or_super_admin_user_detail_access(request)

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
        await project_admin_or_super_admin_user_detail_access(request)

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
    result = await project_admin_or_super_admin_user_detail_access(request)

    # Assert - should return None to let service layer handle 404
    assert result is None
    mock_user_repo.get_by_id.assert_called_once_with(mock_session, "nonexistent-user")
