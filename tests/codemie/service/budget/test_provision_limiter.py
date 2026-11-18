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

import pytest

from codemie.service.budget import provision_limiter


def test_semaphore_is_cached_per_running_loop():
    async def grab():
        return provision_limiter.provision_semaphore(), provision_limiter.provision_semaphore()

    first_a, first_b = asyncio.run(grab())
    second_a, _ = asyncio.run(grab())

    assert first_a is first_b
    assert second_a is not first_a


@pytest.mark.asyncio
async def test_returns_the_provider_result_and_frees_the_slot():
    async def call():
        return "budget-1"

    assert await provision_limiter.run_customer_provisioning_in_slot(call()) == "budget-1"
    assert provision_limiter.provision_semaphore()._value == provision_limiter.PROVISION_PROVIDER_CONCURRENCY


@pytest.mark.asyncio
async def test_cancelled_caller_keeps_the_slot_until_the_call_finishes():
    release = asyncio.Event()

    async def call():
        await release.wait()
        return "late"

    waiter = asyncio.create_task(provision_limiter.run_customer_provisioning_in_slot(call()))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    semaphore = provision_limiter.provision_semaphore()
    assert semaphore._value == provision_limiter.PROVISION_PROVIDER_CONCURRENCY - 1
    release.set()
    await asyncio.sleep(0.01)
    assert semaphore._value == provision_limiter.PROVISION_PROVIDER_CONCURRENCY


@pytest.mark.asyncio
async def test_call_is_closed_when_cancelled_while_waiting_for_a_slot():
    semaphore = provision_limiter.provision_semaphore()
    for _ in range(provision_limiter.PROVISION_PROVIDER_CONCURRENCY):
        await semaphore.acquire()
    started = False

    async def call():
        nonlocal started
        started = True
        return None

    coro = call()
    waiter = asyncio.create_task(provision_limiter.run_customer_provisioning_in_slot(coro))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    for _ in range(provision_limiter.PROVISION_PROVIDER_CONCURRENCY):
        semaphore.release()
    assert started is False
    assert coro.cr_frame is None
