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

import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import asyncpg
import pytest

from codemie.repository.cli_analytics.postgres import dirty, rollups, session_derived
from codemie.repository.cli_analytics.postgres.dirty import RollupFamily
from codemie.repository.cli_analytics.postgres.rollups import RECOMPUTE, RollupRefresher


class QueueConnection:
    """A queue of `rollup_dirty` keys served in batches; records executed statements."""

    def __init__(self, queued: int, batch_size: int) -> None:
        self.queued = queued
        self.batch_size = batch_size
        self.executed: list[str] = []
        self.timeouts: dict[str, float | None] = {}
        self.transactions = 0

    async def fetch(self, sql: str, limit: object, timeout: float | None = None) -> list[dict[str, object]]:
        if sql != rollups._READ_KEYS:  # the session_dims rows the derived update reads: none here
            self.timeouts[sql] = timeout
            return []
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


def test_request_grouping_reads_as_far_as_usage_rows_mark_the_neighbour_day() -> None:
    # One constant: the recompute groups a request's lines within the reach of a day's edges, and a
    # usage_requests row within the same reach of midnight marks the neighbouring day.
    assert rollups.USAGE_REQUEST_REACH is dirty.USAGE_REQUEST_REACH
    assert timedelta(hours=1) == dirty.USAGE_REQUEST_REACH
    assert rollups._GROUP_REACH == "interval '3600 seconds'"  # 1 h = 3600 s
    assert (
        f"u.ts >= r.id_lo - {rollups._GROUP_REACH} AND u.ts < r.id_hi + {rollups._GROUP_REACH}"
        in rollups._BUILD_REQUESTS
    )


def test_an_identifier_pair_is_looked_for_across_midnight_within_the_shared_reach() -> None:
    # A pair by identifier on two days: tier 1 pairs records up to 1 h (USAGE_REQUEST_REACH)
    # past either side of midnight, in both directions; tier 2 keeps 5 s. No second literal of the reach.
    sql = rollups._BUILD_REQUESTS
    reach, match = rollups._GROUP_REACH, rollups._MATCH
    # both candidate sets, transcript requests and OTel records, reach 1 h past the day's edges
    assert f"{rollups._DAY_LO} - {reach} AS id_lo, {rollups._DAY_HI} + {reach} AS id_hi" in sql
    assert "HAVING max(u.ts) >= r.id_lo AND max(u.ts) < r.id_hi" in sql
    assert "l.ts >= r.id_lo AND l.ts < r.id_hi" in sql
    tier_1 = sql.split("t1 AS (", 1)[1].split("\n),", 1)[0]
    # each record within 1 h of the other one's day: both lie within 1 h of the midnight between them
    assert f"b.ts >= {rollups._utc('a.t_day')} - {reach} AND b.ts < {rollups._utc('a.t_day + 1')} + {reach}" in tier_1
    assert f"a.ts >= {rollups._utc('b.o_day')} - {reach} AND a.ts < {rollups._utc('b.o_day + 1')} + {reach}" in tier_1
    assert match not in tier_1
    # tier 2 stays within the day widened by 5 s, on both sides
    tier_2 = sql.split("\nc AS (", 1)[1].split("\n),", 1)[0]
    assert "a.ts >= a.lo AND a.ts < a.hi AND b.ts >= b.lo AND b.ts < b.hi" in tier_2
    assert f"b.ts >= a.ts - {match} AND b.ts <= a.ts + {match}" in tier_2
    # an OTel record paired with a transcript request of the neighbour day is no unmatched row of its own day
    unmatched = sql.rsplit("UNION ALL", 1)[1]
    assert "WHERE b.o_day = b.day" in unmatched
    assert (
        "NOT EXISTS (SELECT 1 FROM p WHERE p.day = b.day AND p.session_id = b.session_id AND p.oid = b.oid)"
        in unmatched
    )
    assert "SELECT day, session_id, tid, oid FROM t1" in sql
    assert "3600" not in sql.replace(reach, "")  # the reach has one literal, the shared constant's


def test_client_text_is_bounded_before_it_becomes_a_key_column() -> None:
    # attrs / usage[] text and uncapped columns that reach a primary-key column of cost_daily or
    # session_usage* are skipped above _MAX_KEY_BYTES (256) bytes ('' = unknown, as every other key path), never
    # truncated.
    bound = rollups._MAX_KEY_BYTES
    assert bound == 256
    requests = rollups._BUILD_REQUESTS
    for value in ("l.attrs->>'speed'", "l.attrs->>'agent.name'", "u.user_email"):
        assert f"CASE WHEN octet_length({value}) <= {bound} THEN {value} END" in requests, value
    assert "l.attrs->>'speed' = 'normal'" not in requests  # only the bounded value is normalised
    totals = rollups._TOTALS_ROWS
    for key in ("model", "speed", "inference_geo", "scope_kind", "scope_name"):
        value = f"e.item->>'{key}'"
        assert f"CASE WHEN octet_length({value}) <= {bound} THEN {value} END" in totals, key
    # an agent id too long for a key column is no subagent (as in subagent_invocations): its totals are skipped
    assert f"octet_length(coalesce(h.agent_id, '')) <= {bound}" in totals
    assert "left(" not in requests + totals


