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

"""Live check of the lanes (EPMCDME-17301): the real Projects and Session Detail handlers, jobs and ingest on one real
pool of 8, over the seed of the ranking perf test (5,763 sessions in 1,237 projects).

Skipped, not failed, when no local analytics Postgres is reachable (`TEST_LOCAL_PG_URL`).
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import patch

import pytest

from codemie.configs.config import config as app_config
from codemie.repository.cli_analytics.postgres import user_rollup
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.dashboard_cache import reset_dashboard_cache
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.postgres import test_live_dashboard_aggregates as live
from tests.codemie.repository.cli_analytics.postgres.test_live_ranking_perf import PER_PAGE, _BulkSeed
from tests.codemie.repository.cli_analytics.postgres.test_live_ranking_rollup import VERSION, _forget_days, _Window

pytestmark = pytest.mark.skipif(
    not live._LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against"
)


@pytest.mark.asyncio
async def test_four_projects_requests_session_detail_and_the_jobs_never_refuse_ingest(monkeypatch):
    engine, seed = await live._engine(), _BulkSeed()
    window = _Window(seed)
    # The pool and the lanes are the engine's own (a pool of 8, reserve 2), the ingest acquire timeout the configured one.
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_POOL_SIZE", engine.settings.pool_size)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_INGEST_RESERVE", engine.settings.ingest_reserve)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_HEAVY_SLOTS", engine.settings.heavy_slots)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_LIGHT_SLOTS", engine.settings.light_slots)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_JOBS_SLOTS", engine.settings.jobs_slots)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S", 120)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 240)
    # The rollup is not built for the seed: the requests take the live statements of the heavy lane, the real workload.
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", False)
    ingest_timeout = engine.settings.ingest_acquire_timeout_s
    refused = 0
    served: list[object] = []

    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        handler = LocalAnalyticsHandler(reader)
        reset_dashboard_cache()

        async def projects(per_page: int):  # a different page size each: identical requests would share one computation
            data, _ = await handler.get_projects(window.filter(), 0, per_page, None)
            served.append(data)

        async def session_detail():  # the light lane
            data, _, _ = await handler.get_session_detail(seed.sid(0), scope=user_rollup.ROLLUP_SCOPE)
            served.append(data)

        release = asyncio.Event()

        async def job():  # a job holding its allowance (and the advisory lock's connection) for the whole run
            async with engine.advisory_lock(f"lanes-test-{id(release)}"):
                await release.wait()

        async def ingest_probe(done: asyncio.Event):  # until the dashboard requests are over
            nonlocal refused
            while not done.is_set():
                try:
                    async with engine.acquire_ingest(timeout=ingest_timeout) as conn:
                        await conn.fetchval("SELECT 1")
                except TimeoutError:
                    refused += 1
                await asyncio.sleep(0.05)

        jobs = [asyncio.create_task(job()) for _ in range(2)]
        try:
            await asyncio.sleep(0.2)
            done = asyncio.Event()
            probe = asyncio.create_task(ingest_probe(done))
            async with asyncio.timeout(300):
                with patch(live._PRICING, live._lookup):
                    await asyncio.gather(*(projects(PER_PAGE + n) for n in range(4)), session_detail())
            done.set()
            await probe
        finally:
            release.set()
            with contextlib.suppress(Exception):
                await asyncio.gather(*jobs)
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, window.days)
        reset_dashboard_cache()
        await engine.close()
    assert len(served) == 5 and all(served), "a request returned nothing"
    assert refused == 0
