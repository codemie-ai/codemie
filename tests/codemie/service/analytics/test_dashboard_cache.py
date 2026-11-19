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


"""The short-lived response cache of /users, /user-charts and /cost (EPMCDME-17051)."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.scope import ViewerScope
from codemie.service.analytics.dashboard_cache import DashboardCache, PricerSnapshot, dashboard_key

NOW = datetime(2026, 11, 11, 12, 0, 30, tzinfo=timezone.utc)
SNAPSHOT = PricerSnapshot(unpriced=["m"], catalog_source="catalog")


def scope(email="a@example.com", unrestricted=True, projects=(), mode="detail"):
    return ViewerScope(viewer_email=email, unrestricted=unrestricted, admin_projects=frozenset(projects), mode=mode)


def make_filter(start=None, end=None, **kwargs):
    kwargs.setdefault("scope", scope())
    return LocalAnalyticsFilter(
        start_dt=start or datetime(2026, 11, 4, tzinfo=timezone.utc), end_dt=end or NOW, **kwargs
    )


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def counting(result=None, delay=0.0):
    calls = []

    async def compute():
        calls.append(1)
        if delay:
            await asyncio.sleep(delay)
        return (result if result is not None else {"rows": [{"n": len(calls)}]}), SNAPSHOT

    return compute, calls


# ── the cache ──


@pytest.mark.asyncio
async def test_a_fresh_answer_is_reused_and_every_caller_gets_its_own_copy():
    cache = DashboardCache(30, clock=FakeClock())
    compute, calls = counting()

    first, _ = await cache.get_or_compute(("k",), compute)
    first["rows"][0]["n"] = 99  # the router edits the rows in place
    second, snapshot = await cache.get_or_compute(("k",), compute)

    assert len(calls) == 1
    assert second == {"rows": [{"n": 1}]}
    assert snapshot == SNAPSHOT


@pytest.mark.asyncio
async def test_an_expired_answer_is_computed_again():
    clock = FakeClock()
    cache = DashboardCache(30, clock=clock)
    compute, calls = counting()

    await cache.get_or_compute(("k",), compute)
    clock.now += 31
    data, _ = await cache.get_or_compute(("k",), compute)

    assert len(calls) == 2
    assert data == {"rows": [{"n": 2}]}


@pytest.mark.asyncio
async def test_different_keys_do_not_share_answers():
    cache = DashboardCache(30, clock=FakeClock())
    compute, calls = counting()

    await cache.get_or_compute(("a",), compute)
    await cache.get_or_compute(("b",), compute)

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_identical_requests_that_overlap_share_one_computation():
    cache = DashboardCache(30, clock=FakeClock())
    compute, calls = counting(delay=0.05)

    results = await asyncio.gather(*(cache.get_or_compute(("k",), compute) for _ in range(5)))

    assert len(calls) == 1
    assert all(data == {"rows": [{"n": 1}]} for data, _ in results)
    assert len({id(data) for data, _ in results}) == 5  # each has its own copy


@pytest.mark.asyncio
async def test_a_failure_reaches_every_waiter_and_is_not_stored():
    cache = DashboardCache(30, clock=FakeClock())
    attempts = []

    async def failing():
        attempts.append(1)
        await asyncio.sleep(0.02)
        raise TimeoutError("budget")

    outcomes = await asyncio.gather(*(cache.get_or_compute(("k",), failing) for _ in range(3)), return_exceptions=True)
    assert all(isinstance(o, TimeoutError) for o in outcomes)
    assert len(attempts) == 1

    compute, calls = counting()
    await cache.get_or_compute(("k",), compute)
    assert len(calls) == 1  # nothing was stored for the failure


@pytest.mark.asyncio
async def test_the_work_stops_when_the_last_waiter_leaves_but_not_before():
    cache = DashboardCache(30, clock=FakeClock())
    started, finished = asyncio.Event(), []

    async def slow():
        started.set()
        await asyncio.sleep(0.2)
        finished.append(1)
        return {"rows": []}, SNAPSHOT

    first = asyncio.ensure_future(cache.get_or_compute(("k",), slow))
    await started.wait()
    second = asyncio.ensure_future(cache.get_or_compute(("k",), slow))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    data, _ = await second  # one waiter is still there: the computation goes on
    assert data == {"rows": []} and finished == [1]

    alone = asyncio.ensure_future(cache.get_or_compute(("other",), slow))
    await asyncio.sleep(0.01)
    alone.cancel()
    await asyncio.gather(alone, return_exceptions=True)
    await asyncio.sleep(0.3)
    assert finished == [1]  # the computation of ("other",) was cancelled


@pytest.mark.asyncio
async def test_the_oldest_entries_are_dropped_beyond_the_limit():
    cache = DashboardCache(30, max_entries=2, clock=FakeClock())
    compute, calls = counting()

    for key in ("a", "b", "c"):
        await cache.get_or_compute((key,), compute)
    await cache.get_or_compute(("a",), compute)  # evicted: computed again

    assert len(calls) == 4


# ── the key ──


def test_another_viewer_scope_is_another_key():
    keys = {
        dashboard_key("cost", make_filter(scope=scope()), now=NOW),
        dashboard_key("cost", make_filter(scope=scope(email="b@example.com", mode="exclude")), now=NOW),
        dashboard_key("cost", make_filter(scope=scope(unrestricted=False, projects=["p"])), now=NOW),
        dashboard_key("cost", make_filter(scope=scope(unrestricted=False, projects=["q"])), now=NOW),
        dashboard_key("cost", make_filter(scope=scope(mode="own_only")), now=NOW),
    }
    assert len(keys) == 5


def test_every_filter_and_the_endpoint_and_the_paging_are_part_of_the_key():
    base = dashboard_key("users", make_filter(), 0, 50, now=NOW)
    variants = [
        dashboard_key("cost", make_filter(), 0, 50, now=NOW),
        dashboard_key("users", make_filter(), 1, 50, now=NOW),
        dashboard_key("users", make_filter(users=["u@example.com"]), 0, 50, now=NOW),
        dashboard_key("users", make_filter(projects=["p"]), 0, 50, now=NOW),
        dashboard_key("users", make_filter(repositories=["r"]), 0, 50, now=NOW),
        dashboard_key("users", make_filter(branch="main"), 0, 50, now=NOW),
        dashboard_key("users", make_filter(deny_all=True), 0, 50, now=NOW),
        dashboard_key("users", make_filter(project_unattributed=True), 0, 50, now=NOW),
        dashboard_key("users", make_filter(subscription_types=["x"]), 0, 50, now=NOW),
        dashboard_key("users", make_filter(project_origins=["x"]), 0, 50, now=NOW),
        dashboard_key("users", make_filter(developers=["d"]), 0, 50, now=NOW),
    ]
    assert base not in variants and len(set(variants)) == len(variants)


def test_the_order_of_a_filter_list_does_not_matter():
    one = dashboard_key("users", make_filter(users=["a@x", "b@x"]), now=NOW)
    two = dashboard_key("users", make_filter(users=["b@x", "a@x"]), now=NOW)
    assert one == two


def test_a_window_ending_now_is_one_key_whatever_the_second():
    start = datetime(2026, 11, 4, tzinfo=timezone.utc)  # a calendar-day preset
    one = dashboard_key("cost", make_filter(start=start, end=NOW), now=NOW)
    later = dashboard_key(
        "cost", make_filter(start=start, end=NOW + timedelta(seconds=20)), now=NOW + timedelta(seconds=20)
    )
    assert one == later
    hour_a = dashboard_key("cost", make_filter(start=NOW - timedelta(hours=1), end=NOW), now=NOW)
    hour_b = dashboard_key(
        "cost",
        make_filter(start=NOW + timedelta(seconds=9) - timedelta(hours=1), end=NOW + timedelta(seconds=9)),
        now=NOW + timedelta(seconds=9),
    )
    assert hour_a == hour_b
    assert hour_a != dashboard_key("cost", make_filter(start=NOW - timedelta(hours=6), end=NOW), now=NOW)


def test_a_window_that_ended_earlier_is_keyed_exactly():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    end_a, end_b = datetime(2026, 11, 3, 10, tzinfo=timezone.utc), datetime(2026, 11, 3, 11, tzinfo=timezone.utc)
    assert dashboard_key("cost", make_filter(start=start, end=end_a), now=NOW) != dashboard_key(
        "cost", make_filter(start=start, end=end_b), now=NOW
    )
    assert dashboard_key("cost", make_filter(start=start, end=end_a), now=NOW) == dashboard_key(
        "cost", make_filter(start=start, end=end_a), now=NOW
    )


def test_a_malformed_scope_is_not_cached():
    assert dashboard_key("cost", make_filter(scope=None), now=NOW) is None
    assert dashboard_key("cost", make_filter(scope=SimpleNamespace(viewer_email="a@x")), now=NOW) is None


# ── the key and the lifetime of the user rollup's answer ──

from codemie.service.analytics import dashboard_cache as dc  # noqa: E402


def test_every_unrestricted_admin_shares_the_rollup_answer_but_no_other_scope_does():
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)
    admin_a = make_filter(start=start, scope=scope(email="a@example.com", mode="exclude"))
    admin_b = make_filter(start=start, scope=scope(email="b@example.com", mode="exclude"))
    project_admin = make_filter(
        start=start, scope=scope(email="a@example.com", unrestricted=False, projects=["p"], mode="exclude")
    )
    other_window = make_filter(start=start + timedelta(days=1), scope=scope(email="a@example.com", mode="exclude"))

    assert dc.rollup_key(admin_a) == dc.rollup_key(admin_b)
    assert dc.rollup_key(admin_a) != dc.rollup_key(project_admin)
    assert dc.rollup_key(admin_a) != dc.rollup_key(other_window)


def test_a_ranking_entry_can_carry_its_own_lifetime():
    clock = FakeClock()
    cache = dc.RankingCache(30, clock=clock)

    cache.put(("short",), "a")
    cache.put(("long",), "b", ttl_s=300)
    clock.now += 60

    assert cache.get(("short",)) is None
    assert cache.get(("long",)) == "b"


def test_the_rollup_counts_as_used_only_for_a_while_after_a_request():
    dc.reset_dashboard_cache()
    assert not dc.rollup_recently_used()

    dc.mark_rollup_used()

    assert dc.rollup_recently_used()
    assert not dc.rollup_recently_used(window_s=-1)


@pytest.mark.asyncio
async def test_a_request_that_arrives_after_the_last_waiter_left_starts_a_new_computation():
    cache = DashboardCache(30, clock=FakeClock())
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(0.05)
        return {"rows": [{"n": 1}]}, SNAPSHOT

    alone = asyncio.ensure_future(cache.get_or_compute(("k",), slow))
    await started.wait()
    alone.cancel()
    await asyncio.sleep(0)  # the waiter has cancelled the flight; the flight's task has not finished unwinding yet
    data, _ = await cache.get_or_compute(("k",), slow)  # must not join the cancelled task and get its CancelledError

    assert data == {"rows": [{"n": 1}]}
    await asyncio.gather(alone, return_exceptions=True)
    assert cache._flights == {}


@pytest.mark.asyncio
async def test_a_copy_is_the_callers_own_as_far_as_the_route_writes_and_shares_the_rest():
    cache = DashboardCache(30, clock=FakeClock())
    activity = [{"day": "2026-11-01", "sessions": 3}]
    answer = {"rows": [{"developer_name": "a", "daily_activity": activity}], "kpis": {"n": 1}, "total_count": 1}
    compute, _ = counting(answer)

    first, pricer = await cache.get_or_compute(("k",), compute)
    first["total_count"] = None  # what the routes change: the count and each row's user_id
    first["rows"][0]["user_id"] = "u1"
    first["kpis"]["n"] = 2
    pricer.unpriced.append("m")
    second, again = await cache.get_or_compute(("k",), compute)

    assert second == answer and again.unpriced == SNAPSHOT.unpriced
    assert second["rows"][0]["daily_activity"] is activity  # deeper values are shared, not copied


@pytest.mark.asyncio
async def test_an_expired_answer_is_dropped_when_it_is_looked_up():
    clock = FakeClock()
    cache = DashboardCache(30, clock=clock)
    started = asyncio.Event()

    async def never():
        started.set()
        await asyncio.sleep(10)

    compute, _ = counting()
    await cache.get_or_compute(("k",), compute)
    clock.now += 31
    waiting = asyncio.ensure_future(cache.get_or_compute(("k",), never))
    await started.wait()

    assert ("k",) not in cache._entries  # not kept until 32 newer keys push it out
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)


def test_branch_unattributed_changes_the_dashboard_key():
    plain = make_filter()
    flagged = make_filter()
    flagged.branch_unattributed = True

    assert dashboard_key("sessions", plain, now=NOW) != dashboard_key("sessions", flagged, now=NOW)


def test_include_repositories_changes_the_dashboard_key():
    # `/projects` answers differently per mode (nested repositories or not): a keyed `extra` keeps the two apart.
    f = make_filter()

    assert dashboard_key("projects", f, True, now=NOW) != dashboard_key("projects", f, False, now=NOW)
