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

"""A ``TTLCache`` whose token-cache operations are safe to call from several threads."""

from __future__ import annotations

import threading
from typing import Any

from cachetools import TTLCache

_MISSING: Any = object()


class ThreadSafeTTLCache(TTLCache):
    """``TTLCache`` with ``get``, ``__setitem__``, ``pop`` and ``purge_prefix`` serialized on one lock.

    ``TTLCache`` is not thread-safe: expiry and iteration share unsynchronized timer state. The per-user
    token caches are read and written from request threads while logout purges them from a worker
    thread, so every operation those callers use goes through the lock. Any other access is unsynchronized.
    """

    def __init__(self, maxsize: float, ttl: float) -> None:
        super().__init__(maxsize=maxsize, ttl=ttl)
        self._lock = threading.RLock()

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            return super().get(key, default)

    def __setitem__(self, key: Any, value: Any) -> None:
        with self._lock:
            super().__setitem__(key, value)

    def pop(self, key: Any, default: Any = _MISSING) -> Any:
        with self._lock:
            if default is _MISSING:
                return super().pop(key)
            return super().pop(key, default)

    def purge_prefix(self, prefix: str) -> None:
        """Drop every entry whose key starts with ``prefix``."""
        with self._lock:
            for key in [key for key in self if key.startswith(prefix)]:
                super().pop(key, None)
