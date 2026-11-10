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

"""Provider-neutral contract suite for hosted application storage adapters.

How to add an adapter (Tasks 6 and 10):
  1. Write a factory ``def make_x(tmp_path) -> adapter`` that returns an opened adapter.
  2. Append ``pytest.param(make_x, id="x")`` to ``ADAPTER_FACTORIES``. Nothing else changes.
     Gate real-bucket params with ``pytest.mark.skipif`` on HOSTED_TEST_S3_BUCKET or
     HOSTED_TEST_GCS_BUCKET.
  3. Tests that take ``storage_adapter`` run once per param. They assert behaviour every
     adapter must keep and must not touch adapter internals such as ``base``.

Filesystem-only cases (symlinks, syscalls off the loop, short writes, the executor, on-disk
paths, volume binding) take the non-parametrized ``filesystem_storage`` fixture, which always
builds the filesystem adapter, so they run exactly once and never on other params.

Object adapters (``HostedObjectStorage``) also run in ``object_storage``, which is parametrized by
``OBJECT_ADAPTER_FACTORIES`` (``s3-fake``, ``gcs-fake`` and the real-bucket params). Those cases seed
objects through the adapter's ops seam (``storage.ops.put_if_absent``), so they need no writer. Fault
injection and races use the non-parametrized ``fake_s3`` and ``fake_gcs`` fixtures and the fake hooks.

Writers take a live ``lease()``: the S3 writer signs every Create and Complete from it, the filesystem
and GCS writers ignore it (GCS fences a writer with its fence object instead). Writing cases use the ``written`` prefix fixture, fresh per test, so a real bucket can rerun
them. Object cleanup is resumable: ``delete_prefix`` runs one provider call per step, so a timeout
between steps keeps the work done, and it returns a proof only after a verifying pass finds nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from uuid import uuid4
from sqlalchemy import select

from codemie.configs import config
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.contract import WriterLease
from codemie.repository.hosted_applications.filesystem import HostedFileStorage
from codemie.repository.hosted_applications.object_storage import (
    OBJECT_EXECUTOR,
    OBJECT_THREADS,
    STORAGE_ID_KEY,
    WINDOW_BYTES,
    HostedObjectStorage,
    ObjectUnavailable,
)
from codemie.repository.hosted_applications.s3_backend import BotoS3Ops
from codemie.rest_api.models.hosted_application import (
    HostedApplicationUserState as State,
    HostedApplicationStorageAttempts as Attempt,
)
from codemie.service.hosted_applications.maintenance import MaintenanceWorker
from tests.codemie.service.hosted_applications.storage_fakes import (
    FakeGcs,
    FakeS3,
    filesystem_adapter,
    gcs_fake_adapter,
    object_adapter,
    s3_fake_adapter,
)
from tests.codemie.service.hosted_applications.test_worker import launchable, queued

PREFIX = "attempt/" + "a" * 32


def make_filesystem(tmp_path):
    return filesystem_adapter(tmp_path / "private-hosted-storage")


def open_s3(ops):
    return object_adapter(ops, "aws")


def make_s3_fake(tmp_path):
    return s3_fake_adapter(tmp_path)


def make_s3_real(tmp_path):
    return open_s3(BotoS3Ops(os.environ["HOSTED_TEST_S3_BUCKET"]))


def make_gcs_fake(tmp_path):
    return gcs_fake_adapter(tmp_path)


def make_gcs_real(tmp_path):
    from codemie.repository.hosted_applications.gcs_backend import GcsOps

    return object_adapter(GcsOps(os.environ["HOSTED_TEST_GCS_BUCKET"]), "gcp")


S3_REAL = pytest.mark.skipif(not os.environ.get("HOSTED_TEST_S3_BUCKET"), reason="HOSTED_TEST_S3_BUCKET is not set")
GCS_REAL = pytest.mark.skipif(not os.environ.get("HOSTED_TEST_GCS_BUCKET"), reason="HOSTED_TEST_GCS_BUCKET is not set")

OBJECT_ADAPTER_FACTORIES = [
    pytest.param(make_s3_fake, id="s3-fake"),
    pytest.param(make_s3_real, id="s3-real", marks=S3_REAL),
    pytest.param(make_gcs_fake, id="gcs-fake"),
    pytest.param(make_gcs_real, id="gcs-real", marks=GCS_REAL),
]

ADAPTER_FACTORIES = [
    pytest.param(make_filesystem, id="filesystem"),
    *OBJECT_ADAPTER_FACTORIES,
]


@pytest.fixture(params=ADAPTER_FACTORIES)
def storage_adapter(request, tmp_path):
    return request.param(tmp_path)


@pytest.fixture
def filesystem_storage(tmp_path):
    return make_filesystem(tmp_path)


@pytest.fixture(params=OBJECT_ADAPTER_FACTORIES)
def object_storage(request, tmp_path):
    return request.param(tmp_path)


@pytest.fixture
def fake_s3():
    return FakeS3()


@pytest.fixture
def fake_gcs():
    return FakeGcs()


@pytest.fixture
def seed(object_storage):
    """Store objects through the ops seam under a fresh attempt prefix; delete them afterwards."""
    prefix = "attempt/" + uuid4().hex
    keys = []

    def store(name, data):
        key = prefix + "/" + name
        assert object_storage.ops.put_if_absent(key, data)
        keys.append(key)
        return key

    yield store
    if keys:
        object_storage.ops.delete_keys(keys)


def lease(seconds=30):
    """A writer lease that has ``seconds`` left, received now on the default monotonic clock."""
    now = datetime.now(timezone.utc)
    return WriterLease(until=now + timedelta(seconds=seconds), database_now=now, received_at=time.monotonic())


@pytest.fixture
def written(storage_adapter):
    """A fresh attempt prefix to write under; on object adapters its objects are deleted afterwards."""
    prefix = "attempt/" + uuid4().hex
    yield prefix
    if isinstance(storage_adapter, HostedObjectStorage):
        page, _ = storage_adapter.ops.list_keys(prefix + "/", None)
        if page:
            storage_adapter.ops.delete_keys([info.key for info in page])


async def chunks(*data):
    for chunk in data:
        yield chunk


# ---------------------------------------------------------------------------
# Provider-neutral contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streamed_immutable_bytes_and_ranges(storage_adapter, written):
    storage = storage_adapter
    async with storage.writer(written, byte_limit=40, object_limit=2, lease=lease()) as writer:
        result = await writer.put("original", chunks(b"hello ", b"world"), expected_size=11)
        assert result.size == 11 and result.digest == hashlib.sha256(b"hello world").digest()
        with pytest.raises(HostedApplicationError) as error:
            await writer.put("original", chunks(b"overwritten"), expected_size=11)
        assert error.value.code == "STORAGE_IMMUTABLE"
    assert await storage.read_small(written + "/original", maximum=20) == b"hello world"
    assert b"".join([part async for part in storage.read_range(written + "/original", 6, 10)]) == b"world"


@pytest.mark.asyncio
async def test_zero_byte_put_round_trips(storage_adapter, written):
    storage = storage_adapter
    async with storage.writer(written, byte_limit=10, object_limit=2, lease=lease()) as writer:
        result = await writer.put("assets/empty.txt", chunks(), expected_size=0)
        assert result.size == 0 and result.digest == hashlib.sha256(b"").digest()
    assert await storage.read_small(written + "/assets/empty.txt", maximum=10) == b""


@pytest.mark.asyncio
async def test_size_mismatch_is_refused(storage_adapter, written):
    storage = storage_adapter
    async with storage.writer(written, byte_limit=40, object_limit=4, lease=lease()) as writer:
        for name, data, expected in (("long", (b"123", b"456"), 4), ("short", (b"12",), 4)):
            with pytest.raises(HostedApplicationError) as error:
                await writer.put(name, chunks(*data), expected_size=expected)
            assert error.value.code == "UPLOAD_SIZE_MISMATCH"


@pytest.mark.asyncio
async def test_checksum_mismatch_never_commits_the_object(storage_adapter, written):
    storage = storage_adapter
    async with storage.writer(written, byte_limit=40, object_limit=4, lease=lease()) as writer:
        wrong = hashlib.sha256(b"other").digest()
        with pytest.raises(HostedApplicationError) as error:
            await writer.put("bad", chunks(b"hello ", b"world"), expected_size=11, expected_digest=wrong)
        assert error.value.code == "UPLOAD_CHECKSUM_MISMATCH"
        right = hashlib.sha256(b"hello world").digest()
        result = await writer.put("good", chunks(b"hello world"), expected_size=11, expected_digest=right)
        assert result.digest == right
    with pytest.raises(HostedApplicationError):
        await storage.read_small(written + "/bad", maximum=20)
    assert await storage.read_small(written + "/good", maximum=20) == b"hello world"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "code"),
    [
        ((b"123", b"456"), "UPLOAD_SIZE_MISMATCH"),
        ((b"12",), "UPLOAD_SIZE_MISMATCH"),
        ((b"12", "not bytes"), "STORAGE_UNAVAILABLE"),
    ],
    ids=["oversize-chunk", "short", "non-bytes-chunk"],
)
async def test_a_failed_filesystem_put_leaves_nothing_behind_so_a_retry_succeeds(filesystem_storage, data, code):
    """CR-009: every failure after the object is created removes it and refunds the reservation."""
    async with filesystem_storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
        with pytest.raises(HostedApplicationError) as error:
            await writer.put("same", chunks(*data), expected_size=4)
        assert error.value.code == code
        assert (writer.used_bytes, writer.used_objects) == (0, 0)
        result = await writer.put("same", chunks(b"1234"), expected_size=4)
        assert result.size == 4
        assert (writer.used_bytes, writer.used_objects) == (4, 1)
    assert await filesystem_storage.read_small(PREFIX + "/same", maximum=10) == b"1234"


@pytest.mark.asyncio
async def test_s3_checksum_mismatch_sends_no_complete_and_leaves_no_upload(fake_s3):
    storage = await open_fake(fake_s3)
    async with storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
        with pytest.raises(HostedApplicationError) as error:
            await writer.put("bad", chunks(b"data"), expected_size=4, expected_digest=b"\x00" * 32)
        assert error.value.code == "UPLOAD_CHECKSUM_MISMATCH"
        assert not fake_s3.uploads and not writer.open_uploads
    assert "presigned_complete" not in [call[0] for call in fake_s3.calls]
    assert PREFIX + "/bad" not in fake_s3.objects


@pytest.mark.asyncio
async def test_active_writer_blocks_cleanup(storage_adapter):
    storage = storage_adapter
    async with storage.writer(PREFIX, byte_limit=20, object_limit=2, lease=lease()) as writer:
        await writer.put("original", chunks(b"data"), expected_size=4)
        with pytest.raises(HostedApplicationError) as error:
            await storage.delete_prefix(PREFIX)
        assert error.value.code == "WRITER_ACTIVE"
    proof = await storage.delete_prefix(PREFIX)
    assert proof.writer_quiesced and proof.absence_verified


@pytest.mark.asyncio
async def test_stream_limits_hold_partial_bytes_until_proven_cleanup(storage_adapter):
    storage = storage_adapter
    with pytest.raises(HostedApplicationError):
        async with storage.writer(PREFIX, byte_limit=4, object_limit=2, lease=lease()) as writer:
            await writer.put("original", chunks(b"123", b"456"), expected_size=4)
    proof = await storage.delete_prefix(PREFIX)
    assert proof.absence_verified


@pytest.mark.asyncio
async def test_prefix_limits_include_existing_objects(storage_adapter, written):
    storage = storage_adapter
    async with storage.writer(written, byte_limit=5, object_limit=1, lease=lease()) as writer:
        await writer.put("original", chunks(b"12345"), expected_size=5)
    with pytest.raises(HostedApplicationError) as error:
        async with storage.writer(written, byte_limit=5, object_limit=1, lease=lease()) as writer:
            await writer.put("second", chunks(b"x"), expected_size=1)
    assert error.value.code == "STORAGE_LIMIT"


@pytest.mark.asyncio
async def test_cancelled_reader_releases_descriptor_and_writer_lock(storage_adapter):
    storage = storage_adapter
    async with storage.writer(PREFIX, byte_limit=20, object_limit=2, lease=lease()) as writer:
        await writer.put("original", chunks(b"data"), expected_size=4)
    reader = storage.read_range(PREFIX + "/original", 0, 3)
    assert await reader.__anext__() == b"data"
    await reader.aclose()
    proof = await storage.delete_prefix(PREFIX)
    assert proof.absence_verified


def test_namespace_binding_is_owned_only_by_its_own_storage(storage_adapter, tmp_path):
    storage = storage_adapter
    bound = storage.bind_namespace("org/owner")
    assert bound.endswith(storage.namespace_suffix)
    assert storage.owns_namespace(bound)
    assert storage.storage_id
    other = HostedFileStorage(tmp_path / "other-hosted-storage")
    assert other.storage_id != storage.storage_id
    assert not other.owns_namespace(bound)
    assert not storage.owns_namespace(other.bind_namespace("org/owner"))


# ---------------------------------------------------------------------------
# Object adapters (objects seeded through the ops seam)
# ---------------------------------------------------------------------------


async def read_all(storage, key, start, end):
    return b"".join([part async for part in storage.read_range(key, start, end)])


@pytest.mark.asyncio
async def test_object_range_reads_straddle_a_window(object_storage, seed):
    data = os.urandom(WINDOW_BYTES + 10)
    key = seed("original", data)
    assert await read_all(object_storage, key, 3, WINDOW_BYTES + 6) == data[3 : WINDOW_BYTES + 7]
    assert await read_all(object_storage, key, WINDOW_BYTES - 2, WINDOW_BYTES + 1) == data[-12:-8]
    assert await read_all(object_storage, key, 0, len(data) - 1) == data


@pytest.mark.asyncio
async def test_object_read_small_refuses_more_than_maximum(object_storage, seed):
    key = seed("manifest.json", b"x" * 21)
    assert await object_storage.read_small(key, maximum=21) == b"x" * 21
    with pytest.raises(HostedApplicationError) as error:
        await object_storage.read_small(key, maximum=20)
    assert error.value.code == "CONTENT_UNAVAILABLE" and error.value.status == 503
    assert await object_storage.read_small(seed("empty", b""), maximum=20) == b""


@pytest.mark.asyncio
async def test_object_missing_or_short_objects_are_content_unavailable(object_storage, seed):
    key = seed("original", b"0123456789")
    for read in (
        object_storage.read_small(key + "-missing", maximum=20),
        read_all(object_storage, key + "-missing", 0, 3),
        read_all(object_storage, key, 5, 10),
        object_storage.fingerprint(key + "-missing"),
    ):
        with pytest.raises(HostedApplicationError) as error:
            await read
        assert error.value.code == "CONTENT_UNAVAILABLE" and error.value.status == 503


@pytest.mark.asyncio
async def test_object_escaping_keys_are_refused(object_storage):
    for key in ("../external/secret", "/etc/passwd", PREFIX + "/../../secret", PREFIX + "/original?other"):
        with pytest.raises(HostedApplicationError) as error:
            await object_storage.read_small(key, maximum=20)
        assert error.value.code == "STORAGE_UNAVAILABLE"
        with pytest.raises(HostedApplicationError) as error:
            await read_all(object_storage, key, 0, 3)
        assert error.value.code == "STORAGE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_object_fingerprint_names_the_stored_object(object_storage, seed):
    short, longer = seed("a", b"1"), seed("ab", b"12")
    assert await object_storage.fingerprint(short) == await object_storage.fingerprint(short)
    assert await object_storage.fingerprint(short) != await object_storage.fingerprint(longer)


@pytest.mark.asyncio
async def test_object_bucket_marker_holds_the_storage_id(object_storage):
    storage = object_storage
    assert len(storage.storage_id) == 32 and int(storage.storage_id, 16) >= 0
    assert storage.namespace_suffix == ":bucket:" + storage.storage_id
    assert await storage.read_small(STORAGE_ID_KEY, maximum=64) == storage.storage_id.encode()
    reopened = await HostedObjectStorage.open(storage.ops, provider=storage.provider, cleanup_grace_seconds=30)
    assert reopened.storage_id == storage.storage_id
    assert reopened.owns_namespace(storage.bind_namespace("org/owner"))


# ---------------------------------------------------------------------------
# S3 adapter on FakeS3 only (fault injection and races)
# ---------------------------------------------------------------------------


async def open_fake(fake):
    return await HostedObjectStorage.open(fake, provider="aws", cleanup_grace_seconds=30)


@pytest.mark.asyncio
async def test_s3_adapter_reports_provider_and_grace(fake_s3):
    storage = await open_fake(fake_s3)
    assert storage.provider == "aws" and storage.cleanup_grace_seconds == 30


@pytest.mark.asyncio
async def test_s3_reads_use_bounded_range_windows(fake_s3):
    storage = await open_fake(fake_s3)
    key = PREFIX + "/original"
    fake_s3.put_if_absent(key, os.urandom(2 * WINDOW_BYTES + 5))
    fake_s3.calls.clear()
    await read_all(storage, key, 1, 2 * WINDOW_BYTES + 3)
    ranges = [call[2:] for call in fake_s3.calls if call[0] == "get_range"]
    assert ranges == [
        (1, WINDOW_BYTES),
        (WINDOW_BYTES + 1, 2 * WINDOW_BYTES),
        (2 * WINDOW_BYTES + 1, 2 * WINDOW_BYTES + 3),
    ]


@pytest.mark.asyncio
async def test_s3_read_small_fetches_one_byte_more_than_maximum(fake_s3):
    storage = await open_fake(fake_s3)
    key = PREFIX + "/manifest.json"
    fake_s3.put_if_absent(key, b"x" * 100)
    fake_s3.calls.clear()
    with pytest.raises(HostedApplicationError):
        await storage.read_small(key, maximum=20)
    assert [call for call in fake_s3.calls if call[0] == "get_range"] == [("get_range", key, 0, 20)]


@pytest.mark.asyncio
async def test_s3_ops_failure_is_content_unavailable(fake_s3):
    storage = await open_fake(fake_s3)
    key = PREFIX + "/original"
    fake_s3.put_if_absent(key, b"data")

    def unavailable(**_):
        raise ObjectUnavailable("SlowDown", 503)

    fake_s3.hooks["before_get_range"] = unavailable
    fake_s3.hooks["before_list_keys"] = unavailable
    for read in (storage.read_small(key, maximum=20), read_all(storage, key, 0, 3), storage.fingerprint(key)):
        with pytest.raises(HostedApplicationError) as error:
            await read
        assert error.value.code == "CONTENT_UNAVAILABLE" and error.value.status == 503


@pytest.mark.asyncio
async def test_two_concurrent_opens_on_an_empty_bucket_get_one_storage_id(fake_s3):
    both_checked = threading.Barrier(2, timeout=5)
    fake_s3.hooks["before_put_if_absent"] = lambda **_: both_checked.wait()
    first, second = await asyncio.gather(open_fake(fake_s3), open_fake(fake_s3))
    assert first.storage_id == second.storage_id
    assert fake_s3.objects[STORAGE_ID_KEY][0] == first.storage_id.encode()
    assert [call[0] for call in fake_s3.calls].count("put_if_absent") == 2


@pytest.mark.asyncio
async def test_another_bucket_does_not_own_the_namespace():
    storage, other = await open_fake(FakeS3()), await open_fake(FakeS3())
    assert other.storage_id != storage.storage_id
    assert not other.owns_namespace(storage.bind_namespace("org/owner"))
    assert not storage.owns_namespace(other.bind_namespace("org/owner"))


@pytest.mark.asyncio
async def test_open_refuses_an_invalid_or_unreadable_marker(fake_s3):
    fake_s3.put_if_absent(STORAGE_ID_KEY, b"not-a-storage-id")
    with pytest.raises(HostedApplicationError) as error:
        await open_fake(fake_s3)
    assert error.value.code == "STORAGE_UNAVAILABLE"

    def unavailable(**_):
        raise ObjectUnavailable("AccessDenied", 403)

    empty = FakeS3()
    empty.hooks["before_put_if_absent"] = unavailable
    with pytest.raises(HostedApplicationError) as error:
        await open_fake(empty)
    assert error.value.code == "STORAGE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_object_ops_run_on_the_bounded_object_executor(fake_s3):
    from codemie.hosted_apps_gateway.admission import ContentLimits

    names = []
    fake_s3.hooks["before_put_if_absent"] = lambda **_: names.append(threading.current_thread().name)
    fake_s3.hooks["before_get_range"] = lambda **_: names.append(threading.current_thread().name)
    storage = await open_fake(fake_s3)
    fake_s3.objects[PREFIX + "/original"] = (b"data", fake_s3.clock())
    await storage.read_small(PREFIX + "/original", maximum=20)
    await read_all(storage, PREFIX + "/original", 0, 3)
    assert names and all(name.startswith("hosted-object-storage") for name in names)
    assert OBJECT_EXECUTOR._max_workers == OBJECT_THREADS == ContentLimits().total_concurrency + 16


def test_fake_s3_multipart_follows_s3_rules():
    from codemie.repository.hosted_applications.object_storage import ObjectExists, PresignExpired, UploadGone

    fake = FakeS3()
    key = PREFIX + "/original"
    upload = fake.presigned_create(key, 30)
    etag = fake.upload_part(key, upload, 1, b"")
    fake.presigned_complete(key, upload, [(1, etag)], 30)
    assert fake.objects[key][0] == b"" and not fake.uploads
    again = fake.presigned_create(key, 30)
    with pytest.raises(ObjectExists):
        fake.presigned_complete(key, again, [(1, fake.upload_part(key, again, 1, b"x"))], 30)
    fake.abort(key, again)
    with pytest.raises(UploadGone):
        fake.abort(key, again)
    late = fake.presigned_create(PREFIX + "/late", 30)
    fake.hooks["before_presigned_complete"] = lambda **_: fake.clock.advance(31)
    with pytest.raises(PresignExpired) as error:
        fake.presigned_complete(PREFIX + "/late", late, [(1, fake.upload_part(PREFIX + "/late", late, 1, b"x"))], 30)
    assert error.value.status == 403 and fake.list_uploads(PREFIX, None) == ([(PREFIX + "/late", late)], None)


def operations(fake, *names):
    return [call for call in fake.calls if call[0] in names]


@pytest.mark.asyncio
async def test_s3_every_object_is_multipart_in_8_mib_parts(fake_s3):
    from codemie.repository.hosted_applications.object_storage import PART_BYTES

    storage = await open_fake(fake_s3)
    data = os.urandom(PART_BYTES + 10)
    async with storage.writer(PREFIX, byte_limit=len(data) + 1, object_limit=2, lease=lease()) as writer:
        result = await writer.put("original", chunks(data[:5], data[5:]), expected_size=len(data))
        empty = await writer.put("empty", chunks(), expected_size=0)
    assert PART_BYTES == 8 * 1024 * 1024
    assert result.digest == hashlib.sha256(data).digest() and empty.size == 0
    parts = [(call[1].rsplit("/", 1)[1], call[3]) for call in operations(fake_s3, "upload_part")]
    assert parts == [("original", 1), ("original", 2), ("empty", 1)]
    assert fake_s3.objects[PREFIX + "/original"][0] == data and fake_s3.objects[PREFIX + "/empty"][0] == b""
    assert not fake_s3.uploads


@pytest.mark.asyncio
async def test_s3_second_put_is_immutable_and_keeps_the_object(fake_s3):
    storage = await open_fake(fake_s3)
    async with storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
        await writer.put("original", chunks(b"first"), expected_size=5)
        with pytest.raises(HostedApplicationError) as error:
            await writer.put("original", chunks(b"other"), expected_size=5)
    assert (error.value.code, error.value.status) == ("STORAGE_IMMUTABLE", 409)
    assert fake_s3.objects[PREFIX + "/original"][0] == b"first" and not fake_s3.uploads


@pytest.mark.asyncio
async def test_s3_size_mismatch_never_completes(fake_s3):
    storage = await open_fake(fake_s3)
    async with storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
        for name, data in (("long", (b"123", b"456")), ("short", (b"12",))):
            with pytest.raises(HostedApplicationError) as error:
                await writer.put(name, chunks(*data), expected_size=4)
            assert error.value.code == "UPLOAD_SIZE_MISMATCH"
    assert operations(fake_s3, "presigned_complete") == []
    assert not [key for key in fake_s3.objects if key.startswith(PREFIX)] and not fake_s3.uploads


@pytest.mark.asyncio
async def test_s3_writer_without_a_second_of_lease_sends_no_request(fake_s3):
    storage = await open_fake(fake_s3)
    fake_s3.calls.clear()
    for short in (None, lease(0.9), lease(-5)):
        with pytest.raises(HostedApplicationError) as error:
            async with storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=short) as writer:
                await writer.put("original", chunks(b"data"), expected_size=4)
        assert (error.value.code, error.value.status) == ("STORAGE_LEASE_LOST", 409)
    assert fake_s3.calls == []


@pytest.mark.asyncio
async def test_s3_expired_presign_is_lease_lost_and_leaves_the_prefix_empty(fake_s3):
    storage = await open_fake(fake_s3)
    fake_s3.hooks["before_presigned_complete"] = lambda expires_in, **_: fake_s3.clock.advance(expires_in + 1)
    with pytest.raises(HostedApplicationError) as error:
        async with storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
            await writer.put("original", chunks(b"data"), expected_size=4)
    assert (error.value.code, error.value.status) == ("STORAGE_LEASE_LOST", 409)
    assert fake_s3.list_keys(PREFIX + "/", None) == ([], None) and not fake_s3.uploads


@pytest.mark.asyncio
async def test_s3_aborted_upload_under_a_live_writer_is_lease_lost(fake_s3):
    storage = await open_fake(fake_s3)

    def cleaned(key, upload_id, **_):
        # Cleanup aborted the upload: the writer's later part finds it gone.
        fake_s3.uploads.pop(upload_id, None)

    fake_s3.hooks["before_upload_part"] = cleaned
    with pytest.raises(HostedApplicationError) as error:
        async with storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
            await writer.put("original", chunks(b"data"), expected_size=4)
    assert error.value.code == "STORAGE_LEASE_LOST" and not fake_s3.uploads


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,code,status",
    [
        ("presigned_create", "AccessDenied", 403),
        ("upload_part", "InvalidAccessKeyId", 403),
        ("presigned_complete", "ExpiredToken", 400),
        ("presigned_create", "NoSuchBucket", 404),
        ("upload_part", "NoSuchBucket", 404),
    ],
)
async def test_s3_permission_or_bucket_errors_under_a_live_lease_are_storage_unavailable(
    fake_s3, operation, code, status
):
    storage = await open_fake(fake_s3)

    def refused(**_):
        raise ObjectUnavailable(code, status)

    fake_s3.hooks["before_" + operation] = refused
    with pytest.raises(HostedApplicationError) as error:
        async with storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
            await writer.put("original", chunks(b"data"), expected_size=4)
    assert (error.value.code, error.value.status) == ("STORAGE_UNAVAILABLE", 503)
    assert under(fake_s3) == ([], [])


@pytest.mark.asyncio
async def test_s3_complete_that_commits_then_loses_its_response_is_stored(fake_s3):
    storage = await open_fake(fake_s3)
    payload = b"d" * (9 * 1024 * 1024)

    def response_lost(**_):
        raise ObjectUnavailable("ReadTimeoutError")

    fake_s3.hooks["after_presigned_complete"] = response_lost
    async with storage.writer(PREFIX, byte_limit=len(payload), object_limit=4, lease=lease()) as writer:
        receipt = await writer.put("original", chunks(payload), expected_size=len(payload))
    assert receipt.size == len(payload) and receipt.digest == hashlib.sha256(payload).digest()
    assert fake_s3.objects[PREFIX + "/original"][0] == payload and not fake_s3.uploads
    assert operations(fake_s3, "abort") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("other_object", [False, True])
async def test_s3_complete_that_fails_without_its_object_is_unavailable_and_aborted(fake_s3, other_object):
    storage = await open_fake(fake_s3)

    def failed(key, **_):
        if other_object:
            # Another object holds the key: same size, other parts, so its ETag differs.
            fake_s3.objects[key] = (b"else", fake_s3.clock())
        raise ObjectUnavailable("InternalError", 500)

    fake_s3.hooks["before_presigned_complete"] = failed
    with pytest.raises(HostedApplicationError) as error:
        async with storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
            await writer.put("original", chunks(b"data"), expected_size=4)
    assert (error.value.code, error.value.status) == ("STORAGE_UNAVAILABLE", 503)
    assert fake_s3.objects.get(PREFIX + "/original", (b"else",))[0] == b"else" and not fake_s3.uploads
    assert len(operations(fake_s3, "abort")) == 1


@pytest.mark.asyncio
async def test_s3_failed_writer_exit_leaves_no_open_upload(fake_s3):
    storage = await open_fake(fake_s3)
    failures = []

    def abort_fails_once(**_):
        if not failures:
            failures.append(True)
            raise ObjectUnavailable("SlowDown", 503)

    async def broken_stream():
        yield b"da"
        raise RuntimeError("client went away")

    fake_s3.hooks["before_abort"] = abort_fails_once
    with pytest.raises(RuntimeError):
        async with storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
            await writer.put("original", broken_stream(), expected_size=4)
    assert failures and not fake_s3.uploads
    assert operations(fake_s3, "presigned_complete") == []


@pytest.mark.asyncio
async def test_s3_cancelled_writer_waits_for_its_calls_then_aborts(fake_s3):
    storage = await open_fake(fake_s3)
    entered, release = threading.Event(), threading.Event()

    def blocking_part(**_):
        entered.set()
        assert release.wait(5)

    fake_s3.hooks["before_upload_part"] = blocking_part

    async def writing():
        async with storage.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
            await writer.put("original", chunks(b"data"), expected_size=4)

    task = asyncio.create_task(writing())
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done(), "the writer exited while its part upload was still running"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not fake_s3.uploads and operations(fake_s3, "presigned_complete") == []


def test_s3_adapter_grace_is_the_s3_grace_setting():
    storage = make_s3_fake(None)
    assert storage.cleanup_grace_seconds == config.HOSTED_APPS_STORAGE_S3_GRACE_SECONDS


def under(fake, prefix=PREFIX):
    """The objects and open uploads that remain under ``prefix``."""
    objects = [key for key in fake.objects if key.startswith(prefix + "/")]
    uploads = [key for key, _ in fake.uploads.values() if key.startswith(prefix + "/")]
    return objects, uploads


def open_upload(fake, key, body=b"late"):
    """An open multipart upload with one uploaded part, as a stalled writer leaves it."""
    upload_id = fake.presigned_create(key, 60)
    return upload_id, [(1, fake.upload_part(key, upload_id, 1, body))]


@pytest.mark.asyncio
async def test_s3_cleanup_of_5002_objects_resumes_across_bounded_calls(fake_s3):
    storage = await open_fake(fake_s3)
    for index in range(5002):
        fake_s3.objects[f"{PREFIX}/assets/{index:05}"] = (b"x", fake_s3.clock())
    for index in range(3):
        open_upload(fake_s3, f"{PREFIX}/open-{index}")
    neighbour = "attempt/" + "b" * 32 + "/kept"
    fake_s3.objects[neighbour] = (b"k", fake_s3.clock())
    loop = asyncio.get_running_loop()
    timeouts, steps = [], []

    def expire(**_):
        # The step itself completes; the 25 s timeout fires right after it, between two steps.
        steps.append(True)
        if len(steps) % 2:
            loop.call_soon_threadsafe(lambda: timeouts[-1].reschedule(loop.time()))

    fake_s3.hooks["after_abort"] = expire
    fake_s3.hooks["after_delete_keys"] = expire
    remaining, proof, timed_out = len(under(fake_s3)[0]) + 3, None, 0
    for _ in range(30):
        try:
            async with asyncio.timeout(25) as timeout:
                timeouts.append(timeout)
                proof = await storage.delete_prefix(PREFIX)
            break
        except TimeoutError:
            timed_out += 1
            objects, uploads = under(fake_s3)
            assert len(objects) + len(uploads) <= remaining, "work done before a timeout must stay done"
            remaining = len(objects) + len(uploads)
    assert timed_out >= 4
    assert proof is not None and proof.writer_quiesced and proof.absence_verified
    assert under(fake_s3) == ([], []) and neighbour in fake_s3.objects
    assert all(len(call[1]) <= 1000 for call in operations(fake_s3, "delete_keys"))
    # The proof comes from a verifying pass: uploads, then objects, both listed from scratch and empty.
    assert fake_s3.calls[-2:] == [("list_uploads", PREFIX + "/", None), ("list_keys", PREFIX + "/", None)]


@pytest.mark.asyncio
async def test_s3_cleanup_never_returns_a_proof_before_the_verifying_pass_finds_nothing(fake_s3):
    storage = await open_fake(fake_s3)
    fake_s3.objects[PREFIX + "/original"] = (b"data", fake_s3.clock())
    listings = []

    def late_complete(**_):
        # A Complete lands right after the delete loop listed the prefix empty: only the verifying
        # pass can see it.
        listings.append(True)
        if len(listings) == 2:
            fake_s3.objects[PREFIX + "/late"] = (b"late", fake_s3.clock())

    fake_s3.hooks["after_list_keys"] = late_complete
    proof = await storage.delete_prefix(PREFIX)
    assert len(listings) > 3 and proof.writer_quiesced and proof.absence_verified
    assert under(fake_s3) == ([], [])
    assert (PREFIX + "/late",) in [call[1] for call in operations(fake_s3, "delete_keys")]


@pytest.mark.asyncio
async def test_s3_complete_between_uploads_listing_and_abort_is_deleted_by_the_relist(fake_s3):
    storage = await open_fake(fake_s3)
    key = PREFIX + "/original"
    upload_id, parts = open_upload(fake_s3, key)
    completed = []

    def complete_now(**_):
        if not completed:
            completed.append(True)
            fake_s3.presigned_complete(key, upload_id, parts, 60)

    fake_s3.hooks["after_list_uploads"] = complete_now
    proof = await storage.delete_prefix(PREFIX)
    assert completed and proof.writer_quiesced and proof.absence_verified
    assert under(fake_s3) == ([], [])
    assert (key,) in [call[1] for call in operations(fake_s3, "delete_keys")]


@pytest.mark.asyncio
async def test_s3_complete_after_cleanup_abort_is_lease_lost_and_the_prefix_stays_empty(fake_s3):
    # The stalled writer runs on another pod: its adapter is another instance on the same bucket.
    writing_pod, cleaning_pod = await open_fake(fake_s3), await open_fake(fake_s3)
    entered, release = threading.Event(), threading.Event()

    def stalled_complete(**_):
        entered.set()
        assert release.wait(5)

    fake_s3.hooks["before_presigned_complete"] = stalled_complete

    async def writing():
        async with writing_pod.writer(PREFIX, byte_limit=40, object_limit=4, lease=lease()) as writer:
            await writer.put("original", chunks(b"data"), expected_size=4)

    task = asyncio.create_task(writing())
    await asyncio.to_thread(entered.wait, 5)
    try:
        proof = await cleaning_pod.delete_prefix(PREFIX)
    finally:
        release.set()
    assert proof.writer_quiesced and proof.absence_verified
    with pytest.raises(HostedApplicationError) as error:
        await task
    assert (error.value.code, error.value.status) == ("STORAGE_LEASE_LOST", 409)
    assert under(fake_s3) == ([], [])


@pytest.mark.asyncio
async def test_s3_upload_that_reappears_in_the_final_listing_gives_no_proof(fake_s3):
    storage = await open_fake(fake_s3)
    open_upload(fake_s3, PREFIX + "/stale")
    listings = []

    def reappear(**_):
        listings.append(True)
        # The last listing is the verifying pass: a writer created a new upload after the abort loop.
        if len(listings) == 3:
            open_upload(fake_s3, PREFIX + "/reappeared")

    fake_s3.hooks["before_list_uploads"] = reappear
    proof = await storage.delete_prefix(PREFIX)
    assert not proof.writer_quiesced and not proof.absence_verified
    assert under(fake_s3)[1] == [PREFIX + "/reappeared"]
    proof = await storage.delete_prefix(PREFIX)
    assert proof.writer_quiesced and proof.absence_verified
    assert under(fake_s3) == ([], [])


@pytest.mark.asyncio
async def test_s3_cleanup_ops_failure_is_storage_unavailable_and_keeps_its_progress(fake_s3):
    storage = await open_fake(fake_s3)
    for index in range(3):
        fake_s3.objects[f"{PREFIX}/{index}"] = (b"x", fake_s3.clock())
    failures = []

    def fail_once(**_):
        if not failures:
            failures.append(True)
            raise ObjectUnavailable("SlowDown", 503)

    fake_s3.hooks["before_delete_keys"] = fail_once
    with pytest.raises(HostedApplicationError) as error:
        await storage.delete_prefix(PREFIX)
    assert (error.value.code, error.value.status) == ("STORAGE_UNAVAILABLE", 503)
    listed = len(operations(fake_s3, "list_keys"))
    proof = await storage.delete_prefix(PREFIX)
    assert proof.writer_quiesced and proof.absence_verified and under(fake_s3) == ([], [])
    # The next call resumes with the batch it had listed: it lists objects only to see the prefix
    # empty, then once more in the verifying pass.
    assert len(operations(fake_s3, "list_keys")) == listed + 2


# ---------------------------------------------------------------------------
# S3 backend on boto3 (offline, botocore Stubber)
# ---------------------------------------------------------------------------


@pytest.fixture
def stubbed_s3():
    import boto3
    from botocore.stub import Stubber

    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
    with Stubber(client) as stubber:
        yield BotoS3Ops("hosted-bucket", client=client), stubber
        stubber.assert_no_pending_responses()


def test_boto_put_if_absent_is_a_conditional_create(stubbed_s3):
    ops, stubber = stubbed_s3
    expected = {"Bucket": "hosted-bucket", "Key": STORAGE_ID_KEY, "Body": b"id", "IfNoneMatch": "*"}
    stubber.add_response("put_object", {}, expected)
    stubber.add_client_error("put_object", "PreconditionFailed", http_status_code=412, expected_params=expected)
    stubber.add_client_error("put_object", "ConditionalRequestConflict", http_status_code=409, expected_params=expected)
    stubber.add_client_error("put_object", "AccessDenied", http_status_code=403, expected_params=expected)
    assert ops.put_if_absent(STORAGE_ID_KEY, b"id") is True
    assert ops.put_if_absent(STORAGE_ID_KEY, b"id") is False
    assert ops.put_if_absent(STORAGE_ID_KEY, b"id") is False
    with pytest.raises(ObjectUnavailable) as error:
        ops.put_if_absent(STORAGE_ID_KEY, b"id")
    assert error.value.code == "AccessDenied"


def test_boto_get_range_maps_provider_errors(stubbed_s3):
    import io

    from botocore.response import StreamingBody

    from codemie.repository.hosted_applications.object_storage import ObjectMissing

    ops, stubber = stubbed_s3
    expected = {"Bucket": "hosted-bucket", "Key": "a/b", "Range": "bytes=2-5"}
    stubber.add_response("get_object", {"Body": StreamingBody(io.BytesIO(b"2345"), 4)}, expected)
    stubber.add_client_error("get_object", "NoSuchKey", http_status_code=404, expected_params=expected)
    stubber.add_client_error("get_object", "InvalidRange", http_status_code=416, expected_params=expected)
    stubber.add_client_error("get_object", "SlowDown", http_status_code=503, expected_params=expected)
    assert ops.get_range("a/b", 2, 5) == b"2345"
    with pytest.raises(ObjectMissing):
        ops.get_range("a/b", 2, 5)
    assert ops.get_range("a/b", 2, 5) == b""
    with pytest.raises(ObjectUnavailable) as error:
        ops.get_range("a/b", 2, 5)
    assert error.value.code == "SlowDown" and error.value.status == 503


def test_boto_list_keys_pages_with_continuation_tokens(stubbed_s3):
    from datetime import datetime, timezone

    ops, stubber = stubbed_s3
    modified = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stubber.add_response(
        "list_objects_v2",
        {
            "Contents": [{"Key": "p/a", "Size": 3, "LastModified": modified}],
            "IsTruncated": True,
            "NextContinuationToken": "next",
        },
        {"Bucket": "hosted-bucket", "Prefix": "p/", "MaxKeys": 1000},
    )
    stubber.add_response(
        "list_objects_v2",
        {"IsTruncated": False},
        {"Bucket": "hosted-bucket", "Prefix": "p/", "MaxKeys": 1000, "ContinuationToken": "next"},
    )
    page, token = ops.list_keys("p/", None)
    assert [(info.key, info.size, info.modified) for info in page] == [("p/a", 3, modified)] and token == "next"
    assert ops.list_keys("p/", "next") == ([], None)


def test_boto_delete_keys_is_one_quiet_batch_and_fails_on_any_key_error(stubbed_s3):
    ops, stubber = stubbed_s3
    expected = {"Bucket": "hosted-bucket", "Delete": {"Objects": [{"Key": "p/a"}, {"Key": "p/b"}], "Quiet": True}}
    stubber.add_response("delete_objects", {}, expected)
    stubber.add_response("delete_objects", {"Errors": [{"Key": "p/b", "Code": "AccessDenied"}]}, expected)
    ops.delete_keys(["p/a", "p/b"])
    with pytest.raises(ObjectUnavailable) as error:
        ops.delete_keys(["p/a", "p/b"])
    assert error.value.code == "AccessDenied"
    ops.delete_keys([])
    with pytest.raises(ValueError):
        ops.delete_keys([f"p/{index}" for index in range(1001)])


def test_boto_server_date_reads_the_head_date(stubbed_s3):
    from datetime import datetime, timezone

    ops, stubber = stubbed_s3
    stubber.add_response(
        "head_object",
        {"ResponseMetadata": {"HTTPHeaders": {"date": "Wed, 28 Oct 2026 10:00:00 GMT"}}},
        {"Bucket": "hosted-bucket", "Key": STORAGE_ID_KEY},
    )
    assert ops.server_date(STORAGE_ID_KEY) == datetime(2026, 10, 28, 10, tzinfo=timezone.utc)


def test_boto_list_uploads_pages_with_key_and_upload_id_markers(stubbed_s3):
    ops, stubber = stubbed_s3
    stubber.add_response(
        "list_multipart_uploads",
        {
            "Uploads": [{"Key": "p/a", "UploadId": "u1"}],
            "IsTruncated": True,
            "NextKeyMarker": "p/a",
            "NextUploadIdMarker": "u1",
        },
        {"Bucket": "hosted-bucket", "Prefix": "p/", "MaxUploads": 1000},
    )
    stubber.add_response(
        "list_multipart_uploads",
        {"Uploads": [{"Key": "p/b", "UploadId": "u2"}], "IsTruncated": False},
        {"Bucket": "hosted-bucket", "Prefix": "p/", "MaxUploads": 1000, "KeyMarker": "p/a", "UploadIdMarker": "u1"},
    )
    stubber.add_client_error("list_multipart_uploads", "SlowDown", http_status_code=503)
    page, marker = ops.list_uploads("p/", None)
    assert page == [("p/a", "u1")] and marker is not None
    assert ops.list_uploads("p/", marker) == ([("p/b", "u2")], None)
    with pytest.raises(ObjectUnavailable) as error:
        ops.list_uploads("p/", None)
    assert error.value.code == "SlowDown" and error.value.status == 503


def test_boto_client_pool_matches_the_object_executor():
    from codemie.repository.hosted_applications.s3_backend import s3_client

    assert s3_client(region_name="us-east-1").meta.config.max_pool_connections == OBJECT_THREADS


def test_boto_head_reads_the_size_and_etag(stubbed_s3):
    from codemie.repository.hosted_applications.object_storage import ObjectMissing

    ops, stubber = stubbed_s3
    expected = {"Bucket": "hosted-bucket", "Key": "a/b"}
    stubber.add_response("head_object", {"ContentLength": 4, "ETag": '"abc-1"'}, expected)
    stubber.add_client_error("head_object", "404", http_status_code=404, expected_params=expected)
    stubber.add_client_error("head_object", "SlowDown", http_status_code=503, expected_params=expected)
    assert ops.head("a/b") == (4, '"abc-1"')
    with pytest.raises(ObjectMissing):
        ops.head("a/b")
    with pytest.raises(ObjectUnavailable):
        ops.head("a/b")


def test_boto_ops_sign_for_the_region_the_bucket_is_in(monkeypatch):
    from botocore.stub import Stubber

    from codemie.repository.hosted_applications import s3_backend

    arguments = {"region_name": "us-east-1", "aws_access_key_id": "test", "aws_secret_access_key": "test"}
    hosted_client = s3_backend.s3_client
    # The configured region (us-east-1) is not the bucket's: HeadBucket names the bucket's own region.
    first = hosted_client(**arguments)

    def client(**overrides):
        return hosted_client(**{**arguments, **overrides}) if overrides else first

    monkeypatch.setattr(s3_backend, "s3_client", client)
    stubber = Stubber(first)
    stubber.add_response(
        "head_bucket",
        {"ResponseMetadata": {"HTTPHeaders": {"x-amz-bucket-region": "eu-west-1"}}},
        {"Bucket": "hosted-bucket"},
    )
    with stubber:
        ops = BotoS3Ops("hosted-bucket")
    assert ops.client.meta.region_name == "eu-west-1"
    url = ops.client.generate_presigned_url(
        ClientMethod="create_multipart_upload", Params={"Bucket": "hosted-bucket", "Key": "a/b"}, ExpiresIn=10
    )
    assert "eu-west-1" in url


def test_presigned_requests_use_the_environment_proxy_and_ca_bundle(monkeypatch):
    import boto3

    from codemie.repository.hosted_applications import s3_backend

    sessions = []

    class RecordingSession:
        def __init__(self, **options):
            sessions.append(options)

    monkeypatch.setattr(s3_backend, "URLLib3Session", RecordingSession, raising=False)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    monkeypatch.setenv("AWS_CA_BUNDLE", "/etc/ssl/hosted-ca.pem")
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
    BotoS3Ops("hosted-bucket", client=client)
    assert len(sessions) == 1
    options = sessions[0]
    assert options["proxies"]["https"] == "http://proxy.internal:3128"
    assert options["verify"] == "/etc/ssl/hosted-ca.pem"
    assert options["max_pool_connections"] == OBJECT_THREADS


# ---------------------------------------------------------------------------
# GCS adapter on FakeGcs only (fence, staging, compose and races)
# ---------------------------------------------------------------------------


async def open_gcs(fake):
    return await HostedObjectStorage.open(fake, provider="gcp", cleanup_grace_seconds=0)


def fences(fake, prefix=PREFIX):
    return sorted(key for key in fake.objects if key.startswith(".fences/" + prefix + "/"))


def staged(fake, prefix=PREFIX):
    return sorted(key for key in fake.objects if key.startswith(".staging/" + prefix + "/"))


@pytest.mark.asyncio
async def test_gcs_adapter_reports_provider_and_no_grace(fake_gcs):
    storage = await open_gcs(fake_gcs)
    assert storage.provider == "gcp" and storage.cleanup_grace_seconds == 0


def test_fake_gcs_follows_gcs_rules():
    from codemie.repository.hosted_applications.object_storage import ObjectExists, ObjectMissing

    fake = FakeGcs()
    fence = fake.create_if_absent(".fences/p/t", b"")
    with pytest.raises(ObjectExists):
        fake.create_if_absent(".fences/p/t", b"")
    upload = fake.begin_upload(".staging/p/t/a")
    staging = fake.finish(upload)
    assert staging > fence and fake.objects[".staging/p/t/a"][0] == b""
    with pytest.raises(ObjectExists):
        fake.compose([(".staging/p/t/a", staging), (".fences/p/t", fence + 1)], "p/a")
    with pytest.raises(ObjectMissing):
        fake.compose([(".staging/p/t/a", staging), (".fences/p/other", fence)], "p/a")
    fake.compose([(".staging/p/t/a", staging), (".fences/p/t", fence)], "p/a")
    assert fake.objects["p/a"][0] == b"" and fake.objects["p/a"][1] > staging
    with pytest.raises(ObjectExists):
        fake.compose([(".staging/p/t/a", staging), (".fences/p/t", fence)], "p/a")
    with pytest.raises(ObjectExists):
        fake.delete(".fences/p/t", fence + 1)
    fake.delete(".fences/p/t", fence)
    with pytest.raises(ObjectMissing):
        fake.delete(".fences/p/t")
    with pytest.raises(ValueError):
        fake.delete_keys([f"p/{index}" for index in range(101)])


@pytest.mark.asyncio
async def test_gcs_writer_composes_staging_with_its_own_fence(fake_gcs):
    storage = await open_gcs(fake_gcs)
    async with storage.writer(PREFIX, byte_limit=40, object_limit=2) as writer:
        [fence] = fences(fake_gcs)
        token = fence.rsplit("/", 1)[1]
        assert re.fullmatch(r"[a-f0-9]{32}", token) and fence == f".fences/{PREFIX}/{token}"
        body, fence_generation, _ = fake_gcs.objects[fence]
        assert body == b"" and operations(fake_gcs, "create_if_absent")[-1] == ("create_if_absent", fence)
        result = await writer.put("assets/app.js", chunks(b"hello ", b"world"), expected_size=11)
        staging = f".staging/{PREFIX}/{token}/assets/app.js"
        assert operations(fake_gcs, "begin_upload") == [("begin_upload", staging)]
        [(_, destination, sources)] = operations(fake_gcs, "compose")
        assert destination == PREFIX + "/assets/app.js"
        assert [key for key, _ in sources] == [staging, fence] and sources[1][1] == fence_generation
        assert staged(fake_gcs) == []
    assert result.size == 11 and result.digest == hashlib.sha256(b"hello world").digest()
    assert fake_gcs.objects[PREFIX + "/assets/app.js"][0] == b"hello world"
    assert fences(fake_gcs) == [] and staged(fake_gcs) == [] and not fake_gcs.uploads


@pytest.mark.asyncio
async def test_gcs_writer_ignores_the_lease(fake_gcs):
    storage = await open_gcs(fake_gcs)
    expired = lease(seconds=-60)
    async with storage.writer(PREFIX, byte_limit=40, object_limit=2, lease=expired) as writer:
        writer.renew(expired)
        await writer.put("original", chunks(b"data"), expected_size=4)
    assert fake_gcs.objects[PREFIX + "/original"][0] == b"data"


@pytest.mark.asyncio
async def test_gcs_writer_streams_staging_in_8_mib_writes(fake_gcs):
    from codemie.repository.hosted_applications.object_storage import PART_BYTES

    storage = await open_gcs(fake_gcs)
    data = os.urandom(2 * PART_BYTES + 10)
    pieces = [data[index : index + 1024 * 1024] for index in range(0, len(data), 1024 * 1024)]
    async with storage.writer(PREFIX, byte_limit=len(data) + 1, object_limit=2) as writer:
        await writer.put("original", chunks(*pieces), expected_size=len(data))
        await writer.put("empty", chunks(), expected_size=0)
    writes = [(call[1].rsplit("/", 1)[1], call[3]) for call in operations(fake_gcs, "write")]
    assert writes == [("original", PART_BYTES), ("original", PART_BYTES), ("original", 10)]
    assert fake_gcs.objects[PREFIX + "/original"][0] == data and fake_gcs.objects[PREFIX + "/empty"][0] == b""


@pytest.mark.asyncio
async def test_gcs_size_or_checksum_mismatch_never_composes(fake_gcs):
    storage = await open_gcs(fake_gcs)
    async with storage.writer(PREFIX, byte_limit=40, object_limit=4) as writer:
        for name, data, digest in (
            ("long", (b"123", b"456"), None),
            ("short", (b"12",), None),
            ("bad", (b"1234",), b"\x00" * 32),
        ):
            with pytest.raises(HostedApplicationError) as error:
                await writer.put(name, chunks(*data), expected_size=4, expected_digest=digest)
            assert error.value.code in ("UPLOAD_SIZE_MISMATCH", "UPLOAD_CHECKSUM_MISMATCH")
        assert staged(fake_gcs) == [] and not fake_gcs.uploads
    assert operations(fake_gcs, "compose") == []
    assert not [key for key in fake_gcs.objects if key.startswith(PREFIX)]


def replace_fence(fake, sources):
    fence = sources[-1][0]
    body = fake.objects.pop(fence)[0]
    fake.seed(fence, body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "race,code",
    [
        ("fence deleted (404)", "STORAGE_LEASE_LOST"),
        ("fence replaced (412)", "STORAGE_LEASE_LOST"),
        ("destination exists (412)", "STORAGE_IMMUTABLE"),
        ("destination exists, fence deleted (404)", "STORAGE_IMMUTABLE"),
    ],
)
async def test_gcs_rejected_compose_is_immutable_only_when_the_destination_exists(fake_gcs, race, code):
    storage = await open_gcs(fake_gcs)

    def interleave(sources, destination):
        if "destination exists" in race:
            fake_gcs.seed(destination, b"other")
        if "fence deleted" in race:
            fake_gcs.objects.pop(sources[-1][0])
        if "fence replaced" in race:
            replace_fence(fake_gcs, sources)

    fake_gcs.hooks["before_compose"] = interleave
    with pytest.raises(HostedApplicationError) as error:
        async with storage.writer(PREFIX, byte_limit=40, object_limit=2) as writer:
            await writer.put("original", chunks(b"data"), expected_size=4)
    assert (error.value.code, error.value.status) == (code, 409)
    assert staged(fake_gcs) == [] and fences(fake_gcs) == []
    expected = b"other" if code == "STORAGE_IMMUTABLE" else None
    assert fake_gcs.objects.get(PREFIX + "/original", (None,))[0] == expected


@pytest.mark.asyncio
async def test_gcs_writer_exit_deletes_its_fence_then_leftover_staging(fake_gcs):
    from codemie.repository.hosted_applications.object_storage import ObjectUnavailable as Unavailable

    storage = await open_gcs(fake_gcs)
    failures = []

    def staging_delete_fails_once(key, generation):
        if key.startswith(".staging/") and not failures:
            failures.append(key)
            raise Unavailable("serviceUnavailable", 503)

    fake_gcs.hooks["before_delete"] = staging_delete_fails_once
    async with storage.writer(PREFIX, byte_limit=40, object_limit=2) as writer:
        await writer.put("original", chunks(b"data"), expected_size=4)
        [fence] = fences(fake_gcs)
        assert staged(fake_gcs) == failures
    assert fences(fake_gcs) == [] and staged(fake_gcs) == []
    removals = [call for call in fake_gcs.calls if call[0] in ("delete", "delete_keys")]
    fence_removed = next(index for index, call in enumerate(removals) if call[:2] == ("delete", fence))
    assert any(failures[0] in call[1] for call in removals[fence_removed + 1 :] if call[0] == "delete_keys")


@pytest.mark.asyncio
async def test_gcs_cancelled_writer_waits_for_its_calls_then_removes_fence_and_staging(fake_gcs):
    storage = await open_gcs(fake_gcs)
    entered, release = threading.Event(), threading.Event()

    def blocking_write(handle, data):
        entered.set()
        assert release.wait(5)

    fake_gcs.hooks["before_write"] = blocking_write

    async def writing():
        async with storage.writer(PREFIX, byte_limit=40, object_limit=2) as writer:
            await writer.put("original", chunks(b"data"), expected_size=4)

    task = asyncio.create_task(writing())
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done() and fences(fake_gcs)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fences(fake_gcs) == [] and staged(fake_gcs) == [] and not fake_gcs.uploads
    assert operations(fake_gcs, "compose") == []


@pytest.mark.asyncio
async def test_gcs_cleanup_removes_objects_staging_and_fences_of_every_writer_token(fake_gcs):
    from codemie.repository.hosted_applications.object_storage import GCS_DELETE_BATCH

    storage = await open_gcs(fake_gcs)
    for index in range(250):
        fake_gcs.seed(f"{PREFIX}/assets/{index:03}")
    for token in ("a" * 32, "b" * 32):
        fake_gcs.seed(f".fences/{PREFIX}/{token}", b"")
        fake_gcs.seed(f".staging/{PREFIX}/{token}/original")
    neighbours = [PREFIX + "b/original", ".fences/attempt/" + "c" * 32 + "/" + "d" * 32]
    for key in neighbours:
        fake_gcs.seed(key)
    proof = await storage.delete_prefix(PREFIX)
    assert proof.writer_quiesced and proof.absence_verified
    assert fences(fake_gcs) == [] and staged(fake_gcs) == []
    assert not [key for key in fake_gcs.objects if key.startswith(PREFIX + "/")]
    assert all(key in fake_gcs.objects for key in neighbours)
    assert GCS_DELETE_BATCH == 100
    assert all(len(call[1]) <= GCS_DELETE_BATCH for call in operations(fake_gcs, "delete_keys"))


def gcs_leftovers(fake, prefix=PREFIX):
    return fences(fake, prefix), staged(fake, prefix), [key for key in fake.objects if key.startswith(prefix + "/")]


@pytest.mark.asyncio
async def test_gcs_cleanup_deletes_every_fence_before_any_object_or_staging(fake_gcs):
    storage = await open_gcs(fake_gcs)
    for token in ("a" * 32, "b" * 32):
        fake_gcs.seed(f".fences/{PREFIX}/{token}", b"")
        fake_gcs.seed(f".staging/{PREFIX}/{token}/original")
    for index in range(150):
        fake_gcs.seed(f"{PREFIX}/assets/{index:03}")
    proof = await storage.delete_prefix(PREFIX)
    assert proof.writer_quiesced and proof.absence_verified
    assert gcs_leftovers(fake_gcs) == ([], [], [])
    removed = [call[1] for call in operations(fake_gcs, "delete_keys")]
    is_fence = [all(key.startswith(".fences/") for key in keys) for keys in removed]
    assert is_fence[0] and is_fence == sorted(is_fence, reverse=True), "all fence deletes come first"


@pytest.mark.asyncio
async def test_gcs_compose_before_the_fence_delete_is_removed_by_the_relist(fake_gcs):
    storage = await open_gcs(fake_gcs)
    fence_key = f".fences/{PREFIX}/{'a' * 32}"
    staging_key = f".staging/{PREFIX}/{'a' * 32}/original"
    fence = fake_gcs.seed(fence_key, b"")
    staging = fake_gcs.seed(staging_key)
    composed = []

    def compose_now(**_):
        if not composed:
            composed.append(True)
            fake_gcs.compose([(staging_key, staging), (fence_key, fence)], PREFIX + "/late")

    fake_gcs.hooks["before_delete_keys"] = compose_now
    proof = await storage.delete_prefix(PREFIX)
    assert composed and proof.writer_quiesced and proof.absence_verified
    assert gcs_leftovers(fake_gcs) == ([], [], [])
    assert any(PREFIX + "/late" in call[1] for call in operations(fake_gcs, "delete_keys"))


@pytest.mark.asyncio
async def test_gcs_compose_after_the_fence_delete_is_lease_lost_and_the_prefix_stays_empty(fake_gcs):
    # The stalled writer runs on another pod: its adapter is another instance on the same bucket.
    writing_pod, cleaning_pod = await open_gcs(fake_gcs), await open_gcs(fake_gcs)
    entered, release = threading.Event(), threading.Event()

    def stalled_compose(**_):
        entered.set()
        assert release.wait(5)

    fake_gcs.hooks["before_compose"] = stalled_compose

    async def writing():
        async with writing_pod.writer(PREFIX, byte_limit=40, object_limit=4) as writer:
            await writer.put("original", chunks(b"data"), expected_size=4)

    task = asyncio.create_task(writing())
    await asyncio.to_thread(entered.wait, 5)
    try:
        proof = await cleaning_pod.delete_prefix(PREFIX)
    finally:
        release.set()
    assert proof.writer_quiesced and proof.absence_verified
    with pytest.raises(HostedApplicationError) as error:
        await task
    assert (error.value.code, error.value.status) == ("STORAGE_LEASE_LOST", 409)
    assert gcs_leftovers(fake_gcs) == ([], [], [])


@pytest.mark.asyncio
async def test_gcs_fence_that_reappears_in_the_final_listing_gives_no_proof(fake_gcs):
    storage = await open_gcs(fake_gcs)
    fake_gcs.seed(f".fences/{PREFIX}/{'a' * 32}", b"")
    fence_listings = []

    def reappear(prefix, token):
        if prefix.startswith(".fences/"):
            fence_listings.append(True)
            # The third fence listing is the verifying pass: a writer opened a new fence after the loop.
            if len(fence_listings) == 3:
                fake_gcs.seed(f".fences/{PREFIX}/{'b' * 32}", b"")

    fake_gcs.hooks["before_list_keys"] = reappear
    proof = await storage.delete_prefix(PREFIX)
    assert len(fence_listings) == 3
    assert not proof.writer_quiesced and not proof.absence_verified
    assert fences(fake_gcs) == [f".fences/{PREFIX}/{'b' * 32}"]
    # The next call starts at the fences drain, removes the fence and proves absence.
    proof = await storage.delete_prefix(PREFIX)
    assert proof.writer_quiesced and proof.absence_verified
    assert gcs_leftovers(fake_gcs) == ([], [], [])


@pytest.mark.asyncio
async def test_requeued_writer_opens_own_fence_after_killed_pod(fake_gcs):
    old_fence = f".fences/{PREFIX}/{'a' * 32}"
    fake_gcs.seed(old_fence, b"")
    storage = await open_gcs(fake_gcs)
    async with storage.writer(PREFIX, byte_limit=40, object_limit=2) as writer:
        [new_fence] = [key for key in fences(fake_gcs) if key != old_fence]
        assert re.fullmatch(rf"\.fences/{PREFIX}/[a-f0-9]{{32}}", new_fence)
        await writer.put("original", chunks(b"data"), expected_size=4)
    assert fake_gcs.objects[PREFIX + "/original"][0] == b"data"
    assert fences(fake_gcs) == [old_fence], "only the killed pod's fence is left, for cleanup"


@pytest.mark.asyncio
async def test_gcs_cleanup_of_5002_objects_resumes_across_bounded_calls(fake_gcs):
    storage = await open_gcs(fake_gcs)
    for index in range(5002):
        fake_gcs.seed(f"{PREFIX}/assets/{index:05}")
    for token in ("a" * 32, "b" * 32):
        fake_gcs.seed(f".fences/{PREFIX}/{token}", b"")
        fake_gcs.seed(f".staging/{PREFIX}/{token}/original")
    neighbour = "attempt/" + "b" * 32 + "/kept"
    fake_gcs.seed(neighbour)
    loop = asyncio.get_running_loop()
    timeouts, steps = [], []

    def expire(**_):
        # The step itself completes; the 25 s timeout fires right after it, between two steps.
        steps.append(True)
        if len(steps) % 2:
            loop.call_soon_threadsafe(lambda: timeouts[-1].reschedule(loop.time()))

    fake_gcs.hooks["after_delete_keys"] = expire
    remaining, proof, timed_out = sum(map(len, gcs_leftovers(fake_gcs))), None, 0
    for _ in range(200):
        try:
            async with asyncio.timeout(25) as timeout:
                timeouts.append(timeout)
                proof = await storage.delete_prefix(PREFIX)
            break
        except TimeoutError:
            timed_out += 1
            now = sum(map(len, gcs_leftovers(fake_gcs)))
            assert now <= remaining, "work done before a timeout must stay done"
            remaining = now
    assert timed_out >= 20
    assert proof is not None and proof.writer_quiesced and proof.absence_verified
    assert gcs_leftovers(fake_gcs) == ([], [], []) and neighbour in fake_gcs.objects
    assert all(len(call[1]) <= 100 for call in operations(fake_gcs, "delete_keys"))
    # The proof comes from a verifying pass: fences, staging, then objects, all listed from scratch and empty.
    assert fake_gcs.calls[-3:] == [
        ("list_keys", f".fences/{PREFIX}/", None),
        ("list_keys", f".staging/{PREFIX}/", None),
        ("list_keys", PREFIX + "/", None),
    ]


# ---------------------------------------------------------------------------
# GCS backend on google-cloud-storage (offline, canned HTTP responses)
# ---------------------------------------------------------------------------


class CannedHttp:
    """A requests transport adapter that records each request and answers from a list of canned responses."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def send(self, request, **kwargs):
        import io

        from requests.adapters import HTTPAdapter
        from urllib3 import HTTPResponse

        self.requests.append(request)
        status, body, headers = (*self.responses.pop(0), {})[:3]
        content = json.dumps(body).encode() if isinstance(body, dict) else body
        raw = HTTPResponse(
            body=io.BytesIO(content),
            headers={"content-type": "application/json", **headers},
            status=status,
            preload_content=False,
            decode_content=False,
        )
        return HTTPAdapter().build_response(request, raw)

    def close(self):
        pass


