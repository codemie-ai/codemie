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

"""In-memory object-store fakes that implement the hosted storage ops seams for offline tests.

``CROSS_ADAPTERS`` builds every offline adapter (filesystem, S3 fake, GCS fake) from a directory path, so a
test that takes one of them runs once per adapter and asserts the same behaviour on each.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codemie.configs import config
from codemie.repository.hosted_applications.contract import WriterLease
from codemie.repository.hosted_applications.filesystem import HostedFileStorage
from codemie.repository.hosted_applications.object_storage import (
    HostedObjectStorage,
    ObjectExists,
    ObjectInfo,
    ObjectMissing,
    ObjectUnavailable,
    PresignExpired,
    UploadGone,
    read_storage_id,
)

MIN_PART_BYTES = 5 * 1024 * 1024
# One GCS JSON API batch request carries at most 100 calls.
GCS_BATCH_KEYS = 100
MONOTONIC_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


class FakeClock:
    """A settable server clock: ``clock()`` returns the current time, ``advance(seconds)`` moves it."""

    def __init__(self, now: datetime | None = None):
        self.now = now or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def monotonic(self) -> float:
        """Seconds on the same clock, as ``time.monotonic`` reports them: inject it as the writer's monotonic."""
        return (self.now - MONOTONIC_EPOCH).total_seconds()


def multipart_etag(parts: list[tuple[int, str]]) -> str:
    """The ETag S3 gives a completed multipart upload: MD5 of the parts' binary MD5s, then the part count."""
    digests = b"".join(bytes.fromhex(etag.strip('"')) for _, etag in parts)
    return f'"{hashlib.md5(digests, usedforsecurity=False).hexdigest()}-{len(parts)}"'