def test_request_derived_rows_of_the_agent_disqualify_its_totals() -> None:
    # Once raw retention purges an agent's usage_requests, the session_usage rows built from them
    # (kept on session retention) still disqualify its totals, which would otherwise count the tokens twice.
    no_request_rows = (
        "NOT EXISTS (SELECT 1 FROM session_usage su WHERE su.session_id = v.session_id AND su.agent_id = v.agent_id "
        "AND coalesce(su.attrs->>'token_source', '') <> 'subagent_usage')"
    )
    rule = rollups._totals_are_a_source("v.session_id", "v.agent_id")
    assert no_request_rows in rule
    assert "FROM usage_requests u" in rule and "FROM log_events l" in rule  # the two raw-record tests stay
    assert rule in rollups._TOTALS_ROWS  # inserted only when a source
    # and a stored totals row that no longer qualifies is deleted on whatever day it is (delete-only)
    assert f"NOT ({rollups._totals_are_a_source('t.session_id', 't.agent_id')})" in rollups._SESSION_USAGE[0]


def test_every_rollup_table_is_recomputed():
    targets = {s.split()[2] for s in RECOMPUTE if s.lstrip().startswith("INSERT INTO")}
    targets -= {t for t in targets if t.startswith("_")}  # per-recompute temp tables, not rollups

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
        "session_usage",
        "session_usage_hourly",
        "subagent_invocations",
    }


def test_usage_tables_are_recomputed_per_day_and_hour() -> None:
    # session_usage per (day, session); session_usage_hourly deletes the session's hours
    # of the day and regroups the same requests by their UTC hour. Row contents are proven live.
    targets = {s.split()[2] for s in RECOMPUTE if s.lstrip().startswith("INSERT INTO")}
    hourly_deletes = [s for s in RECOMPUTE if s.lstrip().startswith("DELETE FROM session_usage_hourly ")]
    hourly_inserts = [s for s in RECOMPUTE if s.lstrip().startswith("INSERT INTO session_usage_hourly ")]

    assert {"session_usage", "session_usage_hourly"} <= targets
    assert len(hourly_deletes) == 1 and len(hourly_inserts) == 1
    assert "t.session_id = k.session_id" in hourly_deletes[0]
    assert f"t.hour >= {rollups._DAY_LO} AND t.hour < {rollups._DAY_HI}" in hourly_deletes[0]
    group_by = hourly_inserts[0].rsplit("GROUP BY", 1)[1]
    assert "date_trunc('hour', r.ts, 'UTC')" in group_by
    # Built from the requests, never from the daily table nor from subagent totals (no invented hour).
    assert "FROM _cli_requests r" in hourly_inserts[0]
    assert "session_usage " not in hourly_inserts[0].split("(", 1)[1]
    assert "hook_events" not in hourly_inserts[0]


def test_cost_daily_insert_lists_the_ten_key_columns_and_split_cache() -> None:
    # cost_daily's key is the original five columns plus the five new identity columns, and the
    # 5m/1h cache split is written too. It groups by exactly those ten. Row contents are proven live.
    inserts = [s for s in RECOMPUTE if s.lstrip().startswith("INSERT INTO cost_daily ")]
    assert len(inserts) == 1
    columns = [c.strip() for c in inserts[0].split("(", 1)[1].split(")", 1)[0].split(",")]
    key = [
        "day",
        "session_id",
        "user_email",
        "model_name",
        "query_source",
        "speed",
        "inference_geo",
        "scope_kind",
        "scope_name",
        "agent_type",
    ]

    assert columns[:10] == key
    assert {"cache_creation_5m_tokens", "cache_creation_1h_tokens"} <= set(columns[10:])
    assert len(columns) == len(set(columns)) == 10 + 8  # 6 original measures + the 5m/1h split
    group_by = inserts[0].rsplit("GROUP BY", 1)[1]
    assert [g.strip() for g in group_by.split(",")] == [str(i) for i in range(1, 11)]


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
    # ... and the read of the derived-column update, the last statement before the keys are released
    assert conn.timeouts[session_derived._READ] == rollups._REFRESH_CLIENT_TIMEOUT_S


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

    def __init__(self, sessions: list[str], poison: str, error: Exception | None = None, kinds: int = 15) -> None:
        self.queue = {s: {**self._key(s), "kinds": kinds} for s in sessions}
        self.loaded_kinds: list[int] = []
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

    async def fetch(self, sql: str, limit: object, timeout: float | None = None) -> list[dict[str, object]]:
        if sql != rollups._READ_KEYS:  # the session_dims rows the derived update reads: none here
            return []
        return self._due(alone=False)[:limit]

    async def fetchrow(self, sql, *args):  # _READ_ALONE_KEY
        due = self._due(alone=True)
        return due[0] if due else None

    async def execute(self, sql, *args, timeout=None):
        if sql == rollups._LOAD_KEYS:
            self.loaded = list(args[1])
            self.loaded_kinds = list(args[2])
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


