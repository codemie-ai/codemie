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

"""Feature runtime owned by the existing management application's lifespan."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress

from pydantic.json import pydantic_encoder
from sqlalchemy import func, select
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from codemie.clients.postgres import PostgresClient, _register_iam_token_event
from codemie.configs import config as application_config
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.settings import HostedSettings
from codemie.repository.hosted_applications.factory import open_hosted_storage
from codemie.repository.hosted_applications.object_storage import HostedObjectStorage
from codemie.repository.hosted_applications.repository import HostedApplicationRepository
from codemie.service.hosted_applications.catalog import HostedApplicationCatalog
from codemie.service.hosted_applications.directory import HostedIdentityDirectory
from codemie.service.hosted_applications.maintenance import MaintenanceWorker
from codemie.service.hosted_applications.sweep import HostedStorageSweep, database_lookup
from codemie.service.hosted_applications.launch import HostedApplicationLaunchService
from codemie.service.hosted_applications.validator_client import ValidatorClient
from codemie.service.hosted_applications.worker import ValidationWorker
from codemie.utils.leader_lock import async_leader_lock

# Hosted traffic has its own capped pool on the core database and schema: content requests never take
# the core API's connections, and a saturated pool fails fast as a retryable 503.
DB_POOL_SIZE = 10
DB_POOL_TIMEOUT_SECONDS = 2.0
# Validation workers per API process. Jobs are leased in PostgreSQL, so every replica can run them.
VALIDATION_WORKERS = 2
# Opening the hosted storage at startup (the filesystem directory, or the bucket with its clock check) may take this
# long; a slow or hung backend keeps the feature closed instead of stalling platform startup (the stuck call stays on
# the storage executor, never the event loop).
STORAGE_START_SECONDS = 10
# The storage sweep lists the whole bucket, so one replica walks it per period: each iteration runs under the
# platform's advisory leader lock (``async_leader_lock``, as the leaderboard and budget schedulers use). The lock
# holds one connection of the core sync pool for at most HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS on the leader.
HOSTED_STORAGE_SWEEP_LOCK_ID = 987654340
# One engine per process (keyed by PID, like the core client): forked children never reuse a parent's pool.
_ENGINES: dict[int, AsyncEngine] = {}


def hosted_engine() -> AsyncEngine:
    """A capped pool for all hosted traffic, isolated from the core pool but on the same database and schema."""
    pid = os.getpid()
    engine = _ENGINES.get(pid)
    if engine is None:
        # Same URL, connect arguments, search_path, serializer and IAM hook as PostgresClient.get_async_engine.
        engine = create_async_engine(
            PostgresClient._get_connection_string(async_mode=True),
            echo=False,
            # Share-link tokens are bound parameters: a failed statement must never print them.
            hide_parameters=True,
            pool_pre_ping=True,
            pool_size=DB_POOL_SIZE,
            max_overflow=0,
            pool_timeout=DB_POOL_TIMEOUT_SECONDS,
            json_serializer=lambda v: json.dumps(v, default=pydantic_encoder),
            connect_args={"server_settings": {"search_path": f"{application_config.DEFAULT_DB_SCHEMA},public"}},
        )
        if application_config.PG_IAM_AUTH_PROVIDER:
            _register_iam_token_event(engine)
        _ENGINES[pid] = engine
    return engine


async def dispose_hosted_engine() -> None:
    engine = _ENGINES.pop(os.getpid(), None)
    if engine is not None:
        await engine.dispose()


async def drain(step, *, maximum, seconds):
    deadline = asyncio.get_running_loop().time() + seconds
    completed = 0
    while completed < maximum and asyncio.get_running_loop().time() < deadline:
        if not await step():
            break
        completed += 1
    return completed


@asynccontextmanager
async def sessions():
    try:
        async with AsyncSession(hosted_engine(), expire_on_commit=False) as session:
            yield session
    except PoolTimeout as exc:
        # Saturation is reported as a bounded retryable condition; it never waits on the core pool.
        raise HostedApplicationError("SERVICE_BUSY", "Applications are busy; retry shortly", 503) from exc


class HostedApplicationsRuntime:
    def __init__(self, config):
        self.settings = HostedSettings.from_config(config)
        self.tasks = []
        self.ready = False
        if not self.settings.enabled:
            return
        self.config = config
        self.repository = HostedApplicationRepository(
            sessions, secret=self.settings.secret, quota_bytes=config.HOSTED_APPS_QUOTA_BYTES
        )
        self.directory = HostedIdentityDirectory(
            sessions,
            config,
        )
        self.catalog = HostedApplicationCatalog(self.repository, self.directory)
        from codemie.service.hosted_applications.administration import HostedApplicationAdministration

        self.administration = HostedApplicationAdministration(self.catalog)
        self.launch = HostedApplicationLaunchService(self.catalog, self.settings)
        self.validator = ValidatorClient(config.HOSTED_APPS_VALIDATOR_URL, config.HOSTED_APPS_VALIDATOR_KEY)
        from codemie.service.hosted_applications.retention import RetentionWorker

        self.retention = RetentionWorker(self.repository)
        from codemie.service.hosted_applications.observability import HostedApplicationsMonitoring

        self.observability = HostedApplicationsMonitoring(sessions)

    async def database_clock(self):
        """PostgreSQL ``clock_timestamp()``: the shared clock the S3 startup skew check compares against."""
        async with sessions() as session:
            return (await session.execute(select(func.clock_timestamp()))).scalar_one()

    async def open_storage(self):
        """Bind the private volume, off the event loop and within a deadline, then the workers that use it."""
        try:
            self.storage = await asyncio.wait_for(
                open_hosted_storage(self.config, self.database_clock), STORAGE_START_SECONDS
            )
        except Exception as exc:
            # Only the code and class name are logged: exception text may hold provider or path details.
            code = exc.code if isinstance(exc, HostedApplicationError) else "STORAGE_UNAVAILABLE"
            from codemie.configs.logger import logger

            logger.error(f"Hosted storage failed to open: code={code}, error={type(exc).__name__}")
            raise
        self.catalog.storage = self.storage
        self.worker = ValidationWorker(self.repository, self.storage, self.validator)
        self.maintenance = MaintenanceWorker(self.repository, self.storage)
        from codemie.service.hosted_applications.retry import RetryWorker

        self.retries = RetryWorker(self.repository, self.storage, self.catalog.storage_namespace)

    async def start(self):
        if not self.settings.enabled:
            return
        await self.open_storage()
        # The queue is durable. Outages are fail-closed and recover without recreating versions.
        for index in range(VALIDATION_WORKERS):
            self.tasks.append(asyncio.create_task(self.work(), name=f"hosted-validation-{index}"))
        self.tasks.append(asyncio.create_task(self.maintain(), name="hosted-maintenance"))
        if isinstance(self.storage, HostedObjectStorage):
            # Its own task: a long bucket walk must never pause cleanup, expiry or retry in maintain_once.
            self.sweep = HostedStorageSweep(
                self.storage,
                database_lookup(self.repository),
                budget_seconds=self.config.HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS,
            )
            self.tasks.append(asyncio.create_task(self.sweep_storage(), name="hosted-storage-sweep"))
        self.ready = True

    async def work(self):
        from codemie.configs.logger import logger

        while True:
            try:
                did_work = await self.observability.run(self.worker.run_one, "validation")
                if not did_work:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Exception text/SQL parameters may contain data: only stable class names are logged.
                logger.warning(f"Hosted validation deferred: {type(exc).__name__}")
                await asyncio.sleep(5)

    async def sweep_storage_once(self):
        """One sweep iteration on the leader replica only; the others skip the period (OS-4 revisited)."""
        async with async_leader_lock(HOSTED_STORAGE_SWEEP_LOCK_ID) as acquired:
            if acquired:
                await self.sweep.run_once()

    async def sweep_storage(self):
        from codemie.configs.logger import logger

        while True:
            try:
                await self.sweep_storage_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Exception text may hold provider details: only the class name is logged.
                logger.warning(f"Hosted storage sweep deferred: {type(exc).__name__}")
            # Readiness refuses a value under 1; this guard keeps a bad value from listing the bucket non-stop.
            await asyncio.sleep(max(1, self.config.HOSTED_APPS_STORAGE_SWEEP_SECONDS))

    async def maintain(self):
        next_retention = 0
        while True:
            retention_due = asyncio.get_running_loop().time() >= next_retention
            if retention_due:
                next_retention = asyncio.get_running_loop().time() + 60
            await self.maintain_once(retention_due=retention_due)
            await asyncio.sleep(5)

    async def maintain_once(self, *, retention_due):
        """Each step is isolated: one failing step (or row) never skips the remaining maintenance."""
        run = self.observability.run
        await self._step("expire", self.maintenance.expire)
        await self._step(
            "cleanup", lambda: drain(lambda: run(self.maintenance.cleanup_one, "cleanup"), maximum=32, seconds=2)
        )
        await self._step(
            "purge", lambda: drain(lambda: run(self.maintenance.finish_purge_one, "purge"), maximum=8, seconds=1)
        )
        await self._step(
            "retention",
            lambda: drain(lambda: run(self.retention.finish_version_one, "retention"), maximum=8, seconds=1),
        )
        await self._step("retry", lambda: drain(lambda: run(self.retries.run_one, "retry"), maximum=4, seconds=1))
        if not retention_due:
            return
        await self._step("schedule_version", lambda: drain(self.retention.schedule_version_one, maximum=25, seconds=2))
        await self._step("gc", self.retention.gc)
        await self._step("observability", self.observability.refresh)

    @staticmethod
    async def _step(name, operation):
        from codemie.configs.logger import logger

        try:
            await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Exception text/SQL parameters may contain data: only the step and stable class names are logged.
            logger.warning(f"Hosted maintenance step deferred: step={name}, error={type(exc).__name__}")

    async def close(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
        if self.settings.enabled:
            await self.validator.close()
        await dispose_hosted_engine()
        self.ready = False