class FakeS3:
    """One in-memory S3 bucket that implements ``S3Ops`` with the S3 rules the adapter relies on.

    State:
      - ``objects``: key -> (body, modified). Seed objects with ``put_if_absent`` or ``objects[key] = ...``.
      - ``uploads``: upload id -> (key, {part number: (etag, body)}) for open multipart uploads.
      - ``calls``: ``(op, key, *details)`` for every op that reached the bucket, in order.

    Rules:
      - ``put_if_absent`` is a conditional create: it returns False and keeps the object when the key exists.
      - ``get_range(key, start, end)`` is inclusive and clipped to the object's end; ``b""`` when start is
        at or past the end; ``ObjectMissing`` when the key does not exist.
      - Presigned ops sign at ``clock()`` on entry and send after the ``before_`` hook. A request whose
        ``clock()`` at send is past ``signed + expires_in`` fails with ``PresignExpired`` (403
        ``AccessDenied``), as an expired presigned URL does on S3.
      - ``presigned_complete`` refuses an existing key (``ObjectExists``, If-None-Match), an unknown or
        aborted upload id (``UploadGone``), a wrong part ETag (``InvalidPart``) and a part other than the
        last one under 5 MiB (``EntityTooSmall``). ``abort`` and ``upload_part`` on an unknown upload id
        raise ``UploadGone``.
      - Listings are sorted and paged by ``page_size``; the token is the last key (or key and upload id).
      - ``head`` returns the size and ETag: a multipart ETag for a completed upload, else the body's MD5.

    Interleaving hooks: ``hooks`` maps ``"before_<op>"`` / ``"after_<op>"`` to a callable. Each op calls its
    hooks with the op's arguments as keywords, outside the bucket lock, on the calling thread. A hook may
    block (for example on a ``threading.Barrier``), advance ``clock``, change the bucket, or raise a
    typed ops error to inject a failure.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None, page_size: int = 1000):
        self.clock = clock or FakeClock()
        self.page_size = page_size
        self.objects: dict[str, tuple[bytes, datetime]] = {}
        self.uploads: dict[str, tuple[str, dict[int, tuple[str, bytes]]]] = {}
        # key -> multipart ETag of every object a Complete created; other objects get a single-part ETag.
        self.etags: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.hooks: dict[str, Callable[..., None]] = {}
        self._lock = threading.Lock()
        self._upload_count = 0

    def _hook(self, name: str, **arguments) -> None:
        hook = self.hooks.get(name)
        if hook is not None:
            hook(**arguments)

    def _record(self, *call) -> None:
        with self._lock:
            self.calls.append(call)

    def _check_signature(self, signed: datetime, expires_in: int) -> None:
        if self.clock() > signed + timedelta(seconds=expires_in):
            raise PresignExpired("AccessDenied", 403)

    def put_if_absent(self, key: str, body: bytes) -> bool:
        self._hook("before_put_if_absent", key=key, body=body)
        self._record("put_if_absent", key)
        with self._lock:
            created = key not in self.objects
            if created:
                self.objects[key] = (bytes(body), self.clock())
        self._hook("after_put_if_absent", key=key, body=body)
        return created

    def get_range(self, key: str, start: int, end: int) -> bytes:
        self._hook("before_get_range", key=key, start=start, end=end)
        self._record("get_range", key, start, end)
        with self._lock:
            if key not in self.objects:
                raise ObjectMissing("NoSuchKey", 404)
            data = self.objects[key][0][start : end + 1]
        self._hook("after_get_range", key=key, start=start, end=end)
        return data

    def list_keys(self, prefix: str, token: str | None) -> tuple[list[ObjectInfo], str | None]:
        self._hook("before_list_keys", prefix=prefix, token=token)
        self._record("list_keys", prefix, token)
        with self._lock:
            keys = sorted(key for key in self.objects if key.startswith(prefix) and (token is None or key > token))
            page = [ObjectInfo(key, len(self.objects[key][0]), self.objects[key][1]) for key in keys[: self.page_size]]
        self._hook("after_list_keys", prefix=prefix, token=token)
        return page, page[-1].key if len(keys) > self.page_size else None

    def delete_keys(self, keys: list[str]) -> None:
        if len(keys) > 1000:
            raise ValueError("S3 deletes at most 1000 keys per request")
        self._hook("before_delete_keys", keys=keys)
        self._record("delete_keys", tuple(keys))
        with self._lock:
            for key in keys:
                self.objects.pop(key, None)
                self.etags.pop(key, None)
        self._hook("after_delete_keys", keys=keys)

    def presigned_create(self, key: str, expires_in: int) -> str:
        signed = self.clock()
        self._hook("before_presigned_create", key=key, expires_in=expires_in)
        self._record("presigned_create", key, expires_in)
        self._check_signature(signed, expires_in)
        with self._lock:
            self._upload_count += 1
            upload_id = f"upload-{self._upload_count}"
            self.uploads[upload_id] = (key, {})
        self._hook("after_presigned_create", key=key, expires_in=expires_in)
        return upload_id

    def _open_upload(self, key: str, upload_id: str) -> dict[int, tuple[str, bytes]]:
        upload = self.uploads.get(upload_id)
        if upload is None or upload[0] != key:
            raise UploadGone("NoSuchUpload", 404)
        return upload[1]

    def upload_part(self, key: str, upload_id: str, number: int, body: bytes) -> str:
        self._hook("before_upload_part", key=key, upload_id=upload_id, number=number, body=body)
        self._record("upload_part", key, upload_id, number)
        etag = '"' + hashlib.md5(body, usedforsecurity=False).hexdigest() + '"'
        with self._lock:
            self._open_upload(key, upload_id)[number] = (etag, bytes(body))
        self._hook("after_upload_part", key=key, upload_id=upload_id, number=number, body=body)
        return etag

    def presigned_complete(self, key: str, upload_id: str, parts: list[tuple[int, str]], expires_in: int) -> None:
        signed = self.clock()
        self._hook("before_presigned_complete", key=key, upload_id=upload_id, parts=parts, expires_in=expires_in)
        self._record("presigned_complete", key, upload_id, expires_in)
        self._check_signature(signed, expires_in)
        with self._lock:
            stored = self._open_upload(key, upload_id)
            if key in self.objects:
                raise ObjectExists("PreconditionFailed", 412)
            if not parts or any(stored.get(number, (None,))[0] != etag for number, etag in parts):
                raise ObjectUnavailable("InvalidPart", 400)
            bodies = [stored[number][1] for number, _ in parts]
            if any(len(body) < MIN_PART_BYTES for body in bodies[:-1]):
                raise ObjectUnavailable("EntityTooSmall", 400)
            self.objects[key] = (b"".join(bodies), self.clock())
            self.etags[key] = multipart_etag(parts)
            del self.uploads[upload_id]
        self._hook("after_presigned_complete", key=key, upload_id=upload_id, parts=parts, expires_in=expires_in)

    def abort(self, key: str, upload_id: str) -> None:
        self._hook("before_abort", key=key, upload_id=upload_id)
        self._record("abort", key, upload_id)
        with self._lock:
            self._open_upload(key, upload_id)
            del self.uploads[upload_id]
        self._hook("after_abort", key=key, upload_id=upload_id)

    def list_uploads(self, prefix: str, marker: str | None) -> tuple[list[tuple[str, str]], str | None]:
        self._hook("before_list_uploads", prefix=prefix, marker=marker)
        self._record("list_uploads", prefix, marker)
        with self._lock:
            uploads = sorted(
                (key, upload_id)
                for upload_id, (key, _) in self.uploads.items()
                if key.startswith(prefix) and (marker is None or f"{key}\0{upload_id}" > marker)
            )
        self._hook("after_list_uploads", prefix=prefix, marker=marker)
        page = uploads[: self.page_size]
        return page, "\0".join(page[-1]) if len(uploads) > self.page_size else None

    def head(self, key: str) -> tuple[int, str]:
        self._hook("before_head", key=key)
        self._record("head", key)
        with self._lock:
            if key not in self.objects:
                raise ObjectMissing("NoSuchKey", 404)
            body = self.objects[key][0]
            etag = self.etags.get(key) or '"' + hashlib.md5(body, usedforsecurity=False).hexdigest() + '"'
        self._hook("after_head", key=key)
        return len(body), etag

    def server_date(self, key: str) -> datetime:
        self._hook("before_server_date", key=key)
        self._record("server_date", key)
        with self._lock:
            if key not in self.objects:
                raise ObjectMissing("NoSuchKey", 404)
        self._hook("after_server_date", key=key)
        return self.clock()


class FakeGcs:
    """One in-memory GCS bucket that implements the GCS ops seam with the GCS rules the adapter relies on.

    State:
      - ``objects``: key -> (body, generation, modified). Every write takes the next value of one
        monotonically increasing generation counter, as GCS generations only grow.
      - ``uploads``: handle -> (key, bytearray) for resumable uploads that are not finished or cancelled.
      - ``calls``: ``(op, key, *details)`` for every op that reached the bucket, in order.

    Rules:
      - ``create_if_absent`` and ``finish`` are ``ifGenerationMatch=0`` writes: an existing key raises
        ``ObjectExists`` (412). ``put_if_absent`` returns False instead.
      - ``compose(sources, destination)`` checks each ``(key, generation)`` source: a missing source raises
        ``ObjectMissing`` (404), another generation raises ``ObjectExists`` (412). An existing destination
        raises ``ObjectExists`` (412). The new object is the sources' bytes in order.
      - ``delete(key, generation)`` raises ``ObjectMissing`` for a missing key and ``ObjectExists`` (412) when
        ``generation`` is given and differs. ``delete_keys`` takes at most 100 keys and ignores missing ones.
      - ``get_range`` and ``list_keys`` behave as on ``FakeS3``.

    Hooks: ``hooks["before_<op>"]`` / ``hooks["after_<op>"]`` are called with the op's arguments as keywords,
    outside the bucket lock, on the calling thread, as on ``FakeS3``.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None, page_size: int = 1000):
        self.clock = clock or FakeClock()
        self.page_size = page_size
        self.objects: dict[str, tuple[bytes, int, datetime]] = {}
        self.uploads: dict[str, tuple[str, bytearray]] = {}
        self.calls: list[tuple] = []
        self.hooks: dict[str, Callable[..., None]] = {}
        self._lock = threading.Lock()
        self._generation = 1000
        self._upload_count = 0

    def _hook(self, name: str, **arguments) -> None:
        hook = self.hooks.get(name)
        if hook is not None:
            hook(**arguments)

    def _record(self, *call) -> None:
        with self._lock:
            self.calls.append(call)

    def _store(self, key: str, body: bytes) -> int:
        """Create ``key`` under the lock held by the caller; returns the new generation."""
        if key in self.objects:
            raise ObjectExists("conditionNotMet", 412)
        self._generation += 1
        self.objects[key] = (bytes(body), self._generation, self.clock())
        return self._generation

    def seed(self, key: str, body: bytes = b"x") -> int:
        """Store an object directly, without a call record or hooks."""
        with self._lock:
            return self._store(key, body)

    def create_if_absent(self, key: str, body: bytes) -> int:
        self._hook("before_create_if_absent", key=key, body=body)
        self._record("create_if_absent", key)
        with self._lock:
            generation = self._store(key, body)
        self._hook("after_create_if_absent", key=key, body=body)
        return generation

    def put_if_absent(self, key: str, body: bytes) -> bool:
        try:
            self.create_if_absent(key, body)
        except ObjectExists:
            return False
        return True

    def begin_upload(self, key: str) -> str:
        self._hook("before_begin_upload", key=key)
        self._record("begin_upload", key)
        with self._lock:
            self._upload_count += 1
            handle = f"upload-{self._upload_count}"
            self.uploads[handle] = (key, bytearray())
        self._hook("after_begin_upload", key=key)
        return handle

    def _upload(self, handle: str) -> tuple[str, bytearray]:
        upload = self.uploads.get(handle)
        if upload is None:
            raise ObjectMissing("notFound", 404)
        return upload

    def write(self, handle: str, data: bytes) -> None:
        self._hook("before_write", handle=handle, data=data)
        with self._lock:
            key, body = self._upload(handle)
            body += data
        self._record("write", key, handle, len(data))
        self._hook("after_write", handle=handle, data=data)

    def finish(self, handle: str) -> int:
        self._hook("before_finish", handle=handle)
        with self._lock:
            key, body = self._upload(handle)
            del self.uploads[handle]
            generation = self._store(key, body)
        self._record("finish", key, handle)
        self._hook("after_finish", handle=handle)
        return generation

    def cancel(self, handle: str) -> None:
        self._hook("before_cancel", handle=handle)
        self._record("cancel", handle)
        with self._lock:
            self.uploads.pop(handle, None)
        self._hook("after_cancel", handle=handle)

    def compose(self, sources: list[tuple[str, int]], destination: str) -> None:
        self._hook("before_compose", sources=sources, destination=destination)
        self._record("compose", destination, tuple(sources))
        with self._lock:
            bodies = []
            for key, generation in sources:
                if key not in self.objects:
                    raise ObjectMissing("notFound", 404)
                if self.objects[key][1] != generation:
                    raise ObjectExists("conditionNotMet", 412)
                bodies.append(self.objects[key][0])
            self._store(destination, b"".join(bodies))
        self._hook("after_compose", sources=sources, destination=destination)

    def exists(self, key: str) -> bool:
        self._hook("before_exists", key=key)
        self._record("exists", key)
        with self._lock:
            found = key in self.objects
        self._hook("after_exists", key=key)
        return found

    def get_range(self, key: str, start: int, end: int) -> bytes:
        self._hook("before_get_range", key=key, start=start, end=end)
        self._record("get_range", key, start, end)
        with self._lock:
            if key not in self.objects:
                raise ObjectMissing("notFound", 404)
            data = self.objects[key][0][start : end + 1]
        self._hook("after_get_range", key=key, start=start, end=end)
        return data

    def list_keys(self, prefix: str, token: str | None) -> tuple[list[ObjectInfo], str | None]:
        self._hook("before_list_keys", prefix=prefix, token=token)
        self._record("list_keys", prefix, token)
        with self._lock:
            keys = sorted(key for key in self.objects if key.startswith(prefix) and (token is None or key > token))
            page = [ObjectInfo(key, len(self.objects[key][0]), self.objects[key][2]) for key in keys[: self.page_size]]
        self._hook("after_list_keys", prefix=prefix, token=token)
        return page, page[-1].key if len(keys) > self.page_size else None

    def delete_keys(self, keys: list[str]) -> None:
        if len(keys) > GCS_BATCH_KEYS:
            raise ValueError("A GCS batch deletes at most 100 keys")
        self._hook("before_delete_keys", keys=keys)
        self._record("delete_keys", tuple(keys))
        with self._lock:
            for key in keys:
                self.objects.pop(key, None)
        self._hook("after_delete_keys", keys=keys)

    def delete(self, key: str, generation: int | None = None) -> None:
        self._hook("before_delete", key=key, generation=generation)
        self._record("delete", key, generation)
        with self._lock:
            if key not in self.objects:
                raise ObjectMissing("notFound", 404)
            if generation is not None and self.objects[key][1] != generation:
                raise ObjectExists("conditionNotMet", 412)
            del self.objects[key]
        self._hook("after_delete", key=key, generation=generation)


