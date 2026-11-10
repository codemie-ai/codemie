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

"""The GCS ops seam of hosted object storage on google-cloud-storage.

The client uses the default credentials, as ``GCPFileRepository`` does, and binds the one dedicated bucket
with ``client.bucket(name)``. It never looks up or creates buckets, never touches the per-owner buckets and
never signs URLs, so the service account needs only the bucket object role (no ``signBlob``).
Every call is synchronous and runs on ``OBJECT_EXECUTOR``; provider failures become typed ops errors:
412 is ``ObjectExists``, 404 is ``ObjectMissing``, anything else is ``ObjectUnavailable``.

Three private attributes of google-cloud-storage have no public equivalent: the client's bucket metadata
cache and HTTP session, and a batch's sub-responses. Every read of them goes through ``_library_attribute``,
so a library release that renames one fails as ``ObjectUnavailable("libraryChanged")``, never as an untyped
``AttributeError``; the dependency is pinned to one minor release.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, cast

import requests
from google.api_core import exceptions as api_exceptions
from google.auth import exceptions as auth_exceptions
from google.cloud import storage
from google.cloud.storage import exceptions as storage_exceptions
from google.cloud.storage.fileio import BlobWriter
from requests.adapters import HTTPAdapter

from codemie.configs import logger
from codemie.repository.hosted_applications.object_storage import (
    GCS_DELETE_BATCH,
    OBJECT_THREADS,
    PART_BYTES,
    ObjectExists,
    ObjectInfo,
    ObjectMissing,
    ObjectOpsError,
    ObjectUnavailable,
)

# Each resumable upload request carries one chunk of this size; the library default is 40 MiB per writer.
UPLOAD_CHUNK_BYTES = PART_BYTES
LIST_PAGE_KEYS = 1000
# Hosts a client talks to: the storage API, plus OAuth token endpoints.
POOL_HOSTS = 4
PROVIDER_ERRORS = (
    api_exceptions.GoogleAPIError,
    auth_exceptions.GoogleAuthError,
    storage_exceptions.InvalidResponse,
    storage_exceptions.DataCorruption,
    requests.RequestException,
)
LIBRARY_CHANGED = "libraryChanged"


def _status(exc: Exception) -> int | None:
    if isinstance(exc, api_exceptions.GoogleAPICallError):
        return exc.code
    # Resumable upload chunks raise the media layer's error, which carries the raw response.
    return getattr(getattr(exc, "response", None), "status_code", None)


def _typed(exc: Exception) -> ObjectOpsError:
    """412 is a failed generation precondition, 404 a missing object or upload session."""
    status = _status(exc)
    if status == 412:
        return ObjectExists("conditionNotMet", status)
    if status == 404:
        return ObjectMissing("notFound", status)
    return ObjectUnavailable(type(exc).__name__, status)


def _translated[F: Callable[..., Any]](function: F) -> F:
    @wraps(function)
    def call(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except PROVIDER_ERRORS as exc:
            raise _typed(exc) from exc

    return cast(F, call)


def _library_attribute(owner: object, name: str) -> Any:
    """Read a private google-cloud-storage attribute; a missing one is a typed failure, never AttributeError."""
    try:
        return getattr(owner, name)
    except AttributeError as exc:
        logger.error(f"Hosted GCS storage needs a library attribute that is missing: code={LIBRARY_CHANGED}")
        raise ObjectUnavailable(LIBRARY_CHANGED) from exc


def gcs_client(**arguments: Any) -> storage.Client:
    """The hosted storage client, with one pooled connection per object executor thread."""
    client = storage.Client(**arguments)
    # The library otherwise reads bucket metadata for its telemetry on background threads: a buckets.get
    # call that the bucket object role does not grant and that would run outside the object executor.
    # Assigning a renamed attribute would silently do nothing, so its presence is checked first.
    _library_attribute(client, "_bucket_metadata_cache")
    client._bucket_metadata_cache = None
    session = _library_attribute(client, "_http")
    # An mTLS session mounts its own adapter for https; keep it.
    if not getattr(session, "is_mtls", False):
        session.mount("https://", HTTPAdapter(pool_connections=POOL_HOSTS, pool_maxsize=OBJECT_THREADS))
    return client


@dataclass
class ResumableUpload:
    """The handle of one resumable upload: the blob it creates and the library writer that streams it."""

    blob: storage.Blob
    writer: BlobWriter


class GcsOps:
    """The GCS seam (``FencedOps`` in ``object_storage``) on google-cloud-storage."""

    @_translated
    def __init__(self, bucket: str, client: storage.Client | None = None):
        if not bucket:
            raise ValueError("bucket must not be empty")
        self.client = client or gcs_client()
        self.bucket = self.client.bucket(bucket)

    @_translated
    def create_if_absent(self, key: str, body: bytes) -> int:
        blob = self.bucket.blob(key)
        blob.upload_from_string(body, if_generation_match=0)
        return blob.generation

    def put_if_absent(self, key: str, body: bytes) -> bool:
        try:
            self.create_if_absent(key, body)
        except ObjectExists:
            return False
        return True

    @_translated
    def begin_upload(self, key: str) -> ResumableUpload:
        """No request yet: the session starts with the first full chunk, or at ``finish``."""
        blob = self.bucket.blob(key, chunk_size=UPLOAD_CHUNK_BYTES)
        writer = BlobWriter(blob, chunk_size=UPLOAD_CHUNK_BYTES, ignore_flush=True, if_generation_match=0)
        return ResumableUpload(blob, writer)

    @_translated
    def write(self, handle: ResumableUpload, data: bytes) -> None:
        handle.writer.write(data)

    @_translated
    def finish(self, handle: ResumableUpload) -> int:
        handle.writer.close()
        # The library writer keeps no final upload response: read the new generation back.
        handle.blob.reload()
        return handle.blob.generation

    @_translated
    def cancel(self, handle: ResumableUpload) -> None:
        handle.writer.terminate()

    @_translated
    def compose(self, sources: list[tuple[str, int]], destination: str) -> None:
        """One compose request, not retried by the library: a retry after a lost success would answer 412."""
        self.bucket.blob(destination).compose(
            [self.bucket.blob(key) for key, _ in sources],
            if_generation_match=0,
            if_source_generation_match=[generation for _, generation in sources],
            retry=None,
        )

    @_translated
    def exists(self, key: str) -> bool:
        return self.bucket.blob(key).exists()

    def get_range(self, key: str, start: int, end: int) -> bytes:
        """Inclusive range, clipped to the object's end; GCS answers 416 past it, including any 0-byte object."""
        try:
            # A partial download has no whole-object checksum to validate; the bytes are read as stored.
            return self.bucket.blob(key).download_as_bytes(start=start, end=end, raw_download=True, checksum=None)
        except api_exceptions.RequestRangeNotSatisfiable:
            return b""
        except PROVIDER_ERRORS as exc:
            raise _typed(exc) from exc

    @_translated
    def list_keys(self, prefix: str, token: str | None) -> tuple[list[ObjectInfo], str | None]:
        iterator = self.client.list_blobs(self.bucket, prefix=prefix, page_size=LIST_PAGE_KEYS, page_token=token)
        page = next(iterator.pages, None)
        infos = [] if page is None else [ObjectInfo(blob.name, blob.size, blob.updated) for blob in page]
        return infos, iterator.next_page_token

    @_translated
    def delete_keys(self, keys: list[str]) -> None:
        """One batch request; a missing key is already deleted, any other per-key failure fails the step."""
        if len(keys) > GCS_DELETE_BATCH:
            raise ValueError("A GCS batch deletes at most 100 keys")
        if not keys:
            return
        with self.client.batch(raise_exception=False) as batch:
            self.bucket.delete_blobs(keys)
        # With raise_exception=False the batch keeps every sub-response there instead of raising.
        responses = _library_attribute(batch, "_responses")
        failed = [response.status_code for response in responses if response.status_code not in (200, 204, 404)]
        if failed:
            raise ObjectUnavailable("batchDeleteFailed", failed[0])

    @_translated
    def delete(self, key: str, generation: int | None = None) -> None:
        self.bucket.blob(key).delete(if_generation_match=generation)
