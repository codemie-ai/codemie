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

"""Live-Postgres proof of the stored activity facts of a session (EPMCDME-16427): what `derive_session_activity` writes
from the five rollup tables, that the maintenance backfill reaches every underived session, and that the Sessions list
admits exactly the sessions the window rule admits for every combination of start, activity days and derived state.

The rule itself (`reader._SESSION_ADMITTED_IN_WINDOW`) is the reference: the same predicate is run straight over
`session_dims` and compared with the ids the list returns, so a stored fact that disagrees with the tables shows as
a difference in a session set, not as a hand-written expectation.

Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable, migrated PostgreSQL.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import migrations
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.repository.cli_analytics.postgres.reader import (
    _SESSION_ADMITTED_IN_WINDOW,
    PostgresCliAnalyticsReader,
    positional,
    window_params,
)
from codemie.repository.cli_analytics.postgres.session_activity import (
    backfill_session_activity,
    derive_session_activity,
)
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_TODAY = datetime.now(timezone.utc).date()
_TABLES = ("turns_daily", "tool_facts_daily", "cost_daily", "session_usage", "lines_daily")
_INSERT = {
    "turns_daily": "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, 1)",
    "tool_facts_daily": "INSERT INTO tool_facts_daily (day, session_id, tool_calls, agent_count, skill_count) "
    "VALUES ($1, $2, 1, 0, 0)",
    "cost_daily": "INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd, "
    "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, api_call_count) "
    "VALUES ($1, $2, '', 'm', 'repl_main_thread', 0.1, 1, 1, 0, 0, 1)",
    "session_usage": "INSERT INTO session_usage (session_id, day, model, speed, inference_geo, scope_kind, scope_name, "
    "agent_id) VALUES ($2, $1, '', '', '', '', '', '')",
    "lines_daily": "INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, lines_removed) "
    "VALUES ($1, $2, '', '', 1, 0)",
}


async def _engine() -> AnalyticsPgEngine:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    migrations.run_migrations(settings)
    # a fresh database has the day partitions of the days around today only: the seeds reach back a few weeks
    today = datetime.now(timezone.utc).date()
    maintainer = PartitionMaintainer(engine, RetentionPolicy.from_settings(settings), clock=lambda: today)
    async with engine.acquire() as conn:
        await maintainer._ensure_partitions(conn, today, MaintenanceReport())
    return engine


class _Seed:
    def __init__(self) -> None:
        self.prefix = f"test-act-{uuid.uuid4().hex[:12]}"
        self.ids: list[str] = []

    def sid(self, slug: str) -> str:
        return f"{self.prefix}-{slug}"

    async def session(
        self, conn, slug: str, started: datetime | None, days: list[tuple[str, date]] = (), derived: bool = False
    ) -> str:
        sid = self.sid(slug)
        self.ids.append(sid)
        await conn.execute(
            "INSERT INTO session_dims (session_id, jwt_email, started_at, last_event_at) VALUES ($1, 'act@example.com', $2, $2)",
            sid,
            started,
        )
        for table, day in days:
            await conn.execute(_INSERT[table], day, sid)
        if derived:
            await derive_session_activity(conn, [sid], 60)
        return sid

    async def facts(self, conn, slug: str) -> tuple:
        row = await conn.fetchrow(
            "SELECT has_activity, last_activity_day, resumed FROM session_dims WHERE session_id = $1", self.sid(slug)
        )
        return row["has_activity"], row["last_activity_day"], row["resumed"]

    async def cleanup(self, engine: AnalyticsPgEngine) -> None:
        async with engine.acquire() as conn:
            for table in (*_TABLES, "session_dims"):
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.ids)  # noqa: S608


def _at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_derive_writes_the_three_facts_from_the_five_tables() -> None:
    engine, seed = await _engine(), _Seed()
    d = _TODAY - timedelta(days=10)
    try:
        async with engine.acquire() as conn:
            for table in _TABLES:  # each of the five tables alone makes a session active
                await seed.session(conn, f"only-{table}", _at(d), [(table, d)])
            await seed.session(conn, "continued", _at(d), [("turns_daily", d), ("cost_daily", d + timedelta(days=3))])
            await seed.session(conn, "predates-start", _at(d), [("turns_daily", d - timedelta(days=1))])
            await seed.session(conn, "no-rows", _at(d))
            await seed.session(conn, "no-start", None, [("turns_daily", d)])
            ids = list(seed.ids)

            assert await derive_session_activity(conn, ids, 60) == len(ids)
            assert await derive_session_activity(conn, ids, 60) == 0  # nothing differs the second time

            for table in _TABLES:
                assert await seed.facts(conn, f"only-{table}") == (True, d, False), table
            assert await seed.facts(conn, "continued") == (True, d + timedelta(days=3), True)
            assert await seed.facts(conn, "predates-start") == (True, d - timedelta(days=1), True)
            assert await seed.facts(conn, "no-rows") == (False, None, False)  # derived: no activity, not NULL
            assert await seed.facts(conn, "no-start") == (True, d, True)  # no start day: every row counts as resumed
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_changed_start_day_changes_resumed_on_the_next_derive() -> None:
    engine, seed = await _engine(), _Seed()
    d = _TODAY - timedelta(days=10)
    try:
        async with engine.acquire() as conn:
            await seed.session(conn, "s", _at(d), [("turns_daily", d + timedelta(days=1))], derived=True)
            assert await seed.facts(conn, "s") == (True, d + timedelta(days=1), True)

            await conn.execute(
                "UPDATE session_dims SET started_at = $2 WHERE session_id = $1",
                seed.sid("s"),
                _at(d + timedelta(days=1)),
            )
            await derive_session_activity(conn, [seed.sid("s")], 60)

            assert await seed.facts(conn, "s") == (True, d + timedelta(days=1), False)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_backfill_derives_every_underived_session_in_bounded_batches() -> None:
    engine, seed = await _engine(), _Seed()
    d = _TODAY - timedelta(days=10)
    try:
        async with engine.acquire() as conn:
            for i in range(5):
                await seed.session(conn, f"p{i}", _at(d) + timedelta(minutes=i), [("turns_daily", d)] if i % 2 else [])
            assert await seed.facts(conn, "p0") == (None, None, None)  # inserted outside the refresher: not derived

            assert await backfill_session_activity(conn, 2, 0.0, 60) == 0  # no time left: nothing is started
            assert await seed.facts(conn, "p0") == (None, None, None)

            derived = await backfill_session_activity(conn, 2, 120.0, 60)  # three batches of at most two sessions

            assert derived >= 5  # at least these five: other underived sessions of the database may precede them
            assert await seed.facts(conn, "p0") == (False, None, False)
            assert await seed.facts(conn, "p1") == (True, d, False)
            assert await backfill_session_activity(conn, 2, 120.0, 60) == 0  # none left
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("narrowed", [True, False], ids=["by-user", "unrestricted"])
async def test_a_session_whose_rollup_rows_passed_retention_stays_listed_for_its_window_unlike_the_rule(
    narrowed: bool,
) -> None:
    """The accepted divergence (spec AC19, review comment CR-001): the facts are written at refresh time and are not
    cleared when the retention drops the rollup partitions of an old window, so the list keeps a session that started
    there, with no data, where `_SESSION_ADMITTED_IN_WINDOW` excludes it. While the rows exist, both admit it."""
    engine, seed = await _engine(), _Seed()
    old = _TODAY - timedelta(days=500)  # past the default rollup retention of 365 days
    start, end = _at(old - timedelta(days=1), 0), _at(old + timedelta(days=1), 23)
    try:
        async with engine.acquire() as conn:
            sid = await seed.session(conn, "aged", _at(old), [("turns_daily", old)], derived=True)
            f = _flt(start, end, narrowed)
            reader = PostgresCliAnalyticsReader(engine)

            _, listed = await reader.get_session_page(f, "start_time", 0, 1000)
            assert sid in await _rule_ids(conn, f, [sid]) and sid in listed  # rows present: both admit

            await conn.execute("DELETE FROM turns_daily WHERE session_id = $1", sid)  # what dropping the partition does

            _, listed = await reader.get_session_page(f, "start_time", 0, 1000)
            assert await seed.facts(conn, "aged") == (True, old, False)  # the facts are still there
            assert sid not in await _rule_ids(conn, f, [sid])  # the rule excludes it
            assert sid in listed  # the list keeps it (accepted)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_default_unfiltered_start_time_pages_are_exact_across_the_arms_and_equal_the_one_pass_path() -> None:
    """The default admin Sessions list takes the index-ordered split path (`start_time_page_sql`: the first
    limit + offset rows of the started arm merged with the other arms, re-sorted, then LIMIT/OFFSET; the count is one
    count per arm group). Every page, the total and the order are asserted against the exact expected order and
    against the one-pass path (the same window narrowed by a user, which takes `_searched_start_time_page`).
    The window is 200 days back, where no other session of the database is."""
    engine, seed = await _engine(), _Seed()
    day = _TODAY - timedelta(days=200)
    start, end = _at(day, 0), _at(day + timedelta(days=3), 23)
    try:
        async with engine.acquire() as conn:
            ids: dict[str, datetime] = {}

            async def add(slug: str, started: datetime, days, derived: bool) -> None:
                await seed.session(conn, slug, started, days, derived=derived)
                ids[seed.sid(slug)] = started

            for hour in (1, 5, 9, 13, 17):  # the started arm (derived), on three days
                await add(f"m{hour:02d}", _at(day + timedelta(days=hour % 3), hour), [("turns_daily", day)], True)
            same = _at(day + timedelta(days=1), 10)  # two with the same start: the order falls to the session id
            await add("tie-b", same, [("cost_daily", day)], True)
            await add("tie-a", same, [("cost_daily", day)], True)
            # started arm, facts underived (NULL): asks the tables, interleaves with the derived ones by start time
            await add("under-1", _at(day + timedelta(days=1), 3), [("turns_daily", day)], False)
            await add("under-2", _at(day + timedelta(days=2), 20), [("lines_daily", day)], False)
            # started before the window, active inside it on whole days: the resumed arm
            await add("res-1", _at(day - timedelta(days=5), 8), [("turns_daily", day + timedelta(days=1))], True)
            await add("res-2", _at(day - timedelta(days=9), 8), [("tool_facts_daily", day + timedelta(days=2))], True)
            # started in the window, no activity at all: in no list
            await seed.session(conn, "idle", _at(day + timedelta(days=1), 2), [], derived=True)
            await seed.session(conn, "idle-underived", _at(day + timedelta(days=1), 4), [], derived=False)

            expected = [s for s, _ in sorted(ids.items(), key=lambda kv: (-kv[1].timestamp(), kv[0].encode()))]
            assert len(expected) == 11
            reader = PostgresCliAnalyticsReader(engine)
            unrestricted, by_user = _flt(start, end, False), _flt(start, end, True)

            for per_page in (3, 4, 11, 20):
                pages = -(-len(expected) // per_page) + 1  # one page past the last
                for page in range(pages):
                    want = expected[page * per_page : (page + 1) * per_page]
                    got = await reader.get_session_page(unrestricted, "start_time", page, per_page)
                    one_pass = await reader.get_session_page(by_user, "start_time", page, per_page)
                    assert got == (len(expected), want), (per_page, page)
                    assert one_pass == got, (per_page, page)  # the two paths agree on every page and the total
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_maintenance_step_derives_the_underived_sessions_on_a_real_database() -> None:
    """The step as the hourly job runs it: the capped backlog count, the budget it picks and the batches."""
    engine, seed = await _engine(), _Seed()
    d = _TODAY - timedelta(days=10)
    try:
        async with engine.acquire() as conn:
            await seed.session(conn, "m0", _at(d), [("turns_daily", d)])
            await seed.session(conn, "m1", _at(d) + timedelta(minutes=1), [])
            await seed.session(conn, "m2", _at(d) + timedelta(minutes=2), [("cost_daily", d + timedelta(days=2))])
            report = MaintenanceReport()

            await PartitionMaintainer(engine, RetentionPolicy.from_settings(engine.settings))._derive_session_activity(
                conn, report
            )

            assert report.derived_sessions >= 3
            assert await conn.fetchval("SELECT count(*) FROM session_dims WHERE has_activity IS NULL") == 0
            assert await seed.facts(conn, "m0") == (True, d, False)
            assert await seed.facts(conn, "m1") == (False, None, False)
            assert await seed.facts(conn, "m2") == (True, d + timedelta(days=2), True)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_refresher_derive_that_waits_for_a_backfill_lock_writes_the_complete_facts() -> None:
    """The refresher (advisory lock "rollup-refresher") and the maintenance backfill (advisory lock "maintenance")
    can overlap. The backfill holds the session row (FOR UPDATE) and derives from the rows committed so far; the
    refresher's own day row is not committed yet. Its derive waits for the row, then writes facts computed from its own
    snapshot, which contains its row: the facts that end up stored are the complete ones, not the backfill's."""
    engine, seed = await _engine(), _Seed()
    d = _TODAY - timedelta(days=10)
    try:
        async with engine.acquire() as backfill, engine.acquire() as refresher:
            sid = await seed.session(backfill, "overlap", _at(d))
            backfill_tx = backfill.transaction()
            await backfill_tx.start()
            await backfill.fetchval("SELECT 1 FROM session_dims WHERE session_id = $1 FOR UPDATE", sid)
            await derive_session_activity(backfill, [sid], 60)  # the day row is not committed: no activity yet

            refresher_tx = refresher.transaction()
            await refresher_tx.start()
            await refresher.execute(_INSERT["turns_daily"], d, sid)
            derive = asyncio.create_task(derive_session_activity(refresher, [sid], 60))
            await asyncio.sleep(0.5)
            assert not derive.done(), "the derive must wait for the row the backfill holds"

            await backfill_tx.commit()
            await asyncio.wait_for(derive, 10)
            await refresher_tx.commit()

            assert await seed.facts(backfill, "overlap") == (True, d, False)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_backfill_skips_a_session_the_refresher_holds_instead_of_waiting_for_it() -> None:
    engine, seed = await _engine(), _Seed()
    d = _TODAY - timedelta(days=10)
    try:
        async with engine.acquire() as backfill, engine.acquire() as refresher:
            sid = await seed.session(backfill, "held", _at(d))
            refresher_tx = refresher.transaction()
            await refresher_tx.start()
            await refresher.execute(_INSERT["turns_daily"], d, sid)
            await derive_session_activity(refresher, [sid], 60)  # holds the row until the batch commits

            # FOR UPDATE SKIP LOCKED: the backfill neither blocks on the row nor derives it from rows it cannot see
            await asyncio.wait_for(backfill_session_activity(backfill, 100, 5.0, 60), 20)
            await refresher_tx.commit()

            assert await seed.facts(backfill, "held") == (True, d, False)
    finally:
        await seed.cleanup(engine)
        await engine.close()