def canned_gcs(*responses):
    from google.auth.credentials import AnonymousCredentials

    from codemie.repository.hosted_applications.gcs_backend import GcsOps, gcs_client

    client = gcs_client(project="test", credentials=AnonymousCredentials())
    http = CannedHttp(*responses)
    client._http.mount("https://", http)
    return GcsOps("hosted-bucket", client=client), http


def test_gcs_client_pool_matches_the_object_executor():
    from google.auth.credentials import AnonymousCredentials

    from codemie.repository.hosted_applications.gcs_backend import gcs_client

    client = gcs_client(project="test", credentials=AnonymousCredentials())
    assert client._http.get_adapter("https://storage.googleapis.com")._pool_maxsize == OBJECT_THREADS


def test_gcs_create_if_absent_is_a_generation_zero_create():
    from codemie.repository.hosted_applications.object_storage import ObjectExists

    ops, http = canned_gcs((200, {"name": STORAGE_ID_KEY, "generation": "17"}), (412, {"error": {"code": 412}}))
    assert ops.create_if_absent(STORAGE_ID_KEY, b"id") == 17
    assert "ifGenerationMatch=0" in http.requests[0].url and "/b/hosted-bucket/o" in http.requests[0].url
    with pytest.raises(ObjectExists):
        ops.create_if_absent(STORAGE_ID_KEY, b"id")


