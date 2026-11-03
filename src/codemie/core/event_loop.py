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

"""Main application event loop registry.

Stores a reference to the main asyncio event loop captured at startup.
Sync code running in thread pool workers can submit coroutines to this loop
via run_coroutine_threadsafe, ensuring they share the app's asyncpg pool.
"""

import asyncio

from codemie.configs import logger

_main_event_loop: asyncio.AbstractEventLoop | None = None


def set_main_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_event_loop
    _main_event_loop = loop


def get_main_event_loop() -> asyncio.AbstractEventLoop | None:
    return _main_event_loop


def run_coroutine_blocking(coro, *, timeout: float = 30.0, on_timeout: str = "operation"):
    """Run a coroutine to completion from **synchronous** code, returning its value or None.

    This is the fourth call site of a pattern this module's docstring already claims, so it lives
    here rather than being hand-rolled a fourth time. It exists because several read paths are
    synchronous by construction - a sync FastAPI handler over a sync `Session` - while the services
    they now need are async, and the coroutine must run on the **main** loop so it shares the app's
    asyncpg pool rather than opening a second one per worker thread.

    Returns None on timeout instead of raising, and the caller decides what an absent value means.
    That is the right split here: the callers are read paths that must degrade a figure rather than
    fail a request.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread - the ordinary case for a sync route handler running in
        # the threadpool. Prefer the app's own loop so the asyncpg pool is reused.
        main_loop = get_main_event_loop()
        if main_loop is None or not main_loop.is_running():
            return asyncio.run(coro)
        loop = main_loop

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()
        logger.warning(f"{on_timeout} timed out after {timeout}s from synchronous code; no value is returned")
        return None
