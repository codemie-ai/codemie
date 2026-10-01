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

from __future__ import annotations

import asyncio
import ssl
import dataclasses
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from codemie.repository.cli_analytics.ports import CliAnalyticsStorageConfigError
from codemie.repository.cli_analytics.postgres import engine as engine_module
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

SETTINGS = AnalyticsPgSettings(
    dsn="postgresql://u:p@h/d",
    schema="codemie_analytics",
    iam_auth=False,
    pool_size=5,
    statement_timeout_ms=30_000,
    work_mem="32MB",
    ingest_acquire_timeout_s=1.0,
    raw_retention_days=90,
    rollup_retention_days=365,
    dedup_retention_days=14,
    rollup_refresh_seconds=30,
    rollup_batch_size=5000,
    partition_premake_weeks=4,
    maintenance_interval_minutes=60,
)


class FakeConnection:
    def __init__(self, lock_granted: bool = True) -> None:
        self.fetchval = AsyncMock(return_value=lock_granted)
        self.execute = AsyncMock()


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.acquire_timeouts: list[float | None] = []
        self.close = AsyncMock()
        self.terminate = MagicMock()

    @asynccontextmanager
    async def acquire(self, timeout=None):
        self.acquire_timeouts.append(timeout)
        yield self.connection


class PoolFactory:
    def __init__(self, connection: FakeConnection | None = None, fail_times: int = 0) -> None:
        self.calls: list[dict] = []
        self.pools: list[FakePool] = []
        self.connection = connection or FakeConnection()
        self.fail_times = fail_times

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(0)  # let concurrent first callers interleave
        if self.fail_times:
            self.fail_times -= 1
            raise OSError("connection refused")
        pool = FakePool(self.connection)
        self.pools.append(pool)
        return pool


@pytest.mark.asyncio
async def test_pool_is_created_on_first_use_with_the_dedicated_settings():
    factory = PoolFactory()
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)
    assert factory.calls == []

    async with engine.acquire() as conn:
        assert conn is factory.connection

    (kwargs,) = factory.calls
    assert kwargs["dsn"] == SETTINGS.dsn
    assert kwargs["min_size"] == 0
    assert kwargs["max_size"] == 5
    assert kwargs["server_settings"] == SETTINGS.server_settings()
    assert kwargs["command_timeout"] == 35.0  # statement_timeout plus a client-side margin
    # Idle connections stay open well past the jobs' intervals, so ingest seldom has to connect
    # within its short acquire timeout.
    assert kwargs["max_inactive_connection_lifetime"] == 1800
    assert "password" not in kwargs


@pytest.mark.asyncio
async def test_concurrent_first_use_creates_a_single_pool():
    factory = PoolFactory()
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)

    async def use():
        async with engine.acquire():
            pass

    await asyncio.gather(*(use() for _ in range(10)))

    assert len(factory.calls) == 1


@pytest.mark.asyncio
async def test_failed_pool_creation_is_retried_on_next_use():
    factory = PoolFactory(fail_times=1)
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)

    with pytest.raises(OSError):
        async with engine.acquire():
            pass
    async with engine.acquire():
        pass

    assert len(factory.calls) == 2


@pytest.mark.asyncio
async def test_iam_auth_supplies_a_fresh_token_per_connection():
    factory = PoolFactory()
    engine = AnalyticsPgEngine(dataclasses.replace(SETTINGS, iam_auth=True), pool_factory=factory)

    async with engine.acquire():
        pass
    password = factory.calls[0]["password"]
    with patch.object(engine_module, "_get_iam_token", side_effect=["token-1", "token-2"]):
        assert [await password(), await password()] == ["token-1", "token-2"]


@pytest.mark.asyncio
async def test_iam_tokens_are_minted_for_the_analytics_database():
    factory = PoolFactory()
    settings = dataclasses.replace(SETTINGS, iam_auth=True, dsn="postgresql://an_user@an.example:6432/an")
    engine = AnalyticsPgEngine(settings, pool_factory=factory)

    async with engine.acquire():
        pass
    with patch.object(engine_module, "_get_iam_token", return_value="token") as mint:
        assert await factory.calls[0]["password"]() == "token"

    mint.assert_called_once_with(host="an.example", port=6432, user="an_user")


@pytest.mark.asyncio
async def test_a_driver_refusing_its_configuration_on_connect_is_a_storage_configuration_error():
    # E.g. sslmode=verify-full without a root certificate: the router answers 503, not 400 or 500.
    factory = PoolFactory()
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)
    async with engine.acquire():
        pass
    refusal = asyncpg.exceptions.ClientConfigurationError("`sslrootcert`: /secret/path not found")

    @asynccontextmanager
    async def refuse(timeout=None):
        raise refusal
        yield

    factory.pools[0].acquire = refuse
    with pytest.raises(CliAnalyticsStorageConfigError) as refused:
        async with engine.acquire():
            pass

    assert "/secret/path" not in str(refused.value) and refused.value.__suppress_context__


