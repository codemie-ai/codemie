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

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_providers.persistent import (
    PersistentUserProvider,
    _extract_local_auth_token,
)


class TestExtractLocalAuthToken:
    """Test suite for _extract_local_auth_token helper function"""

    @patch("codemie.rest_api.security.user_providers.persistent.config")
    def test_extract_token_from_bearer_header(self, mock_config):
        """Test extracting JWT from Authorization Bearer header"""
        # Arrange
        mock_config.AUTH_COOKIE_NAME = "auth_token"
        mock_request = MagicMock()
        mock_request.headers.get.return_value = "Bearer test-jwt-token-123"
        mock_request.cookies.get.return_value = None

        # Act
        token = _extract_local_auth_token(mock_request)

        # Assert
        assert token == "test-jwt-token-123"

    @patch("codemie.rest_api.security.user_providers.persistent.config")
    def test_extract_token_from_cookie(self, mock_config):
        """Test extracting JWT from cookie when no header present"""
        # Arrange
        mock_config.AUTH_COOKIE_NAME = "auth_token"
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None
        mock_request.cookies.get.return_value = "cookie-jwt-token-456"

        # Act
        token = _extract_local_auth_token(mock_request)

        # Assert
        assert token == "cookie-jwt-token-456"

    @patch("codemie.rest_api.security.user_providers.persistent.config")
    def test_extract_token_both_present_raises_401(self, mock_config):
        """Test that having both header and cookie raises 401 (ambiguous auth)"""
        # Arrange
        mock_config.AUTH_COOKIE_NAME = "auth_token"
        mock_request = MagicMock()
        mock_request.headers.get.return_value = "Bearer header-token"
        mock_request.cookies.get.return_value = "cookie-token"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _extract_local_auth_token(mock_request)

        assert exc_info.value.code == 401
        assert "ambiguous" in exc_info.value.message.lower()

    @patch("codemie.rest_api.security.user_providers.persistent.config")
    def test_extract_token_neither_present_raises_401(self, mock_config):
        """Test that missing both header and cookie raises 401"""
        # Arrange
        mock_config.AUTH_COOKIE_NAME = "auth_token"
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None
        mock_request.cookies.get.return_value = None

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _extract_local_auth_token(mock_request)

        assert exc_info.value.code == 401
        assert "authentication required" in exc_info.value.message.lower()

    @patch("codemie.rest_api.security.user_providers.persistent.config")
    def test_extract_token_non_bearer_header_falls_to_cookie(self, mock_config):
        """Test that non-Bearer Authorization header is ignored, falls back to cookie"""
        # Arrange
        mock_config.AUTH_COOKIE_NAME = "auth_token"
        mock_request = MagicMock()
        mock_request.headers.get.return_value = "Basic some-credentials"
        mock_request.cookies.get.return_value = "cookie-fallback-token"

        # Act
        token = _extract_local_auth_token(mock_request)

        # Assert
        assert token == "cookie-fallback-token"


