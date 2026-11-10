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

from uuid import UUID, uuid4

import pytest

from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs,
    HostedApplicationVersions,
    HostedApplicationStorageAttempts,
)
from codemie.rest_api.models.hosted_application_requests import VersionSelection
from tests.codemie.service.hosted_applications.test_worker import launchable, queued


@pytest.mark.asyncio
@pytest.mark.parametrize("tampering", ["configuration_digest", "source_state", "output_receipt"])
async def test_serving_requires_complete_current_attestation(processing, identity, tampering):
    service, repo, _, worker, _ = processing
    context = identity()
    app, staged = await queued(service, context)
    await worker.run_one()
    latest = await service.get_app(context, app["id"])
    activated = await service.activate_version(
        context, app["id"], VersionSelection(version_id=UUID(staged["version_id"])), latest["etag"], str(uuid4())
    )
    await service.publish(context, app["id"], activated["etag"], str(uuid4()))
    assert (await service.list_apps(identity("viewer"), scope="marketplace"))["items"]
    async with repo.sessions() as session, session.begin():
        version = await session.get(HostedApplicationVersions, UUID(staged["version_id"]))
        if tampering == "configuration_digest":
            job = await session.get(HostedApplicationJobs, version.accepted_job_id)
            job.configuration_digest = b"x" * 32
        elif tampering == "source_state":
            source = await session.get(HostedApplicationStorageAttempts, version.source_attempt_id)
            source.state = "delete_requested"
        else:
            output = await session.get(HostedApplicationStorageAttempts, version.accepted_attempt_id)
            output.receipt_digest = b"x" * 32
    assert not await launchable(service, context, app["id"])
    # The card stays listed (AC12: details without a launchable version); its kind no longer comes from a version.
    (card,) = (await service.list_apps(identity("viewer"), scope="marketplace"))["items"]
    assert card["artifact_kind"] is None