def test_rollup_families_name_all_six_bits() -> None:
    names = (rollups._LOG, rollups._DIMS, rollups._SPANS, rollups._METRICS, rollups._USAGE, rollups._SESSION)

    assert names == (1, 2, 4, 8, 16, 32)
    assert all(type(bit) is int for bit in names)
    assert (int(RollupFamily.USAGE_FACTS), int(RollupFamily.SESSION)) == (rollups._USAGE, rollups._SESSION)


def _target_table(statement: str) -> str:
    match = re.match(r"\s*(?:INSERT INTO|DELETE FROM|UPDATE)\s+(\w+)", statement)
    assert match, statement
    return match.group(1)


def test_each_statement_gates_on_its_trigger_bits() -> None:
    # Every statement of RECOMPUTE is gated on exactly the bits of one _TRIGGER_BITS entry of its
    # table (`<table>` or `<table>/<qualifier>`), and every entry gates some statement.
    used: set[str] = set()
    for statement in RECOMPUTE:
        table = _target_table(statement)
        entries = {k: v for k, v in rollups._TRIGGER_BITS.items() if k == table or k.startswith(table + "/")}
        bits = 0
        for gate in re.findall(r"kinds & (\d+)", statement):  # every `kinds & <n>` the gate tests
            bits |= int(gate)
        matching = [k for k, v in entries.items() if v == bits]
        assert matching, f"{table}: gate bits {bits} match no _TRIGGER_BITS entry {entries}"
        used.update(matching)
    assert used == set(rollups._TRIGGER_BITS)


def test_request_matching_runs_before_every_token_table() -> None:
    # cost_daily and session_usage* read the matched requests from the temp table
    # _cli_requests: it is built first, for every key carrying LOG_FACTS or USAGE_FACTS.
    targets = [_target_table(statement) for statement in RECOMPUTE]
    build = targets.index("_cli_requests")
    readers = [i for i, t in enumerate(targets) if t == "cost_daily" or t.startswith("session_usage")]

    assert readers and all(build < i for i in readers)
    assert targets.count("_cli_requests") == 1
    gates = {int(g) for g in re.findall(r"kinds & (\d+)", RECOMPUTE[build])}
    assert gates == {rollups._LOG | rollups._USAGE}
    assert rollups._LOG | rollups._USAGE == 1 | 16
    assert rollups._TRIGGER_BITS["_cli_requests"] == rollups._LOG | rollups._USAGE


@pytest.mark.asyncio
async def test_the_requests_table_is_created_like_the_keys_table_before_the_recompute() -> None:
    # Transaction-scoped: created once per connection, emptied at every commit, never shared.
    conn = QueueConnection(queued=1, batch_size=100)

    await RollupRefresher(Engine(conn), batch_size=100).refresh_batch(conn)  # type: ignore[arg-type]

    create = rollups._CREATE_REQUESTS
    assert create.startswith("CREATE TEMP TABLE IF NOT EXISTS _cli_requests ")
    assert create.endswith("ON COMMIT DELETE ROWS")
    assert conn.executed.index(rollups._CREATE_KEYS) < conn.executed.index(create) < conn.executed.index(RECOMPUTE[0])


@pytest.mark.parametrize("kinds", [16, 32, 48])
@pytest.mark.asyncio
async def test_a_key_carrying_only_the_usage_or_session_bits_is_claimed_and_released(kinds: int) -> None:
    # Control flow on a fake queue; that no rollup row changes is proved live (Task 10), not here.
    conn = PoisonQueue(["s1"], poison="none", kinds=kinds)

    total = await RollupRefresher(Engine(conn), batch_size=100).refresh()  # type: ignore[arg-type]

    assert total == 1
    assert conn.loaded_kinds == [kinds]
    assert conn.recomputed == ["s1"]  # reached the release statement
    assert conn.queue == {}


def _gate_bits(statement: str) -> int:
    bits = 0
    for gate in re.findall(r"kinds & (\d+)", statement):
        bits |= int(gate)
    return bits


class GateQueue(PoisonQueue):
    """PoisonQueue that also records which RECOMPUTE statements select rows for the loaded key: a
    statement runs for a key when one of its `kinds & <n> <> 0` tests passes for the key's mask."""

    def __init__(self, kinds: int) -> None:
        super().__init__(["s1"], poison="none", kinds=kinds)
        self.ran: list[str] = []

    async def execute(self, sql: str, *args: object, timeout: float | None = None) -> str:
        if sql in RECOMPUTE and any(int(n) & self.loaded_kinds[0] for n in re.findall(r"kinds & (\d+) <> 0", sql)):
            self.ran.append(sql)
        return await super().execute(sql, *args, timeout=timeout)