def test_gcs_put_if_absent_is_false_when_the_key_exists():
    ops, _ = canned_gcs((412, {"error": {"code": 412}}))
    assert ops.put_if_absent(STORAGE_ID_KEY, b"id") is False


def test_gcs_compose_requires_both_source_generations_and_an_absent_destination():
    from codemie.repository.hosted_applications.object_storage import ObjectExists, ObjectMissing

    ops, http = canned_gcs(
        (200, {"name": "p/a", "generation": "9"}),
        (412, {"error": {"code": 412}}),
        (404, {"error": {"code": 404}}),
    )
    ops.compose([(".staging/p/t/a", 5), (".fences/p/t", 3)], "p/a")
    request = http.requests[0]
    assert "/o/p%2Fa/compose" in request.url and "ifGenerationMatch=0" in request.url
    sources = json.loads(request.body)["sourceObjects"]
    assert [(item["name"], item["objectPreconditions"]["ifGenerationMatch"]) for item in sources] == [
        (".staging/p/t/a", 5),
        (".fences/p/t", 3),
    ]
    with pytest.raises(ObjectExists):
        ops.compose([(".staging/p/t/a", 5), (".fences/p/t", 3)], "p/a")
    with pytest.raises(ObjectMissing):
        ops.compose([(".staging/p/t/a", 5), (".fences/p/t", 3)], "p/a")


