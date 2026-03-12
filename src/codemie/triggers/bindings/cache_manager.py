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

"""Cache manager for trigger engine"""

from datetime import datetime
from typing import Any, Callable, Dict


class CacheManager:
    """Manages caching for trigger resources with TTL support"""

    def __init__(self, cache_ttl: int = 300):
        """
        Initialize cache manager.

        Args:
            cache_ttl: Cache time-to-live in seconds (default: 5 minutes)
        """
        self._cache: Dict[str, tuple[datetime, Any]] = {}
        self._cache_ttl = cache_ttl

    def is_valid(self, cache_key: str) -> bool:
        """Check if cache entry is still valid based on TTL"""
        if cache_key not in self._cache:
            return False

        cached_time, _ = self._cache[cache_key]
        age = (datetime.now() - cached_time).total_seconds()
        return age < self._cache_ttl

    def get(self, cache_key: str) -> Any | None:
        """Get value from cache if valid"""
        if self.is_valid(cache_key):
            _, cached_value = self._cache[cache_key]
            return cached_value
        return None

    def set(self, cache_key: str, value: Any) -> None:
        """Set value in cache with current timestamp"""
        self._cache[cache_key] = (datetime.now(), value)

    def clean_expired(self) -> int:
        """
        Remove expired cache entries to prevent memory bloat.

        Returns:
            Number of expired entries removed
        """
        now = datetime.now()
        expired_keys = [
            key
            for key, (cached_time, _) in self._cache.items()
            if (now - cached_time).total_seconds() >= self._cache_ttl
        ]

        for key in expired_keys:
            del self._cache[key]

        return len(expired_keys)

    def fetch_with_cache(self, cache_key: str, fetch_func: Callable[[], Any], error_message: str = "") -> Any | None:
        """
        Generic cached database fetch with error handling.

        This method encapsulates the common pattern of:
        1. Check cache
        2. If miss, fetch from DB
        3. Cache the result (even if None to avoid repeated failures)
        4. Handle errors and cache None on failure

        Args:
            cache_key: Key to use for caching
            fetch_func: Callable that fetches data from database
            error_message: Error message to log if fetch fails

        Returns:
            Fetched data or None if not found/error occurred
        """
        # Check cache first
        cached = self.get(cache_key)
        if cached is not None:
            return cached

        # Cache miss - fetch from DB
        try:
            result = fetch_func()
            self.set(cache_key, result)
            return result
        except Exception as e:
            if error_message:
                from codemie.configs import logger

                logger.error("%s: %s", error_message, e)
            # Cache None to avoid repeated failed queries
            self.set(cache_key, None)
            return None

    def clear(self) -> None:
        """Clear all cache entries"""
        self._cache.clear()

    def size(self) -> int:
        """Get number of entries in cache"""
        return len(self._cache)
