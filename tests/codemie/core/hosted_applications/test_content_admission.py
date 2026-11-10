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

import asyncio

import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.hosted_apps_gateway.admission import ContentCapacity


@pytest.mark.asyncio
async def test_queued_viewer_requests_release_general_capacity_for_other_viewers():
    capacity = ContentCapacity(total=2, per_viewer=1, queue_seconds=5)
    first, queued, other = capacity.slot(), capacity.slot(), capacity.slot()
    assert await first.admit() and await queued.admit()
    await first.serve("viewer-a")
    waiting = asyncio.create_task(queued.serve("viewer-a"))
    await asyncio.sleep(0.01)
    assert not waiting.done()
    # The queued request gave back its general slot, so another viewer is admitted immediately.
    assert await other.admit()
    await other.serve("viewer-b")
    other.release()
    first.release()
    await asyncio.wait_for(waiting, 1)
    queued.release()
    assert capacity.viewers == {} and capacity.general._value == 2


@pytest.mark.asyncio
async def test_viewer_share_is_bounded_and_times_out_with_retryable_limit():
    capacity = ContentCapacity(total=4, per_viewer=1, queue_seconds=0.05)
    holder, late = capacity.slot(), capacity.slot()
    assert await holder.admit() and await late.admit()
    await holder.serve("viewer")
    with pytest.raises(HostedApplicationError) as error:
        await late.serve("viewer")
    assert error.value.status == 429
    late.release()
    holder.release()
    assert capacity.viewers == {} and capacity.general._value == 4


@pytest.mark.asyncio
async def test_general_capacity_is_bounded():
    capacity = ContentCapacity(total=1, per_viewer=1, queue_seconds=0.01)
    first, second = capacity.slot(), capacity.slot()
    assert await first.admit()
    assert not await second.admit()
    first.release()
    second.release()
    assert await second.admit()
    second.release()
    assert capacity.general._value == 1


@pytest.mark.asyncio
async def test_release_is_idempotent():
    capacity = ContentCapacity(total=1, per_viewer=1, queue_seconds=0.01)
    slot = capacity.slot()
    assert await slot.admit()
    await slot.serve("viewer")
    slot.release()
    slot.release()
    assert capacity.viewers == {} and capacity.general._value == 1
