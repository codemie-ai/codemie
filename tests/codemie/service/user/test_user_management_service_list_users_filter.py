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

"""Unit tests for list_users platform_role filter access control.

Tests cover:
- is_project_admin=True → no restriction on platform_role=ADMIN filter
- is_project_admin=False, platform_role=ADMIN, no filters.projects → raises 403
- is_project_admin=False, platform_role=ADMIN, has filters.projects, user NOT a member → raises 403
- is_project_admin=False, platform_role=ADMIN, has filters.projects, user IS a member → allowed
- is_project_admin=False, platform_role != ADMIN → no restriction, proceeds normally
- Normal pagination flow works correctly
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_management import PaginationInfo, PlatformRole, UserListFilters
from codemie.service.user.user_management_service import UserManagementService


@pytest.fixture
def mock_session():
    return MagicMock()


# ===========================================
# is_project_admin=True → no restriction
# ===========================================


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_project_admin_can_filter_by_admin_role(mock_user_repo, mock_user_project_repo, mock_session):
    """is_project_admin=True caller can use platform_role=admin filter without restriction."""
    # Arrange
    filters = UserListFilters(platform_role=PlatformRole.ADMIN)
    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Act - should not raise
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="super-admin-1",
        is_project_admin=True,
        filters=filters,
    )

    # Assert
    assert result.data == []
    mock_user_repo.count_users.assert_called_once()
    # Project membership check must NOT be triggered for elevated-privilege callers
    mock_user_project_repo.get_project_names_for_user.assert_not_called()


# ===========================================
# is_project_admin=False + ADMIN + no projects → 403
# ===========================================


def test_non_admin_cannot_filter_by_admin_role_without_projects(mock_session):
    """is_project_admin=False with platform_role=ADMIN and no projects filter raises 403."""
    # Arrange
    filters = UserListFilters(platform_role=PlatformRole.ADMIN)

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.list_users(
            mock_session,
            requesting_user_id="regular-user-1",
            is_project_admin=False,
            filters=filters,
        )

    assert exc_info.value.code == 403
    assert "admin role" in exc_info.value.message.lower()


def test_non_admin_cannot_filter_by_admin_role_with_none_filters(mock_session):
    """is_project_admin=False with default (None) filters and platform_role=ADMIN raises 403.

    Resolved filters default to UserListFilters() which has no projects, so the guard fires.
    """
    # Arrange - explicit filters object with ADMIN but no projects
    filters = UserListFilters(platform_role=PlatformRole.ADMIN, projects=None)

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.list_users(
            mock_session,
            requesting_user_id="regular-user-1",
            is_project_admin=False,
            filters=filters,
        )

    assert exc_info.value.code == 403


def test_non_admin_cannot_filter_by_admin_role_with_empty_projects_list(mock_session):
    """is_project_admin=False with platform_role=ADMIN and empty projects list raises 403."""
    # Arrange
    filters = UserListFilters(platform_role=PlatformRole.ADMIN, projects=[])

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.list_users(
            mock_session,
            requesting_user_id="regular-user-1",
            is_project_admin=False,
            filters=filters,
        )

    assert exc_info.value.code == 403
    assert "admin role" in exc_info.value.message.lower()


# ===========================================
# is_project_admin=False + ADMIN + projects present + user NOT a member → 403
# ===========================================


@patch("codemie.repository.user_project_repository.user_project_repository")
def test_non_admin_admin_filter_user_not_member_of_any_project_raises_403(mock_user_project_repo, mock_session):
    """is_project_admin=False, ADMIN filter, has projects, but user is member of none → 403."""
    # Arrange
    filters = UserListFilters(platform_role=PlatformRole.ADMIN, projects=["project-x", "project-y"])
    # User belongs to completely different projects
    mock_user_project_repo.get_project_names_for_user.return_value = {"project-a", "project-b"}

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.list_users(
            mock_session,
            requesting_user_id="regular-user-1",
            is_project_admin=False,
            filters=filters,
        )

    assert exc_info.value.code == 403
    assert "admin role" in exc_info.value.message.lower()
    mock_user_project_repo.get_project_names_for_user.assert_called_once_with(mock_session, "regular-user-1")


@patch("codemie.repository.user_project_repository.user_project_repository")
def test_non_admin_admin_filter_user_belongs_to_no_projects_raises_403(mock_user_project_repo, mock_session):
    """is_project_admin=False, ADMIN filter, has projects, user has no project memberships → 403."""
    # Arrange
    filters = UserListFilters(platform_role=PlatformRole.ADMIN, projects=["project-x"])
    mock_user_project_repo.get_project_names_for_user.return_value = set()

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.list_users(
            mock_session,
            requesting_user_id="regular-user-1",
            is_project_admin=False,
            filters=filters,
        )

    assert exc_info.value.code == 403


# ===========================================
# is_project_admin=False + ADMIN + projects present + user IS a member → allowed
# ===========================================


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_non_admin_admin_filter_user_member_of_one_project_is_allowed(
    mock_user_repo, mock_user_project_repo, mock_session
):
    """is_project_admin=False, ADMIN filter, user is member of at least one listed project → allowed."""
    # Arrange
    filters = UserListFilters(platform_role=PlatformRole.ADMIN, projects=["project-x", "project-y"])
    # User is a member of project-x (one of the filtered projects)
    mock_user_project_repo.get_project_names_for_user.return_value = {"project-x", "project-z"}
    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Act - should not raise
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="regular-user-1",
        is_project_admin=False,
        filters=filters,
    )

    # Assert
    assert result.data == []
    mock_user_project_repo.get_project_names_for_user.assert_called_once_with(mock_session, "regular-user-1")
    mock_user_repo.count_users.assert_called_once()


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_non_admin_admin_filter_user_member_of_all_projects_is_allowed(
    mock_user_repo, mock_user_project_repo, mock_session
):
    """is_project_admin=False, ADMIN filter, user is member of all listed projects → allowed."""
    # Arrange
    filters = UserListFilters(platform_role=PlatformRole.ADMIN, projects=["project-a", "project-b"])
    mock_user_project_repo.get_project_names_for_user.return_value = {"project-a", "project-b", "project-c"}
    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Act - should not raise
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="regular-user-1",
        is_project_admin=False,
        filters=filters,
    )

    # Assert
    assert result.data == []
    mock_user_repo.count_users.assert_called_once()


# ===========================================
# is_project_admin=False + other roles → no restriction
# ===========================================


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_non_admin_can_filter_by_platform_admin_role(mock_user_repo, mock_user_project_repo, mock_session):
    """is_project_admin=False with platform_role=PLATFORM_ADMIN passes without restriction."""
    # Arrange
    filters = UserListFilters(platform_role=PlatformRole.PLATFORM_ADMIN)
    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Act - should not raise
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="regular-user-1",
        is_project_admin=False,
        filters=filters,
    )

    # Assert
    assert result.data == []
    mock_user_project_repo.get_project_names_for_user.assert_not_called()


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_non_admin_can_filter_by_user_role(mock_user_repo, mock_user_project_repo, mock_session):
    """is_project_admin=False with platform_role=USER passes without restriction."""
    # Arrange
    filters = UserListFilters(platform_role=PlatformRole.USER)
    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Act - should not raise
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="regular-user-1",
        is_project_admin=False,
        filters=filters,
    )

    # Assert
    assert result.data == []
    mock_user_project_repo.get_project_names_for_user.assert_not_called()


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_no_platform_role_filter_passes_without_restriction(mock_user_repo, mock_user_project_repo, mock_session):
    """No platform_role filter passes regardless of caller role."""
    # Arrange
    filters = UserListFilters()  # No platform_role
    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Act - should not raise
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="regular-user-1",
        is_project_admin=False,
        filters=filters,
    )

    # Assert
    assert result.data == []
    mock_user_project_repo.get_project_names_for_user.assert_not_called()


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_none_filters_passes_without_restriction(mock_user_repo, mock_user_project_repo, mock_session):
    """None filters (default) passes without restriction."""
    # Arrange
    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Act - should not raise (filters=None resolves to UserListFilters() internally)
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="regular-user-1",
        is_project_admin=False,
        filters=None,
    )

    # Assert
    assert result.data == []
    mock_user_project_repo.get_project_names_for_user.assert_not_called()


# ===========================================
# Normal pagination flow
# ===========================================


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_list_users_returns_empty_pagination_when_no_results(mock_user_repo, mock_user_project_repo, mock_session):
    """When query returns no users, returns PaginatedUserListResponse with empty data and correct pagination."""
    # Arrange
    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Act
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="admin-1",
        is_project_admin=True,
        page=2,
        per_page=10,
    )

    # Assert
    assert result.data == []
    assert isinstance(result.pagination, PaginationInfo)
    assert result.pagination.total == 0
    assert result.pagination.page == 2
    assert result.pagination.per_page == 10


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_list_users_passes_pagination_params_to_repository(mock_user_repo, mock_user_project_repo, mock_session):
    """Pagination parameters are forwarded correctly to the repository query."""
    # Arrange
    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Act
    UserManagementService.list_users(
        mock_session,
        requesting_user_id="admin-1",
        is_project_admin=True,
        page=3,
        per_page=15,
        search="alice",
    )

    # Assert
    mock_user_repo.count_users.assert_called_once()
    count_call_args = mock_user_repo.count_users.call_args
    assert count_call_args[0][1] == "alice"  # search arg

    mock_user_repo.query_users.assert_called_once()
    query_call_args = mock_user_repo.query_users.call_args
    assert query_call_args[0][3] == 3  # page arg
    assert query_call_args[0][4] == 15  # per_page arg
