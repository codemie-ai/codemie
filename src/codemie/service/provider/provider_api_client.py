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

from codemie.clients.provider import client as provider_client
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_context import get_current_auth_token
from codemie.rest_api.models.provider import ProviderConfiguration
from codemie.configs import logger, config


class ProviderAPIClient:
    """Client for interacting with the provider's API endpoints."""

    LOCAL_MOCK_BEARER = "local"
    NO_AUTH_TOKEN_MSG = "User authentication details are not provided, please contact your administrator."

    def __init__(
        self,
        user: User,
        url: str,
        provider_security_config: ProviderConfiguration,
        log_prefix: str = "",
    ):
        self.user = user
        self.url = url
        self.provider_security_config = provider_security_config  # Fixed variable name to match usage in methods
        self.log_prefix = log_prefix

    def build(self) -> provider_client.ToolInvocationManagementApi:
        """Build a provider client for tool invocation management."""
        host = self.url.rstrip("/")
        client_config = provider_client.Configuration(host=host, **self._get_auth_credentials())
        client_config.verify_ssl = False

        with provider_client.ApiClient(client_config) as api_client:
            return provider_client.ToolInvocationManagementApi(api_client)

    def _get_auth_credentials(self) -> dict:
        """Retrieve authentication credentials based on the configured auth type."""
        security_config = self.provider_security_config

        if config.is_local:
            logger.info(f"{self.log_prefix} Using local mock bearer token")
            return {"access_token": self.LOCAL_MOCK_BEARER}

        if security_config.auth_type == ProviderConfiguration.AuthType.BEARER.value:
            logger.info(f"{self.log_prefix} Using bearer token")

            # Try user.auth_token first (backward compatibility)
            # Fall back to context if not present
            token = self.user.auth_token or get_current_auth_token()

            if not token:
                logger.error(f"{self.log_prefix} User authentication details are not provided")
                raise ValueError(self.NO_AUTH_TOKEN_MSG)

            return {"access_token": token}

        logger.error(f"{self.log_prefix} Unknown auth type: {security_config.auth_type}")
        raise ValueError(f"Unknown auth type: {security_config.auth_type}")
