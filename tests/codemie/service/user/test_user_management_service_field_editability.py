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

"""Unit tests for conditional field editability (Story 8)

Tests cover:
- AC-1-4: User detail response includes all required fields
- AC-5-6: name and picture editable by user and admin
- AC-7: email editable by admin in local mode only
- AC-8: username cannot be changed by anyone
- AC-9: user_type editable by super admin in local mode only
- AC-10: user_type validation (invalid values rejected)
- AC-13: Deactivation sets deleted_at and is_active=false
- AC-15: Non-existent user_id returns 404

Note: AC-11 (is_admin revocation) and AC-12 (project_limit validation)
are covered by Story 5 and Story 6 tests respectively.
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
def local_user():
    """Local auth user fixture"""
    return UserDB(
        id="user-local-1",
        username="localuser",
        email="local@example.com",
        name="Local User",
        user_type="regular",
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
def idp_user():
    """IDP auth user fixture"""
    return UserDB(
        id="user-idp-1",
        username="idpuser",
        email="idp@example.com",
        name="IDP User",
        user_type="external",
        password_hash=None,
        auth_source="keycloak",
        is_active=True,
        is_admin=False,
        email_verified=True,
        project_limit=3,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


# ===========================================
# AC-7: Email editable in local mode only
# ===========================================


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "local")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_email_update_local_mode_success(mock_get_session, mock_repo, local_user):
    """AC-7: Email update succeeds in local mode"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = local_user
    updated_user = UserDB(**local_user.model_dump())
    updated_user.email = "newemail@example.com"
    mock_repo.update.return_value = updated_user

    # Mock get_user_with_relationships
    mock_detail = CodeMieUserDetail(
        id=updated_user.id,
        username=updated_user.username,
        email="newemail@example.com",
        name=updated_user.name,
        picture=None,
        user_type=updated_user.user_type,
        is_active=updated_user.is_active,
        is_admin=updated_user.is_admin,
        auth_source=updated_user.auth_source,
        email_verified=updated_user.email_verified,
        last_login_at=updated_user.last_login_at,
        projects=[],
        project_limit=updated_user.project_limit,
        knowledge_bases=[],
        date=updated_user.date,
        update_date=updated_user.update_date,
        deleted_at=updated_user.deleted_at,
    )

    with patch.object(UserManagementService, "get_user_with_relationships", return_value=mock_detail):
        # Act
        result = UserManagementService.update_user_fields(
            user_id="user-local-1", actor_user_id="admin-1", email="newemail@example.com"
        )

    # Assert
    assert result.email == "newemail@example.com"
    mock_repo.update.assert_called_once()
    assert "email" in mock_repo.update.call_args[1]


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "keycloak")
@patch("codemie.clients.postgres.get_session")
def test_email_update_idp_mode_blocked(mock_get_session):
    """AC-7: Email update blocked in IDP mode with clear error message"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(user_id="user-idp-1", actor_user_id="admin-1", email="new@example.com")

    # Verify exception
    assert exc_info.value.code == 400
    assert "Email cannot be changed in IDP mode" in exc_info.value.message
    assert "keycloak" in exc_info.value.details


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "oidc")
@patch("codemie.clients.postgres.get_session")
def test_email_update_oidc_mode_blocked(mock_get_session):
    """AC-7: Email update blocked in OIDC mode"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(
            user_id="user-oidc-1", actor_user_id="admin-1", email="new@example.com"
        )

    assert exc_info.value.code == 400
    assert "Email cannot be changed in IDP mode" in exc_info.value.message
    assert "oidc" in exc_info.value.details


