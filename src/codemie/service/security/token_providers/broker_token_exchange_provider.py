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

import asyncio
import httpx
from concurrent.futures import ThreadPoolExecutor

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.rest_api.security.user_context import get_current_auth_token, get_current_user
from codemie.service.security.token_providers.base_provider import (
    BaseTokenProvider,
    TokenProviderException,
)

# Error message constants
_CONFIG_INCOMPLETE_MSG = "Broker token exchange configuration is incomplete"


class BrokerTokenExchangeProvider(BaseTokenProvider):
    """
    Token provider that performs multi-hop broker token exchange.

    This provider implements a chained token exchange process supporting
    any number of hops:
    1. Exchanges the user's authentication token with the first broker
    2. Uses each intermediate token to exchange with the next broker
    3. Returns the final token from the last exchange

    Configuration:
        All broker endpoints are configured via comma-separated environment
        variables (see config.py):
        - BROKER_TOKEN_URLS: Comma-separated base URLs (e.g., "https://auth1.com,https://auth2.com")
        - BROKER_TOKEN_REALMS: Comma-separated realm names (e.g., "realm1,realm2")
        - BROKER_TOKEN_BROKERS: Comma-separated broker identifiers (e.g., "broker1,broker2")
        - BROKER_TOKEN_TIMEOUT: Request timeout in seconds (default: 5.0)

        All three lists must have the same number of elements.

    Security:
        - Tokens are never logged or exposed in error messages
        - Uses request-scoped ContextVar for initial token retrieval
        - All HTTP communication uses TLS by default
        - Proper timeout handling to prevent hanging requests

    Usage:
        This provider is intended for scenarios requiring multi-step
        token exchange through intermediate brokers (e.g., federated
        identity providers, token translation services).

    Examples:
        Single hop:  BROKER_TOKEN_URLS="https://auth.example.com"
        Two hops:    BROKER_TOKEN_URLS="https://auth1.com,https://auth2.com"
        Three hops:  BROKER_TOKEN_URLS="https://auth1.com,https://auth2.com,https://auth3.com"
    """

    def __init__(self) -> None:
        """
        Initialize the broker token exchange provider.

        Validates configuration and parses comma-separated lists.
        If broker exchange is not configured, acts as a pass-through provider.

        Raises:
            TokenProviderException: If configuration is partially set or invalid
        """
        self.urls, self.realms, self.brokers = self._parse_and_validate_config()
        self.timeout = config.BROKER_TOKEN_TIMEOUT

    def _parse_and_validate_config(self) -> tuple[list[str], list[str], list[str]]:
        """
        Parse and validate broker configuration from comma-separated strings.

        Returns:
            Tuple of (urls, realms, brokers) as lists. Returns empty lists if
            broker exchange is not configured (all env vars are unset).

        Raises:
            TokenProviderException: If configuration is partially set or invalid
        """
        # Check if all config values are unset - this is valid (pass-through mode)
        if not config.BROKER_TOKEN_URLS and not config.BROKER_TOKEN_REALMS and not config.BROKER_TOKEN_BROKERS:
            logger.info("Broker token exchange not configured - using pass-through mode")
            return [], [], []

        # If some are set but not all, this is a configuration error
        if not config.BROKER_TOKEN_URLS:
            raise TokenProviderException(
                message=_CONFIG_INCOMPLETE_MSG,
                details="BROKER_TOKEN_URLS is required when broker exchange is configured",
            )
        if not config.BROKER_TOKEN_REALMS:
            raise TokenProviderException(
                message=_CONFIG_INCOMPLETE_MSG,
                details="BROKER_TOKEN_REALMS is required when broker exchange is configured",
            )
        if not config.BROKER_TOKEN_BROKERS:
            raise TokenProviderException(
                message=_CONFIG_INCOMPLETE_MSG,
                details="BROKER_TOKEN_BROKERS is required when broker exchange is configured",
            )

        # Parse comma-separated values and strip whitespace
        urls = [url.strip() for url in config.BROKER_TOKEN_URLS.split(",") if url.strip()]
        realms = [realm.strip() for realm in config.BROKER_TOKEN_REALMS.split(",") if realm.strip()]
        brokers = [broker.strip() for broker in config.BROKER_TOKEN_BROKERS.split(",") if broker.strip()]

        # Validate that all lists have the same length
        if len(urls) != len(realms) or len(urls) != len(brokers):
            raise TokenProviderException(
                message="Broker token exchange configuration is invalid",
                details=f"Configuration lists must have the same length: "
                f"URLS={len(urls)}, REALMS={len(realms)}, BROKERS={len(brokers)}",
            )

        logger.info(f"Initialized broker token exchange with {len(urls)} hop(s)")
        return urls, realms, brokers

    def _build_broker_url(self, base_url: str, realm: str, broker: str) -> str:
        """
        Build the broker token exchange URL.

        Args:
            base_url: Base URL of the broker (e.g., "https://auth.example.com")
            realm: Keycloak realm name
            broker: Broker identifier

        Returns:
            Complete URL for the broker token endpoint
        """
        # Remove trailing slash from base_url if present
        base_url = base_url.rstrip("/")
        return f"{base_url}/realms/{realm}/broker/{broker}/token"

    @staticmethod
    def _get_http_error_details(e: httpx.HTTPStatusError) -> str:
        """Extract error details from an HTTP error response."""
        try:
            return f"HTTP {e.response.status_code}: {e.response.json()}"
        except ValueError:
            return f"HTTP {e.response.status_code}: {e.response.text[:1000]}"

    async def _exchange_token(self, url: str, bearer_token: str) -> str:
        """
        Exchange a token with a broker endpoint.

        Args:
            url: Broker token exchange URL
            bearer_token: Bearer token to use for authentication

        Returns:
            The access_token from the broker response

        Raises:
            TokenProviderException: If the exchange fails or response is invalid
        """
        current_user = get_current_user()
        user_id = current_user.id if current_user else 'unknown'

        headers = {"Authorization": f"Bearer {bearer_token}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.debug(f"Exchanging token with broker endpoint for user_id={user_id}")
                response = await client.get(url, headers=headers)
                response.raise_for_status()

                response_data = response.json()

                if "access_token" not in response_data:
                    raise TokenProviderException(
                        message="Broker response missing access_token field",
                        details="Expected 'access_token' in broker response JSON",
                    )

                logger.debug(f"Successfully exchanged token with broker for user_id={user_id}")
                return response_data["access_token"]

        except httpx.HTTPStatusError as e:
            error_msg = f"Broker token exchange failed with HTTP {e.response.status_code}"
            logger.error(f"{error_msg} for user_id={user_id}: {e.response.reason_phrase}")
            raise TokenProviderException(message=error_msg, details=self._get_http_error_details(e)) from e

        except httpx.RequestError as e:
            error_msg = "Broker token exchange request failed"
            logger.error(f"{error_msg} for user_id={user_id}: {type(e).__name__}")
            raise TokenProviderException(message=error_msg, details=f"Network error: {type(e).__name__}") from e

        except Exception as e:
            error_msg = "Unexpected error during broker token exchange"
            logger.exception(f"{error_msg} for user_id={user_id}: {type(e).__name__}")
            raise TokenProviderException(message=error_msg, details=f"Error type: {type(e).__name__}") from e

    async def _aget_token(self) -> str | None:
        """
        Asynchronously retrieve final token through multi-hop broker exchange.

        Implementation:
            1. Retrieve user's auth token from request context
            2. If broker exchange is not configured, return the token as-is (pass-through)
            3. For each broker hop (URL, realm, broker):
               - Build the broker endpoint URL
               - Exchange the current token for the next token
            4. Return the final access token from the last exchange

        Returns:
            Final access token from last broker exchange, or None if initial token unavailable.
            In pass-through mode (no brokers configured), returns the current auth token.

        Raises:
            TokenProviderException: If any exchange step fails

        Security:
            - NEVER logs any token values
            - Only logs operation status
            - Exception messages contain no sensitive data
        """
        # Get user_id for logging if available
        current_user = get_current_user()
        user_id = current_user.id if current_user else 'unknown'

        try:
            # Step 1: Get user's authentication token from context
            current_token = get_current_auth_token()

            if not current_token:
                logger.debug(f"No user auth token available in context for user_id={user_id}")
                return None

            # Step 2: Check if broker exchange is configured
            if not self.urls:
                # Pass-through mode: no broker exchange configured
                logger.debug(f"Pass-through mode: returning current token for user_id={user_id}")
                return current_token

            num_hops = len(self.urls)
            logger.debug(f"Starting {num_hops}-hop broker token exchange for user_id={user_id}")

            # Step 3: Exchange token through each broker hop
            for hop_index, (url, realm, broker) in enumerate(
                zip(self.urls, self.realms, self.brokers, strict=True), start=1
            ):
                broker_url = self._build_broker_url(url, realm, broker)

                logger.debug(f"Executing hop {hop_index}/{num_hops} for user_id={user_id}")

                current_token = await self._exchange_token(broker_url, current_token)

            logger.info(f"Successfully completed {num_hops}-hop broker token exchange for user_id={user_id}")
            return current_token

        except TokenProviderException:
            # Re-raise our custom exceptions
            raise

        except Exception as e:
            error_msg = "Failed to complete broker token exchange"
            logger.exception(f"{error_msg} for user_id={user_id}: {type(e).__name__}")
            raise TokenProviderException(message=error_msg, details=f"Error type: {type(e).__name__}") from e

    def get_token(self) -> str | None:
        """
        Retrieve final token through multi-hop broker exchange.
        Synchronous wrapper for _aget_token.
        """
        try:
            # If this call is made from within an already running loop,
            # get_running_loop() succeeds.
            asyncio.get_running_loop()
            in_running_loop = True
        except RuntimeError:
            in_running_loop = False

        if in_running_loop:
            # Offload the coroutine to a separate thread to avoid deadlock.
            # asyncio.run() is safe to call from a thread with no running loop.
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(lambda: asyncio.run(self._aget_token()))
                return future.result()
        else:
            # No loop is running, so run the coroutine directly.
            return asyncio.run(self._aget_token())

    def get_provider_name(self) -> str:
        """Get provider name for logging and identification."""
        return "BrokerTokenExchangeProvider"
