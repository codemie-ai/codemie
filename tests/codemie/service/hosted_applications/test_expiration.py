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

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, func
from tests.codemie.service.hosted_applications.test_worker import queued
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationOperations as Operation,
)
from codemie.service.hosted_applications.maintenance import MaintenanceWorker
from codemie.service.hosted_applications.retry import RetryWorker


@pytest.mark.asyncio
async def test_expired_validation_has_backoff_and_failure_audit(processing, identity):
    service, repo, _, worker, _ = processing
    _, staged = await queued(service, identity())
    work = await worker.claim()
    assert work
    async with repo.sessions() as session, session.begin():
        job = await session.get(Job, work['job']['id'])
        job.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await MaintenanceWorker(repo, service.storage).expire()
    async with repo.sessions() as session:
        job = await session.get(Job, work['job']['id'])
        failures = (
            (await session.execute(select(Operation).where(Operation.action == 'version.validation.failed')))
            .scalars()
            .all()
        )
        assert job.state == 'failed'
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        snapshot = {'delayed': job.available_at > now, 'failure_audited': bool(failures)}
    assert snapshot == {'delayed': True, 'failure_audited': True}, snapshot
    assert not await RetryWorker(repo, service.storage, service.storage_namespace).run_one()


@pytest.mark.asyncio
@pytest.mark.parametrize('stale', ['configuration', 'source'])
async def test_stale_failed_history_cannot_starve_other_owners_retry(processing, identity, stale):
    from sqlalchemy import func
    from codemie.core.hosted_applications.errors import HostedApplicationError
    from codemie.rest_api.models.hosted_application import (
        HostedApplicationVersions as Version,
        HostedApplicationStorageAttempts as Attempt,
    )

    service, repo, _, worker, validator = processing
    validator.rejection = HostedApplicationError('VALIDATION_UNAVAILABLE', 'Scanner unavailable', 503)
    _, first = await queued(service, identity())
    assert await worker.run_one()
    _, second = await queued(service, identity('other-owner'))
    assert await worker.run_one()
    async with repo.sessions() as session, session.begin():
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        jobs = (await session.execute(select(Job))).scalars().all()
        for job in jobs:
            job.available_at = now - timedelta(seconds=20 if job.version_id == UUID(first['version_id']) else 10)
        version = await session.get(Version, UUID(first['version_id']))
        if stale == 'configuration':
            version.configuration_revision += 1
        else:
            source = await session.get(Attempt, version.source_attempt_id)
            source.state = 'delete_requested'
    retries = RetryWorker(repo, service.storage, service.storage_namespace)
    assert await retries.run_one(), 'A stale immutable failed job must not stall the whole retry queue'
    async with repo.sessions() as session:
        jobs = (
            (
                await session.execute(
                    select(Job).where(Job.version_id == UUID(second['version_id']), Job.state == 'ready')
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1


@pytest.mark.asyncio
async def test_other_empty_volume_cannot_reclaim_original_attempts(processing, identity, tmp_path):
    from codemie.repository.hosted_applications.filesystem import HostedFileStorage
    from codemie.service.hosted_applications.maintenance import MaintenanceWorker

    service, repo, _, worker, _ = processing
    app, staged = await queued(service, identity())
    assert await worker.run_one()
    current = await service.get_app(identity(), app['id'])
    await service.delete(identity(), app['id'], current['etag'], str(uuid4()))
    assert not await MaintenanceWorker(repo, HostedFileStorage(tmp_path / 'wrong-empty-volume')).cleanup_one()
    async with repo.transaction(*identity()) as tx:
        assert (await tx.read_quota(identity().principal_id)).stored_bytes > 0
    assert any(path.name == 'original' for path in service.storage.base.rglob('*'))
    assert await MaintenanceWorker(repo, service.storage).cleanup_one()


class _GraceStorage:
    """Filesystem adapter with a cleanup grace, as the object adapters declare it."""

    def __new__(cls, storage, grace):
        storage.cleanup_grace_seconds = grace
        return storage


async def _purged_attempts(processing, identity):
    from codemie.rest_api.models.hosted_application import HostedApplicationStorageAttempts as Attempt

    service, repo, _, worker, _ = processing
    app, _staged = await queued(service, identity())
    assert await worker.run_one()
    current = await service.get_app(identity(), app['id'])
    await service.delete(identity(), app['id'], current['etag'], str(uuid4()))
    return repo, Attempt


async def _set_attempt_lease(repo, attempt_model, *, seconds_ago, quiesced):
    async with repo.sessions() as session, session.begin():
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        rows = (
            (await session.execute(select(attempt_model).where(attempt_model.state == 'delete_requested')))
            .scalars()
            .all()
        )
        for row in rows:
            row.lease_until = now - timedelta(seconds=seconds_ago)
            row.writer_quiesced_at = now if quiesced else None


@pytest.mark.asyncio
async def test_cleanup_waits_for_grace_after_lease(processing, identity):
    service, _, _, _, _ = processing
    repo, attempt_model = await _purged_attempts(processing, identity)
    worker = MaintenanceWorker(repo, _GraceStorage(service.storage, 30))
    await _set_attempt_lease(repo, attempt_model, seconds_ago=10, quiesced=False)
    assert not await worker.cleanup_one()
    await _set_attempt_lease(repo, attempt_model, seconds_ago=31, quiesced=False)
    assert await worker.cleanup_one()


@pytest.mark.asyncio
async def test_cleanup_of_quiesced_attempt_needs_no_grace(processing, identity):
    service, _, _, _, _ = processing
    repo, attempt_model = await _purged_attempts(processing, identity)
    worker = MaintenanceWorker(repo, _GraceStorage(service.storage, 30))
    await _set_attempt_lease(repo, attempt_model, seconds_ago=-20, quiesced=True)
    assert await worker.cleanup_one()


def test_filesystem_adapter_exposes_provider_neutral_seam(tmp_path):
    import inspect

    from codemie.repository.hosted_applications.filesystem import HostedFileStorage, PrefixWriter

    storage = HostedFileStorage(tmp_path / 'seam')
    assert storage.storage_id == storage.volume_id
    assert storage.namespace_suffix == ':volume:' + storage.volume_id
    assert storage.cleanup_grace_seconds == 0
    assert storage.bind_namespace('ns') == 'ns' + storage.namespace_suffix
    assert storage.owns_namespace('ns' + storage.namespace_suffix)
    assert 'lease' in inspect.signature(storage.writer).parameters
    assert inspect.iscoroutinefunction(PrefixWriter.put)
    assert PrefixWriter(storage, 'p', 1, 1, 0, 0).renew(None) is None


@pytest.mark.asyncio
async def test_joined_io_on_runs_on_the_given_executor_and_disposes_there():
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from codemie.repository.hosted_applications.filesystem import joined_io_on

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='seam-test')
    try:
        name = await joined_io_on(executor, lambda: threading.current_thread().name)
        assert name.startswith('seam-test')
        disposed = []
        gate = threading.Event()

        def slow():
            gate.wait(5)
            return 'resource'

        def dispose(value):
            disposed.append((value, threading.current_thread().name))

        task = asyncio.ensure_future(joined_io_on(executor, slow, cancel_dispose=dispose))
        await asyncio.sleep(0.05)
        task.cancel()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        executor.shutdown(wait=True)
        assert disposed and disposed[0][0] == 'resource' and disposed[0][1].startswith('seam-test')
    finally:
        executor.shutdown(wait=True)


async def _move_attempts_to_bucket(repo, attempt_model, filesystem, bucket):
    """Rebind the purged attempts to the S3 bucket, as if they were written there; returns their prefixes."""
    async with repo.sessions() as session, session.begin():
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        rows = (
            (await session.execute(select(attempt_model).where(attempt_model.state == 'delete_requested')))
            .scalars()
            .all()
        )
        for row in rows:
            row.provider = bucket.provider
            row.namespace = row.namespace.removesuffix(filesystem.namespace_suffix) + bucket.namespace_suffix
            # Quiesced: the candidate filter needs no grace, so the loop can retry at once.
            row.writer_quiesced_at = now
        return [row.prefix for row in rows]


@pytest.mark.asyncio
async def test_s3_cleanup_one_resumes_5002_objects_across_timeouts(processing, identity, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from codemie.repository.hosted_applications.object_storage import HostedObjectStorage
    from codemie.service.hosted_applications import maintenance
    from tests.codemie.service.hosted_applications.storage_fakes import FakeS3

    service, _, _, _, _ = processing
    repo, attempt_model = await _purged_attempts(processing, identity)
    fake = FakeS3()
    bucket = await HostedObjectStorage.open(fake, provider='aws', cleanup_grace_seconds=30)
    prefixes = await _move_attempts_to_bucket(repo, attempt_model, service.storage, bucket)
    for index in range(5002):
        fake.objects[f'{prefixes[0]}/assets/{index:05}'] = (b'x', fake.clock())
    fake.presigned_create(prefixes[0] + '/open', 60)
    loop = asyncio.get_running_loop()
    timeouts, deletes = [], []

    def bounded(delay):
        assert delay == 25
        timeouts.append(asyncio.timeout(delay))
        return timeouts[-1]

    def expire_every_other_delete(**_):
        # The batch delete completes; the 25 s timeout of cleanup_one fires right after it.
        deletes.append(True)
        if len(deletes) % 2:
            loop.call_soon_threadsafe(lambda: timeouts[-1].reschedule(loop.time()))

    monkeypatch.setattr(maintenance, 'asyncio', SimpleNamespace(timeout=bounded))
    fake.hooks['after_delete_keys'] = expire_every_other_delete
    worker = MaintenanceWorker(repo, bucket)
    async with repo.transaction(*identity()) as tx:
        stored_before = (await tx.read_quota(identity().principal_id)).stored_bytes
    results = []
    for _ in range(40):
        async with repo.sessions() as session:
            remaining = (
                (await session.execute(select(attempt_model).where(attempt_model.state == 'delete_requested')))
                .scalars()
                .all()
            )
        if not remaining:
            break
        if any(key.startswith(prefixes[0] + '/') for key in fake.objects):
            # Never a proof before absence is verified: the attempt with objects is still pending.
            assert any(row.prefix == prefixes[0] for row in remaining)
        results.append(await worker.cleanup_one())
    assert False in results and True in results
    assert not [key for key in fake.objects if any(key.startswith(prefix + '/') for prefix in prefixes)]
    assert not fake.uploads
    async with repo.sessions() as session:
        states = (await session.execute(select(attempt_model.state).where(attempt_model.prefix.in_(prefixes)))).all()
    assert {state for (state,) in states} == {'deleted'}
    async with repo.transaction(*identity()) as tx:
        assert (await tx.read_quota(identity().principal_id)).stored_bytes < stored_before
