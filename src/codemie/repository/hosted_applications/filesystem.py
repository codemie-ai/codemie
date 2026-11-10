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

"""Private POSIX storage with immutable writes and kernel-backed writer quiescence.

The dedicated root must be a persistent volume shared by all backend replicas, with
verified flock semantics. It is never served by general file or website routes.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from uuid import uuid4

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.contract import WriterLease

CHUNK_BYTES = 1024 * 1024
STORAGE_UNAVAILABLE_MESSAGE = "Application storage is unavailable"
CONTENT_UNAVAILABLE_MESSAGE = "Application content is unavailable"
LOCKS_DIRECTORY = ".locks"
# Storage I/O never uses the event loop's default executor, which core platform code shares through
# asyncio.to_thread: a slow or hung volume can hold at most these threads, never the whole process.
STORAGE_THREADS = 8
STORAGE_EXECUTOR = ThreadPoolExecutor(max_workers=STORAGE_THREADS, thread_name_prefix="hosted-storage")


def storage_call(function, *args, **kwargs):
    """Run one blocking storage operation on the storage executor; the caller decides how to await it."""
    return asyncio.get_running_loop().run_in_executor(STORAGE_EXECUTOR, partial(function, *args, **kwargs))


async def joined_io(function, *args, cancel_dispose=None, **kwargs):
    """Cancellation cannot outlive a physical write and release its prefix lock."""
    return await joined_io_on(STORAGE_EXECUTOR, function, *args, cancel_dispose=cancel_dispose, **kwargs)


async def joined_io_on(executor, function, *args, cancel_dispose=None, **kwargs):
    """Like joined_io, on the given executor; a cancelled call disposes its result on that executor too."""
    task = asyncio.get_running_loop().run_in_executor(executor, partial(function, *args, **kwargs))
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except Exception:
            # The physical operation has terminated; retrieve its result below.
            break
    if cancellation is not None:
        # Retrieve any exception, but preserve the caller's cancellation after quiescence.
        if not task.cancelled() and task.exception() is None and cancel_dispose is not None:
            # Released on the storage executor too: closing can block on a network file system.
            executor.submit(cancel_dispose, task.result())
        raise cancellation
    return task.result()


@dataclass(frozen=True)
class StoredObject:
    size: int
    digest: bytes


@dataclass(frozen=True)
class DeletionProof:
    writer_quiesced: bool
    absence_verified: bool


def _parts(key):
    if not isinstance(key, str) or len(key.encode()) > 1024:
        raise HostedApplicationError("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, 503)
    parts = key.split("/")
    if len(parts) > 40 or any(
        not part
        or part in (".", "..")
        or any(c in part for c in "\\:%?#")
        or any(ord(c) < 32 or ord(c) == 127 for c in part)
        for part in parts
    ):
        raise HostedApplicationError("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, 503)
    return parts


def _prefix(value):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+){1,8}", value):
        raise HostedApplicationError("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, 503)
    return _parts(value)


def _directory(base, parts, *, create=False, expected_volume=None):
    descriptor = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if expected_volume is not None:
            locks = marker = None
            try:
                locks = os.open(LOCKS_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                marker = os.open('volume-id', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=locks)
                if os.read(marker, 128) != expected_volume.encode('ascii'):
                    raise ValueError('Private volume changed')
            except (OSError, ValueError) as exc:
                raise HostedApplicationError(
                    'STORAGE_UNAVAILABLE', 'The private storage volume is unavailable', 503
                ) from exc
            finally:
                if marker is not None:
                    os.close(marker)
                if locks is not None:
                    os.close(locks)
        for part in parts:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _inventory(descriptor):
    size = count = 0
    for item in os.scandir(descriptor):
        metadata = item.stat(follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(item.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            try:
                child_size, child_count = _inventory(child)
                size += child_size
                count += child_count
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            size += metadata.st_size
            count += 1
        else:
            raise OSError("Unexpected storage object")
    return size, count


def _delete_contents(descriptor):
    for item in os.scandir(descriptor):
        if item.is_dir(follow_symlinks=False):
            child = os.open(item.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            try:
                _delete_contents(child)
            finally:
                os.close(child)
            os.rmdir(item.name, dir_fd=descriptor)
        else:
            os.unlink(item.name, dir_fd=descriptor)
    os.fsync(descriptor)


def _create_object(parent, name):
    # Exclusive, no-follow creation relative to the verified prefix directory: never overwrite or traverse links.
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    return os.fdopen(descriptor, "wb", buffering=0)


def _write_part(file, digest, part):
    # One worker-thread hop per bounded chunk keeps both the write and its hashing off the event loop.
    pending = memoryview(part)
    while pending:
        # Unbuffered writes may be short; the digest must describe exactly the bytes on disk.
        written = file.write(pending)
        if not written:
            raise OSError("Storage write made no progress")
        pending = pending[written:]
    digest.update(part)


def _close_file(file):
    file.close()


class PrefixWriter:
    def __init__(self, storage, prefix, byte_limit, object_limit, used_bytes, used_objects):
        self.storage, self.prefix = storage, prefix
        self.byte_limit, self.object_limit = byte_limit, object_limit
        self.used_bytes, self.used_objects = used_bytes, used_objects
        self.closed = False

    def renew(self, lease: WriterLease) -> None:
        """A filesystem writer holds a kernel lock, not a lease: nothing to renew."""

    async def _write(self, file, parent, chunks, expected_size, expected_digest, written):
        """Stream ``chunks`` into the new object; ``written[0]`` tracks the bytes charged so far."""
        digest = hashlib.sha256()
        async for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise HostedApplicationError("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, 503)
            if written[0] + len(chunk) > expected_size or self.used_bytes + len(chunk) > self.byte_limit:
                raise HostedApplicationError("UPLOAD_SIZE_MISMATCH", "Artifact bytes exceed the declared size", 413)
            for offset in range(0, len(chunk), CHUNK_BYTES):
                part = chunk[offset : offset + CHUNK_BYTES]
                await joined_io(_write_part, file, digest, part)
                written[0] += len(part)
                self.used_bytes += len(part)
        await joined_io(os.fsync, file.fileno())
        await joined_io(os.fsync, parent)
        if written[0] != expected_size:
            raise HostedApplicationError("UPLOAD_SIZE_MISMATCH", "Artifact bytes do not match the declared size")
        if expected_digest is not None and not hmac.compare_digest(digest.digest(), expected_digest):
            # Never publish bytes that differ from their declaration: the caller removes the object.
            raise HostedApplicationError("UPLOAD_CHECKSUM_MISMATCH", "Artifact checksum does not match its declaration")
        return StoredObject(written[0], digest.digest())

    async def _discard(self, parent, name, size):
        """Remove a failed object and refund its reservation; a failed unlink leaves the object charged."""
        try:
            await joined_io(os.unlink, name, dir_fd=parent)
        except OSError:
            return
        self.used_objects -= 1
        self.used_bytes -= size

    async def put(self, relative_key, chunks, *, expected_size, expected_digest=None):
        parts = _parts(relative_key)
        if (
            self.closed
            or type(expected_size) is not int
            or expected_size < 0
            or self.used_objects >= self.object_limit
            or self.used_bytes + expected_size > self.byte_limit
        ):
            raise HostedApplicationError("STORAGE_LIMIT", "Application storage reservation exceeded", 413)
        parent = None
        file = None
        try:
            parent = await joined_io(
                _directory,
                self.storage.base,
                _prefix(self.prefix) + parts[:-1],
                create=True,
                expected_volume=self.storage.volume_id,
                cancel_dispose=os.close,
            )
            file = await joined_io(_create_object, parent, parts[-1], cancel_dispose=_close_file)
            self.used_objects += 1
            written = [0]
            try:
                return await self._write(file, parent, chunks, expected_size, expected_digest, written)
            except BaseException:
                # Never leave a partial immutable object: a retry of the same key must be able to store it.
                await self._discard(parent, parts[-1], written[0])
                raise
        except FileExistsError as exc:
            raise HostedApplicationError("STORAGE_IMMUTABLE", "Artifact bytes have already been stored", 409) from exc
        except OSError as exc:
            raise HostedApplicationError("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, 503) from exc
        finally:
            # Closing an unsynced file flushes it on a network file system: never on the event loop.
            if file:
                await joined_io(_close_file, file)
            if parent is not None:
                await joined_io(os.close, parent)


class HostedFileStorage:
    provider = "filesystem"
    cleanup_grace_seconds = 0

    def __init__(self, base: Path):
        self.base = base.absolute()
        self.base.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.base.is_symlink():
            raise HostedApplicationError("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, 503)
        self.lock_directory = self.base / LOCKS_DIRECTORY
        self.lock_directory.mkdir(mode=0o700, exist_ok=True)
        directory = _directory(self.base, [LOCKS_DIRECTORY])
        try:
            marker = os.open("volume-id", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        finally:
            os.close(directory)
        try:
            fcntl.flock(marker, fcntl.LOCK_EX)
            value = os.read(marker, 128)
            if not value:
                value = uuid4().hex.encode("ascii")
                os.write(marker, value)
                os.fsync(marker)
            if not re.fullmatch(rb"[a-f0-9]{32}", value):
                raise HostedApplicationError("STORAGE_UNAVAILABLE", "Invalid private volume identity", 503)
            self.volume_id = value.decode("ascii")
        finally:
            os.close(marker)

    @property
    def storage_id(self) -> str:
        return self.volume_id

    @property
    def namespace_suffix(self) -> str:
        return ":volume:" + self.volume_id

    def bind_namespace(self, namespace):
        return namespace + self.namespace_suffix

    def owns_namespace(self, namespace):
        return namespace.endswith(self.namespace_suffix)

    def _lock(self, prefix):
        _prefix(prefix)
        key = hashlib.sha256(prefix.encode()).hexdigest()
        descriptor = _directory(self.base, [LOCKS_DIRECTORY], expected_volume=self.volume_id)
        try:
            lock = os.open(key, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        finally:
            os.close(descriptor)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock
        except BlockingIOError as exc:
            os.close(lock)
            raise HostedApplicationError(
                "WRITER_ACTIVE", "Application cleanup is waiting for an active writer", 409
            ) from exc

    @asynccontextmanager
    async def writer(self, prefix, *, byte_limit, object_limit, lease: WriterLease | None = None):
        # Every metadata/lock syscall runs in a joined worker thread: cancellation cannot leak the descriptor
        # or the exclusive prefix lock, and slow shared volumes never stall the event loop.
        lock = await joined_io(self._lock, prefix, cancel_dispose=os.close)
        writer = None
        descriptor = None
        try:
            descriptor = await joined_io(
                _directory,
                self.base,
                _prefix(prefix),
                create=True,
                expected_volume=self.volume_id,
                cancel_dispose=os.close,
            )
            size, count = await joined_io(_inventory, descriptor)
            if size > byte_limit or count > object_limit:
                raise HostedApplicationError("STORAGE_LIMIT", "Application storage reservation exceeded", 413)
            writer = PrefixWriter(self, prefix, byte_limit, object_limit, size, count)
            yield writer
        except OSError as exc:
            raise HostedApplicationError("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, 503) from exc
        finally:
            if writer:
                writer.closed = True
            if descriptor is not None:
                await joined_io(os.close, descriptor)
            await joined_io(os.close, lock)

    def _open_read(self, key):
        parts = _parts(key)
        parent = _directory(self.base, parts[:-1], expected_volume=self.volume_id)
        try:
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise OSError("Not a storage file")
            return os.fdopen(descriptor, "rb")
        finally:
            os.close(parent)

    async def fingerprint(self, key):
        def inspect():
            parts = _parts(key)
            parent = _directory(self.base, parts[:-1], create=False, expected_volume=self.volume_id)
            descriptor = None
            try:
                descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                info = os.fstat(descriptor)
                return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                os.close(parent)

        return await joined_io(inspect)

    async def read_small(self, key, *, maximum):
        try:
            file = await joined_io(self._open_read, key, cancel_dispose=_close_file)
            try:
                data = await joined_io(file.read, maximum + 1)
                if len(data) > maximum:
                    raise HostedApplicationError("CONTENT_UNAVAILABLE", CONTENT_UNAVAILABLE_MESSAGE, 503)
                return data
            finally:
                await joined_io(_close_file, file)
        except OSError as exc:
            raise HostedApplicationError("CONTENT_UNAVAILABLE", CONTENT_UNAVAILABLE_MESSAGE, 503) from exc

    async def read_range(self, key, start, end):
        try:
            file = await joined_io(self._open_read, key, cancel_dispose=_close_file)
            try:
                await joined_io(file.seek, start)
                remaining = end - start + 1
                while remaining:
                    part = await joined_io(file.read, min(CHUNK_BYTES, remaining))
                    if not part:
                        raise OSError("Incomplete storage object")
                    remaining -= len(part)
                    yield part
            finally:
                await joined_io(_close_file, file)
        except OSError as exc:
            raise HostedApplicationError("CONTENT_UNAVAILABLE", CONTENT_UNAVAILABLE_MESSAGE, 503) from exc

    def _delete_prefix(self, prefix):
        lock = self._lock(prefix)
        parent = descriptor = None
        try:
            parts = _prefix(prefix)
            try:
                parent = _directory(self.base, parts[:-1], expected_volume=self.volume_id)
                descriptor = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                return DeletionProof(True, True)
            _delete_contents(descriptor)
            os.close(descriptor)
            descriptor = None
            os.rmdir(parts[-1], dir_fd=parent)
            os.fsync(parent)
            try:
                probe = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                return DeletionProof(True, True)
            os.close(probe)
            raise OSError("Prefix remains present")
        except OSError as exc:
            raise HostedApplicationError("STORAGE_UNAVAILABLE", "Application cleanup is pending", 503) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if parent is not None:
                os.close(parent)
            os.close(lock)

    async def delete_prefix(self, prefix):
        # The exclusive lock is taken, used and released inside one joined worker-thread operation.
        return await joined_io(self._delete_prefix, prefix)
