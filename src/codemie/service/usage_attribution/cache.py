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

"""Shared cache, locks and counters: Redis when it is reachable, an in-process store otherwise.

Redis errors never fail a request: after one, the in-process store is used for ``_BREAKER_S`` seconds before Redis
is tried again. The in-process store is bounded by size and evicts the least recently used entries.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

_BREAKER_S = 30.0
_LOCAL_MAX_BYTES = 256 * 1024 * 1024

_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_EXTEND_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_INCR_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""
# DECR alone would recreate an expired counter at -1 without an expiry.
_DECR_LUA = """
local count = tonumber(redis.call('GET', KEYS[1]))
if count and count > 0 then
    return redis.call('DECR', KEYS[1])
end
return 0
"""


class _LocalStore:
    """Bytes with an expiry, least recently used evicted first, bounded by total size."""

    def __init__(self, max_bytes: int = _LOCAL_MAX_BYTES):
        self.max_bytes = max_bytes
        self.items: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.size = 0

    @staticmethod
    def _len(value: Any) -> int:
        return len(value) if isinstance(value, (bytes, str)) else 64

    def _expire(self, key: str) -> None:
        item = self.items.get(key)
        if item is not None and item[0] <= time.monotonic():
            self.delete(key)

    def get(self, key: str) -> Any:
        self._expire(key)
        item = self.items.get(key)
        if item is None:
            return None
        self.items.move_to_end(key)
        return item[1]

    def set(self, key: str, value: Any, ttl: float) -> None:
        self.delete(key)
        self.items[key] = (time.monotonic() + ttl, value)
        self.size += self._len(value)
        while self.size > self.max_bytes and self.items:
            _, (_exp, old) = self.items.popitem(last=False)
            self.size -= self._len(old)

    def set_nx(self, key: str, value: Any, ttl: float) -> bool:
        self._expire(key)
        if key in self.items:
            return False
        self.set(key, value, ttl)
        return True

    def delete(self, key: str) -> None:
        item = self.items.pop(key, None)
        if item is not None:
            self.size -= self._len(item[1])

    def ttl(self, key: str) -> float:
        self._expire(key)
        item = self.items.get(key)
        return max(0.0, item[0] - time.monotonic()) if item else 0.0


class SharedCache:
    """The async facade the analysis service and the limits use."""

    def __init__(self, redis_factory: Any = None):
        self._redis_factory = redis_factory
        self._redis: Any = None
        self._down_until = 0.0
        self.local = _LocalStore()

    # ------------------------------------------------------------------ redis
    def _client(self) -> Any:
        if time.monotonic() < self._down_until:
            return None
        if self._redis is None:
            try:
                factory = self._redis_factory or _default_redis_factory
                self._redis = factory()
            except Exception as exc:  # noqa: BLE001 - no Redis configured: the in-process store is used
                logger.warning(f"Usage attribution cache: Redis unavailable, using the in-process cache: {exc}")
                self._down_until = time.monotonic() + _BREAKER_S
                return None
        return self._redis

    def _trip(self, exc: Exception) -> None:
        logger.warning(
            f"Usage attribution cache: Redis error, using the in-process cache for {_BREAKER_S:.0f} s: {exc}"
        )
        self._down_until = time.monotonic() + _BREAKER_S

    @property
    def backend(self) -> str:
        return "redis" if self._client() is not None else "in-process"

    # ------------------------------------------------------------------ values
    async def get(self, key: str) -> bytes | None:
        client = self._client()
        if client is not None:
            try:
                return await client.get(key)
            except Exception as exc:  # noqa: BLE001
                self._trip(exc)
        return self.local.get(key)

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        if not keys:
            return []
        client = self._client()
        if client is not None:
            try:
                return list(await client.mget(keys))
            except Exception as exc:  # noqa: BLE001
                self._trip(exc)
        return [self.local.get(k) for k in keys]

    async def set_many(self, mapping: dict[str, bytes], ttl: int) -> None:
        if not mapping:
            return
        client = self._client()
        if client is not None:
            try:
                async with client.pipeline(transaction=True) as pipe:
                    for key, value in mapping.items():
                        pipe.set(key, value, ex=int(ttl))
                    await pipe.execute()
                return
            except Exception as exc:  # noqa: BLE001
                self._trip(exc)
        for key, value in mapping.items():
            self.local.set(key, value, ttl)

    async def delete(self, *keys: str) -> None:
        if not keys:
            return
        client = self._client()
        if client is not None:
            try:
                await client.delete(*keys)
            except Exception as exc:  # noqa: BLE001
                self._trip(exc)
        for key in keys:
            self.local.delete(key)

    async def ttl(self, key: str) -> float:
        client = self._client()
        if client is not None:
            try:
                value = await client.ttl(key)
                return float(value) if value and value > 0 else 0.0
            except Exception as exc:  # noqa: BLE001
                self._trip(exc)
        return self.local.ttl(key)

    # ------------------------------------------------------------------ locks and counters
    async def acquire(self, key: str, ttl: float) -> str | None:
        """SET NX EX: a token when the lock was taken, None when someone else holds it."""
        token = secrets.token_hex(8)
        client = self._client()
        if client is not None:
            try:
                ok = await client.set(key, token, nx=True, ex=max(1, int(ttl)))
                return token if ok else None
            except Exception as exc:  # noqa: BLE001
                self._trip(exc)
        return token if self.local.set_nx(key, token, ttl) else None

    async def release(self, key: str, token: str | None) -> None:
        if not token:
            return
        client = self._client()
        if client is not None:
            try:
                await client.eval(_RELEASE_LUA, 1, key, token)
                return
            except Exception as exc:  # noqa: BLE001
                self._trip(exc)
        current = self.local.get(key)
        if current == token:
            self.local.delete(key)

    async def extend(self, key: str, token: str, ttl: float) -> bool:
        """Renew a lock we still hold; False when it expired or someone else holds it now."""
        client = self._client()
        if client is not None:
            try:
                return bool(await client.eval(_EXTEND_LUA, 1, key, token, max(1, int(ttl))))
            except Exception as exc:  # noqa: BLE001
                self._trip(exc)
        if self.local.get(key) != token:
            return False
        self.local.set(key, token, ttl)
        return True

    async def incr(self, key: str, window_s: int) -> tuple[int, float]:
        """Increment a counter that lives ``window_s`` seconds from its first increment: (count, seconds left)."""
        client = self._client()
        if client is not None:
            try:
                count = int(await client.eval(_INCR_LUA, 1, key, int(window_s)))
                left = await client.ttl(key)
                return count, float(left if left and left > 0 else window_s)
            except Exception as exc:  # noqa: BLE001
                self._trip(exc)
        current = self.local.get(key)
        if current is None:
            self.local.set(key, 1, window_s)
            return 1, float(window_s)
        left = self.local.ttl(key)
        self.local.set(key, int(current) + 1, left)
        return int(current) + 1, left

    async def decr(self, key: str) -> None:
        """Give back one count. A counter that expired in the meantime stays gone, and none goes below zero."""
        client = self._client()
        if client is not None:
            try:
                await client.eval(_DECR_LUA, 1, key)
                return
            except Exception as exc:  # noqa: BLE001
                self._trip(exc)
        current = self.local.get(key)
        if current is not None:
            self.local.set(key, max(0, int(current) - 1), self.local.ttl(key))


def _default_redis_factory() -> Any:
    import redis.asyncio as redis_asyncio

    from codemie.configs.config import config

    return redis_asyncio.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        password=config.REDIS_PASSWORD or None,
        ssl=config.REDIS_SSL,
        ssl_cert_reqs=config.REDIS_SSL_CERT_REQS if config.REDIS_SSL else None,
        decode_responses=False,
        socket_connect_timeout=min(2.0, config.REDIS_CONNECT_TIMEOUT_SECONDS),
        socket_timeout=config.REDIS_TIMEOUT_SECONDS,
    )
