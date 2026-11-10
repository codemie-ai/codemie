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

"""Queued work must never be stranded by a policy change or an owner's backlog."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.policy import POLICY_DIGEST, POLICY_VERSION
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
)
from codemie.service.hosted_applications.retry import RetryWorker
from tests.codemie.service.hosted_applications.test_worker import queued

LEGACY = ("static-legacy", b"\x01" * 32)


async def latest_job(repo, version_id):
    async with repo.sessions() as session:
        return (
            await session.execute(
                select(Job)
                .where(Job.version_id == UUID(version_id), Job.kind == "validate")
                .order_by(Job.created_at.desc(), Job.id.desc())
                .limit(1)
            )
        ).scalar_one()


async def bind_latest_job(repo, version_id, version, digest, **changes):
    job = await latest_job(repo, version_id)
    async with repo.sessions() as session, session.begin():
        row = await session.get(Job, job.id)
        row.policy_version, row.policy_definition_digest = version, digest
        for key, value in changes.items():
            setattr(row, key, value)


async def make_available(repo, version_id):
    async with repo.sessions() as session, session.begin():
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        job = await session.get(Job, (await latest_job(repo, version_id)).id)
        job.available_at = now - timedelta(seconds=1)


@pytest.mark.asyncio
async def test_ready_job_bound_to_an_older_policy_is_rebound_and_validated(processing, identity):
    service, repo, _, worker, _ = processing
    app, staged = await queued(service, identity())
    await bind_latest_job(repo, staged["version_id"], *LEGACY)
    assert await worker.run_one(), "A queued job from an older pod must still be claimed"
    version = await service.get_version(identity(), app["id"], staged["version_id"])
    assert version["validation_state"] == "accepted" and version["can_activate"]
    job = await latest_job(repo, staged["version_id"])
    assert (job.policy_version, job.policy_definition_digest) == (POLICY_VERSION, POLICY_DIGEST)
    assert job.safe_details["rebound_from"] == LEGACY[0]


@pytest.mark.asyncio
async def test_transient_failure_from_an_older_policy_is_retried_under_the_current_one(processing, identity):
    service, repo, _, worker, validator = processing
    app, staged = await queued(service, identity())
    validator.rejection = HostedApplicationError("VALIDATION_UNAVAILABLE", "Scanner unavailable", 503)
    assert await worker.run_one()
    await bind_latest_job(repo, staged["version_id"], *LEGACY)
    await make_available(repo, staged["version_id"])
    assert await RetryWorker(repo, service.storage, service.storage_namespace).run_one()
    job = await latest_job(repo, staged["version_id"])
    assert job.state == "ready" and (job.policy_version, job.policy_definition_digest) == (
        POLICY_VERSION,
        POLICY_DIGEST,
    )
    validator.rejection = None
    assert await worker.run_one()
    assert (await service.get_version(identity(), app["id"], staged["version_id"]))["validation_state"] == "accepted"


@pytest.mark.asyncio
async def test_claim_prefers_owners_without_running_validation(processing, identity):
    service, repo, _, worker, _ = processing
    _, first = await queued(service, identity())
    _, second = await queued(service, identity())
    _, other = await queued(service, identity("other-owner"))
    async with repo.sessions() as session, session.begin():
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        for offset, staged in ((30, first), (20, second), (10, other)):
            job = await session.get(Job, (await latest_job(repo, staged["version_id"])).id)
            job.available_at = now - timedelta(seconds=offset)
    claimed = await worker.claim()
    assert claimed["job"]["version_id"] == UUID(first["version_id"])
    claimed = await worker.claim()
    assert claimed["job"]["version_id"] == UUID(other["version_id"]), "An idle owner goes before a busy owner"
    claimed = await worker.claim()
    assert claimed["job"]["version_id"] == UUID(second["version_id"])


@pytest.mark.asyncio
async def test_policy_change_never_cancels_work_for_other_reasons(processing, identity):
    service, repo, _, worker, _ = processing
    app, staged = await queued(service, identity())
    await bind_latest_job(repo, staged["version_id"], *LEGACY)
    async with repo.sessions() as session, session.begin():
        version = await session.get(Version, UUID(staged["version_id"]))
        version.configuration_revision += 1
    assert await worker.claim() is None
    job = await latest_job(repo, staged["version_id"])
    assert job.state == "failed" and job.phase == "eligibility_changed"
    assert job.safe_details["error_code"] == "VALIDATION_UNAVAILABLE"
    await make_available(repo, staged["version_id"])
    retries = RetryWorker(repo, service.storage, service.storage_namespace)
    assert not await retries.run_one(), "A changed configuration is never retried with the stale request"
    async with repo.sessions() as session:
        output = await session.get(Attempt, job.output_attempt_id)
    assert output.state == "delete_requested", "The unused output reservation goes back through cleanup"


@pytest.mark.asyncio
async def test_job_queued_for_a_deleted_application_is_canceled_and_never_validated(processing, identity):
    service, repo, _, worker, _ = processing
    app, staged = await queued(service, identity())
    current = await service.get_app(identity(), app["id"])
    await service.delete(identity(), app["id"], current["etag"], str(uuid4()))
    assert await worker.claim() is None
    job = await latest_job(repo, staged["version_id"])
    assert job.state == "canceled" and job.phase == "purge_requested"
