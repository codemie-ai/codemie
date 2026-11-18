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

"""Per-event-loop concurrency limit for default budget provider calls.

A slot stays held until the provider call itself finishes, even when the caller stops waiting,
so a hung provider can occupy at most PROVISION_PROVIDER_CONCURRENCY worker threads per loop.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Coroutine
from typing import Any

PROVISION_PROVIDER_CONCURRENCY = 3

_provision_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def provision_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _provision_semaphores.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(PROVISION_PROVIDER_CONCURRENCY)
        _provision_semaphores[loop] = semaphore
    return semaphore


def _release_slot_when_done(semaphore: asyncio.Semaphore, task: asyncio.Future[str | None]) -> None:
    semaphore.release()
    if not task.cancelled():
        task.exception()


async def run_customer_provisioning_in_slot(call: Coroutine[Any, Any, str | None]) -> str | None:
    semaphore = provision_semaphore()
    try:
        await semaphore.acquire()
    except BaseException:
        call.close()
        raise
    task = asyncio.ensure_future(call)
    task.add_done_callback(lambda done: _release_slot_when_done(semaphore, done))
    return await asyncio.shield(task)