@pytest.mark.parametrize(("kinds", "runs"), [(1, True), (4, True), (8, True), (32, True), (2, False)])
@pytest.mark.asyncio
async def test_session_statements_follow_dimensions_and_run_for_bits_1_4_8_32(kinds: int, runs: bool) -> None:
    # The SESSION statements read the rollups and run after DIMENSIONS, for the session of
    # every key with bit 1, 4, 8 or 32. A key with bit 32 only (e.g. a session_attributes record of a
    # session with no rollups) runs them too; DIMENSIONS alone (bit 2) runs none. SQL results are live.
    session = [s for s in RECOMPUTE if _gate_bits(s) & rollups._SESSION]
    dimensions = [
        i for i, s in enumerate(RECOMPUTE) if _target_table(s) == "session_dims" and _gate_bits(s) == rollups._DIMS
    ]
    assert session
    assert len(dimensions) == 1
    assert all(RECOMPUTE.index(s) > dimensions[0] for s in session)
    assert all(_gate_bits(s) == 1 | 4 | 8 | 32 for s in session)
    conn = GateQueue(kinds)

    total = await RollupRefresher(Engine(conn), batch_size=100).refresh()  # type: ignore[arg-type]

    assert total == 1 and conn.queue == {}
    assert [s for s in session if s in conn.ran] == (session if runs else [])


@pytest.mark.asyncio
async def test_recompute_updates_derived_columns_after_session_statements() -> None:
    # feature_id and delivery_framework are derived from what the SESSION statements just
    # wrote, so the derived update runs after every RECOMPUTE statement and before the keys are
    # released, for the sessions of the keys with bit 1, 4, 8 or 32 (not DIMENSIONS alone, not '').
    conn = QueueConnection(queued=0, batch_size=100)
    keys = [
        {"day": date(2026, 9, 1), "session_id": "s2", "kinds": 32, "version": 1},
        {"day": date(2026, 9, 1), "session_id": "s1", "kinds": 4, "version": 1},
        {"day": date(2026, 9, 2), "session_id": "s1", "kinds": 1, "version": 1},
        {"day": date(2026, 9, 1), "session_id": "s3", "kinds": 2, "version": 1},
        {"day": date(2026, 9, 1), "session_id": "", "kinds": 8, "version": 1},
    ]
    calls: list[tuple[object, ...]] = []

    async def derived(
        connection: object, session_ids: list[str], classify: Callable[[list[str]], str] | None, timeout: float
    ) -> None:
        calls.append((connection, session_ids, classify, timeout, len(conn.executed)))

    def classify(skill_names: list[str]) -> str:
        return "Pure chat"

    refresher = RollupRefresher(Engine(conn), batch_size=100, classify=classify)  # type: ignore[arg-type]
    with patch.object(rollups, "update_session_derived", derived):
        assert await refresher.recompute(conn, keys) == 5  # type: ignore[arg-type]

    assert len(calls) == 1
    connection, session_ids, handed, timeout, position = calls[0]
    assert connection is conn and handed is classify
    assert timeout == rollups._REFRESH_CLIENT_TIMEOUT_S  # the refresh budget, as every other statement
    assert session_ids == ["s1", "s2"]
    assert conn.executed[position - len(RECOMPUTE) : position] == RECOMPUTE  # after the SESSION statements
    assert conn.executed[position:] == [rollups._LOCK_CLAIMED_KEYS, rollups._RELEASE_KEYS, rollups._RESTAMP_KEYS]


def test_subagent_invocations_is_upserted_by_session() -> None:
    # subagent_invocations is keyed (session_id, agent_id) and upserted (never DELETE + INSERT)
    # by the SESSION family, for the session of every key with bit 1, 4, 8 or 32 (1|4|8|32 = 45), after
    # every rollup and DIMENSIONS in the same transaction. Row contents are proven live (Task 17).
    targets = [_target_table(s) for s in RECOMPUTE]
    statements = [s for s in RECOMPUTE if _target_table(s) == "subagent_invocations"]
    rollup_tables = {
        "_cli_requests",
        "cost_daily",
        "session_usage",
        "session_usage_hourly",
        "lines_daily",
        "active_time_daily",
        "turns_daily",
        "tool_facts_daily",
        "session_files_daily",
        "invocations_hourly",
        "session_skills",
    }

    assert len(statements) == 1
    upsert = statements[0]
    assert upsert.lstrip().startswith("INSERT INTO subagent_invocations ")
    assert "ON CONFLICT (session_id, agent_id) DO UPDATE SET" in upsert
    assert _gate_bits(upsert) == 1 | 4 | 8 | 32 == 45
    assert rollups._TRIGGER_BITS["subagent_invocations"] == 45
    position = RECOMPUTE.index(upsert)
    assert all(i < position for i, t in enumerate(targets) if t in rollup_tables)
    dimensions = [i for i, s in enumerate(RECOMPUTE) if targets[i] == "session_dims" and _gate_bits(s) == 2]
    assert dimensions and dimensions[0] < position


# A (day, session) pair is a fallback pair when its UTC day has no span of kind TOOL (1),
# TOOL_EXECUTION (2) or INTERACTION (3); the test is on span_kind, never on span_name.
_NO_SPAN_OF_KIND = (
    "NOT EXISTS (SELECT 1 FROM spans s WHERE s.session_id = k.session_id AND s.span_kind IN (1, 2, 3) "
    f"AND s.ts >= {rollups._DAY_LO} AND s.ts < {rollups._DAY_HI})"
)


