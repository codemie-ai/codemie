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

"""Reconciliation sweep: delete old objects whose attempt is deleted or has no row (object backends only).

Objects are orphaned when a database is restored to an older point or a process stops between two steps.
The sweep walks the bucket listing in pages within a time budget and resumes from a saved token on the next run.
Logs carry the event code and counts, never exception text.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select

from codemie.configs.logger import logger as default_logger
from codemie.repository.hosted_applications.object_storage import HostedObjectStorage, ObjectInfo, ObjectOpsError
from codemie.rest_api.models.hosted_application import HostedApplicationStorageAttempts as Attempt

if TYPE_CHECKING:
    from codemie.repository.hosted_applications.repository import HostedApplicationRepository

ORPHAN_MIN_AGE = timedelta(days=1)
# attempt_prefix shape: a-<app hex>/<app hex>/<version hex>/<attempt hex>; nothing else in the bucket matches it.
ATTEMPT_PREFIX = re.compile(r"a-[0-9a-f]{32}(?:/[0-9a-f]{32}){3}")
PREFIX_SEGMENTS = 4
# A listing refused with this status rejects the request itself (for example a page token the provider no
# longer accepts): retrying the same token can never succeed.
REJECTED_REQUEST_STATUS = 400

Lookup = Callable[[set[str]], Awaitable[dict[str, str]]]


def database_lookup(repository: HostedApplicationRepository) -> Lookup:
    """Attempt state by prefix. Prefixes embed the unique attempt id, so the prefix alone identifies the row."""

    async def lookup(prefixes: set[str]) -> dict[str, str]:
        async with repository.sessions() as session:
            rows = await session.execute(
                select(Attempt.prefix, Attempt.state).where(Attempt.prefix.in_(sorted(prefixes)))
            )
            return {row[0]: row[1] for row in rows}

    return lookup


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HostedStorageSweep:
    def __init__(
        self,
        storage: HostedObjectStorage,
        lookup: Lookup,
        *,
        clock: Callable[[], datetime] = _now,
        monotonic: Callable[[], float] = time.monotonic,
        budget_seconds: float,
        logger: logging.Logger = default_logger,
    ):
        self.storage = storage
        self.lookup = lookup
        self.clock = clock
        self.monotonic = monotonic
        self.budget_seconds = budget_seconds
        self.logger = logger
        # Where the next run resumes; None means start from the beginning of the bucket.
        self.token: str | None = None

    async def run_once(self) -> int:
        """Walk pages until the budget ends or the bucket ends. Returns the number of keys deleted."""
        deadline = self.monotonic() + self.budget_seconds
        deleted = 0
        while True:
            page, next_token = await self._page()
            deleted += await self._reconcile(page)
            self.token = next_token
            if next_token is None or self.monotonic() >= deadline:
                return deleted

    async def _page(self) -> tuple[list[ObjectInfo], str | None]:
        """The next listing page. A saved token the provider rejects is dropped: the next run starts over."""
        try:
            return await self.storage.sweep_page(self.token)
        except ObjectOpsError as exc:
            if self.token is not None and exc.status == REJECTED_REQUEST_STATUS:
                self.token = None
                self.logger.warning(f"hosted_storage_sweep_restarted provider_code={exc.code}")
            raise

    async def _reconcile(self, page: list[ObjectInfo]) -> int:
        cutoff = self.clock() - ORPHAN_MIN_AGE
        by_prefix: dict[str, list[str]] = defaultdict(list)
        for info in page:
            name = "/".join(info.key.split("/")[:PREFIX_SEGMENTS])
            if ATTEMPT_PREFIX.fullmatch(name) and info.key != name and info.modified < cutoff:
                by_prefix[name].append(info.key)
        if not by_prefix:
            return 0
        states = await self.lookup(set(by_prefix))
        total = 0
        for name, keys in by_prefix.items():
            if states.get(name, "deleted") != "deleted":
                continue
            await self.storage.delete_listed(keys)
            total += len(keys)
            self.logger.error(f"hosted_storage_orphans_deleted count={len(keys)}")
        return total
