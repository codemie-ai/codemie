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

"""Provider-neutral hosted application storage on a dedicated object-store bucket.

``HostedObjectStorage`` owns the key rules, the bucket marker, Range reads, resumable cleanup and the
error mapping. It delegates every request to a narrow, synchronous ops seam (``S3Ops`` in ``s3_backend``,
``GcsOps`` in ``gcs_backend``), which runs only on ``OBJECT_EXECUTOR``. In-repo fakes implement the same
seams for offline tests.

Two writers share the ``_PrefixWriter`` shape defined here: ``MultipartPrefixWriter`` (``s3_writer``) fences
a writer by presigned expiry, ``FencedPrefixWriter`` (``gcs_writer``) by a fence object that every commit
names as a compose source.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from functools import partial
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from codemie.configs import logger
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.hosted_apps_gateway.admission import ContentLimits
from codemie.repository.hosted_applications.contract import WriterLease
from codemie.repository.hosted_applications.filesystem import (
    CONTENT_UNAVAILABLE_MESSAGE,
    STORAGE_UNAVAILABLE_MESSAGE,
    DeletionProof,
    _parts,
    _prefix,
    joined_io_on,
)

if TYPE_CHECKING:
    from codemie.repository.hosted_applications.gcs_writer import FencedPrefixWriter
    from codemie.repository.hosted_applications.s3_writer import MultipartPrefixWriter

# What a cleanup drain lists and removes: object keys, or (key, upload id) pairs of open S3 uploads.
CleanupItem = str | tuple[str, str]

# One thread per gateway content stream plus headroom for writers, cleanup and the marker.
OBJECT_THREADS = ContentLimits().total_concurrency + 16
OBJECT_EXECUTOR = ThreadPoolExecutor(max_workers=OBJECT_THREADS, thread_name_prefix="hosted-object-storage")
# Large ranges are read as consecutive GETs of at most this many bytes each.
WINDOW_BYTES = 8 * 1024 * 1024
STORAGE_ID_KEY = ".hosted/storage-id"
MARKER_ATTEMPTS = 3
# Every object is a multipart upload of parts this size; a 0-byte object is one empty part.
PART_BYTES = 8 * 1024 * 1024
# One cleanup step deletes at most this many keys: the S3 DeleteObjects limit.
DELETE_BATCH = 1000
# A GCS JSON API batch request carries at most 100 calls.
GCS_DELETE_BATCH = 100
# GCS writers keep their fence and staging objects under these roots, outside every attempt prefix:
# ``.fences/<prefix>/<writer-token>`` and ``.staging/<prefix>/<writer-token>/<relative key>``.
FENCES_ROOT = ".fences/"
STAGING_ROOT = ".staging/"
# Cleanup progress is kept for at most this many prefixes; the oldest is dropped first. A dropped entry
# only costs one listing: the bucket itself keeps the work already done.
CLEANUP_PROGRESS_LIMIT = 256


class ObjectOpsError(Exception):
    """A typed ops failure. ``code`` and ``status`` are the provider's error code and HTTP status."""

    def __init__(self, code: str = "", status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(code)


class ObjectExists(ObjectOpsError):
    """A conditional create found the key already present."""


class ObjectMissing(ObjectOpsError):
    """The key does not exist."""


class UploadGone(ObjectOpsError):
    """The multipart upload id is unknown, completed or aborted."""


class PresignExpired(ObjectOpsError):
    """The presigned request reached the provider after its expiry: the writer lease that signed it ended."""


class ObjectUnavailable(ObjectOpsError):
    """Any other provider, network or credential failure."""


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    modified: datetime


class Digest(Protocol):
    """A running hash, as ``hashlib.sha256()`` returns it."""

    def update(self, data: bytes, /) -> None: ...

    def digest(self) -> bytes: ...


class ObjectOps(Protocol):
    """The read, marker, list and delete half of every provider's ops seam. Calls are synchronous."""

    def put_if_absent(self, key: str, body: bytes) -> bool:
        """Create the object only if the key is absent; False when it already exists."""

    def get_range(self, key: str, start: int, end: int) -> bytes:
        """Bytes ``start..end`` inclusive, clipped to the object's end; ``b""`` when start is past the end."""

    def list_keys(self, prefix: str, token: str | None) -> tuple[list[ObjectInfo], str | None]:
        """One page of objects under ``prefix`` and the token of the next page, or None."""

    def delete_keys(self, keys: list[str]) -> None:
        """Delete at most 1000 keys (GCS: 100); any per-key failure raises ``ObjectUnavailable``."""


class FencedOps(ObjectOps, Protocol):
    """The writer half of the GCS seam: generation-checked creates, resumable uploads and compose."""

    def create_if_absent(self, key: str, body: bytes) -> int:
        """Create the object with ``ifGenerationMatch=0``; its generation. ``ObjectExists`` when present."""

    def begin_upload(self, key: str) -> object:
        """Start a resumable ``ifGenerationMatch=0`` upload to ``key``; returns its handle."""

    def write(self, handle: object, data: bytes) -> None:
        """Append ``data`` to the upload."""

    def finish(self, handle: object) -> int:
        """Finalize the upload; the generation of the new object."""

    def cancel(self, handle: object) -> None:
        """Abandon the upload; no object is created."""

    def compose(self, sources: list[tuple[str, int]], destination: str) -> None:
        """Concatenate the sources, each at its generation, into ``destination`` with ``ifGenerationMatch=0``."""

    def exists(self, key: str) -> bool:
        """Whether ``key`` holds an object."""

    def delete(self, key: str, generation: int | None = None) -> None:
        """Delete ``key``, only at ``generation`` when given."""


def object_call[T](
    function: Callable[..., T],
    *args: object,
    cancel_dispose: Callable[[T], object] | None = None,
    **kwargs: object,
) -> Awaitable[T]:
    """Run one blocking ops call on the object executor; cancellation waits for the call to finish."""
    return joined_io_on(OBJECT_EXECUTOR, function, *args, cancel_dispose=cancel_dispose, **kwargs)


def opening_call[T](function: Callable[..., T], *args: object) -> asyncio.Future[T]:
    """Run one blocking call of the startup open on the object executor; cancellation abandons it at once.

    Opening holds no writer and no lock, so a hung bucket or metadata server must not hold platform startup
    past its deadline: the stuck call stays on the object executor, never on the event loop.
    """
    return asyncio.get_running_loop().run_in_executor(OBJECT_EXECUTOR, partial(function, *args))


def _unavailable(code: str, message: str, exc: ObjectOpsError) -> HostedApplicationError:
    logger.warning(f"Hosted object storage failed: code={code} error={type(exc).__name__} provider_code={exc.code}")
    return HostedApplicationError(code, message, 503)


def lease_lost() -> HostedApplicationError:
    return HostedApplicationError("STORAGE_LEASE_LOST", "The storage lease ended", 409)


def fenced(exc: ObjectOpsError) -> HostedApplicationError:
    """Log a write that cleanup fenced out and return the lost-lease error."""
    logger.warning(
        f"Hosted object storage write fenced: code=STORAGE_LEASE_LOST error={type(exc).__name__} "
        f"provider_code={exc.code}"
    )
    return lease_lost()


async def _uncancellable[T](awaitable: Awaitable[T]) -> T:
    """Run ``awaitable`` to its end even when the caller is cancelled, then re-raise that cancellation."""
    task = asyncio.ensure_future(awaitable)
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except Exception:
            break
    if cancellation is not None:
        if not task.cancelled():
            task.exception()
        raise cancellation
    return task.result()


@dataclass(frozen=True)
class CleanupDrain:
    """One kind of data under a prefix that cleanup removes, as two synchronous one-request steps.

    ``list_page()`` returns the first page of what remains, listed from scratch. ``remove(pending)``
    removes the head of ``pending`` and returns how many items it removed. A ``quiesce`` drain holds
    the writers' staging state: its final listing decides ``DeletionProof.writer_quiesced``.
    """

    name: str
    list_page: Callable[[], list[CleanupItem]]
    remove: Callable[[list[CleanupItem]], int]
    quiesce: bool = False


@dataclass
class CleanupProgress:
    """Where cleanup of one prefix stands between calls. ``drain == len(drains)`` is the verifying pass."""

    drain: int = 0
    # Items the last listing returned that are not removed yet.
    pending: list[CleanupItem] = field(default_factory=list)


def _first_page[T](list_page: Callable[[str, str | None], tuple[list[T], str | None]], prefix: str) -> list[T]:
    """The first non-empty page, or [] when nothing is listed. An empty truncated page reads on."""
    token = None
    while True:
        page, token = list_page(prefix, token)
        if page or token is None:
            return page


def _first_keys(ops: ObjectOps, prefix: str) -> list[str]:
    return [info.key for info in _first_page(ops.list_keys, prefix)]


def _abort_first(ops: ObjectOps, pending: list[tuple[str, str]]) -> int:
    key, upload_id = pending[0]
    # Gone means completed or aborted since it was listed: the objects drain deletes a completed one.
    with suppress(UploadGone):
        ops.abort(key, upload_id)
    return 1


def _delete_batch(ops: ObjectOps, pending: list[str], size: int = DELETE_BATCH) -> int:
    batch = pending[:size]
    ops.delete_keys(batch)
    return len(batch)


def read_storage_id(ops: ObjectOps) -> str:
    """Create the bucket marker if absent, then read it back: concurrent openers all read the winner's id."""
    try:
        for attempt in range(MARKER_ATTEMPTS):
            ops.put_if_absent(STORAGE_ID_KEY, uuid4().hex.encode("ascii"))
            try:
                value = ops.get_range(STORAGE_ID_KEY, 0, 63)
                break
            except ObjectMissing:
                # A concurrent conditional create lost its race and left no marker yet: try again.
                time.sleep(0.1 * (attempt + 1))
        else:
            raise ObjectMissing("NoSuchKey")
    except ObjectOpsError as exc:
        raise _unavailable("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, exc) from exc
    if not re.fullmatch(rb"[a-f0-9]{32}", value):
        raise HostedApplicationError("STORAGE_UNAVAILABLE", "Invalid private bucket identity", 503)
    return value.decode("ascii")


class HostedObjectStorage:
    """Hosted application storage on one dedicated bucket, bound to it by the ``.hosted/storage-id`` marker."""

    def __init__(
        self,
        ops: ObjectOps,
        *,
        provider: str,
        storage_id: str,
        cleanup_grace_seconds: int,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not re.fullmatch(r"[a-f0-9]{32}", storage_id):
            raise HostedApplicationError("STORAGE_UNAVAILABLE", "Invalid private bucket identity", 503)
        self.ops = ops
        self.provider = provider
        self.cleanup_grace_seconds = cleanup_grace_seconds
        # The writer measures time since each lease receipt on this clock; tests inject a fake one.
        self.monotonic = monotonic
        self._storage_id = storage_id
        # Writers open in this process, per prefix: cleanup of that prefix waits for them, as the
        # filesystem lock makes it wait. Writers on other pods are fenced by their presigned expiry.
        self._writers: dict[str, int] = {}
        self._cleanups: dict[str, CleanupProgress] = {}

    @classmethod
    async def open(
        cls,
        ops: ObjectOps,
        *,
        provider: str,
        cleanup_grace_seconds: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> HostedObjectStorage:
        """Read or create the bucket marker on the object executor and return the bound adapter.

        The marker call is abandoned, not joined, when the caller's startup deadline cancels it.
        """
        storage_id = await opening_call(read_storage_id, ops)
        return cls(
            ops,
            provider=provider,
            storage_id=storage_id,
            cleanup_grace_seconds=cleanup_grace_seconds,
            monotonic=monotonic,
        )

    @property
    def storage_id(self) -> str:
        return self._storage_id

    @property
    def namespace_suffix(self) -> str:
        return ":bucket:" + self._storage_id

    def bind_namespace(self, namespace: str) -> str:
        return namespace + self.namespace_suffix

    def owns_namespace(self, namespace: str) -> bool:
        return namespace.endswith(self.namespace_suffix)

    async def fingerprint(self, key: str) -> tuple[str, int, datetime]:
        _parts(key)
        try:
            page, _ = await object_call(self.ops.list_keys, key, None)
        except ObjectOpsError as exc:
            raise _unavailable("CONTENT_UNAVAILABLE", CONTENT_UNAVAILABLE_MESSAGE, exc) from exc
        # Listings are sorted, so the exact key precedes every longer key that shares it as a prefix.
        if not page or page[0].key != key:
            raise HostedApplicationError("CONTENT_UNAVAILABLE", CONTENT_UNAVAILABLE_MESSAGE, 503)
        return page[0].key, page[0].size, page[0].modified

    async def read_small(self, key: str, *, maximum: int) -> bytes:
        _parts(key)
        try:
            data = await object_call(self.ops.get_range, key, 0, maximum)
        except ObjectOpsError as exc:
            raise _unavailable("CONTENT_UNAVAILABLE", CONTENT_UNAVAILABLE_MESSAGE, exc) from exc
        if len(data) > maximum:
            raise HostedApplicationError("CONTENT_UNAVAILABLE", CONTENT_UNAVAILABLE_MESSAGE, 503)
        return data

    async def read_range(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        """Yield bytes ``start..end`` inclusive, one GET per window of at most ``WINDOW_BYTES``."""
        _parts(key)
        offset = start
        while offset <= end:
            last = min(offset + WINDOW_BYTES - 1, end)
            try:
                part = await object_call(self.ops.get_range, key, offset, last)
            except ObjectOpsError as exc:
                raise _unavailable("CONTENT_UNAVAILABLE", CONTENT_UNAVAILABLE_MESSAGE, exc) from exc
            if len(part) != last - offset + 1:
                # The object is shorter than its ledger size: never serve a truncated representation.
                raise HostedApplicationError("CONTENT_UNAVAILABLE", CONTENT_UNAVAILABLE_MESSAGE, 503)
            offset = last + 1
            yield part

    @asynccontextmanager
    async def writer(
        self, prefix: str, *, byte_limit: int, object_limit: int, lease: WriterLease | None = None
    ) -> AsyncIterator[MultipartPrefixWriter | FencedPrefixWriter]:
        """A fenced writer on ``prefix``; its exit waits for its calls and removes what it left behind.

        GCS (provider ``gcp``) gets a ``FencedPrefixWriter``, fenced by its fence object and by the caller's
        database recheck after open; S3 gets a ``MultipartPrefixWriter``, which signs from the lease.
        """
        # The provider writers import this module, so they are imported where they are first needed.
        from codemie.repository.hosted_applications.gcs_writer import FencedPrefixWriter
        from codemie.repository.hosted_applications.s3_writer import MultipartPrefixWriter

        _prefix(prefix)
        writer_type = FencedPrefixWriter if self.provider == "gcp" else MultipartPrefixWriter
        writer = writer_type(self, prefix, byte_limit, object_limit, lease)
        writer.check_lease()
        self._writers[prefix] = self._writers.get(prefix, 0) + 1
        try:
            try:
                size, count = await writer.inventory()
            except ObjectOpsError as exc:
                raise _unavailable("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, exc) from exc
            if size > byte_limit or count > object_limit:
                raise HostedApplicationError("STORAGE_LIMIT", "Application storage reservation exceeded", 413)
            writer.used_bytes, writer.used_objects = size, count
            await writer.open()
            yield writer
        finally:
            try:
                await writer.close()
            finally:
                self._writers[prefix] -= 1
                if not self._writers[prefix]:
                    del self._writers[prefix]

    def _drains(self, base: str) -> tuple[CleanupDrain, ...]:
        """S3: abort every open upload under ``base``, then delete every object under it.

        GCS: delete every fence under ``.fences/<base>`` (any writer token), then every staging object
        under ``.staging/<base>``, then every object under ``base``, 100 keys per step. The fences are a
        quiesce drain: a fence found by the verifying pass gives no proof (``writer_quiesced`` is False).
        """
        ops = self.ops
        if self.provider == "gcp":
            remove = partial(_delete_batch, ops, size=GCS_DELETE_BATCH)
            return (
                CleanupDrain("fences", partial(_first_keys, ops, FENCES_ROOT + base), remove, True),
                CleanupDrain("staging", partial(_first_keys, ops, STAGING_ROOT + base), remove),
                CleanupDrain("objects", partial(_first_keys, ops, base), remove),
            )
        return (
            CleanupDrain("uploads", partial(_first_page, ops.list_uploads, base), partial(_abort_first, ops), True),
            CleanupDrain("objects", partial(_first_keys, ops, base), partial(_delete_batch, ops)),
        )

    async def sweep_page(self, token: str | None) -> tuple[list[ObjectInfo], str | None]:
        """One listing page of the whole bucket, resuming after ``token``, for the reconciliation sweep."""
        return await object_call(self.ops.list_keys, "", token)

    async def delete_listed(self, keys: list[str]) -> None:
        """Delete keys the sweep listed, in requests no larger than the provider allows."""
        size = GCS_DELETE_BATCH if self.provider == "gcp" else DELETE_BATCH
        for start in range(0, len(keys), size):
            await object_call(self.ops.delete_keys, keys[start : start + size])

    async def delete_prefix(self, prefix: str) -> DeletionProof:
        """Remove everything under ``prefix``, one provider request per step, resumable across calls.

        Each drain lists from scratch and removes what it lists until a listing is empty. Then a verifying
        pass lists every drain again, in order. A timeout cancels between steps: a finished step stays done,
        a step whose result never arrived is repeated, and the next call goes on from the progress kept here.

        Returns ``DeletionProof(True, True)`` only when the verifying pass lists nothing. When it lists a
        quiesce item (an S3 upload opened after the abort loop) it returns ``DeletionProof(False, False)``,
        which is no proof: ``cleanup_one`` keeps the attempt and the next call aborts that item first.
        Objects found by the verifying pass are deleted in the same call, then the pass runs again.
        """
        _prefix(prefix)
        if self._writers.get(prefix):
            raise HostedApplicationError("WRITER_ACTIVE", "Application cleanup is waiting for an active writer", 409)
        drains = self._drains(prefix + "/")
        progress = self._cleanups.pop(prefix, None) or CleanupProgress()
        # Re-inserted last, so the least recently cleaned prefix is dropped first.
        self._cleanups[prefix] = progress
        while len(self._cleanups) > CLEANUP_PROGRESS_LIMIT:
            del self._cleanups[next(iter(self._cleanups))]
        try:
            while True:
                if progress.drain < len(drains):
                    await self._drain_step(drains[progress.drain], progress)
                    continue
                found = await self._verify(drains)
                if found is None:
                    self._cleanups.pop(prefix, None)
                    return DeletionProof(writer_quiesced=True, absence_verified=True)
                progress.drain, progress.pending = found, []
                if drains[found].quiesce:
                    logger.warning(
                        f"Hosted object storage cleanup is not quiesced: code=WRITER_ACTIVE drain={drains[found].name}"
                    )
                    return DeletionProof(writer_quiesced=False, absence_verified=False)
        except ObjectOpsError as exc:
            raise _unavailable("STORAGE_UNAVAILABLE", "Application cleanup is pending", exc) from exc

    @staticmethod
    async def _drain_step(drain: CleanupDrain, progress: CleanupProgress) -> None:
        """One request: remove the head of the pending items, or list the drain again."""
        if progress.pending:
            removed = await object_call(drain.remove, list(progress.pending))
            # Recorded only after the request returned: a cancelled one is repeated, and removal is idempotent.
            del progress.pending[:removed]
            return
        page = await object_call(drain.list_page)
        if page:
            progress.pending = list(page)
        else:
            progress.drain += 1

    @staticmethod
    async def _verify(drains: tuple[CleanupDrain, ...]) -> int | None:
        """List every drain from scratch, quiesce drain first; the index of the first non-empty one.

        Listing the uploads before the objects is what makes the pass a proof: with no open upload left
        and every presigned Create expired, no object can appear after the objects listing.
        """
        for index, drain in enumerate(drains):
            if await object_call(drain.list_page):
                return index
        return None


class _PrefixWriter:
    """What both writers share: the reservation limits, the in-flight call count and the exit.

    Exit (normal, failed or cancelled) refuses new puts, waits until no call of this writer is in flight,
    then runs ``_release`` to remove what the writer left behind.
    """

    def __init__(
        self,
        storage: HostedObjectStorage,
        prefix: str,
        byte_limit: int,
        object_limit: int,
        lease: WriterLease | None,
    ):
        self.storage, self.ops, self.prefix = storage, storage.ops, prefix
        self.byte_limit, self.object_limit = byte_limit, object_limit
        self.used_bytes = self.used_objects = 0
        self.lease = lease
        self.closed = False
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()

    def renew(self, lease: WriterLease) -> None:
        """Keep the latest heartbeat result: the S3 writer signs from it, the GCS writer ignores it."""
        self.lease = lease

    def check_lease(self) -> None:
        """Refuse to open before any request when the lease cannot fence this writer."""

    async def open(self) -> None:
        """Provider state the writer needs before its first put."""

    async def _call[T](self, function: Callable[..., T], *args: object) -> T:
        self._active += 1
        self._idle.clear()
        try:
            return await object_call(function, *args)
        finally:
            self._active -= 1
            if not self._active:
                self._idle.set()

    async def inventory(self) -> tuple[int, int]:
        """The size and count of the objects already under the prefix."""
        size = count = 0
        token = None
        while True:
            page, token = await self._call(self.ops.list_keys, self.prefix + "/", token)
            size += sum(info.size for info in page)
            count += len(page)
            if token is None:
                return size, count

    def _admit(self, relative_key: str, expected_size: int) -> str:
        """The full key of a put that fits the reservation; ``STORAGE_LIMIT`` otherwise."""
        _parts(relative_key)
        key = self.prefix + "/" + relative_key
        _parts(key)
        if (
            self.closed
            or type(expected_size) is not int
            or expected_size < 0
            or self.used_objects >= self.object_limit
            or self.used_bytes + expected_size > self.byte_limit
        ):
            raise HostedApplicationError("STORAGE_LIMIT", "Application storage reservation exceeded", 413)
        return key

    def _count(self, size: int, chunk: object, expected_size: int) -> None:
        """Check one streamed chunk against the declared size and the byte limit, then charge it."""
        if not isinstance(chunk, bytes):
            raise ValueError("Invalid storage chunk")
        if size + len(chunk) > expected_size or self.used_bytes + len(chunk) > self.byte_limit:
            raise HostedApplicationError("UPLOAD_SIZE_MISMATCH", "Artifact bytes exceed the declared size", 413)
        self.used_bytes += len(chunk)

    async def close(self) -> None:
        """Refuse new puts, wait for every call in flight, then remove what the writer left behind."""
        self.closed = True
        await _uncancellable(self._quiesce())

    async def _quiesce(self) -> None:
        await self._idle.wait()
        await self._release()

    async def _release(self) -> None:
        """Remove the provider state this writer left behind; failures are logged and left for cleanup."""


def _size_mismatch() -> HostedApplicationError:
    return HostedApplicationError("UPLOAD_SIZE_MISMATCH", "Artifact bytes do not match the declared size")


def _checksum_mismatch() -> HostedApplicationError:
    return HostedApplicationError("UPLOAD_CHECKSUM_MISMATCH", "Artifact checksum does not match its declaration")
