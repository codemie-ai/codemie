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

"""Ticket consumption under real row locks: one use, and expiry checked after the lock wait.

Access itself is re-evaluated by the access rule inside the gateway's single statement (see test_access_matrix);
no directory read precedes or repeats it.
"""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import func, select

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.hosted_application import HostedApplicationViewSessions as Credential
from codemie.service.hosted_applications.launch import HostedApplicationLaunchService
from tests.codemie.service.hosted_applications.test_worker import queued


@pytest.mark.asyncio
async def test_narrow_preflight_ticket_stays_one_use_with_concurrent_consumers(launch, identity):
    original, service, worker, _ = launch
    app, staged = await queued(service, identity())
    assert await worker.run_one()
    candidate = HostedApplicationLaunchService(service, original.settings, original.absolute_auth_seconds)
    ticket = await candidate.issue(identity(), app['id'], purpose='owner_preview')
    host = candidate.host(UUID(staged['version_id']))
    results = await asyncio.gather(
        *(candidate.consume(ticket['ticket'], host) for _ in range(8)), return_exceptions=True
    )
    assert sum(isinstance(result, tuple) for result in results) == 1
    assert all(isinstance(result, (tuple, HostedApplicationError)) for result in results)


@pytest.mark.asyncio
async def test_ticket_expiry_is_checked_after_real_row_lock_wait(launch, identity):
    from datetime import timedelta
    from sqlalchemy import text

    original, service, worker, _ = launch
    app, staged = await queued(service, identity())
    assert await worker.run_one()
    candidate = HostedApplicationLaunchService(service, original.settings, original.absolute_auth_seconds)
    ticket = await candidate.issue(identity(), app['id'], purpose='owner_preview')
    request = None
    try:
        async with service.repository.sessions() as blocker, blocker.begin():
            record = (
                await blocker.execute(
                    select(Credential)
                    .where(
                        Credential.kind == 'ticket',
                        Credential.token_digest == candidate.digest(ticket['ticket'], 'ticket'),
                    )
                    .with_for_update()
                )
            ).scalar_one()
            now = (await blocker.execute(select(func.clock_timestamp()))).scalar_one()
            record.expires_at = now + timedelta(seconds=0.35)
            await blocker.flush()
            pid = (await blocker.execute(text('select pg_backend_pid()'))).scalar_one()
            request = asyncio.create_task(
                candidate.consume(ticket['ticket'], candidate.host(UUID(staged['version_id'])))
            )
            async with service.repository.sessions() as observer:
                async with asyncio.timeout(5):
                    while not (
                        await observer.execute(
                            text(
                                'select exists(select 1 from pg_stat_activity where :pid = any(pg_blocking_pids(pid)))'
                            ),
                            {'pid': pid},
                        )
                    ).scalar_one():
                        await asyncio.sleep(0.01)
            await asyncio.sleep(0.5)
        with pytest.raises(HostedApplicationError):
            await asyncio.wait_for(request, 5)
    finally:
        if request is not None and not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