def test_gcs_get_range_is_inclusive_and_empty_past_the_end():
    from codemie.repository.hosted_applications.object_storage import ObjectMissing

    ops, http = canned_gcs((206, b"2345", {"content-type": "application/octet-stream"}), (416, b""), (404, b""))
    assert ops.get_range("a/b", 2, 5) == b"2345"
    assert http.requests[0].headers["range"] == "bytes=2-5"
    assert ops.get_range("a/b", 9, 12) == b""
    with pytest.raises(ObjectMissing):
        ops.get_range("a/b", 0, 3)


def test_gcs_list_keys_pages_with_page_tokens():
    ops, http = canned_gcs(
        (200, {"items": [{"name": "p/a", "size": "3", "updated": "2026-01-01T00:00:00.000Z"}], "nextPageToken": "n"}),
        (200, {}),
    )
    page, token = ops.list_keys("p/", None)
    assert [(info.key, info.size, info.modified.year) for info in page] == [("p/a", 3, 2026)] and token == "n"
    assert ops.list_keys("p/", "n") == ([], None)
    assert "pageToken=n" in http.requests[1].url and "prefix=p%2F" in http.requests[1].url


def test_gcs_delete_is_conditional_on_the_generation():
    from codemie.repository.hosted_applications.object_storage import ObjectMissing

    ops, http = canned_gcs((204, b""), (404, {"error": {"code": 404}}))
    ops.delete(".fences/p/t", 3)
    assert "ifGenerationMatch=3" in http.requests[0].url and http.requests[0].method == "DELETE"
    with pytest.raises(ObjectMissing):
        ops.delete(".fences/p/t")


