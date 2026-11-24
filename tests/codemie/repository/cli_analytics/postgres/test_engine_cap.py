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

"""The ingest reserve: every connection but ingest's goes through one cap of pool - reserve (jobs under their own)."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.cli_analytics.ports import CliAnalyticsStorageConfigError
from codemie.repository.cli_analytics.postgres import engine as engine_module
from codemie.repository.cli_analytics.postgres import reader as reader_module
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import _ConnectionPair
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings, LaneSizes, lane_sizes

SETTINGS = AnalyticsPgSettings(
    dsn="postgresql://u:p@h/d",
    schema="codemie_analytics",
    iam_auth=False,
    pool_size=8,
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


class UnlimitedPool:
    """A pool that never makes anyone wait, so every wait seen is the engine's own cap."""

    def __init__(self) -> None:
        self.close = AsyncMock()
        self.terminate = MagicMock()
        self.timeouts: list[float | None] = []

    @asynccontextmanager
    async def acquire(self, timeout=None):
        self.timeouts.append(timeout)
        yield MagicMock(fetchval=AsyncMock(return_value=True), execute=AsyncMock())


def make_engine(**overrides) -> AnalyticsPgEngine:
    pool = UnlimitedPool()

    async def factory(**_kwargs):
        return pool

    engine = AnalyticsPgEngine(dataclasses.replace(SETTINGS, **overrides), pool_factory=factory)
    engine.fake_pool = pool  # type: ignore[attr-defined]
    return engine


async def hold_in_tasks(manager, n: int, stack: contextlib.AsyncExitStack) -> None:
    """Hold `n` context managers (`manager()` makes one), each in a task of its own: a job that already holds one of
    the jobs' slots takes no second, so separate jobs are separate contexts."""
    for _ in range(n):
        ready, release = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(_hold_one(manager, ready, release))
        await ready.wait()

        async def finish(task=task, release=release):
            release.set()
            await task

        stack.push_async_callback(finish)


async def _hold_one(manager, ready: asyncio.Event, release: asyncio.Event) -> None:
    async with manager():
        ready.set()
        await release.wait()


async def hold(engine: AnalyticsPgEngine, n: int, caller: str, stack: contextlib.AsyncExitStack) -> None:
    await hold_in_tasks(lambda: engine.acquire(timeout=1, caller=caller), n, stack)


# --- lane_sizes -------------------------------------------------------------------------------------------------


def test_defaults_fill_a_pool_of_eight_less_the_reserve():
    assert lane_sizes(8, 2) == LaneSizes(3, 2, 1)


def test_explicit_sizes_that_fit_are_kept():
    assert lane_sizes(8, 2, heavy=3, light=2, jobs=1) == LaneSizes(3, 2, 1)


def test_explicit_sizes_over_pool_minus_reserve_are_refused():
    with pytest.raises(CliAnalyticsStorageConfigError, match="pool of 8 keeps 2 for ingest"):
        lane_sizes(8, 2, heavy=3, light=2, jobs=2)


def test_unset_lanes_shrink_to_a_small_pool_that_still_fits():
    assert lane_sizes(5, 2) == LaneSizes(1, 1, 1)


@pytest.mark.parametrize("pool, reserve", [(2, 2), (1, 2), (1, 0)])
def test_a_pool_of_one_or_two_with_unset_lanes_still_works_with_the_default_reserve(pool, reserve):
    assert lane_sizes(pool, reserve) == LaneSizes(1, 1, 1)


@pytest.mark.parametrize("pool, reserve", [(2, 2), (1, 2)])
def test_a_small_pool_with_lanes_set_by_hand_that_do_not_fit_is_refused(pool, reserve):
    with pytest.raises(CliAnalyticsStorageConfigError):
        lane_sizes(pool, reserve, heavy=1, light=1, jobs=1)


@pytest.mark.parametrize("pool, reserve", [(4, 2), (3, 1)])
def test_a_small_pool_with_unset_lanes_shares_one_slot_per_lane(pool, reserve):
    assert lane_sizes(pool, reserve) == LaneSizes(1, 1, 1)


