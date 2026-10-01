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

import threading
from typing import Any, ClassVar

from cachetools import TTLCache

from codemie.configs.config import config
from codemie.configs.logger import logger
from codemie.rest_api.security.user_context import get_current_user
from codemie.service.security.principal_token_resolver import (
    PrincipalType,
    resolve_current_bearer_token,
)
from codemie.service.security.token_providers.base_provider import (
    BaseTokenProvider,
    BrokerAuthRequiredException,
    TokenProviderException,
)
from codemie.service.security.token_providers.broker_token_exchange_provider import (
    BrokerTokenExchangeProvider,
)
from codemie.service.security.token_providers.context_token_provider import (
    ContextTokenProvider,
)
from codemie.service.security.jwt_utils import parse_jwt_exp

# Separate cache/store slots per principal type so a cached client token is never
# served for a user-principal request and vice versa. Both the read/write path and
# the eviction path derive their keys from here, so the two cannot drift apart.
_STORE_ALIASES: dict[PrincipalType, str] = {
    PrincipalType.USER: "__idp_token__",
    PrincipalType.CLIENT: "__client_token__",
}


def _cache_key(user_id: str, principal_type: PrincipalType) -> str:
    return f"auth_token:{user_id}:{principal_type.value}"


class TokenExchangeService:
    """
    Singleton service for token retrieval with caching.

    This service provides a centralized service for retrieving authentication
    tokens for the current user. It implements TTL-based caching to improve
    performance and reduce repeated lookups.

    Features:
        - Thread-safe singleton pattern with double-checked locking
        - TTL-based caching (default 5 minutes, configurable)
        - User-scoped cache keys for multi-user isolation
        - Provider pattern for extensibility (Phase 2)

    Cache Strategy:
        - Cache key format: f"auth_token:{user_id}"
        - TTL: Configured via TOKEN_CACHE_TTL (default 300 seconds)
        - Max entries: Configured via TOKEN_CACHE_MAX_SIZE (default 1024)
        - User isolation: Separate cache entry per user

    Security:
        - NEVER logs token values
        - Only logs user_id, operation types, and error types
        - Cache keys include user ID for isolation
        - ContextVar provides request-scoped isolation

    Usage:
        from codemie.service.security.token_exchange_service import token_exchange_service

        # Get token for current user (most common usage)
        token = token_exchange_service.get_token_for_current_user()

        # Clear cache on logout
        token_exchange_service.clear_cache(user_id=user.id)

        # Monitor cache performance
        stats = token_exchange_service.get_cache_stats()
    """

    _instance: ClassVar[TokenExchangeService | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()
    _store: ClassVar[Any] = None

    @classmethod
    def set_tms(cls, tms: Any, audit_context_provider: Any) -> None:
        from codemie.service.security.tms_token_store import TMSTokenStore

        cls._store = TMSTokenStore(tms, audit_context_provider)

    @classmethod
    def clear_tms(cls) -> None:
        cls._store = None

    def __new__(cls) -> TokenExchangeService:
        """
        Create or return singleton instance using double-checked locking.

        This ensures thread-safe singleton initialization even in multi-threaded
        environments. The double-checked locking pattern minimizes lock contention
        by checking the instance twice: once without lock, once with lock.

        Returns:
            The singleton TokenExchangeService instance
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(TokenExchangeService, cls).__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """
        Initialize cache and default provider.

        This method is called once during singleton creation to set up:
        - TTLCache with configured TTL and max size
        - Default provider

        Configuration:
            TOKEN_CACHE_TTL: Cache time-to-live in seconds (default 300)
            TOKEN_CACHE_MAX_SIZE: Maximum cache entries (default 1024)
        """
        self._cache: TTLCache[str, str | None] = TTLCache(
            maxsize=config.TOKEN_CACHE_MAX_SIZE, ttl=config.TOKEN_CACHE_TTL
        )
        if config.BROKER_TOKEN_URLS:
            self._default_provider: BaseTokenProvider = BrokerTokenExchangeProvider()
        else:
            self._default_provider: BaseTokenProvider = ContextTokenProvider()
        logger.info(f"TokenExchangeService initialized with cache_ttl={config.TOKEN_CACHE_TTL}s")

    def get_token_for_current_user(self) -> str | None:
        token, _principal_type = self.get_token_with_principal_type_for_current_user()
        return token

    def get_token_with_principal_type_for_current_user(self) -> tuple[str | None, PrincipalType | None]:
        """Retrieve the outbound bearer token and the principal type it represents.

        The principal type comes from the request context (user token vs client
        token, see principal_token_resolver). Exchanged tokens keep the principal
        type of their subject token: a client-credentials token never becomes a
        user-principal token.
        """
        current_user = get_current_user()
        if not current_user:
            logger.debug("No current user in context")
            return None, None

        _token, principal_type = resolve_current_bearer_token()
        if principal_type is None:
            logger.debug(f"No bearer token in context for user_id={current_user.id}")
            return None, None

        token = self._get_token_for_principal(current_user.id, principal_type)
        if not token:
            return None, None
        return token, principal_type

    def _get_token_for_principal(self, user_id: str, principal_type: PrincipalType) -> str | None:
        store_alias = _STORE_ALIASES[principal_type]

        if self._store is not None:
            cached_token = self._store.get(user_id, store_alias)
            if cached_token is not None:
                logger.debug(f"Token TMS hit for user_id={user_id}")
                return cached_token

            logger.debug(f"Token TMS miss for user_id={user_id}")
            try:
                token = self._default_provider.get_token()
            except BrokerAuthRequiredException:
                raise
            except TokenProviderException as e:
                logger.exception(f"Token provider failed for user_id={user_id}: {e.message}")
                raise

            if token:
                expires_at = parse_jwt_exp(token)
                self._store.put(user_id, store_alias, access_token=token, expires_at=expires_at)
                logger.debug(f"Stored token in TMS for user_id={user_id}")

            return token

        cache_key = _cache_key(user_id, principal_type)
        cached_token = self._cache.get(cache_key)
        if cached_token is not None:
            logger.debug(f"Token cache hit for user_id={user_id}")
            return cached_token

        logger.debug(f"Token cache miss for user_id={user_id}")
        try:
            token = self._default_provider.get_token()
            if token:
                self._cache[cache_key] = token
                logger.debug(f"Cached token for user_id={user_id}")
            return token
        except BrokerAuthRequiredException:
            logger.debug(f"Broker re-authentication required for user_id={user_id}")
            raise
        except TokenProviderException as e:
            logger.exception(f"Token provider failed for user_id={user_id}: {e.message}")
            raise

    def clear_cache(self, user_id: str | None = None) -> None:
        if user_id:
            # Evict every principal slot: logout must not leave a usable credential
            # behind for either the user- or the client-scoped principal.
            for principal_type, store_alias in _STORE_ALIASES.items():
                if self._store is not None:
                    self._store.invalidate(user_id, store_alias)
                self._cache.pop(_cache_key(user_id, principal_type), None)
            logger.info(f"Cleared token cache for user_id={user_id}")
        else:
            self._cache.clear()
            logger.info("Cleared all token cache")

    def purge_user_cache(self, user_id: str) -> None:
        """Drop this process's cached tokens for ``user_id`` without any TMS round-trip.

        Unlike ``clear_cache(user_id)``, the store is only purged locally: its ``invalidate``
        is a TMS delete per principal alias.
        """
        for principal_type in _STORE_ALIASES:
            self._cache.pop(_cache_key(user_id, principal_type), None)
        if self._store is not None:
            self._store.purge_fallback_for_user(user_id)

    def get_cache_stats(self) -> dict[str, int]:
        """
        Get cache statistics for monitoring and debugging.

        Returns:
            Dictionary with cache metrics:
                - cache_size: Number of entries in cache
                - cache_ttl: Configured TTL in seconds

        Usage:
            stats = token_exchange_service.get_cache_stats()
            print(f"Cache size: {stats['cache_size']}, TTL: {stats['cache_ttl']}s")
        """
        return {
            "cache_size": len(self._cache),
            "cache_ttl": config.TOKEN_CACHE_TTL,
        }


# Module-level singleton instance for convenient access
# Import this in other modules to use the service
token_exchange_service = TokenExchangeService()
