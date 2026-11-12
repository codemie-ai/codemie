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

from dataclasses import replace
from uuid import UUID, uuid4

import httpx
import pytest
from starlette.responses import Response

from codemie.hosted_apps_gateway.content import ContentGateway
from codemie.hosted_apps_gateway.dispatch import HostDispatcher
from tests.codemie.service.hosted_applications.test_worker import queued


async def unavailable(scope, receive, send):
    await Response(status_code=404)(scope, receive, send)


@pytest.mark.asyncio
@pytest.mark.parametrize("production", [False, True])
async def test_embedded_launch_has_scoped_frame_ancestors_partitioned_cookie_and_live_revocation(
    launch, identity, production
):
    handoff, service, worker, _ = launch
    if production:
        handoff.settings = replace(
            handoff.settings,
            local=False,
            content_domain="codemie-content.net",
            content_port=443,
            ui_origins=("https://codemie.example.com",),
        )
    app, staged = await queued(service, identity(), b'<html>Embedded application</html>')
    assert await worker.run_one()
    version_id = UUID(staged['version_id'])
    gateway = ContentGateway(handoff, service.storage)
    tree = HostDispatcher(
        unavailable,
        gateway.app(),
        content_domain=handoff.settings.content_domain,
        content_port=handoff.settings.content_port,
    )
    origin = ('https' if production else 'http') + '://' + handoff.host(version_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=tree), base_url=origin) as client:
        ticket = await handoff.issue(identity(), app['id'], purpose='owner_preview')
        bootstrap = await client.post(
            '/_platform/launch',
            data={'ticket': ticket['ticket']},
            headers={
                'origin': handoff.settings.ui_origins[0],
                'sec-fetch-mode': 'navigate',
                'sec-fetch-dest': 'iframe',
                'sec-fetch-site': 'cross-site',
            },
        )
        assert bootstrap.status_code == 303
        cookie = bootstrap.headers['set-cookie']
        assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=none' in cookie and 'Partitioned' in cookie
        assert 'Domain=' not in cookie and 'Path=/' in cookie
        assert bootstrap.headers['location'] == '/_platform/view'
        # HTTPX enforces Secure literally, whereas localhost secure-context cookie exceptions are browser-specific.
        client.cookies.clear()
        client.cookies.set(gateway.embedded_cookie_name, cookie.split(';')[0].partition('=')[2])
        ready = await client.get('/_platform/view', headers={'sec-fetch-dest': 'iframe'})
        assert ready.status_code == 200 and 'codemie-hosted-ready' in ready.text
        content = await client.get('/')
        assert content.status_code == 200 and 'Embedded application' in content.text
        expected = (
            'frame-ancestors '
            + ' '.join((*handoff.settings.ui_origins, handoff.settings.version_origin(version_id, viewer=True)))
            + ';'
        )
        assert expected in content.headers['content-security-policy']
        assert content.headers['x-dns-prefetch-control'] == 'off'
        assert content.headers['cache-control'] == 'private, no-cache'
        assert ready.headers['cache-control'] == 'private, no-store'
        assert bootstrap.headers['cache-control'] == 'private, no-store'
        assert content.headers['content-security-policy'].endswith(
            "sandbox allow-scripts allow-same-origin allow-popups"
        )
        assert content.headers['permissions-policy'] == (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), fullscreen=(self)"
        )
        replay = await client.post(
            '/_platform/launch',
            data={'ticket': ticket['ticket']},
            headers={
                'origin': handoff.settings.ui_origins[0],
                'sec-fetch-mode': 'navigate',
                'sec-fetch-dest': 'iframe',
                'sec-fetch-site': 'cross-site',
            },
        )
        assert replay.status_code == 401
        assert expected in replay.headers['content-security-policy']
        current = await service.get_app(identity(), app['id'])
        await service.delete(identity(), app['id'], current['etag'], str(uuid4()))
        for method, path, headers in [
            ('GET', '/', {}),
            ('HEAD', '/', {}),
            ('GET', '/', {'range': 'bytes=0-2'}),
            ('GET', '/', {'if-none-match': content.headers['etag']}),
            ('GET', '/missing.js', {}),
            ('GET', '/_platform/view', {}),
        ]:
            assert (await client.request(method, path, headers=headers)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'changes',
    [
        {'origin': 'https://attacker.test'},
        {'origin': 'null'},
        {'sec-fetch-dest': 'object'},
        {'sec-fetch-dest': 'script'},
        {'sec-fetch-mode': 'cors'},
    ],
)
async def test_untrusted_embedding_cannot_consume_a_ticket(launch, identity, changes):
    handoff, service, worker, _ = launch
    app, staged = await queued(service, identity())
    assert await worker.run_one()
    host = handoff.host(UUID(staged['version_id']))
    tree = HostDispatcher(
        unavailable,
        ContentGateway(handoff, service.storage).app(),
        content_domain='apps.localhost',
        content_port=8081,
    )
    ticket = await handoff.issue(identity(), app['id'], purpose='owner_preview')
    headers = {
        'origin': 'http://localhost:5173',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-dest': 'iframe',
        'sec-fetch-site': 'cross-site',
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=tree), base_url='http://' + host) as client:
        denied = await client.post('/_platform/launch', data={'ticket': ticket['ticket']}, headers=headers | changes)
        assert denied.status_code == 401 and 'set-cookie' not in denied.headers
    # A failed origin/destination check must not burn the legitimate viewer's ticket.
    cookie, _ = await handoff.consume(ticket['ticket'], host)
    assert len(cookie) == 43


