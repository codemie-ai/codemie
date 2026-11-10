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

"""Owner-uploaded card covers: bounded, signature-checked rasters stored beside the application row.

Images are never decoded on the server. The declared media type must match the file signature and the
shared structure checks, and the header dimensions are bounded, before anything is written.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterable

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from codemie.core.hosted_applications.binary import image
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.protocol import check_etag
from codemie.rest_api.models.hosted_application_cover import HostedApplicationCovers as Cover

MAX_COVER_BYTES = 1024 * 1024
MAX_COVER_SIDE = 2048
MAX_COVER_PIXELS = 4 * 1024 * 1024
# Exact request media types and the suffix the shared raster checks expect.
COVER_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def sniff(data: bytes) -> str | None:
    """The raster type named by the leading signature; the declared type and any file name are ignored."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def read_cover(stream: AsyncIterable[bytes]) -> bytes:
    """Collect the request body, refusing it as soon as it passes the cover limit."""
    body = bytearray()
    async for chunk in stream:
        body.extend(chunk)
        if len(body) > MAX_COVER_BYTES:
            raise HostedApplicationError("COVER_TOO_LARGE", "Cover images are limited to 1 MiB", 413)
    return bytes(body)


def verify_cover(data: bytes, media_type: str) -> tuple[int, int]:
    """Width and height of a PNG, JPEG or WebP whose signature, structure and size bounds all hold."""
    # thumbnails imports the catalog, which composes this module, so its header parser is imported on use.
    from codemie.service.hosted_applications.thumbnails import dimensions

    suffix = COVER_TYPES.get(media_type)
    if suffix is None:
        raise HostedApplicationError("UNSUPPORTED_MEDIA_TYPE", "Choose a PNG, JPEG or WebP image", 415)
    if not data:
        raise HostedApplicationError("INVALID_COVER", "The cover image is empty", 422)
    if sniff(data) != media_type:
        raise HostedApplicationError("UNSUPPORTED_MEDIA_TYPE", "The image does not match its declared type", 415)
    # A header the parser cannot read reports 0 x 0.
    width, height = dimensions(data, suffix)
    if not image(data, suffix) or width < 1 or height < 1:
        raise HostedApplicationError("INVALID_COVER", "The cover image is damaged", 422)
    if width > MAX_COVER_SIDE or height > MAX_COVER_SIDE or width * height > MAX_COVER_PIXELS:
        raise HostedApplicationError("INVALID_COVER", "Cover images are limited to 2048 pixels per side", 422)
    return width, height


class CoverServiceMixin:
    async def _covered(self, tx, apps) -> set:
        """Ids of ``apps`` that have a cover: one primary-key probe for a whole page, never the image bytes."""
        if not apps:
            return set()
        query = select(Cover.app_id).where(Cover.app_id.in_([app.id for app in apps]))
        return set((await tx.session.execute(query)).scalars())

    async def _cover_size(self, tx, app) -> int:
        """Bytes the application's cover charges to its owner (0 without one); the image is not read."""
        query = select(Cover.byte_size).where(Cover.app_id == app.id)
        return (await tx.session.execute(query)).scalar_one_or_none() or 0

    @staticmethod
    def _charge_cover(tx, delta):
        """Covers are stored bytes of their owner, under the same quota as versions: apply the size change.

        ``tx.quota`` is the creator's quota row, which ``managed_app(lock=True)`` locked before the application.
        """
        if delta > 0 and tx.quota.stored_bytes + tx.quota.reserved_bytes + delta > tx.quota.byte_limit:
            raise HostedApplicationError("QUOTA_EXCEEDED", "Not enough application storage is available", 409)
        if delta:
            tx.quota.stored_bytes += delta
            tx.quota.revision += 1
            tx.quota.updated_at = tx.now

    async def put_cover(self, identity, app_id, media_type, if_match, key, stream):
        await self.directory.require(identity)
        # Read and verify before the write transaction: no row lock is held while bytes arrive.
        data = await read_cover(stream)
        width, height = verify_cover(data, media_type)
        digest = hashlib.sha256(data).digest()
        details = {
            "media_type": media_type,
            "byte_size": len(data),
            "width": width,
            "height": height,
            "sha256": digest.hex(),
        }
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            op, replay = await tx.operation(
                "app.cover.update",
                app.id,
                {"media_type": media_type, "sha256": digest.hex(), "byte_size": len(data), "if_match": if_match},
                key,
                app_id=app.id,
            )
            if replay is not None:
                return replay
            check_etag(if_match, "app", app.id, app.revision)
            # The locked application row serializes cover writes, so the previous size cannot change meanwhile.
            self._charge_cover(tx, len(data) - await self._cover_size(tx, app))
            values = {
                "media_type": media_type,
                "sha256": digest,
                "byte_size": len(data),
                "width": width,
                "height": height,
                "data": data,
                "updated_by": identity.principal_id,
                "updated_at": tx.now,
            }
            await tx.session.execute(
                insert(Cover)
                .values(app_id=app.id, created_at=tx.now, **values)
                .on_conflict_do_update(index_elements=["app_id"], set_=values)
            )
            app.revision += 1
            app.updated_at = tx.now
            return await tx.finish(op, await self._project(tx, app, identity.user), details=details)

    async def delete_cover(self, identity, app_id, if_match, key):
        """Removing a missing cover is an idempotent no-op: the application and its ETag stay unchanged."""
        await self.directory.require(identity)
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            op, replay = await tx.operation("app.cover.delete", app.id, {"if_match": if_match}, key, app_id=app.id)
            if replay is not None:
                return replay
            check_etag(if_match, "app", app.id, app.revision)
            removed = (
                await tx.session.execute(delete(Cover).where(Cover.app_id == app.id).returning(Cover.byte_size))
            ).scalar_one_or_none()
            if removed is not None:
                self._charge_cover(tx, -removed)
                app.revision += 1
                app.updated_at = tx.now
            return await tx.finish(
                op, await self._project(tx, app, identity.user), details={"removed": removed is not None}
            )
