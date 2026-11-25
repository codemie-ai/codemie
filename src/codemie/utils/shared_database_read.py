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

import asyncio
from collections.abc import Awaitable, Callable, Hashable


class SharedDatabaseRead[Key: Hashable, Value]:
    """Share overlapping database reads by key within an event loop.

    The caller that starts the read owns its resources. Entries are removed when the read
    finishes, so later callers can read fresh data or apply their own cache policy.
    """

    def __init__(self) -> None:
        self._pending: dict[tuple[asyncio.AbstractEventLoop, Key], asyncio.Future[Value]] = {}

    async def read(self, key: Key, load: Callable[[], Awaitable[Value]]) -> Value:
        loop = asyncio.get_running_loop()
        slot = (loop, key)
        while (pending := self._pending.get(slot)) is not None:
            try:
                return await asyncio.shield(pending)
            except asyncio.CancelledError:
                # The leader owns the session. If it was cancelled, a surviving caller retries
                # with its own session. A cancelled follower never cancels the shared read.
                caller = asyncio.current_task()
                if not pending.cancelled() or (caller is not None and caller.cancelling()):
                    raise

        pending = loop.create_future()
        self._pending[slot] = pending
        try:
            result = await load()
            pending.set_result(result)
            return result
        except asyncio.CancelledError:
            pending.cancel()
            raise
        except Exception as error:
            pending.set_exception(error)
            pending.exception()
            raise
        finally:
            del self._pending[slot]
