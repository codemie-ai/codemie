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

"""Control flow of the rollup refresher: claims, batches, isolation, deferrals and drops."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import asyncpg
import pytest

from codemie.repository.cli_analytics.postgres import rollups
from codemie.repository.cli_analytics.postgres.rollups import RECOMPUTE, RollupRefresher


class QueueConnection:
    """A queue of `rollup_dirty` keys served in batches; records executed statements."""

    def __init__(self, queued: int, batch_size: int) -> None:
        self.queued = queued
        self.batch_size = batch_size
        self.executed: list[str] = []
        self.timeouts: dict[str, float | None] = {}
        self.transactions = 0

    async def fetch(self, sql, limit):
        n = min(self.queued, limit)
        self.queued -= n
        return [{"day": date(2026, 9, 1), "session_id": f"s{i}", "kinds": 15, "version": 1} for i in range(n)]

    async def fetchrow(self, sql):
        if sql == rollups._READ_ALONE_KEY:
            return None
        return {"n": self.queued, "age": 12.5 if self.queued else None}

    async def execute(self, sql, *args, timeout=None):
        self.executed.append(sql)
        self.timeouts[sql] = timeout
        return "INSERT 0 42"

    @asynccontextmanager
    async def _tx(self):
        self.transactions += 1
        yield

    def transaction(self):
        return self._tx()


class Engine:
    def __init__(self, conn: QueueConnection) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self, timeout=None):
        yield self.conn


@pytest.mark.asyncio
async def test_refresh_works_the_queue_down_in_batches():
    conn = QueueConnection(queued=250, batch_size=100)

    total = await RollupRefresher(Engine(conn), batch_size=100).refresh()  # type: ignore[arg-type]

    assert total == 250
    assert conn.transactions == 3


@pytest.mark.asyncio
async def test_refresh_with_an_empty_queue_opens_no_transaction():
    conn = QueueConnection(queued=0, batch_size=100)

    assert await RollupRefresher(Engine(conn), batch_size=100).refresh() == 0  # type: ignore[arg-type]
    assert conn.transactions == 0


@pytest.mark.asyncio
async def test_refresh_stops_when_its_time_budget_is_spent():
    conn = QueueConnection(queued=10_000, batch_size=100)
    ticks = iter([0.0, 5.0, 11.0, 99.0])

    with patch.object(rollups.time, "monotonic", side_effect=lambda: next(ticks)):
        total = await RollupRefresher(Engine(conn), batch_size=100, max_run_seconds=10).refresh()  # type: ignore[arg-type]

    assert total == 200


@pytest.mark.asyncio
async def test_a_batch_recomputes_every_family_then_releases_its_keys_in_one_transaction():
    conn = QueueConnection(queued=5, batch_size=100)

    assert await RollupRefresher(Engine(conn), batch_size=100).refresh_batch(conn) == 5  # type: ignore[arg-type]

    assert conn.transactions == 1
    assert conn.executed[0].startswith("SET LOCAL statement_timeout")
    assert conn.executed[-3:] == [rollups._LOCK_CLAIMED_KEYS, rollups._RELEASE_KEYS, rollups._RESTAMP_KEYS]
    assert "r.version = k.version" in rollups._RELEASE_KEYS  # only keys not marked again meanwhile
    assert conn.executed[-3 - len(RECOMPUTE) : -3] == RECOMPUTE


def test_every_rollup_table_is_recomputed():
    targets = {s.split()[2] for s in RECOMPUTE if s.lstrip().startswith("INSERT INTO")}

    assert targets == {
        "cost_daily",
        "lines_daily",
        "active_time_daily",
        "turns_daily",
        "tool_facts_daily",
        "session_files_daily",
        "invocations_hourly",
        "session_skills",
        "session_dims",
    }


@pytest.mark.asyncio
async def test_mark_dirty_marks_one_day_per_statement_with_the_refresh_time_budget():
    # One statement over a multi-week range would outlast a dashboard's 30 s limit and mark nothing.
    conn = QueueConnection(queued=0, batch_size=1)

    marked = await RollupRefresher(Engine(conn), batch_size=1).mark_dirty(date(2026, 9, 1), date(2026, 9, 3))  # type: ignore[arg-type]

    assert marked == 3 * 42
    assert conn.executed.count(rollups._MARK_RANGE) == 3
    assert conn.executed.count(rollups._REFRESH_STATEMENT_TIMEOUT) == 3
    assert conn.timeouts[rollups._MARK_RANGE] >= 300


@pytest.mark.asyncio
async def test_backlog_is_the_queue_size_and_oldest_age():
    assert await RollupRefresher(Engine(QueueConnection(3, 1)), batch_size=1).backlog() == (3, 12.5)  # type: ignore[arg-type]
    assert await RollupRefresher(Engine(QueueConnection(0, 1)), batch_size=1).backlog() == (0, None)  # type: ignore[arg-type]


def test_constants_in_sql_are_quoted():
    assert rollups._text_array(("it's",)) == "ARRAY['it''s']::text[]"


@pytest.mark.asyncio
async def test_recompute_statements_get_the_refresh_budget_not_the_pools_statement_limit():
    conn = QueueConnection(queued=1, batch_size=100)

    await RollupRefresher(Engine(conn), batch_size=100).refresh_batch(conn)  # type: ignore[arg-type]

    # The pool's client-side limit (statement timeout + 5 s) would otherwise cancel them first.
    assert all(conn.timeouts[statement] >= 300 for statement in RECOMPUTE)


def test_expired_keys_are_deleted_in_the_order_ingest_marks_them():
    drop = " ".join(rollups._DROP_EXPIRED_KEYS.split())
    assert "ORDER BY day, session_id FOR UPDATE" in drop


def test_keys_are_locked_in_the_order_ingest_marks_them_before_any_is_released():
    # Ingest upserts rollup_dirty rows sorted by (day, session_id); locking them in another
    # order could deadlock with it.
    lock = " ".join(rollups._LOCK_CLAIMED_KEYS.split())
    assert lock.endswith("ORDER BY d.day, d.session_id FOR UPDATE OF d")


class PoisonQueue:
    """rollup_dirty as the refresher sees it, with one key whose recompute always fails.

    Keys carry the columns the refresher keeps its timeout state in (`timeouts`, `alone`) and a
    due time; `now` is the clock tests patch into the refresher. Any number of refreshers (pods)
    can share one queue.
    """

    def __init__(self, sessions: list[str], poison: str, error: Exception | None = None) -> None:
        self.queue = {s: self._key(s) for s in sessions}
        self.poison = poison
        self.error = error or asyncpg.exceptions.NumericValueOutOfRangeError("bigint out of range")
        self.loaded: list[str] = []
        self.dropped: list[str] = []
        self.recomputed: list[str] = []
        self.deferred: list[str] = []
        self.failed_attempts = 0
        self.now = 0.0  # the refreshers' clock (time.monotonic), when a test patches it in
        self.slow_seconds = 0.0  # how long a failing recompute takes before it fails
        self.remarked_on_failure = False  # ingest marks the failing key again meanwhile

    @staticmethod
    def _key(session: str, version: int = 1) -> dict:
        day = date(2026, 9, 1)
        return {
            "day": day,
            "session_id": session,
            "kinds": 15,
            "version": version,
            "timeouts": 0,
            "alone": False,
            "due": 0.0,
        }

    def _due(self, alone: bool) -> list[dict]:
        return [k for k in self.queue.values() if k["alone"] == alone and k["due"] <= self.now]

    async def fetch(self, sql, limit):  # _READ_KEYS
        return self._due(alone=False)[:limit]

    async def fetchrow(self, sql, *args):  # _READ_ALONE_KEY
        due = self._due(alone=True)
        return due[0] if due else None

    async def execute(self, sql, *args, timeout=None):
        if sql == rollups._LOAD_KEYS:
            self.loaded = list(args[1])
        elif sql in RECOMPUTE and self.poison in self.loaded:
            self.failed_attempts += 1
            self.now += self.slow_seconds
            if self.remarked_on_failure:
                key = self.queue[self.poison]
                self.queue[self.poison] = {**key, "version": key["version"] + 1}
            raise self.error
        elif sql == rollups._FLAG_ALONE:
            for session in args[1]:
                if session in self.queue:
                    self.queue[session] = {**self.queue[session], "alone": True}
        elif sql == rollups._DEFER_KEY:
            key = self.queue.pop(args[1])  # to the back of the queue
            timeouts = key["timeouts"] + 1
            self.queue[args[1]] = {**key, "timeouts": timeouts, "alone": True, "due": self.now + timeouts * args[2]}
            self.deferred.append(args[1])
        elif sql == rollups._RELEASE_KEYS:
            self.recomputed += self.loaded
            for session in self.loaded:
                self.queue.pop(session, None)
        elif sql == rollups._DROP_KEY:
            if self.queue.pop(args[1], None) is None:
                return "DELETE 0"
            self.dropped.append(args[1])
        return "DELETE 1"

    @asynccontextmanager
    async def _tx(self):
        loaded = self.loaded
        try:
            yield
        except BaseException:
            self.loaded = loaded
            raise

    def transaction(self):
        return self._tx()


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.exceptions.NumericValueOutOfRangeError("bigint out of range"),
        asyncpg.exceptions.ProgramLimitExceededError("index row size exceeds btree maximum"),
    ],
)
@pytest.mark.asyncio
async def test_a_key_whose_data_cannot_be_recomputed_is_dropped_and_the_rest_are_refreshed(error):
    # Each fails again on the same data: retried as one batch, they would freeze every rollup.
    sessions = [f"s{i}" for i in range(10)]
    conn = PoisonQueue(sessions, poison="s6", error=error)

    with patch.object(rollups, "logger") as logger:
        await RollupRefresher(Engine(conn), batch_size=100).refresh()  # type: ignore[arg-type]

    assert sorted(conn.recomputed) == sorted(set(sessions) - {"s6"})
    assert conn.dropped == ["s6"]
    assert conn.queue == {}
    assert "s6" in logger.error.call_args.args[0]


STATEMENT_TIMEOUT = asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout")


@pytest.mark.asyncio
async def test_a_key_that_times_out_waits_at_the_back_of_the_queue_instead_of_being_dropped():
    # A timeout may be passing load: the key is retried later, and the rest are refreshed now.
    sessions = [f"s{i}" for i in range(10)]
    conn = PoisonQueue(sessions, poison="s6", error=STATEMENT_TIMEOUT)

    with patch.object(rollups, "logger") as logger:
        await RollupRefresher(Engine(conn), batch_size=100).refresh()  # type: ignore[arg-type]

    assert sorted(conn.recomputed) == sorted(set(sessions) - {"s6"})
    assert (conn.dropped, conn.deferred, list(conn.queue)) == ([], ["s6"], ["s6"])
    # Key by key after the batch timed out: two slow attempts, not one per halving.
    assert conn.failed_attempts == 2
    assert "s6" in logger.warning.call_args.args[0]


@pytest.mark.asyncio
async def test_a_key_that_keeps_timing_out_is_dropped_after_three_timeouts_in_a_row():
    conn = PoisonQueue(["s1", "s2"], poison="s1", error=STATEMENT_TIMEOUT)
    refresher = RollupRefresher(Engine(conn), batch_size=100)  # type: ignore[arg-type]

    with patch.object(rollups, "logger"):
        await refresher.refresh()
        await refresher.refresh()  # not due yet: nothing is tried
        assert (conn.dropped, conn.deferred, conn.failed_attempts) == ([], ["s1"], 2)
        for _ in range(2):
            conn.now += 3600  # due again
            await refresher.refresh()

    assert (conn.dropped, conn.deferred) == (["s1"], ["s1", "s1"])  # dropped at the third, not deferred


@pytest.mark.asyncio
async def test_a_key_recomputed_in_time_starts_counting_its_timeouts_again():
    conn = PoisonQueue(["s1"], poison="s1", error=STATEMENT_TIMEOUT)
    refresher = RollupRefresher(Engine(conn), batch_size=100)  # type: ignore[arg-type]

    with patch.object(rollups, "logger"):
        for poison in ("s1", "s1", "none", "s1", "s1"):  # the load passed once in between
            conn.poison = poison
            conn.now += 3600
            conn.queue.setdefault("s1", PoisonQueue._key("s1", version=2))
            await refresher.refresh()

    assert conn.dropped == []


@pytest.mark.asyncio
async def test_what_a_timeout_teaches_is_shared_by_every_pod():
    # A statement timeout (5 min) arrives after a run's deadline (2 min), and the next run is often
    # another pod's: it must take the batch's keys one at a time, not claim the batch again.
    sessions = [f"s{i}" for i in range(10)]
    conn = PoisonQueue(sessions, poison="s6", error=STATEMENT_TIMEOUT)
    conn.slow_seconds = 300
    pod_a, pod_b = (RollupRefresher(Engine(conn), batch_size=100, max_run_seconds=120) for _ in range(2))  # type: ignore[arg-type]

    with patch.object(rollups, "logger"), patch.object(rollups, "time", SimpleNamespace(monotonic=lambda: conn.now)):
        assert await pod_a.refresh() == 0  # the batch timed out: its keys are left to take one at a time
        assert await pod_b.refresh() == 6  # s0..s5 one by one, then s6 times out alone
        assert await pod_a.refresh() == 3  # s7..s9; s6 is not due for 10 minutes
        for pod in (pod_b, pod_a):  # due again, twice: its third timeout in a row drops it
            conn.now += 3600
            await pod.refresh()

    assert sorted(conn.recomputed) == sorted(set(sessions) - {"s6"})
    assert (conn.deferred, conn.dropped, conn.queue) == (["s6", "s6"], ["s6"], {})
    assert conn.failed_attempts == 1 + 3  # the batch once, then s6 alone three times


@pytest.mark.asyncio
async def test_a_key_marked_again_is_still_dropped_after_its_third_timeout_in_a_row():
    # A slow session is usually active: ingest marks it again while its recompute runs.
    conn = PoisonQueue(["s1"], poison="s1", error=STATEMENT_TIMEOUT)
    conn.remarked_on_failure = True
    refresher = RollupRefresher(Engine(conn), batch_size=100)  # type: ignore[arg-type]

    with patch.object(rollups, "logger") as logger:
        for _ in range(3):
            conn.now += 3600
            await refresher.refresh()

    assert (conn.dropped, conn.queue) == (["s1"], {})
    assert logger.error.call_count == 1


@pytest.mark.asyncio
async def test_a_transient_failure_drops_nothing_and_leaves_the_keys_for_the_next_run():
    conn = PoisonQueue(["s1", "s2"], poison="s1", error=asyncpg.exceptions.DeadlockDetectedError("deadlock"))

    with pytest.raises(asyncpg.exceptions.DeadlockDetectedError):
        await RollupRefresher(Engine(conn), batch_size=100).refresh()  # type: ignore[arg-type]

    assert conn.dropped == []
    assert set(conn.queue) == {"s1", "s2"}
