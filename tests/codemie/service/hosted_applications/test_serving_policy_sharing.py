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

"""A policy version this code no longer serves withdraws its versions from viewing."""

from uuid import UUID, uuid4

import pytest

from codemie.core.hosted_applications import policy
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.policy import POLICY_VERSION
from codemie.rest_api.models.hosted_application_requests import VersionSelection
from tests.codemie.service.hosted_applications.test_worker import queued


@pytest.mark.asyncio
async def test_withdrawn_policy_version_can_no_longer_be_viewed(launch, identity, monkeypatch):
    handoff, service, worker, _ = launch
    app, staged = await queued(service, identity())
    assert await worker.run_one()
    version_id = UUID(staged['version_id'])
    latest = await service.get_app(identity(), app['id'])
    active = await service.activate_version(
        identity(), app['id'], VersionSelection(version_id=version_id), latest['etag'], str(uuid4())
    )
    await service.publish(identity(), app['id'], active['etag'], str(uuid4()))
    ticket = await handoff.issue(identity('viewer'), app['id'], purpose='published_view')
    cookie, _ = await handoff.consume(ticket['ticket'], handoff.host(version_id))
    monkeypatch.delitem(policy.PERMITTED_POLICIES, POLICY_VERSION)
    with pytest.raises(HostedApplicationError):
        await handoff.authorize(cookie, version_id)
    with pytest.raises(HostedApplicationError):
        await handoff.issue(identity('viewer'), app['id'], purpose='published_view')
