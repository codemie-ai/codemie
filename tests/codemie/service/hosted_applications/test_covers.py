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

"""Owner-uploaded card covers: bounded, signature-checked, owner-only and served before bundled rasters."""

import asyncio
import hashlib
import json
import struct
import zlib
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.hosted_application import (
    HostedApplicationOperations,
    HostedApplicationStorageAttempts,
    HostedApplicationUserState,
    HostedApplications,
)
from codemie.rest_api.models.hosted_application_cover import HostedApplicationCovers
from codemie.rest_api.models.hosted_application_requests import AppCreate, AppPatch
from codemie.service.hosted_applications.covers import MAX_COVER_BYTES, verify_cover
from codemie.service.hosted_applications.maintenance import MaintenanceWorker
from codemie.service.hosted_applications.thumbnails import HostedThumbnailService
from tests.codemie.service.hosted_applications.test_query_plans import recording
from tests.codemie.service.hosted_applications.test_thumbnail import image_app, png, share_with_project
from tests.codemie.service.hosted_applications.test_worker import queued

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
HTML = b"<!doctype html><script>alert(1)</script>"
GIF = b"GIF89a" + struct.pack("<HH", 8, 8) + bytes(3) + b";"


def jpeg(width=64, height=48):
    """SOI, one baseline frame header (the only part read) and EOI."""
    return b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + bytes(9) + b"\xff\xd9"


