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

"""Unit tests for AuthenticationService

Tests authentication flows including:
- Local authentication (email/password)
- IDP user creation and profile sync
- Persistent authentication (session mode)
- Dev header authentication
- Complete login flows
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from codemie.configs import config  # noqa: F401 (used in test_build_security_user patch)
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.rest_api.security import user as security_user
from codemie.service.user.authentication_service import AuthenticationService, clear_auth_token_cache


@pytest.fixture(autouse=True)
def _clear_auth_cache():
    """Clear auth token cache before each test to prevent cross-test state leakage."""
    clear_auth_token_cache()
    yield
    clear_auth_token_cache()


def _make_async_session_cm(mock_session):
    """Create an async context manager mock that yields mock_session."""
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_session
    cm.__aexit__.return_value = False
    return cm


class TestAuthenticateLocal:
    """Tests for authenticate_local() method"""

    @pytest.mark.asyncio
    async def test_authenticate_local_success(self):
        """Valid credentials authenticate successfully"""
        # Arrange
        session = AsyncMock()
        email = "test@example.com"
        password = "ValidPassword123"
        user_id = str(uuid4())

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            email=email,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$hash",
            is_active=True,
            email_verified=True,
            deleted_at=None,
        )

        with (
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.password_service.password_service") as mock_pwd_service,
        ):
            mock_user_repo.aget_by_email = AsyncMock(return_value=mock_user)
            mock_pwd_service.verify_password = MagicMock(return_value=True)
            mock_pwd_service.needs_rehash = MagicMock(return_value=False)
            mock_user_repo.aupdate_last_login = AsyncMock()

            # Act
            result = await AuthenticationService.authenticate_local(session, email, password)

            # Assert
            assert result == mock_user
            mock_user_repo.aget_by_email.assert_called_once_with(session, email)
            mock_pwd_service.verify_password.assert_called_once_with(mock_user.password_hash, password)
            mock_user_repo.aupdate_last_login.assert_called_once_with(session, user_id)

    @pytest.mark.asyncio
    async def test_authenticate_local_user_not_found(self):
        """Raises 401 when user does not exist"""
        # Arrange
        session = AsyncMock()
        email = "nonexistent@example.com"
        password = "password"

        with patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo:
            mock_user_repo.aget_by_email = AsyncMock(return_value=None)

            # Act & Assert
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await AuthenticationService.authenticate_local(session, email, password)

            assert exc_info.value.code == 401
            assert exc_info.value.message == "Invalid email or password"

    @pytest.mark.asyncio
    async def test_authenticate_local_no_password_hash(self):
        """Raises 401 when user has no password hash (IDP user)"""
        # Arrange
        session = AsyncMock()
        email = "idp@example.com"
        password = "password"

        mock_user = UserDB(
            id=str(uuid4()),
            username="idpuser",
            email=email,
            password_hash=None,  # IDP user
            is_active=True,
            email_verified=True,
        )

        with patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo:
            mock_user_repo.aget_by_email = AsyncMock(return_value=mock_user)

            # Act & Assert
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await AuthenticationService.authenticate_local(session, email, password)

            assert exc_info.value.code == 401
            assert exc_info.value.message == "Invalid email or password"

    @pytest.mark.asyncio
    async def test_authenticate_local_wrong_password(self):
        """Raises 401 when password is incorrect"""
        # Arrange
        session = AsyncMock()
        email = "test@example.com"
        password = "WrongPassword"
        user_id = str(uuid4())

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            email=email,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$hash",
            is_active=True,
            email_verified=True,
        )

        with (
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.password_service.password_service") as mock_pwd_service,
            patch("codemie.service.user.authentication_service.logger") as mock_logger,
        ):
            mock_user_repo.aget_by_email = AsyncMock(return_value=mock_user)
            mock_pwd_service.verify_password = MagicMock(return_value=False)

            # Act & Assert
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await AuthenticationService.authenticate_local(session, email, password)

            assert exc_info.value.code == 401
            assert exc_info.value.message == "Invalid email or password"
            # Verify failed login logged
            mock_logger.warning.assert_called_once()
            assert f"user_id={user_id}" in mock_logger.warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_authenticate_local_inactive_user(self):
        """Raises 401 when user is inactive"""
        # Arrange
        session = AsyncMock()
        email = "inactive@example.com"
        password = "password"

        mock_user = UserDB(
            id=str(uuid4()),
            username="inactive",
            email=email,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$hash",
            is_active=False,  # Inactive
            email_verified=True,
        )

        with (
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.password_service.password_service") as mock_pwd_service,
        ):
            mock_user_repo.aget_by_email = AsyncMock(return_value=mock_user)
            mock_pwd_service.verify_password = MagicMock(return_value=True)

            # Act & Assert
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await AuthenticationService.authenticate_local(session, email, password)

            assert exc_info.value.code == 401
            assert exc_info.value.message == "Account is deactivated"

    @pytest.mark.asyncio
    async def test_authenticate_local_email_not_verified(self):
        """Raises 401 when email is not verified"""
        # Arrange
        session = AsyncMock()
        email = "unverified@example.com"
        password = "password"

        mock_user = UserDB(
            id=str(uuid4()),
            username="unverified",
            email=email,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$hash",
            is_active=True,
            email_verified=False,  # Not verified
        )

        with (
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.password_service.password_service") as mock_pwd_service,
        ):
            mock_user_repo.aget_by_email = AsyncMock(return_value=mock_user)
            mock_pwd_service.verify_password = MagicMock(return_value=True)

            # Act & Assert
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await AuthenticationService.authenticate_local(session, email, password)

            assert exc_info.value.code == 401
            assert exc_info.value.message == "Email not verified"

    @pytest.mark.asyncio
    async def test_authenticate_local_rehash_password(self):
        """Triggers password rehash when needed"""
        # Arrange
        session = AsyncMock()
        email = "test@example.com"
        password = "ValidPassword123"
        user_id = str(uuid4())
        new_hash = "$argon2id$v=19$m=131072,t=4,p=8$newhash"

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            email=email,
            password_hash="$argon2id$v=19$m=65536,t=2,p=2$oldhash",  # Outdated params
            is_active=True,
            email_verified=True,
        )

        with (
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.password_service.password_service") as mock_pwd_service,
        ):
            mock_user_repo.aget_by_email = AsyncMock(return_value=mock_user)
            mock_pwd_service.verify_password = MagicMock(return_value=True)
            mock_pwd_service.needs_rehash = MagicMock(return_value=True)
            mock_pwd_service.hash_password = MagicMock(return_value=new_hash)
            mock_user_repo.aupdate = AsyncMock()
            mock_user_repo.aupdate_last_login = AsyncMock()

            # Act
            await AuthenticationService.authenticate_local(session, email, password)

            # Assert
            mock_pwd_service.needs_rehash.assert_called_once_with(mock_user.password_hash)
            mock_pwd_service.hash_password.assert_called_once_with(password)
            mock_user_repo.aupdate.assert_called_once_with(session, user_id, password_hash=new_hash)


class TestLoadUserForAuth:
    """Tests for load_user_for_auth() method"""

    @pytest.mark.asyncio
    async def test_load_user_for_auth_found(self):
        """Returns security.User with relationships when user exists"""
        # Arrange
        session = AsyncMock()
        user_id = str(uuid4())

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            name="Test User",
            email="test@example.com",
            picture="https://example.com/pic.jpg",
            user_type="human",
            is_admin=False,
        )

        mock_projects = [
            UserProject(user_id=user_id, project_name="project1", is_project_admin=True),
            UserProject(user_id=user_id, project_name="project2", is_project_admin=False),
        ]

        mock_kbs = [
            MagicMock(kb_name="kb1"),
            MagicMock(kb_name="kb2"),
        ]

        with (
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.user.authentication_service.user_project_repository") as mock_proj_repo,
            patch("codemie.service.user.authentication_service.user_kb_repository") as mock_kb_repo,
        ):
            mock_user_repo.aget_active_by_id = AsyncMock(return_value=mock_user)
            mock_proj_repo.aget_by_user_id = AsyncMock(return_value=mock_projects)
            mock_kb_repo.aget_by_user_id = AsyncMock(return_value=mock_kbs)

            # Act
            result = await AuthenticationService.load_user_for_auth(session, user_id)

            # Assert
            assert isinstance(result, security_user.User)
            assert result.id == user_id
            assert result.username == "testuser"
            assert result.email == "test@example.com"
            assert result.project_names == ["project1", "project2"]
            assert result.admin_project_names == ["project1"]
            assert result.knowledge_bases == ["kb1", "kb2"]
            assert result.is_admin is False

    @pytest.mark.asyncio
    async def test_load_user_for_auth_not_found(self):
        """Returns None when user does not exist"""
        # Arrange
        session = AsyncMock()
        user_id = str(uuid4())

        with patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo:
            mock_user_repo.aget_active_by_id = AsyncMock(return_value=None)

            # Act
            result = await AuthenticationService.load_user_for_auth(session, user_id)

            # Assert
            assert result is None


class TestCreateUserFromIdp:
    """Tests for create_user_from_idp() method"""

    @pytest.mark.asyncio
    async def test_create_user_from_idp_success(self):
        """Creates user from IDP with projects and KBs"""
        # Arrange
        session = AsyncMock()
        user_id = str(uuid4())

        idp_user = security_user.User(
            id=user_id,
            username="idpuser",
            name="IDP User",
            email="idp@example.com",
            picture="https://idp.com/pic.jpg",
            user_type="human",
            roles=[],
            project_names=["project1", "project2"],
            admin_project_names=["project1"],
            knowledge_bases=["kb1"],
            is_admin=False,
        )

        mock_created_user = UserDB(
            id=user_id,
            email="idp@example.com",
            username="idpuser",
            name="IDP User",
            picture="https://idp.com/pic.jpg",
            user_type="human",
            auth_source="keycloak",
            email_verified=True,
            is_active=True,
            is_admin=False,
        )

        with (
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.user.authentication_service.user_project_repository") as mock_proj_repo,
            patch("codemie.service.user.authentication_service.user_kb_repository") as mock_kb_repo,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._ensure_projects_exist",
                new_callable=AsyncMock,
            ) as mock_ensure,
            patch("codemie.service.user.authentication_service.config") as mock_config,
        ):
            mock_config.IDP_PROVIDER = "keycloak"
            mock_config.ADMIN_USER_ID = "admin-id"
            mock_config.ADMIN_ROLE_NAME = "SuperAdmin"

            mock_user_repo.acreate = AsyncMock(return_value=mock_created_user)
            mock_proj_repo.aadd_project = AsyncMock()
            mock_kb_repo.aadd_kb = AsyncMock()

            # Act
            result = await AuthenticationService.create_user_from_idp(session, idp_user)

            # Assert
            assert result == mock_created_user
            mock_user_repo.acreate.assert_called_once()
            # Verify projects added
            assert mock_proj_repo.aadd_project.call_count == 2
            mock_proj_repo.aadd_project.assert_any_call(session, user_id, "project1", True)
            mock_proj_repo.aadd_project.assert_any_call(session, user_id, "project2", False)
            # Verify KB added
            mock_kb_repo.aadd_kb.assert_called_once_with(session, user_id, "kb1")
            # Verify projects ensured
            mock_ensure.assert_called_once_with(["project1", "project2", "idp@example.com"])

    @pytest.mark.asyncio
    async def test_create_user_from_idp_invalid_uuid(self):
        """Raises 422 when user_id is not a valid UUID"""
        # Arrange
        session = AsyncMock()

        idp_user = security_user.User(
            id="not-a-uuid",  # Invalid UUID
            username="idpuser",
            name="IDP User",
            email="idp@example.com",
            user_type="human",
            roles=[],
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
            is_admin=False,
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await AuthenticationService.create_user_from_idp(session, idp_user)

        assert exc_info.value.code == 422
        assert "User ID must be a valid UUID" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_create_user_from_idp_admin_role(self):
        """Sets is_admin=True when user has admin role"""
        # Arrange
        session = AsyncMock()
        user_id = str(uuid4())

        idp_user = security_user.User(
            id=user_id,
            username="admin",
            name="Admin User",
            email="admin@example.com",
            user_type="human",
            roles=["SuperAdmin"],  # Admin role
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
            is_admin=False,
        )

        with (
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch("codemie.service.user.authentication_service.user_project_repository"),
            patch("codemie.service.user.authentication_service.user_kb_repository"),
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._ensure_projects_exist",
                new_callable=AsyncMock,
            ),
            patch("codemie.service.user.authentication_service.config") as mock_config,
        ):
            mock_config.IDP_PROVIDER = "keycloak"
            mock_config.ADMIN_USER_ID = "other-id"
            mock_config.ADMIN_ROLE_NAME = "SuperAdmin"

            # Capture the user passed to acreate
            created_user = None

            def capture_create(sess, user):
                nonlocal created_user
                created_user = user
                return AsyncMock(return_value=user)()

            mock_user_repo.acreate = AsyncMock(side_effect=lambda sess, user: user)

            # Act
            await AuthenticationService.create_user_from_idp(session, idp_user)

            # Assert
            call_args = mock_user_repo.acreate.call_args[0]
            created_user = call_args[1]
            assert created_user.is_admin is True


class TestSyncIdpUserProfile:
    """Tests for sync_idp_user_profile() method"""

    @pytest.mark.asyncio
    async def test_sync_idp_user_profile_updates_changed_fields(self):
        """Updates email, name, and picture when changed"""
        # Arrange
        session = AsyncMock()
        user_id = str(uuid4())

        db_user = UserDB(
            id=user_id,
            email="old@example.com",
            name="Old Name",
            picture="https://old.com/pic.jpg",
            username="user",
        )

        idp_user = security_user.User(
            id=user_id,
            username="user",
            name="New Name",
            email="new@example.com",
            picture="https://new.com/pic.jpg",
            user_type="human",
            roles=[],
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
            is_admin=False,
        )

        with patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo:
            mock_user_repo.aupdate = AsyncMock(return_value=db_user)

            # Act
            await AuthenticationService.sync_idp_user_profile(session, db_user, idp_user)

            # Assert
            mock_user_repo.aupdate.assert_called_once_with(
                session,
                user_id,
                email="new@example.com",
                name="New Name",
                picture="https://new.com/pic.jpg",
            )

    @pytest.mark.asyncio
    async def test_sync_idp_user_profile_no_changes(self):
        """Does not update when no changes detected"""
        # Arrange
        session = AsyncMock()
        user_id = str(uuid4())

        db_user = UserDB(
            id=user_id,
            email="same@example.com",
            name="Same Name",
            picture="https://same.com/pic.jpg",
            username="user",
        )

        idp_user = security_user.User(
            id=user_id,
            username="user",
            name="Same Name",
            email="same@example.com",
            picture="https://same.com/pic.jpg",
            user_type="human",
            roles=[],
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
            is_admin=False,
        )

        with patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo:
            mock_user_repo.aupdate = AsyncMock()

            # Act
            result = await AuthenticationService.sync_idp_user_profile(session, db_user, idp_user)

            # Assert
            mock_user_repo.aupdate.assert_not_called()
            assert result == db_user


class TestBuildSecurityUser:
    """Tests for _build_security_user() method"""

    def test_build_security_user(self):
        """Builds security.User from DB user with correct field mapping"""
        # Arrange
        user_id = str(uuid4())
        auth_token = "test-token-123"

        db_user = UserDB(
            id=user_id,
            username="testuser",
            name="Test User",
            email="test@example.com",
            picture="https://example.com/pic.jpg",
            user_type="human",
            is_admin=True,
        )

        # Act - patch config so resolve_is_admin preserves the passed is_admin=True value
        with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
            result = AuthenticationService._build_security_user(db_user, auth_token)

        # Assert
        assert isinstance(result, security_user.User)
        assert result.id == user_id
        assert result.username == "testuser"
        assert result.name == "Test User"
        assert result.email == "test@example.com"
        assert result.picture == "https://example.com/pic.jpg"
        assert result.user_type == "human"
        assert result.is_admin is True
        assert result.auth_token == auth_token
        # Relationships empty (populated later)
        assert result.project_names == []
        assert result.admin_project_names == []
        assert result.knowledge_bases == []
        assert result.roles == []


class TestValidateUserIdUuid:
    """Tests for _validate_user_id_uuid() method"""

    def test_validate_user_id_uuid_valid(self):
        """No exception for valid UUID"""
        # Arrange
        valid_uuid = str(uuid4())

        # Act & Assert (no exception)
        AuthenticationService._validate_user_id_uuid(valid_uuid)

    def test_validate_user_id_uuid_invalid(self):
        """Raises 422 for invalid UUID"""
        # Arrange
        invalid_uuid = "not-a-uuid"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            AuthenticationService._validate_user_id_uuid(invalid_uuid)

        assert exc_info.value.code == 422
        assert "User ID must be a valid UUID" in exc_info.value.message


class TestAuthenticatePersistentUser:
    """Tests for authenticate_persistent_user() method"""

    @pytest.mark.asyncio
    async def test_authenticate_persistent_user_existing(self):
        """Loads existing user with profile sync"""
        # Arrange
        user_id = str(uuid4())
        auth_token = "token-123"

        db_user = UserDB(
            id=user_id,
            email="test@example.com",
            username="testuser",
            name="Test User",
            is_active=True,
            deleted_at=None,
        )

        idp_user = security_user.User(
            id=user_id,
            username="testuser",
            name="Test User",
            email="test@example.com",
            user_type="human",
            roles=[],
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
            is_admin=False,
        )

        mock_session = AsyncMock()

        with (
            patch("codemie.clients.postgres.get_async_session") as mock_get_session,
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._sync_existing_user",
                new_callable=AsyncMock,
            ) as mock_sync,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._finalize_authentication",
                new_callable=AsyncMock,
            ) as mock_finalize,
            patch("codemie.service.user.authentication_service.config") as mock_config,
        ):
            mock_config.IDP_PROVIDER = "keycloak"
            mock_get_session.return_value = _make_async_session_cm(mock_session)
            mock_user_repo.aget_by_id = AsyncMock(return_value=db_user)
            mock_sync.return_value = "test@example.com"

            expected_user = security_user.User(
                id=user_id,
                username="testuser",
                name="Test User",
                email="test@example.com",
                user_type="human",
                roles=[],
                project_names=["project1"],
                admin_project_names=[],
                knowledge_bases=[],
                is_admin=False,
                auth_token=auth_token,
            )
            mock_finalize.return_value = expected_user

            # Act
            result = await AuthenticationService.authenticate_persistent_user(user_id, idp_user, auth_token)

            # Assert
            assert result == expected_user
            mock_user_repo.aget_by_id.assert_called_once_with(mock_session, user_id)
            mock_sync.assert_called_once_with(mock_session, db_user, idp_user)
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_authenticate_persistent_user_first_login(self):
        """Creates user on first login"""
        # Arrange
        user_id = str(uuid4())
        auth_token = "token-123"

        idp_user = security_user.User(
            id=user_id,
            username="newuser",
            name="New User",
            email="new@example.com",
            user_type="human",
            roles=[],
            project_names=["project1"],
            admin_project_names=[],
            knowledge_bases=[],
            is_admin=False,
        )

        created_user = UserDB(
            id=user_id,
            email="new@example.com",
            username="newuser",
            name="New User",
            is_active=True,
            deleted_at=None,
        )

        mock_session = AsyncMock()

        with (
            patch("codemie.clients.postgres.get_async_session") as mock_get_session,
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._create_first_login_user",
                new_callable=AsyncMock,
            ) as mock_create,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._finalize_authentication",
                new_callable=AsyncMock,
            ) as mock_finalize,
            patch("codemie.service.user.authentication_service.config") as mock_config,
        ):
            mock_config.IDP_PROVIDER = "keycloak"
            mock_get_session.return_value = _make_async_session_cm(mock_session)
            mock_user_repo.aget_by_id = AsyncMock(return_value=None)  # User doesn't exist
            mock_create.return_value = created_user

            expected_user = security_user.User(
                id=user_id,
                username="newuser",
                name="New User",
                email="new@example.com",
                user_type="human",
                roles=[],
                project_names=["project1"],
                admin_project_names=[],
                knowledge_bases=[],
                is_admin=False,
                auth_token=auth_token,
            )
            mock_finalize.return_value = expected_user

            # Act
            result = await AuthenticationService.authenticate_persistent_user(user_id, idp_user, auth_token)

            # Assert
            assert result == expected_user
            mock_create.assert_called_once_with(mock_session, idp_user)

    @pytest.mark.asyncio
    async def test_authenticate_persistent_user_email_changed_reconciles(self):
        """Reconciles personal project when email changes during IDP sync"""
        # Arrange
        user_id = str(uuid4())
        auth_token = "token-123"
        old_email = "old@example.com"
        new_email = "new@example.com"

        db_user = UserDB(
            id=user_id,
            email=old_email,
            username="testuser",
            name="Test User",
            is_active=True,
            deleted_at=None,
        )

        # IDP user has new email
        idp_user = security_user.User(
            id=user_id,
            username="testuser",
            name="Test User",
            email=new_email,
            user_type="human",
            roles=[],
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
            is_admin=False,
        )

        mock_session = AsyncMock()

        with (
            patch("codemie.clients.postgres.get_async_session") as mock_get_session,
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._sync_existing_user",
                new_callable=AsyncMock,
            ) as mock_sync,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._finalize_authentication",
                new_callable=AsyncMock,
            ) as mock_finalize,
            patch("codemie.service.project.personal_project_service.personal_project_service") as mock_personal,
            patch("codemie.service.user.authentication_service.config") as mock_config,
        ):
            mock_config.IDP_PROVIDER = "keycloak"
            mock_get_session.return_value = _make_async_session_cm(mock_session)
            mock_user_repo.aget_by_id = AsyncMock(return_value=db_user)
            mock_sync.return_value = old_email  # Return pre-sync email

            # Update db_user email to simulate sync
            db_user.email = new_email

            expected_user = security_user.User(
                id=user_id,
                username="testuser",
                name="Test User",
                email=new_email,
                user_type="human",
                roles=[],
                project_names=[],
                admin_project_names=[],
                knowledge_bases=[],
                is_admin=False,
                auth_token=auth_token,
            )
            mock_finalize.return_value = expected_user
            mock_personal.reconcile_personal_project_on_email_change = AsyncMock()

            # Act
            result = await AuthenticationService.authenticate_persistent_user(user_id, idp_user, auth_token)

            # Assert
            assert result == expected_user
            # Verify reconciliation called
            mock_personal.reconcile_personal_project_on_email_change.assert_called_once_with(
                user_id, old_email, new_email
            )

    @pytest.mark.asyncio
    async def test_authenticate_persistent_user_deactivated(self):
        """Raises 401 when user is deactivated"""
        # Arrange
        user_id = str(uuid4())
        auth_token = "token-123"

        db_user = UserDB(
            id=user_id,
            email="deactivated@example.com",
            username="deactivated",
            is_active=False,  # Deactivated
            deleted_at=None,
        )

        idp_user = security_user.User(
            id=user_id,
            username="deactivated",
            email="deactivated@example.com",
            user_type="human",
            roles=[],
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
            is_admin=False,
        )

        mock_session = AsyncMock()

        with (
            patch("codemie.clients.postgres.get_async_session") as mock_get_session,
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._sync_existing_user",
                new_callable=AsyncMock,
            ),
        ):
            mock_get_session.return_value = _make_async_session_cm(mock_session)
            mock_user_repo.aget_by_id = AsyncMock(return_value=db_user)

            # Act & Assert
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await AuthenticationService.authenticate_persistent_user(user_id, idp_user, auth_token)

            assert exc_info.value.code == 401
            assert exc_info.value.message == "Account is deactivated"


class TestEnsureProjectExists:
    """Tests for ensure_project_exists() method"""

    @pytest.mark.asyncio
    async def test_ensure_project_exists_success(self):
        """Successfully ensures project exists"""
        # Arrange
        project_name = "test-project"
        mock_session = AsyncMock()

        with (
            patch("codemie.clients.postgres.get_async_session") as mock_get_session,
            patch("codemie.service.user.authentication_service.application_repository") as mock_app_repo,
        ):
            mock_get_session.return_value = _make_async_session_cm(mock_session)
            mock_app_repo.aget_or_create = AsyncMock()

            # Act
            await AuthenticationService.ensure_project_exists(project_name)

            # Assert
            mock_app_repo.aget_or_create.assert_called_once_with(mock_session, project_name)
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_project_exists_handles_failure(self):
        """Logs warning and continues when project creation fails"""
        # Arrange
        project_name = "failing-project"
        mock_session = AsyncMock()

        with (
            patch("codemie.clients.postgres.get_async_session") as mock_get_session,
            patch("codemie.service.user.authentication_service.application_repository") as mock_app_repo,
            patch("codemie.service.user.authentication_service.logger") as mock_logger,
        ):
            mock_get_session.return_value = _make_async_session_cm(mock_session)
            mock_app_repo.aget_or_create = AsyncMock(side_effect=Exception("DB error"))

            # Act - should not raise
            await AuthenticationService.ensure_project_exists(project_name)

            # Assert
            mock_logger.warning.assert_called_once()
            assert project_name in mock_logger.warning.call_args[0][0]


class TestAuthenticateDevHeader:
    """Tests for authenticate_dev_header() method"""

    @pytest.mark.asyncio
    async def test_authenticate_dev_header_existing_user(self):
        """Loads existing dev header user"""
        # Arrange
        user_id = str(uuid4())

        db_user = UserDB(
            id=user_id,
            email=user_id,
            username=user_id,
            name="Dev User",
            auth_source="dev_header",
            is_active=True,
            is_admin=True,
        )

        mock_session = AsyncMock()

        with (
            patch("codemie.clients.postgres.get_async_session") as mock_get_session,
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._finalize_authentication",
                new_callable=AsyncMock,
            ) as mock_finalize,
        ):
            mock_get_session.return_value = _make_async_session_cm(mock_session)
            mock_user_repo.aget_by_id = AsyncMock(return_value=db_user)
            mock_user_repo.aupdate_last_login = AsyncMock()

            expected_user = security_user.User(
                id=user_id,
                username=user_id,
                name="Dev User",
                email=user_id,
                user_type="human",
                roles=[],
                project_names=[],
                admin_project_names=[],
                knowledge_bases=[],
                is_admin=True,
            )
            mock_finalize.return_value = expected_user

            # Act
            result = await AuthenticationService.authenticate_dev_header(user_id)

            # Assert
            assert result == expected_user
            mock_user_repo.aget_by_id.assert_called_once_with(mock_session, user_id)
            mock_user_repo.aupdate_last_login.assert_called_once_with(mock_session, user_id)

    @pytest.mark.asyncio
    async def test_authenticate_dev_header_new_user(self):
        """Creates new dev header user on first access"""
        # Arrange
        user_id = "dev-user-123"

        mock_session = AsyncMock()

        created_user = UserDB(
            id=user_id,
            email=user_id,
            username=user_id,
            name="Dev User",
            auth_source="dev_header",
            email_verified=True,
            is_active=True,
            is_admin=True,
        )

        with (
            patch("codemie.clients.postgres.get_async_session") as mock_get_session,
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._finalize_authentication",
                new_callable=AsyncMock,
            ) as mock_finalize,
            patch("codemie.service.user.authentication_service.logger") as mock_logger,
        ):
            mock_get_session.return_value = _make_async_session_cm(mock_session)
            mock_user_repo.aget_by_id = AsyncMock(return_value=None)  # User doesn't exist
            mock_user_repo.acreate = AsyncMock(return_value=created_user)
            mock_user_repo.aupdate_last_login = AsyncMock()

            expected_user = security_user.User(
                id=user_id,
                username=user_id,
                name="Dev User",
                email=user_id,
                user_type="human",
                roles=[],
                project_names=[],
                admin_project_names=[],
                knowledge_bases=[],
                is_admin=True,
            )
            mock_finalize.return_value = expected_user

            # Act
            result = await AuthenticationService.authenticate_dev_header(user_id)

            # Assert
            assert result == expected_user
            mock_user_repo.acreate.assert_called_once()
            created_call = mock_user_repo.acreate.call_args[0][1]
            assert created_call.id == user_id
            assert created_call.auth_source == "dev_header"
            assert created_call.is_admin is True
            mock_logger.info.assert_called_once()
            assert f"user_id={user_id}" in mock_logger.info.call_args[0][0]


class TestAuthenticatePersistentUserRaceCondition:
    """Tests for race condition handling in authenticate_persistent_user()"""

    @pytest.mark.asyncio
    async def test_authenticate_persistent_user_race_condition_handled(self):
        """Handles race condition when user created between check and create"""
        # Arrange
        user_id = str(uuid4())
        auth_token = "token-123"

        idp_user = security_user.User(
            id=user_id,
            username="newuser",
            name="New User",
            email="new@example.com",
            user_type="human",
            roles=[],
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
            is_admin=False,
        )

        created_user = UserDB(
            id=user_id,
            email="new@example.com",
            username="newuser",
            name="New User",
            is_active=True,
            deleted_at=None,
        )

        mock_session = AsyncMock()

        with (
            patch("codemie.clients.postgres.get_async_session") as mock_get_session,
            patch("codemie.service.user.authentication_service.user_repository") as mock_user_repo,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._create_first_login_user",
                new_callable=AsyncMock,
            ) as mock_create,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._sync_existing_user",
                new_callable=AsyncMock,
            ) as mock_sync,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService._finalize_authentication",
                new_callable=AsyncMock,
            ) as mock_finalize,
            patch("codemie.service.user.authentication_service.config") as mock_config,
            patch("codemie.service.user.authentication_service.logger"),
        ):
            mock_config.IDP_PROVIDER = "keycloak"
            mock_get_session.return_value = _make_async_session_cm(mock_session)

            # First call: user doesn't exist
            # Second call (after IntegrityError): user exists
            mock_user_repo.aget_by_id = AsyncMock(side_effect=[None, created_user])

            # Simulate IntegrityError on create
            mock_create.side_effect = IntegrityError("duplicate key", params=None, orig=Exception("unique violation"))
            mock_sync.return_value = "new@example.com"

            expected_user = security_user.User(
                id=user_id,
                username="newuser",
                name="New User",
                email="new@example.com",
                user_type="human",
                roles=[],
                project_names=[],
                admin_project_names=[],
                knowledge_bases=[],
                is_admin=False,
                auth_token=auth_token,
            )
            mock_finalize.return_value = expected_user

            # Act
            result = await AuthenticationService.authenticate_persistent_user(user_id, idp_user, auth_token)

            # Assert
            assert result == expected_user
            # Verify rollback was called
            mock_session.rollback.assert_called_once()
            # Verify user was fetched again after rollback
            assert mock_user_repo.aget_by_id.call_count == 2
            # Verify sync was called
            mock_sync.assert_called_once()


class TestAuthenticateAndLogin:
    """Tests for authenticate_and_login() method"""

    @pytest.mark.asyncio
    async def test_authenticate_and_login_success(self):
        """Complete login flow returns token and user detail"""
        # Arrange
        email = "test@example.com"
        password = "ValidPassword123"
        user_id = str(uuid4())

        mock_user = UserDB(
            id=user_id,
            username="testuser",
            email=email,
            name="Test User",
            picture="https://example.com/pic.jpg",
            user_type="human",
            is_active=True,
            is_admin=False,
            auth_source="local",
            email_verified=True,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$hash",
            last_login_at=None,
            project_limit=10,
        )

        mock_projects = [
            UserProject(user_id=user_id, project_name="project1", is_project_admin=True),
        ]

        mock_session = AsyncMock()
        access_token = "jwt-token-123"

        with (
            patch("codemie.clients.postgres.get_async_session") as mock_get_session,
            patch(
                "codemie.service.user.authentication_service.AuthenticationService.authenticate_local",
                new_callable=AsyncMock,
            ) as mock_auth,
            patch("codemie.service.user.authentication_service.user_project_repository") as mock_proj_repo,
            patch("codemie.service.project.personal_project_service.personal_project_service") as mock_personal,
            patch("codemie.rest_api.security.jwt_local.generate_access_token") as mock_gen_token,
        ):
            mock_get_session.return_value = _make_async_session_cm(mock_session)
            mock_auth.return_value = mock_user
            mock_proj_repo.aget_by_user_id = AsyncMock(return_value=mock_projects)
            mock_personal.ensure_personal_project_async = AsyncMock()
            mock_gen_token.return_value = access_token

            # Act
            result = await AuthenticationService.authenticate_and_login(email, password)

            # Assert
            assert "access_token" in result
            assert result["access_token"] == access_token
            assert "user" in result
            assert result["user"].id == user_id
            assert result["user"].email == email
            assert len(result["user"].projects) == 1
            assert result["user"].projects[0].name == "project1"
            assert result["user"].projects[0].is_project_admin is True

            # Verify flow
            mock_auth.assert_called_once_with(mock_session, email, password)
            mock_proj_repo.aget_by_user_id.assert_called_once_with(mock_session, user_id)
            mock_personal.ensure_personal_project_async.assert_called_once_with(user_id, email)
            mock_gen_token.assert_called_once_with(user_id, email, "local")
