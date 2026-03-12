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

"""Unit tests for user profile router (PUT /v1/user/profile).

Comprehensive tests for the user profile update endpoint, covering:
- Successful profile updates
- Feature flag enforcement
- IDP provider restrictions
- Authentication requirements
- Service delegation
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import UserResponse
from codemie.rest_api.routers.user_profile_router import (
    update_profile,
    UserProfileUpdateRequest,
)
from codemie.rest_api.security.user import User


@pytest.fixture
def regular_user() -> User:
    """Mock regular user for testing authenticated endpoints."""
    return User(
        id="user-123",
        username="testuser",
        email="testuser@example.com",
        name="Test User",
        is_super_admin=False,
        project_names=["demo"],
        admin_project_names=[],
        knowledge_bases=["kb-1"],
    )


@pytest.fixture
def mock_updated_user():
    """Mock user returned from user_profile_service.update_profile."""
    return SimpleNamespace(
        id="user-123",
        username="testuser",
        email="newemail@example.com",
        name="Updated Name",
        picture="https://example.com/new-picture.jpg",
        user_type="local",
    )


@pytest.fixture
def mock_user_projects():
    """Mock user projects returned from user_project_repository."""
    return [
        SimpleNamespace(project_name="demo", is_project_admin=False),
        SimpleNamespace(project_name="analytics", is_project_admin=True),
    ]


class TestUpdateProfileSuccess:
    """Tests for successful profile update flow."""

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_all_fields_success(
        self,
        mock_config,
        mock_service,
        mock_get_session,
        mock_user_project_repo,
        regular_user,
        mock_updated_user,
        mock_user_projects,
    ):
        """Test successful profile update with all fields provided."""
        # Arrange: Enable feature and set local IDP
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        mock_config.ENV = "test"  # Set non-local env to avoid dev override

        # Mock async service method
        mock_service.update_profile = AsyncMock(return_value=mock_updated_user)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_project_repo.get_by_user_id.return_value = mock_user_projects

        payload = UserProfileUpdateRequest(
            name="Updated Name",
            picture="https://example.com/new-picture.jpg",
            email="newemail@example.com",
        )

        # Act
        result = await update_profile(data=payload, user=regular_user)

        # Assert: Verify service was called with correct parameters
        mock_service.update_profile.assert_called_once_with(
            user_id="user-123",
            name="Updated Name",
            picture="https://example.com/new-picture.jpg",
            email="newemail@example.com",
        )

        # Assert: Verify response structure
        assert isinstance(result, UserResponse)
        assert result.user_id == "user-123"
        assert result.name == "Updated Name"
        assert result.username == "testuser"
        assert result.email == "newemail@example.com"
        assert result.picture == "https://example.com/new-picture.jpg"
        # is_super_admin comes from user.is_admin property (may be True in local/dev)
        assert result.is_super_admin == regular_user.is_admin
        assert result.user_type == "local"
        assert result.knowledge_bases == ["kb-1"]
        assert len(result.projects) == 2
        assert result.projects[0].name == "demo"
        assert result.projects[0].is_project_admin is False
        assert result.projects[1].name == "analytics"
        assert result.projects[1].is_project_admin is True

        # Assert: Verify database query was made
        mock_user_project_repo.get_by_user_id.assert_called_once_with(mock_session, "user-123")

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_name_only(
        self,
        mock_config,
        mock_service,
        mock_get_session,
        mock_user_project_repo,
        regular_user,
        mock_user_projects,
    ):
        """Test profile update with only name field."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        updated_user = SimpleNamespace(
            id="user-123",
            username="testuser",
            email="testuser@example.com",
            name="New Name Only",
            picture="https://example.com/old-picture.jpg",
            user_type="local",
        )
        mock_service.update_profile = AsyncMock(return_value=updated_user)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_project_repo.get_by_user_id.return_value = mock_user_projects

        payload = UserProfileUpdateRequest(name="New Name Only")

        # Act
        result = await update_profile(data=payload, user=regular_user)

        # Assert: Service called with name only, others None
        mock_service.update_profile.assert_called_once_with(
            user_id="user-123",
            name="New Name Only",
            picture=None,
            email=None,
        )

        assert result.name == "New Name Only"
        assert result.email == "testuser@example.com"  # Unchanged

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_picture_only(
        self,
        mock_config,
        mock_service,
        mock_get_session,
        mock_user_project_repo,
        regular_user,
        mock_user_projects,
    ):
        """Test profile update with only picture field."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        updated_user = SimpleNamespace(
            id="user-123",
            username="testuser",
            email="testuser@example.com",
            name="Test User",
            picture="https://example.com/updated-avatar.png",
            user_type="local",
        )
        mock_service.update_profile = AsyncMock(return_value=updated_user)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_project_repo.get_by_user_id.return_value = mock_user_projects

        payload = UserProfileUpdateRequest(picture="https://example.com/updated-avatar.png")

        # Act
        result = await update_profile(data=payload, user=regular_user)

        # Assert
        mock_service.update_profile.assert_called_once_with(
            user_id="user-123",
            name=None,
            picture="https://example.com/updated-avatar.png",
            email=None,
        )

        assert result.picture == "https://example.com/updated-avatar.png"

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_email_only(
        self,
        mock_config,
        mock_service,
        mock_get_session,
        mock_user_project_repo,
        regular_user,
        mock_user_projects,
    ):
        """Test profile update with only email field."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        updated_user = SimpleNamespace(
            id="user-123",
            username="testuser",
            email="newemail@example.com",
            name="Test User",
            picture=None,
            user_type="local",
        )
        mock_service.update_profile = AsyncMock(return_value=updated_user)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_project_repo.get_by_user_id.return_value = mock_user_projects

        payload = UserProfileUpdateRequest(email="newemail@example.com")

        # Act
        result = await update_profile(data=payload, user=regular_user)

        # Assert
        mock_service.update_profile.assert_called_once_with(
            user_id="user-123",
            name=None,
            picture=None,
            email="newemail@example.com",
        )

        assert result.email == "newemail@example.com"

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_handles_empty_picture(
        self,
        mock_config,
        mock_service,
        mock_get_session,
        mock_user_project_repo,
        regular_user,
        mock_user_projects,
    ):
        """Test profile update handles None/empty picture gracefully."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        updated_user = SimpleNamespace(
            id="user-123",
            username="testuser",
            email="testuser@example.com",
            name="Test User",
            picture=None,  # No picture set
            user_type="local",
        )
        mock_service.update_profile = AsyncMock(return_value=updated_user)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_project_repo.get_by_user_id.return_value = mock_user_projects

        payload = UserProfileUpdateRequest(name="Test User")

        # Act
        result = await update_profile(data=payload, user=regular_user)

        # Assert: picture defaults to empty string when None
        assert result.picture == ""


class TestUpdateProfileFeatureDisabled:
    """Tests for feature flag enforcement (ENABLE_USER_MANAGEMENT)."""

    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_disabled_feature_flag(
        self,
        mock_config,
        regular_user,
    ):
        """Test profile update returns 400 when ENABLE_USER_MANAGEMENT=False."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False
        mock_config.IDP_PROVIDER = "local"  # IDP check happens after feature flag

        payload = UserProfileUpdateRequest(name="New Name")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await update_profile(data=payload, user=regular_user)

        assert exc_info.value.code == 400
        assert exc_info.value.message == "User management not enabled"