def test_a_small_pool_with_lanes_set_by_hand_is_still_refused():
    with pytest.raises(CliAnalyticsStorageConfigError):
        lane_sizes(4, 2, heavy=2, light=1, jobs=1)


def test_a_lane_of_zero_is_refused():
    with pytest.raises(CliAnalyticsStorageConfigError):
        lane_sizes(8, 2, heavy=0)


def test_engine_refuses_overflowing_lane_settings():
    with pytest.raises(CliAnalyticsStorageConfigError):
        make_engine(heavy_slots=4, light_slots=4, jobs_slots=4)


# --- the cap ----------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_ingest_acquires_never_exceed_pool_minus_reserve():
    engine = make_engine(jobs_slots=2)  # 8 - 2 = 6
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 6, "dashboard", stack)
        with pytest.raises(TimeoutError):
            async with engine.acquire(timeout=0.05, caller="dashboard"):
                pass


@pytest.mark.asyncio
async def test_a_released_slot_is_reusable():
    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 5, "dashboard", stack)
        async with engine.acquire(timeout=0.5, caller="dashboard"):
            pass
        async with engine.acquire(timeout=0.5, caller="dashboard"):
            pass


@pytest.mark.asyncio
async def test_jobs_never_exceed_their_allowance_though_the_cap_has_room():
    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 2, "job", stack)
        with pytest.raises(TimeoutError):
            async with engine.acquire(timeout=0.05, caller="job"):
                pass
        async with engine.acquire(timeout=0.5, caller="dashboard"):  # dashboards are not held up by jobs
            pass


@pytest.mark.asyncio
async def test_jobs_count_against_the_shared_cap():
    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 2, "job", stack)
        await hold(engine, 4, "dashboard", stack)
        with pytest.raises(TimeoutError):
            async with engine.acquire(timeout=0.05, caller="dashboard"):
                pass


@pytest.mark.asyncio
async def test_a_timed_out_wait_for_a_job_slot_leaves_the_cap_intact():
    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 2, "job", stack)
        for _ in range(3):
            with pytest.raises(TimeoutError):
                async with engine.acquire(timeout=0.02, caller="job"):
                    pass
        await hold(engine, 4, "dashboard", stack)  # all four others still free


@pytest.mark.asyncio
async def test_only_acquire_ingest_bypasses_the_cap():
    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 6, "dashboard", stack)
        async with engine.acquire_ingest(timeout=0.5) as conn:
            assert conn is not None
        assert engine.fake_pool.timeouts[-1] == pytest.approx(0.5, abs=0.05)


@pytest.mark.asyncio
async def test_acquire_refuses_to_be_named_ingest():
    engine = make_engine(jobs_slots=2)
    with pytest.raises(ValueError):
        async with engine.acquire(caller="ingest"):
            pass


@pytest.mark.asyncio
async def test_timeout_bounds_cap_and_pool_wait_together():
    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 6, "dashboard", stack)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            async with engine.acquire(timeout=0.2, caller="dashboard"):
                pass
        assert 0.15 <= time.monotonic() - started < 0.6


@pytest.mark.asyncio
async def test_the_pool_gets_only_what_is_left_of_the_timeout():
    engine = make_engine(jobs_slots=2)
    holders = contextlib.AsyncExitStack()
    await hold(engine, 6, "dashboard", holders)
    waiter = asyncio.create_task(_acquire_once(engine, timeout=1.0))
    await asyncio.sleep(0.2)
    await holders.aclose()
    await waiter
    assert engine.fake_pool.timeouts[-1] < 0.9


async def _acquire_once(engine: AnalyticsPgEngine, timeout: float) -> None:
    async with engine.acquire(timeout=timeout, caller="dashboard"):
        pass


@pytest.mark.asyncio
async def test_default_timeout_applies_only_when_none_is_passed(monkeypatch):
    monkeypatch.setattr(engine_module, "ACQUIRE_TIMEOUT_S", 0.1)
    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 6, "dashboard", stack)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            async with engine.acquire(caller="job"):
                pass
        assert time.monotonic() - started < 0.5
    assert engine_module.ACQUIRE_TIMEOUT_S == 0.1