# ===========================================
# AC-8: Username cannot be changed
# ===========================================


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "local")
@patch("codemie.clients.postgres.get_session")
def test_username_update_always_blocked(mock_get_session):
    """AC-8: Username update always blocked (immutable identifier)"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(
            user_id="user-local-1", actor_user_id="admin-1", username="newusername"
        )

    # Verify exception
    assert exc_info.value.code == 400
    assert "Username cannot be changed" in exc_info.value.message
    assert "immutable" in exc_info.value.details.lower()


# ===========================================
# AC-9: user_type editable in local mode only
# ===========================================


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "local")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_user_type_update_local_mode_success(mock_get_session, mock_repo, local_user):
    """AC-9: user_type update succeeds in local mode by super admin"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Create super admin actor
    super_admin = UserDB(
        id="super-admin-1",
        username="superadmin",
        email="superadmin@example.com",
        name="Super Admin",
        user_type="regular",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        email_verified=True,
        project_limit=None,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )

    # First call: fetch actor (super admin), second: fetch target user if needed
    mock_repo.get_by_id.side_effect = [super_admin, local_user]
    updated_user = UserDB(**local_user.model_dump())
    updated_user.user_type = "external"
    mock_repo.update.return_value = updated_user

    # Mock get_user_with_relationships
    mock_detail = CodeMieUserDetail(
        id=updated_user.id,
        username=updated_user.username,
        email=updated_user.email,
        name=updated_user.name,
        picture=None,
        user_type="external",
        is_active=updated_user.is_active,
        is_admin=updated_user.is_admin,
        auth_source=updated_user.auth_source,
        email_verified=updated_user.email_verified,
        last_login_at=updated_user.last_login_at,
        projects=[],
        project_limit=updated_user.project_limit,
        knowledge_bases=[],
        date=updated_user.date,
        update_date=updated_user.update_date,
        deleted_at=updated_user.deleted_at,
    )

    with patch.object(UserManagementService, "get_user_with_relationships", return_value=mock_detail):
        # Act
        result = UserManagementService.update_user_fields(
            user_id="user-local-1", actor_user_id="super-admin-1", user_type="external"
        )

    # Assert
    assert result.user_type == "external"
    mock_repo.update.assert_called_once()
    assert "user_type" in mock_repo.update.call_args[1]


# ===========================================
# AC-10: user_type validation
# ===========================================


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "local")
@patch("codemie.clients.postgres.get_session")
def test_user_type_invalid_value_rejected(mock_get_session):
    """AC-10: Invalid user_type values rejected with 400 Bad Request"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(
            user_id="user-local-1",
            actor_user_id="admin-1",
            user_type="contractor",  # Invalid value
        )

    # Verify exception
    assert exc_info.value.code == 400
    assert "Invalid user_type" in exc_info.value.message
    assert "regular" in exc_info.value.details
    assert "external" in exc_info.value.details


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "local")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_user_type_case_insensitive_normalization(mock_get_session, mock_repo, local_user):
    """AC-10: user_type accepts case-insensitive values and normalizes to lowercase"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Create super admin actor
    super_admin = UserDB(
        id="super-admin-1",
        username="superadmin",
        email="superadmin@example.com",
        name="Super Admin",
        user_type="regular",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        email_verified=True,
        project_limit=None,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )

    # First call: fetch actor (super admin), second: fetch target user if needed
    mock_repo.get_by_id.side_effect = [super_admin, local_user]
    updated_user = UserDB(**local_user.model_dump())
    updated_user.user_type = "external"
    mock_repo.update.return_value = updated_user

    # Mock get_user_with_relationships
    mock_detail = CodeMieUserDetail(
        id=updated_user.id,
        username=updated_user.username,
        email=updated_user.email,
        name=updated_user.name,
        picture=None,
        user_type="external",
        is_active=updated_user.is_active,
        is_admin=updated_user.is_admin,
        auth_source=updated_user.auth_source,
        email_verified=updated_user.email_verified,
        last_login_at=updated_user.last_login_at,
        projects=[],
        project_limit=updated_user.project_limit,
        knowledge_bases=[],
        date=updated_user.date,
        update_date=updated_user.update_date,
        deleted_at=updated_user.deleted_at,
    )

    with patch.object(UserManagementService, "get_user_with_relationships", return_value=mock_detail):
        # Act - uppercase input by super admin
        result = UserManagementService.update_user_fields(
            user_id="user-local-1",
            actor_user_id="super-admin-1",
            user_type="EXTERNAL",  # Uppercase
        )

    # Assert - normalized to lowercase
    assert result.user_type == "external"
    update_call = mock_repo.update.call_args[1]
    assert update_call["user_type"] == "external"  # Normalized


