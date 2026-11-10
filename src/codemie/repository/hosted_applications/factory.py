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

"""Selects the hosted application storage adapter from ``FILES_STORAGE_TYPE``.

``filesystem`` binds the private volume on the storage executor. ``aws`` binds the dedicated hosted bucket
and refuses to open when the S3, database and pod clocks disagree by more than the cleanup grace.
``gcp`` binds the dedicated hosted bucket with no grace and no clock check: a GCS writer is fenced by its
fence object, not by an expiry.
Every blocking open step runs through ``opening_call`` or ``storage_call``, which the startup deadline can
abandon: a hung bucket, volume or metadata server never holds platform startup past it.
Tests replace ``BotoS3Ops`` or ``GcsOps`` in this module with a fake-backed constructor and pass ``pod_clock``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

from codemie.configs.config import Config
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.filesystem import (
    STORAGE_UNAVAILABLE_MESSAGE,
    HostedFileStorage,
    storage_call,
)
from codemie.repository.hosted_applications.gcs_backend import GcsOps
from codemie.repository.hosted_applications.object_storage import (
    STORAGE_ID_KEY,
    HostedObjectStorage,
    ObjectOpsError,
    opening_call,
)
from codemie.repository.hosted_applications.s3_backend import BotoS3Ops

DatabaseClock = Callable[[], Awaitable[datetime]]
PodClock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def open_hosted_storage(
    config: Config,
    database_clock: DatabaseClock,
    *,
    pod_clock: PodClock | None = None,
) -> HostedFileStorage | HostedObjectStorage:
    """Open the adapter ``FILES_STORAGE_TYPE`` selects; any failure is a ``HostedApplicationError``."""
    kind = config.FILES_STORAGE_TYPE
    if kind == "filesystem":
        return await storage_call(HostedFileStorage, Path(config.HOSTED_APPS_STORAGE_DIR))
    if kind == "aws":
        return await _open_s3(config, database_clock, pod_clock or utc_now)
    if kind == "gcp":
        return await _open_gcs(config)
    raise HostedApplicationError("STORAGE_UNAVAILABLE", "Unsupported hosted storage type", 503)


async def _open_gcs(config: Config) -> HostedObjectStorage:
    try:
        # Building the client resolves the default credentials, which may call the metadata server.
        ops = await opening_call(GcsOps, config.HOSTED_APPS_STORAGE_BUCKET)
    except ObjectOpsError as exc:
        raise HostedApplicationError("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, 503) from exc
    return await HostedObjectStorage.open(ops, provider="gcp", cleanup_grace_seconds=0)


def _midpoint(before: datetime, after: datetime) -> datetime:
    return before + (after - before) / 2


async def _open_s3(config: Config, database_clock: DatabaseClock, pod_clock: PodClock) -> HostedObjectStorage:
    grace = config.HOSTED_APPS_STORAGE_S3_GRACE_SECONDS
    try:
        # Building the client finds the bucket's region with a HeadBucket call.
        ops = await opening_call(BotoS3Ops, config.HOSTED_APPS_STORAGE_BUCKET)
    except ObjectOpsError as exc:
        raise HostedApplicationError("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, 503) from exc
    opened = await HostedObjectStorage.open(ops, provider="aws", cleanup_grace_seconds=grace)
    pod_before = pod_clock()
    database_before = await database_clock()
    try:
        s3_time = await opening_call(ops.server_date, STORAGE_ID_KEY)
    except ObjectOpsError as exc:
        raise HostedApplicationError("STORAGE_UNAVAILABLE", STORAGE_UNAVAILABLE_MESSAGE, 503) from exc
    database_after = await database_clock()
    pod_after = pod_clock()
    s3_offset = abs((s3_time - _midpoint(database_before, database_after)).total_seconds())
    pod_offset = abs((s3_time - _midpoint(pod_before, pod_after)).total_seconds())
    if s3_offset + pod_offset > grace:
        raise HostedApplicationError("STORAGE_CLOCK_SKEW", "Storage clock differs from the platform clocks", 503)
    return opened
