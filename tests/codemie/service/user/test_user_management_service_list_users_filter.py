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
- Non-super-admin cannot filter by platform_role=super_admin (403)
- Super admin can filter by platform_role=super_admin
- Non-super-admin can filter by other platform roles (platform_admin, user)
- No platform_role filter is unaffected by the guard
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_management import PlatformRole, UserListFilters
from codemie.service.user.user_management_service import UserManagementService


@pytest.fixture
def mock_session():
    return MagicMock()


# ===========================================
# Non-super-admin filtering by super_admin role
# ===========================================


def test_non_super_admin_cannot_filter_by_super_admin_role(mock_session):
    """Non-super-admin requesting platform_role=super_admin filter gets 403."""
    filters = UserListFilters(platform_role=PlatformRole.SUPER_ADMIN)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.list_users(
            mock_session,
            requesting_user_id="project-admin-1",
            is_admin=False,
            filters=filters,
        )

    assert exc_info.value.code == 403
    assert "super_admin" in exc_info.value.message.lower()


# ===========================================
# Super admin can filter by super_admin role
# ===========================================


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_super_admin_can_filter_by_super_admin_role(mock_user_repo, mock_user_project_repo, mock_session):
    """Super admin can use platform_role=super_admin filter without restriction."""
    filters = UserListFilters(platform_role=PlatformRole.SUPER_ADMIN)

    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Should not raise
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="super-admin-1",
        is_admin=True,
        filters=filters,
    )

    assert result.data == []
    mock_user_repo.count_users.assert_called_once()


# ===========================================
# Non-super-admin can filter by other roles
# ===========================================


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_non_super_admin_can_filter_by_platform_admin_role(mock_user_repo, mock_user_project_repo, mock_session):
    """Non-super-admin can filter by platform_role=platform_admin."""
    filters = UserListFilters(platform_role=PlatformRole.PLATFORM_ADMIN)

    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Should not raise
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="project-admin-1",
        is_admin=False,
        filters=filters,
    )

    assert result.data == []


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_non_super_admin_can_filter_by_user_role(mock_user_repo, mock_user_project_repo, mock_session):
    """Non-super-admin can filter by platform_role=user."""
    filters = UserListFilters(platform_role=PlatformRole.USER)

    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    # Should not raise
    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="project-admin-1",
        is_admin=False,
        filters=filters,
    )

    assert result.data == []


# ===========================================
# No platform_role filter is unaffected
# ===========================================


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_no_platform_role_filter_is_unaffected(mock_user_repo, mock_user_project_repo, mock_session):
    """Request with no platform_role filter passes through regardless of caller role."""
    filters = UserListFilters()  # No platform_role

    mock_user_repo.count_users.return_value = 0
    mock_user_repo.query_users.return_value = []

    result = UserManagementService.list_users(
        mock_session,
        requesting_user_id="project-admin-1",
        is_admin=False,
        filters=filters,
    )

    assert result.data == []
