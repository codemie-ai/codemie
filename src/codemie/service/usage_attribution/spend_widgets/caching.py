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

import functools
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime
from typing import Any

from codemie.service.usage_attribution.constants import CACHE_NAMESPACE
from codemie.service.usage_attribution.settings import stable_hash

LEDGER_TTL_S = 15 * 60
KEY_VERSION = "v2"


def cached_read(read: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Cache a widget's ``read(start, end[, keys])`` per view, clipped window, access boundary and keys.

    Below the floor, or with the ledger not activated, nothing is read from or written to the cache. An answer that
    is not ``complete`` - the port could not give a figure - is returned but not cached, so the figures come back
    the moment the ledger does.
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
        cached = await self._context.cache.get(key)

        if cached is not None:
            return self.output(**json.loads(cached))

        answer = await read(self, start, end, *keys)
        if answer.complete:
            await self._context.cache.set_many({key: json.dumps(asdict(answer)).encode("utf-8")}, LEDGER_TTL_S)

        return answer

    return wrapper