def test_default_acquire_timeout_is_thirty_seconds():
    assert engine_module.ACQUIRE_TIMEOUT_S == 30.0


@pytest.mark.asyncio
async def test_job_scope_makes_unnamed_acquires_jobs():
    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        with engine_module.job_scope():
            await hold_in_tasks(lambda: engine.acquire(timeout=1), 2, stack)
            with pytest.raises(TimeoutError):
                async with engine.acquire(timeout=0.05):
                    pass


@pytest.mark.asyncio
async def test_advisory_lock_is_a_job():
    engine = make_engine(jobs_slots=2)
    conn = MagicMock(fetchval=AsyncMock(return_value=True), execute=AsyncMock())
    engine.fake_pool.acquire = _fixed(conn)
    async with contextlib.AsyncExitStack() as stack:
        await hold_in_tasks(lambda: engine.advisory_lock("a"), 1, stack)
        await hold_in_tasks(lambda: engine.advisory_lock("b"), 1, stack)
        with pytest.raises(TimeoutError):
            async with engine.acquire(timeout=0.05, caller="job"):
                pass


def _fixed(conn):
    @asynccontextmanager
    async def acquire(timeout=None):
        yield conn

    return acquire


# --- the second connection of a request ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_widened_under_a_full_cap_runs_on_one_connection(monkeypatch):
    monkeypatch.setattr(reader_module, "SECOND_CONNECTION_TIMEOUT_S", 0.1)
    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 5, "dashboard", stack)  # the request's own first connection is the sixth
        async with engine.acquire(timeout=1, caller="dashboard") as first:
            pair = _ConnectionPair(engine, [first])
            started = time.monotonic()
            async with pair.widen():
                assert time.monotonic() - started >= 0.09
                assert pair._free.qsize() == 1  # no second connection joined
                async with pair.acquire() as conn:
                    assert conn is first


def test_settings_from_config_refuse_lanes_over_pool_minus_reserve():
    from codemie.configs.config import Config

    cfg = Config(
        POSTGRES_HOST="db.local",
        CLI_ANALYTICS_PG_POOL_SIZE=8,
        CLI_ANALYTICS_PG_INGEST_RESERVE=2,
        CLI_ANALYTICS_PG_HEAVY_SLOTS=4,
        CLI_ANALYTICS_PG_LIGHT_SLOTS=2,
        CLI_ANALYTICS_PG_JOBS_SLOTS=2,
    )
    with pytest.raises(CliAnalyticsStorageConfigError, match="lanes need 8"):
        AnalyticsPgSettings.from_config(cfg)


# --- nested job acquires, and the dashboard's wait for the cap --------------------------------------------------


@pytest.mark.asyncio
async def test_a_job_holding_its_slot_can_acquire_again_without_a_second_jobs_slot():
    engine = make_engine(jobs_slots=1)
    async with engine.acquire(timeout=0.3, caller="job"):  # the advisory lock's connection
        async with engine.acquire(timeout=0.3, caller="job"):  # a rollup path inside the same job
            pass
    async with engine.acquire(timeout=0.3, caller="job"):  # the slot is back afterwards
        pass


@pytest.mark.asyncio
async def test_two_jobs_with_one_slot_each_do_not_wait_for_each_other_nested():
    engine = make_engine(jobs_slots=2)
    ready = asyncio.Barrier(2)

    async def job():
        async with engine.acquire(timeout=0.5, caller="job"):
            await ready.wait()  # both jobs hold their slot
            async with engine.acquire(timeout=0.5, caller="job"):
                pass

    await asyncio.gather(job(), job())


@pytest.mark.asyncio
async def test_a_nested_job_acquire_still_counts_against_the_cap():
    engine = make_engine(jobs_slots=1)  # cap 6
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 4, "dashboard", stack)
        async with engine.acquire(timeout=0.3, caller="job"), engine.acquire(timeout=0.3, caller="job"):
            with pytest.raises(TimeoutError):
                async with engine.acquire(timeout=0.1, caller="dashboard"):
                    pytest.fail("the cap is full")


