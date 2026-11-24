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
from codemie.repository.cli_analytics.postgres.settings import LaneSizes, dashboard_concurrency_limit
from codemie.service.analytics.handlers.cli_analytics_handler import (
    LANE_BY_METHOD,
    LocalAnalyticsHandler,
    dashboard_request,
    dashboard_slot,
)


@pytest.fixture(autouse=True)
def _limit(monkeypatch):
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT", 2)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 5)


@pytest.mark.parametrize(
    ("configured", "sizes", "expected"),
    [
        (0, LaneSizes(2, 2, 2), 4),  # the heavy and light lanes together
        (0, LaneSizes(1, 1, 1), 2),
        (0, LaneSizes(5, 3, 2), 8),
        (3, LaneSizes(2, 2, 2), 3),
        (12, LaneSizes(2, 2, 2), 4),  # clamped by the lanes
    ],
)
def test_limit_is_the_dashboard_lanes_unless_configured_lower(configured, sizes, expected):
    assert dashboard_concurrency_limit(configured, sizes) == expected


@pytest.mark.asyncio
async def test_slot_limit_follows_the_pool_and_reserve(monkeypatch):
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT", 0)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_POOL_SIZE", 20)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_INGEST_RESERVE", 2)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_HEAVY_SLOTS", 6)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_LIGHT_SLOTS", 4)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_JOBS_SLOTS", 2)
    inside = {"heavy": 0, "light": 0}
    peak = {"heavy": 0, "light": 0}
    release = asyncio.Event()

    async def request(lane):
        async with dashboard_slot(lane):
            inside[lane] += 1
            peak[lane] = max(peak[lane], inside[lane])
            await release.wait()
            inside[lane] -= 1

    tasks = [asyncio.create_task(request(lane)) for lane in ("heavy", "light") for _ in range(8)]
    await asyncio.sleep(0.05)
    assert peak == {"heavy": 6, "light": 4}
    release.set()
    await asyncio.gather(*tasks)


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


def _methods_reading_the_store() -> list[str]:
    import inspect

    return sorted(
        n
        for n, m in inspect.getmembers(LocalAnalyticsHandler, inspect.isfunction)
        if "self._repo" in inspect.getsource(m) and n != "__init__"
    )


def test_every_store_reading_method_is_in_a_lane():
    assert set(LANE_BY_METHOD.values()) <= {"heavy", "light"}
    assert _methods_reading_the_store() == sorted(LANE_BY_METHOD)


def test_the_lane_table_is_the_lane_each_method_enters():
    import inspect

    for name, lane in LANE_BY_METHOD.items():
        source = inspect.getsource(getattr(LocalAnalyticsHandler, name))
        # A rollup read enters the light lane through `_read_light_then_heavy`, which repeats it in the heavy lane only
        # for a merge of resumed sessions that must run live.
        enters_light = 'dashboard_request("light")' in source or "_read_light_then_heavy" in source
        assert enters_light == (lane == "light"), name


def test_every_store_reading_method_goes_through_the_gate():
    import inspect

    from codemie.service.analytics.handlers import cli_analytics_handler as mod

    gated = sorted(
        n
        for n, m in inspect.getmembers(LocalAnalyticsHandler, inspect.isfunction)
        if n != "_read_light_then_heavy"  # the gate itself, which the rollup reads enter through
        and ("dashboard_request" in inspect.getsource(m) or "_read_light_then_heavy" in inspect.getsource(m))
    )
    assert gated == sorted(LANE_BY_METHOD)
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