def _flt(start: datetime, end: datetime, narrowed: bool) -> LocalAnalyticsFilter:
    """`narrowed` filters by user (the one-pass page); not narrowed is the unrestricted shared list (index-ordered page)."""
    return LocalAnalyticsFilter(
        scope=EXCLUDE_SCOPE,
        start_dt=start.astimezone(timezone.utc).replace(tzinfo=None),
        end_dt=end.astimezone(timezone.utc).replace(tzinfo=None),
        users=["act@example.com"] if narrowed else None,
    )


async def _rule_ids(conn, f: LocalAnalyticsFilter, ids: list[str]) -> set[str]:
    """The sessions `_SESSION_ADMITTED_IN_WINDOW` admits, asked straight of the tables (the stored facts unused)."""
    sql, args = positional(
        "SELECT d.session_id FROM session_dims d WHERE d.started_at IS NOT NULL AND "
        f"{_SESSION_ADMITTED_IN_WINDOW} AND d.session_id = ANY($ids::text[])",
        {**window_params(f), "ids": ids},
    )
    return {r["session_id"] for r in await conn.fetch(sql, *args)}


@pytest.mark.asyncio
@pytest.mark.parametrize("narrowed", [True, False], ids=["by-user", "unrestricted"])
@pytest.mark.parametrize("derived", [False, True], ids=["underived", "derived"])
@pytest.mark.parametrize("window", ["whole-days", "mid-day-edges", "one-day", "future-end"])
async def test_the_list_admits_exactly_the_sessions_of_the_window_rule(
    window: str, derived: bool, narrowed: bool
) -> None:
    engine, seed = await _engine(), _Seed()
    # a window of whole days or of mid-day edges, ending today or a day ahead; sessions around both of its edges
    lo_day, hi_day = _TODAY - timedelta(days=20), _TODAY - timedelta(days=5)
    starts = {
        "whole-days": (_at(lo_day, 0), _at(hi_day, 23) + timedelta(minutes=59, seconds=59, milliseconds=999)),
        "mid-day-edges": (_at(lo_day, 9), _at(hi_day, 15)),
        "one-day": (_at(lo_day, 9), _at(lo_day, 17)),
        "future-end": (_at(lo_day, 9), _at(_TODAY + timedelta(days=1), 9)),
    }[window]
    start, end = starts
    try:
        async with engine.acquire() as conn:
            before, inside, after = lo_day - timedelta(days=30), lo_day + timedelta(days=3), hi_day + timedelta(days=2)
            for name, started in (
                ("far-before", _at(before)),
                ("day-before", _at(lo_day - timedelta(days=1))),
                ("edge-day-early", _at(lo_day, 2)),  # the window's first day, before a mid-day start
                ("edge-day-mid", _at(lo_day, 12)),  # inside the mid-day windows
                ("edge-day-late", _at(lo_day, 20)),
                ("inside", _at(inside)),
                ("last-day-early", _at(hi_day, 3)),
                ("last-day-late", _at(hi_day, 22)),
                ("after", _at(after)),
                ("no-start", None),
            ):
                same_day = None if started is None else started.date()
                kinds = {
                    "none": [],
                    "start-day": [("turns_daily", same_day)] if same_day else [],
                    "resumed-in": [("cost_daily", inside), ("turns_daily", same_day or inside)],
                    "resumed-edge-first": [("lines_daily", lo_day)],
                    "resumed-edge-last": [("tool_facts_daily", hi_day)],
                    "resumed-out": [("session_usage", before)],
                    "resumed-after": [("turns_daily", after)],
                }
                for kind, days in kinds.items():
                    await seed.session(conn, f"{name}-{kind}", started, days, derived=False)
            if derived:
                await derive_session_activity(conn, seed.ids, 120)
            f = _flt(start, end, narrowed)

            expected = await _rule_ids(conn, f, seed.ids)
            total, listed = await PostgresCliAnalyticsReader(engine).get_session_page(f, "start_time", 0, 1000)
            listed_mine = {s for s in listed if s.startswith(seed.prefix)}

            assert expected, "the seed must admit something, or the comparison is empty"
            assert expected != set(seed.ids), "the seed must exclude something, or the comparison is empty"
            assert listed_mine == expected, sorted(listed_mine ^ expected)
            assert total >= len(expected)
    finally:
        await seed.cleanup(engine)
        await engine.close()
