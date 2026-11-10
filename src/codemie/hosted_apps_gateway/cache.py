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

"""Bounded cache of accepted version manifests. Callers must complete current authorization first."""

from __future__ import annotations

import asyncio
import weakref
from collections import OrderedDict

from codemie.core.hosted_applications.manifest import MAX_MANIFEST_BYTES, parse_manifest

_SHARED: "weakref.WeakKeyDictionary[object, ImmutableContentCache]" = weakref.WeakKeyDictionary()


def _retrieve(task):
    # Every waiter may have been cancelled; never leave an unretrieved task exception behind.
    if not task.cancelled():
        task.exception()


class ImmutableContentCache:
    """Digest-bound version manifests.

    Each manifest is read and parsed once per process (single flight per key) without a lock
    shared by other manifests; a few concurrent parses bound transient memory, and the
    retained entries stay within ``maximum`` bytes and ``entries`` items. Asset bytes are not
    re-hashed when served: they were hashed when the version was accepted and are never rewritten.
    """

    def __init__(self, storage, maximum=32 * 1024 * 1024, *, entries=256, parallel_loads=4):
        self.storage, self.maximum, self.entries = storage, maximum, entries
        self.manifests = OrderedDict()
        self.size = 0
        self.loading = {}
        self.load_slots = asyncio.Semaphore(parallel_loads)

    @classmethod
    def shared(cls, storage):
        """One cache per storage object, shared by the content gateway and cover thumbnails."""
        try:
            cache = _SHARED.get(storage)
            if cache is None:
                cache = _SHARED[storage] = cls(storage)
            return cache
        except TypeError:
            return cls(storage)

    async def manifest(self, version, attempt):
        key = (version['manifest_key'], bytes(version['manifest_digest']), attempt['prefix'])
        cached = self.manifests.get(key)
        if cached is not None:
            self.manifests.move_to_end(key)
            return cached[0]
        task = self.loading.get(key)
        if task is None:
            task = asyncio.ensure_future(self._load(key, version, attempt))
            self.loading[key] = task
            task.add_done_callback(_retrieve)
            task.add_done_callback(lambda done: self.loading.pop(key, None) if self.loading.get(key) is done else None)
        # A cancelled requester never cancels the shared load other requests are waiting for.
        return await asyncio.shield(task)

    async def _load(self, key, version, attempt):
        async with self.load_slots:
            data = await self.storage.read_small(version['manifest_key'], maximum=MAX_MANIFEST_BYTES)
            value = parse_manifest(data, expected_digest=version['manifest_digest'], prefix=attempt['prefix'])
        cost = len(data) + 512 * len(value['files'])
        if cost <= self.maximum and key not in self.manifests:
            while self.manifests and (self.size + cost > self.maximum or len(self.manifests) >= self.entries):
                _, (_, old_cost) = self.manifests.popitem(last=False)
                self.size -= old_cost
            self.manifests[key] = (value, cost)
            self.size += cost
        return value