@pytest.mark.asyncio
async def test_two_entries_of_one_request_share_one_budget(monkeypatch):
    # /users and /cost read the rollup and, when it cannot answer, the live statements: two entries, one budget
    from codemie.repository.cli_analytics.ports import RequestBudgetExceeded
    from codemie.service.analytics.handlers.cli_analytics_handler import dashboard_request, one_request_budget

    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S", 0.1)

    @one_request_budget
    async def request():
        async with dashboard_request():
            await asyncio.sleep(0.07)  # the rollup read answers None inside the budget
        async with dashboard_request():
            await asyncio.sleep(0.07)  # the live path would get a second 0.1 s if it started a budget of its own

    with pytest.raises(RequestBudgetExceeded):
        await request()

    async with dashboard_request():  # outside such a method an entry has the whole budget again
        await asyncio.sleep(0.07)


@pytest.fixture(autouse=True)
def _live_rankings():
    """Overrides the directory's fixture: these tests read the handler's methods as they are."""


LIGHT_METHODS = {
    "authorize_session",
    "_rollup_cost",
    "_rollup_efficiency",
    "_rollup_overview",
    "_rollup_ranking_read",
    "_rollup_tools",
    "_rollup_users",
    "get_activity",
    "get_session_detail",
}


def test_the_light_lane_is_the_rollup_reads_session_detail_and_activity():
    assert {n for n, lane in LANE_BY_METHOD.items() if lane == "light"} == LIGHT_METHODS


@pytest.mark.asyncio
async def test_light_requests_finish_while_every_heavy_slot_is_held():
    hold = asyncio.Event()

    async def heavy():
        async with dashboard_request():
            await hold.wait()

    holders = [asyncio.create_task(heavy()) for _ in range(2)]
    await asyncio.sleep(0.01)
    try:
        for _ in range(3):  # more than the light lane holds at once, one after another
            async with asyncio.timeout(1), dashboard_request("light"):
                pass
    finally:
        hold.set()
        await asyncio.gather(*holders)


@pytest.mark.asyncio
async def test_heavy_requests_are_not_held_up_by_a_full_light_lane():
    hold = asyncio.Event()

    async def light():
        async with dashboard_request("light"):
            await hold.wait()

    holders = [asyncio.create_task(light()) for _ in range(2)]
    await asyncio.sleep(0.01)
    try:
        async with asyncio.timeout(1), dashboard_request():
            pass
    finally:
        hold.set()
        await asyncio.gather(*holders)


class _RefusingRollup:
    """A rollup that refuses (None), then the live statements; each notes which lane gates are held when it runs."""

    def __init__(self):
        self.seen: dict[str, dict[str, bool]] = {}

    def _held(self):
        import codemie.service.analytics.handlers.cli_analytics_handler as mod

        loop = asyncio.get_running_loop()
        gates = mod._dashboard_gates[loop][1]
        return {lane: gate.locked() for lane, gate in gates.items()}

    async def rollup_overview(self, f, rates, max_age):
        self.seen["rollup"] = self._held()
        return None

    async def materialize_sel(self, f):
        self.seen["live"] = self._held()
        raise RuntimeError("stop")

    def bounded(self):
        outer = self

        @asynccontextmanager
        async def bounded():
            yield outer

        return bounded()


class _BudgetProbe:
    """A reader whose rollup refuses (None) and whose live statements stop the request, each noting the request's
    shared deadline, as `one_request_budget` sets it."""

    def __init__(self):
        self.deadlines: list[float | None] = []

    def _note(self):
        import codemie.service.analytics.handlers.cli_analytics_handler as mod

        self.deadlines.append(mod._request_deadline.get())

    async def rollup_tools(self, *args, **kwargs):
        self._note()

    async def rollup_efficiency(self, *args, **kwargs):
        self._note()

    async def materialize_sel(self, f):
        self._note()
        raise RuntimeError("stop")

    def bounded(self):
        outer = self

        @asynccontextmanager
        async def bounded():
            yield outer

        return bounded()


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("get_tools", {"include_class_lists": False}),
        ("get_efficiency", {"include_class_lists": False, "include_buckets": False}),
    ],
)
@pytest.mark.asyncio
async def test_a_refused_rollup_and_the_live_fallback_of_tools_and_efficiency_share_one_budget(
    monkeypatch, method, kwargs
):
    """Each `dashboard_request` entry must not start a budget of its own: twice 540 s is past the proxy's timeout."""
    import codemie.service.analytics.handlers.cli_analytics_handler as mod

    monkeypatch.setattr(app_config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True)
    monkeypatch.setattr(mod, "get_ranking_cache", lambda: MagicMock(enabled=False))
    probe = _BudgetProbe()

    with pytest.raises(RuntimeError, match="stop"):
        await getattr(LocalAnalyticsHandler(probe), method)(MagicMock(), **kwargs)

    assert len(probe.deadlines) >= 2, "the rollup read and the live statements both ran"
    assert probe.deadlines[0] is not None and len(set(probe.deadlines)) == 1
    assert mod._request_deadline.get() is None  # the budget ends with the request


