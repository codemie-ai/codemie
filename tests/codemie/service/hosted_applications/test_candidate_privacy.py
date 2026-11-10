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

from io import BytesIO
from zipfile import ZipFile
from uuid import uuid4
import pytest
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.hosted_application_requests import (
    AppCreate,
    VersionCreate,
    VersionSelection,
    UploadComplete,
)
from tests.codemie.service.hosted_applications.test_thumbnail import publish_to_marketplace


async def stage(service, context, app, kind):
    data = b"<html>Visual</html>"
    if kind == 'zip':
        output = BytesIO()
        with ZipFile(output, 'w') as archive:
            archive.writestr('index.html', data)
        data = output.getvalue()
    result = await service.begin_version(
        context,
        app['id'],
        VersionCreate(filename='visual.' + kind, artifact_kind=kind, byte_size=len(data)),
        app['etag'],
        str(uuid4()),
    )

    async def chunks():
        yield data

    receipt = await service.transfer(context, app['id'], result['version_id'], 1, result['upload_etag'], chunks())
    await service.complete_version(
        context,
        app['id'],
        result['version_id'],
        UploadComplete(upload_id=result['upload_id'], generation=1, sha256=receipt['sha256']),
        receipt['version_etag'],
        str(uuid4()),
    )
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize('initial,candidate', [('html', 'zip'), ('zip', 'html')])
@pytest.mark.parametrize('state', ['queued', 'rejected', 'canceled', 'accepted'])
async def test_private_replacement_cannot_change_viewer_kind_or_filters(
    processing, identity, initial, candidate, state
):
    service, _, _, worker, validator = processing
    owner = identity()
    app = await service.create_app(
        owner, AppCreate(name='Published visual', description='Stable type', project='demo'), str(uuid4())
    )
    first = await stage(service, owner, app, initial)
    assert await worker.run_one()
    current = await service.get_app(owner, app['id'])
    await publish_to_marketplace(service, owner, current, first['version_id'])
    replacement = await stage(service, owner, await service.get_app(owner, app['id']), candidate)
    if state == 'rejected':
        validator.rejection = HostedApplicationError('MALWARE_DETECTED', 'Rejected')
        assert await worker.run_one()
    elif state == 'canceled':
        version = await service.get_version(owner, app['id'], replacement['version_id'])
        await service.version_action(owner, app['id'], version['id'], 'cancel', version['etag'], str(uuid4()))
    elif state == 'accepted':
        assert await worker.run_one()
    # The owner still previews the selected version, including its correct passive cover mode.
    assert (await service.get_app(owner, app['id']))['artifact_kind'] == initial
    assert [item['id'] for item in (await service.list_apps(owner, scope='mine', artifact_kind=initial))['items']] == [
        app['id']
    ]
    assert (await service.list_apps(owner, scope='mine', artifact_kind=candidate))['items'] == []
    viewer = identity('viewer')
    assert (await service.get_app(viewer, app['id']))['artifact_kind'] == initial
    assert [
        item['id'] for item in (await service.list_apps(viewer, scope='marketplace', artifact_kind=initial))['items']
    ] == [app['id']]
    assert (await service.list_apps(viewer, scope='marketplace', artifact_kind=candidate))['items'] == []
    if state == 'accepted':
        version = await service.get_version(owner, app['id'], replacement['version_id'])
        current = await service.get_app(owner, app['id'])
        await service.select_version(
            owner, app['id'], VersionSelection(version_id=version['id']), current['etag'], str(uuid4())
        )
        assert (await service.get_app(owner, app['id']))['artifact_kind'] == candidate
        assert (await service.get_app(viewer, app['id']))['artifact_kind'] == initial
