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

"""Unit tests for Super Admin revocation protection (Story 5)

Tests cover:
- AC-1: Self-revocation blocked
- AC-2+3: Last admin protection (deactivation and status revocation)
- AC-4: Symmetric revocation between admins
- AC-5: Normal revocation with 3+ admins
- AC-6: Promotion unrestricted
- AC-7: Self-revocation check applies regardless of admin count
- AC-8: Logging of blocked operations
- AC-9: Protection applies to both update and deactivation endpoints
"""

from contextlib import suppress
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
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


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
        is_maintainer=False,
        email_verified=True,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


# ===========================================
# AC-1: Self-revocation blocked
# ===========================================


@patch("codemie.service.user.user_management_service.logger")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_self_revocation_blocked(mock_get_session, mock_repo, mock_logger, super_admin_user):
    """AC-1: Super admin calling PUT /v1/admin/users/{own_id} with is_admin=false receives 403"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = super_admin_user

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(
            user_id="super-admin-1",
            actor_user_id="super-admin-1",  # Same as target = self-revocation
            is_admin=False,
        )

    # Verify exception
    assert exc_info.value.code == 403
    assert "Cannot revoke own admin status" in exc_info.value.message

    # Verify logging
    mock_logger.warning.assert_called_once()
    log_call = mock_logger.warning.call_args[0][0]
    assert "blocked_self_revocation" in log_call
    assert "actor_user_id=super-admin-1" in log_call
    assert "target_user_id=super-admin-1" in log_call


# ===========================================
# AC-2: Last admin protection (status revocation)
# ===========================================


@patch("codemie.service.user.user_management_service.logger")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_last_admin_status_revocation_blocked(mock_get_session, mock_repo, mock_logger, super_admin_user):
    """AC-2: When system has 1 super admin, attempting to revoke their status returns 403"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.side_effect = [super_admin_user, super_admin_user]
    mock_repo.count_active_admins.return_value = 1  # Last admin

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(
            user_id="super-admin-1",
            actor_user_id="other-admin",  # Different actor
            is_admin=False,
        )

    # Verify exception
    assert exc_info.value.code == 403
    assert "Cannot revoke last admin" in exc_info.value.message

    # Verify logging
    mock_logger.warning.assert_called_once()
    log_call = mock_logger.warning.call_args[0][0]
    assert "blocked_last_admin_revocation" in log_call


# ===========================================
# AC-3: Last admin protection (deactivation)
# ===========================================


