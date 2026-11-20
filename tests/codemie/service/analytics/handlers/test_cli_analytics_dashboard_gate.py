# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""The per-pod limit on concurrent dashboard requests (EPMCDME-17051)."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

from codemie.configs.config import config as app_config
from codemie.repository.cli_analytics.postgres.settings import dashboard_concurrency_limit
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler, dashboard_slot


@pytest.fixture(autouse=True)
def _limit(monkeypatch):
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT", 2)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 5)


@pytest.mark.parametrize(
    ("configured", "pool", "expected"),
    [(0, 8, 4), (0, 5, 1), (0, 4, 1), (0, 1, 1), (3, 8, 3), (0, 20, 16), (12, 8, 8)],  # never above the pool
)
def test_limit_defaults_to_pool_minus_four_with_floor_one(configured, pool, expected):
    assert dashboard_concurrency_limit(configured, pool) == expected


@pytest.mark.asyncio
async def test_at_most_limit_inside_and_rest_queue_fifo():
    inside = peak = 0
    entered: list[int] = []
    release = asyncio.Event()

    async def request(n):
        nonlocal inside, peak
        async with dashboard_slot():
            inside += 1
            peak = max(peak, inside)
            entered.append(n)
            await release.wait()
            inside -= 1

    tasks = []
    for n in range(4):
        tasks.append(asyncio.create_task(request(n)))
        await asyncio.sleep(0)  # arrival order 0..3
    await asyncio.sleep(0.01)
    assert entered == [0, 1]
    release.set()
    await asyncio.gather(*tasks)

    assert peak == 2
    assert entered == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_wait_past_budget_times_out_and_leaks_no_slot():
    hold = asyncio.Event()

    async def holder():
        async with dashboard_slot():
            await hold.wait()

    holders = [asyncio.create_task(holder()) for _ in range(2)]
    await asyncio.sleep(0.01)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02), dashboard_slot():
            pytest.fail("must not get a slot")

    hold.set()
    await asyncio.gather(*holders)
    # both slots are back: two requests get in at once
    async with asyncio.timeout(1), dashboard_slot(), dashboard_slot():
        pass


@pytest.mark.asyncio
async def test_slot_released_on_exception():
    for _ in range(3):
        with pytest.raises(ValueError):
            async with dashboard_slot():
                raise ValueError("boom")
    async with asyncio.timeout(1), dashboard_slot(), dashboard_slot():
        pass


@pytest.mark.asyncio
async def test_slot_released_on_cancel():
    started = asyncio.Event()

    async def request():
        async with dashboard_slot():
            started.set()
            await asyncio.sleep(10)

    tasks = [asyncio.create_task(request()) for _ in range(2)]
    await started.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    async with asyncio.timeout(1), dashboard_slot(), dashboard_slot():
        pass


class _Reader:
    def __init__(self):
        self.inside = 0
        self.peak = 0

    async def materialize_sel(self, f):
        return None

    async def materialize_files(self, f):
        return None

    async def get_cost_models(self, f):
        self.inside += 1
        self.peak = max(self.peak, self.inside)
        try:
            await asyncio.sleep(0.05)
            raise RuntimeError("stop")
        finally:
            self.inside -= 1

    def top_level(self):
        outer = MagicMock(spec=["bounded"])
        reader = self

        @asynccontextmanager
        async def bounded():
            yield reader

        outer.bounded = bounded
        return outer


@pytest.mark.asyncio
async def test_get_cost_and_get_users_go_through_the_gate():
    reader = _Reader()
    handler = LocalAnalyticsHandler(reader.top_level())
    calls = [handler.get_cost(MagicMock()) for _ in range(3)] + [
        handler.get_users(MagicMock(), None, None) for _ in range(3)
    ]

    results = await asyncio.gather(*calls, return_exceptions=True)

    assert all(isinstance(r, RuntimeError) for r in results)
    assert reader.peak == 2


@pytest.mark.asyncio
async def test_other_paths_do_not_use_the_gate():
    handler = LocalAnalyticsHandler(MagicMock())
    async with dashboard_slot(), dashboard_slot():
        # a third holder would wait forever; the sessions-like path never asks for a slot
        assert not hasattr(handler, "dashboard_slot")
        import inspect

        from codemie.service.analytics.handlers import cli_analytics_handler as mod

        users = [
            n
            for n, m in inspect.getmembers(LocalAnalyticsHandler, inspect.isfunction)
            if "dashboard_request" in inspect.getsource(m)
        ]
        # every window-wide dashboard: /cost (live) and its rollup read, the two steps of /users and /user-charts
        # (rollup read, live statements), Overview (live) and its rollup read, Efficiency, Repositories, Branches and Tools; not
        # Sessions or a session detail
        assert sorted(users) == [
            "_rollup_cost",
            "_rollup_overview",
            "_rollup_users",
            "_users_live",
            "get_branches",
            "get_cost",
            "get_efficiency",
            "get_overview",
            "get_repositories",
            "get_tools",
        ]
        assert mod.dashboard_slot is dashboard_slot


@pytest.mark.asyncio
async def test_a_request_past_its_budget_is_named_and_another_timeout_passes_through(monkeypatch):
    from codemie.repository.cli_analytics.ports import RequestBudgetExceeded, StatementTimeout
    from codemie.service.analytics.handlers.cli_analytics_handler import dashboard_request

    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 0.01)
    with pytest.raises(RequestBudgetExceeded):
        async with dashboard_request():
            await asyncio.sleep(1)

    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 5)
    with pytest.raises(TimeoutError) as pool_wait:  # a pool or connect timeout inside the budget: the store is down
        async with dashboard_request():
            raise TimeoutError
    assert not isinstance(pool_wait.value, StatementTimeout)
