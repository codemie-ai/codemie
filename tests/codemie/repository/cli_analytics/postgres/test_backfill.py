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

"""The operator's backfill and repair command for the PostgreSQL rollups."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from codemie.repository.cli_analytics.postgres import backfill, rollups
from codemie.repository.cli_analytics.vocabulary import EVENT_KIND, SPAN_KIND
from tests.codemie.repository.cli_analytics.postgres.test_engine import SETTINGS

STARTED = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


class Connection:
    """Answers the backfill's own queries: its start time, then how many of its keys are queued."""

    def __init__(self, queued_before_start: list[int]) -> None:
        self.fetchval = AsyncMock(side_effect=[STARTED, *queued_before_start])
        self.execute = AsyncMock(return_value="UPDATE 2")

    @asynccontextmanager
    async def _tx(self):
        yield

    def transaction(self):
        return self._tx()


class Engine:
    def __init__(self, queued_before_start: list[int] | None = None, leader: bool = True) -> None:
        self.work = Connection([])
        self.lock_connection = Connection([])
        self.lock_connection.fetchval = AsyncMock(side_effect=queued_before_start or [0])
        self.work.fetchval = AsyncMock(return_value=STARTED)
        self.leader = leader
        self.locks: list[str] = []
        self.close = AsyncMock()

    @asynccontextmanager
    async def acquire(self, timeout=None):
        yield self.work

    @asynccontextmanager
    async def advisory_lock(self, name: str):
        self.locks.append(name)
        yield self.lock_connection if self.leader else None


def _refresher(queued: int = 42, batches: tuple[int, ...] = (30, 12)) -> MagicMock:
    refresher = MagicMock()
    refresher.mark_dirty = AsyncMock(return_value=queued)
    refresher.refresh = AsyncMock(side_effect=list(batches))
    return refresher


@pytest.mark.asyncio
async def test_a_range_is_reclassified_then_queued_for_the_running_refresh_job():
    engine, refresher = Engine(), _refresher()

    with patch.object(backfill, "RollupRefresher", return_value=refresher):
        result = await backfill.backfill(engine, SETTINGS, date(2026, 9, 1), date(2026, 9, 2), now=False)  # type: ignore[arg-type]

    refresher.mark_dirty.assert_awaited_once_with(date(2026, 9, 1), date(2026, 9, 2), engine.work)
    refresher.refresh.assert_not_awaited()
    assert engine.locks == []
    # Two days, spans and log events each: 4 updates of 2 rows.
    assert result == backfill.BackfillResult(reclassified=8, queued=42, rebuilt=0)


@pytest.mark.asyncio
async def test_rows_stored_before_their_name_was_mapped_get_the_mapped_kind():
    engine = Engine()

    await backfill.reclassify(engine.work, date(2026, 9, 1), date(2026, 9, 1))  # type: ignore[arg-type]

    updates = [c.args for c in engine.work.execute.await_args_list if "UPDATE" in c.args[0]]
    (spans,) = [u for u in updates if "UPDATE spans" in u[0]]
    (logs,) = [u for u in updates if "UPDATE log_events" in u[0]]
    assert dict(zip(spans[1], spans[2], strict=True)) == {name: int(kind) for name, kind in SPAN_KIND.items()}
    assert dict(zip(logs[1], logs[2], strict=True)) == {name: int(kind) for name, kind in EVENT_KIND.items()}
    assert "span_kind = 0" in spans[0] and "event_kind = 0" in logs[0]  # only rows never classified


@pytest.mark.asyncio
async def test_reclassifying_a_large_day_is_not_cut_short_by_the_pools_client_timeout():
    async def execute(sql, *args, timeout=None):
        # asyncpg: without `timeout=` the pool's command timeout (a dashboard query's) applies.
        if "UPDATE" in sql and (timeout or 35) < 120:
            raise TimeoutError
        return "UPDATE 2"

    conn = Connection([])
    conn.execute = AsyncMock(side_effect=execute)

    assert await backfill.reclassify(conn, date(2026, 9, 1), date(2026, 9, 1)) == 4  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_now_rebuilds_what_it_queued_under_the_refreshers_lock():
    engine, refresher = Engine(queued_before_start=[5, 1, 0]), _refresher()

    with patch.object(backfill, "RollupRefresher", return_value=refresher):
        result = await backfill.backfill(engine, SETTINGS, date(2026, 9, 1), date(2026, 9, 1), now=True)  # type: ignore[arg-type]

    assert engine.locks == ["rollup-refresher"]  # never alongside a pod's refresh job
    refresher.refresh.assert_awaited_with(engine.lock_connection)
    assert result.rebuilt == 42


@pytest.mark.asyncio
async def test_now_stops_once_its_own_keys_are_rebuilt_while_ingest_keeps_marking_more():
    engine = Engine(queued_before_start=[3, 0])
    refresher = _refresher(batches=(7, 7, 7, 7))  # live ingest: never an empty queue

    with patch.object(backfill, "RollupRefresher", return_value=refresher):
        result = await backfill.backfill(engine, SETTINGS, date(2026, 9, 1), date(2026, 9, 1), now=True)  # type: ignore[arg-type]

    assert refresher.refresh.await_count == 1
    assert result.rebuilt == 7
    # Keys queued before the command started, whatever ingest has marked since.
    assert engine.lock_connection.fetchval.await_args_list[0].args[1] == STARTED


