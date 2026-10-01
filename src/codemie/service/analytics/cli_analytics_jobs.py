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

"""Background jobs of the CLI Analytics storage.

The storage adapter decides which jobs it needs (PostgreSQL: rollup refresh and partition
maintenance; ClickHouse: none) and takes care of running each on one pod only. This
module schedules them with APScheduler and ties them to the application lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from codemie.configs.customer_config import customer_config
from codemie.repository.cli_analytics.factory import get_cli_analytics_storage
from codemie.repository.cli_analytics.ports import CliAnalyticsRuntime, CliAnalyticsStorageConfigError, ScheduledJob

logger = logging.getLogger(__name__)


def _guarded(job: ScheduledJob) -> Callable[[], Awaitable[None]]:
    async def run() -> None:
        try:
            await job.run()
        except Exception:
            logger.exception(f"cli_analytics: background job {job.job_id} failed")

    return run


class CliAnalyticsJobsScheduler:
    def __init__(self, scheduler: AsyncIOScheduler, runtime: CliAnalyticsRuntime) -> None:
        self._scheduler = scheduler
        self._runtime = runtime
        self.startup: asyncio.Task | None = None  # the storage preparing itself (migrations, partitions)

    @property
    def runtime(self) -> CliAnalyticsRuntime:
        return self._runtime

    def start(self) -> None:
        for job in self._runtime.jobs():
            self._scheduler.add_job(
                _guarded(job),
                trigger=IntervalTrigger(seconds=job.interval_seconds),
                id=job.job_id,
                name=job.job_id,
                replace_existing=True,
                max_instances=1,  # a run still going when the next is due skips the next
                coalesce=True,
                # Run late rather than never: by default APScheduler drops a run that starts
                # more than 1 s late, e.g. when the event loop was busy at that moment.
                misfire_grace_time=None,
            )
            logger.info(f"cli_analytics: scheduled {job.job_id} every {job.interval_seconds}s")
        if not self._scheduler.running:
            self._scheduler.start()

    def stop(self) -> None:
        self._scheduler.shutdown(wait=False)


async def start_cli_analytics_runtime() -> CliAnalyticsJobsScheduler | None:
    """Prepare the configured storage and schedule its background jobs, if it has any.

    Nothing is built while the CLI Analytics feature is disabled: its endpoints answer 404,
    so a storage would only hold connections and run jobs for nobody. The flag comes from
    YAML or the environment and cannot change while the process runs.
    """
    if not customer_config.is_feature_enabled("cliAnalytics"):
        return None
    try:
        runtime = get_cli_analytics_storage().runtime
    except CliAnalyticsStorageConfigError as exc:
        # The configured storage refuses its configuration (its endpoints fail with it); the
        # application itself still starts.
        logger.error(f"cli_analytics: the configured analytics storage cannot be used: {exc}")
        return None
    if runtime is None:
        return None
    jobs = CliAnalyticsJobsScheduler(AsyncIOScheduler(), runtime)
    jobs.start()
    # The application does not wait: an unreachable analytics database only delays analytics.
    jobs.startup = asyncio.create_task(runtime.start())
    return jobs


async def stop_cli_analytics_runtime(jobs: CliAnalyticsJobsScheduler | None) -> None:
    """Stop the jobs and close the storage; never raises into the application shutdown."""
    if jobs is None:
        return
    try:
        jobs.stop()
        if jobs.startup is not None and not jobs.startup.done():
            jobs.startup.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await jobs.startup
        await jobs.runtime.aclose()
    except Exception:
        logger.exception("cli_analytics: the analytics storage did not shut down cleanly")