def test_gcs_resumable_upload_starts_with_generation_zero_and_sends_8_mib_chunks():
    from codemie.repository.hosted_applications.object_storage import PART_BYTES

    import base64

    import google_crc32c

    session = "https://storage.googleapis.com/upload/storage/v1/b/hosted-bucket/o?upload_id=u1"
    crc32c = base64.b64encode(google_crc32c.Checksum(b"x" * PART_BYTES).digest()).decode()
    stored = {"name": "s/a", "generation": "21", "size": str(PART_BYTES), "crc32c": crc32c}
    ops, http = canned_gcs(
        (200, b"", {"location": session}),
        (308, b"", {"range": f"bytes=0-{PART_BYTES - 1}"}),
        (200, stored),
        (200, stored),
    )
    handle = ops.begin_upload("s/a")
    ops.write(handle, b"x" * PART_BYTES)
    assert ops.finish(handle) == 21
    assert "uploadType=resumable" in http.requests[0].url and "ifGenerationMatch=0" in http.requests[0].url
    assert len(http.requests[1].body) == PART_BYTES
    assert http.requests[1].headers["content-range"] == f"bytes 0-{PART_BYTES - 1}/*"


def batch_response(*statuses):
    """A canned GCS JSON API batch answer: one ``multipart/mixed`` part per sub-response status."""
    parts = []
    for index, status in enumerate(statuses, 1):
        payload = "" if status == 204 else json.dumps({"error": {"code": status}})
        parts.append(
            f"--batch_boundary\r\nContent-Type: application/http\r\nContent-ID: <response-{index}>\r\n\r\n"
            f"HTTP/1.1 {status} Status\r\nContent-Type: application/json\r\n\r\n{payload}\r\n"
        )
    body = ("".join(parts) + "--batch_boundary--\r\n").encode()
    return 200, body, {"content-type": "multipart/mixed; boundary=batch_boundary"}


