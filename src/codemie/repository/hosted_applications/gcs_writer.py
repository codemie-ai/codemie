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

"""The GCS writer of hosted object storage: staged uploads composed with a per-writer fence object."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import AsyncIterable
from uuid import uuid4

from codemie.configs import logger
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.contract import WriterLease
from codemie.repository.hosted_applications.filesystem import StoredObject
from codemie.repository.hosted_applications.object_storage import (
    FENCES_ROOT,
    GCS_DELETE_BATCH,
    PART_BYTES,
    STAGING_ROOT,
    Digest,
    FencedOps,
    HostedObjectStorage,
    ObjectExists,
    ObjectMissing,
    ObjectOpsError,
    _checksum_mismatch,
    _delete_batch,
    _first_keys,
    _PrefixWriter,
    _size_mismatch,
    _unavailable,
    fenced,
)


def _fenced_write_error(exc: ObjectOpsError) -> HostedApplicationError:
    """A gone upload session or staging object is a lost lease (cleanup removed it); the rest is unavailable."""
    if isinstance(exc, ObjectMissing):
        return fenced(exc)
    return _unavailable("STORAGE_UNAVAILABLE", "Application storage is unavailable", exc)


def _hash_and_write(ops: FencedOps, digest: Digest, handle: object, body: bytes) -> None:
    digest.update(body)
    ops.write(handle, body)


class FencedPrefixWriter(_PrefixWriter):
    """Writes immutable objects under one attempt prefix, fenced by a fence object (GCS).

    Open creates the 0-byte fence ``.fences/<prefix>/<writer-token>`` with ``ifGenerationMatch=0``; the token
    is fresh per writer, so a requeued writer on the same prefix gets its own fence. A put streams to
    ``.staging/<prefix>/<writer-token>/<relative key>`` through a resumable upload in ``PART_BYTES`` writes,
    checks size and SHA-256 before the upload is finalized, then composes ``[staging@G, fence@F]`` into the
    key with ``ifGenerationMatch=0``. Cleanup deletes fences first, so a writer whose fence is gone cannot
    commit.

    The fence has two halves. The fence object covers a writer whose fence existed when cleanup started.
    The database recheck right after ``open()`` covers a writer that creates its fence only after cleanup's
    verifying pass: every caller rechecks its claim in the database before the first put
    (``_receive_heartbeat`` in ``receiving.py``, ``fence(work)`` in ``worker.py``). Cleanup takes an attempt
    only after its lease ended or its writer fence changed, and the recheck refuses both, so no compose is
    sent; the writer exit then deletes the late fence. A writer that passes the recheck already has its
    fence, which cleanup deletes before anything else. This writer never signs from the lease, so it keeps
    the lease only for ``renew``.
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
        self.token = uuid4().hex
        self.fence_key = FENCES_ROOT + prefix + "/" + self.token
        self.staging_base = STAGING_ROOT + prefix + "/" + self.token + "/"
        self.fence_generation: int | None = None
        self._fence_requested = False

    async def open(self) -> None:
        self._fence_requested = True
        try:
            self.fence_generation = await self._call(self.ops.create_if_absent, self.fence_key, b"")
        except ObjectOpsError as exc:
            raise _unavailable("STORAGE_UNAVAILABLE", "Application storage is unavailable", exc) from exc

    async def put(
        self,
        relative_key: str,
        chunks: AsyncIterable[bytes],
        *,
        expected_size: int,
        expected_digest: bytes | None = None,
    ) -> StoredObject:
        key = self._admit(relative_key, expected_size)
        staging = self.staging_base + relative_key
        handle = None
        try:
            handle = await self._call(self.ops.begin_upload, staging)
            self.used_objects += 1
            size = 0
            digest = hashlib.sha256()
            buffer = bytearray()
            async for chunk in chunks:
                self._count(size, chunk, expected_size)
                buffer += chunk
                size += len(chunk)
                while len(buffer) >= PART_BYTES:
                    await self._call(_hash_and_write, self.ops, digest, handle, bytes(buffer[:PART_BYTES]))
                    del buffer[:PART_BYTES]
            if size != expected_size:
                raise _size_mismatch()
            if buffer:
                await self._call(_hash_and_write, self.ops, digest, handle, bytes(buffer))
            if expected_digest is not None and not hmac.compare_digest(digest.digest(), expected_digest):
                # The upload is never finalized: the finally block cancels it, so no staging object exists.
                raise _checksum_mismatch()
            generation = await self._call(self.ops.finish, handle)
            handle = None
            try:
                await self._commit(key, staging, generation)
            finally:
                await self._remove_staging(staging, generation)
            return StoredObject(size, digest.digest())
        except ObjectOpsError as exc:
            raise _fenced_write_error(exc) from exc
        finally:
            if handle is not None:
                await self._cancel(handle)

    async def _commit(self, key: str, staging: str, generation: int) -> None:
        """Compose ``[staging, fence]`` into ``key``; the 0-byte fence adds no bytes.

        A 412 or 404 is ambiguous: the key already exists, or a source (the fence) was deleted or replaced.
        The key decides: present is ``STORAGE_IMMUTABLE``, absent is ``STORAGE_LEASE_LOST``.
        """
        sources = [(staging, generation), (self.fence_key, self.fence_generation)]
        try:
            await self._call(self.ops.compose, sources, key)
        except (ObjectExists, ObjectMissing) as exc:
            if await self._call(self.ops.exists, key):
                raise HostedApplicationError(
                    "STORAGE_IMMUTABLE", "Artifact bytes have already been stored", 409
                ) from exc
            raise fenced(exc) from exc

    async def _remove_staging(self, staging: str, generation: int) -> None:
        try:
            await self._call(self.ops.delete, staging, generation)
        except ObjectMissing:
            pass
        except ObjectOpsError as exc:
            # The writer exit lists its staging objects and deletes what is left.
            logger.warning(
                f"Hosted object storage staging delete failed: code=STORAGE_UNAVAILABLE error={type(exc).__name__} "
                f"provider_code={exc.code}"
            )

    async def _cancel(self, handle: object) -> None:
        try:
            await self._call(self.ops.cancel, handle)
        except ObjectOpsError as exc:
            # An abandoned resumable session creates no object; the provider expires it.
            logger.warning(
                f"Hosted object storage upload cancel failed: code=STORAGE_UNAVAILABLE error={type(exc).__name__} "
                f"provider_code={exc.code}"
            )

    async def _release(self) -> None:
        """Delete the fence first, so nothing more can commit, then every staging object of this writer."""
        if self._fence_requested:
            try:
                # The key holds this writer's own token: whatever generation is there is this writer's.
                await self._call(self.ops.delete, self.fence_key)
            except ObjectMissing:
                pass
            except ObjectOpsError as exc:
                logger.warning(
                    f"Hosted object storage fence delete failed: code=STORAGE_UNAVAILABLE "
                    f"error={type(exc).__name__} provider_code={exc.code}"
                )
        try:
            while keys := await self._call(_first_keys, self.ops, self.staging_base):
                await self._call(_delete_batch, self.ops, keys, GCS_DELETE_BATCH)
        except ObjectOpsError as exc:
            logger.warning(
                f"Hosted object storage staging cleanup failed: code=STORAGE_UNAVAILABLE "
                f"error={type(exc).__name__} provider_code={exc.code}"
            )