class TestUpdateProfileNonLocalIDP:
    """Tests for IDP provider restrictions (local auth only)."""

    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_keycloak_idp_rejected(
        self,
        mock_config,
        regular_user,
    ):
        """Test profile update returns 403 when IDP_PROVIDER is Keycloak."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "keycloak"

        payload = UserProfileUpdateRequest(email="newemail@example.com")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await update_profile(data=payload, user=regular_user)

        assert exc_info.value.code == 403
        assert "Profile update only available in local auth mode" in exc_info.value.message
        assert "Use IDP to manage profile" in exc_info.value.message

    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_oidc_idp_rejected(
        self,
        mock_config,
        regular_user,
    ):
        """Test profile update returns 403 when IDP_PROVIDER is OIDC."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "oidc"

        payload = UserProfileUpdateRequest(name="New Name")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await update_profile(data=payload, user=regular_user)

        assert exc_info.value.code == 403
        assert "local auth mode" in exc_info.value.message

    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_azure_ad_idp_rejected(
        self,
        mock_config,
        regular_user,
    ):
        """Test profile update returns 403 when IDP_PROVIDER is Azure AD."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "azure-ad"

        payload = UserProfileUpdateRequest(picture="https://example.com/pic.jpg")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await update_profile(data=payload, user=regular_user)

        assert exc_info.value.code == 403


class TestUpdateProfileServiceErrors:
    """Tests for service-layer error propagation."""

    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_service_not_found_error(
        self,
        mock_config,
        mock_service,
        regular_user,
    ):
        """Test profile update propagates 404 when user not found."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        mock_service.update_profile = AsyncMock(
            side_effect=ExtendedHTTPException(
                code=404,
                message="User not found",
            )
        )

        payload = UserProfileUpdateRequest(name="New Name")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await update_profile(data=payload, user=regular_user)

        assert exc_info.value.code == 404
        assert exc_info.value.message == "User not found"

    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_service_email_conflict_error(
        self,
        mock_config,
        mock_service,
        regular_user,
    ):
        """Test profile update propagates 409 when email already exists."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        mock_service.update_profile = AsyncMock(
            side_effect=ExtendedHTTPException(
                code=409,
                message="Email already in use",
            )
        )

        payload = UserProfileUpdateRequest(email="taken@example.com")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await update_profile(data=payload, user=regular_user)

        assert exc_info.value.code == 409
        assert exc_info.value.message == "Email already in use"

    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_service_validation_error(
        self,
        mock_config,
        mock_service,
        regular_user,
    ):
        """Test profile update propagates 400 for validation errors."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        mock_service.update_profile = AsyncMock(
            side_effect=ExtendedHTTPException(
                code=400,
                message="No fields to update",
            )
        )

        payload = UserProfileUpdateRequest()

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await update_profile(data=payload, user=regular_user)

        assert exc_info.value.code == 400
        assert "No fields" in exc_info.value.message


