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

"""Authorized raster covers: the owner's uploaded cover first, then rasters from accepted bundles.

No rendering, decoding or remote fetch.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import PurePosixPath
from typing import NamedTuple

from sqlalchemy import select

from codemie.core.hosted_applications.binary import image
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.hosted_apps_gateway.cache import ImmutableContentCache
from codemie.repository.hosted_applications.repository import as_uuid, not_found
from codemie.rest_api.models.hosted_application import (
    HostedApplications as App,
    HostedApplicationStorageAttempts as Attempt,
)
from codemie.rest_api.models.hosted_application_cover import HostedApplicationCovers as UploadedCover
from codemie.core.hosted_applications.access import manageable, readable

MAX_THUMBNAIL_BYTES = 1024 * 1024
RASTER = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}


# JPEG start-of-frame markers, the segments that carry the frame size.
JPEG_FRAME_MARKERS = (192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207)
THUMBNAIL_PRIORITIES = ('preview', 'thumbnail', 'screenshot', 'cover', 'poster')


def _png_dimensions(data):
    return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')


def _webp_dimensions(data):
    if data[12:16] == b'VP8X':
        return 1 + int.from_bytes(data[24:27], 'little'), 1 + int.from_bytes(data[27:30], 'little')
    if data[12:16] == b'VP8L' and data[20] == 47:
        bits = int.from_bytes(data[21:25], 'little')
        return 1 + (bits & 16383), 1 + ((bits >> 14) & 16383)
    if data[12:16] == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
        return int.from_bytes(data[26:28], 'little') & 16383, int.from_bytes(data[28:30], 'little') & 16383
    return 0, 0


def _jpeg_dimensions(data):
    position = 2
    for _ in range(4096):
        if position + 4 > len(data) or data[position] != 255:
            break
        marker = data[position + 1]
        if marker == 255:
            position += 1
            continue
        length = int.from_bytes(data[position + 2 : position + 4], 'big')
        if length < 2 or position + 2 + length > len(data):
            break
        if marker in JPEG_FRAME_MARKERS and length >= 8:
            return int.from_bytes(data[position + 7 : position + 9], 'big'), int.from_bytes(
                data[position + 5 : position + 7], 'big'
            )
        position += 2 + length
    return 0, 0


def dimensions(data, suffix):
    if suffix == '.png' and len(data) >= 24:
        return _png_dimensions(data)
    if suffix == '.webp' and len(data) >= 30:
        return _webp_dimensions(data)
    if suffix in ('.jpg', '.jpeg'):
        return _jpeg_dimensions(data)
    return 0, 0


def _priority(stem):
    """The rank of a cover-like file name, or None when the name is not one."""
    for rank, name in enumerate(THUMBNAIL_PRIORITIES):
        if stem == name or stem.startswith(name + '-') or stem.startswith(name + '_'):
            return rank
    return None


def _candidates(manifest):
    """Up to five bundled rasters that may serve as the cover, best first."""
    candidates = []
    for path, asset in manifest['files'].items():
        file = PurePosixPath(path)
        stem, suffix = file.stem.casefold(), file.suffix.casefold()
        if suffix not in RASTER or asset['mime'] != RASTER[suffix] or not 0 < asset['size'] <= MAX_THUMBNAIL_BYTES:
            continue
        rank = _priority(stem)
        if rank is not None:
            candidates.append((rank, path, asset))
    return sorted(candidates)[:5]


def _fits(body, suffix):
    """Whether ``body`` is a raster of ``suffix``'s type within the cover size limits."""
    width, height = dimensions(body, suffix)
    return image(body, suffix) and 1 <= width <= 2048 and 1 <= height <= 2048 and width * height <= 1024 * 1024


async def _matches(tx, app, condition):
    """Whether ``app`` satisfies the access ``condition``."""
    found = await tx.session.execute(select(App.id).where(App.id == app.id, condition))
    return found.scalar_one_or_none() is not None


class Cover(NamedTuple):
    """A verified cover image; ``etag`` is its strong validator (the image's SHA-256)."""

    body: bytes
    mime: str
    etag: str


class HostedThumbnailService:
    def __init__(self, catalog):
        self.catalog = catalog
        self.storage = catalog.storage
        # Digest-bound manifests are immutable: reuse the content gateway's bounded cache.
        self.cache = ImmutableContentCache.shared(self.storage)

    async def get(self, identity, app_id) -> Cover | None:
        # Existing projection checks viewer/owner membership before the definitive primary check
        # below; authorization runs on every call, only immutable manifest data is cached.
        await self.catalog.get_app(identity, app_id)
        async with self.catalog.repository.transaction(*identity) as tx:
            app = (await tx.session.execute(select(App).where(App.id == as_uuid(app_id)))).scalar_one_or_none()
            if app is None or app.lifecycle_state != 'active':
                raise not_found()
            # A manager (creator, project admin, platform admin) sees the working version; a viewer, the live one.
            owner = await _matches(tx, app, manageable(App.__table__, identity.user.id))
            if not owner and not await _matches(tx, app, readable(App.__table__, identity.user.id)):
                raise not_found()
            version = await self.catalog._eligible_version(
                tx, app, app.selected_version_id if owner else app.active_version_id
            )
            # Viewers keep the bundled-raster rule (a servable live version); the owner needs only an active app.
            if version is None and not owner:
                raise not_found()
            uploaded = (
                await tx.session.execute(
                    select(UploadedCover.media_type, UploadedCover.sha256, UploadedCover.data).where(
                        UploadedCover.app_id == app.id
                    )
                )
            ).first()
            if uploaded is not None:
                # Verified when stored; its SHA-256 is the strong validator, as for bundled rasters.
                return Cover(bytes(uploaded.data), uploaded.media_type, '"' + uploaded.sha256.hex() + '"')
            if version is None:
                raise not_found()
            attempt = await tx.session.get(Attempt, version.accepted_attempt_id)
            if attempt is None:
                raise not_found()
            version, attempt = version.model_dump(), attempt.model_dump()
        manifest = await self.cache.manifest(version, attempt)
        for _, path, asset in _candidates(manifest):
            body = await self._verified(asset)
            if _fits(body, PurePosixPath(path).suffix.casefold()):
                return Cover(body, asset['mime'], '"' + asset['sha256'] + '"')
        return None

    async def _verified(self, asset):
        """The stored bytes of ``asset``, refused unless they match the manifest's size and digest."""
        body = await self.storage.read_small(asset['key'], maximum=MAX_THUMBNAIL_BYTES)
        if len(body) != asset['size'] or not hmac.compare_digest(hashlib.sha256(body).hexdigest(), asset['sha256']):
            raise HostedApplicationError('CONTENT_UNAVAILABLE', 'Application preview is unavailable', 503)
        return body
