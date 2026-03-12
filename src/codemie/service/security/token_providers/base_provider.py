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

from abc import ABC, abstractmethod


class TokenProviderException(Exception):
    """Exception raised when token retrieval fails."""

    def __init__(self, message: str, details: str | None = None):
        self.message = message
        self.details = details
        super().__init__(message)


class BrokerAuthRequiredException(TokenProviderException):
    """
    Raised when a broker token exchange endpoint returns an HTTP error.

    Signals that the client must re-authenticate via the configured auth location.
    The ``auth_location`` value is used as the ``x-user-mcp-auth-location`` response header.
    """

    def __init__(self, message: str, auth_location: str, details: str | None = None):
        self.auth_location = auth_location
        super().__init__(message=message, details=details)


class BaseTokenProvider(ABC):
    """
    Abstract base class for token providers.

    This class defines the interface for token providers that can retrieve
    authentication tokens for users. Implementations must provide the logic
    for obtaining tokens from different sources (e.g., context, OAuth2, SAML).

    Security Note:
        All implementations must ensure tokens are NEVER logged.
        Only log user_id, operation types, and error types.
    """

    @abstractmethod
    def get_token(self) -> str | None:
        """
        Retrieve authentication token.

        Returns:
            Authentication token string if available, None otherwise

        Raises:
            TokenProviderException: If token retrieval fails due to provider errors

        Security:
            - NEVER log the returned token value
            - Only log operation status
            - Use generic error messages
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """
        Get provider name for logging and identification.

        Returns:
            Human-readable provider name (e.g., "ContextTokenProvider")
        """
        pass