@pytest.mark.asyncio
async def test_an_unrelated_job_waits_for_the_jobs_slot():
    engine = make_engine(jobs_slots=1)
    release = asyncio.Event()

    async def holder():
        async with engine.acquire(timeout=0.3, caller="job"):
            await release.wait()

    task = asyncio.create_task(holder())
    await asyncio.sleep(0.01)
    # this context holds none: it must queue behind the holder
    with pytest.raises(TimeoutError):
        async with engine.acquire(timeout=0.1, caller="job"):
            pytest.fail("the jobs' slot is taken")
    release.set()
    await task


@pytest.mark.asyncio
async def test_a_dashboard_waiting_past_the_timeout_for_the_cap_raises_the_subtype():
    from codemie.repository.cli_analytics.ports import DashboardAcquireTimeout

    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 6, "dashboard", stack)
        with pytest.raises(DashboardAcquireTimeout):
            async with engine.acquire(timeout=0.05, caller="dashboard"):
                pytest.fail("the cap is full")
        with pytest.raises(TimeoutError) as job_wait:  # a job's wait stays a plain timeout
            async with engine.acquire(timeout=0.05, caller="job"):
                pytest.fail("the cap is full")
        assert type(job_wait.value) is TimeoutError


@pytest.mark.asyncio
async def test_a_dashboard_with_no_timeout_waits_the_configured_acquire_timeout(monkeypatch):
    from codemie.configs.config import config as app_config
    from codemie.repository.cli_analytics.ports import DashboardAcquireTimeout

    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_ACQUIRE_TIMEOUT_S", 0.05)
    engine = make_engine(jobs_slots=2)
    async with contextlib.AsyncExitStack() as stack:
        await hold(engine, 6, "dashboard", stack)
        started = time.monotonic()
        with pytest.raises(DashboardAcquireTimeout):
            async with engine.acquire(caller="dashboard"):
                pytest.fail("the cap is full")
        assert time.monotonic() - started < 1.0
        async with asyncio.timeout(1):  # nothing leaked: after a release the cap serves again
            pass
    async with engine.acquire(timeout=0.3, caller="dashboard"):
        pass


class FullPool:
    """A pool whose connections are all taken: acquire waits out its timeout."""

    def __init__(self) -> None:
        self.close = AsyncMock()
        self.terminate = MagicMock()

    @asynccontextmanager
    async def acquire(self, timeout=None):
        await asyncio.sleep(timeout or 0)
        raise TimeoutError
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_a_pool_wait_that_runs_out_is_an_acquire_timeout_for_a_dashboard_request():
    from codemie.repository.cli_analytics.ports import DashboardAcquireTimeout

    async def factory(**_kwargs):
        return FullPool()

    engine = AnalyticsPgEngine(SETTINGS, pool_factory=factory)
    with pytest.raises(DashboardAcquireTimeout):
        async with engine.acquire(timeout=0.05, caller="dashboard"):
            pass
    with pytest.raises(TimeoutError) as raised:
        async with engine.acquire(timeout=0.05, caller="job"):
            pass
    assert not isinstance(raised.value, DashboardAcquireTimeout)


@pytest.mark.asyncio
async def test_a_job_acquire_inside_its_advisory_lock_reuses_the_held_connection():
    engine = make_engine(jobs_slots=1)
    async with engine.advisory_lock("x") as held:
        async with engine.acquire(timeout=0.05, caller="job") as nested:
            assert nested is held
        async with engine.acquire(timeout=0.05, caller="job") as again:  # free again once the nested one is done
            assert again is held


@pytest.mark.asyncio
async def test_an_advisory_lock_taken_inside_a_job_lock_gets_a_connection_of_its_own():
    """The server's advisory locks are re-entrant within one session: taking the same lock on the connection that holds
    it would be granted again, so the second lock must not reuse the held connection (the CLI of a pod runs in the
    context of nothing, but a job that locks again inside its lock must still see the first lock as held)."""
    engine = make_engine(jobs_slots=2)
    async with engine.advisory_lock("x") as held:
        async with engine.advisory_lock("y") as second:
            assert second is not held
        async with engine.acquire(timeout=0.05, caller="job") as nested:
            assert nested is held  # an ordinary nested acquire still reuses it: no second cap token
