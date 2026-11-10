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

from datetime import timedelta
from uuid import UUID
import pytest
from sqlalchemy import select
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationStorageAttempts as Attempt,
)
from codemie.service.hosted_applications.retry import RetryWorker
from tests.codemie.service.hosted_applications.test_worker import queued


@pytest.mark.asyncio
async def test_transient_failure_has_bounded_linked_retry_with_fresh_prefix(processing, identity):
    service, repo, _, worker, validator = processing
    app, staged = await queued(service, identity())
    validator.rejection = HostedApplicationError('VALIDATION_UNAVAILABLE', 'Scanner unavailable', 503)
    retries = RetryWorker(repo, service.storage, service.storage_namespace)
    for expected in range(1, 4):
        assert await worker.run_one()
        assert not await retries.run_one()
        async with repo.sessions() as session, session.begin():
            jobs = (
                (await session.execute(select(Job).where(Job.version_id == UUID(staged['version_id'])))).scalars().all()
            )
            latest = max(jobs, key=lambda row: row.created_at)
            latest.available_at = latest.created_at - timedelta(seconds=1)
        assert await retries.run_one()
        assert not await retries.run_one()
        async with repo.sessions() as session:
            jobs = (
                (await session.execute(select(Job).where(Job.version_id == UUID(staged['version_id'])))).scalars().all()
            )
            latest = max(jobs, key=lambda row: row.created_at)
            assert latest.safe_details['retry_count'] == expected and latest.previous_job_id
            outputs = (
                (
                    await session.execute(
                        select(Attempt).where(
                            Attempt.version_id == UUID(staged['version_id']), Attempt.kind == 'validation'
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len({row.prefix for row in outputs}) == expected + 1
    assert await worker.run_one()
    async with repo.sessions() as session, session.begin():
        jobs = (await session.execute(select(Job).where(Job.version_id == UUID(staged['version_id'])))).scalars().all()
        latest = max(jobs, key=lambda row: row.created_at)
        latest.available_at = latest.created_at - timedelta(seconds=1)
    assert not await retries.run_one()
    assert len(jobs) == 4
    assert (await service.get_version(identity(), app['id'], staged['version_id']))['validation_state'] == 'failed'


@pytest.mark.asyncio
async def test_confirmed_rejection_is_never_automatically_retried(processing, identity):
    service, repo, _, worker, validator = processing
    await queued(service, identity())
    validator.rejection = HostedApplicationError('INVALID_ARCHIVE_PATH', 'Rejected')
    assert await worker.run_one()
    assert not await RetryWorker(repo, service.storage, service.storage_namespace).run_one()