@pytest.mark.asyncio
async def test_now_leaves_the_queue_to_a_pod_already_refreshing():
    engine, refresher = Engine(leader=False), _refresher()

    with patch.object(backfill, "RollupRefresher", return_value=refresher):
        result = await backfill.backfill(engine, SETTINGS, date(2026, 9, 1), date(2026, 9, 1), now=True)  # type: ignore[arg-type]

    refresher.refresh.assert_not_awaited()
    assert (result.queued, result.rebuilt) == (42, 0)


class SlowQueueDatabase:
    """rollup_dirty with every recompute timing out after 5 minutes, and the clock that sees it."""

    def __init__(self, sessions: list[str]) -> None:
        self.now = 0.0
        self.stamp = 0  # marked_at, as a counter: later marks compare greater
        self.queue = {
            s: {
                "day": date(2026, 9, 1),
                "session_id": s,
                "kinds": 15,
                "version": 1,
                "at": 0,
                "timeouts": 0,
                "alone": False,
            }
            for s in sessions
        }
        self.started = 0

    def _due(self, alone: bool) -> list[dict]:
        due = [k for k in self.queue.values() if k["alone"] == alone and k["at"] <= self.stamp]
        return sorted(due, key=lambda k: k["at"])

    async def fetch(self, sql, *args, timeout=None):  # _READ_KEYS
        await asyncio.sleep(0)  # lets wait_for stop a run that never ends
        return self._due(alone=False)[: args[0]]

    async def fetchrow(self, sql, *args, timeout=None):  # _READ_ALONE_KEY
        due = self._due(alone=True)
        return due[0] if due else None

    async def fetchval(self, sql, *args, timeout=None):
        if "clock_timestamp()" in sql:
            self.stamp += 1
            self.started = self.stamp
            return self.stamp
        if sql == backfill._QUEUED_SINCE_BEFORE:
            return sum(1 for k in self.queue.values() if k["at"] <= args[0])
        return date(2026, 9, 23)  # the database's date

    async def execute(self, sql, *args, timeout=None):
        if sql in rollups.RECOMPUTE:
            self.now += 300
            raise asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout")
        if sql == rollups._FLAG_ALONE:
            for session in args[1]:
                self.queue[session]["alone"] = True
        elif sql == rollups._DEFER_KEY:
            key = self.queue[args[1]]
            key.update(timeouts=key["timeouts"] + 1, alone=True, at=self.stamp + 10_000)  # due in minutes
        elif sql == rollups._DROP_KEY:
            return f"DELETE {int(self.queue.pop(args[1], None) is not None)}"
        return "UPDATE 0"

    @asynccontextmanager
    async def _tx(self):
        yield

    def transaction(self):
        return self._tx()


@pytest.mark.asyncio
async def test_now_ends_even_when_every_recompute_times_out():
    # Holding the refresher's lock, a --now that never ends would stop every pod's refresh job.
    db = SlowQueueDatabase(["s1", "s2", "s3"])
    engine = Engine()
    engine.work = engine.lock_connection = db  # type: ignore[assignment]

    with (
        patch.object(backfill, "reclassify", AsyncMock(return_value=0)),
        patch.object(backfill.RollupRefresher, "mark_dirty", AsyncMock(return_value=3)),
        patch.object(rollups, "time", SimpleNamespace(monotonic=lambda: db.now)),
        patch.object(rollups, "logger"),
    ):
        result = await asyncio.wait_for(
            backfill.backfill(engine, SETTINGS, date(2026, 9, 1), date(2026, 9, 1), now=True),  # type: ignore[arg-type]
            timeout=5,
        )

    assert result.rebuilt == 0  # nothing could be rebuilt, and nothing is claimed to be
    assert all(k["at"] > db.started for k in db.queue.values())  # each moved behind the backfill


def test_main_runs_the_backfill_and_closes_the_pool():
    engine = Engine()
    run = AsyncMock(return_value=backfill.BackfillResult(reclassified=3, queued=7, rebuilt=0))

    with (
        patch.object(backfill.AnalyticsPgSettings, "from_config", return_value=SETTINGS),
        patch.object(backfill, "AnalyticsPgEngine", return_value=engine),
        patch.object(backfill, "backfill", run),
        patch.object(backfill, "logger") as logger,
    ):
        assert backfill.main(["--from", "2026-09-01", "--to", "2026-09-23"]) == 0

    run.assert_awaited_once_with(engine, SETTINGS, date(2026, 9, 1), date(2026, 9, 23), now=False)
    engine.close.assert_awaited_once()
    audit = logger.info.call_args.args[0]
    assert "2026-09-01..2026-09-23" in audit
    assert "3 rows reclassified" in audit and "7 (day, session) keys queued" in audit


@pytest.mark.parametrize(
    "argv",
    [
        ["--from", "2026-09-23", "--to", "2026-09-01"],  # an empty range
        ["--from", "2026-09-01"],
        ["--from", "yesterday", "--to", "2026-09-01"],
    ],
)
def test_main_refuses_what_it_cannot_do(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        backfill.main(argv)

    assert exit_.value.code == 2
