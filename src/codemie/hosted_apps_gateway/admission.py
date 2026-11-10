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

"""Content-origin capacity: per-process request slots and a bounded share per authenticated viewer.

Content requests are not counted per user or per address. Each process caps how many it serves at
once, and one viewer (including an application's own scripts in that viewer's browser) holds at most
a fixed share of those slots. Floods from outside the platform are the edge's job, as for every other
CodeMie route.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from codemie.core.hosted_applications.errors import HostedApplicationError


@dataclass(frozen=True)
class ContentLimits:
    """Per-process content capacity: total slots, one viewer's share, queueing and request deadline."""

    viewer_concurrency: int = 16
    total_concurrency: int = 64
    queue_seconds: float = 5.0
    request_seconds: float = 120.0


class ContentCapacity:
    """Per-process request slots with a bounded per-viewer share.

    A request takes a general slot before it is authenticated and one of its viewer's slots once
    authorized. While it queues for the viewer's share it gives its general slot back, so one
    viewer's burst cannot hold every slot of the process.
    """

    def __init__(self, total, per_viewer, queue_seconds):
        self.general = asyncio.Semaphore(total)
        self.per_viewer, self.queue_seconds = per_viewer, queue_seconds
        self.viewers: dict[str, list] = {}

    def slot(self) -> CapacitySlot:
        return CapacitySlot(self)


class _Share:
    """One slot of a viewer's share, queueing with a deadline."""

    def __init__(self, shares, key, size):
        self.shares, self.key = shares, key
        self.entry = shares.setdefault(key, [asyncio.Semaphore(size), 0])
        self.entry[1] += 1
        self.held = False

    @property
    def available(self) -> bool:
        return not self.entry[0].locked()

    async def acquire(self, timeout) -> bool:
        try:
            await asyncio.wait_for(self.entry[0].acquire(), timeout=timeout)
        except TimeoutError:
            return False
        self.held = True
        return True

    def release(self) -> None:
        if self.held:
            self.held = False
            self.entry[0].release()
        self.entry[1] -= 1
        if self.entry[1] == 0 and self.shares.get(self.key) is self.entry:
            del self.shares[self.key]


class CapacitySlot:
    """The capacity held by one request; ``release`` is idempotent and always safe to call."""

    def __init__(self, capacity: ContentCapacity):
        self.capacity = capacity
        self.general = False
        self.viewer: _Share | None = None

    async def admit(self) -> bool:
        """Take a general slot; False when the process stays busy for the whole queue time."""
        try:
            await asyncio.wait_for(self.capacity.general.acquire(), timeout=self.capacity.queue_seconds)
        except TimeoutError:
            return False
        self.general = True
        return True

    async def serve(self, subject: str) -> None:
        """Hold one slot of the authenticated viewer's share for the rest of the request."""
        if self.viewer is not None:
            return
        capacity = self.capacity
        self.viewer = _Share(capacity.viewers, subject, capacity.per_viewer)
        queued = not self.viewer.available
        if queued:
            self._release_general()
        if not await self.viewer.acquire(capacity.queue_seconds):
            raise HostedApplicationError("RATE_LIMITED", "Too many concurrent application requests; retry later", 429)
        if queued and not await self.admit():
            raise HostedApplicationError("CONTENT_UNAVAILABLE", "Application busy; retry later", 503)

    def _release_general(self) -> None:
        if self.general:
            self.general = False
            self.capacity.general.release()

    def release(self) -> None:
        self._release_general()
        if self.viewer is not None:
            self.viewer.release()
            self.viewer = None
