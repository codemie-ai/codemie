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

"""The S3 writer of hosted object storage: multipart uploads fenced by the presigned expiry of the lease."""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING

from codemie.configs import logger
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.contract import WriterLease
from codemie.repository.hosted_applications.filesystem import StoredObject
from codemie.repository.hosted_applications.object_storage import (
    PART_BYTES,
    Digest,
    HostedObjectStorage,
    ObjectExists,
    ObjectMissing,
    ObjectOpsError,
    ObjectUnavailable,
    PresignExpired,
    UploadGone,
    _checksum_mismatch,
    _PrefixWriter,
    _size_mismatch,
    _unavailable,
    fenced,
    lease_lost,
)

if TYPE_CHECKING:
    from codemie.repository.hosted_applications.s3_backend import S3Ops

# A 409 ConditionalRequestConflict on Complete is transient: retry it, each time with a fresh expiry.
COMPLETE_ATTEMPTS = 3


def multipart_etag(parts: list[tuple[int, str]]) -> str:
    """The ETag S3 gives a completed multipart upload: MD5 of the parts' binary MD5s, then the part count."""
    digests = b"".join(bytes.fromhex(etag.strip('"')) for _, etag in parts)
    return f'"{hashlib.md5(digests, usedforsecurity=False).hexdigest()}-{len(parts)}"'


def _is_conflict(exc: ObjectOpsError) -> bool:
    return isinstance(exc, ObjectUnavailable) and (exc.code == "ConditionalRequestConflict" or exc.status == 409)


def _is_ambiguous(exc: ObjectOpsError) -> bool:
    """No answer, or a server error: the request may have committed before it failed."""
    return isinstance(exc, ObjectUnavailable) and (exc.status is None or exc.status >= 500)


def _write_error(exc: ObjectOpsError) -> HostedApplicationError:
    """412 is an existing object. Only a gone upload or an expired presign is a lost lease.

    Any other 403 or 404 (``AccessDenied`` under a live lease, ``InvalidAccessKeyId``, ``NoSuchBucket``) is a
    permission or bucket misconfiguration: a retryable ``STORAGE_UNAVAILABLE``, never a lease conflict.
    """
    if isinstance(exc, ObjectExists):
        return HostedApplicationError("STORAGE_IMMUTABLE", "Artifact bytes have already been stored", 409)
    if isinstance(exc, UploadGone | PresignExpired):
        return fenced(exc)
    return _unavailable("STORAGE_UNAVAILABLE", "Application storage is unavailable", exc)


def _hash_and_upload(ops: S3Ops, digest: Digest, key: str, upload_id: str, number: int, body: bytes) -> str:
    # Hashing runs on the object executor with the upload, never on the event loop.
    digest.update(body)
    return ops.upload_part(key, upload_id, number, body)


