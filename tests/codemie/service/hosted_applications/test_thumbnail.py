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

import hashlib
import io
import struct
import zipfile
import zlib
from uuid import UUID, uuid4

import pytest
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.hosted_application import HostedApplicationVersions
from codemie.rest_api.models.hosted_application_requests import (
    AppCreate,
    AppPatch,
    PublishRequest,
    VersionCreate,
    VersionSelection,
    UploadComplete,
)
from codemie.rest_api.models.user_management import UserDB
from codemie.service.hosted_applications.thumbnails import Cover, HostedThumbnailService
from tests.codemie.service.hosted_applications.conftest import CATEGORY_A, preview_jpeg


async def activate(service, owner, app, version_id):
    """Make ``version_id`` the live version; returns the application after the step."""
    return await service.activate_version(
        owner, app['id'], VersionSelection(version_id=UUID(str(version_id))), app['etag'], str(uuid4())
    )


async def share_with_project(service, owner, app, version_id):
    """Activate ``version_id`` and share the application with its project."""
    activated = await activate(service, owner, app, version_id)
    return await service.patch_app(owner, app['id'], AppPatch(shared=True), activated['etag'], str(uuid4()))


async def publish_to_marketplace(service, owner, app, version_id):
    """Activate ``version_id`` and list the application in the marketplace."""
    activated = await activate(service, owner, app, version_id)
    return await service.publish(
        owner, app['id'], PublishRequest(categories=[CATEGORY_A]), activated['etag'], str(uuid4())
    )


def png(width=320, height=180):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + chunk(
            b'IDAT',
            zlib.compress((b'\0' + b'\0' * width * 3) * height if width * height <= 1048576 else b'\0' * 100000),
        )
        + chunk(b'IEND', b'')
    )


async def image_app(processing, identity, image=None, filename='preview.png', app=None, preview=None):
    """Upload and validate a bundle; ``filename=None`` bundles no raster, ``preview`` is the captured picture."""
    service, _, _, worker, validator = processing
    # The seam keeps its picture between uploads, so every call sets it.
    validator.preview = preview
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('index.html', '<html><h1>Actual visual</h1></html>')
        if filename is not None:
            archive.writestr(filename, png() if image is None else image)
    original = stream.getvalue()
    owner = identity()
    if app is None:
        app = await service.create_app(
            owner, AppCreate(name='Image app', description='A visual with preview', project='demo'), str(uuid4())
        )
    staged = await service.begin_version(
        owner,
        app['id'],
        VersionCreate(filename='visual.zip', artifact_kind='zip', byte_size=len(original)),
        app['etag'],
        str(uuid4()),
    )

    async def body():
        yield original

    receipt = await service.transfer(owner, app['id'], staged['version_id'], 1, staged['upload_etag'], body())
    await service.complete_version(
        owner,
        app['id'],
        staged['version_id'],
        UploadComplete(upload_id=staged['upload_id'], generation=1, sha256=receipt['sha256']),
        receipt['version_etag'],
        str(uuid4()),
    )
    assert await worker.run_one()
    return service, await service.get_app(owner, app['id']), staged


@pytest.mark.asyncio
async def test_thumbnail_requires_live_access_and_validated_selected_or_active_version(processing, identity):
    service, app, staged = await image_app(processing, identity)
    thumbnails = HostedThumbnailService(service)
    result = await thumbnails.get(identity(), app['id'])
    assert result is not None and result[0] == png() and result[1] == 'image/png'
    with pytest.raises(HostedApplicationError):
        await thumbnails.get(identity('viewer'), app['id'])
    shared = await share_with_project(service, identity(), app, staged['version_id'])
    assert await thumbnails.get(identity('viewer'), app['id']) == result
    await service.delete(identity(), app['id'], shared['etag'], str(uuid4()))
    with pytest.raises(HostedApplicationError):
        await thumbnails.get(identity(), app['id'])
    with pytest.raises(HostedApplicationError):
        await thumbnails.get(identity('viewer'), app['id'])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'image, filename',
    [(png(100000, 100000), 'preview.png'), (b'<svg xmlns="http://www.w3.org/2000/svg"/>', 'preview.svg')],
)
async def test_thumbnail_excludes_oversized_dimensions_and_active_formats(processing, identity, image, filename):
    service, app, _ = await image_app(processing, identity, image, filename)
    assert await HostedThumbnailService(service).get(identity(), app['id']) is None