@pytest.mark.asyncio
async def test_a_certificate_that_fails_verification_is_not_taken_for_a_configuration_error():
    # ssl.SSLCertVerificationError is an OSError and a ValueError: an expired or rotated server
    # certificate is a connection problem, and must say so.
    factory = PoolFactory()
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)
    async with engine.acquire():
        pass

    @asynccontextmanager
    async def fail_verification(timeout=None):
        raise ssl.SSLCertVerificationError("certificate has expired")
        yield

    factory.pools[0].acquire = fail_verification
    with pytest.raises(ssl.SSLCertVerificationError):
        async with engine.acquire():
            pass


@pytest.mark.asyncio
async def test_a_configuration_error_names_the_drivers_error_class_only():
    factory = PoolFactory()
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)
    async with engine.acquire():
        pass

    @asynccontextmanager
    async def refuse(timeout=None):
        raise asyncpg.exceptions.ClientConfigurationError("sslrootcert /secret/path not found")
        yield

    factory.pools[0].acquire = refuse
    with pytest.raises(CliAnalyticsStorageConfigError, match="ClientConfigurationError") as refused:
        async with engine.acquire():
            pass

    assert "/secret/path" not in str(refused.value)


@pytest.mark.asyncio
async def test_iam_token_failure_becomes_a_storage_configuration_error():
    """A botocore/cloud-SDK error during IAM authentication wraps to CliAnalyticsStorageConfigError."""
    factory = PoolFactory()
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)
    async with engine.acquire():
        pass

    @asynccontextmanager
    async def fail_iam(timeout=None):
        raise RuntimeError("Unable to locate credentials")
        yield

    factory.pools[0].acquire = fail_iam
    with pytest.raises(CliAnalyticsStorageConfigError, match="authentication failed") as exc_info:
        async with engine.acquire():
            pass

    assert exc_info.value.__cause__ is not None


@pytest.mark.asyncio
async def test_a_value_error_inside_the_block_is_not_taken_for_a_configuration_error():
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=PoolFactory())

    with pytest.raises(ValueError, match="the caller's own"):
        async with engine.acquire():
            raise ValueError("the caller's own")


@pytest.mark.asyncio
async def test_acquire_passes_the_timeout_to_the_pool():
    factory = PoolFactory()
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)

    async with engine.acquire(timeout=0.5):
        pass

    assert factory.pools[0].acquire_timeouts == [0.5]


@pytest.mark.asyncio
async def test_advisory_lock_yields_the_connection_holding_it_and_releases_it_after():
    conn = FakeConnection(lock_granted=True)
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=PoolFactory(conn))

    async with engine.advisory_lock("rollup-refresher") as holder:
        assert holder is conn  # the job works on it: one pooled connection per job
        conn.execute.assert_not_awaited()

    key = engine.lock_key("rollup-refresher")
    conn.fetchval.assert_awaited_once_with("SELECT pg_try_advisory_lock($1)", key)
    conn.execute.assert_awaited_once_with("SELECT pg_advisory_unlock($1)", key)


@pytest.mark.asyncio
async def test_advisory_lock_is_released_when_the_block_fails():
    conn = FakeConnection(lock_granted=True)
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=PoolFactory(conn))

    with pytest.raises(RuntimeError):
        async with engine.advisory_lock("maintenance"):
            raise RuntimeError("job failed")

    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_advisory_lock_held_elsewhere_yields_none_and_is_not_released():
    conn = FakeConnection(lock_granted=False)
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=PoolFactory(conn))

    async with engine.advisory_lock("maintenance") as holder:
        assert holder is None

    conn.execute.assert_not_awaited()


def test_lock_keys_are_stable_and_scoped_to_schema_and_name():
    a = AnalyticsPgEngine(SETTINGS)
    b = AnalyticsPgEngine(dataclasses.replace(SETTINGS, schema="other"))

    assert a.lock_key("x") == AnalyticsPgEngine(SETTINGS).lock_key("x")
    assert len({a.lock_key("x"), a.lock_key("y"), b.lock_key("x")}) == 3
    assert -(2**63) <= a.lock_key("x") < 2**63


@pytest.mark.asyncio
async def test_close_closes_the_pool_once():
    factory = PoolFactory()
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)
    async with engine.acquire():
        pass

    await engine.close()
    await engine.close()

    factory.pools[0].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_without_a_pool_does_nothing():
    await AnalyticsPgEngine(SETTINGS, pool_factory=PoolFactory()).close()


@pytest.mark.asyncio
async def test_close_terminates_a_pool_that_does_not_close_in_time():
    factory = PoolFactory()
    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)
    async with engine.acquire():
        pass
    pool = factory.pools[0]

    async def never_closes():  # a connection is never released
        await asyncio.sleep(3600)

    pool.close = AsyncMock(side_effect=never_closes)

    await engine.close(timeout=0.05)

    pool.terminate.assert_called_once()