class MultipartPrefixWriter(_PrefixWriter):
    """Writes immutable objects under one attempt prefix, fenced by the database writer lease.

    Every object is a multipart upload. Create and Complete are presigned at send time with an expiry
    of ``floor((until - database_now) - (monotonic() - received_at))`` from the latest lease, so a
    writer that lost its lease cannot commit after cleanup starts. Parts use plain SDK calls: a part
    commits nothing. Complete carries ``If-None-Match: *``, so a key is never overwritten.
    """

    def __init__(
        self,
        storage: HostedObjectStorage,
        prefix: str,
        byte_limit: int,
        object_limit: int,
        lease: WriterLease | None,
    ):
        super().__init__(storage, prefix, byte_limit, object_limit, lease)
        # upload id -> key of every upload this writer created and did not complete or abort.
        self.open_uploads: dict[str, str] = {}

    def check_lease(self) -> None:
        # No lease, or under a second of it: there is no safe expiry, so fail before any request.
        self.expiry()

    def expiry(self) -> int:
        """Whole seconds left on the lease, measured on the monotonic clock since its receipt."""
        lease = self.lease
        if lease is not None:
            remaining = (lease.until - lease.database_now).total_seconds() - (
                self.storage.monotonic() - lease.received_at
            )
            if remaining >= 1:
                return math.floor(remaining)
        logger.warning("Hosted object storage write fenced: code=STORAGE_LEASE_LOST error=LeaseExpired")
        raise lease_lost()

    async def put(
        self,
        relative_key: str,
        chunks: AsyncIterable[bytes],
        *,
        expected_size: int,
        expected_digest: bytes | None = None,
    ) -> StoredObject:
        key = self._admit(relative_key, expected_size)
        upload_id = None
        try:
            upload_id = await self._call(self.ops.presigned_create, key, self.expiry())
            self.open_uploads[upload_id] = key
            self.used_objects += 1
            size = 0
            digest = hashlib.sha256()
            parts: list[tuple[int, str]] = []
            buffer = bytearray()
            async for chunk in chunks:
                self._count(size, chunk, expected_size)
                buffer += chunk
                size += len(chunk)
                while len(buffer) >= PART_BYTES:
                    await self._upload_part(key, upload_id, parts, digest, bytes(buffer[:PART_BYTES]))
                    del buffer[:PART_BYTES]
            if size != expected_size:
                raise _size_mismatch()
            if buffer or not parts:
                # The last part, which may be short; a 0-byte object is one empty part.
                await self._upload_part(key, upload_id, parts, digest, bytes(buffer))
            if expected_digest is not None and not hmac.compare_digest(digest.digest(), expected_digest):
                # No Complete is sent: the finally block aborts the upload, so the key never exists.
                raise _checksum_mismatch()
            await self._complete(key, upload_id, parts, size)
            del self.open_uploads[upload_id]
            return StoredObject(size, digest.digest())
        except ObjectOpsError as exc:
            raise _write_error(exc) from exc
        finally:
            if upload_id in self.open_uploads:
                await self._abort(key, upload_id)

    async def _upload_part(
        self, key: str, upload_id: str, parts: list[tuple[int, str]], digest: Digest, body: bytes
    ) -> None:
        number = len(parts) + 1
        parts.append((number, await self._call(_hash_and_upload, self.ops, digest, key, upload_id, number, body)))

    async def _complete(self, key: str, upload_id: str, parts: list[tuple[int, str]], size: int) -> None:
        """Send Complete; retry a conflict, and settle a lost answer by what the key now holds."""
        for attempt in range(1, COMPLETE_ATTEMPTS + 1):
            try:
                await self._call(self.ops.presigned_complete, key, upload_id, list(parts), self.expiry())
                return
            except ObjectUnavailable as exc:
                if _is_ambiguous(exc) and await self._committed(key, parts, size):
                    logger.info(f"Hosted object storage Complete committed before it failed: provider_code={exc.code}")
                    return
                if attempt == COMPLETE_ATTEMPTS or not _is_conflict(exc):
                    raise
                logger.info(f"Hosted object storage retries Complete: provider_code={exc.code} attempt={attempt}")

    async def _committed(self, key: str, parts: list[tuple[int, str]], size: int) -> bool:
        """Whether ``key`` holds exactly this upload: its size and the multipart ETag of these parts."""
        try:
            stored_size, etag = await self._call(self.ops.head, key)
        except ObjectMissing:
            return False
        return stored_size == size and etag == multipart_etag(parts)

    async def _abort(self, key: str, upload_id: str) -> None:
        try:
            await self._call(self.ops.abort, key, upload_id)
        except UploadGone:
            pass
        except ObjectOpsError as exc:
            # The upload stays listed for the writer exit, then for cleanup.
            logger.warning(
                f"Hosted object storage abort failed: code=STORAGE_UNAVAILABLE error={type(exc).__name__} "
                f"provider_code={exc.code}"
            )
            return
        self.open_uploads.pop(upload_id, None)

    async def _release(self) -> None:
        """Abort the uploads left open."""
        for upload_id, key in list(self.open_uploads.items()):
            await self._abort(key, upload_id)
        if self.open_uploads:
            logger.warning(f"Hosted object storage writer left open uploads: count={len(self.open_uploads)}")