@pytest.mark.asyncio
async def test_thumbnail_rechecks_physical_integrity_and_creator_account(processing, identity):
    service, app, _ = await image_app(processing, identity)
    thumbnails = HostedThumbnailService(service)
    assert await thumbnails.get(identity(), app['id']) is not None
    image = next(p for p in service.storage.base.rglob('preview.png') if 'assets' in p.parts)
    image.write_bytes(b'corrupt')
    with pytest.raises(HostedApplicationError):
        await thumbnails.get(identity(), app['id'])
    # The creator's access ends with their platform account, which the users table holds.
    async with processing[1].sessions() as session, session.begin():
        await session.execute(UserDB.__table__.update().where(UserDB.__table__.c.id == 'owner').values(is_active=False))
    with pytest.raises(HostedApplicationError):
        await thumbnails.get(identity(), app['id'])


@pytest.mark.asyncio
async def test_thumbnail_viewer_never_observes_owner_private_candidate(processing, identity):
    service, app, first = await image_app(processing, identity)
    shared = await publish_to_marketplace(service, identity(), app, first['version_id'])
    _, candidate, upload = await image_app(processing, identity, image=png(240, 120), app=shared)
    await service.select_version(
        identity(), app['id'], VersionSelection(version_id=upload['version_id']), candidate['etag'], str(uuid4())
    )
    thumbnails = HostedThumbnailService(service)
    assert (await thumbnails.get(identity(), app['id']))[0] == png(240, 120)
    assert (await thumbnails.get(identity('viewer'), app['id']))[0] == png()


@pytest.mark.asyncio
async def test_thumbnail_reuses_the_immutable_manifest_cache_but_authorizes_every_request(
    processing, identity, monkeypatch
):
    service, app, staged = await image_app(processing, identity)
    reads = []
    original = service.storage.read_small

    async def counted(key, *, maximum):
        reads.append(key)
        return await original(key, maximum=maximum)

    monkeypatch.setattr(service.storage, 'read_small', counted)
    for _ in range(3):
        cover = await HostedThumbnailService(service).get(identity(), app['id'])
        assert cover.body == png() and cover.mime == 'image/png'
    assert len([key for key in reads if key.endswith('/manifest.json')]) == 1
    assert cover.etag == '"' + __import__('hashlib').sha256(png()).hexdigest() + '"'
    current = await service.get_app(identity(), app['id'])
    await service.delete(identity(), app['id'], current['etag'], str(uuid4()))
    with pytest.raises(HostedApplicationError):
        await HostedThumbnailService(service).get(identity(), app['id'])


@pytest.mark.asyncio
async def test_thumbnail_route_revalidates_with_a_strong_etag_after_authorization(processing, identity):
    from types import SimpleNamespace

    from codemie.rest_api.routers.hosted_applications import thumbnail as thumbnail_endpoint

    service, app, staged = await image_app(processing, identity)
    value = SimpleNamespace(catalog=service)
    app_id = UUID(app['id'])
    first = await thumbnail_endpoint(app_id, identity(), value, None)
    tag = '"' + __import__('hashlib').sha256(png()).hexdigest() + '"'
    assert first.status_code == 200 and first.body == png()
    assert first.headers['cache-control'] == 'private, no-cache' and first.headers['etag'] == tag
    assert first.headers['x-content-type-options'] == 'nosniff'
    for validator in (tag, 'W/' + tag, '"other", ' + tag):
        cached = await thumbnail_endpoint(app_id, identity(), value, validator)
        assert cached.status_code == 304 and cached.body == b'' and cached.headers['etag'] == tag
        assert cached.headers['cache-control'] == 'private, no-cache'
    assert (await thumbnail_endpoint(app_id, identity(), value, '"stale"')).status_code == 200
    # A matching validator never bypasses live authorization.
    with pytest.raises(HostedApplicationError):
        await thumbnail_endpoint(app_id, identity('viewer'), value, tag)


