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

from fastapi import APIRouter, Request, Response, Depends
from pydantic import BaseModel

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.rate_limit import limiter
from codemie.rest_api.models.user_management import (
    RegistrationRequest,
    LoginRequest,
    PasswordResetRequest,
    PasswordChangeRequest,
    ForgotPasswordRequest,
    VerifyEmailRequest,
    CodeMieUserDetail,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.jwt_local import get_public_jwks
from codemie.rest_api.security.user import User
from codemie.service.user.authentication_service import authentication_service
from codemie.service.user.registration_service import registration_service
from codemie.service.user.password_management_service import password_management_service


router = APIRouter(
    tags=["local-auth"],
    prefix="/v1/local-auth",
    dependencies=[],  # Public endpoints, auth handled per-endpoint where needed
)


class MessageResponse(BaseModel):
    """Simple message response"""

    message: str


class TokenResponse(BaseModel):
    """Access token response"""

    access_token: str
    user: CodeMieUserDetail


def _set_auth_cookie(response: Response, token: str) -> None:
    """Set the authentication cookie on the response."""
    response.set_cookie(
        key=config.AUTH_COOKIE_NAME,
        value=token,
        httponly=config.AUTH_COOKIE_HTTPONLY,
        secure=config.AUTH_COOKIE_SECURE,
        samesite=config.AUTH_COOKIE_SAMESITE,
        path=config.AUTH_COOKIE_PATH,
        max_age=config.JWT_EXPIRATION_HOURS * 3600,
    )


# ===========================================
# Public Endpoints (No Auth Required)
# ===========================================


@router.post("/register", response_model=MessageResponse | TokenResponse)
@limiter.limit("3/hour")
async def register(request: Request, response: Response, data: RegistrationRequest):
    """Register a new local user

    Rate limit: 3/hour

    Returns:
    - EMAIL_VERIFICATION_ENABLED=True: {message} and sends verification email
    - EMAIL_VERIFICATION_ENABLED=False: {access_token, user} (instant login)
    """
    # Check feature flag
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message="User registration not available")

    # Check local auth mode
    if config.IDP_PROVIDER != "local":
        raise ExtendedHTTPException(code=400, message="Registration only available in local auth mode")

    # Delegate to service layer
    result = await registration_service.register_user_with_flow(
        email=data.email, username=data.username, password=data.password, name=data.name
    )

    # Return appropriate response based on result type
    if result["type"] == "message":
        return MessageResponse(message=result["message"])
    else:
        _set_auth_cookie(response, result["access_token"])
        return TokenResponse(access_token=result["access_token"], user=result["user"])


class VerifyEmailResponse(BaseModel):
    """Email verification response (matches spec)"""

    message: str
    access_token: str


@router.post("/verify-email", response_model=VerifyEmailResponse)
@limiter.limit("10/hour")
async def verify_email(request: Request, response: Response, data: VerifyEmailRequest):
    """Verify email with token

    Rate limit: 10/hour
    """
    # Check feature flag
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message="Email verification not available")

    # Check local auth mode
    if config.IDP_PROVIDER != "local":
        raise ExtendedHTTPException(code=400, message="Email verification only available in local auth mode")

    # Delegate to service layer
    result = registration_service.verify_email_and_login(data.token)

    _set_auth_cookie(response, result["access_token"])
    return VerifyEmailResponse(message=result["message"], access_token=result["access_token"])


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/15minutes")
async def login(request: Request, response: Response, data: LoginRequest):
    """Login with email and password

    Rate limit: 5/15min
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message="Local login not available")

    if config.IDP_PROVIDER != "local":
        raise ExtendedHTTPException(code=400, message="Login only available in local auth mode")

    # Delegate to service layer
    result = await authentication_service.authenticate_and_login(data.email, data.password)

    _set_auth_cookie(response, result["access_token"])
    return TokenResponse(access_token=result["access_token"], user=result["user"])


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("3/hour")
async def forgot_password(request: Request, data: ForgotPasswordRequest):
    """Request password reset

    Rate limit: 3/hour

    Always returns success for privacy (doesn't reveal if email exists)
    """
    # Check feature flag
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message="Password reset not available")

    # Check local auth mode
    if config.IDP_PROVIDER != "local":
        raise ExtendedHTTPException(code=400, message="Password reset only available in local auth mode")

    # Delegate to service layer
    result = await password_management_service.request_password_reset_flow(data.email)

    return MessageResponse(message=result["message"])


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit("5/hour")
async def reset_password(request: Request, data: PasswordResetRequest):
    """Reset password with token

    Rate limit: 5/hour
    """
    # Check feature flag
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message="Password reset not available")

    # Check local auth mode
    if config.IDP_PROVIDER != "local":
        raise ExtendedHTTPException(code=400, message="Password reset only available in local auth mode")

    # Delegate to service layer
    result = password_management_service.reset_password_with_token(data.token, data.new_password)

    return MessageResponse(message=result["message"])


@router.post("/change-password", response_model=MessageResponse)
async def change_password(data: PasswordChangeRequest, user: User = Depends(authenticate)):
    """Change password for authenticated user

    Requires current password for verification.
    """
    # Check feature flag
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message="Password change not available")

    # Check local auth mode
    if config.IDP_PROVIDER != "local":
        raise ExtendedHTTPException(code=400, message="Password change only available in local auth mode")

    # Delegate to service layer
    result = password_management_service.change_password_authenticated(
        user_id=user.id, current_password=data.current_password, new_password=data.new_password
    )

    return MessageResponse(message=result["message"])


@router.post("/logout", response_model=MessageResponse)
async def logout(response: Response, _user: User = Depends(authenticate)):
    """Logout and clear the authentication cookie

    Requires authentication (via cookie or Authorization header).
    """
    response.delete_cookie(
        key=config.AUTH_COOKIE_NAME,
        path=config.AUTH_COOKIE_PATH,
    )
    return MessageResponse(message="Logged out successfully")


@router.get("/.well-known/jwks.json")
async def get_jwks():
    """Get public keys in JWKS format for JWT validation

    This endpoint is public (no authentication required) and provides
    the public key used to validate local JWTs.
    """
    return get_public_jwks()