def webp(width=64, height=48):
    chunk = b"VP8X" + struct.pack("<I", 10) + bytes(4) + (width - 1).to_bytes(3, "little")
    chunk += (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def padded_png(size):
    """A structurally valid PNG of exactly ``size`` bytes: an ancillary chunk after IHDR pads it."""
    base = png(16, 16)
    filler = bytes(size - len(base) - 12)
    chunk = struct.pack(">I", len(filler)) + b"pAdD" + filler + struct.pack(">I", zlib.crc32(b"pAdD" + filler))
    return base[:33] + chunk + base[33:]


def damaged(data):
    flipped = bytearray(data)
    flipped[45] ^= 0xFF  # inside the IDAT payload: the chunk CRC no longer matches
    return bytes(flipped)


async def chunks(data, size=65536):
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def create(service, context, name="Cover app"):
    return await service.create_app(
        context, AppCreate(name=name, description="A visual with a cover", project="demo"), str(uuid4())
    )


async def put(service, context, app, data=None, media_type="image/png", key=None):
    body = png() if data is None else data
    return await service.put_cover(context, app["id"], media_type, app["etag"], key or str(uuid4()), chunks(body))


async def remove(service, context, app, key=None):
    return await service.delete_cover(context, app["id"], app["etag"], key or str(uuid4()))


async def cover_rows(repo):
    async with repo.sessions() as session:
        return (
            await session.execute(
                select(
                    HostedApplicationCovers.app_id,
                    HostedApplicationCovers.media_type,
                    HostedApplicationCovers.width,
                    HostedApplicationCovers.height,
                    HostedApplicationCovers.byte_size,
                    HostedApplicationCovers.sha256,
                    HostedApplicationCovers.updated_by,
                )
            )
        ).all()


async def cover_audit(repo):
    async with repo.sessions() as session:
        return (
            await session.execute(
                select(HostedApplicationOperations.action, HostedApplicationOperations.audit_details)
                .where(HostedApplicationOperations.action.like("app.cover.%"))
                .order_by(HostedApplicationOperations.created_at)
            )
        ).all()


async def usage(service, repo, context):
    """What the usage endpoint the UI polls reports for ``context``."""
    from codemie.rest_api.routers.hosted_applications import usage as usage_endpoint

    response = await usage_endpoint(context, SimpleNamespace(directory=service.directory, repository=repo))
    return json.loads(response.body)


async def leave_free(repo, free):
    """Charge the owner's quota so that exactly ``free`` bytes stay available."""
    async with repo.sessions() as session, session.begin():
        await session.execute(
            update(HostedApplicationUserState)
            .where(HostedApplicationUserState.principal_id == "owner")
            .values(stored_bytes=HostedApplicationUserState.byte_limit - free)
        )


@pytest.mark.asyncio
async def test_owner_uploads_replaces_and_removes_a_cover(catalog, identity):
    service, repo, _ = catalog
    owner = identity()
    app = await create(service, owner)
    assert app["has_cover"] is False
    uploaded = await put(service, owner, app)
    assert uploaded["has_cover"] is True
    assert uploaded["revision"] == app["revision"] + 1 and uploaded["etag"] != app["etag"]
    assert uploaded["updated_at"] != app["updated_at"]
    assert (await service.get_app(owner, app["id"]))["has_cover"] is True
    image = jpeg(640, 360)
    replaced = await put(service, owner, uploaded, image, "image/jpeg")
    assert replaced["has_cover"] is True and replaced["revision"] == uploaded["revision"] + 1
    assert [tuple(row) for row in await cover_rows(repo)] == [
        (UUID(app["id"]), "image/jpeg", 640, 360, len(image), hashlib.sha256(image).digest(), "owner")
    ]
    removed = await remove(service, owner, replaced)
    assert removed["has_cover"] is False and removed["revision"] == replaced["revision"] + 1
    # Removing a missing cover is an idempotent no-op: 200 with the unchanged application.
    again = await remove(service, owner, removed)
    assert again["has_cover"] is False and (again["revision"], again["etag"]) == (removed["revision"], removed["etag"])
    assert await cover_rows(repo) == []
    audit = await cover_audit(repo)
    assert [action for action, _ in audit] == ["app.cover.update"] * 2 + ["app.cover.delete"] * 2
    assert audit[1][1] == {
        "action": "app.cover.update",
        "media_type": "image/jpeg",
        "byte_size": len(image),
        "width": 640,
        "height": 360,
        "sha256": hashlib.sha256(image).hexdigest(),
    }
    assert [details["removed"] for _, details in audit[2:]] == [True, False]
    # Audit rows describe the image; they never carry its bytes.
    assert all(len(json.dumps(details)) < 512 for _, details in audit)


@pytest.mark.asyncio
async def test_cover_bytes_are_charged_to_the_owner_quota_by_their_size_difference(catalog, identity):
    service, repo, _ = catalog
    owner = identity()
    app = await create(service, owner)
    small, large = png(16, 16), padded_png(200_000)
    assert (await usage(service, repo, owner))["stored_bytes"] == 0
    key = str(uuid4())
    first = await put(service, owner, app, small, key=key)
    reported = await usage(service, repo, owner)
    assert reported["stored_bytes"] == len(small)
    assert reported["available_bytes"] == reported["byte_limit"] - len(small)
    # A replay answers from the stored result and never charges again.
    assert await put(service, owner, app, small, key=key) == first
    assert (await usage(service, repo, owner))["stored_bytes"] == len(small)
    grown = await put(service, owner, first, large)
    assert (await usage(service, repo, owner))["stored_bytes"] == len(large)
    shrunk = await put(service, owner, grown, webp(), "image/webp")
    assert (await usage(service, repo, owner))["stored_bytes"] == len(webp())
    second = await put(service, owner, await create(service, owner, "Second"), jpeg(), "image/jpeg")
    assert (await usage(service, repo, owner))["stored_bytes"] == len(webp()) + len(jpeg())
    removed = await remove(service, owner, shrunk)
    assert (await usage(service, repo, owner))["stored_bytes"] == len(jpeg())
    # Removing a missing cover refunds nothing.
    await remove(service, owner, removed)
    assert (await usage(service, repo, owner))["stored_bytes"] == len(jpeg())
    await remove(service, owner, second)
    final = await usage(service, repo, owner)
    assert (final["stored_bytes"], final["reserved_bytes"]) == (0, 0)


@pytest.mark.asyncio
async def test_a_cover_over_the_quota_is_refused_and_changes_nothing(catalog, identity):
    service, repo, _ = catalog
    owner = identity()
    app = await create(service, owner)
    image = padded_png(4096)
    await leave_free(repo, len(image) - 1)
    before = await usage(service, repo, owner)
    with pytest.raises(HostedApplicationError) as error:
        await put(service, owner, app, image)
    assert (error.value.status, error.value.code, error.value.message) == (
        409,
        "QUOTA_EXCEEDED",
        "Not enough application storage is available",
    )
    current = await service.get_app(owner, app["id"])
    assert (current["revision"], current["etag"], current["has_cover"]) == (app["revision"], app["etag"], False)
    assert await cover_rows(repo) == [] and await cover_audit(repo) == []
    assert await usage(service, repo, owner) == before
    # An exact fit is accepted; at a full quota only a bigger replacement is refused.
    await leave_free(repo, len(image))
    covered = await put(service, owner, app, image)
    assert (await usage(service, repo, owner))["available_bytes"] == 0
    with pytest.raises(HostedApplicationError) as error:
        await put(service, owner, covered, padded_png(len(image) + 1))
    assert error.value.code == "QUOTA_EXCEEDED"
    assert [row.byte_size for row in await cover_rows(repo)] == [len(image)]
    await put(service, owner, covered, padded_png(len(image) - 1000))
    assert (await usage(service, repo, owner))["available_bytes"] == 1000


@pytest.mark.asyncio
async def test_cover_writes_replay_by_key_and_require_the_current_etag(catalog, identity):
    service, _, _ = catalog
    owner = identity()
    app = await create(service, owner)
    key = str(uuid4())
    first = await put(service, owner, app, key=key)
    # The replay answers with the stored result even though the request's ETag is stale by now.
    assert await put(service, owner, app, key=key) == first
    assert (await service.get_app(owner, app["id"]))["revision"] == first["revision"]
    with pytest.raises(HostedApplicationError) as error:
        await put(service, owner, app, jpeg(), "image/jpeg", key=key)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(HostedApplicationError) as error:
        await put(service, owner, app)
    assert (error.value.status, error.value.code) == (409, "REVISION_CONFLICT")
    with pytest.raises(HostedApplicationError) as error:
        await service.put_cover(owner, app["id"], "image/png", None, str(uuid4()), chunks(png()))
    assert (error.value.status, error.value.code) == (428, "PRECONDITION_REQUIRED")
    delete_key = str(uuid4())
    removed = await remove(service, owner, first, key=delete_key)
    assert await remove(service, owner, first, key=delete_key) == removed
    with pytest.raises(HostedApplicationError) as error:
        await remove(service, owner, first)
    assert (error.value.status, error.value.code) == (409, "REVISION_CONFLICT")


@pytest.mark.asyncio
async def test_only_the_owner_can_change_a_cover(catalog, identity):
    service, repo, _ = catalog
    app = await create(service, identity())
    covered = await put(service, identity(), app)
    stranger = identity("viewer")
    with pytest.raises(HostedApplicationError) as error:
        await put(service, stranger, covered, jpeg(), "image/jpeg")
    assert (error.value.status, error.value.code) == (404, "NOT_FOUND")
    with pytest.raises(HostedApplicationError) as error:
        await remove(service, stranger, covered)
    assert (error.value.status, error.value.code) == (404, "NOT_FOUND")
    assert [row.media_type for row in await cover_rows(repo)] == ["image/png"]
    assert (await service.get_app(identity(), app["id"]))["revision"] == covered["revision"]


@pytest.mark.asyncio
async def test_managers_keep_changing_the_cover_of_a_suspended_application(catalog, identity):
    """AC15: managers keep working on a suspended app; only activate, publish, shared on and move are refused."""
    service, repo, _ = catalog
    owner = identity()
    covered = await put(service, owner, await create(service, owner))
    assert covered["has_cover"] is True
    async with repo.sessions() as session, session.begin():
        await session.execute(
            update(HostedApplications)
            .where(HostedApplications.id == UUID(covered["id"]))
            .values(security_state="suspended")
        )
    replaced = await put(service, owner, covered, jpeg(), "image/jpeg")
    assert replaced["suspended"] is True
    assert [row.media_type for row in await cover_rows(repo)] == ["image/jpeg"]
    await remove(service, owner, replaced)
    assert await cover_rows(repo) == []


@pytest.mark.parametrize(
    ("media_type", "data", "status", "code"),
    [
        ("image/gif", GIF, 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("image/svg+xml", SVG, 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("text/html", HTML, 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("application/json", b"{}", 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("image/jpeg", png(), 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("image/webp", png(), 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("image/png", jpeg(), 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("image/png", SVG, 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("image/png", HTML, 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("image/png", GIF, 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("image/png", b"", 422, "INVALID_COVER"),
        ("image/png", damaged(png()), 422, "INVALID_COVER"),
        ("image/png", png() + HTML, 422, "INVALID_COVER"),
        ("image/jpeg", b"\xff\xd8\xff\xd9", 422, "INVALID_COVER"),
        ("image/webp", webp()[:-1], 422, "INVALID_COVER"),
        ("image/png", png(0, 16), 422, "INVALID_COVER"),
        ("image/png", png(2049, 16), 422, "INVALID_COVER"),
        ("image/png", png(16, 2049), 422, "INVALID_COVER"),
        ("image/jpeg", jpeg(4096, 4096), 422, "INVALID_COVER"),
        ("image/webp", webp(2048, 2049), 422, "INVALID_COVER"),
    ],
)
def test_declared_type_must_match_a_bounded_raster_signature(media_type, data, status, code):
    with pytest.raises(HostedApplicationError) as error:
        verify_cover(data, media_type)
    assert (error.value.status, error.value.code) == (status, code)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (damaged(png()), "The cover image is damaged"),
        (png(0, 16), "The cover image is damaged"),
        (png(2049, 16), "Cover images are limited to 2048 pixels per side"),
    ],
)
def test_damaged_and_oversized_covers_are_reported_differently(data, message):
    with pytest.raises(HostedApplicationError) as error:
        verify_cover(data, "image/png")
    assert error.value.message == message


@pytest.mark.parametrize(
    ("media_type", "data", "size"),
    [
        ("image/png", png(2048, 2048), (2048, 2048)),
        ("image/png", padded_png(MAX_COVER_BYTES), (16, 16)),
        ("image/jpeg", jpeg(1, 2048), (1, 2048)),
        ("image/webp", webp(640, 360), (640, 360)),
    ],
)
def test_bounded_rasters_are_accepted_from_their_headers(media_type, data, size):
    assert verify_cover(data, media_type) == size


@pytest.mark.asyncio
async def test_a_rejected_cover_changes_nothing(catalog, identity):
    service, repo, _ = catalog
    owner = identity()
    app = await create(service, owner)
    for media_type, data in (("image/png", SVG), ("image/svg+xml", SVG), ("image/png", png(4096, 16))):
        with pytest.raises(HostedApplicationError):
            await put(service, owner, app, data, media_type)
    current = await service.get_app(owner, app["id"])
    assert current["revision"] == app["revision"] and current["has_cover"] is False
    assert await cover_rows(repo) == [] and await cover_audit(repo) == []


@pytest.mark.asyncio
async def test_cover_stream_stops_at_one_mebibyte(catalog, identity):
    service, _, _ = catalog
    owner = identity()
    exact = await put(service, owner, await create(service, owner), padded_png(MAX_COVER_BYTES))
    assert exact["has_cover"] is True
    consumed = []

    async def endless():
        while True:
            consumed.append(65536)
            yield bytes(65536)

    with pytest.raises(HostedApplicationError) as error:
        await service.put_cover(owner, exact["id"], "image/png", exact["etag"], str(uuid4()), endless())
    assert (error.value.status, error.value.code) == (413, "COVER_TOO_LARGE")
    assert sum(consumed) == MAX_COVER_BYTES + 65536
    with pytest.raises(HostedApplicationError) as error:
        await put(service, owner, exact, padded_png(MAX_COVER_BYTES) + b"\0")
    assert (error.value.status, error.value.code) == (413, "COVER_TOO_LARGE")


@pytest.mark.asyncio
async def test_custom_cover_wins_over_the_bundled_raster_for_owner_and_project_viewer(processing, identity):
    service, app, staged = await image_app(processing, identity)
    thumbnails = HostedThumbnailService(service)
    owner, viewer = identity(), identity("viewer")
    assert (await thumbnails.get(owner, app["id"])).body == png()
    custom = jpeg(800, 450)
    expected = (custom, "image/jpeg", '"' + hashlib.sha256(custom).hexdigest() + '"')
    covered = await put(service, owner, app, custom, "image/jpeg")
    assert tuple(await thumbnails.get(owner, app["id"])) == expected
    shared = await share_with_project(service, owner, covered, staged["version_id"])
    assert shared["has_cover"] is True
    assert tuple(await thumbnails.get(viewer, app["id"])) == expected
    assert (await service.get_app(viewer, app["id"]))["has_cover"] is True
    listed = (await service.list_apps(viewer, scope="project"))["items"]
    assert [(item["id"], item["has_cover"]) for item in listed] == [(app["id"], True)]
    removed = await remove(service, owner, shared)
    for context in (owner, viewer):
        assert (await thumbnails.get(context, app["id"])).body == png()
    assert (await service.get_app(viewer, app["id"]))["has_cover"] is False
    covered = await put(service, owner, removed, custom, "image/jpeg")
    unshared = await service.patch_app(owner, app["id"], AppPatch(shared=False), covered["etag"], str(uuid4()))
    assert unshared["has_cover"] is True
    # The cover still exists, but an application that is no longer shared is invisible to its former viewer.
    with pytest.raises(HostedApplicationError) as error:
        await thumbnails.get(viewer, app["id"])
    assert error.value.status == 404
    assert tuple(await thumbnails.get(owner, app["id"])) == expected


@pytest.mark.asyncio
async def test_cover_follows_live_visibility_not_its_own_existence(processing, identity):
    service, _, _, _, _ = processing
    owner = identity()
    custom = webp(640, 360)
    covered = await put(service, owner, await create(service, owner), custom, "image/webp")
    thumbnails = HostedThumbnailService(service)
    # No accepted version yet: the owner still sees the cover while the application is active.
    assert (await thumbnails.get(owner, covered["id"])).body == custom
    for stranger in (identity("viewer"),):
        with pytest.raises(HostedApplicationError) as error:
            await thumbnails.get(stranger, covered["id"])
        assert error.value.status == 404
    await service.delete(owner, covered["id"], covered["etag"], str(uuid4()))
    with pytest.raises(HostedApplicationError) as error:
        await thumbnails.get(owner, covered["id"])
    assert error.value.status == 404


@pytest.mark.asyncio
async def test_thumbnail_route_serves_the_custom_cover_with_the_same_headers(processing, identity):
    from codemie.rest_api.routers.hosted_applications import thumbnail as thumbnail_endpoint

    service, _, _, _, _ = processing
    owner = identity()
    custom = png(320, 180)
    covered = await put(service, owner, await create(service, owner), custom)
    value = SimpleNamespace(catalog=service)
    app_id, tag = UUID(covered["id"]), '"' + hashlib.sha256(custom).hexdigest() + '"'
    first = await thumbnail_endpoint(app_id, owner, value, None)
    assert first.status_code == 200 and first.body == custom and first.media_type == "image/png"
    assert first.headers["etag"] == tag and first.headers["cache-control"] == "private, no-cache"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert first.headers["content-security-policy"] == "default-src 'none'; sandbox; frame-ancestors 'none'"
    assert first.headers["content-disposition"] == 'inline; filename="application-preview"'
    cached = await thumbnail_endpoint(app_id, owner, value, tag)
    assert cached.status_code == 304 and cached.body == b"" and cached.headers["etag"] == tag
    # A matching validator never bypasses live authorization.
    with pytest.raises(HostedApplicationError):
        await thumbnail_endpoint(app_id, identity("viewer"), value, tag)


@pytest.mark.asyncio
async def test_delete_purges_and_refunds_the_cover_with_the_other_application_rows(processing, identity):
    service, repo, _, worker, _ = processing
    owner = identity()
    app, _ = await queued(service, owner)
    assert await worker.run_one()
    image = padded_png(50_000)
    covered = await put(service, owner, await service.get_app(owner, app["id"]), image)
    await service.delete(owner, app["id"], covered["etag"], str(uuid4()))
    maintenance = MaintenanceWorker(repo, service.storage)
    async with repo.sessions() as session, session.begin():
        attempts = (
            (
                await session.execute(
                    select(HostedApplicationStorageAttempts).where(
                        HostedApplicationStorageAttempts.app_id == UUID(app["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        for attempt in attempts:
            if attempt.lease_until:
                attempt.lease_until -= timedelta(minutes=5)
    while await maintenance.cleanup_one():
        pass
    # Every stored file is refunded by cleanup; the cover stays charged until the purge finishes.
    assert [row.app_id for row in await cover_rows(repo)] == [UUID(app["id"])]
    assert (await usage(service, repo, owner))["stored_bytes"] == len(image)
    status = await service.deletion_status(owner, app["id"])
    assert (status["state"], status["remaining_prefixes"], status["charged_bytes"]) == ("purging", 0, len(image))
    assert await maintenance.finish_purge_one()
    assert await cover_rows(repo) == []
    final = await usage(service, repo, owner)
    assert (final["stored_bytes"], final["reserved_bytes"]) == (0, 0)
    status = await service.deletion_status(owner, app["id"])
    assert (status["state"], status["charged_bytes"]) == ("purged", 0)
    async with repo.sessions() as session:
        assert (await session.get(HostedApplications, UUID(app["id"]))).lifecycle_state == "purged"


@pytest.mark.asyncio
async def test_purge_refund_never_waits_for_a_busy_owner_state(processing, identity):
    service, repo, _, _, _ = processing
    owner = identity()
    covered = await put(service, owner, await create(service, owner))
    await service.delete(owner, covered["id"], covered["etag"], str(uuid4()))
    maintenance = MaintenanceWorker(repo, service.storage)
    async with repo.sessions() as busy, busy.begin():
        # An owner write holds this lock while it waits for the application row the purge holds.
        await busy.execute(
            select(HostedApplicationUserState)
            .where(HostedApplicationUserState.principal_id == "owner")
            .with_for_update()
        )
        assert not await asyncio.wait_for(maintenance.finish_purge_one(), timeout=5)
    assert [row.app_id for row in await cover_rows(repo)] == [UUID(covered["id"])]
    assert (await usage(service, repo, owner))["stored_bytes"] == len(png())
    assert await maintenance.finish_purge_one()
    assert await cover_rows(repo) == [] and (await usage(service, repo, owner))["stored_bytes"] == 0


@pytest.mark.asyncio
async def test_catalog_reports_has_cover_with_one_batched_probe_and_no_image_bytes(catalog, identity):
    service, repo, _ = catalog
    owner = identity()
    first = await put(service, owner, await create(service, owner, "First"))

    async def listed():
        statements, original = recording(repo)
        try:
            page = await service.list_apps(owner, sort="name_asc")
        finally:
            repo.sessions = original
        return {item["name"]: item["has_cover"] for item in page["items"]}, [str(item) for item in statements]

    covers, few = await listed()
    assert covers == {"First": True}
    second = await create(service, owner, "Second")
    await put(service, owner, await create(service, owner, "Third"), jpeg(), "image/jpeg")
    await create(service, owner, "Fourth")
    covers, many = await listed()
    assert covers == {"First": True, "Fourth": False, "Second": False, "Third": True}
    assert len(many) == len(few), "has_cover must not add a query per application"
    assert not any("hosted_application_covers.data" in statement for statement in few + many)
    assert (await service.get_app(owner, second["id"]))["has_cover"] is False
    assert (await service.get_app(owner, first["id"]))["has_cover"] is True