def test_span_facts_statements_read_hooks_for_pairs_without_tool_or_interaction_spans() -> None:
    # Each SPAN_FACTS INSERT keeps its spans branch and gains hook_events branches that run only on a
    # fallback pair, so spans and hooks are never added together. Row contents are proven live (Task 17).
    inserts = {
        _target_table(s): s
        for s in RECOMPUTE
        if s.lstrip().startswith("INSERT INTO") and _gate_bits(s) & rollups._SPANS
    }
    # table -> (hook branches, hook event types it reads)
    fallback = {
        "turns_daily": (1, ("agent.prompt.submit",)),
        "tool_facts_daily": (1, ("agent.tool.start", "agent.subagent.start", "agent.skill.dispatch")),
        "session_files_daily": (1, ("agent.tool.start",)),
        # kinds 1, 2 and 3; kind 1 success reads agent.tool.end
        "invocations_hourly": (
            3,
            ("agent.tool.start", "agent.tool.end", "agent.subagent.start", "agent.skill.dispatch"),
        ),
    }

    for table, (branches, events) in fallback.items():
        insert = inserts[table]
        assert " spans s" in insert or " spans t" in insert, table  # a pair with spans keeps them
        assert insert.count("JOIN hook_events h ON") == branches, table
        assert insert.count(_NO_SPAN_OF_KIND) == branches, table
        assert "span_name" not in insert, table
        for event in events:
            assert f"'{event}'" in insert, (table, event)
        assert _gate_bits(insert) == rollups._TRIGGER_BITS[table]

    # success = agent.tool.end of the same session and tool_use_id, on the start's day; '' never pairs.
    kind_1 = inserts["invocations_hourly"]
    assert "x.session_id = k.session_id AND x.event_type = 'agent.tool.end' AND x.tool_use_id = h.tool_use_id" in kind_1
    assert "ON h.tool_use_id <> ''" in kind_1
    # File flags come from agent.tool.start's file_path (stored in attrs) and follow the tool name.
    files = inserts["session_files_daily"]
    assert "h.attrs->'file_path'" in files
    assert "h.tool_name = ANY(" in files
    # A hook skill is counted where it is also a row of kind 2: both statements test the same name.
    hook_skill = (
        "(h.event_type = 'agent.tool.start' OR h.event_type = 'agent.skill.dispatch') "
        f"AND {rollups._hook_key('h.skill_name')}"
    )
    assert f"count(*) FILTER (WHERE {hook_skill})" in inserts["tool_facts_daily"]
    assert f"AND {hook_skill}" in kind_1


def test_mark_range_yields_the_ingest_bits_per_source() -> None:
    sql = rollups._MARK_RANGE
    inner = sql.split("FROM (", 1)[1]
    branches = {b.split("FROM", 1)[1].split()[0]: b for b in inner.split("UNION ALL")}
    # usage_requests: LOG 1 + USAGE 16 + SESSION 32 (the session bit only for a non-empty session_id)
    assert set(branches) >= {"usage_requests", "log_events", "hook_events", "spans", "metric_points"}
    assert "1 | 16 | CASE WHEN session_id <> '' THEN 32" in branches["usage_requests"]
    # hooks: the six fallback types mark bit 4 on `day` and on (ts - 1 h)::date
    for hook_type in (
        "agent.prompt.submit",
        "agent.tool.start",
        "agent.tool.end",
        "agent.tool.error",
        "agent.skill.dispatch",
        "agent.subagent.start",
    ):
        assert f"'{hook_type}'" in sql
    assert "((ts - interval '1 hour') AT TIME ZONE 'UTC')::date" in sql
    # spans: own day, and for tool, execution and interaction spans (kinds 1, 2, 3) the day of ts - 1 h too,
    # as dirty_keys marks them: a span in the first hour after the range still queues the range's last day
    shifted_spans = [b for b in inner.split("UNION ALL") if " FROM spans" in b and "interval '1 hour'" in b]
    assert len(shifted_spans) == 1
    assert f"SELECT ((ts - interval '1 hour') AT TIME ZONE 'UTC')::date, session_id, {rollups._SPANS} FROM spans" in (
        " ".join(shifted_spans[0].split())
    )
    assert f"WHERE {rollups._RANGE_SHIFTED} AND span_kind IN (1, 2, 3)" in " ".join(shifted_spans[0].split())
    assert "'agent.subagent.usage'" in sql
    # requested mask: default includes 16 and 32
    assert rollups._ALL_BITS & 16 and rollups._ALL_BITS & 32
    assert "& $3" in sql
    # Neighbour days, as ingest marks them (dirty.py): a usage_requests row within 1 h of
    # midnight, an api_request within 1 h with a non-empty request_id or within 5 s without one, mark the day
    # on the other side with the same bits as their own day (1 | 16 | 32).
    by_table: dict[str, list[str]] = {}
    for branch in inner.split("UNION ALL"):
        by_table.setdefault(branch.split("FROM", 1)[1].split()[0], []).append(branch)
    reach, match = rollups._GROUP_REACH, rollups._MATCH
    usage_neighbour = rollups._neighbour_day(reach)
    api_neighbour = rollups._neighbour_day(f"CASE WHEN request_id <> '' THEN {reach} ELSE {match} END")
    assert [b for b in by_table["usage_requests"] if usage_neighbour in b] != []
    api_branch = next(b for b in by_table["log_events"] if api_neighbour in b)
    assert "1 | 16 | CASE WHEN session_id <> '' THEN 32 ELSE 0 END" in api_branch
    assert "event_kind = 1" in api_branch  # EventKind.API_REQUEST
    # the rule of dirty._mark_near_midnight: bounds inclusive, the day before or after
    day_start = "((ts AT TIME ZONE 'UTC')::date::timestamp AT TIME ZONE 'UTC')"
    assert usage_neighbour == (
        f"CASE WHEN ts - {day_start} <= {reach} THEN (ts AT TIME ZONE 'UTC')::date - 1 "
        f"WHEN {day_start} + interval '1 day' - ts <= {reach} THEN (ts AT TIME ZONE 'UTC')::date + 1 END"
    )
    # clamped to raw retention ($4, NULL without it); a row far from midnight queues no neighbour
    assert "WHERE raw.day IS NOT NULL AND ($4::date IS NULL OR raw.day >= $4::date)" in sql


