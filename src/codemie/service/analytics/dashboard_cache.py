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

"""Short-lived, per-pod response cache of the heavy dashboard requests (/users, /user-charts, /cost).

A response is reused for `CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S` seconds (the default matches the rollup refresh
interval, so a cached answer is never older than the data it was built from can be). Requests that arrive while the
same answer is being computed wait for that computation instead of starting their own (single flight).

The key carries everything that selects or cuts the data: the endpoint, the viewer scope (e-mail, privilege, admin
projects, mode), every filter and the paging. A window that ends "now" is a different `end_dt` on every request, so a
window ending within `_LIVE_WINDOW_S` of the clock is keyed by its start (or length) instead; it then shares an answer
computed for a window that ended at most one TTL earlier. Anything the key cannot describe is not cached.

Every caller gets its own deep copy: the router edits the rows in place (user ids, `total_count`).
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from codemie.configs.config import config
from codemie.repository.cli_analytics.ports import RequestBudgetExceeded
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.scope import ViewerScope

MAX_ENTRIES = 32
# A window whose end is this close to the clock was asked for as "up to now" (presets); it is keyed as live.
_LIVE_WINDOW_S = 60.0


@dataclass(frozen=True)
class PricerSnapshot:
    """What the router reads of a request's `CatalogPricer` (`unpriced`, `catalog_source`), kept after it is gone."""

    unpriced: list[str]
    catalog_source: str

    @staticmethod
    def of(pricer: Any) -> PricerSnapshot:
        return PricerSnapshot(unpriced=list(pricer.unpriced), catalog_source=pricer.catalog_source)


Computed = tuple[dict[str, Any], PricerSnapshot]


def _sorted(values: list[str] | None) -> tuple[str, ...] | None:
    return tuple(sorted(values)) if values is not None else None


def _window_key(f: LocalAnalyticsFilter, now: datetime) -> tuple[Any, ...]:
    start, end = f.start_dt, f.end_dt
    if end.tzinfo is None or start.tzinfo is None:
        return ("exact", start, end)
    if abs((now - end).total_seconds()) > _LIVE_WINDOW_S:
        return ("exact", start, end)
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    if start == midnight:  # a calendar-day preset: its start does not move during the day
        return ("live-from", start)
    return ("live-span", round((end - start).total_seconds()))  # last hour, 6 hours, 24 hours


def dashboard_key(endpoint: str, f: LocalAnalyticsFilter, *extra: Any, now: datetime | None = None) -> tuple | None:
    """The cache key of a request, or None when it must not be cached (a scope that is not a `ViewerScope`)."""
    scope = f.scope
    if not isinstance(scope, ViewerScope):
        return None
    now = now or datetime.now(timezone.utc)
    return (
        endpoint,
        (
            scope.viewer_email,
            scope.unrestricted,
            tuple(sorted(scope.admin_projects)),
            scope.mode,
            scope.sees_private,
            scope.own_shared,
        ),
        _window_key(f, now),
        _sorted(f.users),
        _sorted(f.projects),
        _sorted(f.repositories),
        f.branch,
        f.branch_unattributed,
        f.deny_all,
        f.project_unattributed,
        _sorted(f.session_ids),
        _sorted(f.subscription_types),
        _sorted(f.project_origins),
        _sorted(f.developers),
        extra,
    )


def _own_copy(computed: Any) -> Any:
    """A copy the route may change: it sets `total_count` and each row's `user_id`, so the answer's dict, its lists and
    the dicts in them are new and everything deeper is shared, read-only. A whole deep copy of an answer for 8,000
    users is 90 ms of pure Python that holds the GIL, and ingestion runs on the same event loop. Anything that is not
    an answer (data, pricer) is deep-copied."""
    if not (isinstance(computed, tuple) and len(computed) == 2 and isinstance(computed[0], dict)):
        return copy.deepcopy(computed)
    data, pricer = computed
    fresh: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, list):
            fresh[key] = [dict(item) if isinstance(item, dict) else item for item in value]
        else:
            fresh[key] = dict(value) if isinstance(value, dict) else value
    if isinstance(pricer, PricerSnapshot):
        pricer = PricerSnapshot(list(pricer.unpriced), pricer.catalog_source)
    return fresh, pricer