def writer_lease(seconds: float = 300) -> WriterLease:
    """A writer lease with ``seconds`` left, received now on the default monotonic clock."""
    now = datetime.now(timezone.utc)
    return WriterLease(until=now + timedelta(seconds=seconds), database_now=now, received_at=time.monotonic())


def object_adapter(ops, provider: str) -> HostedObjectStorage:
    """An object adapter bound to ``ops`` synchronously, with the provider's cleanup grace."""
    grace = config.HOSTED_APPS_STORAGE_S3_GRACE_SECONDS if provider == "aws" else 0
    return HostedObjectStorage(ops, provider=provider, storage_id=read_storage_id(ops), cleanup_grace_seconds=grace)


def filesystem_adapter(path: Path) -> HostedFileStorage:
    return HostedFileStorage(path)


def s3_fake_adapter(path: Path) -> HostedObjectStorage:
    return object_adapter(FakeS3(), "aws")


def gcs_fake_adapter(path: Path) -> HostedObjectStorage:
    return object_adapter(FakeGcs(), "gcp")


CROSS_ADAPTERS = [
    pytest.param(filesystem_adapter, id="filesystem"),
    pytest.param(s3_fake_adapter, id="s3-fake"),
    pytest.param(gcs_fake_adapter, id="gcs-fake"),
]
