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
from uuid import UUID, uuid4
from datetime import timedelta
import pytest
from sqlalchemy import select
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs,
    HostedApplicationOperations,
    HostedApplicationVersions,
    HostedApplicationStorageAttempts,
    HostedApplicationUserState,
    HostedApplications,
)
from codemie.rest_api.models.hosted_application_requests import AppCreate, AppPatch
from codemie.service.hosted_applications.maintenance import MaintenanceWorker
from tests.codemie.service.hosted_applications.test_thumbnail import publish_to_marketplace
from tests.codemie.service.hosted_applications.test_worker import queued


@pytest.mark.asyncio
async def test_purge_denies_first_then_proves_absence_and_retains_tombstone(processing, identity):
    service, repo, _, worker, _ = processing
    context = identity()
    app, version = await queued(service, context)
    await worker.run_one()
    current = await service.get_app(context, app['id'])
    result = await service.delete(context, app['id'], current['etag'], str(uuid4()))
    assert result['state'] == 'purging'
    with pytest.raises(HostedApplicationError) as error:
        await service.get_app(context, app['id'])
    assert error.value.status == 404
    maintenance = MaintenanceWorker(repo, service.storage)
    assert not await maintenance.finish_purge_one()
    async with repo.sessions() as session, session.begin():
        attempts = (
            (
                await session.execute(
                    select(HostedApplicationStorageAttempts).where(HostedApplicationStorageAttempts.app_id == app["id"])
                )
            )
            .scalars()
            .all()
        )
        for attempt in attempts:
            if attempt.lease_until:
                attempt.lease_until -= timedelta(minutes=5)
    while await maintenance.cleanup_one():
        pass
    assert await maintenance.finish_purge_one()
    assert not await maintenance.finish_purge_one()
    async with repo.sessions() as session:
        tombstone = await session.get(HostedApplications, app['id'])
        assert tombstone.lifecycle_state == 'purged' and tombstone.name is None
        # Only the creator's id stays for the audit records; the username and the display name are gone.
        assert tombstone.created_by.id == context.principal_id
        assert (tombstone.created_by.username, tombstone.created_by.name) == ('', '')
        for model in (HostedApplicationVersions, HostedApplicationStorageAttempts):
            assert (await session.execute(select(model).where(model.app_id == tombstone.id))).scalars().all() == []
        jobs = (
            (await session.execute(select(HostedApplicationJobs).where(HostedApplicationJobs.app_id == tombstone.id)))
            .scalars()
            .all()
        )
        assert len(jobs) == 1 and jobs[0].state == 'completed'
        state = await session.get(HostedApplicationUserState, 'owner')
        assert state.stored_bytes == 0 and state.reserved_bytes == 0
    assert list(service.storage.base.rglob('manifest.json')) == []


@pytest.mark.asyncio
async def test_delete_refunds_the_quota_bytes_of_an_active_application(processing, identity):
    service, repo, _, worker, _ = processing
    context = identity()
    app, _ = await queued(service, context)
    await worker.run_one()
    async with repo.sessions() as session:
        assert (await session.get(HostedApplicationUserState, 'owner')).stored_bytes > 0
    current = await service.get_app(context, app['id'])
    await service.delete(context, app['id'], current['etag'], str(uuid4()))
    maintenance = MaintenanceWorker(repo, service.storage)
    while await maintenance.cleanup_one():
        pass
    assert await maintenance.finish_purge_one()
    assert (await service.deletion_status(context, app['id']))['state'] == 'purged'
    async with repo.sessions() as session:
        state = await session.get(HostedApplicationUserState, 'owner')
        assert state.stored_bytes == 0 and state.reserved_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('unpublish', [False, True])
