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

"""Lifecycle of the PostgreSQL analytics storage: startup, background jobs, shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import threading
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.cli_analytics.ports import CliAnalyticsRuntime
from codemie.repository.cli_analytics.postgres import runtime as runtime_module
from codemie.repository.cli_analytics.postgres.maintenance import MaintenanceReport
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from tests.codemie.repository.cli_analytics.postgres.test_engine import SETTINGS


class LockingEngine:
    """Grants or refuses the advisory lock; records which locks were requested."""

    def __init__(self, leader: bool = True) -> None:
        self.settings = SETTINGS
        self.leader = leader
        self.locks: list[str] = []
        self.close = AsyncMock()
        self.lock_connection = MagicMock(name="connection holding the lock")

    @asynccontextmanager
    async def advisory_lock(self, name: str):
        self.locks.append(name)
        yield self.lock_connection if self.leader else None


def _runtime(engine: LockingEngine, migrate=None) -> tuple[PostgresAnalyticsRuntime, MagicMock, MagicMock]:
    migrate = migrate or MagicMock()
    runtime = PostgresAnalyticsRuntime(engine, SETTINGS, migrate=migrate)  # type: ignore[arg-type]
    runtime._refresher = MagicMock(refresh=AsyncMock(return_value=0), backlog=AsyncMock(return_value=(0, None)))
    runtime._maintainer = MagicMock(run=AsyncMock(return_value=MaintenanceReport(defaults_ready=True)))
    return runtime, migrate, runtime._maintainer


def test_runtime_implements_the_runtime_port():
    assert isinstance(PostgresAnalyticsRuntime(LockingEngine(), SETTINGS, migrate=MagicMock()), CliAnalyticsRuntime)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_start_migrates_then_prepares_partitions():
    runtime, migrate, maintainer = _runtime(LockingEngine())

    await runtime.start()

    migrate.assert_called_once_with(SETTINGS)
    maintainer.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_migration_is_logged_not_raised_and_retried_by_the_next_refresh():
    migrate = MagicMock(side_effect=[OSError("database is down"), OSError("still down"), None])
    runtime, _, maintainer = _runtime(LockingEngine(), migrate)

    await runtime.start()  # must not raise: the application keeps starting
    maintainer.run.assert_not_awaited()
    await runtime.refresh_rollups()  # retries the schema; nothing to refresh while it is missing
    runtime._refresher.refresh.assert_not_awaited()

    await runtime.refresh_rollups()  # 30 s later, not an hour later

    assert migrate.call_count == 3
    maintainer.run.assert_awaited_once()
    runtime._refresher.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_migration_waiting_for_another_pods_lock_does_not_hold_up_shutdown():
    # A migration waits up to 2 minutes for another pod's migration lock. The event loop and the
    # interpreter join their executor threads on exit, so a stopping pod must not wait for it.
    entered, release, threads = threading.Event(), threading.Event(), []

    def migrate(_settings) -> None:
        threads.append(threading.current_thread())
        entered.set()
        release.wait(10)

    runtime, _, _ = _runtime(LockingEngine(), migrate)
    startup = asyncio.create_task(runtime.start())
    try:
        while not entered.is_set():
            await asyncio.sleep(0.01)
        startup.cancel()  # the application stops while the migration waits
        with contextlib.suppress(asyncio.CancelledError):
            await startup

        await asyncio.wait_for(asyncio.get_running_loop().shutdown_default_executor(), timeout=1)
        assert threads[0].daemon  # nor does the interpreter wait for it at exit
    finally:
        release.set()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # the thread's death is the point
@pytest.mark.asyncio
async def test_a_migration_thread_that_dies_counts_as_a_failed_migration():
    runtime, _, maintainer = _runtime(LockingEngine(), MagicMock(side_effect=SystemExit))

    await runtime.start()

    maintainer.run.assert_not_awaited()  # the schema is not assumed ready; the next job retries


@pytest.mark.asyncio
async def test_a_failed_partitioning_is_retried_by_the_next_refresh_not_the_next_maintenance():
    # Without DEFAULT partitions ingest has nowhere to write: 503 until partitioning succeeds.
    runtime, _, maintainer = _runtime(LockingEngine())
    maintainer.run.side_effect = [MaintenanceReport(defaults_ready=False), MaintenanceReport(defaults_ready=True)]

    await runtime.start()
    await runtime.refresh_rollups()  # partitions again, then refreshes
    await runtime.refresh_rollups()  # ready: only refreshes

    assert maintainer.run.await_count == 2
    assert runtime._refresher.refresh.await_count == 2


@pytest.mark.asyncio
async def test_a_maintenance_failure_before_a_refresh_does_not_stop_the_refresh():
    runtime, _, maintainer = _runtime(LockingEngine())
    maintainer.run = AsyncMock(side_effect=TimeoutError())  # e.g. an unexpected client-side timeout

    with patch.object(runtime_module, "logger"):
        await runtime.start()
        await runtime.refresh_rollups()  # retries maintenance first, as partitions are not ready

    runtime._refresher.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_each_job_works_on_the_connection_holding_its_lock():
    # A job that held its lock connection while waiting for a second one could deadlock a small pool.
    engine = LockingEngine()
    runtime, _, maintainer = _runtime(engine)
    await runtime.start()

    await runtime.refresh_rollups()
    await runtime.run_maintenance()

    runtime._refresher.refresh.assert_awaited_with(engine.lock_connection)
    runtime._refresher.backlog.assert_awaited_with(engine.lock_connection)
    maintainer.run.assert_awaited_with(engine.lock_connection)


@pytest.mark.asyncio
async def test_start_never_fails_the_application_startup():
    runtime, _, maintainer = _runtime(LockingEngine())
    maintainer.run.side_effect = ConnectionRefusedError("database went away")

    with patch.object(runtime_module, "logger") as logger:
        await runtime.start()

    logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_runs_only_on_the_pod_holding_the_lock():
    leader, follower = LockingEngine(leader=True), LockingEngine(leader=False)
    lead, _, _ = _runtime(leader)
    follow, _, _ = _runtime(follower)
    await lead.start()
    await follow.start()

    await lead.refresh_rollups()
    await follow.refresh_rollups()

    lead._refresher.refresh.assert_awaited_once()
    follow._refresher.refresh.assert_not_awaited()
    assert leader.locks[-1] == "rollup-refresher"


@pytest.mark.asyncio
async def test_maintenance_runs_only_on_the_pod_holding_the_lock():
    runtime, _, maintainer = _runtime(LockingEngine(leader=False))
    runtime._schema_ready = True

    await runtime.run_maintenance()

    maintainer.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stale_rollup_queue_is_reported_by_the_refresh_job_within_minutes():
    # The refresh job runs every 30 s, so a frozen queue shows up minutes after it froze, not hours.
    runtime, _, _ = _runtime(LockingEngine())
    runtime._schema_ready = True
    runtime._refresher.backlog = AsyncMock(return_value=(12_000, 900.0))

    with patch.object(runtime_module, "logger") as logger:
        await runtime.refresh_rollups()

    assert "rollup queue" in logger.warning.call_args.args[0]


@pytest.mark.asyncio
async def test_the_stale_queue_warning_repeats_at_most_every_five_minutes():
    runtime, _, _ = _runtime(LockingEngine())
    runtime._schema_ready = True
    runtime._refresher.backlog = AsyncMock(return_value=(12_000, 900.0))
    clock = iter([1000.0, 1030.0, 1300.1])

    with patch.object(runtime_module.time, "monotonic", side_effect=lambda: next(clock)):
        with patch.object(runtime_module, "logger") as logger:
            for _ in range(3):
                await runtime.refresh_rollups()

    assert logger.warning.call_count == 2  # at 1000 s and again past 1300 s


@pytest.mark.asyncio
async def test_a_queue_that_keeps_up_is_not_reported():
    runtime, _, _ = _runtime(LockingEngine())
    runtime._schema_ready = True
    runtime._refresher.backlog = AsyncMock(return_value=(40, 12.0))

    with patch.object(runtime_module, "logger") as logger:
        await runtime.refresh_rollups()

    logger.warning.assert_not_called()


def test_jobs_follow_the_configured_intervals():
    settings = dataclasses.replace(SETTINGS, rollup_refresh_seconds=15, maintenance_interval_minutes=10)
    runtime = PostgresAnalyticsRuntime(LockingEngine(), settings, migrate=MagicMock())  # type: ignore[arg-type]

    jobs = {job.job_id: job for job in runtime.jobs()}

    assert jobs["cli_analytics_rollup_refresh"].interval_seconds == 15
    assert jobs["cli_analytics_rollup_refresh"].run == runtime.refresh_rollups
    assert jobs["cli_analytics_maintenance"].interval_seconds == 600
    assert jobs["cli_analytics_maintenance"].run == runtime.run_maintenance


@pytest.mark.asyncio
async def test_close_releases_the_pool():
    engine = LockingEngine()
    runtime, _, _ = _runtime(engine)

    await runtime.aclose()

    engine.close.assert_awaited_once()
