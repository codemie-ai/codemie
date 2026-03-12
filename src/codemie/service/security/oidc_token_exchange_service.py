# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import httpx
from cachetools import TTLCache

from codemie.configs.config import config
from codemie.configs.logger import logger
from codemie.rest_api.security.user_context import get_current_user
from codemie.service.security.token_providers.base_provider import TokenProviderException


class OIDCTokenExchangeService:
    """
    Singleton service for OIDC token exchange (RFC 8693).

    Exchanges a user's IdP token for a service-specific access token scoped
    to a given audience, using Keycloak's token exchange endpoint.

    The exchange is performed only when:
    - The MCP server configuration has an ``audience`` field set.
    - ``TOKEN_EXCHANGE_URL`` is configured.

    Otherwise the caller falls back to the raw IdP token.

    Configuration (via environment variables):
        TOKEN_EXCHANGE_URL: Keycloak token endpoint URL
        TOKEN_EXCHANGE_GRANT_TYPE: OAuth2 grant type (default: token-exchange URN)
        TOKEN_EXCHANGE_CLIENT_ID: OAuth2 client ID
        TOKEN_EXCHANGE_CLIENT_SECRET: OAuth2 client secret
        TOKEN_EXCHANGE_SUBJECT_TOKEN_TYPE: Subject token type URN
        TOKEN_EXCHANGE_TIMEOUT: HTTP request timeout in seconds (default: 5.0)

    Caching:
        Uses ``cachetools.TTLCache`` to cache exchanged tokens per
        ``(user_id, audience)`` pair for ``TOKEN_CACHE_TTL`` seconds
        (default 300 s). LRU eviction applies when ``TOKEN_CACHE_MAX_SIZE``
        is reached.

    Security:
        - Token values are NEVER logged.
        - Client secrets are read from config at call time, never stored in logs.
        - Cache keys contain only ``user_id`` and ``audience`` (no token data).
    """

    _instance: ClassVar[OIDCTokenExchangeService | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __new__(cls) -> OIDCTokenExchangeService:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        self._cache: TTLCache[str, str] = TTLCache(maxsize=config.TOKEN_CACHE_MAX_SIZE, ttl=config.TOKEN_CACHE_TTL)
        logger.info(f"OIDCTokenExchangeService initialized with cache_ttl={config.TOKEN_CACHE_TTL}s")

    async def _aexchange_token(self, subject_token: str, audience: str) -> str:
        """
        POST to Keycloak token endpoint and return the exchanged access_token.

        Args:
            subject_token: The user's current IdP access token.
            audience: The target service audience for the exchanged token.

        Returns:
            The ``access_token`` string from the Keycloak response.

        Raises:
            TokenProviderException: On HTTP error, network error, or missing
                ``access_token`` in the response.
        """
        current_user = get_current_user()
        user_id = current_user.id if current_user else "unknown"

        data = {
            "grant_type": config.TOKEN_EXCHANGE_GRANT_TYPE,
            "client_id": config.TOKEN_EXCHANGE_CLIENT_ID,
            "client_secret": config.TOKEN_EXCHANGE_CLIENT_SECRET,
            "subject_token": subject_token,
            "subject_token_type": config.TOKEN_EXCHANGE_SUBJECT_TOKEN_TYPE,
            "audience": audience,
        }

        try:
            async with httpx.AsyncClient(timeout=config.TOKEN_EXCHANGE_TIMEOUT) as client:
                logger.debug(f"Performing OIDC token exchange for user_id={user_id} audience={audience}")
                response = await client.post(
                    config.TOKEN_EXCHANGE_URL,
                    data=data,
                )
                response.raise_for_status()

                response_data = response.json()

                if "access_token" not in response_data:
                    raise TokenProviderException(
                        message="OIDC token exchange response missing access_token field",
                        details="Expected 'access_token' in Keycloak token exchange response JSON",
                    )

                logger.debug(f"OIDC token exchange succeeded for user_id={user_id} audience={audience}")
                return response_data["access_token"]

        except httpx.HTTPStatusError as e:
            error_msg = f"OIDC token exchange failed with HTTP {e.response.status_code}"
            logger.error(f"{error_msg} for user_id={user_id} audience={audience}: {e.response.reason_phrase}")
            try:
                error_details = e.response.json()
                details = f"HTTP {e.response.status_code}: {error_details}"
            except Exception:
                details = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            raise TokenProviderException(message=error_msg, details=details) from e

        except httpx.RequestError as e:
            error_msg = "OIDC token exchange request failed"
            logger.error(f"{error_msg} for user_id={user_id} audience={audience}: {type(e).__name__}")
            raise TokenProviderException(message=error_msg, details=f"Network error: {type(e).__name__}") from e

        except TokenProviderException:
            raise

        except Exception as e:
            error_msg = "Unexpected error during OIDC token exchange"
            logger.exception(f"{error_msg} for user_id={user_id} audience={audience}: {type(e).__name__}")
            raise TokenProviderException(message=error_msg, details=f"Error type: {type(e).__name__}") from e

    def _run_async(self, coro) -> str:
        """Run an async coroutine from sync context, handling running event loops."""
        try:
            asyncio.get_running_loop()
            in_running_loop = True
        except RuntimeError:
            in_running_loop = False

        if in_running_loop:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(lambda: asyncio.run(coro))
                return future.result()
        else:
            return asyncio.run(coro)

    def get_exchanged_token(self, audience: str) -> str | None:
        """
        Get the IdP token for the current user and exchange it for the given audience.

        Implements a cache-first strategy: the exchanged token is stored under
        ``oidc_exchange:{user_id}:{audience}`` for ``TOKEN_CACHE_TTL`` seconds.

        Args:
            audience: Target service audience (e.g. ``oauth-client.epm-srdr.staffing-radar``).

        Returns:
            The audience-scoped access token, or ``None`` if no user is in context
            or no IdP token is available.

        Raises:
            TokenProviderException: If the Keycloak exchange call fails.

        Security:
            NEVER logs the token value — only logs ``user_id`` and ``audience``.
        """
        from codemie.service.security.token_exchange_service import token_exchange_service

        current_user = get_current_user()
        if not current_user:
            logger.debug("No current user in context for OIDC token exchange")
            return None

        user_id = current_user.id
        cache_key = f"oidc_exchange:{user_id}:{audience}"

        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"OIDC exchange token cache hit for user_id={user_id} audience={audience}")
            return cached

        logger.debug(f"OIDC exchange token cache miss for user_id={user_id} audience={audience}")

        idp_token = token_exchange_service.get_token_for_current_user()
        if not idp_token:
            logger.debug(f"No IdP token available for OIDC exchange for user_id={user_id}")
            return None

        exchanged_token = self._run_async(self._aexchange_token(idp_token, audience))
        self._cache[cache_key] = exchanged_token
        logger.debug(f"Cached OIDC exchanged token for user_id={user_id} audience={audience}")
        return exchanged_token


# Module-level singleton instance
oidc_token_exchange_service = OIDCTokenExchangeService()