@pytest.mark.asyncio
async def test_a_rollup_refusal_runs_the_live_statements_in_the_heavy_gate_after_releasing_the_light_one(monkeypatch):
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT", 0)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_POOL_SIZE", 8)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_INGEST_RESERVE", 2)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_HEAVY_SLOTS", 1)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_LIGHT_SLOTS", 1)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_PG_JOBS_SLOTS", 1)
    monkeypatch.setattr(app_config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True)
    import codemie.service.analytics.handlers.cli_analytics_handler as mod

    monkeypatch.setattr(mod, "get_ranking_cache", lambda: MagicMock(enabled=False))
    reader = _RefusingRollup()
    handler = LocalAnalyticsHandler(reader)

    with pytest.raises(RuntimeError, match="stop"):
        await handler.get_overview(MagicMock(), include_class_lists=False, include_buckets=False)

    assert reader.seen["rollup"] == {"heavy": False, "light": True}
    assert reader.seen["live"] == {"heavy": True, "light": False}


@pytest.mark.asyncio
async def test_session_authorization_is_read_in_the_light_lane():
    from unittest.mock import AsyncMock, MagicMock

    from codemie.repository.cli_analytics.postgres import request_stats

    lanes = []
    repo = MagicMock()

    async def authorize(*_a):
        lanes.append(request_stats.current().lane)
        return True

    repo.authorize_session = AsyncMock(side_effect=authorize)
    assert await LocalAnalyticsHandler(repo).authorize_session("s1", object()) is True
    assert lanes == ["light"]


# ── a rollup read refuses the live merge in the light lane and repeats it in the heavy lane ─────────────────────────


@pytest.mark.asyncio
async def test_a_rollup_read_that_must_merge_live_repeats_in_the_heavy_lane_and_otherwise_stays_light(monkeypatch):
    from codemie.repository.cli_analytics.ports import LiveMergeNeeded
    from codemie.repository.cli_analytics.postgres import request_stats
    from codemie.service.analytics.handlers import cli_analytics_handler as mod

    lanes: list[str] = []
    allowed: list[bool] = []

    @asynccontextmanager
    async def fake_request(lane: str = "heavy"):
        lanes.append(lane)
        yield

    monkeypatch.setattr(mod, "dashboard_request", fake_request)

    async def plain(x):
        allowed.append(request_stats.live_merge_allowed.get())
        return x

    async def needs_merge(x):
        allowed.append(request_stats.live_merge_allowed.get())
        if request_stats.live_merge_allowed.get() is False:
            raise LiveMergeNeeded("merge")
        return x

    assert await LocalAnalyticsHandler._read_light_then_heavy(plain, "a") == "a"
    assert lanes == ["light"] and allowed == [False]  # light only, the merge refused there

    lanes.clear(), allowed.clear()
    assert await LocalAnalyticsHandler._read_light_then_heavy(needs_merge, "b") == "b"
    assert lanes == ["light", "heavy"]  # the light slot is left before the heavy one is taken
    assert allowed == [False, True]  # the merge may run in the heavy lane
    assert request_stats.live_merge_allowed.get() is True  # and the flag is back to its default after both