@patch("codemie.service.user.user_management_service.logger")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_last_admin_deactivation_blocked(mock_get_session, mock_repo, mock_logger, super_admin_user):
    """AC-3: When system has 1 super admin, attempting to deactivate them returns 403"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = super_admin_user
    mock_repo.count_active_admins.return_value = 1  # Last admin

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.deactivate_user_flow(user_id="super-admin-1", actor_user_id="system")

    # Verify exception
    assert exc_info.value.code == 403
    assert "Cannot deactivate last admin" in exc_info.value.message

    # Verify logging
    mock_logger.warning.assert_called_once()
    log_call = mock_logger.warning.call_args[0][0]
    assert "blocked_last_admin_deactivation" in log_call


# ===========================================
# AC-4: Symmetric revocation (2 admins)
# ===========================================


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_symmetric_revocation_with_two_admins(mock_get_session, mock_repo, mock_get_relationships, super_admin_user):
    """AC-4: When system has 2 super admins, Admin A can revoke Admin B's status"""
    # Arrange
    admin_b = UserDB(
        id="super-admin-2",
        username="admin2",
        email="admin2@example.com",
        name="Admin Two",
        is_admin=True,
        is_maintainer=True,
        is_active=True,
        auth_source="local",
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )

    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.side_effect = [super_admin_user, admin_b, super_admin_user]
    mock_repo.count_active_admins.return_value = 2  # 2 admins
    mock_repo.update.return_value = admin_b

    mock_result = CodeMieUserDetail(
        id=admin_b.id,
        username=admin_b.username,
        email=admin_b.email,
        name=admin_b.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=False,  # Revoked
        is_maintainer=admin_b.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=3,
        knowledge_bases=[],
        date=admin_b.date,
        update_date=admin_b.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    result = UserManagementService.update_user_fields(
        user_id="super-admin-2",
        actor_user_id="super-admin-1",
        is_admin=False,  # Admin A revokes Admin B
    )

    # Assert
    assert result.is_admin is False
    mock_repo.count_active_admins.assert_called_once_with(mock_session)


# ===========================================
# AC-5: Normal revocation (3+ admins)
# ===========================================


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_revocation_succeeds_with_three_admins(mock_get_session, mock_repo, mock_get_relationships, super_admin_user):
    """AC-5: When system has 3+ super admins, revocation succeeds normally"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.side_effect = [super_admin_user, super_admin_user, super_admin_user]
    mock_repo.count_active_admins.return_value = 3  # 3+ admins
    mock_repo.update.return_value = super_admin_user

    mock_result = CodeMieUserDetail(
        id=super_admin_user.id,
        username=super_admin_user.username,
        email=super_admin_user.email,
        name=super_admin_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=False,
        is_maintainer=super_admin_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=3,
        knowledge_bases=[],
        date=super_admin_user.date,
        update_date=super_admin_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    result = UserManagementService.update_user_fields(
        user_id="super-admin-1", actor_user_id="other-admin", is_admin=False
    )

    # Assert
    assert result.is_admin is False
    mock_repo.count_active_admins.assert_called_once_with(mock_session)


# ===========================================
# AC-6: Promotion unrestricted
# ===========================================


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_promotion_to_super_admin_unrestricted(mock_get_session, mock_repo, mock_get_relationships, regular_user):
    """AC-6: Any super admin can promote any regular user to super admin without restrictions"""
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
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    mock_repo.get_by_id.side_effect = [actor_user, regular_user, actor_user]
    mock_repo.update.return_value = regular_user

    promoted_user = regular_user
    promoted_user.is_admin = True
    promoted_user.is_maintainer = True

    mock_result = CodeMieUserDetail(
        id=promoted_user.id,
        username=promoted_user.username,
        email=promoted_user.email,
        name=promoted_user.name,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=True,  # Promoted
        is_maintainer=True,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=3,
        knowledge_bases=[],
        date=promoted_user.date,
        update_date=promoted_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    result = UserManagementService.update_user_fields(
        user_id="regular-1",
        actor_user_id="super-admin-1",
        is_admin=True,
        is_maintainer=True,
    )

    # Assert
    assert result.is_admin is True
    # count_active_admins should NOT be called for promotion
    mock_repo.count_active_admins.assert_not_called()


# ===========================================
# AC-7: Self-revocation applies regardless of count
# ===========================================


@patch("codemie.service.user.user_management_service.logger")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_self_revocation_blocked_even_with_multiple_admins(mock_get_session, mock_repo, mock_logger, super_admin_user):
    """AC-7: Self-revocation check applies regardless of how many super admins exist"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = super_admin_user
    # Even with 5 admins, self-revocation is blocked
    mock_repo.count_active_admins.return_value = 5

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(
            user_id="super-admin-1",
            actor_user_id="super-admin-1",
            is_admin=False,  # Self-revocation
        )

    # Verify exception
    assert exc_info.value.code == 403
    assert "Cannot revoke own admin status" in exc_info.value.message

    # count_active_admins should NOT be called (self-revocation checked first)
    mock_repo.count_active_admins.assert_not_called()


# ===========================================
# AC-8: Logging verification
# ===========================================


@patch("codemie.service.user.user_management_service.logger")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_blocked_operations_are_logged(mock_get_session, mock_repo, mock_logger, super_admin_user):
    """AC-8: All blocked revocation attempts are logged with context"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = super_admin_user

    # Test self-revocation logging
    with suppress(ExtendedHTTPException):
        UserManagementService.update_user_fields(user_id="super-admin-1", actor_user_id="super-admin-1", is_admin=False)

    # Verify logging contains required fields
    log_call = mock_logger.warning.call_args[0][0]
    assert "blocked_self_revocation" in log_call
    assert "actor_user_id=super-admin-1" in log_call
    assert "target_user_id=super-admin-1" in log_call
    assert "timestamp=" in log_call


# ===========================================
# AC-9: Protection applies to both endpoints
# ===========================================


@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_protection_applies_to_deactivation_endpoint(mock_get_session, mock_repo, super_admin_user):
    """AC-9: Protection applies to deactivation endpoint"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = super_admin_user
    mock_repo.count_active_admins.return_value = 1

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.deactivate_user_flow(user_id="super-admin-1", actor_user_id="system")

    assert exc_info.value.code == 403


@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_protection_applies_to_update_endpoint(mock_get_session, mock_repo, super_admin_user):
    """AC-9: Protection applies to update endpoint"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    non_maintainer_admin = UserDB(
        id="other-admin",
        username="admin2",
        email="admin2@example.com",
        name="Admin Two",
        is_admin=True,
        is_maintainer=False,
        is_active=True,
        auth_source="local",
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    mock_repo.get_by_id.return_value = non_maintainer_admin

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(user_id="super-admin-1", actor_user_id="other-admin", is_admin=False)

    assert exc_info.value.code == 403
    assert "Only maintainers can modify admin or maintainer roles" in exc_info.value.details


# ===========================================
# Edge Cases & Additional Coverage
# ===========================================


@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_regular_user_to_false_is_noop(mock_get_session, mock_repo, mock_get_relationships, regular_user):
    """Setting is_admin=false on regular user should succeed (no-op)"""
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
        is_admin=False,  # Remains false
        is_maintainer=regular_user.is_maintainer,
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

    # Act - setting is_admin=false on a regular user
    result = UserManagementService.update_user_fields(
        user_id="regular-1", actor_user_id="super-admin-1", is_admin=False
    )

    # Assert
    assert result.is_admin is False
    # Protection logic should NOT be triggered since user is NOT currently a super admin
    # count_active_admins is only called when revoking EXISTING admin status
    mock_repo.count_active_admins.assert_not_called()


@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_user_not_found_returns_404(mock_get_session, mock_repo):
    """User not found should return 404 before protection checks"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    actor = UserDB(
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
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )
    mock_repo.get_by_id.side_effect = [actor, None]

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(user_id="nonexistent", actor_user_id="super-admin-1", is_admin=False)

    assert exc_info.value.code == 404
    assert "User not found" in exc_info.value.message