@pytest.mark.asyncio
async def test_launch_bootstrap_refuses_extra_fields_queries_and_replay(launch, identity):
    handoff, service, worker, _ = launch
    app, staged = await queued(service, identity(), b'<html>Embedded</html>')
    assert await worker.run_one()
    host = handoff.host(UUID(staged['version_id']))
    gateway = ContentGateway(handoff, service.storage)
    tree = HostDispatcher(
        unavailable,
        gateway.app(),
        content_domain='apps.localhost',
        content_port=8081,
    )
    ticket = await handoff.issue(identity(), app['id'], purpose='owner_preview')
    headers = {
        'origin': 'http://localhost:5173',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-dest': 'iframe',
        'sec-fetch-site': 'cross-site',
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=tree), base_url='http://' + host) as client:
        for path, extra in (
            ('/_platform/launch', {'preview': 'static'}),
            ('/_platform/launch', {'target': '/'}),
            ('/_platform/launch', {'ticket': ['x', 'y']}),
            ('/_platform/launch?preview=static', {}),
        ):
            response = await client.post(path, data={'ticket': ticket['ticket']} | extra, headers=headers)
            assert response.status_code == 401 and 'set-cookie' not in response.headers
        response = await client.post('/_platform/launch', data={'ticket': ticket['ticket']}, headers=headers)
        assert response.status_code == 303
        assert response.headers['location'] == '/_platform/view'
        assert response.headers['set-cookie'].partition('=')[0] == gateway.embedded_cookie_name
        assert 'Partitioned' in response.headers['set-cookie']
        replay = await client.post('/_platform/launch', data={'ticket': ticket['ticket']}, headers=headers)
        assert replay.status_code == 401


@pytest.mark.asyncio
async def test_every_launch_is_audited(launch, identity):
    from sqlalchemy import select
    from codemie.rest_api.models.hosted_application import HostedApplicationOperations as Operation

    handoff, service, worker, _ = launch
    app, _ = await queued(service, identity(), b'<html>Audited open</html>')
    assert await worker.run_one()
    await handoff.issue(identity(), app['id'], purpose='owner_preview')
    async with service.repository.sessions() as session:
        launches = (await session.execute(select(Operation).where(Operation.action == 'app.launch'))).scalars().all()
    assert len(launches) == 1 and launches[0].audit_details == {
        'purpose': 'owner_preview',
        'authentication_kind': 'local_jwt',
    }
