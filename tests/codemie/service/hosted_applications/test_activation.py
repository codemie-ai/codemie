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

import httpx
import pytest
from sqlalchemy import select
from starlette.responses import Response

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.hosted_apps_gateway.content import ContentGateway
from codemie.hosted_apps_gateway.dispatch import HostDispatcher
from codemie.rest_api.models.hosted_application import (
    HostedApplicationOperations as Operation,
    HostedApplicationVersions as Version,
)
from codemie.rest_api.models.hosted_application_requests import VersionSelection
from tests.codemie.service.hosted_applications.test_retention import add_version
from tests.codemie.service.hosted_applications.test_worker import queued


async def published_versions(launch, identity):
    handoff, service, worker, _ = launch
    owner = identity()
    app, first = await queued(service, owner, b'<html>Original published version</html>')
    assert await worker.run_one()
    old_id = UUID(first['version_id'])
    new_id = await add_version(service, worker, owner, app, b'<html>Replacement accepted version</html>')
    current = await service.get_app(owner, app['id'])
    activated = await service.activate_version(
        owner, app['id'], VersionSelection(version_id=old_id), current['etag'], str(uuid4())
    )
    shared = await service.publish(owner, app['id'], activated['etag'], str(uuid4()))
    return handoff, service, app, shared, old_id, new_id


async def unavailable(scope, receive, send):
    await Response(status_code=404)(scope, receive, send)


@pytest.mark.asyncio
async def test_activation_denies_existing_cookie_and_every_old_published_host_asset_response(launch, identity):
    handoff, service, app, shared, old_id, new_id = await published_versions(launch, identity)
    ticket = await handoff.issue(identity('viewer'), app['id'], purpose='published_view')
    cookie, _ = await handoff.consume(ticket['ticket'], handoff.host(old_id))
    gateway = HostDispatcher(
        unavailable,
        ContentGateway(handoff, service.storage).app(),
        content_domain='apps.localhost',
        content_port=8081,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway),
        base_url='http://' + handoff.host(old_id),
        cookies={'codemie-app-session-local': cookie},
    ) as client:
        first = await client.get('/')
        assert first.status_code == 200
        changed = await service.activate_version(
            identity(), app['id'], VersionSelection(version_id=new_id), shared['etag'], str(uuid4())
        )
        assert changed['active_version_id'] == str(new_id)
        with pytest.raises(HostedApplicationError):
            await handoff.authorize(cookie, old_id)
        for method, path, headers in [
            ('GET', '/', {}),
            ('HEAD', '/', {}),
            ('GET', '/', {'if-none-match': first.headers['etag']}),
            ('GET', '/', {'range': 'bytes=0-2'}),
            ('GET', '/missing.js', {}),
        ]:
            assert (await client.request(method, path, headers=headers)).status_code == 401
    fresh = await handoff.issue(identity('viewer'), app['id'], purpose='published_view')
    assert fresh['action'] == 'http://' + handoff.host(new_id) + '/_platform/launch'
    new_cookie, _ = await handoff.consume(fresh['ticket'], handoff.host(new_id))
    assert (await handoff.authorize(new_cookie, new_id))['version']['id'] == new_id
    with pytest.raises(HostedApplicationError):
        await handoff.authorize(new_cookie, old_id)
    with pytest.raises(HostedApplicationError):
        await handoff.issue(identity('viewer'), app['id'], purpose='published_view', version_id=old_id)


@pytest.mark.asyncio
async def test_competing_same_etag_activations_have_exactly_one_winner_and_one_conflict(launch, identity):
    _, service, app, shared, old_id, new_id = await published_versions(launch, identity)
    activations_of = select(Operation.id).where(Operation.app_id == UUID(app['id']), Operation.action == 'app.activate')
    async with service.repository.sessions() as session:
        # ``published_versions`` already activated the first version once.
        earlier = set((await session.execute(activations_of)).scalars().all())
    targets = [new_id, old_id]
    results = await asyncio.gather(
        *(
            service.activate_version(
                identity(), app['id'], VersionSelection(version_id=target), shared['etag'], str(uuid4())
            )
            for target in targets
        ),
        return_exceptions=True,
    )
    winners = [index for index, result in enumerate(results) if isinstance(result, dict)]
    assert len(winners) == 1
    loser = results[1 - winners[0]]
    assert isinstance(loser, HostedApplicationError) and loser.code == 'REVISION_CONFLICT' and loser.status == 409
    current = await service.get_app(identity(), app['id'])
    assert current['active_version_id'] == str(targets[winners[0]])
    assert current['selected_version_id'] == str(targets[winners[0]])
    assert current['revision'] == shared['revision'] + 1
    async with service.repository.sessions() as session:
        activations = (
            (
                await session.execute(
                    select(Operation).where(Operation.app_id == UUID(app['id']), Operation.action == 'app.activate')
                )
            )
            .scalars()
            .all()
        )
        competing = [operation for operation in activations if operation.id not in earlier]
        assert len(competing) == 1 and competing[0].state == 'completed'


@pytest.mark.asyncio
async def test_blocked_rollback_preserves_active_version_and_current_viewing_session(launch, identity):
    handoff, service, app, shared, old_id, new_id = await published_versions(launch, identity)
    await service.activate_version(
        identity(), app['id'], VersionSelection(version_id=new_id), shared['etag'], str(uuid4())
    )
    # A blocked version, as a malware finding or retention leaves it, can never become the live one again.
    async with service.repository.sessions() as session, session.begin():
        blocked = await session.get(Version, old_id)
        blocked.security_state, blocked.revision = 'blocked', blocked.revision + 1
    before = await service.get_app(identity(), app['id'])
    ticket = await handoff.issue(identity('viewer'), app['id'], purpose='published_view')
    cookie, _ = await handoff.consume(ticket['ticket'], handoff.host(new_id))
    with pytest.raises(HostedApplicationError) as error:
        await service.activate_version(
            identity(), app['id'], VersionSelection(version_id=old_id), before['etag'], str(uuid4())
        )
    assert error.value.code == 'VERSION_NOT_ACCEPTED'
    after = await service.get_app(identity(), app['id'])
    assert after['active_version_id'] == before['active_version_id'] == str(new_id)
    assert after['selected_version_id'] == before['selected_version_id'] == str(new_id)
    assert after['revision'] == before['revision']
    assert after['is_global'] is True
    assert (await handoff.authorize(cookie, new_id))['version']['id'] == new_id
    with pytest.raises(HostedApplicationError):
        await handoff.issue(identity(), app['id'], purpose='owner_preview', version_id=old_id)
