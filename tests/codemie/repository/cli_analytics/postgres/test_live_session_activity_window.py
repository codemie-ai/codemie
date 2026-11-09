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

"""Live-Postgres proof of period-scoped session membership: `sel` (`_sessions_cte`) admits a session for a
window when either its `started_at` falls in the window and it has ever produced a real activity signal, or
it has a `turns_daily`, `tool_facts_daily`, `cost_daily`, `session_usage` or `lines_daily` row on a day wholly
inside the window, or on a day the window covers in part together with a raw activity record stamped inside
the window, regardless of its all-time `session_dims.started_at`.

Most sessions are seeded straight into `session_dims` / `turns_daily` / `tool_facts_daily` / `cost_daily` /
`session_usage` / `lines_daily` under one unique id prefix, bypassing the hook/rollup pipeline (the activity
predicate reads these tables, plus, for a day the window covers in part, the raw record a test adds with
`_Seed.raw`). The exclusion tests that hinge on exactly
which hook types leave no activity-table row -- a lone `agent.session.end`, or only
`agent.session.start`/`agent.session.env` -- seed real `hook_events` rows through `_seed_via_hooks` and run
the actual rollup instead, so they prove what the dims upsert really leaves, not an assumption about it.
Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable PostgreSQL (the live
suite migrates it).
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone

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
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.session_activity import derive_session_activity
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_DIMS_SQL = """
    INSERT INTO session_dims (session_id, started_at, last_event_at, jwt_email, first_prompt)
    VALUES ($1, $2, $3, $4, $5)
"""
_TURNS_SQL = "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, $3)"
_TOOLS_SQL = """
    INSERT INTO tool_facts_daily (day, session_id, tool_calls, agent_count, skill_count)
    VALUES ($1, $2, $3, 0, 0)
"""
_COST_SQL = """
    INSERT INTO cost_daily
        (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, api_call_count)
    VALUES ($1, $2, $3, 'claude-sonnet', 'repl_main_thread', 0.1, 10, 5, 0, 0, 1)
"""
_USAGE_SQL = """
    INSERT INTO session_usage (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id)
    VALUES ($1, $2, '', '', '', '', '', '')
"""
_LINES_SQL = """
    INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, lines_removed)
    VALUES ($1, $2, $3, '', 1, 0)