class TestPersistentUserProvider:
    """Test suite for PersistentUserProvider authentication flow"""

    @pytest.fixture
    def provider(self):
        """Create PersistentUserProvider instance"""
        return PersistentUserProvider()

    @pytest.fixture
    def mock_request(self):
        """Create mock FastAPI Request"""
        request = MagicMock()
        request.headers.get = MagicMock(return_value=None)
        request.cookies.get = MagicMock(return_value=None)
        return request

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user_providers.persistent.authentication_service")
    @patch("codemie.rest_api.security.user_providers.persistent.config")
    async def test_authenticate_dev_header_local_env(self, mock_config, mock_auth_service, provider, mock_request):
        """Test dev header authentication in local environment"""
        # Arrange
        mock_config.ENV = "local"
        dev_user_id = "dev-user-123"
        mock_request.headers.get.side_effect = lambda key: dev_user_id if key == "user-id" else None

        expected_user = User(
            id=dev_user_id,
            email="dev@example.com",
            name="Dev User",
            auth_token="dev-token",
            is_admin=False,
            projects=[],
            knowledge_bases=[],
        )
        mock_auth_service.authenticate_dev_header = AsyncMock(return_value=expected_user)

        mock_idp = MagicMock()

        # Act
        result = await provider.authenticate_and_load_user(mock_request, mock_idp)

        # Assert
        assert result == expected_user
        mock_auth_service.authenticate_dev_header.assert_called_once_with(dev_user_id)

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user_providers.persistent.authentication_service")
    @patch("codemie.rest_api.security.jwt_local.validate_local_jwt")
    @patch("codemie.rest_api.security.user_providers.persistent.config")
    async def test_authenticate_local_jwt(
        self, mock_config, mock_validate_jwt, mock_auth_service, provider, mock_request
    ):
        """Test local JWT authentication flow"""
        # Arrange
        mock_config.ENV = "production"
        mock_config.IDP_PROVIDER = "local"
        mock_config.AUTH_COOKIE_NAME = "auth_token"

        jwt_token = "local.jwt.token"
        user_id = "uuid-user-789"

        mock_request.headers.get.return_value = f"Bearer {jwt_token}"
        mock_request.cookies.get.return_value = None

        mock_validate_jwt.return_value = {
            "sub": user_id,
            "email": "user@example.com",
            "iss": "codemie-local",
        }

        expected_user = User(
            id=user_id,
            email="user@example.com",
            name="JWT User",
            auth_token=jwt_token,
            is_admin=False,
            projects=[],
            knowledge_bases=[],
        )
        mock_auth_service.authenticate_persistent_user = AsyncMock(return_value=expected_user)

        mock_idp = MagicMock()

        # Act
        result = await provider.authenticate_and_load_user(mock_request, mock_idp)

        # Assert
        assert result == expected_user
        mock_validate_jwt.assert_called_once_with(jwt_token)
        mock_auth_service.authenticate_persistent_user.assert_called_once_with(
            user_id=user_id, idp_user=None, auth_token=jwt_token
        )

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user_providers.persistent.authentication_service")
    @patch("codemie.rest_api.security.user_providers.persistent.config")
    async def test_authenticate_idp(self, mock_config, mock_auth_service, provider, mock_request):
        """Test IDP authentication flow (Keycloak/OIDC)"""
        # Arrange
        mock_config.ENV = "production"
        mock_config.IDP_PROVIDER = "keycloak"

        idp_user = User(
            id="idp-user-123",
            email="idp@example.com",
            name="IDP User",
            auth_token="idp-bearer-token",
            is_admin=False,
            projects=[],
            knowledge_bases=[],
        )

        mock_idp = MagicMock()
        mock_idp.authenticate = AsyncMock(return_value=idp_user)

        expected_user = User(
            id="idp-user-123",
            email="idp@example.com",
            name="IDP User",
            auth_token="idp-bearer-token",
            is_admin=False,
            projects=[],
            knowledge_bases=[],
        )
        mock_auth_service.authenticate_persistent_user = AsyncMock(return_value=expected_user)

        # Act
        result = await provider.authenticate_and_load_user(mock_request, mock_idp)

        # Assert
        assert result == expected_user
        mock_idp.authenticate.assert_called_once_with(mock_request)
        mock_auth_service.authenticate_persistent_user.assert_called_once_with(
            user_id="idp-user-123", idp_user=idp_user, auth_token="idp-bearer-token"
        )

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user_providers.persistent.authentication_service")
    @patch("codemie.rest_api.security.jwt_local.validate_local_jwt")
    @patch("codemie.rest_api.security.user_providers.persistent.config")
    async def test_authenticate_no_dev_header_non_local_env(
        self, mock_config, mock_validate_jwt, mock_auth_service, provider, mock_request
    ):
        """Test that dev header is ignored in non-local environments"""
        # Arrange
        mock_config.ENV = "production"  # Not 'local'
        mock_config.IDP_PROVIDER = "local"
        mock_config.AUTH_COOKIE_NAME = "auth_token"

        jwt_token = "prod.jwt.token"
        user_id = "prod-user-456"

        # Dev header present but should be ignored, Authorization header has JWT
        mock_request.headers.get.side_effect = lambda key: (
            "dev-user-id" if key == "user-id" else f"Bearer {jwt_token}" if key == "Authorization" else None
        )
        mock_request.cookies.get.return_value = None

        mock_validate_jwt.return_value = {
            "sub": user_id,
            "email": "prod@example.com",
            "iss": "codemie-local",
        }

        expected_user = User(
            id=user_id,
            email="prod@example.com",
            name="Prod User",
            auth_token=jwt_token,
            is_admin=False,
            projects=[],
            knowledge_bases=[],
        )
        mock_auth_service.authenticate_persistent_user = AsyncMock(return_value=expected_user)

        mock_idp = MagicMock()

        # Act
        result = await provider.authenticate_and_load_user(mock_request, mock_idp)

        # Assert
        assert result == expected_user
        # Dev header authentication should NOT have been called
        assert not mock_auth_service.authenticate_dev_header.called

    @pytest.mark.asyncio
    @patch("codemie.rest_api.security.user_providers.persistent.authentication_service")
    @patch("codemie.rest_api.security.user_providers.persistent.config")
    async def test_authenticate_idp_with_missing_token(self, mock_config, mock_auth_service, provider, mock_request):
        """Test IDP authentication when idp_user.auth_token is None"""
        # Arrange
        mock_config.ENV = "production"
        mock_config.IDP_PROVIDER = "oidc"

        idp_user = User(
            id="idp-user-no-token",
            email="notoken@example.com",
            name="No Token User",
            auth_token=None,  # Missing token
            is_admin=False,
            projects=[],
            knowledge_bases=[],
        )

        mock_idp = MagicMock()
        mock_idp.authenticate = AsyncMock(return_value=idp_user)

        expected_user = User(
            id="idp-user-no-token",
            email="notoken@example.com",
            name="No Token User",
            auth_token="",  # Should default to empty string
            is_admin=False,
            projects=[],
            knowledge_bases=[],
        )
        mock_auth_service.authenticate_persistent_user = AsyncMock(return_value=expected_user)

        # Act
        result = await provider.authenticate_and_load_user(mock_request, mock_idp)

        # Assert
        assert result == expected_user
        mock_auth_service.authenticate_persistent_user.assert_called_once_with(
            user_id="idp-user-no-token", idp_user=idp_user, auth_token=""
        )