def test_gcs_batch_delete_is_one_request_that_tolerates_missing_keys():
    ops, http = canned_gcs(batch_response(204, 404), batch_response(204, 403))
    ops.delete_keys(["p/a", "p/b"])
    assert len(http.requests) == 1 and http.requests[0].url.endswith("/batch/storage/v1")
    body = http.requests[0].body
    body = body.decode() if isinstance(body, bytes) else body
    assert body.count("DELETE ") == 2 and "p%2Fa" in body and "p%2Fb" in body
    with pytest.raises(ObjectUnavailable) as error:
        ops.delete_keys(["p/a", "p/b"])
    assert (error.value.code, error.value.status) == ("batchDeleteFailed", 403)
    assert len(http.requests) == 2


def test_gcs_library_keeps_the_private_attributes_the_backend_reads():
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import storage

    client = storage.Client(project="test", credentials=AnonymousCredentials())
    assert hasattr(client, "_bucket_metadata_cache") and hasattr(client, "_http")
    assert hasattr(client.batch(raise_exception=False), "_responses")


def test_gcs_client_without_a_private_attribute_is_storage_unavailable(monkeypatch):
    import requests

    from codemie.repository.hosted_applications import gcs_backend

    class ChangedClient:
        """A library release that renamed the bucket metadata cache."""

        def __init__(self, **_):
            self._http = requests.Session()

    monkeypatch.setattr(gcs_backend.storage, "Client", ChangedClient)
    with pytest.raises(ObjectUnavailable) as error:
        gcs_backend.gcs_client()
    assert error.value.code == "libraryChanged"