@pytest.mark.asyncio
async def test_missing_cover_is_never_stored(processing, identity):
    from types import SimpleNamespace

    from codemie.rest_api.routers.hosted_applications import thumbnail as thumbnail_endpoint

    service, app, _ = await image_app(processing, identity, b'<svg xmlns="http://www.w3.org/2000/svg"/>', 'preview.svg')
    value = SimpleNamespace(catalog=service)
    response = await thumbnail_endpoint(UUID(app['id']), identity(), value, None)
    assert response.status_code == 204 and response.headers['cache-control'] == 'private, no-store'
    assert 'etag' not in response.headers


def preview_cover(picture):
    return Cover(picture, 'image/jpeg', '"' + hashlib.sha256(picture).hexdigest() + '"')


def record_reads(service, monkeypatch):
    """Every storage key ``read_small`` reads from now on."""
    reads = []
    original = service.storage.read_small

    async def counted(key, *, maximum):
        reads.append(key)
        return await original(key, maximum=maximum)

    monkeypatch.setattr(service.storage, 'read_small', counted)
    return reads


@pytest.mark.asyncio
async def test_preview_serves_when_no_cover_or_bundled_raster(processing, identity):
    service, app, _ = await image_app(processing, identity, filename=None, preview=preview_jpeg())
    assert await HostedThumbnailService(service).get(identity(), app['id']) == preview_cover(preview_jpeg())
    _, plain, _ = await image_app(processing, identity, filename=None)
    assert await HostedThumbnailService(service).get(identity(), plain['id']) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('header', ['"{}"', '*'])
async def test_matching_etag_skips_the_read(processing, identity, monkeypatch, header):
    service, app, _ = await image_app(processing, identity, filename=None, preview=preview_jpeg())
    thumbnails = HostedThumbnailService(service)
    reads = record_reads(service, monkeypatch)
    # A stale tag reads the picture: that read is the preview key.
    await thumbnails.get(identity(), app['id'], '"0000"')
    preview_key = reads[-1]
    reads.clear()
    digest = hashlib.sha256(preview_jpeg()).hexdigest()
    cover = await thumbnails.get(identity(), app['id'], header.format(digest))
    assert cover == Cover(None, 'image/jpeg', '"' + digest + '"')
    assert preview_key not in reads


@pytest.mark.asyncio
async def test_stale_etag_reads_and_verifies(processing, identity):
    service, app, _ = await image_app(processing, identity, filename=None, preview=preview_jpeg())
    cover = await HostedThumbnailService(service).get(identity(), app['id'], '"0000"')
    assert cover == preview_cover(preview_jpeg())


@pytest.mark.asyncio
async def test_matching_etag_still_reads_a_bundled_raster(processing, identity):
    service, app, _ = await image_app(processing, identity, preview=preview_jpeg())
    tag = '"' + hashlib.sha256(preview_jpeg()).hexdigest() + '"'
    cover = await HostedThumbnailService(service).get(identity(), app['id'], tag)
    assert cover.body == png() and cover.mime == 'image/png'


@pytest.mark.asyncio
async def test_bundled_raster_wins_over_preview(processing, identity):
    service, app, _ = await image_app(processing, identity, preview=preview_jpeg())
    cover = await HostedThumbnailService(service).get(identity(), app['id'])
    assert cover.body == png() and cover.mime == 'image/png'


