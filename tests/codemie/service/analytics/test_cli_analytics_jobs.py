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

"""Scheduling of the CLI Analytics storage's background jobs."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.triggers.interval import IntervalTrigger

from codemie.configs.customer_config import CustomerConfig
from codemie.repository.cli_analytics.factory import CliAnalyticsStorage
from codemie.service.customer_config_declarations import by_component_id
from codemie.repository.cli_analytics.ports import CliAnalyticsStorageConfigError, ScheduledJob
from codemie.service.analytics import cli_analytics_jobs
from codemie.service.analytics.cli_analytics_jobs import (
    CliAnalyticsJobsScheduler,
    start_cli_analytics_runtime,
    stop_cli_analytics_runtime,
)
from codemie.service.analytics.delivery_framework import classify_delivery_framework


def _runtime(*jobs: ScheduledJob) -> MagicMock:
    runtime = MagicMock()
    runtime.jobs.return_value = list(jobs)
    runtime.start = AsyncMock()
    runtime.aclose = AsyncMock()
    return runtime


def test_each_job_runs_at_its_interval_one_at_a_time_and_late_rather_than_skipped():
    scheduler = MagicMock(running=False)
    refresh = ScheduledJob("refresh", 30, AsyncMock())
    maintenance = ScheduledJob("maintenance", 3600, AsyncMock())

    CliAnalyticsJobsScheduler(scheduler, _runtime(refresh, maintenance)).start()

    calls = {c.kwargs["id"]: c.kwargs for c in scheduler.add_job.call_args_list}
    assert set(calls) == {"refresh", "maintenance"}
    for job_id, seconds in (("refresh", 30), ("maintenance", 3600)):
        trigger = calls[job_id]["trigger"]
        assert isinstance(trigger, IntervalTrigger)
        assert trigger.interval.total_seconds() == seconds
        assert calls[job_id]["max_instances"] == 1
        assert calls[job_id]["coalesce"] is True
        assert calls[job_id]["replace_existing"] is True
        # APScheduler drops a run whose start is more than 1 s late by default.
        assert calls[job_id].get("misfire_grace_time", "default") is None
    scheduler.start.assert_called_once()


@pytest.mark.asyncio
async def test_a_failing_job_is_logged_and_the_scheduler_keeps_running():
    scheduler = MagicMock(running=True)
    failing = ScheduledJob("refresh", 30, AsyncMock(side_effect=RuntimeError("database gone")))
    CliAnalyticsJobsScheduler(scheduler, _runtime(failing)).start()
    wrapped = scheduler.add_job.call_args.args[0]

    with patch.object(cli_analytics_jobs, "logger") as logger:
        await wrapped()  # must not raise into APScheduler

    assert "refresh" in logger.exception.call_args.args[0]


def test_stop_shuts_the_scheduler_down_without_waiting():
    scheduler = MagicMock(running=True)

    CliAnalyticsJobsScheduler(scheduler, _runtime()).stop()

    scheduler.shutdown.assert_called_once_with(wait=False)


def _feature(enabled: bool):
    return patch.object(CustomerConfig, "is_feature_enabled", return_value=enabled)


@pytest.mark.asyncio
async def test_nothing_is_built_or_started_while_the_cli_analytics_feature_is_disabled():
    get_storage = MagicMock()

    with _feature(False) as is_enabled, patch.object(cli_analytics_jobs, "get_cli_analytics_storage", get_storage):
        assert await start_cli_analytics_runtime() is None

    is_enabled.assert_called_once_with("cliAnalytics")
    get_storage.assert_not_called()  # no connection pool, no migration, no job


def test_the_feature_flag_is_read_at_boot_because_it_cannot_change_at_runtime():
    # start_cli_analytics_runtime checks the cliAnalytics on/off flag once, at boot. The component may
    # declare other settings (classification, personal ingest), but if it ever declares the on/off flag
    # as a dynamic field, the runtime must also be started when an admin switches the feature on.
    declaration = by_component_id("features:cliAnalytics")
    fields = {f.name.lower() for f in declaration.fields} if declaration else set()
    assert "enabled" not in fields


@pytest.mark.asyncio
async def test_a_storage_that_refuses_its_configuration_does_not_stop_the_application():
    # E.g. a URL asking for a security setting the analytics driver cannot apply: analytics
    # fails closed, and the rest of the application still starts.
    refused = MagicMock(side_effect=CliAnalyticsStorageConfigError("the analytics database URL sets channel_binding"))

    with (
        _feature(True),
        patch.object(cli_analytics_jobs, "get_cli_analytics_storage", refused),
        patch.object(cli_analytics_jobs, "logger") as logger,
    ):
        assert await start_cli_analytics_runtime() is None

    assert "channel_binding" in str(logger.error.call_args)


@pytest.mark.asyncio
async def test_a_storage_without_background_work_starts_nothing() -> None:
    storage = CliAnalyticsStorage(reader=MagicMock(), ingestor=MagicMock())

    with _feature(True), patch.object(cli_analytics_jobs, "get_cli_analytics_storage", return_value=storage):
        assert await start_cli_analytics_runtime() is None


@pytest.mark.asyncio
async def test_a_storage_with_a_runtime_is_started_and_scheduled() -> None:
    runtime = _runtime(ScheduledJob("refresh", 30, AsyncMock()))
    storage = CliAnalyticsStorage(reader=MagicMock(), ingestor=MagicMock(), runtime=runtime)

    with (
        _feature(True),
        patch.object(cli_analytics_jobs, "get_cli_analytics_storage", return_value=storage),
        patch.object(cli_analytics_jobs, "AsyncIOScheduler") as scheduler_type,
    ):
        order = MagicMock()  # one parent to read the order of the calls off
        order.attach_mock(runtime.set_classifier, "set_classifier")
        order.attach_mock(runtime.set_rates, "set_rates")
        order.attach_mock(runtime.start, "runtime_start")
        order.attach_mock(scheduler_type.return_value.add_job, "add_job")
        jobs = await start_cli_analytics_runtime()
        await asyncio.sleep(0)  # the storage prepares itself in the background

    runtime.start.assert_awaited_once()
    assert jobs is not None
    # The runtime's own job and the warm-up of the user rollup's answer.
    added = scheduler_type.return_value.add_job.call_args_list
    assert [c.kwargs["id"] for c in added] == ["refresh", "cli_analytics_user_rollup_warm"]
    # The refresher gets the delivery-framework classifier before anything can run it.
    runtime.set_classifier.assert_called_once_with(classify_delivery_framework)
    # The rates version reaches the runtime and the reader as one shared callable.
    (version_source,) = runtime.set_rates.call_args.args
    storage.reader.set_rates.assert_called_once_with(version_source)
    # The stored keys are priced with the request-time resolution of each raw model name.
    assert runtime.set_rates.call_args.kwargs["rates"] is cli_analytics_jobs.model_rates_by_name
    assert [c[0] for c in order.mock_calls] == ["set_classifier", "set_rates", "add_job", "add_job", "runtime_start"]


@pytest.mark.asyncio
async def test_the_application_does_not_wait_for_the_analytics_database_at_startup():
    # A black-holed database or a migration lock held by a stuck pod must not delay startup.
    never = asyncio.Event()
    runtime = _runtime(ScheduledJob("refresh", 30, AsyncMock()))
    runtime.start = AsyncMock(side_effect=never.wait)
    storage = CliAnalyticsStorage(reader=MagicMock(), ingestor=MagicMock(), runtime=runtime)

    with (
        _feature(True),
        patch.object(cli_analytics_jobs, "get_cli_analytics_storage", return_value=storage),
        patch.object(cli_analytics_jobs, "AsyncIOScheduler") as scheduler_type,
    ):
        jobs = await asyncio.wait_for(start_cli_analytics_runtime(), timeout=1)
        assert scheduler_type.return_value.add_job.call_count == 2  # the runtime's job and the rollup warm-up

        await stop_cli_analytics_runtime(jobs)  # a start still pending is cancelled

    runtime.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_stopping_never_fails_the_application_shutdown():
    runtime = _runtime()
    runtime.aclose.side_effect = OSError("connection reset")
    jobs = CliAnalyticsJobsScheduler(MagicMock(running=True), runtime)

    with patch.object(cli_analytics_jobs, "logger") as logger:
        await stop_cli_analytics_runtime(jobs)

    logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_stopping_closes_the_runtime_after_the_scheduler():
    runtime = _runtime()
    jobs = CliAnalyticsJobsScheduler(MagicMock(running=True), runtime)

    await stop_cli_analytics_runtime(jobs)

    jobs._scheduler.shutdown.assert_called_once_with(wait=False)
    runtime.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_stopping_without_a_runtime_does_nothing():
    await stop_cli_analytics_runtime(None)


# ── the warm-up of the user rollup's answer ──


class _WarmReader:
    def __init__(self, served, overview=None, overview_fails_on=None):
        self.served, self.overview, self.overview_fails_on = served, overview, overview_fails_on
        self.windows, self.overview_windows = [], []

    async def rollup_users(self, f, rate_of, max_age_s):
        self.windows.append((f.start_dt, f.scope))
        return self.served

    async def rollup_overview(self, f, rate_of, max_age_s):
        self.overview_windows.append(f.start_dt)
        if self.overview_fails_on == len(self.overview_windows):
            raise RuntimeError("the window's overview read failed")
        return self.overview


def _admin_request_filter(period):
    from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
    from codemie.repository.cli_analytics.scope import ViewerScope
    from codemie.service.analytics.time_parser import TimeParser

    start, end = TimeParser.parse(period, None, None)
    scope = ViewerScope(viewer_email="admin@example.com", unrestricted=True, admin_projects=frozenset(), mode="exclude")
    return LocalAnalyticsFilter(start_dt=start, end_dt=end, scope=scope)


@pytest.mark.asyncio
async def test_the_warm_up_does_nothing_while_nobody_uses_the_rollup():
    from codemie.service.analytics import dashboard_cache as dc
    from codemie.service.analytics.cli_analytics_jobs import warm_user_rollup

    dc.reset_dashboard_cache()
    reader = _WarmReader(object())

    await warm_user_rollup(reader)

    assert reader.windows == []


@pytest.mark.asyncio
async def test_the_warmed_answer_is_found_by_the_key_of_a_real_admin_request():
    from codemie.service.analytics import dashboard_cache as dc
    from codemie.service.analytics.cli_analytics_jobs import WARM_PERIODS, warm_user_rollup

    dc.reset_dashboard_cache()
    dc.mark_rollup_used()
    served = object()
    reader = _WarmReader(served)

    await warm_user_rollup(reader)

    assert len(reader.windows) == len(WARM_PERIODS)
    for period in WARM_PERIODS:
        assert dc.get_ranking_cache().get(dc.rollup_key(_admin_request_filter(period))) is served


@pytest.mark.asyncio
async def test_the_overview_answer_is_warmed_under_the_key_its_request_reads():
    from codemie.service.analytics import dashboard_cache as dc
    from codemie.service.analytics.cli_analytics_jobs import WARM_PERIODS, warm_user_rollup

    dc.reset_dashboard_cache()
    dc.mark_rollup_used()
    overview = object()

    await warm_user_rollup(_WarmReader(None, overview))

    for period in WARM_PERIODS:
        key = dc.rollup_key(_admin_request_filter(period), "overview-rollup")
        assert dc.get_ranking_cache().get(key) is overview


@pytest.mark.asyncio
async def test_nothing_is_warmed_when_the_rollup_cannot_answer():
    from codemie.service.analytics import dashboard_cache as dc
    from codemie.service.analytics.cli_analytics_jobs import warm_user_rollup

    dc.reset_dashboard_cache()
    dc.mark_rollup_used()

    await warm_user_rollup(_WarmReader(None))

    assert dc.get_ranking_cache().get(dc.rollup_key(_admin_request_filter("last_30_days"))) is None


@pytest.mark.asyncio
async def test_an_overview_read_that_fails_does_not_skip_the_remaining_windows():
    from codemie.service.analytics import dashboard_cache as dc
    from codemie.service.analytics.cli_analytics_jobs import WARM_PERIODS, warm_user_rollup

    dc.reset_dashboard_cache()
    dc.mark_rollup_used()
    served, overview = object(), object()
    reader = _WarmReader(served, overview, overview_fails_on=1)

    await warm_user_rollup(reader)  # does not raise

    assert len(reader.windows) == len(WARM_PERIODS) == len(reader.overview_windows)  # every window was tried
    first, second = (_admin_request_filter(p) for p in WARM_PERIODS)
    cache = dc.get_ranking_cache()
    assert cache.get(dc.rollup_key(first)) is served  # the Users answer of the failed window is kept
    assert cache.get(dc.rollup_key(first, "overview-rollup")) is None  # the failed read warmed nothing
    assert cache.get(dc.rollup_key(second, "overview-rollup")) is overview  # the next window was warmed
