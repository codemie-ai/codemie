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

from codemie.configs.logger import logger
from codemie.rest_api.security.user_context import get_current_auth_token, get_current_user
from codemie.service.security.token_providers.base_provider import (
    BaseTokenProvider,
    TokenProviderException,
)


class ContextTokenProvider(BaseTokenProvider):
    """
    Phase 1 token provider that retrieves tokens from request context.

    This provider uses Python's ContextVar mechanism to retrieve the
    authentication token that was set during request authentication.
    The token is stored in a request-scoped context variable that is
    automatically isolated per request.

    Security:
        - Tokens stored in request-scoped ContextVar
        - Never logged or exposed in error messages
        - Automatically isolated per request (thread-safe and async-safe)

    Usage:
        This is the default provider for Phase 1 implementation.
        It retrieves the user's JWT token that was set during authentication.
    """

    def get_token(self) -> str | None:
        """
        Retrieve token from request context using ContextVar.

        Implementation:
            1. Call get_current_auth_token() to retrieve token from ContextVar
            2. Log success/failure status (NOT the token value)
            3. Return token or None

        Returns:
            JWT authentication token if available in context, None otherwise

        Raises:
            TokenProviderException: If token retrieval fails due to unexpected errors

        Security:
            - NEVER logs the token value
            - Only logs operation status
            - Exception messages are generic
        """
        try:
            # Get user_id for logging if available
            current_user = get_current_user()
            user_id = current_user.id if current_user else 'unknown'

            token = get_current_auth_token()

            if token:
                logger.debug(f"Retrieved auth token from context for user_id={user_id}")
            else:
                logger.debug(f"No auth token found in context for user_id={user_id}")

            return token

        except Exception as e:
            # Get user_id for error logging if available
            current_user = get_current_user()
            user_id = current_user.id if current_user else 'unknown'

            error_msg = f"Failed to retrieve token from context for user_id={user_id}"
            logger.exception(f"{error_msg}: {type(e).__name__}")
            raise TokenProviderException(message=error_msg, details=f"Error type: {type(e).__name__}") from e

    def get_provider_name(self) -> str:
        """Get provider name for logging."""
        return "ContextTokenProvider"