def test_gcs_batch_without_its_responses_is_storage_unavailable(monkeypatch):
    ops, _ = canned_gcs()

    class ChangedBatch:
        """A library release that renamed the batch's sub-responses."""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ops.client, "batch", lambda raise_exception=True: ChangedBatch())
    monkeypatch.setattr(ops.bucket, "delete_blobs", lambda keys: None)
    with pytest.raises(ObjectUnavailable) as error:
        ops.delete_keys(["p/a"])
    assert error.value.code == "libraryChanged"


# ---------------------------------------------------------------------------
# Filesystem adapter only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filesystem_delete_prefix_removes_the_directory(filesystem_storage):
    storage = filesystem_storage
    async with storage.writer(PREFIX, byte_limit=20, object_limit=2) as writer:
        await writer.put("original", chunks(b"data"), expected_size=4)
    proof = await storage.delete_prefix(PREFIX)
    assert proof.writer_quiesced and proof.absence_verified
    assert not (storage.base / PREFIX).exists()


@pytest.mark.asyncio
async def test_filesystem_partial_bytes_are_removed_when_the_put_fails(filesystem_storage):
    """CR-009: a failed put never leaves a partial immutable object for a retry of the same key to trip on."""
    storage = filesystem_storage
    with pytest.raises(HostedApplicationError):
        async with storage.writer(PREFIX, byte_limit=4, object_limit=2) as writer:
            await writer.put("original", chunks(b"123", b"456"), expected_size=4)
    assert not (storage.base / PREFIX / "original").exists()
    proof = await storage.delete_prefix(PREFIX)
    assert proof.absence_verified