@pytest.mark.asyncio
async def test_mark_dirty_never_queues_a_day_past_raw_retention() -> None:
    # today 2026-09-29 - 30 days of raw retention = 2026-08-30; a range starting 2026-08-28 starts there
    args: list[tuple[object, ...]] = []

    class Recording(QueueConnection):
        async def execute(self, sql: str, *a: object, timeout: float | None = None) -> str:
            if sql == rollups._MARK_RANGE:
                args.append(a)
            return await super().execute(sql, *a, timeout=timeout)

    conn = Recording(queued=0, batch_size=1)
    refresher = RollupRefresher(Engine(conn), batch_size=1, raw_retention_days=30, clock=lambda: date(2026, 9, 29))  # type: ignore[arg-type]

    marked = await refresher.mark_dirty(date(2026, 8, 28), date(2026, 9, 1))

    assert [a[0] for a in args] == [date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 1)]
    assert marked == 3 * 42
    assert all(a[2] == rollups._ALL_BITS for a in args)
    # the neighbour days are clamped at the same edge: 2026-09-29 - 30 days = 2026-08-30
    assert all(a[3] == date(2026, 8, 30) for a in args)


@pytest.mark.asyncio
async def test_mark_dirty_without_raw_retention_clamps_no_neighbour_day() -> None:
    args: list[tuple[object, ...]] = []

    class Recording(QueueConnection):
        async def execute(self, sql: str, *a: object, timeout: float | None = None) -> str:
            if sql == rollups._MARK_RANGE:
                args.append(a)
            return await super().execute(sql, *a, timeout=timeout)

    conn = Recording(queued=0, batch_size=1)
    await RollupRefresher(Engine(conn), batch_size=1).mark_dirty(date(2026, 9, 1), date(2026, 9, 1))  # type: ignore[arg-type]

    assert args == [(date(2026, 9, 1), date(2026, 9, 1), rollups._ALL_BITS, None)]


def test_otel_usage_rows_of_the_session_disqualify_subagent_totals() -> None:
    # session_usage rows built from OTel records (matched, service or OTel-only requests) carry
    # attrs.otel_requests; while they exist on any day and any agent, totals would count the same tokens twice.
    guard = rollups._otel_usage_exists("v.session_id")
    assert guard == (
        "NOT EXISTS (SELECT 1 FROM session_usage su2 WHERE su2.session_id = v.session_id "
        "AND su2.attrs ? 'otel_requests')"
    )
    rule = rollups._totals_are_a_source("v.session_id", "v.agent_id")
    assert guard in rule
    assert "FROM usage_requests u" in rule and "FROM log_events l" in rule  # the three earlier clauses stay
    assert "su.agent_id = v.agent_id" in rule
    assert rule in rollups._TOTALS_ROWS
    stale_delete = rollups._SESSION_USAGE[0]
    assert rollups._otel_usage_exists("t.session_id") in stale_delete


def test_session_usage_counts_otel_requests_including_matched_ones() -> None:
    insert = " ".join(" ".join(rollups._SESSION_USAGE[1:]).split())
    # a matched request (transcript tokens, OTel cost) counts too, so the filter names all three kinds
    assert "r.req_kind IN ('matched', 'service', 'otel_only')" in insert
    assert "jsonb_build_object('otel_requests', sum(x.otel))" in insert
    assert "bool_and(x.totals)" in insert and rollups._TOTALS_ATTRS in insert
    assert "'otel_requests', 'otel" not in insert  # otel_requests is a count, never a token_source value


def test_the_otel_requests_marker_is_set_only_when_the_row_has_otel_requests() -> None:
    # Without `WHEN sum(x.otel) > 0` every non-totals row would carry {"otel_requests": 0}; that marks the
    # session as OTel-counted, disqualifies all its subagent totals and the stale-totals DELETE removes them.
    insert = " ".join(" ".join(rollups._SESSION_USAGE[1:]).split())
    assert (
        f"CASE WHEN bool_and(x.totals) THEN {rollups._TOTALS_ATTRS} "
        "WHEN sum(x.otel) > 0 THEN jsonb_build_object('otel_requests', sum(x.otel)) END FROM ("
    ) in insert
    # totals rows contribute no OTel request, so a totals-only group never gets the marker
    totals = " ".join(rollups._TOTALS_ROWS.split())
    assert "NULL::bigint, NULL::bigint, true, 0 FROM (" in totals


