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

"""Unit tests for local_auth_router.py

Tests all public endpoints for local authentication including registration,
login, email verification, password management, logout, and JWKS endpoint.

Coverage target: >= 80%

Note: Rate limiter is disabled using conftest.py fixture.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Response
from starlette.requests import Request

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_management import (
    CodeMieUserDetail,
    ForgotPasswordRequest,
    LoginRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
    RegistrationRequest,
    VerifyEmailRequest,
)
from codemie.rest_api.routers.local_auth_router import (
    _set_auth_cookie,
    change_password,
    forgot_password,
    get_jwks,
    login,
    logout,
    register,
    reset_password,
    router,
    verify_email,
)
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_request():
    """Mock FastAPI Request object as a proper Starlette Request"""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 8000),
        "server": ("localhost", 8000),
    }
    return Request(scope)


@pytest.fixture
def mock_response():
    """Mock FastAPI Response object with mocked cookie methods for testing"""
    response = Response()
    # Mock the cookie methods so we can assert on them
    response.set_cookie = MagicMock()
    response.delete_cookie = MagicMock()
    return response


@pytest.fixture
def mock_user():
    """Mock authenticated user"""
    return User(
        id="user-123",
        email="user@example.com",
        username="user123",
        name="Test User",
        is_super_admin=False,
        project_names=["project-a"],
        admin_project_names=[],
    )


@pytest.fixture
def mock_user_detail():
    """Mock CodeMieUserDetail response"""
    return CodeMieUserDetail(
        id="user-123",
        username="user123",
        email="user@example.com",
        name="Test User",
        picture=None,
        user_type="regular",
        is_active=True,
        is_super_admin=False,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        projects=[],
        project_limit=None,
        knowledge_bases=[],
        date=None,
        update_date=None,
        deleted_at=None,
    )


class TestSetAuthCookie:
    """Test _set_auth_cookie helper function"""

    @patch("codemie.rest_api.routers.local_auth_router.config")
    def test_set_auth_cookie_calls_set_cookie_with_correct_params(self, mock_config):
        """Verify cookie is set with correct parameters from config"""
        # Arrange
        mock_config.AUTH_COOKIE_NAME = "test_cookie"
        mock_config.AUTH_COOKIE_HTTPONLY = True
        mock_config.AUTH_COOKIE_SECURE = False
        mock_config.AUTH_COOKIE_SAMESITE = "lax"
        mock_config.AUTH_COOKIE_PATH = "/"
        mock_config.JWT_EXPIRATION_HOURS = 24
        token = "test-token-123"

        # Create a mock response for verification
        mock_resp = MagicMock()

        # Act
        _set_auth_cookie(mock_resp, token)

        # Assert
        mock_resp.set_cookie.assert_called_once_with(
            key="test_cookie",
            value=token,
            httponly=True,
            secure=False,
            samesite="lax",
            path="/",
            max_age=24 * 3600,
        )


class TestRegisterEndpoint:
    """Test POST /register endpoint"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    @patch("codemie.rest_api.routers.local_auth_router.registration_service")
    async def test_register_success_with_email_verification(
        self, mock_service, mock_config, mock_request, mock_response
    ):
        """Register success with email verification enabled returns message"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        # Use AsyncMock for async service methods
        mock_service.register_user_with_flow = AsyncMock(
            return_value={
                "type": "message",
                "message": "Verification email sent",
            }
        )
        data = RegistrationRequest(
            email="new@example.com", username="newuser", password="securepass123456", name="New User"
        )

        # Act
        result = await register(mock_request, mock_response, data)

        # Assert
        assert result.message == "Verification email sent"
        mock_service.register_user_with_flow.assert_called_once_with(
            email="new@example.com", username="newuser", password="securepass123456", name="New User"
        )
        mock_response.set_cookie.assert_not_called()

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    @patch("codemie.rest_api.routers.local_auth_router.registration_service")
    async def test_register_success_instant_login(
        self, mock_service, mock_config, mock_request, mock_response, mock_user_detail
    ):
        """Register success with email verification disabled returns token and sets cookie"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        mock_config.AUTH_COOKIE_NAME = "codemie_access_token"
        mock_config.AUTH_COOKIE_HTTPONLY = True
        mock_config.AUTH_COOKIE_SECURE = False
        mock_config.AUTH_COOKIE_SAMESITE = "lax"
        mock_config.AUTH_COOKIE_PATH = "/"
        mock_config.JWT_EXPIRATION_HOURS = 24

        # Use AsyncMock for async service methods
        mock_service.register_user_with_flow = AsyncMock(
            return_value={
                "type": "token",
                "access_token": "token-123",
                "user": mock_user_detail,
            }
        )
        data = RegistrationRequest(
            email="new@example.com", username="newuser", password="securepass123456", name="New User"
        )

        # Act
        result = await register(mock_request, mock_response, data)

        # Assert
        assert result.access_token == "token-123"
        assert result.user == mock_user_detail
        mock_response.set_cookie.assert_called_once()
        call_kwargs = mock_response.set_cookie.call_args[1]
        assert call_kwargs["key"] == "codemie_access_token"
        assert call_kwargs["value"] == "token-123"

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_register_disabled_feature_flag(self, mock_config, mock_request, mock_response):
        """Register fails when ENABLE_USER_MANAGEMENT=False"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False
        data = RegistrationRequest(
            email="new@example.com", username="newuser", password="securepass123456", name="New User"
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await register(mock_request, mock_response, data)

        assert exc_info.value.code == 400
        assert "User registration not available" in exc_info.value.message

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_register_non_local_idp(self, mock_config, mock_request, mock_response):
        """Register fails when IDP_PROVIDER is not local"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "keycloak"
        data = RegistrationRequest(
            email="new@example.com", username="newuser", password="securepass123456", name="New User"
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await register(mock_request, mock_response, data)

        assert exc_info.value.code == 400
        assert "Registration only available in local auth mode" in exc_info.value.message


class TestLoginEndpoint:
    """Test POST /login endpoint"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    @patch("codemie.rest_api.routers.local_auth_router.authentication_service")
    async def test_login_success(self, mock_service, mock_config, mock_request, mock_response, mock_user_detail):
        """Login success returns token, user, and sets cookie"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        mock_config.AUTH_COOKIE_NAME = "codemie_access_token"
        mock_config.AUTH_COOKIE_HTTPONLY = True
        mock_config.AUTH_COOKIE_SECURE = False
        mock_config.AUTH_COOKIE_SAMESITE = "lax"
        mock_config.AUTH_COOKIE_PATH = "/"
        mock_config.JWT_EXPIRATION_HOURS = 24

        # Use AsyncMock for async service methods
        mock_service.authenticate_and_login = AsyncMock(
            return_value={
                "access_token": "login-token-456",
                "user": mock_user_detail,
            }
        )
        data = LoginRequest(email="user@example.com", password="correctpassword")

        # Act
        result = await login(mock_request, mock_response, data)

        # Assert
        assert result.access_token == "login-token-456"
        assert result.user == mock_user_detail
        mock_service.authenticate_and_login.assert_called_once_with("user@example.com", "correctpassword")
        mock_response.set_cookie.assert_called_once()
        call_kwargs = mock_response.set_cookie.call_args[1]
        assert call_kwargs["key"] == "codemie_access_token"
        assert call_kwargs["value"] == "login-token-456"

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    @patch("codemie.rest_api.routers.local_auth_router.authentication_service")
    async def test_login_invalid_credentials(self, mock_service, mock_config, mock_request, mock_response):
        """Login with invalid credentials raises 401"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        mock_service.authenticate_and_login.side_effect = ExtendedHTTPException(code=401, message="Invalid credentials")
        data = LoginRequest(email="user@example.com", password="wrongpassword")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await login(mock_request, mock_response, data)

        assert exc_info.value.code == 401
        assert "Invalid credentials" in exc_info.value.message

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_login_disabled_feature_flag(self, mock_config, mock_request, mock_response):
        """Login fails when ENABLE_USER_MANAGEMENT=False"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False
        data = LoginRequest(email="user@example.com", password="password123456")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await login(mock_request, mock_response, data)

        assert exc_info.value.code == 400
        assert "Local login not available" in exc_info.value.message

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_login_non_local_idp(self, mock_config, mock_request, mock_response):
        """Login fails when IDP_PROVIDER is not local"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "oidc"
        data = LoginRequest(email="user@example.com", password="password123456")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await login(mock_request, mock_response, data)

        assert exc_info.value.code == 400
        assert "Login only available in local auth mode" in exc_info.value.message


class TestVerifyEmailEndpoint:
    """Test POST /verify-email endpoint"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    @patch("codemie.rest_api.routers.local_auth_router.registration_service")
    async def test_verify_email_success(self, mock_service, mock_config, mock_request, mock_response):
        """Verify email success returns token and sets cookie"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        mock_config.AUTH_COOKIE_NAME = "codemie_access_token"
        mock_config.AUTH_COOKIE_HTTPONLY = True
        mock_config.AUTH_COOKIE_SECURE = False
        mock_config.AUTH_COOKIE_SAMESITE = "lax"
        mock_config.AUTH_COOKIE_PATH = "/"
        mock_config.JWT_EXPIRATION_HOURS = 24

        mock_service.verify_email_and_login.return_value = {
            "message": "Email verified successfully",
            "access_token": "verify-token-789",
        }
        data = VerifyEmailRequest(token="verification-token-abc")

        # Act
        result = await verify_email(mock_request, mock_response, data)

        # Assert
        assert result.message == "Email verified successfully"
        assert result.access_token == "verify-token-789"
        mock_service.verify_email_and_login.assert_called_once_with("verification-token-abc")
        mock_response.set_cookie.assert_called_once()
        call_kwargs = mock_response.set_cookie.call_args[1]
        assert call_kwargs["key"] == "codemie_access_token"
        assert call_kwargs["value"] == "verify-token-789"

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_verify_email_disabled_feature_flag(self, mock_config, mock_request, mock_response):
        """Verify email fails when ENABLE_USER_MANAGEMENT=False"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False
        data = VerifyEmailRequest(token="verification-token-abc")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await verify_email(mock_request, mock_response, data)

        assert exc_info.value.code == 400
        assert "Email verification not available" in exc_info.value.message

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_verify_email_non_local_idp(self, mock_config, mock_request, mock_response):
        """Verify email fails when IDP_PROVIDER is not local"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "keycloak"
        data = VerifyEmailRequest(token="verification-token-abc")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await verify_email(mock_request, mock_response, data)

        assert exc_info.value.code == 400
        assert "Email verification only available in local auth mode" in exc_info.value.message


class TestForgotPasswordEndpoint:
    """Test POST /forgot-password endpoint"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    @patch("codemie.rest_api.routers.local_auth_router.password_management_service")
    async def test_forgot_password_success(self, mock_service, mock_config, mock_request):
        """Forgot password success returns message"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        # Use AsyncMock for async service methods
        mock_service.request_password_reset_flow = AsyncMock(
            return_value={"message": "Password reset email sent if account exists"}
        )
        data = ForgotPasswordRequest(email="user@example.com")

        # Act
        result = await forgot_password(mock_request, data)

        # Assert
        assert result.message == "Password reset email sent if account exists"
        mock_service.request_password_reset_flow.assert_called_once_with("user@example.com")

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_forgot_password_disabled_feature_flag(self, mock_config, mock_request):
        """Forgot password fails when ENABLE_USER_MANAGEMENT=False"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False
        data = ForgotPasswordRequest(email="user@example.com")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await forgot_password(mock_request, data)

        assert exc_info.value.code == 400
        assert "Password reset not available" in exc_info.value.message

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_forgot_password_non_local_idp(self, mock_config, mock_request):
        """Forgot password fails when IDP_PROVIDER is not local"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "oidc"
        data = ForgotPasswordRequest(email="user@example.com")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await forgot_password(mock_request, data)

        assert exc_info.value.code == 400
        assert "Password reset only available in local auth mode" in exc_info.value.message


class TestResetPasswordEndpoint:
    """Test POST /reset-password endpoint"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    @patch("codemie.rest_api.routers.local_auth_router.password_management_service")
    async def test_reset_password_success(self, mock_service, mock_config, mock_request):
        """Reset password success returns message"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        mock_service.reset_password_with_token.return_value = {"message": "Password reset successfully"}
        data = PasswordResetRequest(token="reset-token-xyz", new_password="newsecurepass456789")

        # Act
        result = await reset_password(mock_request, data)

        # Assert
        assert result.message == "Password reset successfully"
        mock_service.reset_password_with_token.assert_called_once_with("reset-token-xyz", "newsecurepass456789")

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_reset_password_disabled_feature_flag(self, mock_config, mock_request):
        """Reset password fails when ENABLE_USER_MANAGEMENT=False"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False
        data = PasswordResetRequest(token="reset-token-xyz", new_password="newsecurepass456789")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await reset_password(mock_request, data)

        assert exc_info.value.code == 400
        assert "Password reset not available" in exc_info.value.message

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_reset_password_non_local_idp(self, mock_config, mock_request):
        """Reset password fails when IDP_PROVIDER is not local"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "keycloak"
        data = PasswordResetRequest(token="reset-token-xyz", new_password="newsecurepass456789")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await reset_password(mock_request, data)

        assert exc_info.value.code == 400
        assert "Password reset only available in local auth mode" in exc_info.value.message


class TestChangePasswordEndpoint:
    """Test POST /change-password endpoint"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    @patch("codemie.rest_api.routers.local_auth_router.password_management_service")
    async def test_change_password_success(self, mock_service, mock_config, mock_user):
        """Change password success returns message"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "local"
        mock_service.change_password_authenticated.return_value = {"message": "Password changed successfully"}
        data = PasswordChangeRequest(current_password="OldTestPass123", new_password="NewTestPass123")

        # Act
        result = await change_password(data, user=mock_user)

        # Assert
        assert result.message == "Password changed successfully"
        mock_service.change_password_authenticated.assert_called_once_with(
            user_id="user-123", current_password="OldTestPass123", new_password="NewTestPass123"
        )

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_change_password_disabled_feature_flag(self, mock_config, mock_user):
        """Change password fails when ENABLE_USER_MANAGEMENT=False"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False
        data = PasswordChangeRequest(current_password="OldTestPass123", new_password="NewTestPass123")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await change_password(data, user=mock_user)

        assert exc_info.value.code == 400
        assert "Password change not available" in exc_info.value.message

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_change_password_non_local_idp(self, mock_config, mock_user):
        """Change password fails when IDP_PROVIDER is not local"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_config.IDP_PROVIDER = "oidc"
        data = PasswordChangeRequest(current_password="OldTestPass123", new_password="NewTestPass123")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await change_password(data, user=mock_user)

        assert exc_info.value.code == 400
        assert "Password change only available in local auth mode" in exc_info.value.message


class TestLogoutEndpoint:
    """Test POST /logout endpoint"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.config")
    async def test_logout_clears_cookie(self, mock_config, mock_response, mock_user):
        """Logout clears authentication cookie"""
        # Arrange
        mock_config.AUTH_COOKIE_NAME = "codemie_access_token"
        mock_config.AUTH_COOKIE_PATH = "/"

        # Act
        result = await logout(mock_response, _user=mock_user)

        # Assert
        assert result.message == "Logged out successfully"
        mock_response.delete_cookie.assert_called_once_with(
            key="codemie_access_token",
            path="/",
        )


class TestJWKSEndpoint:
    """Test GET /.well-known/jwks.json endpoint"""

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.local_auth_router.get_public_jwks")
    async def test_jwks_endpoint_returns_jwks_structure(self, mock_get_jwks):
        """JWKS endpoint returns public key structure"""
        # Arrange
        expected_jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "kid": "local-jwt-key",
                    "n": "test-modulus",
                    "e": "AQAB",
                }
            ]
        }
        mock_get_jwks.return_value = expected_jwks

        # Act
        result = await get_jwks()

        # Assert
        assert result == expected_jwks
        mock_get_jwks.assert_called_once()


class TestRouterConfiguration:
    """Test router configuration and dependencies"""

    def test_router_prefix_and_tags(self):
        """Verify router has correct prefix and tags"""
        assert router.prefix == "/v1/local-auth"
        assert "local-auth" in router.tags

    def test_public_endpoints_have_no_global_auth(self):
        """Verify public endpoints don't require authentication by default"""
        # Router dependencies should be empty (public endpoints)
        assert router.dependencies == []