@pytest.mark.asyncio
async def test_symlinks_and_escaping_keys_never_read_external_data(filesystem_storage, tmp_path):
    storage = filesystem_storage
    storage.base.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret").write_bytes(b"secret")
    (storage.base / "attempt").symlink_to(external, target_is_directory=True)
    with pytest.raises(HostedApplicationError):
        async with storage.writer(PREFIX, byte_limit=20, object_limit=2):
            pass
    for key in ("../external/secret", "/etc/passwd", PREFIX + "/../../secret", PREFIX + "/original?other"):
        with pytest.raises(HostedApplicationError):
            await storage.read_small(key, maximum=20)
    assert (external / "secret").read_bytes() == b"secret"


@pytest.mark.asyncio
async def test_repeated_cancellation_keeps_physical_writer_locked(filesystem_storage):
    storage = filesystem_storage
    import threading
    from contextlib import suppress
    from codemie.repository.hosted_applications.filesystem import joined_io

    entered = asyncio.Event()
    finish = threading.Event()
    loop = asyncio.get_running_loop()

    def physical_write():
        loop.call_soon_threadsafe(entered.set)
        assert finish.wait(5)
        (storage.base / PREFIX / "original").write_bytes(b"finished")

    async def writing():
        async with storage.writer(PREFIX, byte_limit=20, object_limit=2):
            await joined_io(physical_write)

    task = asyncio.create_task(writing())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done(), "physical writer outlived its lock after repeated cancellation"
        with pytest.raises(HostedApplicationError) as error:
            await storage.delete_prefix(PREFIX)
        assert error.value.code == "WRITER_ACTIVE"
    finally:
        finish.set()
        with suppress(asyncio.CancelledError):
            await task
    proof = await storage.delete_prefix(PREFIX)
    assert proof.writer_quiesced and proof.absence_verified


@pytest.mark.asyncio
async def test_filesystem_metadata_and_lock_syscalls_never_run_on_the_event_loop(filesystem_storage, monkeypatch):
    storage = filesystem_storage
    import os
    import threading

    from codemie.repository.hosted_applications import filesystem

    loop_thread = threading.get_ident()
    on_loop = []

    def observed(name, function):
        def wrapper(*args, **kwargs):
            if threading.get_ident() == loop_thread:
                on_loop.append(name)
            return function(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(filesystem, "_directory", observed("directory", filesystem._directory))
    monkeypatch.setattr(filesystem.fcntl, "flock", observed("flock", filesystem.fcntl.flock))
    monkeypatch.setattr(filesystem, "_close_file", observed("close_file", filesystem._close_file))
    for name in ("rmdir", "fsync", "fstat", "scandir", "close"):
        monkeypatch.setattr(os, name, observed(name, getattr(os, name)))
    async with storage.writer(PREFIX, byte_limit=40, object_limit=2) as writer:
        await writer.put("original", chunks(b"hello ", b"world"), expected_size=11)
    assert await storage.read_small(PREFIX + "/original", maximum=20) == b"hello world"
    assert b"".join([part async for part in storage.read_range(PREFIX + "/original", 0, 4)]) == b"hello"
    assert await storage.fingerprint(PREFIX + "/original")
    proof = await storage.delete_prefix(PREFIX)
    assert proof.writer_quiesced and proof.absence_verified
    assert on_loop == [], f"Blocking storage syscalls ran on the event loop: {sorted(set(on_loop))}"


def test_short_unbuffered_writes_complete_before_the_digest_is_extended():
    import hashlib

    from codemie.repository.hosted_applications.filesystem import _write_part

    class ShortWrites:
        def __init__(self, step):
            self.data, self.step = bytearray(), step

        def write(self, view):
            chunk = bytes(view[: self.step])
            self.data += chunk
            return len(chunk)

    target, digest = ShortWrites(3), hashlib.sha256()
    _write_part(target, digest, b"0123456789")
    assert bytes(target.data) == b"0123456789" and digest.digest() == hashlib.sha256(b"0123456789").digest()
    with pytest.raises(OSError):
        _write_part(ShortWrites(0), hashlib.sha256(), b"stalled")


@pytest.mark.asyncio
async def test_storage_runs_on_its_own_bounded_executor_not_the_shared_default(filesystem_storage):
    import threading

    from codemie.repository.hosted_applications import filesystem

    names = []
    await filesystem.joined_io(lambda: names.append(threading.current_thread().name))
    assert names[0].startswith("hosted-storage")
    assert filesystem.STORAGE_EXECUTOR._max_workers == filesystem.STORAGE_THREADS == 8

    def default_executor_name():
        return threading.current_thread().name

    assert not (await asyncio.to_thread(default_executor_name)).startswith("hosted-storage")


@pytest.mark.asyncio
async def test_other_volume_cannot_write_source_planned_by_original_volume(processing, identity, tmp_path):
    from codemie.rest_api.models.hosted_application_requests import AppCreate, VersionCreate
    from codemie.service.hosted_applications.catalog import HostedApplicationCatalog
    from codemie.core.hosted_applications.errors import HostedApplicationError

    service, repo, directory, _, _ = processing
    original = b'<html>Bound upload</html>'
    app = await service.create_app(
        identity(), AppCreate(name='Volume binding', description='Pending upload', project='demo'), str(uuid4())
    )
    staged = await service.begin_version(
        identity(),
        app['id'],
        VersionCreate(filename='index.html', artifact_kind='html', byte_size=len(original)),
        app['etag'],
        str(uuid4()),
    )
    other = HostedApplicationCatalog(repo, directory)
    other.storage = HostedFileStorage(tmp_path / 'wrong-writing-volume')

    async def chunks():
        yield original

    with pytest.raises(HostedApplicationError):
        await other.transfer(identity(), app['id'], staged['version_id'], 1, staged['upload_etag'], chunks())
    assert not list(other.storage.base.rglob('original'))


@pytest.mark.asyncio
async def test_other_volume_never_advertises_old_accepted_version_as_launchable(processing, identity, tmp_path):
    from codemie.service.hosted_applications.catalog import HostedApplicationCatalog

    service, repo, directory, worker, _ = processing
    app, _ = await queued(service, identity())
    assert await worker.run_one()
    other = HostedApplicationCatalog(repo, directory)
    other.storage = HostedFileStorage(tmp_path / 'wrong-serving-volume')
    assert not await launchable(
        other, identity(), app['id']
    ), 'Foreign-volume attestations do not establish local storage availability'


@pytest.mark.asyncio
async def test_live_adapter_refuses_replaced_root_absence_proof(processing, identity, tmp_path):
    service, repo, _, worker, _ = processing
    app, _ = await queued(service, identity())
    assert await worker.run_one()
    current = await service.get_app(identity(), app['id'])
    await service.delete(identity(), app['id'], current['etag'], str(uuid4()))
    original = service.storage.base
    displaced = tmp_path / 'detached-original-volume'
    original.rename(displaced)
    replacement = HostedFileStorage(original)
    assert replacement.volume_id != service.storage.volume_id
    # Root rename is the temporary-directory analogue of remount/path replacement during uptime.
    async with repo.sessions() as session:
        state = await session.get(State, 'owner')
        before = state.stored_bytes + state.reserved_bytes
    cleaned = await MaintenanceWorker(repo, service.storage).cleanup_one()
    async with repo.sessions() as session:
        state = await session.get(State, 'owner')
        deleted = (await session.execute(select(Attempt).where(Attempt.state == 'deleted'))).scalars().all()
        if not cleaned:
            assert state.stored_bytes + state.reserved_bytes == before
            assert list(displaced.rglob('original'))
        else:
            assert deleted
            for attempt in deleted:
                assert not (
                    displaced / attempt.prefix
                ).exists(), 'Reclaimed attempt bytes remain on actual original root'
