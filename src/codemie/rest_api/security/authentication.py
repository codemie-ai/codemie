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

import secrets

from fastapi import Depends, Request, status
from fastapi.security import APIKeyHeader

from codemie.configs import config as _app_config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.idp.local import USER_ID_HEADER, LocalIdp
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_context import set_current_user, set_current_auth_token
from codemie.rest_api.security.user_providers import get_user_provider  # EPMCDME-10160
from codemie.rest_api.security.idp.factory import IdpFactory  # EPMCDME-10160

BEARER_AUTHORIZATION_HEADER = "Authorization"

ACCESS_DENIED_MESSAGE = "Access denied"
_ADMIN_OR_PROJECT_ADMIN_REQUIRED = "This action requires administrator or project administrator privileges."
_CONTACT_ADMIN_HELP = "If you believe you should have access, please contact your system administrator."
BIND_KEY_HEADER = "X-Bind-Key"

user_id_header = APIKeyHeader(name=USER_ID_HEADER, auto_error=False, scheme_name=USER_ID_HEADER)
bind_key_header = APIKeyHeader(name=BIND_KEY_HEADER, auto_error=False, scheme_name=BIND_KEY_HEADER)

__bind_key: str = _app_config.INTERNAL_BIND_KEY if _app_config.INTERNAL_BIND_KEY else secrets.token_hex(32)


def _create_auth_error(details: str) -> ExtendedHTTPException:
    """Create a standardized authentication error with helpful message."""
    return ExtendedHTTPException(
        code=status.HTTP_401_UNAUTHORIZED,
        message="Authentication failed",
        details=details,
        help=(
            "Please ensure you're logged in and your session hasn't expired. "
            "If you're using an API key, make sure it's valid and correctly included in the request headers."
        ),
    )


def get_bind_key() -> str:
    return __bind_key


async def authenticate(
    request: Request,
    internal_user_id: str | None = Depends(user_id_header),
    bind_key: str | None = Depends(bind_key_header),
) -> User:
    """Authenticate request and return User (EPMCDME-10160)

    Delegates to appropriate UserProvider based on ENABLE_USER_MANAGEMENT flag:
    - Flag OFF: LegacyJwtUserProvider (ephemeral, no DB)
    - Flag ON: PersistentUserProvider (database-backed)

    Args:
        request: FastAPI request object

    Returns:
        security.User object

    Raises:
        ExtendedHTTPException: On authentication failure
    """
    try:
        if bind_key is not None:
            # Internal service-to-service call (e.g. trigger actors calling loopback API)
            if bind_key != get_bind_key():
                raise _create_auth_error("Invalid internal service credentials.")
            if not internal_user_id:
                raise _create_auth_error("Missing user-id header for internal service authentication.")
            # Valid bind key — authenticate using the user-id header via LocalIdp
            user = await LocalIdp().authenticate(request)
        else:
            # Standard external authentication flow
            # 1. Get provider based on feature flag (EPMCDME-10160)
            provider = get_user_provider()

            # 2. Get IDP (use factory for consistency)
            idp = IdpFactory.create()

            # 3. Delegate authentication to provider
            # Note: Personal workspace creation is handled by provider
            # (LegacyJwtUserProvider for flag OFF, PersistentUserProvider for flag ON)
            user = await provider.authenticate_and_load_user(request, idp)

        # 4. Store in context (unchanged from current behavior)
        request.state.user = user
        set_current_user(user)
        if user.auth_token:
            set_current_auth_token(user.auth_token)

        return user

    except Exception as e:
        if isinstance(e, ExtendedHTTPException):
            logger.warning(f"Authentication failed with HTTP {e.code}: {e.message}", exc_info=True)
            raise e
        # Do not leak internal exception details in API response
        logger.error(f"Authentication failed with unexpected error: {type(e).__name__}: {e}", exc_info=True)
        raise _create_auth_error("No valid authentication credentials were provided with this request.")


