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

"""`bounded()` and `widened()`: a Sessions request holds one analytics connection for the window statements, two at
most for the detail statements, and frees them before Python work."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import SESSION_PAGE_SORTS
from codemie.repository.cli_analytics.postgres import reader as reader_module
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.handlers import cli_analytics_handler
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE


class CountingEngine:
    """A pool of `capacity` connections: acquire() waits up to `timeout` for a free one (TimeoutError) and records
    how many are held at once; each connection's fetch records how many statements run at once."""

    def __init__(self, capacity: int = 8, fetch_seconds: float = 0.0) -> None:
        self._slots = asyncio.Semaphore(capacity)
        self.held = 0
        self.max_held = 0
        self.acquisitions = 0
        self.running = 0
        self.max_running = 0
        self.fetch_seconds = fetch_seconds
        self.fetch = AsyncMock(side_effect=self._fetch)

    async def _fetch(self, text, *args):
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await asyncio.sleep(self.fetch_seconds)
            return []
        finally:
            self.running -= 1

    @asynccontextmanager
    async def acquire(self, timeout: float | None = None):
        await asyncio.wait_for(self._slots.acquire(), timeout)
        self.held += 1
        self.acquisitions += 1
        self.max_held = max(self.max_held, self.held)
        try:
            yield self  # the engine doubles as the connection: it has fetch()
        finally:
            self.held -= 1
            self._slots.release()


def _filter() -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31))


async def _six_statements(reader) -> None:
    await asyncio.gather(
        reader.get_turns_by_session(_filter()),
        reader.get_lines_by_session(_filter()),
        reader.get_tool_success_by_session(_filter()),
        reader.get_session_cost_facts(_filter()),
        reader.get_session_model_costs(_filter()),
        reader.get_skill_names_by_session(["s1"], scope=EXCLUDE_SCOPE),
    )


@pytest.mark.asyncio
async def test_a_widened_reader_holds_two_connections_and_runs_at_most_two_statements_at_once() -> None:
    engine = CountingEngine(fetch_seconds=0.02)

    async with PostgresCliAnalyticsReader(engine).bounded() as reader:  # type: ignore[arg-type]
        assert engine.held == 1
        async with reader.widened() as wide:
            assert engine.held == 2
            await _six_statements(wide)
        assert engine.held == 1  # the second connection went back as the details ended

    assert (engine.acquisitions, engine.held, engine.max_held) == (2, 0, 2)
    assert engine.max_running == 2 and engine.fetch.await_count == 6


@pytest.mark.asyncio
async def test_the_window_statements_of_a_bounded_reader_hold_one_connection_and_run_one_at_a_time() -> None:
    """The window phase is the long one and its statements run one after another: it must not hold an idle second
    connection (four slow requests would otherwise hold a pool of eight and starve ingest)."""
    engine = CountingEngine(fetch_seconds=0.01)

    async with PostgresCliAnalyticsReader(engine).bounded() as reader:  # type: ignore[arg-type]
        await _six_statements(reader)
        assert engine.held == 1

    assert (engine.acquisitions, engine.max_held, engine.max_running, engine.held) == (1, 1, 1, 0)


@pytest.mark.asyncio
async def test_the_connections_are_released_when_a_bounded_statement_fails() -> None:
    engine = CountingEngine()
    engine.fetch = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        async with PostgresCliAnalyticsReader(engine).bounded() as reader:  # type: ignore[arg-type]
            await reader.get_turns_by_session(_filter())

    assert engine.held == 0


@pytest.mark.asyncio
async def test_a_request_runs_on_the_one_connection_it_has_when_the_pool_has_no_second_one_soon() -> None:
    engine = CountingEngine(capacity=1, fetch_seconds=0.01)

    with patch.object(reader_module, "SECOND_CONNECTION_TIMEOUT_S", 0.05):
        async with PostgresCliAnalyticsReader(engine).bounded() as reader:  # type: ignore[arg-type]
            async with reader.widened() as wide:
                await _six_statements(wide)

    assert (engine.max_held, engine.max_running, engine.fetch.await_count, engine.held) == (1, 1, 6, 0)


@pytest.mark.asyncio
async def test_a_bounded_reader_never_raises_a_pool_wait_timeout_so_a_timeout_of_a_sessions_request_is_a_statement_one() -> (
    None
):
    """The first connection is waited for without a limit and the second one's timeout is swallowed, so a
    TimeoutError that reaches the Sessions route (answered 504) is a statement's command timeout, never a busy pool."""
    waits: list[float | None] = []
    engine = CountingEngine(capacity=1)
    original = engine.acquire

    def recording(timeout: float | None = None):
        waits.append(timeout)
        return original(timeout)

    engine.acquire = recording  # type: ignore[method-assign]
    with patch.object(reader_module, "SECOND_CONNECTION_TIMEOUT_S", 0.05):
        async with PostgresCliAnalyticsReader(engine).bounded() as reader:  # type: ignore[arg-type]
            async with reader.widened():
                pass

    assert waits == [None, 0.05]  # the first wait is unbounded, the second bounded and its timeout suppressed


