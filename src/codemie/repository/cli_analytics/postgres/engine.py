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
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from codemie.clients.postgres import _get_iam_token
from codemie.repository.cli_analytics.ports import CliAnalyticsStorageConfigError
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

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
_DRIVER_REFUSED = "the analytics database driver refuses the analytics database URL's settings"


async def _iam_password(host: str | None, port: int, user: str | None) -> str:
    """A fresh IAM token for the analytics database; the provider call blocks, so it runs in a thread."""
    return await asyncio.to_thread(_get_iam_token, host=host, port=port, user=user)


class AnalyticsPgEngine:
    def __init__(self, settings: AnalyticsPgSettings, pool_factory: PoolFactory = asyncpg.create_pool) -> None:
        self.settings = settings
        self._pool_factory = pool_factory
        self._pool: Any = None
        self._lock = asyncio.Lock()

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
    async def acquire(self, timeout: float | None = None) -> AsyncIterator[asyncpg.Connection]:
        """A pooled connection; `timeout` bounds the wait for a free one (asyncio.TimeoutError).

        The driver refusing its configuration on connect (asyncpg's ClientConfigurationError and
        its other ValueErrors, e.g. sslmode=verify-full without a root certificate) becomes a
        CliAnalyticsStorageConfigError, whose message quotes nothing from the configuration.
        """
        async with contextlib.AsyncExitStack() as stack:
            try:
                pool = await self._get_pool()
                connection = await stack.enter_async_context(pool.acquire(timeout=timeout))
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
            yield connection

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
        async with self.acquire() as connection:
            acquired = bool(await connection.fetchval("SELECT pg_try_advisory_lock($1)", key))
            try:
                yield connection if acquired else None
            finally:
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