"""


# Every scenario runs twice: with the sessions' stored activity facts NULL (as before they were derived: the rule is asked
# of the rollup tables) and with them derived from what the test seeded (the stored facts decide the candidates). The
# result must be the rule's in both. The seeds below write the tables directly, so "derived" derives on each acquire.
@pytest.fixture(autouse=True, params=["underived", "derived"])
def _activity_facts(request, monkeypatch):
    monkeypatch.setattr(f"{__name__}._DERIVED", request.param == "derived")


_DERIVED = False


def _deriving(engine: AnalyticsPgEngine) -> AnalyticsPgEngine:
    acquire = engine.acquire

    @asynccontextmanager
    async def acquire_deriving(*args, **kwargs):
        async with acquire(*args, **kwargs) as conn:
            ids = [r["session_id"] for r in await conn.fetch("SELECT session_id FROM session_dims")]
            await derive_session_activity(conn, ids, 120)
            yield conn

    engine.acquire = acquire_deriving  # type: ignore[method-assign]
    return engine


async def _engine() -> AnalyticsPgEngine:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    migrations.run_migrations(settings)  # idempotent: a migrated database is left as it is
    maintainer = PartitionMaintainer(
        engine, RetentionPolicy.from_settings(settings), clock=lambda: datetime.now(timezone.utc).date()
    )
    async with engine.acquire() as conn:
        await maintainer._ensure_partitions(conn, datetime.now(timezone.utc).date(), MaintenanceReport())
    return _deriving(engine) if _DERIVED else engine


async def _cleanup(engine: AnalyticsPgEngine, session_ids: list[str]) -> None:
    async with engine.acquire() as conn:
        tables = (
            "session_dims",
            "turns_daily",
            "tool_facts_daily",
            "cost_daily",
            "session_usage",
            "lines_daily",
            "hook_events",
            "usage_requests",
            "log_events",
            "spans",
            "metric_points",
        )
        for table in tables:
            await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", session_ids)  # noqa: S608


def _naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


# Sub-day windows: a day row says only that activity happened on that UTC day, not when. A window that
# covers part of a day must look at the raw record time of that activity instead.
_NOON = datetime.combine(datetime.now(timezone.utc).date(), time(12), tzinfo=timezone.utc)

# One raw record per source that feeds an activity table, at `$2`, for session `$1`.
_RAW_ACTIVITY_SQL = {
    "prompt_hook": "INSERT INTO hook_events (session_id, ts, event_type) VALUES ($1, $2, 'agent.prompt.submit')",
    "tool_hook": "INSERT INTO hook_events (session_id, ts, event_type, tool_name) VALUES ($1, $2, 'agent.tool.start', 'Bash')",
    "tool_error_hook": "INSERT INTO hook_events (session_id, ts, event_type, tool_name) VALUES ($1, $2, 'agent.tool.error', 'Bash')",
    "tool_denied_hook": "INSERT INTO hook_events (session_id, ts, event_type, tool_name) VALUES ($1, $2, 'agent.tool.denied', 'Bash')",
    "subagent_usage_hook": "INSERT INTO hook_events (session_id, ts, event_type) VALUES ($1, $2, 'agent.subagent.usage')",
    "usage_request": "INSERT INTO usage_requests (session_id, ts) VALUES ($1, $2)",
    "otel_api_request": (
        "INSERT INTO log_events (session_id, ts, event_kind, event_name, severity, resource_id) "
        "VALUES ($1, $2, 1, 'api_request', 9, 0)"
    ),
    "otel_interaction_span": (
        "INSERT INTO spans (session_id, ts, duration_ns, trace_id, span_id, span_kind, span_name, status_code, "
        "resource_id) VALUES ($1, $2, 1, '\x01'::bytea, '\x02'::bytea, 3, 'claude_code.interaction', 0, 0)"
    ),
    "otel_lines_metric": (
        "INSERT INTO metric_points (session_id, ts, metric_name, value, temporality, is_monotonic, resource_id) "
        "VALUES ($1, $2, 'claude_code.lines_of_code.count', 1, 1, true, 0)"
    ),
}


class _Seed:
    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex
        self.email = f"activity-window-{self.suffix}@example.com"
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def sid(self, name: str) -> str:
        return f"test-activity-window-{self.suffix}-{name}"

    def window(self) -> LocalAnalyticsFilter:
        return LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE,
            start_dt=_naive(self.now - timedelta(days=1)),
            end_dt=_naive(self.now + timedelta(hours=1)),
            users=[self.email],
        )

    async def dims(self, engine: AnalyticsPgEngine, name: str, started_at: datetime) -> None:
        async with engine.acquire() as conn:
            await conn.execute(_DIMS_SQL, self.sid(name), started_at, started_at, self.email, f"session {name}")

    async def turn(self, engine: AnalyticsPgEngine, name: str, day) -> None:
        async with engine.acquire() as conn:
            await conn.execute(_TURNS_SQL, day, self.sid(name), 1)

    async def tool_call(self, engine: AnalyticsPgEngine, name: str, day) -> None:
        async with engine.acquire() as conn:
            await conn.execute(_TOOLS_SQL, day, self.sid(name), 1)

    async def cost(self, engine: AnalyticsPgEngine, name: str, day) -> None:
        async with engine.acquire() as conn:
            await conn.execute(_COST_SQL, day, self.sid(name), self.email)

    async def usage(self, engine: AnalyticsPgEngine, name: str, day) -> None:
        async with engine.acquire() as conn:
            await conn.execute(_USAGE_SQL, self.sid(name), day)

    async def lines(self, engine: AnalyticsPgEngine, name: str, day) -> None:
        async with engine.acquire() as conn:
            await conn.execute(_LINES_SQL, day, self.sid(name), self.email)

    async def raw(self, engine: AnalyticsPgEngine, name: str, source: str, ts: datetime) -> None:
        """The raw record a hand-seeded day row of `source` is built from, at `ts`."""
        async with engine.acquire() as conn:
            await conn.execute(_RAW_ACTIVITY_SQL[source], self.sid(name), ts)


async def _is_listed(reader: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, session_id: str) -> bool:
    facts = await reader.get_session_cost_facts(f, include_costless=True)
    return session_id in {r["session_id"] for r in facts}


async def _seed_via_hooks(
    engine: AnalyticsPgEngine, session_id: str, events: list[tuple[str, datetime]], user_email: str = ""
) -> None:
    """Insert raw `hook_events` rows and run the real rollup (dims upsert included) over their days, so
    `session_dims` ends up exactly as real ingestion would leave it -- not hand-crafted to match what the
    admission predicate is assumed to need. `user_email` becomes `session_dims.jwt_email` via the dims
    upsert, the same way a real client's hooks carry the authenticated sender's email."""
    days = set()
    async with engine.acquire() as conn:
        for event_type, ts in events:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, user_email) VALUES ($1, $2, $3, $4)",
                ts,
                session_id,
                event_type,
                user_email,
            )
            days.add(ts.date())
    async with live_rollup_lock(engine):
        refresher = RollupRefresher(engine, batch_size=100)
        async with engine.acquire() as conn:
            for day in days:
                await refresher.mark_dirty(day, day, conn)
        await refresher.refresh()


@pytest.mark.asyncio
async def test_old_session_with_no_activity_in_the_period_is_not_listed():
    engine = await _engine()
    seed = _Seed()
    name = "stale"
    try:
        old_day = (seed.now - timedelta(days=10)).date()
        await seed.dims(engine, name, seed.now - timedelta(days=10))
        await seed.turn(engine, name, old_day)
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, seed.window(), seed.sid(name)) is False
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_old_session_with_fresh_activity_in_the_period_is_listed():
    engine = await _engine()
    seed = _Seed()
    name = "resumed"
    try:
        old_day = (seed.now - timedelta(days=10)).date()
        await seed.dims(engine, name, seed.now - timedelta(days=10))
        await seed.turn(engine, name, old_day)
        await seed.turn(engine, name, seed.now.date())  # a fresh prompt inside the window
        await seed.raw(engine, name, "prompt_hook", seed.now)
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, seed.window(), seed.sid(name)) is True
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_session_with_only_cost_rows_in_the_period_is_listed_via_cost_daily_arm():
    engine = await _engine()
    seed = _Seed()
    name = "cost-only"
    try:
        # started_at is outside the window: only the cost_daily arm can admit this session.
        await seed.dims(engine, name, seed.now - timedelta(days=10))
        await seed.cost(engine, name, seed.now.date())
        await seed.raw(engine, name, "otel_api_request", seed.now)
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, seed.window(), seed.sid(name)) is True
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_session_with_only_session_usage_rows_in_the_period_is_listed_via_the_usage_arm():
    engine = await _engine()
    seed = _Seed()
    name = "usage-only"
    try:
        # started_at is outside the window: only the session_usage arm can admit this session.
        await seed.dims(engine, name, seed.now - timedelta(days=10))
        await seed.usage(engine, name, seed.now.date())
        await seed.raw(engine, name, "usage_request", seed.now)
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, seed.window(), seed.sid(name)) is True
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_session_with_only_lines_daily_rows_in_the_period_is_listed_via_the_lines_arm():
    engine = await _engine()
    seed = _Seed()
    name = "lines-only"
    try:
        # started_at is outside the window: only the lines_daily arm can admit this session.
        await seed.dims(engine, name, seed.now - timedelta(days=10))
        await seed.lines(engine, name, seed.now.date())
        await seed.raw(engine, name, "otel_lines_metric", seed.now)
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, seed.window(), seed.sid(name)) is True
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_tool_denied_hook_with_no_successful_call_counts_as_activity():
    # A denied/errored tool call never gets an agent.tool.end hook event or a matching span, so the
    # rollup's hook-fallback branch (rollups.py's tool_facts_daily UNION ALL arm, gated on
    # event_type IN ('agent.tool.start', 'agent.subagent.start', 'agent.tool.error', 'agent.tool.denied')
    # with no matching span) is the only thing that can produce a tool_facts_daily row for it. Seed a
    # real agent.tool.denied hook event and run the real rollup instead of inserting the row directly, so
    # this proves that path, not a row that happens to exist. tool_calls must stay 0: a denied call is a
    # presence signal for admission, never a counted call (see get_file_facts_by_session/get_session_detail
    # readers, which sum tool_facts_daily.tool_calls as the displayed "tool calls" metric).
    engine = await _engine()
    seed = _Seed()
    name = "denied-only"
    try:
        await seed.dims(engine, name, seed.now - timedelta(days=10))
        day = seed.now.date()
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, tool_name, tool_use_id) "
                "VALUES ($1, $2, 'agent.tool.denied', 'Bash', $3)",
                datetime.combine(day, seed.now.timetz()),
                seed.sid(name),
                f"toolu_{seed.suffix}",
            )
        async with live_rollup_lock(engine):
            refresher = RollupRefresher(engine, batch_size=100)
            async with engine.acquire() as conn:
                await refresher.mark_dirty(day, day, conn)
            await refresher.refresh()
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, seed.window(), seed.sid(name)) is True
        async with engine.acquire() as conn:
            tool_calls = await conn.fetchval(
                "SELECT sum(tool_calls) FROM tool_facts_daily WHERE session_id = $1", seed.sid(name)
            )
        assert tool_calls in (0, None)
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_session_that_started_inside_the_window_with_no_activity_ever_is_not_listed():
    # A session that only ever got session.start/session.env hooks (e.g. a VS Code restart reopening a
    # panel, or a Copilot session closed without a prompt) must not be listed at all, even when its
    # started_at falls inside the window. Seeded through the real hook->rollup pipeline (not a
    # hand-crafted session_dims row) so this proves the dims upsert actually leaves no activity-table row
    # for a session with only these two hook types, not an assumption about what it would leave.
    engine = await _engine()
    seed = _Seed()
    name = "fresh-but-empty"
    try:
        await _seed_via_hooks(
            engine, seed.sid(name), [("agent.session.start", seed.now), ("agent.session.env", seed.now)]
        )
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, seed.window(), seed.sid(name)) is False
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_lone_session_end_hook_with_no_session_start_is_not_listed():
    # A background sessionEnd hook with no session start and no activity at all (e.g. VS Code's built-in
    # Copilot runtime firing one lone agent.session.end with nothing else). agent.session.end is itself
    # one of the 13 dimension hook types, so the dims upsert still sets started_at from it -- the
    # exclusion must come from having no row in any activity table, not from a missing started_at.
    engine = await _engine()
    seed = _Seed()
    name = "lone-end"
    try:
        await _seed_via_hooks(engine, seed.sid(name), [("agent.session.end", seed.now)])
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, seed.window(), seed.sid(name)) is False
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_short_window_crossing_utc_midnight_admits_activity_on_the_correct_utc_day():
    # A window narrower than a day, crossing UTC midnight, must still admit an old session whose fresh
    # activity falls on the UTC day the window's end touches -- exercising _START_DAY_UTC/_END_DAY_UTC's
    # explicit UTC cast rather than a day boundary that could follow the session's TimeZone GUC.
    engine = await _engine()
    seed = _Seed()
    name = "midnight-cross"
    try:
        today_utc = datetime.now(timezone.utc).date()
        midnight = datetime.combine(today_utc, datetime.min.time(), tzinfo=timezone.utc)
        window_start = midnight - timedelta(hours=1)  # 23:00 UTC the day before
        window_end = midnight + timedelta(hours=1)  # 01:00 UTC today
        await seed.dims(engine, name, seed.now - timedelta(days=10))
        await seed.turn(engine, name, today_utc)  # activity on the UTC day the window's end falls on
        await seed.raw(engine, name, "prompt_hook", midnight + timedelta(minutes=30))
        f = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE, start_dt=_naive(window_start), end_dt=_naive(window_end), users=[seed.email]
        )
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, f, seed.sid(name)) is True
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_session_that_started_inside_the_window_with_real_activity_is_still_listed():
    # Sub-day window-boundary precision for a recently started, genuinely active session is unaffected by
    # the no-activity-ever exclusion above: as soon as a session has produced one real signal, its exact
    # `started_at BETWEEN` admission still applies.
    engine = await _engine()
    seed = _Seed()
    name = "fresh-and-active"
    try:
        await seed.dims(engine, name, seed.now)
        await seed.turn(engine, name, seed.now.date())
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, seed.window(), seed.sid(name)) is True
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_get_session_page_total_excludes_a_session_with_no_activity_in_the_window():
    # The SQL-side paging total (get_session_page) must agree with the full-list membership fix: a stale
    # session excluded from `sel` must also drop out of the paged total, not just the full list.
    engine = await _engine()
    seed = _Seed()
    stale, active = "stale", "active"
    try:
        await seed.dims(engine, stale, seed.now - timedelta(days=10))
        await seed.turn(engine, stale, (seed.now - timedelta(days=10)).date())  # activity only outside the window
        await seed.dims(engine, active, seed.now)
        await seed.turn(engine, active, seed.now.date())
        reader = PostgresCliAnalyticsReader(engine)

        total, ids = await reader.get_session_page(seed.window(), "start_time", 0, 1000)

        assert (total, ids) == (1, [seed.sid(active)])
    finally:
        await _cleanup(engine, [seed.sid(stale), seed.sid(active)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_session_present_in_both_turns_daily_and_cost_daily_is_counted_once():
    # A session can be admitted to `sel` via more than one activity arm at once (turns_daily and
    # cost_daily both have a row for it) — it must still be counted once, not double-counted.
    engine = await _engine()
    seed = _Seed()
    name = "two-arms"
    try:
        await seed.dims(engine, name, seed.now - timedelta(days=10))
        await seed.turn(engine, name, seed.now.date())
        await seed.cost(engine, name, seed.now.date())
        await seed.raw(engine, name, "prompt_hook", seed.now)
        await seed.raw(engine, name, "otel_api_request", seed.now)
        reader = PostgresCliAnalyticsReader(engine)

        facts = await reader.get_session_cost_facts(seed.window(), include_costless=True)

        assert sum(1 for r in facts if r["session_id"] == seed.sid(name)) == 1
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_get_cost_kpis_and_get_users_count_a_costless_session_like_the_sessions_list():
    # Overview's total_sessions and get_users' session_count/session_ids now count every session of
    # `sel`, priced or not -- the same universe get_session_cost_facts(include_costless=True) already
    # lists.
    engine = await _engine()
    seed = _Seed()
    priced, costless = "priced", "costless"
    try:
        await seed.dims(engine, priced, seed.now)
        await seed.cost(engine, priced, seed.now.date())
        await seed.dims(engine, costless, seed.now)
        await seed.turn(engine, costless, seed.now.date())
        reader = PostgresCliAnalyticsReader(engine)

        (kpis,) = await reader.get_cost_kpis(seed.window())
        users = await reader.get_users(seed.window())
        facts = await reader.get_session_cost_facts(seed.window(), include_costless=True)

        assert kpis["total_sessions"] == 2 == len(facts)
        assert sum(u["session_count"] for u in users) == 2
        listed_ids = {sid for u in users for sid in u["session_ids"]}
        assert listed_ids == {seed.sid(priced), seed.sid(costless)}
    finally:
        await _cleanup(engine, [seed.sid(priced), seed.sid(costless)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_costless_company_billed_session_does_not_count_as_a_spend_session():
    # `x NOT IN (<empty subquery>)` is unconditionally TRUE in Postgres, even for a NULL
    # x -- so when no session in the window is personal-billed (the common case, as here), _spend("c")
    # alone is vacuously true for a costless row too. spend_sessions must also require an actual
    # cost_daily row (c.session_id IS NOT NULL) on top of the billing-class check.
    engine = await _engine()
    seed = _Seed()
    priced, costless = "spend-priced", "spend-costless"
    try:
        await seed.dims(engine, priced, seed.now)
        await seed.cost(engine, priced, seed.now.date())
        await seed.dims(engine, costless, seed.now)
        await seed.turn(engine, costless, seed.now.date())
        reader = PostgresCliAnalyticsReader(engine)

        (kpis,) = await reader.get_cost_kpis(seed.window())

        assert kpis["total_sessions"] == 2
        assert kpis["spend_sessions"] == 1
    finally:
        await _cleanup(engine, [seed.sid(priced), seed.sid(costless)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_costless_developer_sorts_below_a_priced_one_on_the_leaderboard():
    # get_users' ORDER BY cost_usd DESC defaults to NULLS FIRST in Postgres, so a costless
    # developer (cost_usd NULL after the RIGHT JOIN sel widening) would otherwise sort above every
    # priced developer instead of below them.
    engine = await _engine()
    seed = _Seed()
    priced, costless = "leaderboard-priced", "leaderboard-costless"
    priced_dev, costless_dev = f"priced-{seed.suffix}@example.com", f"costless-{seed.suffix}@example.com"
    try:
        async with engine.acquire() as conn:
            await conn.execute(_DIMS_SQL, seed.sid(priced), seed.now, seed.now, priced_dev, "p")
            await conn.execute(_DIMS_SQL, seed.sid(costless), seed.now, seed.now, costless_dev, "c")
        await seed.cost(engine, priced, seed.now.date())
        await seed.turn(engine, costless, seed.now.date())
        window = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE,
            start_dt=_naive(seed.now - timedelta(days=1)),
            end_dt=_naive(seed.now + timedelta(hours=1)),
            users=None,
        )
        reader = PostgresCliAnalyticsReader(engine)

        users = await reader.get_users(window)
        names = [u["developer_name"] for u in users]

        assert names.index(priced_dev) < names.index(costless_dev)
    finally:
        await _cleanup(engine, [seed.sid(priced), seed.sid(costless)])
        await engine.close()


@pytest.mark.asyncio
async def test_get_users_and_get_users_daily_activity_agree_on_a_costless_sessions_developer():
    # A costless session with no session_dims email and no cost_daily row, attributed only
    # through its lines_daily row, must resolve to the same developer_name in both get_users (the
    # per-developer universe) and get_users_daily_activity (the per-day universe) -- not 'unknown' in
    # one and the real email in the other.
    engine = await _engine()
    seed = _Seed()
    name = "lines-only-developer"
    developer_email = f"lines-developer-{seed.suffix}@example.com"
    try:
        async with engine.acquire() as conn:
            await conn.execute(_DIMS_SQL, seed.sid(name), seed.now, seed.now, "", f"session {name}")
        await seed.lines(engine, name, seed.now.date())
        async with engine.acquire() as conn:
            await conn.execute(
                "UPDATE lines_daily SET user_email = $1 WHERE session_id = $2", developer_email, seed.sid(name)
            )
        window = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE,
            start_dt=_naive(seed.now - timedelta(days=1)),
            end_dt=_naive(seed.now + timedelta(hours=1)),
            users=None,
        )
        reader = PostgresCliAnalyticsReader(engine)

        (users,) = [u for u in await reader.get_users(window) if seed.sid(name) in u["session_ids"]]
        daily = [d for d in await reader.get_users_daily_activity(window) if d["day"] == seed.now.date()]
        (matching_daily,) = [d for d in daily if d["developer_name"] == users["developer_name"]]

        assert users["developer_name"] == developer_email
        assert matching_daily["session_count"] >= 1
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_vs_code_restart_session_with_only_start_and_env_hooks_is_absent_once_stale():
    # A session that only ever got agent.session.start and agent.session.env (a VS Code restart
    # reopening a Claude Code panel, or a Copilot session closed without a prompt) has a session_dims
    # row and nothing else, ever -- seeded here through real hook_events rows and the actual rollup, so
    # this proves what those two hooks really leave, not an assumption about it. Proven end-to-end
    # through the handler (not just the reader) that this is absent from both get_sessions and
    # get_overview's total_sessions once the window no longer covers the session's start. A window that
    # still covers the session's start is the residual case — see
    # test_a_fresh_empty_session_is_excluded_from_the_sessions_list_and_overview_totals below.
    #
    # This lives alongside the other live-Postgres tests of this file rather than in
    # test_cli_analytics_handler.py (the plan's literal file choice): that file is a pure mock-based unit
    # suite with no live-Postgres fixture, and this scenario is only meaningful end-to-end against a real
    # database the way the rest of this file already seeds one.
    engine = await _engine()
    seed = _Seed()
    name = "vscode-restart-empty"
    try:
        started_at = seed.now - timedelta(days=10)
        await _seed_via_hooks(
            engine,
            seed.sid(name),
            [("agent.session.start", started_at), ("agent.session.env", started_at)],
            seed.email,
        )
        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)

        sessions, _, _ = await handler.get_sessions(seed.window(), 0, 1000, "start_time", None)
        overview, _, _ = await handler.get_overview(seed.window())

        assert seed.sid(name) not in {s["trace_id"] for s in sessions["sessions"]}
        assert overview["kpis"]["total_sessions"] == 0
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_fresh_empty_session_is_excluded_from_the_sessions_list_and_overview_totals():
    # A session whose started_at falls INSIDE the window being viewed (e.g. "today") but that only ever got
    # session.start/session.env hooks, never any real activity, is excluded from `sel` entirely -- not
    # merely flagged dead -- so it is absent from the Sessions list and from Overview's total_sessions
    # and dead_sessions for that window. Seeded through real hook_events rows and the actual rollup, like
    # the sibling test above, so this proves what those two hooks really leave.
    engine = await _engine()
    seed = _Seed()
    name = "fresh-empty-residual"
    try:
        await _seed_via_hooks(
            engine,
            seed.sid(name),
            [("agent.session.start", seed.now), ("agent.session.env", seed.now)],
            seed.email,
        )
        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)

        sessions, _, _ = await handler.get_sessions(seed.window(), 0, 1000, "start_time", None)
        overview, _, _ = await handler.get_overview(seed.window())

        assert seed.sid(name) not in {s["trace_id"] for s in sessions["sessions"]}
        assert overview["kpis"]["total_sessions"] == 0
        assert overview["kpis"]["dead_sessions"] == 0
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


def _between(start: datetime, end: datetime) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=_naive(start), end_dt=_naive(end))


@pytest.mark.asyncio
async def test_a_same_day_resume_with_no_new_prompt_is_not_listed_for_a_sub_day_window():
    # Ticket repro on one day: a prompt in the morning, then `--resume` and /exit with nothing typed inside
    # the last hour. The morning prompt gives the session a turns_daily row for today, which must not admit
    # it to a window that only holds its lifecycle hooks.
    engine = await _engine()
    seed = _Seed()
    sid = seed.sid("same-day-resume")
    try:
        await _seed_via_hooks(
            engine,
            sid,
            [
                ("agent.session.start", _NOON - timedelta(hours=3)),
                ("agent.prompt.submit", _NOON - timedelta(hours=3) + timedelta(minutes=1)),
                ("agent.session.start", _NOON - timedelta(minutes=20)),
                ("agent.session.end", _NOON - timedelta(minutes=10)),
            ],
        )
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, _between(_NOON - timedelta(hours=1), _NOON), sid) is False
    finally:
        await _cleanup(engine, [sid])
        await engine.close()


@pytest.mark.asyncio
async def test_a_session_that_started_after_a_sub_day_window_is_not_listed():
    engine = await _engine()
    seed = _Seed()
    sid = seed.sid("started-after-window")
    try:
        await _seed_via_hooks(
            engine,
            sid,
            [("agent.session.start", _NOON + timedelta(hours=1)), ("agent.prompt.submit", _NOON + timedelta(hours=1))],
        )
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, _between(_NOON - timedelta(hours=2), _NOON - timedelta(hours=1)), sid) is False
    finally:
        await _cleanup(engine, [sid])
        await engine.close()


@pytest.mark.asyncio
async def test_an_old_session_with_a_new_prompt_inside_a_sub_day_window_is_listed():
    engine = await _engine()
    seed = _Seed()
    sid = seed.sid("old-with-new-prompt")
    old = _NOON - timedelta(days=3)
    try:
        await _seed_via_hooks(engine, sid, [("agent.session.start", old), ("agent.prompt.submit", old)])
        await _seed_via_hooks(
            engine,
            sid,
            [
                ("agent.session.start", _NOON - timedelta(minutes=30)),
                ("agent.prompt.submit", _NOON - timedelta(minutes=25)),
            ],
        )
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, _between(_NOON - timedelta(hours=1), _NOON), sid) is True
    finally:
        await _cleanup(engine, [sid])
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", sorted(_RAW_ACTIVITY_SQL))
@pytest.mark.parametrize(("offset", "listed"), [(timedelta(minutes=30), True), (timedelta(hours=3), False)])
async def test_a_partial_window_day_admits_an_old_session_only_when_its_raw_activity_is_inside_the_window(
    source, offset, listed
):
    # The day row exists either way; only the time of the raw record that produced it decides.
    engine = await _engine()
    seed = _Seed()
    name = f"partial-day-{source}"
    try:
        await seed.dims(engine, name, _NOON - timedelta(days=10))
        await seed.turn(engine, name, _NOON.date())
        async with engine.acquire() as conn:
            await conn.execute(_RAW_ACTIVITY_SQL[source], seed.sid(name), _NOON - offset)
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, _between(_NOON - timedelta(hours=1), _NOON), seed.sid(name)) is listed
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()


@pytest.mark.asyncio
async def test_a_day_wholly_inside_the_window_admits_an_old_session_from_its_day_row_alone():
    # Whole days keep the day-row rule, so activity older than raw retention still counts.
    engine = await _engine()
    seed = _Seed()
    name = "whole-day"
    try:
        start = datetime.combine(_NOON.date() - timedelta(days=3), time(0), tzinfo=timezone.utc)
        await seed.dims(engine, name, _NOON - timedelta(days=10))
        await seed.turn(engine, name, _NOON.date() - timedelta(days=2))
        reader = PostgresCliAnalyticsReader(engine)

        assert await _is_listed(reader, _between(start, _NOON), seed.sid(name)) is True
    finally:
        await _cleanup(engine, [seed.sid(name)])
        await engine.close()
