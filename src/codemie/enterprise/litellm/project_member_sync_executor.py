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

"""Bounded executor for the blocking LiteLLM calls of batch project member budget sync.

Every blocking call goes through one dedicated thread pool. A semaphore on the
owning event loop caps in-flight calls at the worker count. A slot is held until
the worker call finishes, even when the awaiting coroutine is cancelled, so the
cap always reflects the threads actually busy. Pool threads must never call
``run`` themselves. Slots are returned on the loop that first called ``run``, so the
executor must only be used from the application's main event loop; a call from any
other loop raises instead of hanging.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from opentelemetry.context import Context

from codemie.configs import config
from codemie.core.otel_tracing import attach_otel_context, detach_otel_context, get_otel_context_for_thread


def _call_with_otel_context[T](otel_ctx: Context, fn: Callable[..., T], kwargs: dict[str, Any]) -> T:
    token = attach_otel_context(otel_ctx)
    try:
        return fn(**kwargs)
    finally:
        detach_otel_context(token)


def _release_on_loop(loop: asyncio.AbstractEventLoop, semaphore: asyncio.BoundedSemaphore, _: Future) -> None:
    # A closed owning loop raises RuntimeError; there is nobody left to hand the slot to.
    with contextlib.suppress(RuntimeError):
        loop.call_soon_threadsafe(semaphore.release)


class ProjectMemberSyncExecutor:
    """Runs blocking calls on a dedicated pool with at most ``workers`` calls in flight."""

    def __init__(self, workers: int | None = None) -> None:
        self._workers = workers if workers is not None else config.LITELLM_PROJECT_MEMBER_SYNC_WORKERS
        self._executor: ThreadPoolExecutor | None = None
        self._semaphore: asyncio.BoundedSemaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    def _get_executor(self) -> ThreadPoolExecutor:
        if self._closed:
            raise RuntimeError("Project member sync executor is shut down")
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._workers, thread_name_prefix="litellm-project-member-sync"
            )
        return self._executor

    async def run[T](self, fn: Callable[..., T], /, **kwargs: Any) -> T:
        """Run ``fn(**kwargs)`` on a pool thread with the caller's contextvars and OTel context."""
        running_loop = asyncio.get_running_loop()
        if self._semaphore is None:
            self._semaphore = asyncio.BoundedSemaphore(self._workers)
            self._loop = running_loop
        elif running_loop is not self._loop:
            raise RuntimeError(
                "Project member sync executor is bound to the event loop that first used it "
                "and cannot be used from a different event loop"
            )
        semaphore, loop = self._semaphore, self._loop

        await semaphore.acquire()
        ctx = contextvars.copy_context()
        otel_ctx = get_otel_context_for_thread()
        try:
            cf = self._get_executor().submit(ctx.run, _call_with_otel_context, otel_ctx, fn, kwargs)
        except BaseException:
            semaphore.release()
            raise
        cf.add_done_callback(lambda done: _release_on_loop(loop, semaphore, done))
        return await asyncio.shield(asyncio.wrap_future(cf))

    async def shutdown(self) -> None:
        """Stop accepting calls and wait, off the event loop, for running calls to finish."""
        self._closed = True
        if self._executor is not None:
            await asyncio.to_thread(self._executor.shutdown, wait=True)


_executor: ProjectMemberSyncExecutor | None = None


def get_project_member_sync_executor() -> ProjectMemberSyncExecutor:
    """Return the process-wide executor. Its thread pool is only built on first ``run``."""
    global _executor  # noqa: PLW0603
    if _executor is None:
        _executor = ProjectMemberSyncExecutor()
    return _executor


async def shutdown_project_member_sync_executor() -> None:
    """Shut the process-wide executor down; later ``get_`` calls return the same closed instance."""
    await get_project_member_sync_executor().shutdown()