@pytest.mark.asyncio
async def test_viewer_gets_active_and_manager_selected_preview(processing, identity):
    first, second = preview_jpeg(), preview_jpeg(80, 60)
    service, app, staged = await image_app(processing, identity, filename=None, preview=first)
    thumbnails = HostedThumbnailService(service)
    # A manager sees the selected version's preview before it is activated; a viewer sees nothing yet.
    assert await thumbnails.get(identity(), app['id']) == preview_cover(first)
    with pytest.raises(HostedApplicationError):
        await thumbnails.get(identity('viewer'), app['id'])
    shared = await share_with_project(service, identity(), app, staged['version_id'])
    assert await thumbnails.get(identity('viewer'), app['id']) == preview_cover(first)
    _, candidate, upload = await image_app(processing, identity, filename=None, app=shared, preview=second)
    await service.select_version(
        identity(), app['id'], VersionSelection(version_id=upload['version_id']), candidate['etag'], str(uuid4())
    )
    selected = await thumbnails.get(identity(), app['id'])
    assert selected == preview_cover(second) and selected.etag != preview_cover(first).etag
    assert await thumbnails.get(identity('viewer'), app['id']) == preview_cover(first)


@pytest.mark.asyncio
async def test_rejected_version_never_serves_preview(processing, identity, monkeypatch):
    processing[4].rejection = HostedApplicationError('MALWARE_DETECTED', 'Rejected')
    service, app, _ = await image_app(processing, identity, filename=None, preview=preview_jpeg())
    reads = record_reads(service, monkeypatch)
    # No eligible version and no uploaded cover: the manager gets the not-found answer.
    with pytest.raises(HostedApplicationError):
        await HostedThumbnailService(service).get(identity(), app['id'])
    assert not [key for key in reads if key.endswith('/preview.jpg')]


@pytest.mark.asyncio
async def test_unreadable_viewer_reads_no_preview_bytes(processing, identity, monkeypatch):
    service, app, _ = await image_app(processing, identity, filename=None, preview=preview_jpeg())
    reads = record_reads(service, monkeypatch)
    with pytest.raises(HostedApplicationError):
        await HostedThumbnailService(service).get(identity('viewer'), app['id'])
    assert not [key for key in reads if key.endswith('/preview.jpg')]


@pytest.mark.asyncio
async def test_preview_integrity_is_rechecked(processing, identity):
    service, app, _ = await image_app(processing, identity, filename=None, preview=preview_jpeg())
    stored = next(p for p in service.storage.base.rglob('preview.jpg') if 'assets' not in p.parts)
    stored.write_bytes(b'corrupt')
    with pytest.raises(HostedApplicationError) as error:
        await HostedThumbnailService(service).get(identity(), app['id'])
    assert error.value.code == 'CONTENT_UNAVAILABLE'


@pytest.mark.asyncio
async def test_preview_over_the_bundle_area_limit_is_served(processing, identity):
    # The preview has its own bounds (2048 px per side, no area limit), not the bundled-raster area limit.
    picture = preview_jpeg(2048, 2048)
    service, app, _ = await image_app(processing, identity, filename=None, preview=picture)
    assert await HostedThumbnailService(service).get(identity(), app['id']) == preview_cover(picture)


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [png(64, 48), preview_jpeg(2049, 16), b'\xff\xd8\xff\xd9'])
async def test_a_stored_preview_that_is_not_a_jpeg_within_the_bounds_is_not_served(processing, identity, body):
    service, app, staged = await image_app(processing, identity, filename=None, preview=preview_jpeg())
    stored = next(p for p in service.storage.base.rglob('preview.jpg') if 'assets' not in p.parts)
    stored.write_bytes(body)
    # The version row's size and digest match the stored bytes, so only the fit check can refuse them.
    async with processing[1].sessions() as session, session.begin():
        row = await session.get(HostedApplicationVersions, UUID(str(staged['version_id'])))
        row.preview_size, row.preview_sha256 = len(body), hashlib.sha256(body).digest()
    assert await HostedThumbnailService(service).get(identity(), app['id']) is None
