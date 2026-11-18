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

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from codemie.core import event_loop

_MAIN_LOOP = "codemie.core.event_loop.get_main_event_loop"


@pytest.mark.asyncio
async def test_on_the_main_loop_thread_starts_a_task_and_holds_it_until_done():
    loop = asyncio.get_running_loop()
    finish = asyncio.Event()
    ran_on = []

    async def work():
        ran_on.append(asyncio.get_running_loop())
        await finish.wait()

    with patch(_MAIN_LOOP, return_value=loop):
        assert event_loop.run_in_background(work) is True

    await asyncio.sleep(0)
    assert ran_on == [loop]
    assert len(event_loop._background_futures) == 1
    finish.set()
    await asyncio.sleep(0.01)
    assert not event_loop._background_futures


@pytest.mark.asyncio
async def test_from_a_worker_thread_runs_on_the_main_loop_without_waiting():
    loop = asyncio.get_running_loop()
    finish = asyncio.Event()
    ran_on = []

    async def work():
        ran_on.append(asyncio.get_running_loop())
        await finish.wait()

    with patch(_MAIN_LOOP, return_value=loop):
        started = await asyncio.wait_for(asyncio.to_thread(event_loop.run_in_background, work), timeout=1)

    assert started is True
    await asyncio.sleep(0.01)
    assert ran_on == [loop]
    finish.set()
    await asyncio.sleep(0.01)
    assert not event_loop._background_futures


@pytest.mark.asyncio
async def test_without_a_main_loop_uses_the_current_loop():
    done = asyncio.Event()

    async def work():
        done.set()

    with patch(_MAIN_LOOP, return_value=None):
        assert event_loop.run_in_background(work) is True

    await asyncio.wait_for(done.wait(), timeout=1)


def test_without_any_running_loop_returns_false_and_creates_no_coroutine():
    make_coro = MagicMock()

    with patch(_MAIN_LOOP, return_value=None):
        assert event_loop.run_in_background(make_coro) is False

    make_coro.assert_not_called()
