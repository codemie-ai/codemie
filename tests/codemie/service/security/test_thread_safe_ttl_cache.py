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

"""ThreadSafeTTLCache: prefix purge, and every supported access serialized on one lock."""

from __future__ import annotations

import threading

import pytest

from codemie.service.security.thread_safe_ttl_cache import ThreadSafeTTLCache

_LOCK_WAIT_SECONDS = 0.2
_JOIN_SECONDS = 2


@pytest.fixture
def cache() -> ThreadSafeTTLCache:
    return ThreadSafeTTLCache(maxsize=16, ttl=60)


def test_purge_prefix_drops_only_prefixed_keys(cache):
    cache["user-1:a"] = "a"
    cache["user-1:b"] = "b"
    cache["user-10:c"] = "c"
    cache["user-2:d"] = "d"

    cache.purge_prefix("user-1:")

    assert set(cache) == {"user-10:c", "user-2:d"}


def test_purge_prefix_on_empty_cache_is_a_no_op(cache):
    cache.purge_prefix("user-1:")

    assert len(cache) == 0


def test_get_set_and_pop_behave_like_a_ttl_cache(cache):
    cache["k"] = "v"

    assert cache.get("k") == "v"
    assert cache.get("missing") is None
    assert cache.get("missing", "fallback") == "fallback"
    assert cache.pop("k") == "v"
    assert cache.pop("k", None) is None


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(lambda c: c.get("k"), id="get"),
        pytest.param(lambda c: c.__setitem__("k", "v"), id="set"),
        pytest.param(lambda c: c.pop("k", None), id="pop"),
        pytest.param(lambda c: c.purge_prefix("k"), id="purge_prefix"),
    ],
)
def test_access_waits_while_another_thread_holds_the_cache_lock(cache, operation):
    """TTLCache keeps unsynchronized timer state, so concurrent access must be serialized."""
    worker = threading.Thread(target=operation, args=(cache,))

    with cache._lock:
        worker.start()
        worker.join(timeout=_LOCK_WAIT_SECONDS)
        blocked_while_locked = worker.is_alive()
    worker.join(timeout=_JOIN_SECONDS)

    assert blocked_while_locked
    assert not worker.is_alive()