def _int32(value: str) -> str:
    return f"CASE WHEN {value} BETWEEN -2147483648 AND 2147483647 THEN {value} END"


def test_sums_written_to_int_columns_of_the_usage_tables_are_bounded() -> None:
    # api_calls and the two web request counts are int columns, and one request may carry up to 10^9 of
    # each: three requests of a key would overflow the INSERT (class 22) and the key would be dropped with
    # every rollup of its transaction. A sum past the column is written as NULL instead.
    daily = " ".join(" ".join(rollups._SESSION_USAGE[1:]).split())
    hourly = " ".join(" ".join(rollups._SESSION_USAGE_HOURLY).split())
    assert rollups._fits("x") == _int32("x")

    for value in ("sum(x.api_calls)", "sum(x.web_search_requests)", "sum(x.web_fetch_requests)"):
        assert _int32(value) in daily, value
        assert daily.count(value) == 2, value  # only inside the bound: once in its test, once as its value
    for value in ("sum(r.web_search_requests)", "sum(r.web_fetch_requests)", "count(*)"):
        assert _int32(value) in hourly, value
        assert hourly.count(value) == 2, value


def test_subagent_totals_come_from_the_latest_event_that_carries_them() -> None:
    # An agent.subagent.usage event without usage[] rows (absent, not an array, empty, no object in it)
    # is no totals event: were it chosen as the latest, the rows written from an earlier event would be
    # deleted with nothing to insert in their place.
    carries_totals = rollups._has_totals("h.attrs")
    assert carries_totals == (
        "EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(h.attrs->'usage') = 'array' "
        "THEN h.attrs->'usage' END) i (item) WHERE jsonb_typeof(i.item) = 'object')"
    )
    latest_event = " ".join(rollups._TOTALS_ROWS.split("FROM (", 1)[1].split(") v", 1)[0].split())
    assert f"WHERE {carries_totals} ORDER BY h.session_id, coalesce(h.agent_id, ''), h.ts DESC" in latest_event
    # the stale-row delete compares with the day of the same event
    assert f"AND {carries_totals} ORDER BY h.ts DESC" in rollups._latest_totals_day("t.session_id", "t.agent_id")
    assert rollups._latest_totals_day("t.session_id", "t.agent_id") in rollups._SESSION_USAGE[0]


def test_a_transcript_request_is_written_only_on_its_own_day() -> None:
    # A day reads transcript requests up to 1 h past its edges to group and pair them. The matched and
    # the transcript-only rows are then kept only when the request's own UTC day is the key's day:
    # without the filter a request near midnight is written on both days and counted twice.
    sql = rollups._BUILD_REQUESTS
    assert "(tr.ts AT TIME ZONE 'UTC')::date AS t_day" in sql
    matched, transcript, _unmatched = (" ".join(b.split()) for b in sql.rsplit("\n)\n", 1)[1].split("UNION ALL"))
    assert "'matched'" in matched and matched.endswith("WHERE a.t_day = a.day")
    assert "'transcript'" in transcript
    assert "FROM t a WHERE a.t_day = a.day AND NOT EXISTS (SELECT 1 FROM p WHERE" in transcript


def test_the_fingerprint_compares_the_raw_model_and_the_token_counts() -> None:
    # Tier 2 pairs a transcript request with an OTel record that has no shared identifier. The OTel
    # model is compared with the transcript's raw model (the normalised one never equals it); a wrong
    # comparison pairs nothing, and the request is then written twice: as transcript and as service row.
    tier_2 = " ".join(rollups._BUILD_REQUESTS.split("\nc AS (", 1)[1].split("\n),", 1)[0].split())
    assert "AND b.model = a.model_raw AND" in tier_2
    assert "b.model = a.model " not in tier_2
    for otel, transcript in (
        ("b.input_tokens", "coalesce(a.input_tokens, 0)"),
        ("b.output_tokens", "coalesce(a.output_tokens, 0)"),
        ("b.cache_read_tokens", "coalesce(a.cache_read_tokens, 0)"),
        ("b.cache_creation_tokens", "coalesce(a.cache_5m, 0) + coalesce(a.cache_1h, 0)"),
    ):
        assert f"coalesce({otel}, 0) = {transcript}" in tier_2, otel


def _plugin_model(raw: str) -> str:
    """The affixes of rollups._MODEL_AFFIXES applied the way the SQL applies them."""
    name = raw.lower().strip()
    for affix in rollups._MODEL_AFFIXES:
        name = re.sub(affix, "", name)
    return name