# ===========================================
# AC-5-6: name and picture always editable
# ===========================================


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "keycloak")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_name_and_picture_editable_in_idp_mode(mock_get_session, mock_repo, idp_user):
    """AC-5-6: name and picture always editable regardless of auth mode"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = idp_user
    updated_user = UserDB(**idp_user.model_dump())
    updated_user.name = "Updated Name"
    updated_user.picture = "https://example.com/pic.jpg"
    mock_repo.update.return_value = updated_user

    # Mock get_user_with_relationships
    mock_detail = CodeMieUserDetail(
        id=updated_user.id,
        username=updated_user.username,
        email=updated_user.email,
        name="Updated Name",
        picture="https://example.com/pic.jpg",
        user_type=updated_user.user_type,
        is_active=updated_user.is_active,
        is_admin=updated_user.is_admin,
        auth_source=updated_user.auth_source,
        email_verified=updated_user.email_verified,
        last_login_at=updated_user.last_login_at,
        projects=[],
        project_limit=updated_user.project_limit,
        knowledge_bases=[],
        date=updated_user.date,
        update_date=updated_user.update_date,
        deleted_at=updated_user.deleted_at,
    )

    with patch.object(UserManagementService, "get_user_with_relationships", return_value=mock_detail):
        # Act
        result = UserManagementService.update_user_fields(
            user_id="user-idp-1",
            actor_user_id="admin-1",
            name="Updated Name",
            picture="https://example.com/pic.jpg",
        )

    # Assert
    assert result.name == "Updated Name"
    assert result.picture == "https://example.com/pic.jpg"
    mock_repo.update.assert_called_once()
    update_call = mock_repo.update.call_args[1]
    assert update_call["name"] == "Updated Name"
    assert update_call["picture"] == "https://example.com/pic.jpg"


# ===========================================
# AC-15: Non-existent user_id returns 404
# ===========================================


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "local")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_user_not_found_returns_404(mock_get_session, mock_repo):
    """AC-15: GET /v1/admin/users/{nonexistent_id} returns 404"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = None

    # Act & Assert - Story 10: Pass requesting user context
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.get_user_detail("nonexistent-id", "admin-user", is_admin=True)

    # Verify exception
    assert exc_info.value.code == 404
    assert "User not found" in exc_info.value.message


# ===========================================
# Edge Cases & Error Scenarios
# ===========================================


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "local")
@patch("codemie.clients.postgres.get_session")
def test_no_fields_to_update_returns_400(mock_get_session):
    """Edge case: No fields provided for update returns 400"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(user_id="user-1", actor_user_id="admin-1")

    # Verify exception
    assert exc_info.value.code == 400
    assert "No fields to update" in exc_info.value.message


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "local")
@patch("codemie.clients.postgres.get_session")
def test_multiple_validation_errors_username_first(mock_get_session):
    """Edge case: Multiple validation errors - username immutability checked first"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Act & Assert - username checked before other validations
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(
            user_id="user-1",
            actor_user_id="admin-1",
            username="newname",
            user_type="invalid",  # Also invalid
        )

    # Verify username error raised first
    assert exc_info.value.code == 400
    assert "Username cannot be changed" in exc_info.value.message


# ===========================================
# AC-9: user_type requires super admin (HIGH-2 fix)
# ===========================================