@dataclass
class _Flight:
    task: asyncio.Task
    waiters: int = 0


class DashboardCache:
    def __init__(self, ttl_s: float, max_entries: int = MAX_ENTRIES, clock: Callable[[], float] = time.monotonic):
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[tuple, tuple[float, Computed]] = OrderedDict()
        self._flights: dict[tuple, _Flight] = {}

    @property
    def enabled(self) -> bool:
        return self._ttl_s > 0

    async def get_or_compute(self, key: tuple, compute: Callable[[], Awaitable[Computed]]) -> Computed:
        """The answer for `key`: a fresh stored one, the one being computed, or a new computation of `compute`.
        A failed computation is not stored; every request waiting for it gets the error. A request that is cancelled
        or times out while waiting gets its own CancelledError and the others keep waiting; the computation is
        cancelled, and unregistered at once, only when the last one waiting leaves, so a request that arrives after
        that starts a new one instead of joining a cancelled task."""
        entry = self._entries.get(key)
        if entry is not None:
            if entry[0] > self._clock():
                self._entries.move_to_end(key)
                return _own_copy(entry[1])
            del self._entries[key]  # expired: a 34 MB answer must not stay until 32 newer keys push it out
        flight = self._flights.get(key)
        if flight is None:
            flight = _Flight(asyncio.ensure_future(self._run(key, compute)))
            self._flights[key] = flight
        flight.waiters += 1
        try:
            computed = await asyncio.shield(flight.task)
        except asyncio.CancelledError:
            if flight.waiters == 1 and not flight.task.done():  # the last one waiting left: stop the work
                flight.task.cancel()
                if self._flights.get(key) is flight:
                    del self._flights[key]
            raise
        finally:
            flight.waiters -= 1
        return _own_copy(computed)

    async def _run(self, key: tuple, compute: Callable[[], Awaitable[Computed]]) -> Computed:
        try:
            computed = await compute()
            self._entries[key] = (self._clock() + self._ttl_s, computed)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return computed
        finally:
            flight = self._flights.get(key)
            if flight is not None and flight.task is asyncio.current_task():  # not a newer flight of the same key
                del self._flights[key]


