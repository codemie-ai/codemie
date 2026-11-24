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

"""The analytics connection pool: dedicated, lazily created, never the application pool.

Analytics load (bursts of ingest, 30 s dashboard queries) must not be able to starve the
application's SQLAlchemy pool, so the adapter keeps its own asyncpg pool with its own
size, `search_path`, `work_mem` and `statement_timeout`.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

import asyncpg

from codemie.clients.postgres import _get_iam_token
from codemie.configs.config import config
from codemie.repository.cli_analytics.ports import CliAnalyticsStorageConfigError, DashboardAcquireTimeout
from codemie.repository.cli_analytics.postgres import request_stats
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings, lane_sizes

PoolFactory = Callable[..., Awaitable[Any]]

# Client-side limit on top of the server's statement_timeout, so a dead connection cannot
# hang a request forever while a slow statement still gets cancelled by the server first.
# A statement run under a raised SET LOCAL statement_timeout must pass timeout= with the
# same margin: without it, asyncpg applies the pool's command_timeout (a dashboard query's).
CLIENT_TIMEOUT_MARGIN_S = 5.0
# Shutdown waits this long for connections still in use before closing them forcibly.
CLOSE_TIMEOUT_S = 10.0
# Idle connections are kept this long (asyncpg's default is 5 minutes). Ingest's acquire timeout
# also covers opening a connection (TLS, an IAM token), so a warm pool keeps it for the waiting.
IDLE_CONNECTION_LIFETIME_S = 1800
# One wait bounds the cap and the pool together; a caller that passes no timeout waits this long.
ACQUIRE_TIMEOUT_S = 30.0
DASHBOARD = "dashboard"
JOB = "job"
# Set inside `job_scope()`: the work of a background job, whose engine acquires then count as jobs.
_job_scope: ContextVar[str | None] = ContextVar("cli_analytics_job_scope", default=None)
# Set while this context holds one of the jobs' slots: a job that acquires again (the advisory lock's connection, then
# a rollup path inside it) takes no second slot, which two such jobs could wait for from each other.
_holds_jobs_slot: ContextVar[bool] = ContextVar("cli_analytics_holds_jobs_slot", default=False)
# The connection a job holds under its advisory lock, and whether a nested acquire has it out: the work the job does
# inside the lock (a rollup build) takes that connection again instead of a second cap token and connection.
_held_job_connection: ContextVar[list | None] = ContextVar("cli_analytics_held_job_connection", default=None)
_DRIVER_REFUSED = "the analytics database driver refuses the analytics database URL's settings"


@contextlib.contextmanager
def job_scope() -> Iterator[None]:
    """Acquires made unnamed inside the block (by a reader a job uses) are jobs', not dashboard requests'."""
    token = _job_scope.set(JOB)
    try:
        yield
    finally:
        _job_scope.reset(token)


async def _iam_password(host: str | None, port: int, user: str | None) -> str:
    """A fresh IAM token for the analytics database; the provider call blocks, so it runs in a thread."""
    return await asyncio.to_thread(_get_iam_token, host=host, port=port, user=user)


class AnalyticsPgEngine:
    def __init__(self, settings: AnalyticsPgSettings, pool_factory: PoolFactory = asyncpg.create_pool) -> None:
        self.settings = settings
        self._pool_factory = pool_factory
        self._pool: Any = None
        self._lock = asyncio.Lock()
        sizes = lane_sizes(
            settings.pool_size,
            settings.ingest_reserve,
            settings.heavy_slots,
            settings.light_slots,
            settings.jobs_slots,
        )
        # Dashboard requests and jobs share pool - reserve connections (jobs also their own allowance); ingest has the
        # rest to itself. Lane gates in the service layer queue requests before they reach this cap.
        capacity = max(1, settings.pool_size - max(0, settings.ingest_reserve))
        self._cap = asyncio.Semaphore(capacity)
        self._jobs = asyncio.Semaphore(min(sizes.jobs, capacity))

    async def _get_pool(self) -> Any:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    kwargs: dict[str, Any] = {
                        "dsn": self.settings.dsn,
                        # No connection is opened eagerly: a database that is down at startup
                        # turns into per-request errors (503 on ingest) instead of a failed boot.
                        "min_size": 0,
                        "max_size": self.settings.pool_size,
                        "server_settings": self.settings.server_settings(),
                        "command_timeout": self.settings.statement_timeout_ms / 1000 + CLIENT_TIMEOUT_MARGIN_S,
                        "max_inactive_connection_lifetime": IDLE_CONNECTION_LIFETIME_S,
                    }
                    if self.settings.iam_auth:
                        kwargs["password"] = functools.partial(_iam_password, *self.settings.iam_endpoint)
                    self._pool = await self._pool_factory(**kwargs)
        return self._pool

    @asynccontextmanager
    async def acquire(
        self, timeout: float | None = None, *, caller: str | None = None, own_connection: bool = False
    ) -> AsyncIterator[asyncpg.Connection]:
        """A pooled connection for a dashboard request or a job, from the share of the pool ingest leaves them.

        At most `pool - reserve` such connections are held at once, and a job (`caller="job"`) also takes one of the
        jobs' slots first (not again inside a job that holds one). `timeout` (30 s when None, for a dashboard request
        `CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S`) bounds the wait for the cap and for a free pooled connection
        together (TimeoutError). Only `acquire_ingest` is outside the cap. Unnamed, the caller is a job inside
        `job_scope()`, a dashboard inside a dashboard request, and a job otherwise. A job inside its advisory lock
        gets the connection that holds the lock for a nested acquire (no second cap token); `own_connection` asks for a
        connection of its own anyway, which an advisory lock needs: the server's advisory locks are re-entrant within
        one session, so the same connection would grant the same lock twice instead of reporting it busy.
        """
        stats = request_stats.current()
        caller = caller or _job_scope.get() or ("dashboard" if stats is not None else "job")
        if caller not in (DASHBOARD, JOB):
            raise ValueError(f"acquire is for dashboard requests and jobs, not {caller!r}; ingest uses acquire_ingest")
        if timeout is None:
            timeout = config.CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S if caller == DASHBOARD else ACQUIRE_TIMEOUT_S
        held = _held_job_connection.get() if caller == JOB and not own_connection else None
        if held is not None and not held[1]:  # [connection, in use by a nested acquire]
            held[1] = True
            try:
                yield held[0]
            finally:
                held[1] = False
            return
        async with self._open(caller, timeout, capped=True) as connection:
            yield connection

    @asynccontextmanager
    async def acquire_ingest(self, timeout: float) -> AsyncIterator[asyncpg.Connection]:
        """A pooled connection for an ingest transaction: the one acquire outside the cap, so the reserve is always
        there for it. `timeout` bounds the wait for a free pooled connection."""
        async with self._open("ingest", timeout, capped=False) as connection:
            yield connection

    @asynccontextmanager
    async def _open(self, caller: str, timeout: float, *, capped: bool) -> AsyncIterator[asyncpg.Connection]:
        """The cap (when `capped`), then a pooled connection, within `timeout` in all; the in-use gauge labelled
        `caller`. The wait is added to the request's stats, and the connection counts its statements into them until it
        goes back to the pool."""
        stats = request_stats.current()
        async with contextlib.AsyncExitStack() as stack:
            started = time.monotonic()
            deadline = started + timeout
            request_stats.pool_waiting(1)
            try:
                if capped:
                    await self._take_slots(stack, caller, deadline)
                try:
                    connection = await self._connect(stack, max(0.0, deadline - time.monotonic()))
                except TimeoutError:  # the pool's own wait, after the cap's took most of the time
                    if caller == DASHBOARD:
                        raise DashboardAcquireTimeout(
                            "the dashboard request waited too long for the analytics pool"
                        ) from None
                    raise
            finally:
                waited = time.monotonic() - started
                request_stats.pool_waiting(-1)
                if stats is not None:
                    stats.pool_wait += waited
                if caller == "ingest":
                    request_stats.ingest_acquire_waited(waited)
            request_stats.pool_in_use(caller, 1)
            stack.callback(request_stats.pool_in_use, caller, -1)
            if stats is not None:
                log = request_stats.connection_logger(stats)
                connection.add_query_logger(log)
                stack.callback(connection.remove_query_logger, log)
            yield connection

    async def _take_slots(self, stack: contextlib.AsyncExitStack, caller: str, deadline: float) -> None:
        """The jobs' slot (a job that holds none yet), then the cap, each given back when `stack` closes. A wait past
        `deadline` is a TimeoutError, a `DashboardAcquireTimeout` for a dashboard request."""
        gates = [self._cap]
        if caller == JOB and not _holds_jobs_slot.get():
            gates.insert(0, self._jobs)
        for gate in gates:
            try:
                await asyncio.wait_for(gate.acquire(), max(0.0, deadline - time.monotonic()))
            except TimeoutError:
                if caller == DASHBOARD:
                    raise DashboardAcquireTimeout(
                        "the dashboard request waited too long for the analytics pool"
                    ) from None
                raise
            stack.callback(gate.release)
            if gate is self._jobs:
                token = _holds_jobs_slot.set(True)
                stack.callback(_holds_jobs_slot.reset, token)

    async def _connect(self, stack: contextlib.AsyncExitStack, timeout: float) -> asyncpg.Connection:
        try:
            pool = await self._get_pool()
            return await stack.enter_async_context(pool.acquire(timeout=timeout))
        except TimeoutError:
            raise  # the wait for a free pooled connection ran out; `_open` names it for a dashboard request
        except OSError:
            raise  # the network or TLS (a server certificate failing verification is also a ValueError)
        except asyncpg.PostgresError:
            raise  # a server error during connect (e.g. auth failure); handled by the caller
        except ValueError as exc:
            raise CliAnalyticsStorageConfigError(f"{_DRIVER_REFUSED} ({type(exc).__name__})") from None
        except asyncpg.InterfaceError:
            raise  # a non-configuration connection failure; handled by the caller
        except Exception as exc:
            raise CliAnalyticsStorageConfigError(
                f"analytics database authentication failed ({type(exc).__name__})"
            ) from exc

    def lock_key(self, name: str) -> int:
        return self.settings.lock_key(name)

    @asynccontextmanager
    async def advisory_lock(self, name: str) -> AsyncIterator[asyncpg.Connection | None]:
        """Try to take a session-level advisory lock for the duration of the block.

        Yields the connection holding the lock, for the job to do its work on, or None
        when another pod holds it and this one skips the work. A job therefore needs one
        pooled connection, so even a pool of one cannot deadlock on its own jobs. The
        lock is released before the connection returns to the pool (asyncpg's reset
        would also release it).
        """
        key = self.lock_key(name)
        async with self.acquire(caller=JOB, own_connection=True) as connection:
            acquired = bool(await connection.fetchval("SELECT pg_try_advisory_lock($1)", key))
            token = _held_job_connection.set([connection, False]) if acquired else None
            try:
                yield connection if acquired else None
            finally:
                if token is not None:
                    _held_job_connection.reset(token)
                if acquired:
                    await connection.execute("SELECT pg_advisory_unlock($1)", key)

    async def close(self, timeout: float = CLOSE_TIMEOUT_S) -> None:
        """Close the pool; connections still in use after `timeout` are closed forcibly."""
        pool, self._pool = self._pool, None
        if pool is None:
            return
        try:
            await asyncio.wait_for(pool.close(), timeout)
        except TimeoutError:
            pool.terminate()
