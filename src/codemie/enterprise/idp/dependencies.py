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

"""Enterprise IDP dependencies — adapter and registration.

Bridges enterprise IDP providers (zero-coupling) to base codemie's
BaseIdp interface. Follows the same pattern as plugin/dependencies.py.
"""

from __future__ import annotations

from codemie.configs.logger import logger
from codemie.enterprise.loader import HAS_IDP


def is_enterprise_idp_available() -> bool:
    """Check if enterprise IDP providers are available."""
    return HAS_IDP


def _wrap_enterprise_idp(enterprise_provider_class, provider_name: str):
    """Create a BaseIdp wrapper class for an enterprise IDP provider.

    The wrapper:
    - Extends BaseIdp (satisfies the factory contract)
    - Delegates to the enterprise provider (zero-coupling maintained)
    - Converts enterprise IdpUser → codemie User
    - Converts enterprise exceptions → ExtendedHTTPException

    Args:
        enterprise_provider_class: Enterprise IDP class (KeycloakIdpProvider or OidcIdpProvider)
        provider_name: Human-readable name for logging

    Returns:
        A new class that extends BaseIdp and wraps the enterprise provider
    """
    from fastapi import Request, status

    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.rest_api.security.idp.base import BaseIdp
    from codemie.rest_api.security.user import User

    class EnterpriseIdpWrapper(BaseIdp):
        """Adapter: wraps enterprise IDP provider as BaseIdp."""

        def __init__(self):
            self._provider = enterprise_provider_class()

        def get_session_cookie(self) -> str:
            return self._provider.get_session_cookie()

        async def authenticate(self, request: Request) -> User:
            """Authenticate via enterprise provider and map to core User.

            Converts:
            - enterprise IdpUser → codemie User
            - enterprise AuthenticationError → ExtendedHTTPException(401)
            - enterprise InvalidUserTypeError → ExtendedHTTPException(401)
            """
            from codemie_enterprise.idp.utils import AuthenticationError
            from codemie_enterprise.idp.user_type import InvalidUserTypeError

            try:
                # Extract headers as plain dict for enterprise provider
                headers = dict(request.headers)
                idp_user = self._provider.authenticate(headers)

                # Map enterprise IdpUser → codemie User
                return User(
                    id=idp_user.id,
                    username=idp_user.username,
                    name=idp_user.name,
                    email=idp_user.email,
                    roles=list(idp_user.roles),
                    project_names=list(idp_user.project_names),
                    admin_project_names=list(idp_user.admin_project_names),
                    knowledge_bases=list(idp_user.knowledge_bases),
                    picture=idp_user.picture,
                    user_type=idp_user.user_type,
                    auth_token=idp_user.auth_token,
                )
            except InvalidUserTypeError as e:
                logger.error(f"{provider_name} user type validation failed: {e}", exc_info=True)
                raise ExtendedHTTPException(
                    code=status.HTTP_401_UNAUTHORIZED,
                    message=(
                        f"Invalid user_type attribute from IDP. "
                        f"Expected 'regular' or 'external', got: {repr(e.value)}"
                    ),
                    details=e.detail,
                    help=e.help_text,
                )
            except AuthenticationError as e:
                logger.warning(f"{provider_name} authentication failed: {e}")
                raise ExtendedHTTPException(
                    code=status.HTTP_401_UNAUTHORIZED,
                    message="Authentication failed",
                    details="No valid authentication credentials were provided with this request.",
                    help=(
                        "Please ensure you're logged in and your session hasn't expired. "
                        "If you're using an API key, make sure it's valid and correctly "
                        "included in the request headers."
                    ),
                )
            except ExtendedHTTPException:
                raise
            except Exception as e:
                logger.error(f"{provider_name} authentication unexpected error: {type(e).__name__}: {e}", exc_info=True)
                raise ExtendedHTTPException(
                    code=status.HTTP_401_UNAUTHORIZED,
                    message="Authentication failed",
                    details="No valid authentication credentials were provided with this request.",
                    help=(
                        "Please ensure you're logged in and your session hasn't expired. "
                        "If you're using an API key, make sure it's valid and correctly "
                        "included in the request headers."
                    ),
                )

    # Set a meaningful class name for debugging
    EnterpriseIdpWrapper.__name__ = f"Enterprise{provider_name}Idp"
    EnterpriseIdpWrapper.__qualname__ = f"Enterprise{provider_name}Idp"

    return EnterpriseIdpWrapper


def register_enterprise_idps() -> None:
    """Register enterprise IDP providers with the IdpFactory.

    Called during application startup (main.py lifespan).
    Safe to call when enterprise package is not installed (no-op).

    This function:
    1. Checks if enterprise IDP module is available
    2. Creates BaseIdp wrapper classes for each enterprise provider
    3. Registers them with IdpFactory via IdpFactory.register()
    """
    if not HAS_IDP:
        logger.info("Enterprise IDP providers not available, using base IDPs only")
        return

    try:
        from codemie_enterprise.idp import get_available_providers

        from codemie.rest_api.security.idp.factory import IdpFactory

        providers = get_available_providers()
        registered = []

        for provider_type, provider_class, display_name in providers:
            wrapper = _wrap_enterprise_idp(provider_class, display_name)
            IdpFactory.register(provider_type, wrapper)
            registered.append(provider_type)

        logger.info(f"Enterprise IDP providers registered: {', '.join(registered)}")

    except Exception as e:
        logger.error(f"Failed to register enterprise IDP providers: {e}", exc_info=True)