class TestUpdateProfileUnauthenticated:
    """Tests for authentication requirement.

    Note: In practice, FastAPI's Depends(authenticate) would reject
    unauthenticated requests before reaching the handler. These tests
    verify the handler expects an authenticated user.
    """

    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_uses_authenticated_user_id(
        self,
        mock_config,
        mock_service,
        regular_user,
    ):
        """Test profile update uses authenticated user's ID from JWT."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        mock_service.update_profile = AsyncMock(
            return_value=SimpleNamespace(
                id="user-123",
                username="testuser",
                email="testuser@example.com",
                name="Test User",
                picture=None,
                user_type="local",
            )
        )

        # Mock get_session to avoid actual DB calls
        with patch("codemie.clients.postgres.get_session") as mock_get_session:
            with patch("codemie.repository.user_project_repository.user_project_repository") as mock_repo:
                mock_session = MagicMock()
                mock_get_session.return_value.__enter__.return_value = mock_session
                mock_repo.get_by_user_id.return_value = []

                payload = UserProfileUpdateRequest(name="New Name")

                # Act
                await update_profile(data=payload, user=regular_user)

                # Assert: Service was called with authenticated user's ID
                mock_service.update_profile.assert_called_once()
                call_args = mock_service.update_profile.call_args
                assert call_args.kwargs["user_id"] == "user-123"


class TestUpdateProfileResponseStructure:
    """Tests for response model structure and field mapping."""

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.security.user.config")  # Patch where User.is_admin uses it
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_response_includes_is_super_admin(
        self,
        mock_router_config,
        mock_user_config,
        mock_service,
        mock_get_session,
        mock_user_project_repo,
        mock_user_projects,
    ):
        """Test response includes is_super_admin from authenticated user context."""
        # Arrange - Configure router
        mock_router_config.ENABLE_USER_MANAGEMENT = True
        mock_router_config.IDP_PROVIDER = "local"
        mock_router_config.ENV = "test"  # Non-local to avoid dev override

        # Configure for User.is_admin property evaluation
        mock_user_config.ENABLE_USER_MANAGEMENT = True
        mock_user_config.ENV = "test"  # Non-local to avoid dev override

        super_admin_user = User(
            id="admin-1",
            username="admin",
            email="admin@example.com",
            is_super_admin=True,
            project_names=["demo"],
            admin_project_names=["demo"],
            knowledge_bases=[],
        )

        updated_user = SimpleNamespace(
            id="admin-1",
            username="admin",
            email="admin@example.com",
            name="Super Admin",
            picture=None,
            user_type="local",
        )
        mock_service.update_profile = AsyncMock(return_value=updated_user)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_project_repo.get_by_user_id.return_value = mock_user_projects

        payload = UserProfileUpdateRequest(name="Super Admin")

        # Act
        result = await update_profile(data=payload, user=super_admin_user)

        # Assert: is_super_admin comes from authenticated user context
        assert result.is_super_admin is True
        assert result.user_id == "admin-1"

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_response_includes_projects(
        self,
        mock_config,
        mock_service,
        mock_get_session,
        mock_user_project_repo,
        regular_user,
    ):
        """Test response includes user's project assignments with admin flags."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        updated_user = SimpleNamespace(
            id="user-123",
            username="testuser",
            email="testuser@example.com",
            name="Test User",
            picture=None,
            user_type="local",
        )
        mock_service.update_profile = AsyncMock(return_value=updated_user)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        user_projects = [
            SimpleNamespace(project_name="project-a", is_project_admin=True),
            SimpleNamespace(project_name="project-b", is_project_admin=False),
            SimpleNamespace(project_name="project-c", is_project_admin=False),
        ]
        mock_user_project_repo.get_by_user_id.return_value = user_projects

        payload = UserProfileUpdateRequest(name="Test User")

        # Act
        result = await update_profile(data=payload, user=regular_user)

        # Assert: Projects list is populated from repository
        assert len(result.projects) == 3
        assert result.projects[0].name == "project-a"
        assert result.projects[0].is_project_admin is True
        assert result.projects[1].name == "project-b"
        assert result.projects[1].is_project_admin is False
        assert result.projects[2].name == "project-c"
        assert result.projects[2].is_project_admin is False

    @patch("codemie.repository.user_project_repository.user_project_repository")
    @patch("codemie.clients.postgres.get_session")
    @patch("codemie.rest_api.routers.user_profile_router.user_profile_service")
    @patch("codemie.rest_api.routers.user_profile_router.config")
    @pytest.mark.asyncio
    async def test_update_profile_response_includes_knowledge_bases(
        self,
        mock_config,
        mock_service,
        mock_get_session,
        mock_user_project_repo,
        regular_user,
    ):
        """Test response includes user's knowledge base assignments from context."""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"

        updated_user = SimpleNamespace(
            id="user-123",
            username="testuser",
            email="testuser@example.com",
            name="Test User",
            picture=None,
            user_type="local",
        )
        mock_service.update_profile = AsyncMock(return_value=updated_user)

        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_user_project_repo.get_by_user_id.return_value = []

        payload = UserProfileUpdateRequest(name="Test User")

        # Act
        result = await update_profile(data=payload, user=regular_user)

        # Assert: Knowledge bases come from authenticated user context
        assert result.knowledge_bases == ["kb-1"]