@pytest.mark.asyncio
async def test_a_timeout_inside_the_widened_block_is_not_swallowed_as_a_missing_second_connection() -> None:
    engine = CountingEngine(capacity=8)
    with pytest.raises(TimeoutError):
        async with PostgresCliAnalyticsReader(engine).bounded() as reader:  # type: ignore[arg-type]
            async with reader.widened():
                raise TimeoutError("statement command timeout")
    assert engine.held == 0


@pytest.mark.asyncio
async def test_requests_that_each_take_one_connection_cannot_deadlock_a_nearly_exhausted_pool() -> None:
    engine = CountingEngine(capacity=3, fetch_seconds=0.01)

    async def request() -> None:
        async with PostgresCliAnalyticsReader(engine).bounded() as reader:  # type: ignore[arg-type]
            await _six_statements(reader)
            async with reader.widened() as wide:
                await _six_statements(wide)

    with patch.object(reader_module, "SECOND_CONNECTION_TIMEOUT_S", 0.05):
        await asyncio.wait_for(asyncio.gather(*(request() for _ in range(6))), timeout=10)

    assert engine.held == 0 and engine.max_held <= 3


class BoundedRepo:
    """A reader whose pinned() records acquire and release around the statements it serves."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.held = 0
        self.max_held = 0
        self.rows = {"s1": {"session_id": "s1", "model_name": "claude", "started_at": datetime(2026, 1, 1)}}

    @asynccontextmanager
    async def bounded(self):
        self.held += 1
        self.max_held = max(self.max_held, self.held)
        self.events.append("acquire")
        try:
            yield self
        finally:
            self.held -= 1
            self.events.append("release")

    @asynccontextmanager
    async def widened(self):
        self.events.append("widen")
        try:
            yield self
        finally:
            self.events.append("narrow")

    def _stmt(self, name: str, value):
        async def run(*args, **kwargs):
            assert self.held == 1, f"{name} ran without the pinned connection"
            self.events.append(name)
            return value

        return run

    def __getattr__(self, name: str):
        if name == "get_session_page":
            return self._stmt(name, (1, ["s1"]))
        if name == "get_window_model_flags":
            return self._stmt(name, [])
        if name == "get_framework_page":
            outer = self

            async def framework_page(f, sort_by, page, per_page, matching, **kwargs):
                assert outer.held == 1, "get_framework_page ran without the pinned connection"
                outer.events.append(name)
                matching([[]])  # the classification of the window's name sets runs while the connection is held
                return 1, ["s1"], []

            return framework_page
        if name == "get_cost_models":
            return self._stmt(name, [])
        if name == "get_cost_page":
            return self._stmt(name, (1, ["s1"], []))
        if name == "get_session_cost_facts":
            return self._stmt(name, [{"session_id": "s1", "prompt": "p", "started_at": datetime(2026, 1, 1)}])
        if name.startswith("get_"):
            return self._stmt(name, [])
        raise AttributeError(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("framework", [None, "superpowers"])
@pytest.mark.parametrize("sort_by", SESSION_PAGE_SORTS)
async def test_a_sessions_request_holds_the_connection_block_once_and_frees_it_before_pricing(
    sort_by, framework
) -> None:
    events: list[str] = []
    repo = BoundedRepo(events)
    handler = LocalAnalyticsHandler(repo)
    real_price = cli_analytics_handler.price_session_models

    def price(*args, **kwargs):
        events.append("pricing")
        return real_price(*args, **kwargs)

    with (
        patch.object(cli_analytics_handler, "price_session_models", side_effect=price),
        patch.object(cli_analytics_handler, "classify_delivery_framework", return_value=framework) as classify,
    ):
        if framework:
            classify.side_effect = lambda names: events.append("classify") or framework
        await handler.get_sessions(_filter(), page=0, per_page=20, sort_by=sort_by, search=None, framework=framework)

    assert repo.max_held == 1
    assert repo.held == 0
    assert events.index("pricing") > max(i for i, e in enumerate(events) if e == "release")
    assert events.count("acquire") == 1  # one hold; the details widen it
    # the window statements ran before the second connection was taken, the detail statements inside it
    window = {"get_session_page", "get_cost_page", "get_framework_page", "get_cost_models"}
    assert all(i < events.index("widen") for i, e in enumerate(events) if e in window)
    assert events.index("widen") < events.index("get_turns_by_session") < events.index("narrow")
