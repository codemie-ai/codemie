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

"""The ``@cached_read`` decorator, on a widget of its own so the cache is tested apart from any ledger read."""

import asyncio
from datetime import datetime

import pytest

from codemie.service.usage_attribution.constants import CACHE_NAMESPACE
from codemie.service.usage_attribution.cache import SharedCache
from codemie.service.usage_attribution.spend_widgets import SpendFigures, SpendTable, SpendWidget, cached_read
from codemie.service.usage_attribution.spend_widgets.caching import KEY_VERSION, LEDGER_TTL_S

from .conftest import END, LATER, OTHER_SCOPE, SCOPE, START, FakeCache, context, coverage


class CountingWidget(SpendWidget[SpendTable]):
    """Reads whatever ``answer`` is, and counts the reads."""

    view = "counting"
    output = SpendTable

    def __init__(self, ctx, answer=None):
        super().__init__(ctx)
        self.answer = answer or SpendTable({"a": SpendFigures(total=1.0)})
        self.reads = 0

    @cached_read
    async def read(self, start: datetime, end: datetime, names: list[str]) -> SpendTable:
        self.reads += 1
        return self.answer


@pytest.mark.asyncio
class TestCachedRead:
    async def test_concurrent_cold_reads_share_one_computation(self, monkeypatch):
        from codemie.service.usage_attribution.spend_widgets import caching

        cache = SharedCache(redis_factory=lambda: None)
        computing, waiting, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
        acquire = cache.acquire

        async def observe_acquire(key, ttl):
            token = await acquire(key, ttl)
            if token is None:
                waiting.set()
            return token

        monkeypatch.setattr(cache, "acquire", observe_acquire)
        monkeypatch.setattr(caching, "_LOCK_POLL_S", 0.001)

        class BlockingWidget(CountingWidget):
            @cached_read
            async def read(self, start, end, names):
                self.reads += 1
                computing.set()
                await finish.wait()
                return self.answer

        first = BlockingWidget(context(cache))
        second = BlockingWidget(context(cache))
        with coverage():
            async with asyncio.timeout(2), asyncio.TaskGroup() as tasks:
                one = tasks.create_task(first.read(START, END, ["a"]))
                await computing.wait()
                two = tasks.create_task(second.read(START, END, ["a"]))
                await waiting.wait()
                finish.set()

        assert one.result() == two.result() == first.answer
        assert (first.reads, second.reads) == (1, 0)

    async def test_below_the_floor_nothing_is_read_from_or_written_to_the_cache(self):
        cache = FakeCache()
        widget = CountingWidget(context(cache))
        with coverage(floor=END):
            await widget.read(START, END, ["a"])
        assert widget.reads == 1  # the read itself decides what below the floor means
        assert cache.store == {}

    async def test_a_second_read_of_the_same_scope_window_and_names_is_served_from_the_cache(self):
        cache = FakeCache()
        with coverage():
            first = await CountingWidget(context(cache)).read(START, END, ["a"])
            widget = CountingWidget(context(cache), SpendTable({"a": SpendFigures(total=99.0)}))
            second = await widget.read(START, END, ["a"])
        assert first == second and second.of("a").total == 1.0
        assert widget.reads == 0
        (key,) = cache.store
        assert key.startswith(f"{CACHE_NAMESPACE}:ledger:{KEY_VERSION}:counting:")
        assert cache.ttls == [LEDGER_TTL_S]

    async def test_another_access_scope_is_read_and_cached_on_its_own(self):
        cache = FakeCache()
        with coverage():
            await CountingWidget(context(cache, SCOPE)).read(START, END, ["a"])
            other = CountingWidget(context(cache, OTHER_SCOPE))
            await other.read(START, END, ["a"])
        assert other.reads == 1
        assert len(cache.store) == 2

    async def test_another_window_is_read_and_cached_on_its_own(self):
        cache = FakeCache()
        widget = CountingWidget(context(cache))
        with coverage():
            await widget.read(START, END, ["a"])
            await widget.read(START, LATER, ["a"])
        assert widget.reads == 2
        assert len(cache.store) == 2

    async def test_other_names_are_read_and_cached_on_their_own(self):
        cache = FakeCache()
        widget = CountingWidget(context(cache))
        with coverage():
            await widget.read(START, END, ["a"])
            await widget.read(START, END, ["b"])
        assert widget.reads == 2
        assert len(cache.store) == 2

    async def test_an_incomplete_answer_is_returned_but_not_cached(self):
        cache = FakeCache()
        widget = CountingWidget(context(cache), SpendTable({"a": SpendFigures()}, complete=False))
        with coverage():
            out = await widget.read(START, END, ["a"])
            await widget.read(START, END, ["a"])
        assert out.of("a") == SpendFigures()
        assert widget.reads == 2
        assert cache.store == {}

    async def test_the_figures_come_back_the_moment_the_ledger_does(self):
        cache = FakeCache()
        widget = CountingWidget(context(cache), SpendTable({"a": SpendFigures()}, complete=False))
        with coverage():
            await widget.read(START, END, ["a"])
            widget.answer = SpendTable({"a": SpendFigures(total=1.0)})
            recovered = await widget.read(START, END, ["a"])
        assert recovered.of("a").total == 1.0
        assert len(cache.store) == 1