async def test_delete_of_a_published_or_unpublished_application_removes_it_everywhere(processing, identity, unpublish):
    service, repo, _, worker, _ = processing
    context = identity()
    app, staged = await queued(service, context)
    await worker.run_one()
    current = await service.get_app(context, app['id'])
    shared = await publish_to_marketplace(service, context, current, staged['version_id'])
    assert shared['is_global']
    if unpublish:
        shared = await service.unpublish(context, app['id'], shared['etag'], str(uuid4()))
        assert not shared['is_global']
    result = await service.delete(context, app['id'], shared['etag'], str(uuid4()))
    # Delete leaves the sharing flags alone (only PATCH and /marketplace/* change them); the lifecycle ends access.
    assert result['state'] == 'purging' and result['application']['is_global'] is (not unpublish)
    for scope in ('mine', 'project', 'marketplace'):
        assert (await service.list_apps(context, scope=scope))['items'] == []
    async with repo.sessions() as session:
        row = await session.get(HostedApplications, app['id'])
        assert row.lifecycle_state == 'purging' and row.is_global is (not unpublish)
    for call in (
        service.get_app(context, app['id']),
        service.list_versions(context, app['id']),
    ):
        with pytest.raises(HostedApplicationError) as error:
            await call
        assert error.value.status == 404
    assert (await service.deletion_status(context, app['id']))['state'] == 'purging'


@pytest.mark.asyncio
async def test_delete_replays_for_the_same_key_and_answers_not_found_for_a_new_one(catalog, identity):
    service, _, _ = catalog
    context = identity()
    app = await service.create_app(
        context, AppCreate(name='Delivery map', description='Visual', project='demo'), str(uuid4())
    )
    key = str(uuid4())
    first = await service.delete(context, app['id'], app['etag'], key)
    assert first['state'] == 'purging'
    assert await service.delete(context, app['id'], app['etag'], key) == first
    with pytest.raises(HostedApplicationError) as error:
        await service.delete(context, app['id'], app['etag'], str(uuid4()))
    assert error.value.status == 404
    with pytest.raises(HostedApplicationError) as error:
        await service.delete(identity('viewer'), app['id'], app['etag'], key)
    assert error.value.status == 404


@pytest.mark.asyncio
async def test_delete_requires_the_current_revision(catalog, identity):
    service, _, _ = catalog
    context = identity()
    app = await service.create_app(
        context, AppCreate(name='Delivery map', description='Visual', project='demo'), str(uuid4())
    )
    await service.patch_app(context, app['id'], AppPatch(name='Renamed'), app['etag'], str(uuid4()))
    with pytest.raises(HostedApplicationError) as error:
        await service.delete(context, app['id'], app['etag'], str(uuid4()))
    assert error.value.code == 'REVISION_CONFLICT'
    assert (await service.get_app(context, app['id']))['lifecycle_state'] == 'active'


@pytest.mark.asyncio
async def test_a_patch_queued_behind_the_delete_row_lock_answers_not_found(catalog, identity):
    service, repo, _ = catalog
    context = identity()
    app = await service.create_app(
        context, AppCreate(name='Delivery map', description='Visual', project='demo'), str(uuid4())
    )
    async with repo.transaction(*context, write=True) as tx:
        locked = await tx.managed_app(app['id'], context.user, lock=True)
        patch = asyncio.create_task(
            service.patch_app(context, app['id'], AppPatch(name='Renamed'), app['etag'], str(uuid4()))
        )
        await asyncio.sleep(0.5)
        assert not patch.done(), 'The patch must wait for the row lock held by the delete'
        await service._begin_purge(tx, locked, context.user)
    with pytest.raises(HostedApplicationError) as error:
        await patch
    assert error.value.status == 404


@pytest.mark.asyncio
async def test_a_locked_managed_app_moved_to_purging_after_the_unlocked_read_answers_not_found(catalog, identity):
    service, repo, _ = catalog
    context = identity()
    app = await service.create_app(
        context, AppCreate(name='Delivery map', description='Visual', project='demo'), str(uuid4())
    )
    async with repo.sessions() as before:
        operations = len((await before.execute(select(HostedApplicationOperations))).scalars().all())
    with pytest.raises(HostedApplicationError) as error:
        async with repo.transaction(*context, write=True) as tx:
            lock_quota = tx.lock_quota

            async def purge_after_quota_lock(principal_id):
                state = await lock_quota(principal_id)
                # Another session moves the app to purging after the unlocked read and before the app lock.
                async with repo.sessions() as other, other.begin():
                    row = (
                        await other.execute(select(HostedApplications).where(HostedApplications.id == UUID(app['id'])))
                    ).scalar_one()
                    row.lifecycle_state = 'purging'
                return state

            tx.lock_quota = purge_after_quota_lock
            await tx.managed_app(app['id'], context.user, lock=True)
    assert error.value.status == 404
    async with repo.sessions() as check:
        assert len((await check.execute(select(HostedApplicationOperations))).scalars().all()) == operations