@patch("codemie.service.user.user_management_service.config")
@patch("codemie.service.user.user_management_service.UserManagementService.get_user_with_relationships")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_other_fields_can_be_updated_without_triggering_protection(
    mock_get_session, mock_repo, mock_get_relationships, mock_config, super_admin_user
):
    """Updating other fields (name, email) should not trigger super admin protection"""
    # Arrange
    mock_config.IDP_PROVIDER = "local"
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = super_admin_user
    mock_repo.update.return_value = super_admin_user

    mock_result = CodeMieUserDetail(
        id=super_admin_user.id,
        username=super_admin_user.username,
        email="newemail@example.com",
        name="New Name",
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=True,  # Unchanged
        is_maintainer=super_admin_user.is_maintainer,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=3,
        knowledge_bases=[],
        date=super_admin_user.date,
        update_date=super_admin_user.update_date,
        deleted_at=None,
    )
    mock_get_relationships.return_value = mock_result

    # Act
    result = UserManagementService.update_user_fields(
        user_id="super-admin-1", actor_user_id="super-admin-1", name="New Name", email="newemail@example.com"
    )

    # Assert
    assert result.name == "New Name"
    assert result.email == "newemail@example.com"
    # Protection not triggered
    mock_repo.count_active_admins.assert_not_called()
