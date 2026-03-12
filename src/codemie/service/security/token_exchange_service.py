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
from typing import ClassVar

from cachetools import TTLCache

from codemie.configs.config import config
from codemie.configs.logger import logger
from codemie.rest_api.security.user_context import get_current_user
from codemie.service.security.token_providers.base_provider import (
    BaseTokenProvider,
    TokenProviderException,
)
from codemie.service.security.token_providers.broker_token_exchange_provider import (
    BrokerTokenExchangeProvider,
)
from codemie.service.security.token_providers.context_token_provider import (
    ContextTokenProvider,
)


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
        """
        Get authentication token for current user with caching.

        This is the primary method for retrieving user tokens. It implements
        a cache-first strategy with user-scoped isolation.

        Flow:
            1. Get current user from ContextVar
            2. Check cache using key: f"auth_token:{user_id}"
            3. On cache hit: Return cached token immediately
            4. On cache miss: Fetch from provider
            5. Cache the token
            6. Return token

        Returns:
            JWT authentication token if available, None otherwise

        Raises:
            TokenProviderException: If provider fails to retrieve token

        Security:
            - NEVER logs the token value
            - Only logs user_id and cache hit/miss status
            - Uses user_id in cache key for isolation

        Performance:
            - Cache hit: <10ms (target)
            - Cache miss: <50ms (target)
            - Cache hit rate: >80% (target)
        """
        current_user = get_current_user()
        if not current_user:
            logger.debug("No current user in context")
            return None

        user_id = current_user.id
        cache_key = f"auth_token:{user_id}"

        # Check cache first
        cached_token = self._cache.get(cache_key)
        if cached_token is not None:
            logger.debug(f"Token cache hit for user_id={user_id}")
            return cached_token

        # Cache miss - fetch from provider
        logger.debug(f"Token cache miss for user_id={user_id}")

        try:
            token = self._default_provider.get_token()

            if token:
                self._cache[cache_key] = token
                logger.debug(f"Cached token for user_id={user_id}")
            else:
                logger.debug(f"No token available for user_id={user_id}")

            return token

        except TokenProviderException as e:
            logger.exception(f"Token provider failed for user_id={user_id}: {e.message}")
            raise

    def clear_cache(self, user_id: str | None = None) -> None:
        """
        Clear token cache for specific user or all users.

        This method is useful for:
        - Logout: Clear specific user's cached token
        - Token rotation: Force fresh token fetch
        - Testing: Reset cache state

        Args:
            user_id: User ID to clear cache for. If None, clears all cache entries.

        Security:
            - Safe to call multiple times (idempotent)
            - Does not log token values
        """
        if user_id:
            cache_key = f"auth_token:{user_id}"
            self._cache.pop(cache_key, None)
            logger.info(f"Cleared token cache for user_id={user_id}")
        else:
            self._cache.clear()
            logger.info("Cleared all token cache")

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
