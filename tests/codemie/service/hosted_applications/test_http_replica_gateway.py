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
import json
from time import perf_counter
from uuid import UUID, uuid4
import httpx
import pytest
from starlette.responses import Response
from codemie.hosted_apps_gateway.content import ContentGateway
from codemie.hosted_apps_gateway.dispatch import HostDispatcher
from codemie.repository.hosted_applications.filesystem import HostedFileStorage
from tests.codemie.service.hosted_applications.test_worker import queued


async def unavailable(scope, receive, send):
    await Response(status_code=404)(scope, receive, send)


@pytest.mark.asyncio
async def test_real_primary_assets_two_replicas_and_revocation_statuses(launch, identity, tmp_path):
    handoff, service, worker, _ = launch
    app, staged = await queued(service, identity())
    assert await worker.run_one()
    version = UUID(staged['version_id'])
    host = handoff.host(version)
    clients = []
    for _ in range(2):
        storage = HostedFileStorage(service.storage.base)
        assert storage.volume_id == service.storage.volume_id
        gateway = ContentGateway(handoff, storage).app()
        tree = HostDispatcher(
            unavailable,
            gateway,
            content_domain='apps.localhost',
            content_port=8081,
        )
        clients.append(httpx.AsyncClient(transport=httpx.ASGITransport(app=tree), base_url='http://' + host))
    try:
        ticket = await handoff.issue(identity(), app['id'], purpose='owner_preview')
        bootstrap = await clients[0].post(
            '/_platform/launch',
            data={'ticket': ticket['ticket']},
            headers={
                'origin': 'http://localhost:5173',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-dest': 'document',
                'sec-fetch-site': 'cross-site',
            },
        )
        assert bootstrap.status_code == 303
        assert bootstrap.headers['location'] == handoff.settings.version_origin(version, viewer=True) + '/'
        assert 'HttpOnly' in bootstrap.headers['set-cookie'] and 'SameSite=lax' in bootstrap.headers['set-cookie']
        clients[1].cookies.update(clients[0].cookies)
        first = await clients[0].get('/')
        assert first.status_code == 200 and first.headers['x-content-type-options'] == 'nosniff'
        # Immutable assets are revalidated on every reuse; each revalidation is fully authorized.
        assert first.headers['cache-control'] == 'private, no-cache'
        tag = first.headers['etag']
        revalidated = await clients[1].get('/', headers={'if-none-match': tag})
        assert revalidated.status_code == 304 and revalidated.headers['cache-control'] == 'private, no-cache'
        assert (await clients[1].get('/', headers={'range': 'bytes=0-2'})).status_code == 206
        assert (await clients[1].head('/')).status_code == 200
        assert (await clients[1].get('/', headers={'range': 'bytes=9000-'})).status_code == 416
        assert (await clients[1].get('/unknown.js', headers={'accept': 'text/html'})).status_code == 404
        # Local measured baseline includes primary SQL+filesystem integrity+two adapters.
        timings = []
        semaphore = asyncio.Semaphore(8)

        async def request(index):
            async with semaphore:
                start = perf_counter()
                response = await clients[index % 2].get('/')
                timings.append((perf_counter() - start) * 1000)
                assert response.status_code == 200

        await asyncio.gather(*(request(index) for index in range(100)))
        evidence = {
            'requests': 100,
            'concurrency': 8,
            'replica_adapters': 2,
            'p95_ms': round(sorted(timings)[94], 2),
            'max_ms': round(max(timings), 2),
            'identity': 'in-process eligible directory seam',
            'transport': 'real ASGI + PostgreSQL + shared private POSIX',
        }
        (tmp_path / 'gateway-load.json').write_text(json.dumps(evidence))
        print(json.dumps(evidence))  # noqa: T201 -- bounded local benchmark aggregates only
        current = await service.get_app(identity(), app['id'])
        await service.delete(identity(), app['id'], current['etag'], str(uuid4()))
        for client in clients:
            for method, headers, path in [
                ('GET', {}, '/'),
                ('HEAD', {}, '/'),
                ('GET', {'range': 'bytes=0-2'}, '/'),
                ('GET', {'if-none-match': tag}, '/'),
                ('GET', {'range': 'bytes=9000-'}, '/'),
                ('GET', {}, '/unknown.js'),
            ]:
                assert (await client.request(method, path, headers=headers)).status_code == 401
    finally:
        for client in clients:
            await client.aclose()