class RankingCache:
    """The money of every user of a window (`UsersMoneyPage` with no page cut), kept for the same TTL as the answers.

    Every page of /users and /user-charts of one viewer, window and filters needs the same ranking; only the facts of
    the page's developers differ. The stored value is read-only, so it is shared, not copied."""

    def __init__(self, ttl_s: float, max_entries: int = 8, clock: Callable[[], float] = time.monotonic):
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
        self._flights: dict[tuple, _Flight] = {}

    @property
    def enabled(self) -> bool:
        return self._ttl_s > 0

    def get(self, key: tuple) -> Any | None:
        entry = self._entries.get(key)
        if entry is None or entry[0] <= self._clock():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return entry[1]

    async def single_flight(self, key: tuple, compute: Callable[[], Awaitable[Any]]) -> Any:
        """`compute()`, once for every request that asks for `key` while it runs: the others wait for that read instead
        of starting their own (N users opening a tab at the same moment send N identical reads, and the rollup's lane
        has a few slots). The answer is returned as it is (callers store it themselves, and it is read-only). A failed
        read gives every waiting request the error and is not remembered. A request that is cancelled or times out
        while waiting leaves the others waiting; the read is cancelled, and unregistered at once, only when the last
        one waiting leaves, so a request that arrives after that starts a new one."""
        led = False
        while True:
            flight, started = self._join(key, compute)
            led = led or started
            try:
                return await asyncio.shield(flight.task)
            except asyncio.CancelledError:
                self._abandon_if_last(key, flight)
                raise
            except RequestBudgetExceeded:
                # The read ran under the budget of the request that started it (the task copied its context). A
                # follower with budget left starts its own read; its own budget is enforced around this call.
                if led:
                    raise
                led = True
            finally:
                flight.waiters -= 1

    def _join(self, key: tuple, compute: Callable[[], Awaitable[Any]]) -> tuple[_Flight, bool]:
        """The flight of `key` with this request counted among its waiters, and whether this request started it."""
        flight = self._flights.get(key)
        started = flight is None
        if flight is None:
            flight = _Flight(asyncio.ensure_future(self._fly(key, compute)))
            self._flights[key] = flight
        flight.waiters += 1
        return flight, started

    def _abandon_if_last(self, key: tuple, flight: _Flight) -> None:
        """A cancelled waiter leaves the others waiting; the last one cancels the read and unregisters it at once, so a
        request that arrives after that starts a new one."""
        if flight.waiters == 1 and not flight.task.done():
            flight.task.cancel()
            if self._flights.get(key) is flight:
                del self._flights[key]

    async def _fly(self, key: tuple, compute: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await compute()
        finally:
            flight = self._flights.get(key)
            if flight is not None and flight.task is asyncio.current_task():  # not a newer flight of the same key
                del self._flights[key]

    def put(self, key: tuple, value: Any, ttl_s: float | None = None) -> None:
        self._entries[key] = (self._clock() + (self._ttl_s if ttl_s is None else ttl_s), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


_cache: DashboardCache | None = None
_ranking_cache: RankingCache | None = None


def get_dashboard_cache() -> DashboardCache:
    global _cache
    ttl_s = config.CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S
    if _cache is None or _cache._ttl_s != ttl_s:
        _cache = DashboardCache(ttl_s)
    return _cache


def get_ranking_cache() -> RankingCache:
    global _ranking_cache
    ttl_s = config.CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S
    if _ranking_cache is None or _ranking_cache._ttl_s != ttl_s:
        _ranking_cache = RankingCache(ttl_s)
    return _ranking_cache


_rollup_used_at = float("-inf")
RECENTLY_USED_S = 1800.0


def mark_rollup_used() -> None:
    """A request asked for the rollup answer: the warm-up job keeps it fresh for a while."""
    global _rollup_used_at
    _rollup_used_at = time.monotonic()


def rollup_recently_used(window_s: float = RECENTLY_USED_S) -> bool:
    return time.monotonic() - _rollup_used_at <= window_s


def rollup_key(f: LocalAnalyticsFilter, endpoint: str = "users-rollup", *extra: Any) -> tuple | None:
    """The cache key of the user rollup's answer. The viewer's e-mail never enters the SQL of an unrestricted
    "exclude" scope (every shared session, whoever asks), so every such viewer shares one key; any other scope keeps
    its own."""
    from codemie.repository.cli_analytics.postgres.user_rollup import ROLLUP_SCOPE

    scope = f.scope
    if isinstance(scope, ViewerScope) and scope.unrestricted and scope.mode == "exclude":
        f = copy.copy(f)
        f.scope = ROLLUP_SCOPE
    return dashboard_key(endpoint, f, *extra)


def rollup_max_age_s() -> float:
    """How long ago the job may last have confirmed today's row before a request stops trusting it: a rebuild is held
    for MIN_REBUILD_S and takes a while."""
    return max(300.0, 3.0 * config.CLI_ANALYTICS_USER_ROLLUP_MIN_REBUILD_S + 120.0)


def reset_dashboard_cache() -> None:
    """Drops the caches and every computation in flight's registration (tests, and a pod that wants a clean start)."""
    global _cache, _ranking_cache, _rollup_used_at
    _cache = None
    _ranking_cache = None
    _rollup_used_at = float("-inf")


async def cached_dashboard(
    endpoint: str,
    f: LocalAnalyticsFilter,
    compute: Callable[[], Awaitable[tuple[dict[str, Any], Any]]],
    *extra: Any,
) -> tuple[dict[str, Any], PricerSnapshot]:
    """`compute()` (the handler's `(data, pricer)`) through the cache; the pricer comes back as a snapshot."""

    async def snapshot() -> Computed:
        data, pricer = await compute()
        return data, PricerSnapshot.of(pricer)

    cache = get_dashboard_cache()
    key = dashboard_key(endpoint, f, *extra) if cache.enabled else None
    if key is None:
        return await snapshot()
    return await cache.get_or_compute(key, snapshot)