def test_an_otel_request_without_a_transcript_pair_is_stored_under_the_normalised_model() -> None:
    # A matched or transcript-only request takes the plugin's normalised `model`. A service or OTel-only
    # request has only the OTel name, which carries the date suffix: written as it is, one model would
    # have two `model` values in session_usage and its totals would be split.
    for raw, model in (
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("claude-sonnet-5", "claude-sonnet-5"),
        # each step runs once, in the plugin's order: the date goes before the version, so this one keeps it
        ("us.anthropic.claude-opus-5-5-20260101-v1:0", "claude-opus-5-5-20260101"),
        ("vertex/claude-opus-5-5@20260101", "claude-opus-5-5"),
        ("anthropic.claude-sonnet-4-6@1", "claude-sonnet-4-6"),
        (" Claude-Opus-5-5_20260101 ", "claude-opus-5-5"),
    ):
        assert _plugin_model(raw) == model, raw
    normalised = rollups._normalised_model("b.model")
    assert normalised.startswith("regexp_replace(" * len(rollups._MODEL_AFFIXES))
    assert "regexp_replace(lower(b.model), '^[[:space:]]+|[[:space:]]+$', '', 'g')" in normalised
    assert [normalised.index(f"'{affix}'") for affix in rollups._MODEL_AFFIXES] == sorted(
        normalised.index(f"'{affix}'") for affix in rollups._MODEL_AFFIXES
    )
    # `model` is normalised, `model_name` (the cost_daily key) keeps the OTel name
    unmatched = rollups._BUILD_REQUESTS.rsplit("UNION ALL", 1)[1]
    assert f"coalesce({normalised}, ''), coalesce(b.model, '')," in unmatched
    assert rollups._BUILD_REQUESTS.count(normalised) == 1


def test_the_summary_time_and_commands_are_stored_only_from_a_summary() -> None:
    # The reader counts a session's slash commands from session_dims.commands when summary_ts is set
    # and from its OTel records otherwise. Both columns therefore come from a summary alone, and a
    # stored summary is replaced only by one that is not older.
    values = dict(rollups._COUNTER_VALUES)
    assert values["summary_ts"] == "sm.ts"
    assert values["commands"] == (
        "CASE WHEN sm.ts IS NOT NULL THEN CASE WHEN jsonb_typeof(sm.attrs->'commands') = 'array' "
        "THEN sm.attrs->'commands' END ELSE NULL::jsonb END"
    )
    wins, fallback = rollups._SUMMARY_WINS, rollups._FALLBACK_RUNS
    assert "EXCLUDED.summary_ts >= sd.summary_ts" in wins
    summary_ts = (
        f"summary_ts = CASE WHEN {wins} THEN coalesce(EXCLUDED.summary_ts, sd.summary_ts) "
        f"WHEN {fallback} THEN sd.summary_ts ELSE sd.summary_ts END"
    )
    commands = (
        f"commands = CASE WHEN {wins} OR {fallback} THEN coalesce(EXCLUDED.commands, sd.commands) ELSE sd.commands END"
    )
    assert rollups._counter_set("summary_ts") == summary_ts
    assert rollups._counter_set("commands") == commands
    upsert = rollups._SESSION_DIMS
    assert upsert in RECOMPUTE
    columns = [c.strip() for c in upsert.split("(", 1)[1].split(")", 1)[0].split(",")]
    assert {"summary_ts", "commands"} <= set(columns)
    assert summary_ts in upsert and commands in upsert


def test_a_client_number_is_cast_only_with_a_bounded_fraction() -> None:
    # The text of a client number is cast to numeric, which holds at most 16,383 digits after the point:
    # a longer fraction would raise on the cast (class 22) and the key would be dropped with its rollups.
    assert rollups._MAX_FRACTION_DIGITS == 30
    number = "'^[0-9]{1,9}([.][0-9]{1,30})?$'"
    assert rollups._whole_number("v", 9) == f"CASE WHEN v ~ {number} THEN trunc(v::numeric)::bigint END"
    # 10^9 has ten digits: a duration of at most nine is read
    assert rollups._attr_ms("l", "duration_ms") == rollups._whole_number("(l.attrs->>'duration_ms')", 9)
    assert rollups._json_count("sm.attrs", "turns", 9) == rollups._whole_number("(sm.attrs->>'turns')", 9)
    # and no statement of the recompute matches an open-ended fraction before such a cast
    assert [st for st in (*RECOMPUTE, rollups._BUILD_REQUESTS) if "[0-9]+)?$" in st] == []
    assert [st for st in (*RECOMPUTE, rollups._BUILD_REQUESTS) if number in st] != []


def test_a_computed_subagent_duration_is_never_negative() -> None:
    # Without a duration in the usage event it is ended_at - started_at, and the two come from
    # different client-dated events (a start, a stop, a request). An end before the start gives NULL,
    # which keeps a stored value, not a negative number.
    duration = dict(rollups._SUBAGENT_VALUES)["duration_ms"]
    computed = rollups._ms_between("tm.ended_at", "tm.started_at")
    assert duration.endswith(f", CASE WHEN tm.ended_at >= tm.started_at THEN {computed} END)")
    assert duration.count(computed) == 1  # the difference is taken only inside the guard
    assert duration in rollups._SUBAGENT_INVOCATIONS
