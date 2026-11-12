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

The storage adapter decides which jobs it needs (rollup refresh and partition
maintenance) and takes care of running each on one pod only. This
module schedules them with APScheduler and ties them to the application lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from codemie.configs.config import config
from codemie.configs.customer_config import customer_config
from codemie.repository.cli_analytics.factory import get_cli_analytics_storage
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import (
    CliAnalyticsRuntime,
    CliAnalyticsStorageConfigError,
    ModelRates,
    ScheduledJob,
)
from codemie.repository.cli_analytics.postgres.rates_version import RatesVersionProvider, rates_version
from codemie.repository.cli_analytics.postgres.user_rollup import ROLLUP_SCOPE
from codemie.service.analytics.dashboard_cache import (
    get_ranking_cache,
    rollup_key,
    rollup_max_age_s,
    rollup_recently_used,
)
from codemie.service.analytics.time_parser import TimeParser
from codemie.service.analytics.handlers.coding_agent_pricing import catalog_rate_fingerprint, model_rates_for
from codemie.service.analytics.delivery_framework import classify_delivery_framework

logger = logging.getLogger(__name__)


def _guarded(job: ScheduledJob) -> Callable[[], Awaitable[None]]:
    async def run() -> None:
        try:
            await job.run()
        except Exception:
            logger.exception(f"cli_analytics: background job {job.job_id} failed")

    return run


WARM_PERIODS = ("last_7_days", "last_30_days")


async def warm_user_rollup(reader: Any) -> None:
    """Keep the user rollup's answer for the usual windows in the in-memory cache, so a first load of the Users tab only
    formats it. Only while the rollup is in use (a request asked for it lately): an idle system does no extra work."""
    cache = get_ranking_cache()
    if not (cache.enabled and config.CLI_ANALYTICS_USER_ROLLUP_ENABLED and rollup_recently_used()):
        return
    ttl_s = 3.0 * config.CLI_ANALYTICS_USER_ROLLUP_WARM_S
    for period in WARM_PERIODS:
        start, end = TimeParser.parse(period, None, None)
        f = LocalAnalyticsFilter(scope=ROLLUP_SCOPE, start_dt=start, end_dt=end)
        served = await reader.rollup_users(f, model_rates_for, rollup_max_age_s())
        if served is not None:
            cache.put(rollup_key(f), served, ttl_s=ttl_s)
        # The Overview is the tab the CLI analytics page opens on: its rollup answer is kept warm as well. A failure
        # here (a timeout, a broken connection) is logged and does not skip the remaining windows.
        try:
            overview = await reader.rollup_overview(f, model_rates_for, rollup_max_age_s())
        except Exception as exc:  # noqa: BLE001 - the warm-up is best effort
            logger.warning(f"cli_analytics: overview rollup not warmed for {period}: {exc!r}")
            continue
        if overview is not None:
            cache.put(rollup_key(f, "overview-rollup"), overview, ttl_s=ttl_s)


class CliAnalyticsJobsScheduler:
    def __init__(
        self, scheduler: AsyncIOScheduler, runtime: CliAnalyticsRuntime, extra_jobs: list[ScheduledJob] | None = None
    ) -> None:
        self._scheduler = scheduler
        self._runtime = runtime
        self._extra_jobs = extra_jobs or []
        self.startup: asyncio.Task | None = None  # the storage preparing itself (migrations, partitions)

    @property
    def runtime(self) -> CliAnalyticsRuntime:
        return self._runtime

    def start(self) -> None:
        for job in [*self._runtime.jobs(), *self._extra_jobs]:
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


def model_rates_by_name(names: Iterable[str]) -> dict[str, ModelRates]:
    """The `RatesFor` of the stored session sort keys: each raw model name resolved with `model_rates_for`, exactly as
    a request resolves the raw names of its window, so aliases and normalised names are priced alike."""
    return {name: model_rates_for(name) for name in dict.fromkeys(names)}


def catalog_rates_version() -> str:
    """The version of everything the rate resolution reads (see `catalog_rate_fingerprint`): it changes whenever the
    rates of any raw model name can change, so a stamp equal to it names the rates `model_rates_by_name` answers."""
    return rates_version(catalog_rate_fingerprint())


async def start_cli_analytics_runtime() -> CliAnalyticsJobsScheduler | None:
    """Prepare the configured storage and schedule its background jobs, if it has any.

    Nothing is built while the CLI Analytics feature is disabled: its endpoints answer 404,
    so a storage would only hold connections and run jobs for nobody. The flag comes from
    YAML or the environment and cannot change while the process runs.
    """
    if not customer_config.is_feature_enabled("cliAnalytics"):
        return None
    try:
        storage = get_cli_analytics_storage()
        runtime = storage.runtime
    except CliAnalyticsStorageConfigError as exc:
        # The configured storage refuses its configuration (its endpoints fail with it); the
        # application itself still starts.
        logger.error(f"cli_analytics: the configured analytics storage cannot be used: {exc}")
        return None
    if runtime is None:
        return None
    # The repository layer never imports the service layer: the refresher gets the classifier here.
    runtime.set_classifier(classify_delivery_framework)
    # Likewise the rates: one version provider, so the catalog is hashed once while it does not change, and the
    # request-time resolution of each raw model name, so a stored cost key equals today's statement.
    current_rates_version = RatesVersionProvider(catalog_rate_fingerprint).current
    runtime.set_rates(current_rates_version, rates=model_rates_by_name)
    storage.reader.set_rates(current_rates_version)
    extra = []
    if config.CLI_ANALYTICS_USER_ROLLUP_ENABLED and config.CLI_ANALYTICS_USER_ROLLUP_WARM_S > 0:
        reader = storage.reader
        extra.append(
            ScheduledJob(
                "cli_analytics_user_rollup_warm",
                config.CLI_ANALYTICS_USER_ROLLUP_WARM_S,
                lambda: warm_user_rollup(reader),
            )
        )
    jobs = CliAnalyticsJobsScheduler(AsyncIOScheduler(), runtime, extra)
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