async def admin_access_only(request: Request):
    """
    Checks if current user is admin or maintainer
    """
    if not request.state.user.is_admin_or_maintainer:
        logger.warning("Access denied: admin or maintainer privileges required")
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details="This action requires administrator or maintainer privileges.",
            help="If you believe you should have elevated access, please contact your"
            " system administrator or check your account settings.",
        )


async def maintainer_access_only(request: Request):
    """Checks if current user is maintainer."""
    if not request.state.user.is_maintainer:
        logger.warning("Access denied: maintainer privileges required")
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details="This action requires maintainer privileges.",
            help="If you believe you should have maintainer access, please contact your system administrator.",
        )


async def project_admin_or_admin_user_detail_access(request: Request):
    """Check if user is admin or project admin with access to target user (Story 18)

    Authorization for user detail endpoint (GET /v1/admin/users/{user_id}).

    Authorization logic:
    - Admins: Full access to any user
    - Project admins: Can view users who exist in projects they admin
    - Regular users: Denied (403)

    Note: User existence is checked in the service layer (returns 404 if not found).
    This dependency focuses on authorization (403) for existing users.

    Args:
        request: FastAPI request with authenticated user in state

    Raises:
        ExtendedHTTPException: 403 if user lacks required privileges or target user not in admin's projects

    Returns:
        None if authorized
    """
    from codemie.clients.postgres import get_session
    from codemie.repository.user_project_repository import user_project_repository
    from codemie.repository.user_repository import user_repository

    user = request.state.user

    # Admins and maintainers have full access
    if user.is_admin_or_maintainer:
        return

    # Project admins need to check if target user is in projects they admin
    if user.is_applications_admin:
        # Extract target_user_id from path parameters
        target_user_id = request.path_params.get("user_id")
        if not target_user_id:
            logger.warning("Access denied: missing target user_id in path for user detail access")
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=ACCESS_DENIED_MESSAGE,
                details=_ADMIN_OR_PROJECT_ADMIN_REQUIRED,
                help=_CONTACT_ADMIN_HELP,
            )

        # Check if target user exists first (to distinguish 404 from 403)
        with get_session() as session:
            target_user_exists = user_repository.get_by_id(session, target_user_id) is not None

            if not target_user_exists:
                # User doesn't exist - let service layer return 404
                # We return here to allow the request to proceed
                return

            # User exists - check if project admin can view them
            can_view = user_project_repository.can_project_admin_view_user(session, user.id, target_user_id)

        if can_view:
            return

        # Project admin cannot view this user (user exists but not in their projects)
        logger.warning("Access denied: project admin cannot view user outside their projects")
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="User not found in your projects",
            details="You can only view details for users who are members of projects you administer.",
            help="Contact an administrator if you need access to this user's information.",
        )

    # Regular users are denied
    logger.warning("Access denied: admin or project admin privileges required for user detail")
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message=ACCESS_DENIED_MESSAGE,
        details=_ADMIN_OR_PROJECT_ADMIN_REQUIRED,
        help=_CONTACT_ADMIN_HELP,
    )


def application_access_check(request: Request, app_name: str):
    """
    Checks if current user has access to application
    """
    if not request.state.user.has_access_to_application(app_name):
        logger.warning(f"Access denied: user lacks access to application '{app_name}'")
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details=f"You do not have permission to access the application '{app_name}'.",
            help="If you believe you should have access to this application, "
            "please contact your system administrator or the application owner.",
        )


def project_access_check(user: User, project_name: str):
    if not user.has_access_to_application(project_name):
        logger.warning(f"Access denied: user lacks access to project '{project_name}'")
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details=f"You do not have permission to access the project '{project_name}'.",
            help="If you believe you should have access to this project, "
            "please contact your system administrator or the project owner.",
        )


def kb_access_check(request: Request, name: str):
    """
    Checks if current user has access to knowledge base
    """
    if not request.state.user.has_access_to_kb(name):
        logger.warning(f"Access denied: user lacks access to knowledge base '{name}'")
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details=f"You do not have permission to access the knowledge base '{name}'.",
            help="If you believe you should have access to this knowledge base, "
            "please contact your system administrator or the knowledge base owner.",
        )
