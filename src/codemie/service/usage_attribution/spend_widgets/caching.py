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

"""The cache every spend widget reads through, as one decorator."""

import asyncio
import functools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime
from typing import Any

from codemie.service.usage_attribution.constants import CACHE_NAMESPACE
from codemie.service.usage_attribution.settings import stable_hash
from codemie.service.usage_attribution.spend_widgets.base import LockingWidgetCache, WidgetCache

logger = logging.getLogger(__name__)

LEDGER_TTL_S = 15 * 60
KEY_VERSION = "v4"
_LOCK_TTL_S = 30
_LOCK_POLL_S = 0.5
_MAX_WAIT_S = _LOCK_TTL_S * 2
_MAX_HOLD_S = _LOCK_TTL_S * 4


async def _renew_lock(cache: LockingWidgetCache, key: str, token: str) -> None:
    deadline = time.monotonic() + _MAX_HOLD_S
    try:
        while time.monotonic() < deadline:
            await asyncio.sleep(_LOCK_TTL_S / 3)
            if not await cache.extend(key, token, _LOCK_TTL_S):
                logger.warning("Spend cache lock lost while the read was running: %s", key)
                return
        logger.warning("Spend cache lock no longer renewed after %ss: %s", _MAX_HOLD_S, key)
    except Exception:
        logger.warning("Spend cache lock renewal failed: %s", key, exc_info=True)


async def _read_under_lock(
    cache: LockingWidgetCache,
    key: str,
    lookup: Callable[[], Awaitable[Any | None]],
    compute: Callable[[], Awaitable[Any]],
) -> Any:
    """Wait for the key's lock (or for another caller to fill the cache), then compute while holding it."""
    lock_key = f"{key}:lock"
    deadline = time.monotonic() + _MAX_WAIT_S
    while (token := await cache.acquire(lock_key, _LOCK_TTL_S)) is None:
        if time.monotonic() >= deadline:
            logger.warning("Spend cache lock wait timed out, reading without it: %s", key)
            return await compute()
        await asyncio.sleep(_LOCK_POLL_S)
        if (hit := await lookup()) is not None:
            return hit

    renew = asyncio.create_task(_renew_lock(cache, lock_key, token))
    try:
        # The previous holder may have populated the cache while we acquired the lock.
        hit = await lookup()
        return hit if hit is not None else await compute()
    finally:
        renew.cancel()
        await asyncio.gather(renew, return_exceptions=True)
        await cache.release(lock_key, token)


def cached_read(read: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Cache a widget's ``read(start, end[, keys])`` per view, clipped window, access boundary and keys.

    The shared cache lock lets one caller populate a cold key while other callers wait for the result.
    Below the floor, or with the ledger not activated, nothing is read from or written to the cache. An answer that
    is not ``complete`` - the port could not give a figure - is returned but not cached, so the figures come
    back the moment the ledger does. A waiter stops waiting for the lock after ``_MAX_WAIT_S`` and reads without it.
    """

    @functools.wraps(read)
    async def wrapper(self: Any, start: datetime, end: datetime, *keys: Any) -> Any:
        window = self._window(start, end)
        if window is None:
            return await read(self, start, end, *keys)

        key = (
            f"{CACHE_NAMESPACE}:ledger:{KEY_VERSION}:{self.view}:{int(window.start.timestamp())}:"
            f"{int(window.end.timestamp())}:{stable_hash([self._context.scope, *keys])}"
        )
        cache: WidgetCache = self._context.cache

        async def lookup() -> Any | None:
            cached = await cache.get(key)
            return self.output(**json.loads(cached)) if cached is not None else None

        if (hit := await lookup()) is not None:
            return hit

        async def compute() -> Any:
            answer = await read(self, start, end, *keys)
            if answer.complete:
                await cache.set_many({key: json.dumps(asdict(answer)).encode("utf-8")}, LEDGER_TTL_S)
            return answer

        if not isinstance(cache, LockingWidgetCache):
            return await compute()

        return await _read_under_lock(cache, key, lookup, compute)

    return wrapper
