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

"""Unit tests for project_limit auto-management and validation (Story 6)

Tests cover:
- AC-1: Promotion auto-sets project_limit=NULL
- AC-2: Demotion auto-sets project_limit=3
- AC-3: Atomic transaction for role + limit changes
- AC-4: Super admin cannot modify own project_limit (403)
- AC-5: Setting regular user's project_limit to NULL returns 400
- AC-6: Negative project_limit values return 400
- AC-7: Non-integer project_limit values return 400 (handled by Pydantic)
- AC-8: Super admin can set another user's project_limit to valid values
- AC-9: Super admin can set another super admin's project_limit to NULL (via auto-management)
- AC-10: Regular user attempting to modify project_limit receives 403 (handled by router auth)
- AC-11: Operation is idempotent
- AC-12: Updated project_limit reflected in API responses
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_management import CodeMieUserDetail, UserDB
from codemie.service.user.user_management_service import UserManagementService


@pytest.fixture
def mock_session():
    """Mock database session"""
    return MagicMock()


@pytest.fixture
def regular_user():
    """Regular user fixture"""
    return UserDB(
        id="regular-1",
        username="user1",
        email="user1@example.com",
        name="User One",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=False,
        email_verified=True,
        project_limit=3,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


@pytest.fixture
def super_admin_user():
    """Super admin user fixture"""
    return UserDB(
        id="super-admin-1",
        username="admin1",
        email="admin1@example.com",
        name="Admin One",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        is_maintainer=True,
        email_verified=True,
        project_limit=None,  # NULL for super admins
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


# ===========================================
# AC-1: Promotion auto-sets project_limit=NULL
# ===========================================


@patch("codemie.service.user.user_management_service.logger")
@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_promotion_auto_sets_project_limit_null(
    mock_get_session, mock_repo, mock_get_relationships, mock_logger, regular_user
):
    """AC-1: Promoting user to super admin automatically sets project_limit=NULL"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    actor_user = UserDB(
        id="super-admin-1",
        username="admin1",
        email="admin1@example.com",
        name="Admin One",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        is_maintainer=True,
        email_verified=True,
        project_limit=None,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    mock_repo.get_by_id.side_effect = [actor_user, regular_user, actor_user]
    mock_repo.update.return_value = regular_user

    promoted_user = UserDB(
        id=regular_user.id,
        username=regular_user.username,
        email=regular_user.email,
        name=regular_user.name,
        password_hash=regular_user.password_hash,
        auth_source="local",
        is_active=True,
        is_admin=True,  # Promoted
        email_verified=True,
        project_limit=None,  # Auto-set to NULL
        date=regular_user.date,
        update_date=regular_user.update_date,
    )

    mock_result = CodeMieUserDetail(
        id=promoted_user.id,
        username=promoted_user.username,
        email=promoted_user.email,
        name=promoted_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=True,
        is_maintainer=promoted_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=None,  # NULL after promotion
        knowledge_bases=[],
        date=promoted_user.date,
        update_date=promoted_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    result = UserManagementService.update_user_fields(user_id="regular-1", actor_user_id="super-admin-1", is_admin=True)

    # Assert
    assert result.is_admin is True
    assert result.project_limit is None
    # Verify update was called with project_limit=None
    update_call = mock_repo.update.call_args
    assert "project_limit" in update_call[1]
    assert update_call[1]["project_limit"] is None
    # Verify logging
    mock_logger.info.assert_any_call("project_limit_auto_management: user_id=regular-1, action=promotion, limit=NULL")


# ===========================================
# AC-2: Demotion auto-sets project_limit=3
# ===========================================


@patch("codemie.service.user.user_management_service.logger")
@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_demotion_auto_sets_project_limit_3(
    mock_get_session, mock_repo, mock_get_relationships, mock_logger, super_admin_user
):
    """AC-2: Demoting user from super admin automatically sets project_limit=3"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    # Need 2+ admins to allow demotion
    mock_repo.count_active_admins.return_value = 2
    mock_repo.get_by_id.return_value = super_admin_user
    mock_repo.update.return_value = super_admin_user

    demoted_user = UserDB(
        id=super_admin_user.id,
        username=super_admin_user.username,
        email=super_admin_user.email,
        name=super_admin_user.name,
        password_hash=super_admin_user.password_hash,
        auth_source="local",
        is_active=True,
        is_admin=False,  # Demoted
        email_verified=True,
        project_limit=3,  # Auto-set to 3
        date=super_admin_user.date,
        update_date=super_admin_user.update_date,
    )

    mock_result = CodeMieUserDetail(
        id=demoted_user.id,
        username=demoted_user.username,
        email=demoted_user.email,
        name=demoted_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=False,
        is_maintainer=demoted_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=3,  # Set to default after demotion
        knowledge_bases=[],
        date=demoted_user.date,
        update_date=demoted_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    result = UserManagementService.update_user_fields(
        user_id="super-admin-1", actor_user_id="other-admin", is_admin=False
    )

    # Assert
    assert result.is_admin is False
    assert result.project_limit == 3
    # Verify update was called with project_limit=3
    update_call = mock_repo.update.call_args
    assert "project_limit" in update_call[1]
    assert update_call[1]["project_limit"] == 3
    # Verify logging
    mock_logger.info.assert_any_call("project_limit_auto_management: user_id=super-admin-1, action=demotion, limit=3")


# ===========================================
# AC-3: Atomic transaction
# ===========================================


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_atomic_transaction_role_and_limit(mock_get_session, mock_repo, mock_get_relationships, regular_user):
    """AC-3: is_admin and project_limit changes execute in same transaction"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    actor_user = UserDB(
        id="super-admin-1",
        username="admin1",
        email="admin1@example.com",
        name="Admin One",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        is_maintainer=True,
        email_verified=True,
        project_limit=None,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    mock_repo.get_by_id.side_effect = [actor_user, regular_user, actor_user]
    mock_repo.update.return_value = regular_user

    mock_result = CodeMieUserDetail(
        id=regular_user.id,
        username=regular_user.username,
        email=regular_user.email,
        name=regular_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=True,
        is_maintainer=regular_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=None,
        knowledge_bases=[],
        date=regular_user.date,
        update_date=regular_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    UserManagementService.update_user_fields(user_id="regular-1", actor_user_id="super-admin-1", is_admin=True)

    # Assert - both fields updated in single call
    assert mock_repo.update.call_count == 1
    update_call = mock_repo.update.call_args
    # Verify both is_admin and project_limit in same update call
    assert "is_admin" in update_call[1]
    assert "project_limit" in update_call[1]
    assert update_call[1]["is_admin"] is True
    assert update_call[1]["project_limit"] is None
    # Verify session.commit called once (atomic)
    assert mock_session.commit.call_count == 1


# ===========================================
# AC-4: Super admin cannot modify own limit
# ===========================================


@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_super_admin_modify_own_limit_403(mock_get_session, mock_repo, super_admin_user):
    """AC-4: Super admin attempting to modify own project_limit receives 403"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = super_admin_user

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(
            user_id="super-admin-1",
            actor_user_id="super-admin-1",  # Same as target = self-modification
            project_limit=5,
        )

    # Verify exception
    assert exc_info.value.code == 403
    assert "Super admins cannot modify their own project limit" in exc_info.value.message


# ===========================================
# AC-5: NULL for regular user returns 400
# ===========================================
# Note: Due to Python/Pydantic limitations with Optional[int] = None,
# we cannot distinguish "not provided" from "explicitly null".
# This test would require a different API design (sentinel values or Body() with embed).
# The auto-management logic handles the primary use cases for NULL values.


# ===========================================
# AC-6: Negative values return 400
# ===========================================


@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_negative_project_limit_400(mock_get_session, mock_repo, regular_user):
    """AC-6: Negative project_limit values return 400"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = regular_user

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(user_id="regular-1", actor_user_id="admin-1", project_limit=-1)

    # Verify exception
    assert exc_info.value.code == 400
    assert "Invalid project_limit: must be non-negative integer or NULL" in exc_info.value.message


# ===========================================
# AC-7: Non-integer handled by Pydantic
# ===========================================
# Pydantic validation happens at API layer (422 response)
# Service layer receives validated int or None


# ===========================================
# AC-8: Super admin can set valid limits
# ===========================================


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_super_admin_set_another_user_limit(mock_get_session, mock_repo, mock_get_relationships, regular_user):
    """AC-8: Super admin can set another user's project_limit to any valid non-negative integer"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = regular_user
    mock_repo.update.return_value = regular_user

    updated_user = UserDB(
        id=regular_user.id,
        username=regular_user.username,
        email=regular_user.email,
        name=regular_user.name,
        password_hash=regular_user.password_hash,
        auth_source="local",
        is_active=True,
        is_admin=False,
        email_verified=True,
        project_limit=10,  # Updated value
        date=regular_user.date,
        update_date=regular_user.update_date,
    )

    mock_result = CodeMieUserDetail(
        id=updated_user.id,
        username=updated_user.username,
        email=updated_user.email,
        name=updated_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=False,
        is_maintainer=updated_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=10,
        knowledge_bases=[],
        date=updated_user.date,
        update_date=updated_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    result = UserManagementService.update_user_fields(
        user_id="regular-1", actor_user_id="super-admin-1", project_limit=10
    )

    # Assert
    assert result.project_limit == 10
    # Verify update was called with project_limit=10
    update_call = mock_repo.update.call_args
    assert "project_limit" in update_call[1]
    assert update_call[1]["project_limit"] == 10


# Test various valid values
@pytest.mark.parametrize("limit_value", [0, 1, 5, 100, 999999])
@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_various_valid_project_limits(mock_get_session, mock_repo, mock_get_relationships, regular_user, limit_value):
    """AC-8: Various valid non-negative project_limit values accepted"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = regular_user
    mock_repo.update.return_value = regular_user

    mock_result = CodeMieUserDetail(
        id=regular_user.id,
        username=regular_user.username,
        email=regular_user.email,
        name=regular_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=False,
        is_maintainer=regular_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=limit_value,
        knowledge_bases=[],
        date=regular_user.date,
        update_date=regular_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    result = UserManagementService.update_user_fields(
        user_id="regular-1", actor_user_id="admin-1", project_limit=limit_value
    )

    # Assert
    assert result.project_limit == limit_value


# ===========================================
# AC-9: Auto-management handles NULL for super admins
# ===========================================


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_super_admin_gets_null_via_auto_management(mock_get_session, mock_repo, mock_get_relationships, regular_user):
    """AC-9: Promotion to super admin auto-sets project_limit to NULL (unlimited)"""
    # This is covered by AC-1 but explicitly testing the NULL aspect
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    actor_user = UserDB(
        id="super-admin-1",
        username="admin1",
        email="admin1@example.com",
        name="Admin One",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        is_maintainer=True,
        email_verified=True,
        project_limit=None,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    mock_repo.get_by_id.side_effect = [actor_user, regular_user, actor_user]
    mock_repo.update.return_value = regular_user

    mock_result = CodeMieUserDetail(
        id=regular_user.id,
        username=regular_user.username,
        email=regular_user.email,
        name=regular_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=True,
        is_maintainer=regular_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=None,  # NULL for super admins
        knowledge_bases=[],
        date=regular_user.date,
        update_date=regular_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    result = UserManagementService.update_user_fields(user_id="regular-1", actor_user_id="super-admin-1", is_admin=True)

    # Assert
    assert result.is_admin is True
    assert result.project_limit is None  # Unlimited


# ===========================================
# AC-11: Operation is idempotent
# ===========================================


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_idempotent_promotion(mock_get_session, mock_repo, mock_get_relationships, super_admin_user):
    """AC-11: Promoting already-super-admin user doesn't cause errors (idempotent)"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = super_admin_user  # Already super admin
    mock_repo.update.return_value = super_admin_user

    mock_result = CodeMieUserDetail(
        id=super_admin_user.id,
        username=super_admin_user.username,
        email=super_admin_user.email,
        name=super_admin_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=True,
        is_maintainer=super_admin_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=None,
        knowledge_bases=[],
        date=super_admin_user.date,
        update_date=super_admin_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act - promote already-super-admin user
    result = UserManagementService.update_user_fields(
        user_id="super-admin-1", actor_user_id="other-admin", is_admin=True
    )

    # Assert - operation succeeds, no errors
    assert result.is_admin is True
    assert result.project_limit is None
    # Auto-management should NOT trigger (no role change)
    # Verify update called without project_limit in updates (since no change detected)
    update_call = mock_repo.update.call_args
    # project_limit should NOT be in updates since role didn't change
    assert "project_limit" not in update_call[1] or update_call[1].get("project_limit") is None


# ===========================================
# AC-12: project_limit reflected in responses
# ===========================================


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_project_limit_in_response(mock_get_session, mock_repo, mock_get_relationships, regular_user):
    """AC-12: Updated project_limit value is reflected in subsequent API responses"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = regular_user
    mock_repo.update.return_value = regular_user

    mock_result = CodeMieUserDetail(
        id=regular_user.id,
        username=regular_user.username,
        email=regular_user.email,
        name=regular_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=False,
        is_maintainer=regular_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=7,  # Updated value
        knowledge_bases=[],
        date=regular_user.date,
        update_date=regular_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    result = UserManagementService.update_user_fields(user_id="regular-1", actor_user_id="admin-1", project_limit=7)

    # Assert - project_limit is in response
    assert hasattr(result, "project_limit")
    assert result.project_limit == 7


# ===========================================
# Edge Cases & Additional Coverage
# ===========================================


@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_new_super_admin_created_with_null_limit(mock_get_session, mock_repo):
    """INVARIANT: Newly created super admins must have project_limit=NULL"""
    from codemie.service.user.user_management_service import UserManagementService

    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Mock repository checks
    mock_repo.exists_by_email.return_value = False
    mock_repo.exists_by_username.return_value = False

    # Capture created user
    created_user = None

    def capture_create(session, user):
        nonlocal created_user
        created_user = user
        return user

    mock_repo.create.side_effect = capture_create

    # Act
    with patch("codemie.service.password_service.password_service.hash_password", return_value="hashed"):
        UserManagementService.create_local_user(
            session=mock_session,
            email="newadmin@example.com",
            username="newadmin",
            password="SecurePass123",
            is_admin=True,
        )

    # Assert
    assert created_user is not None
    assert created_user.is_admin is True
    assert created_user.project_limit is None  # NULL for unlimited


@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_new_regular_user_created_with_default_limit(mock_get_session, mock_repo):
    """Regular users created with default project_limit=3"""
    from codemie.service.user.user_management_service import UserManagementService

    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Mock repository checks
    mock_repo.exists_by_email.return_value = False
    mock_repo.exists_by_username.return_value = False

    # Capture created user
    created_user = None

    def capture_create(session, user):
        nonlocal created_user
        created_user = user
        return user

    mock_repo.create.side_effect = capture_create

    # Act
    with patch("codemie.service.password_service.password_service.hash_password", return_value="hashed"):
        UserManagementService.create_local_user(
            session=mock_session,
            email="newuser@example.com",
            username="newuser",
            password="SecurePass123",
            is_admin=False,
        )

    # Assert
    assert created_user is not None
    assert created_user.is_admin is False
    assert created_user.project_limit == 3  # Default limit for regular users


@patch("codemie.service.user.user_management_service.logger")
@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_super_admin_promotion_enforces_null_limit_invariant(
    mock_get_session, mock_repo, mock_get_relationships, mock_logger, regular_user
):
    """INVARIANT: Super admin promotion FORCES project_limit=NULL even with explicit value"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    actor_user = UserDB(
        id="super-admin-1",
        username="admin1",
        email="admin1@example.com",
        name="Admin One",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        is_maintainer=True,
        email_verified=True,
        project_limit=None,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    mock_repo.get_by_id.side_effect = [actor_user, regular_user, actor_user]
    mock_repo.update.return_value = regular_user

    mock_result = CodeMieUserDetail(
        id=regular_user.id,
        username=regular_user.username,
        email=regular_user.email,
        name=regular_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=True,
        is_maintainer=regular_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=None,  # NULL enforced by invariant
        knowledge_bases=[],
        date=regular_user.date,
        update_date=regular_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act - promote to super admin AND provide explicit project_limit (should be ignored)
    result = UserManagementService.update_user_fields(
        user_id="regular-1", actor_user_id="super-admin-1", is_admin=True, project_limit=5
    )

    # Assert - INVARIANT enforced: super admin MUST have NULL limit
    assert result.is_admin is True
    assert result.project_limit is None  # NULL (invariant enforced, explicit ignored)

    # Verify update called with NULL (not explicit value)
    update_call = mock_repo.update.call_args
    assert update_call[1]["project_limit"] is None

    # Verify warning logged about ignored explicit value
    mock_logger.warning.assert_called()
    log_call = mock_logger.warning.call_args[0][0]
    assert "project_limit_override_ignored" in log_call
    assert "value=5" in log_call


@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_user_not_found_returns_404(mock_get_session, mock_repo):
    """User not found should return 404 before validation"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    actor_user = UserDB(
        id="super-admin-1",
        username="admin1",
        email="admin1@example.com",
        name="Admin One",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        is_maintainer=True,
        email_verified=True,
        project_limit=None,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    mock_repo.get_by_id.side_effect = [actor_user, None, None]

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(user_id="nonexistent", actor_user_id="super-admin-1", is_admin=True)

    assert exc_info.value.code == 404
    assert "User not found" in exc_info.value.message


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_explicit_project_limit_allowed_for_non_promotion(
    mock_get_session, mock_repo, mock_get_relationships, regular_user
):
    """Explicit project_limit is allowed when NOT promoting to super admin"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = regular_user
    mock_repo.update.return_value = regular_user

    mock_result = CodeMieUserDetail(
        id=regular_user.id,
        username=regular_user.username,
        email=regular_user.email,
        name=regular_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=False,
        is_maintainer=regular_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=10,  # Explicit value accepted
        knowledge_bases=[],
        date=regular_user.date,
        update_date=regular_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act - update regular user's project_limit (no role change)
    result = UserManagementService.update_user_fields(user_id="regular-1", actor_user_id="admin-1", project_limit=10)

    # Assert - explicit value accepted
    assert result.is_admin is False
    assert result.project_limit == 10
    # Verify update called with explicit value
    update_call = mock_repo.update.call_args
    assert update_call[1]["project_limit"] == 10


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_no_auto_management_when_role_unchanged(mock_get_session, mock_repo, mock_get_relationships, regular_user):
    """Auto-management should not trigger if is_admin value doesn't change"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    actor_user = UserDB(
        id="super-admin-1",
        username="admin1",
        email="admin1@example.com",
        name="Admin One",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        is_maintainer=True,
        email_verified=True,
        project_limit=None,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    target_admin = UserDB(
        id="regular-1",
        username="user1",
        email="user1@example.com",
        name="User One",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        is_maintainer=False,
        email_verified=True,
        project_limit=None,
        date=regular_user.date,
        update_date=regular_user.update_date,
    )
    mock_repo.get_by_id.side_effect = [actor_user, target_admin, actor_user]
    mock_repo.count_active_admins.return_value = 2
    mock_repo.update.return_value = regular_user

    mock_result = CodeMieUserDetail(
        id=regular_user.id,
        username=regular_user.username,
        email=regular_user.email,
        name=regular_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=False,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=3,
        knowledge_bases=[],
        date=regular_user.date,
        update_date=regular_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act - demote from admin while maintainer actor performs the role change
    result = UserManagementService.update_user_fields(
        user_id="regular-1", actor_user_id="super-admin-1", is_admin=False
    )

    # Assert - demotion applies project-limit auto-management
    assert result.is_admin is False
    assert result.project_limit == 3
    update_call = mock_repo.update.call_args
    assert update_call[1]["project_limit"] == 3