@pytest.fixture
def super_admin_user():
    """Super admin user fixture"""
    return UserDB(
        id="super-admin-1",
        username="superadmin",
        email="superadmin@example.com",
        name="Super Admin",
        user_type="regular",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=True,
        email_verified=True,
        project_limit=None,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


@pytest.fixture
def regular_admin_user():
    """Regular admin user fixture (not super admin)"""
    return UserDB(
        id="regular-admin-1",
        username="admin",
        email="admin@example.com",
        name="Regular Admin",
        user_type="regular",
        password_hash="hashed",
        auth_source="local",
        is_active=True,
        is_admin=False,
        email_verified=True,
        project_limit=3,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "local")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_user_type_update_requires_super_admin(mock_get_session, mock_repo, super_admin_user, local_user):
    """AC-9: user_type update requires super admin in local mode"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    # First call: fetch actor user (super admin)
    # Second call: fetch target user if needed
    mock_repo.get_by_id.side_effect = [super_admin_user, local_user]
    updated_user = UserDB(**local_user.model_dump())
    updated_user.user_type = "external"
    mock_repo.update.return_value = updated_user

    # Mock get_user_with_relationships
    mock_detail = CodeMieUserDetail(
        id=updated_user.id,
        username=updated_user.username,
        email=updated_user.email,
        name=updated_user.name,
        picture=None,
        user_type="external",
        is_active=updated_user.is_active,
        is_admin=updated_user.is_admin,
        auth_source=updated_user.auth_source,
        email_verified=updated_user.email_verified,
        last_login_at=updated_user.last_login_at,
        projects=[],
        project_limit=updated_user.project_limit,
        knowledge_bases=[],
        date=updated_user.date,
        update_date=updated_user.update_date,
        deleted_at=updated_user.deleted_at,
    )

    with patch.object(UserManagementService, "get_user_with_relationships", return_value=mock_detail):
        # Act - super admin can change user_type
        result = UserManagementService.update_user_fields(
            user_id="user-local-1", actor_user_id="super-admin-1", user_type="external"
        )

    # Assert
    assert result.user_type == "external"


@patch("codemie.service.user.user_management_service.config.IDP_PROVIDER", "local")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_user_type_update_blocked_for_non_super_admin(mock_get_session, mock_repo, regular_admin_user):
    """AC-9: user_type update blocked for non-super admin even in local mode"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = regular_admin_user

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(
            user_id="user-local-1", actor_user_id="regular-admin-1", user_type="external"
        )

    # Verify exception
    assert exc_info.value.code == 403
    assert "Insufficient permissions to change user type" in exc_info.value.message
    assert "super admin" in exc_info.value.details.lower()


# ===========================================
# AC-13: Deactivation tests (HIGH-3 fix)
# ===========================================


@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_deactivation_via_put_is_active_false(mock_get_session, mock_repo, local_user):
    """AC-13: PUT with is_active=False triggers soft delete"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = local_user
    mock_repo.count_active_superadmins.return_value = 2  # Not last admin
    mock_repo.soft_delete.return_value = True

    deactivated_user = UserDB(**local_user.model_dump())
    deactivated_user.is_active = False
    deactivated_user.deleted_at = datetime.now(UTC)

    # Mock get_user_with_relationships for deactivated user
    mock_detail = CodeMieUserDetail(
        id=deactivated_user.id,
        username=deactivated_user.username,
        email=deactivated_user.email,
        name=deactivated_user.name,
        picture=None,
        user_type=deactivated_user.user_type,
        is_active=False,
        is_admin=deactivated_user.is_admin,
        auth_source=deactivated_user.auth_source,
        email_verified=deactivated_user.email_verified,
        last_login_at=deactivated_user.last_login_at,
        projects=[],
        project_limit=deactivated_user.project_limit,
        knowledge_bases=[],
        date=deactivated_user.date,
        update_date=deactivated_user.update_date,
        deleted_at=deactivated_user.deleted_at,
    )

    with patch.object(UserManagementService, "get_user_with_relationships", return_value=mock_detail):
        # Act
        result = UserManagementService.update_user_fields(
            user_id="user-local-1", actor_user_id="admin-1", is_active=False
        )

    # Assert
    assert result.is_active is False
    assert result.deleted_at is not None
    mock_repo.soft_delete.assert_called_once_with(mock_session, "user-local-1")


@patch("codemie.clients.postgres.get_session")
def test_reactivation_via_put_is_active_true_blocked(mock_get_session):
    """AC-13: PUT with is_active=True returns 400 (reactivation not supported)"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        UserManagementService.update_user_fields(user_id="user-1", actor_user_id="admin-1", is_active=True)

    # Verify exception
    assert exc_info.value.code == 400
    assert "Cannot reactivate user" in exc_info.value.message


# ===========================================
# AC-1-4: GET response field completeness (HIGH-1 fix)
# ===========================================


@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_get_user_detail_includes_all_required_fields(mock_get_session, mock_repo, local_user):
    """AC-1-4: GET user detail returns all fields from spec"""
    # Arrange
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session

    # Set all fields explicitly
    local_user.last_login_at = datetime.now(UTC)
    local_user.deleted_at = None

    mock_repo.get_by_id.return_value = local_user
    mock_repo.get_user_projects.return_value = []
    mock_repo.get_user_knowledge_bases.return_value = ["kb1", "kb2"]

    # Act
    result = UserManagementService.get_user_detail("user-local-1", "admin-user", is_admin=True)

    # Assert - verify all required fields are present
    assert hasattr(result, "id")
    assert hasattr(result, "username")
    assert hasattr(result, "email")
    assert hasattr(result, "name")
    assert hasattr(result, "picture")
    assert hasattr(result, "user_type")
    assert hasattr(result, "is_active")
    assert hasattr(result, "is_admin")
    assert hasattr(result, "auth_source")
    assert hasattr(result, "email_verified")
    assert hasattr(result, "last_login_at")
    assert hasattr(result, "projects")
    assert hasattr(result, "project_limit")
    assert hasattr(result, "knowledge_bases")
    assert hasattr(result, "date")
    assert hasattr(result, "update_date")
    assert hasattr(result, "deleted_at")

    # Verify values
    assert result.id == local_user.id
    assert result.username == local_user.username
    assert result.knowledge_bases == ["kb1", "kb2"]


# ===========================================
# AC-5: Projects JSON key validation (HIGH-5 fix)
# ===========================================


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.clients.postgres.get_session")
def test_projects_response_uses_name_key_not_project_name(mock_get_session, mock_repo, mock_user_proj_repo, local_user):
    """AC-5: Verify projects array uses 'name' key, not 'project_name'"""
    # Arrange
    from codemie.rest_api.models.user_management import UserProject

    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_repo.get_by_id.return_value = local_user

    # Mock projects with project_name field (repository model)
    mock_projects = [
        UserProject(
            id="proj-1",
            user_id=local_user.id,
            project_name="project-a",
            is_project_admin=True,
            date=datetime.now(UTC),
        ),
        UserProject(
            id="proj-2",
            user_id=local_user.id,
            project_name="project-b",
            is_project_admin=False,
            date=datetime.now(UTC),
        ),
    ]
    mock_repo.get_user_projects.return_value = mock_projects
    mock_repo.get_user_knowledge_bases.return_value = []
    # Story 10: Mock visibility filtering to return all projects (admin can see all)
    mock_user_proj_repo.get_visible_projects_for_user.return_value = mock_projects

    # Act
    result = UserManagementService.get_user_detail("user-local-1", "admin-user", is_admin=True)

    # Assert - projects should use 'name' not 'project_name'
    assert len(result.projects) == 2
    # Check first project has 'name' attribute
    assert hasattr(result.projects[0], "name")
    assert result.projects[0].name == "project-a"
    assert result.projects[0].is_project_admin is True
    # Check second project
    assert result.projects[1].name == "project-b"
    assert result.projects[1].is_project_admin is False

    # Verify JSON serialization uses correct keys
    result_dict = result.model_dump()
    assert "projects" in result_dict
    assert result_dict["projects"][0]["name"] == "project-a"
    assert "project_name" not in result_dict["projects"][0]
