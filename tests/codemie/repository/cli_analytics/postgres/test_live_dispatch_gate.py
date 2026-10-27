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

"""Live-Postgres checks for the denied-Agent gate, the slash-command skill-dispatch hook-fallback gate,
and the no-agentId fallback cost split.

The SQL-text tests in test_reader.py and test_rollups.py prove the query shape only. These write rows
shaped like real telemetry into the local analytics Postgres, run the real rollup refresh, then compare
two views of the same session: the rows the reader's SQL returns, and the session-detail response the
service builds from them (the response the REST route serves). Cleans up by session_id. There is no
default database: without TEST_LOCAL_PG_URL every test here is skipped, and with it a test is skipped
(not failed) when that Postgres is unreachable. Point TEST_LOCAL_PG_URL at a disposable PostgreSQL;
the tests migrate it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from unittest.mock import MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio

from codemie.configs.config import Config
from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import migrations, rollups
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.postgres.fork_attribution import tool_response_agent_id
from codemie.repository.cli_analytics.vocabulary import EventKind
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_TOOL, _EXECUTION, _INTERACTION = 1, 2, 3
_DAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)

_SPANS_SQL = """
    INSERT INTO spans
        (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
         session_id, tool_name, tool_use_id, subagent_type, status_code, resource_id)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 0, 0)
"""
# A Skill call as ingest stores it: subagent_type absent, so NULL (not '').
_SKILL_SPAN_SQL = """
    INSERT INTO spans
        (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
         session_id, tool_name, tool_use_id, subagent_type, skill_name, status_code, resource_id)
    VALUES ($1, 1, $2, $3, $4, 1, 'claude_code.tool', $5, 'Skill', $6, NULL, $7, 0, 0)
"""
# A request of `cost` USD: as many input tokens as that cost buys at _CATALOG's input price of 1 USD per MTok, so
# the money the handler prices equals the cost the producer stored.
_LOG_EVENTS_SQL = """
    INSERT INTO log_events
        (ts, session_id, event_kind, span_id, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, severity, resource_id, model, user_email)
    VALUES ($1, $2, $3, $4, $5::float8, round($5::float8 * 1000000)::bigint, 0, 0, 0, 0, 0, 'claude-sonnet-5-5', 'test@example.com')
"""
_CATALOG = [
    MagicMock(base_name="claude-sonnet-5-5", deployment_name="claude-sonnet-5-5", cost=CostConfig(input=1e-6, output=0))
]
# The agent.tool.end hook event: fires only on a completed call, unlike agent.tool.denied/.error.
_AGENT_TOOL_END_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type, tool_name, tool_use_id)
    VALUES ($1, $2, 'agent.tool.end', 'Agent', $3)
"""
# A main-thread slash-command skill (e.g. typing /code-review): no TOOL span, only this hook event.
_SKILL_DISPATCH_HOOK_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type, skill_name)
    VALUES ($1, $2, 'agent.skill.dispatch', $3)
"""
# A forked skill's SubagentStart, carrying the skill name the CLI reads from the fork's .meta.json sidecar.
_SUBAGENT_START_SKILL_HOOK_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type, skill_name, agent_type)
    VALUES ($1, $2, 'agent.subagent.start', $3, $4)
"""


async def _get_engine() -> AnalyticsPgEngine:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError) as exc:  # no reachable database: skip, do not fail
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    migrations.run_migrations(settings)  # idempotent: a migrated database is left as it is
    await _ensure_todays_partitions(engine, settings)
    return engine


async def _ensure_todays_partitions(engine: AnalyticsPgEngine, settings: AnalyticsPgSettings) -> None:
    """Create the day's partitions the way the maintenance job does. Only that step runs: a full
    maintenance pass also drops and purges old rows, which a test must never do to a shared database."""
    maintainer = PartitionMaintainer(engine, RetentionPolicy.from_settings(settings), clock=lambda: _DAY.date())
    async with engine.acquire() as conn:
        await maintainer._ensure_partitions(conn, _DAY.date(), MaintenanceReport())


@pytest_asyncio.fixture
async def engine():
    """The migrated analytics engine, held under the live rollup lock for the test's duration: a
    refresh recomputes the whole queue of the one shared database."""
    analytics = await _get_engine()
    async with live_rollup_lock(analytics):
        with patch(
            "codemie.service.analytics.handlers.coding_agent_pricing.llm_service.get_all_llm_model_info",
            return_value=_CATALOG,
        ):
            yield analytics
    await analytics.close()


async def _cleanup(engine: AnalyticsPgEngine, session_id: str) -> None:
    """Remove the session from every analytics table that has a session_id: raw rows, rollups and the
    rollup queue. A rollup row left behind would collide with the next run's refresh."""
    async with engine.acquire() as conn:
        tables = await conn.fetch(
            """SELECT DISTINCT table_name FROM information_schema.columns
               WHERE table_schema = 'codemie_analytics' AND column_name = 'session_id'"""
        )
        for row in tables:
            await conn.execute(f"DELETE FROM codemie_analytics.{row['table_name']} WHERE session_id = $1", session_id)


def _id(name: str) -> bytes:
    # A real span_id is a unique per-span OTel id; hash (not truncate) so two different names never
    # collapse to the same 8 bytes, which plain truncation did for any two names sharing a long
    # enough prefix (e.g. same session_id, different minute).
    return hashlib.sha1(name.encode()).digest()[:8]


_COSTS = ((1, 1.0), (20, 2.0))


async def _seed_turn(
    engine: AnalyticsPgEngine,
    session_id: str,
    tool_calls: list[tuple[Any, ...]],
    costs: tuple[tuple[int, float], ...] = _COSTS,
) -> None:
    """One interaction at 10:00 with API requests (by default 1.0 USD at minute 1 and 2.0 USD at minute 20),
    plus the Agent tool calls.

    Each tool call is (minute, tool_use_id, subagent_type). An agent.tool.end hook event is written for
    each call whose tool_use_id ends in 'ok': that is what marks a call as executed, since it fires only
    on a completed call, unlike agent.tool.denied/.error. Each cost is (minute, usd).
    """
    interaction = _id(f"int-{session_id}")
    trace = b"t" * 16
    async with engine.acquire() as conn:
        await conn.execute(
            _SPANS_SQL,
            _DAY,
            3_600_000_000_000,
            trace,
            interaction,
            None,
            _INTERACTION,
            "interaction",
            session_id,
            "",
            "",
            "",
        )
        for minute, tool_use_id, subagent_type in tool_calls:
            span_id = _id(f"tool-{session_id}-{minute}")
            await conn.execute(
                _SPANS_SQL,
                _DAY + timedelta(minutes=minute),
                1,
                trace,
                span_id,
                interaction,
                _TOOL,
                "claude_code.tool",
                session_id,
                "Agent",
                tool_use_id,
                subagent_type,
            )
            if tool_use_id.endswith("ok"):
                await conn.execute(
                    _AGENT_TOOL_END_SQL,
                    _DAY + timedelta(minutes=minute),
                    session_id,
                    tool_use_id,
                )
        for minute, cost in costs:
            await conn.execute(
                _LOG_EVENTS_SQL,
                _DAY + timedelta(minutes=minute),
                session_id,
                int(EventKind.API_REQUEST),
                interaction,
                cost,
            )


async def _refresh_rollups(engine: AnalyticsPgEngine) -> None:
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: _DAY.date())
    async with engine.acquire() as conn:
        await refresher.mark_dirty(_DAY.date(), _DAY.date(), conn)
        await refresher.refresh(conn)


async def _sql_agents(engine: AnalyticsPgEngine, session_id: str) -> list[dict[str, Any]]:
    rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id)
    return [r for r in rows if r["subagent_type"]]


async def _rollup_agent_counts(engine: AnalyticsPgEngine, session_id: str, subtype: str) -> tuple[int, int]:
    """tool_facts_daily.agent_count and the kind-3 invocations_hourly calls of `subtype` for the session."""
    async with engine.acquire() as conn:
        agent_count = await conn.fetchval(
            "SELECT coalesce(sum(agent_count), 0) FROM codemie_analytics.tool_facts_daily WHERE session_id = $1",
            session_id,
        )
        subtype_calls = await conn.fetchval(
            """SELECT coalesce(sum(calls), 0) FROM codemie_analytics.invocations_hourly
               WHERE session_id = $1 AND kind = 3 AND name = $2""",
            session_id,
            subtype,
        )
    return agent_count, subtype_calls


async def _rollup_skill_counts(engine: AnalyticsPgEngine, session_id: str, skill_name: str) -> tuple[int, int]:
    """tool_facts_daily.skill_count and the kind-2 invocations_hourly calls of `skill_name` for the session."""
    async with engine.acquire() as conn:
        skill_count = await conn.fetchval(
            "SELECT coalesce(sum(skill_count), 0) FROM codemie_analytics.tool_facts_daily WHERE session_id = $1",
            session_id,
        )
        name_calls = await conn.fetchval(
            """SELECT coalesce(sum(calls), 0) FROM codemie_analytics.invocations_hourly
               WHERE session_id = $1 AND kind = 2 AND name = $2""",
            session_id,
            skill_name,
        )
    return skill_count, name_calls


async def _api_detail(engine: AnalyticsPgEngine, session_id: str) -> dict[str, Any]:
    detail, _, _ = await LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)).get_session_detail(session_id)
    assert detail is not None, "the session must exist in the session rollups after the refresh"
    return detail


@pytest.mark.asyncio
async def test_live_denied_agent_call_is_absent_from_sql_and_api_and_takes_no_cost(engine):
    """The denied call (no agent.tool.end hook event) is not a dispatch in the SQL rows or in the API response, and
    it does not open the cost window: the 0.5 USD request at minute 7, after the denied call (minute 5) and
    before the executed one (minute 10), stays out, so the window keeps 2.0 USD, not 2.5."""
    session_id = "test-epmcdme-15557-live-denied"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(
            engine,
            session_id,
            [(5, "denied", "Explore"), (10, "ok", "Explore")],
            costs=((1, 1.0), (7, 0.5), (20, 2.0)),
        )
        await _refresh_rollups(engine)

        sql_agents = await _sql_agents(engine, session_id)
        detail = await _api_detail(engine, session_id)
        api_agents = [d for d in detail["dispatches"] if d["kind"] == "agent"]

        assert len(sql_agents) == len(api_agents) == 1
        assert api_agents[0]["label"] == "agent · Explore"
        # start_offset_ms is not checked: a spans-only seed has no session start, so the offset is epoch-based
        assert api_agents[0]["cost_usd"] == pytest.approx(2.0)
        assert float(sql_agents[0]["cost_usd"]) == pytest.approx(api_agents[0]["cost_usd"])
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_agent_count_in_api_matches_the_rollup_and_skips_denied_calls(engine):
    """The response's agent_count and the rollup's agent_count both count only the executed calls. The
    evidence is looked for from 1 hour before the call to 1 day after it: an agent.tool.end or a
    TOOL_EXECUTION span 23 hours later still proves the call ran, one 2 days later or an agent.tool.end
    2 hours earlier does not."""
    session_id = "test-epmcdme-15557-live-counts"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(
            engine,
            session_id,
            [
                (5, "denied", "Explore"),
                (10, "ok", "Explore"),
                (30, "late", "Explore"),
                (40, "next-day", "Explore"),
                (50, "exec-late", "Explore"),
                (55, "exec-next-day", "Explore"),
                (180, "early", "Explore"),
            ],
        )
        async with engine.acquire() as conn:
            await conn.execute(_AGENT_TOOL_END_SQL, _DAY + timedelta(minutes=60), session_id, "early")
            await conn.execute(_AGENT_TOOL_END_SQL, _DAY + timedelta(minutes=30, days=2), session_id, "late")
            await conn.execute(_AGENT_TOOL_END_SQL, _DAY + timedelta(minutes=40, hours=23), session_id, "next-day")
            for ts, tool_use_id in (
                (_DAY + timedelta(minutes=50, days=2), "exec-late"),
                (_DAY + timedelta(minutes=55, hours=23), "exec-next-day"),
            ):
                await conn.execute(
                    _SPANS_SQL, ts, 1, b"t" * 16, _id(f"exec-{tool_use_id}"), None, _EXECUTION, "tool.execution",
                    session_id, "Agent", tool_use_id, "",
                )  # fmt: skip
        await _refresh_rollups(engine)

        detail = await _api_detail(engine, session_id)
        rollup_count, subtype_calls = await _rollup_agent_counts(engine, session_id, "Explore")

        assert rollup_count == detail["agent_count"] == 3
        assert subtype_calls == 3
        # The offset names the call: an agent bar starts at its launch ack (agent.tool.end), else at its tool span,
        # counted from the session start at minute 0 (the interaction span). On time: its end at minute 10 -> 10 min;
        # execution span 23 h later: no end, its span at minute 55 -> 55 min; next-day end: its end at minute 40 + 23 h
        # -> 23 h 40 min. An excluded call would add a row of its own: late at 2 days 30 min, exec-late at 50 min,
        # early at 60 min, denied (a tool span at minute 5, no end and no execution span) at 5 min.
        assert [(d["label"], d["start_offset_ms"]) for d in detail["dispatches"] if d["kind"] == "agent"] == [
            ("agent · Explore #1", 600_000),
            ("agent · Explore #2", 3_300_000),
            ("agent · Explore #3", 85_200_000),
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_agent_without_tool_use_id_still_appears_in_sql_and_api(engine):
    """Without a tool_use_id a denial cannot be proven, so the call stays a dispatch."""
    session_id = "test-epmcdme-15557-live-no-id"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(engine, session_id, [(15, "", "Plan")])
        await _refresh_rollups(engine)

        sql_agents = await _sql_agents(engine, session_id)
        detail = await _api_detail(engine, session_id)
        api_agents = [d for d in detail["dispatches"] if d["kind"] == "agent"]

        assert [r["subagent_type"] for r in sql_agents] == ["Plan"]
        assert [d["label"] for d in api_agents] == ["agent · Plan"]
        assert await _rollup_agent_counts(engine, session_id, "Plan") == (1, 1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_session_whose_only_agent_call_is_denied_has_no_dispatch_and_no_agent(engine):
    """With its only Agent call denied, the session has no Agent dispatch and agent_count 0."""
    session_id = "test-epmcdme-15557-live-only-denied"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(engine, session_id, [(5, "denied", "Explore")])
        await _refresh_rollups(engine)

        detail = await _api_detail(engine, session_id)

        assert await _sql_agents(engine, session_id) == []
        assert [d for d in detail["dispatches"] if d["kind"] == "agent"] == []
        assert detail["agent_count"] == 0
        assert await _rollup_agent_counts(engine, session_id, "Explore") == (0, 0)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_an_otel_only_agent_call_with_no_hook_events_still_counts(engine):
    """A session with no plugin hook rows at all (OTel-only ingest) proves a call ran with its
    TOOL_EXECUTION span alone: the denial gate must not mistake "no agent.tool.end hook event" for
    "denied" when there are no hook events in the session to begin with."""
    session_id = "test-epmcdme-15557-live-otel-only"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(engine, session_id, [])
        interaction = _id(f"int-{session_id}")
        tool_use_id = "otel-only-call"
        span_id = _id(f"tool-{session_id}-otel")
        exec_id = _id(f"exec-{session_id}-otel")
        async with engine.acquire() as conn:
            await conn.execute(
                _SPANS_SQL,
                _DAY + timedelta(minutes=5),
                1,
                b"t" * 16,
                span_id,
                interaction,
                _TOOL,
                "claude_code.tool",
                session_id,
                "Agent",
                tool_use_id,
                "Explore",
            )
            await conn.execute(
                """INSERT INTO spans
                    (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
                     session_id, tool_name, tool_use_id, success, status_code, resource_id)
                   VALUES ($1, 30000000000, $2, $3, $4, 2, 'tool_execution', $5, '', $6, 'true', 0, 0)""",
                _DAY + timedelta(minutes=5, seconds=30),
                b"t" * 16,
                exec_id,
                span_id,
                session_id,
                tool_use_id,
            )
        await _refresh_rollups(engine)

        sql_agents = await _sql_agents(engine, session_id)
        detail = await _api_detail(engine, session_id)
        api_agents = [d for d in detail["dispatches"] if d["kind"] == "agent"]

        assert [r["subagent_type"] for r in sql_agents] == ["Explore"]
        assert [d["label"] for d in api_agents] == ["agent · Explore"]
        assert await _rollup_agent_counts(engine, session_id, "Explore") == (1, 1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_an_agent_call_that_ran_and_errored_still_counts(engine):
    """A call whose subagent ran and then failed or was interrupted gets agent.tool.error, never
    agent.tool.end — it must still count as a dispatch, unlike a denied call which gets neither."""
    session_id = "test-epmcdme-15557-live-tool-error"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(engine, session_id, [(5, "errored", "Explore")])
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, tool_name, tool_use_id) "
                "VALUES ($1, $2, 'agent.tool.error', 'Agent', $3)",
                _DAY + timedelta(minutes=5, seconds=1),
                session_id,
                "errored",
            )
        await _refresh_rollups(engine)

        sql_agents = await _sql_agents(engine, session_id)
        detail = await _api_detail(engine, session_id)
        api_agents = [d for d in detail["dispatches"] if d["kind"] == "agent"]

        assert [r["subagent_type"] for r in sql_agents] == ["Explore"]
        assert [d["label"] for d in api_agents] == ["agent · Explore"]
        assert await _rollup_agent_counts(engine, session_id, "Explore") == (1, 1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_skill_dispatch_hook_counts_despite_unrelated_tool_spans_same_day(engine):
    """A main-thread slash-command skill's agent.skill.dispatch hook event — no
    matching TOOL span, since ingestion never creates one for this signal — must still count toward
    skill_count and invocations_hourly on a day the session also has ordinary, unrelated Agent tool-
    call spans: the skill branch is not gated by the blanket _NO_SPAN_OF_KIND, which a day with any tool span
    fails (skill_count/calls both 0, 'command · code-review' absent from dispatches under that gate). A
    dispatch sourced from this hook-only branch is labelled 'command', not 'skill': the
    reader flags it `is_slash_command=1`, which the handler maps to kind='command' (see
    `_dispatch_label_and_kind`) — unlike the span-sourced Skill-tool_use case covered by
    `test_live_a_skill_call_counts_only_with_evidence_it_ran` below, which stays kind='skill'.

    A real TOOL span and a skill-dispatch hook event on the same session+day both emit a tool_facts_daily row
    for one (day, session_id) key, and `_replace_daily`'s plain DELETE+INSERT has no merge across UNION ALL
    arms: the refresh sums the arms before the INSERT, so it raises no UniqueViolationError."""
    session_id = "test-epmcdme-15968-live-skill-hook"
    skill_name = "code-review"
    try:
        await _cleanup(engine, session_id)
        # An ordinary, unrelated Agent dispatch: a span the blanket _NO_SPAN_OF_KIND gate would stop the skill
        # branch on.
        await _seed_turn(engine, session_id, [(5, "ok", "Explore")])
        async with engine.acquire() as conn:
            await conn.execute(_SKILL_DISPATCH_HOOK_SQL, _DAY + timedelta(minutes=6), session_id, skill_name)
        await _refresh_rollups(engine)

        skill_count, name_calls = await _rollup_skill_counts(engine, session_id, skill_name)
        detail = await _api_detail(engine, session_id)
        command_dispatches = [d for d in detail["dispatches"] if d["kind"] == "command"]

        assert skill_count == 1
        assert name_calls == 1
        assert detail["skill_count"] == 1
        assert [d["label"] for d in command_dispatches] == [f"command · {skill_name}"]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_hook_only_ordinary_agent_is_counted_not_dropped_by_null_skill_name(engine):
    """A hook-only session (no spans) with an ordinary agent.subagent.start — skill_name left NULL
    by ingest, as it is for every non-skill dispatch — counts in tool_facts_daily.agent_count and
    invocations_hourly kind 3: the skill-tag test reads skill_name through coalesce, since
    `NOT (NULL <> '' AND ...)` is NULL, which a FILTER/WHERE treats as false."""
    session_id = "test-epmcdme-15968-live-hook-only-agent"
    try:
        await _cleanup(engine, session_id)
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, agent_type, skill_name) "
                "VALUES ($1, $2, 'agent.subagent.start', $3, NULL)",
                _DAY + timedelta(minutes=5),
                session_id,
                "general-purpose",
            )
            await conn.execute(
                _LOG_EVENTS_SQL,
                _DAY + timedelta(minutes=1),
                session_id,
                int(EventKind.API_REQUEST),
                None,
                1.0,
            )
        await _refresh_rollups(engine)

        agent_count, subtype_calls = await _rollup_agent_counts(engine, session_id, "general-purpose")

        assert (agent_count, subtype_calls) == (1, 1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_subagent_start_hook_with_skill_name_counts_as_skill_not_agent(engine):
    """An agent.subagent.start carrying a forked skill's name is a skill, never also an agent: it counts in
    skill_count and invocations_hourly kind 2, not in agent_count or kind 3. A real unrelated Explore dispatch
    stays an agent, untouched: the classification is per dispatch, not session-wide. The session detail's
    agent_count/skill_count corrections add nothing for a name the rollup already counts."""
    session_id = "test-epmcdme-15968-live-subagent-skill"
    skill_name = "code-review"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(engine, session_id, [(5, "ok", "Explore")])  # a real, unrelated agent dispatch
        async with engine.acquire() as conn:
            await conn.execute(
                _SUBAGENT_START_SKILL_HOOK_SQL,
                _DAY + timedelta(minutes=6),
                session_id,
                skill_name,
                "general-purpose",
            )
        await _refresh_rollups(engine)

        skill_count, skill_calls = await _rollup_skill_counts(engine, session_id, skill_name)
        agent_count, explore_calls = await _rollup_agent_counts(engine, session_id, "Explore")
        detail = await _api_detail(engine, session_id)
        command_dispatches = [d for d in detail["dispatches"] if d["kind"] == "command"]
        agent_dispatches = [d for d in detail["dispatches"] if d["kind"] == "agent"]

        assert (skill_count, skill_calls) == (1, 1)
        assert (agent_count, explore_calls) == (1, 1)  # the unrelated Explore dispatch, untouched
        assert detail["skill_count"] == 1
        assert detail["agent_count"] == 1
        assert [d["label"] for d in command_dispatches] == [f"command · {skill_name}"]
        assert [d["label"] for d in agent_dispatches] == ["agent · Explore"]
        assert _dispatch_rows(detail) == [
            ("agent · Explore", "agent", None, 2.0),
            ("command · code-review", "command", None, None),
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_two_no_agent_id_dispatches_sharing_a_parent_split_cost_not_duplicate_it(engine):
    """Two Agent calls sharing one parent span, neither
    with a resolvable agentId (no agent.tool.start/agent.subagent.stop hooks, so `launched` finds no
    agent_id to extract), must not each take the parent's whole API-request sum. One $1.00 request
    lands after both dispatches started: only the later dispatch's window (from its own start, since
    it is the last sibling, unbounded) contains it. The earlier dispatch's window closes at the later
    one's start and contains no request at all, so it gets NULL cost (the same "no data in this
    dispatch's window" NULL the known-agentId path already returns, not a guessed 0) — never the same
    $1.00 the other dispatch shows."""
    session_id = "test-epmcdme-15576-live-fallback-split"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(
            engine,
            session_id,
            [(5, "explore-ok", "Explore"), (10, "review-ok", "Review")],
            costs=((20, 1.0),),
        )
        await _refresh_rollups(engine)

        sql_agents = await _sql_agents(engine, session_id)
        detail = await _api_detail(engine, session_id)
        api_agents = [d for d in detail["dispatches"] if d["kind"] == "agent"]

        assert len(sql_agents) == len(api_agents) == 2
        costs_by_subtype = {r["subagent_type"]: r["cost_usd"] for r in sql_agents}
        assert costs_by_subtype["Explore"] is None
        assert costs_by_subtype["Review"] == pytest.approx(1.0)
        assert sum(c or 0 for c in costs_by_subtype.values()) == pytest.approx(detail["cost_usd"])
        # The handler prices each window at the catalog: the 1.0 USD request at 1 USD per MTok of input.
        assert _agent_costs(detail) == {"agent · Explore": None, "agent · Review": pytest.approx(1.0)}
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_session_detail_agent_count_correction_does_not_overcount_forever_on_a_day_with_spans(engine):
    """get_session_detail_scalars's agent_count hook correction
    must apply only where the rollup's own kind-3 hook branch would also run — a day with no
    TOOL/TOOL_EXECUTION/INTERACTION span at all (_NO_SPAN_OF_KIND in rollups.py). On a day that has
    spans (an ordinary, unrelated Explore dispatch here), that branch never runs, so the rollup can
    never "catch up" to an ordinary agent.subagent.start with no skill_name and no matching Agent
    span: the correction adds nothing there, as it has no backing row in tool_facts_daily or
    invocations_hourly and no row in the dispatch timeline either."""
    session_id = "test-epmcdme-15968-live-scalars-no-catchup"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(engine, session_id, [(5, "ok", "Explore")])  # the day has a span
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, agent_type, skill_name) "
                "VALUES ($1, $2, 'agent.subagent.start', $3, NULL)",
                _DAY + timedelta(minutes=7),
                session_id,
                "general-purpose",
            )
        await _refresh_rollups(engine)

        detail = await _api_detail(engine, session_id)
        explore_count, explore_calls = await _rollup_agent_counts(engine, session_id, "Explore")
        gp_count, gp_calls = await _rollup_agent_counts(engine, session_id, "general-purpose")

        assert (explore_count, explore_calls) == (1, 1)
        assert (gp_count, gp_calls) == (1, 0)  # the rollup never counted it: no matching span, no catch-up
        assert detail["agent_count"] == 1  # must match the rollup, not add a permanent phantom +1
        assert [d["kind"] for d in detail["dispatches"] if d["kind"] == "agent"] == ["agent"]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_same_skill_name_from_a_span_and_a_slash_dispatch_both_count_same_session(engine):
    """A skill-dispatch hook event (agent.skill.dispatch or a
    skill-tagged agent.subagent.start) never has a TOOL span of its own, unlike agent.tool.start — so
    a real Skill-tool call and a later, unrelated slash-command dispatch of the exact same skill name
    in one session must both count, not just one. Both rows land in the same hour and raise no
    UniqueViolationError on invocations_hourly's (hour, session_id, kind, name) primary key."""
    session_id = "test-epmcdme-15968-live-skill-name-both-count"
    skill_name = "code-review"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(engine, session_id, [])
        await _seed_skill_span(engine, session_id, _DAY + timedelta(minutes=5), "skill-call-1", skill_name)
        async with engine.acquire() as conn:
            await conn.execute(_SKILL_DISPATCH_HOOK_SQL, _DAY + timedelta(minutes=15), session_id, skill_name)
        await _refresh_rollups(engine)

        skill_count, name_calls = await _rollup_skill_counts(engine, session_id, skill_name)
        detail = await _api_detail(engine, session_id)

        assert skill_count == 2
        assert name_calls == 2
        assert detail["skill_count"] == 2
        kinds = sorted(d["kind"] for d in detail["dispatches"] if d["label"].endswith(skill_name))
        assert kinds == ["command", "skill"]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_forked_skill_dispatch_takes_its_cost_and_duration_from_its_own_agent_id(engine):
    """A forked skill (a skill-tagged agent.subagent.start)
    carries the same agentId an ordinary Agent dispatch's hook event does, so its real spend and run
    time must come from the same agent_usage/stopped lookups by agentId, not render as a free,
    instant row.

    The reader row alone isn't enough: the session-detail API response (what the timeline actually
    renders) is a second, separate gate — the handler's _duration_for_kind reads real_duration_ms for
    this row's "command" kind, so the API's duration_ms is checked beside the reader row."""
    session_id = "test-epmcdme-15968-live-forked-skill-cost"
    skill_name = "code-review"
    agent_id = "agentid-forked-skill"
    try:
        await _cleanup(engine, session_id)
        await _seed_turn(engine, session_id, [], costs=())
        llm_req_span = _id(f"llmreq-{session_id}")
        group_span = _id(f"group-{session_id}")
        async with engine.acquire() as conn:
            await conn.execute(
                _SUBAGENT_START_SKILL_HOOK_SQL,
                _DAY + timedelta(minutes=5),
                session_id,
                skill_name,
                "general-purpose",
            )
            await conn.execute(
                "UPDATE hook_events SET agent_id = $1 WHERE session_id = $2 AND event_type = 'agent.subagent.start'",
                agent_id,
                session_id,
            )
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, agent_id) VALUES ($1, $2, "
                "'agent.subagent.stop', $3)",
                _DAY + timedelta(minutes=8),
                session_id,
                agent_id,
            )
            await conn.execute(
                "INSERT INTO spans (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name, "
                "session_id, attrs, status_code, resource_id) VALUES ($1, 1, $2, $3, $4, 4, 'llm_request', $5, "
                "jsonb_build_object('agent_id', $6::text), 0, 0)",
                _DAY + timedelta(minutes=6),
                b"t" * 16,
                llm_req_span,
                group_span,
                session_id,
                agent_id,
            )
            await conn.execute(
                _LOG_EVENTS_SQL,
                _DAY + timedelta(minutes=6, seconds=30),
                session_id,
                int(EventKind.API_REQUEST),
                group_span,
                3.5,
            )
        await _refresh_rollups(engine)

        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id)
        [row] = [r for r in rows if r["skill_name"] == skill_name]

        assert row["cost_usd"] == pytest.approx(3.5)
        assert row["subagent_duration_ms"] == 180_000  # minute 5 to minute 8

        detail = await _api_detail(engine, session_id)
        [api_row] = [d for d in detail["dispatches"] if d["kind"] == "command" and d["label"].endswith(skill_name)]

        assert api_row["cost_usd"] == pytest.approx(3.5)
        assert api_row["duration_ms"] == 180_000  # minute 5 to minute 8, same as subagent_duration_ms
    finally:
        await _cleanup(engine, session_id)


# ── forked skills: one skill invocation counts once ──
# Hook events in the field shape the codemie-code plugin sends (PostToolUse / SubagentStart as the raw hook
# payload with `platform`, tool_input / tool_response as JSON strings cut to 300 chars; UserPromptExpansion
# as agent.skill.dispatch with its fixed fields and prompt_id), filled with the values of a live Claude Code
# 2.1.302 capture, stored through the ingest path.

_FORK_SKILL = "probe-fork"
_BACKGROUND_FORK_ID = "a861e999c42f7f90c"
_FOREGROUND_FORK_ID = "a097aad50ce13b18b"
_SKILL_PROMPT_ID = "322d4da8-def0-4f4a-be42-146d8d22aae6"
_SLASH_PROMPT_ID = "288507e7-4c09-4d99-b7d1-3c1e0f077221"
_SLASH_FORK_ID = "a01e30c7b42a0bdac"
_SKILL_TOOL_USE_ID = "toolu_01NfuBSHJgod1LUWmjd7r2dG"
_BACKGROUND_RESULT = (
    '{"success":true,"commandName":"probe-fork","status":"forked","background":true,'
    '"agentId":"a861e999c42f7f90c","result":"Running in the background as @probe-fork"}'
)
_FOREGROUND_RESULT = (
    '{"success":true,"commandName":"probe-fork","status":"forked","agentId":"a097aad50ce13b18b","result":"forked."}'
)
_AGENT_RESULT = (
    '{"isAsync":true,"status":"async_launched","agentId":"a9d8798d295da2cf0","description":"Answer 2+2",'
    '"resolvedModel":"claude-haiku-5-5","prompt":"What is 2+2? Reply with the answer only.",'
    '"outputFile":"/private/tmp/claude-501/-Users-developerdeveloper--claude-jobs-fe70423b-tmp-probe-cwd5/'
    'f3c3dc3f-1c98-4018-a30c-a027255e1e09/tasks/a9d8798d295da2cf0.output","canReadOutputFile":true,'
    '"canContinueAgent":true}'
)
_CUT_RESULT = '{"success":true,"commandName":"probe-fork","status":"forked","background":true,"agentId":"a861e999c4'
_CLAUDE_CODE = {"platform": "claude-code"}


def _session(case: str) -> str:
    # The ingest ledger keeps every record's identity (it has no session_id to clean up by): a fresh session
    # per run keeps a re-run on the same database from being skipped as a re-send.
    return f"test-forked-skill-{case}-{uuid.uuid4().hex[:8]}"


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _hook(
    event_type: str, hook_event_name: str, session_id: str, ts: datetime, platform: dict[str, str], **fields
) -> dict[str, Any]:
    return {
        "type": event_type,
        "timestamp": _iso(ts),
        "session_id": session_id,
        "hook_event_name": hook_event_name,
        **platform,
        **fields,
    }


def _skill_call(
    session_id: str,
    ts: datetime,
    tool_response: str,
    *,
    tool_use_id: str = _SKILL_TOOL_USE_ID,
    tagged: bool = True,
    platform: dict[str, str] = _CLAUDE_CODE,
) -> list[dict[str, Any]]:
    """PreToolUse(Skill) and PostToolUse(Skill) of one model call; `tagged` — the start carries skill_name."""
    common = {
        "prompt_id": _SKILL_PROMPT_ID,
        "tool_name": "Skill",
        "tool_input": json.dumps({"skill": _FORK_SKILL}),
        "tool_use_id": tool_use_id,
    }
    start = _hook("agent.tool.start", "PreToolUse", session_id, ts, platform, **common)
    if tagged:
        start["skill_name"] = _FORK_SKILL
    end = _hook(
        "agent.tool.end",
        "PostToolUse",
        session_id,
        ts + timedelta(milliseconds=75),
        platform,
        **common,
        tool_response=tool_response,
        duration_ms=12,
    )
    return [start, end]


def _fork(
    session_id: str,
    ts: datetime,
    agent_id: str,
    prompt_id: str,
    skill_name: str | None = _FORK_SKILL,
    platform: dict[str, str] = _CLAUDE_CODE,
) -> dict[str, Any]:
    """SubagentStart of a fork; skill_name comes from the fork's marker, absent for a foreground fork."""
    event = _hook(
        "agent.subagent.start",
        "SubagentStart",
        session_id,
        ts,
        platform,
        prompt_id=prompt_id,
        agent_id=agent_id,
        agent_type="general-purpose",
    )
    if skill_name is not None:
        event["skill_name"] = skill_name
    return event


def _dispatch(
    session_id: str,
    ts: datetime,
    prompt_id: str,
    skill_name: str = _FORK_SKILL,
    platform: dict[str, str] = _CLAUDE_CODE,
) -> dict[str, Any]:
    """UserPromptExpansion of a slash command."""
    return _hook(
        "agent.skill.dispatch",
        "UserPromptExpansion",
        session_id,
        ts,
        platform,
        expansion_type="slash_command",
        command_name=skill_name,
        command_source="projectSettings",
        skill_name=skill_name,
        prompt_id=prompt_id,
    )


async def _ingest(engine: AnalyticsPgEngine, events: list[dict[str, Any]]) -> None:
    ingestor = PostgresTelemetryIngestor(engine, clock=lambda: _DAY.date())
    await ingestor.ingest_hook_events(events, "test@example.com", 0)


async def _seed_request(engine: AnalyticsPgEngine, session_id: str) -> None:
    """One API request at 10:01 and no span: a session the detail serves whose day stays a fallback pair."""
    async with engine.acquire() as conn:
        await conn.execute(
            _LOG_EVENTS_SQL, _DAY + timedelta(minutes=1), session_id, int(EventKind.API_REQUEST), None, 1.0
        )


async def _counts(engine: AnalyticsPgEngine, session_id: str, skill_name: str = _FORK_SKILL) -> dict[str, Any]:
    """Skill and agent counts of the session in every place that holds them."""
    skill_count, skill_calls = await _rollup_skill_counts(engine, session_id, skill_name)
    async with engine.acquire() as conn:
        agent_count = await conn.fetchval(
            "SELECT coalesce(sum(agent_count), 0) FROM codemie_analytics.tool_facts_daily WHERE session_id = $1",
            session_id,
        )
        agent_rows = await conn.fetchval(
            "SELECT count(*) FROM codemie_analytics.invocations_hourly WHERE session_id = $1 AND kind = 3",
            session_id,
        )
    detail = await _api_detail(engine, session_id)
    return {
        "skill_count": skill_count,
        "skill_calls": skill_calls,
        "agent_count": agent_count,
        "agent_rows": agent_rows,
        "api_skill_count": detail["skill_count"],
        "api_agent_count": detail["agent_count"],
    }


def _expected(skills: int, agents: int = 0, skill_calls: int | None = None, agent_rows: int = 0) -> dict[str, Any]:
    return {
        "skill_count": skills,
        "skill_calls": skills if skill_calls is None else skill_calls,
        "agent_count": agents,
        "agent_rows": agent_rows,
        "api_skill_count": skills,
        "api_agent_count": agents,
    }


def _stop(session_id: str, ts: datetime, agent_id: str, platform: dict[str, str] = _CLAUDE_CODE) -> dict[str, Any]:
    """SubagentStop of a fork or an agent."""
    return _hook(
        "agent.subagent.stop", "SubagentStop", session_id, ts, platform, agent_id=agent_id, agent_type="general-purpose"
    )


async def _seed_fork_request(engine: AnalyticsPgEngine, session_id: str, agent_id: str, ts: datetime, cost: float):
    """The subagent's llm_request span carrying its agent_id, and its api_request on the span that groups them."""
    group = _id(f"group-{session_id}-{agent_id}")
    async with engine.acquire() as conn:
        await conn.execute(
            "INSERT INTO spans (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name, "
            "session_id, attrs, status_code, resource_id) VALUES ($1, 1, $2, $3, $4, 4, 'llm_request', $5, "
            "jsonb_build_object('agent_id', $6::text), 0, 0)",
            ts,
            b"t" * 16,
            _id(f"llm-{session_id}-{agent_id}"),
            group,
            session_id,
            agent_id,
        )
        await conn.execute(_LOG_EVENTS_SQL, ts, session_id, int(EventKind.API_REQUEST), group, cost)


def _agent_costs(detail: dict[str, Any]) -> dict[str, Any]:
    """The handler's catalog money of each agent dispatch of the session-detail response, by label."""
    return {d["label"]: d["cost_usd"] for d in detail["dispatches"] if d["kind"] == "agent"}


def _dispatch_rows(detail: dict[str, Any]) -> list[tuple[Any, ...]]:
    """The timeline rows of the session-detail response, in start order: (label, kind, duration_ms, cost_usd)."""
    return [
        (d["label"], d["kind"], d["duration_ms"], d["cost_usd"]) for d in detail["dispatches"] if d["kind"] != "session"
    ]


async def _skills_invoked(engine: AnalyticsPgEngine) -> list[dict[str, Any]]:
    start = datetime.combine(_DAY.date(), time())
    f = LocalAnalyticsFilter(start_dt=start, end_dt=start + timedelta(days=1))
    return (await LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)).get_tools(f))["skills_invoked"]


async def _session_dims(engine: AnalyticsPgEngine, session_id: str, column: str):
    async with engine.acquire() as conn:
        value = await conn.fetchval(
            f"SELECT {column} FROM codemie_analytics.session_dims WHERE session_id = $1", session_id
        )
    return json.loads(value) if value is not None else None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_response", "expected"),
    [
        # a background fork, as a raw hook payload holds it (spaces after the separators): 172 chars
        (json.dumps(json.loads(_BACKGROUND_RESULT)), _BACKGROUND_FORK_ID),
        (_FOREGROUND_RESULT, _FOREGROUND_FORK_ID),
        (_AGENT_RESULT[:300], "a9d8798d295da2cf0"),  # 405 chars, cut to 300 by the CLI
        (_CUT_RESULT, None),  # the cut falls inside the id
    ],
    ids=["skill-background", "skill-foreground", "agent-cut-to-300", "cut-inside-the-id"],
)
async def test_live_tool_response_agent_id_takes_only_a_complete_id(engine, tool_response, expected):
    async with engine.acquire() as conn:
        assert await conn.fetchval(f"SELECT {tool_response_agent_id('$1::text')}", tool_response) == expected


async def _seed_skill_span(
    engine: AnalyticsPgEngine,
    session_id: str,
    ts: datetime,
    tool_use_id: str,
    skill_name: str = _FORK_SKILL,
    executed: bool = True,
) -> None:
    """The Skill TOOL span of the call under the turn's interaction span and, for a call that ran, its
    TOOL_EXECUTION span."""
    async with engine.acquire() as conn:
        await conn.execute(
            _SKILL_SPAN_SQL,
            ts,
            b"t" * 16,
            _id(f"skill-{session_id}-{tool_use_id}"),
            _id(f"int-{session_id}"),
            session_id,
            tool_use_id,
            skill_name,
        )
    if executed:
        await _seed_execution_span(engine, session_id, ts + timedelta(milliseconds=1), tool_use_id)


async def _seed_execution_span(engine: AnalyticsPgEngine, session_id: str, ts: datetime, tool_use_id: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute(
            """INSERT INTO spans
                (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
                 session_id, tool_name, tool_use_id, success, status_code, resource_id)
               VALUES ($1, 12000000, $2, $3, $4, 2, 'tool_execution', $5, '', $6, 'true', 0, 0)""",
            ts,
            b"t" * 16,
            _id(f"exec-{session_id}-{tool_use_id}"),
            _id(f"skill-{session_id}-{tool_use_id}"),
            session_id,
            tool_use_id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [_CLAUDE_CODE, {}], ids=["claude-code", "no-platform"])
async def test_live_a_skill_call_and_its_fork_count_once_on_a_day_with_spans(engine, platform):
    """The model's Skill call (span, PreToolUse with skill_name, PostToolUse naming the fork's agentId) and its
    fork's SubagentStart with skill_name are one skill invocation, and the fork is no agent. An event without
    `platform` is Claude Code's. The fork's usage row keeps the skill scope it was sent with. On the timeline
    the invocation is one `skill` row with the fork's run and cost."""
    session_id = _session(f"span-{bool(platform)}")
    call_ts = _DAY + timedelta(minutes=5)
    fork_ts = call_ts + timedelta(milliseconds=75)
    try:
        await _seed_turn(engine, session_id, [])
        await _seed_skill_span(engine, session_id, call_ts, _SKILL_TOOL_USE_ID)
        await _seed_fork_request(engine, session_id, _BACKGROUND_FORK_ID, call_ts + timedelta(seconds=2), 0.25)
        usage = {
            "type": "agent.usage.request",
            "timestamp": _iso(call_ts + timedelta(seconds=3)),
            "session_id": session_id,
            "request_id": "req_011CTx9aB",
            "message_id": "msg_01H7Yq",
            "agent_id": _BACKGROUND_FORK_ID,
            "agent_type": "general-purpose",
            "scope_kind": "skill",
            "scope_name": _FORK_SKILL,
            "model": "claude-haiku-5-5",
            "input_tokens": 12,
            "output_tokens": 30,
            **platform,
        }
        await _ingest(
            engine,
            [
                *_skill_call(session_id, call_ts, _BACKGROUND_RESULT, platform=platform),
                _fork(session_id, fork_ts, _BACKGROUND_FORK_ID, _SKILL_PROMPT_ID, platform=platform),
                usage,
                _stop(session_id, fork_ts + timedelta(seconds=30), _BACKGROUND_FORK_ID, platform=platform),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(1)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [("skill · probe-fork", "skill", 30_000, 0.25)]
        assert await _skills_invoked(engine) == [{"name": _FORK_SKILL, "count": 1}]
        assert await _session_dims(engine, session_id, "skills") == {_FORK_SKILL: 1}
        async with engine.acquire() as conn:
            scopes = await conn.fetch(
                "SELECT scope_kind, scope_name FROM codemie_analytics.session_usage "
                "WHERE session_id = $1 AND agent_id = $2",
                session_id,
                _BACKGROUND_FORK_ID,
            )
        assert [tuple(r) for r in scopes] == [("skill", _FORK_SKILL)]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_skill_call_and_its_fork_count_once_on_a_fallback_pair(engine):
    """With no span that day the skill-tagged PreToolUse is the counted initiator; its fork is not added. The
    initiator has no timeline row, so the fork's own row stays: one row, with the fork's run and cost."""
    session_id = _session("fallback")
    call_ts = _DAY + timedelta(minutes=5)
    fork_ts = call_ts + timedelta(milliseconds=75)
    try:
        await _seed_request(engine, session_id)
        await _seed_fork_request(engine, session_id, _BACKGROUND_FORK_ID, call_ts + timedelta(seconds=2), 0.25)
        await _ingest(
            engine,
            [
                *_skill_call(session_id, call_ts, _BACKGROUND_RESULT),
                _fork(session_id, fork_ts, _BACKGROUND_FORK_ID, _SKILL_PROMPT_ID),
                _stop(session_id, fork_ts + timedelta(seconds=30), _BACKGROUND_FORK_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(1)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", 30_000, 0.25)
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_foreground_fork_of_an_uncounted_skill_call_counts_once_as_an_agent(engine):
    """A fork without skill_name named by a Skill call that is not counted (no span, no skill_name on its start) is
    neither a skill nor dropped: it counts once, as the agent it is on its own."""
    session_id = _session("uncounted-foreground")
    call_ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_request(engine, session_id)
        await _ingest(
            engine,
            [
                *_skill_call(session_id, call_ts, _FOREGROUND_RESULT, tagged=False),
                _fork(session_id, call_ts + timedelta(seconds=1), _FOREGROUND_FORK_ID, _SKILL_PROMPT_ID, None),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(0, agents=1, skill_calls=0, agent_rows=1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_another_clients_skill_tagged_start_counts_as_an_agent(engine):
    """A skill_name on agent.subagent.start marks a skill only from Claude Code; another client's tagged start
    counts as the agent it is."""
    session_id = _session("copilot-tagged")
    try:
        await _seed_request(engine, session_id)
        await _ingest(
            engine,
            [
                _fork(
                    session_id,
                    _DAY + timedelta(minutes=5),
                    _BACKGROUND_FORK_ID,
                    "",
                    platform={"platform": "copilot-cli"},
                )
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(0, agents=1, skill_calls=0, agent_rows=1)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == []
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_fork_whose_skill_call_is_not_counted_counts_on_its_own(engine):
    """A PreToolUse without skill_name is no counted initiator: on a fallback pair the fork is the only skill
    event, and it counts once; it is still no agent."""
    session_id = _session("uncounted-call")
    call_ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_request(engine, session_id)
        await _ingest(
            engine,
            [
                *_skill_call(session_id, call_ts, _BACKGROUND_RESULT, tagged=False),
                _fork(session_id, call_ts + timedelta(milliseconds=75), _BACKGROUND_FORK_ID, _SKILL_PROMPT_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(1)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", None, None)
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_forks_without_an_agent_id_take_no_run_or_cost_from_each_other(engine):
    """Two skill-tagged starts and two stops with an empty agent_id, and a request grouped under an empty
    agent_id: no stop or cost belongs to either fork, so both rows have no run and no cost. Written past
    ingest, which stores an empty agent_id as NULL."""
    session_id = _session("empty-agent-id")
    start = _DAY + timedelta(minutes=5)
    try:
        await _seed_request(engine, session_id)
        async with engine.acquire() as conn:
            for offset in (0, 1):
                await conn.execute(
                    "INSERT INTO hook_events (ts, session_id, event_type, skill_name, agent_type, agent_id) "
                    "VALUES ($1, $2, 'agent.subagent.start', $3, 'general-purpose', '')",
                    start + timedelta(minutes=offset),
                    session_id,
                    _FORK_SKILL,
                )
                await conn.execute(
                    "INSERT INTO hook_events (ts, session_id, event_type, agent_type, agent_id) "
                    "VALUES ($1, $2, 'agent.subagent.stop', 'general-purpose', '')",
                    start + timedelta(minutes=offset, seconds=30),
                    session_id,
                )
        await _seed_fork_request(engine, session_id, "", start + timedelta(seconds=10), 0.5)
        await _refresh_rollups(engine)

        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", None, None),
            ("command · probe-fork", "command", None, None),
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "agents"), [(_CLAUDE_CODE, 0), ({"platform": "copilot-cli"}, 1)], ids=["claude-code", "copilot-cli"]
)
async def test_live_a_slash_command_and_its_fork_count_once(engine, platform, agents):
    """/probe-fork: the dispatch and the fork's SubagentStart share the turn's prompt_id, so they are one
    invocation: one `command` row with the fork's run and cost. Another client's events are never paired and its
    skill-tagged start is no skill: the dispatch counts as the skill, the start as an agent."""
    session_id = _session(f"slash-{platform['platform']}")
    ts = _DAY + timedelta(minutes=5)
    fork_ts = ts + timedelta(milliseconds=113)
    try:
        await _seed_request(engine, session_id)
        await _seed_fork_request(engine, session_id, _SLASH_FORK_ID, ts + timedelta(seconds=2), 0.5)
        await _ingest(
            engine,
            [
                _dispatch(session_id, ts, _SLASH_PROMPT_ID, platform=platform),
                _fork(session_id, fork_ts, _SLASH_FORK_ID, _SLASH_PROMPT_ID, platform=platform),
                _stop(session_id, fork_ts + timedelta(seconds=40), _SLASH_FORK_ID, platform=platform),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(1, agents=agents, agent_rows=agents)
        fork_row = ("command · probe-fork", "command", 40_000, 0.5)
        unpaired_dispatch_row = ("command · probe-fork", "command", 0, None)
        expected_rows = [fork_row] if agents == 0 else [unpaired_dispatch_row]
        assert _dispatch_rows(await _api_detail(engine, session_id)) == expected_rows
        assert await _skills_invoked(engine) == [{"name": _FORK_SKILL, "count": 1}]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dispatched", "skills"), [("probeplug:pfork", 1), ("pfork", 2)], ids=["same-name", "bare-name"]
)
async def test_live_a_plugin_slash_fork_pairs_only_by_the_same_skill_name(engine, dispatched, skills):
    """A plugin skill is `probeplug:pfork` in every event; a dispatch of another name is no initiator of it."""
    session_id = _session(f"plugin-{dispatched.replace(':', '-')}")
    prompt_id = "cd543f71-9e74-421b-a7ae-0f9ce103b17d"
    ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_request(engine, session_id)
        await _ingest(
            engine,
            [
                _dispatch(session_id, ts, prompt_id, skill_name=dispatched),
                _fork(session_id, ts + timedelta(milliseconds=114), "a93711423bf852988", prompt_id,
                      skill_name="probeplug:pfork"),
            ],
        )  # fmt: skip
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id, "probeplug:pfork") == _expected(skills, skill_calls=1)
        fork_row = ("command · probeplug:pfork", "command", None, None)
        expected_rows = [fork_row] if skills == 1 else [("command · pfork", "command", 0, None), fork_row]
        assert _dispatch_rows(await _api_detail(engine, session_id)) == expected_rows
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_two_slash_commands_of_one_skill_count_twice(engine):
    """Two /probe-fork, 45.9 s apart, each with its own prompt_id and fork: two `command` rows, each with its own
    fork's run and cost."""
    session_id = _session("two-slash")
    first, second = _DAY + timedelta(minutes=5), _DAY + timedelta(minutes=5, seconds=45.9)
    first_fork, second_fork = first + timedelta(milliseconds=113), second + timedelta(milliseconds=105)
    try:
        await _seed_request(engine, session_id)
        await _seed_fork_request(engine, session_id, _SLASH_FORK_ID, first + timedelta(seconds=2), 0.25)
        await _seed_fork_request(engine, session_id, "a35680c9da5ddd926", second + timedelta(seconds=2), 0.5)
        await _ingest(
            engine,
            [
                _dispatch(session_id, first, _SLASH_PROMPT_ID),
                _fork(session_id, first_fork, _SLASH_FORK_ID, _SLASH_PROMPT_ID),
                _stop(session_id, first_fork + timedelta(seconds=30), _SLASH_FORK_ID),
                _dispatch(session_id, second, "071562db-d2c9-42ff-b197-6708c7b5429b"),
                _fork(session_id, second_fork, "a35680c9da5ddd926", "071562db-d2c9-42ff-b197-6708c7b5429b"),
                _stop(session_id, second_fork + timedelta(seconds=40), "a35680c9da5ddd926"),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(2)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", 30_000, 0.25),
            ("command · probe-fork", "command", 40_000, 0.5),
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_slash_command_takes_only_the_fork_of_its_own_turn(engine):
    """The first /probe-fork ran its fork in the foreground, so that SubagentStart has no skill name; the next
    /probe-fork's fork is the earliest named one after both dispatches, but only the second shares its
    prompt_id: the first takes only its own foreground fork, which has no run or cost yet."""
    session_id = _session("own-turn")
    first, second = _DAY + timedelta(minutes=5), _DAY + timedelta(minutes=5, seconds=45.9)
    second_prompt_id, second_fork = "071562db-d2c9-42ff-b197-6708c7b5429b", second + timedelta(milliseconds=105)
    try:
        await _seed_request(engine, session_id)
        await _seed_fork_request(engine, session_id, "a35680c9da5ddd926", second + timedelta(seconds=2), 0.5)
        await _ingest(
            engine,
            [
                _dispatch(session_id, first, _SLASH_PROMPT_ID),
                _fork(session_id, first + timedelta(milliseconds=113), _SLASH_FORK_ID, _SLASH_PROMPT_ID,
                      skill_name=None),
                _dispatch(session_id, second, second_prompt_id),
                _fork(session_id, second_fork, "a35680c9da5ddd926", second_prompt_id),
                _stop(session_id, second_fork + timedelta(seconds=40), "a35680c9da5ddd926"),
            ],
        )  # fmt: skip
        await _refresh_rollups(engine)

        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", None, None),
            ("command · probe-fork", "command", 40_000, 0.5),
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_two_parallel_skill_calls_count_twice_and_neither_fork_is_an_agent(engine, spans):
    """Two Skill calls in one message share the prompt_id and are told apart by their agentId. The foreground
    fork has no marker, so its SubagentStart has no skill_name: it is still the fork of a Skill call, not an
    agent. With spans each call is one `skill` row with its own fork's run and cost, the foreground fork's
    too; with no span that day only the background fork has a row of its own, and the foreground one none."""
    session_id = _session(f"parallel-{spans}")
    first, second = _DAY + timedelta(minutes=5), _DAY + timedelta(minutes=5, milliseconds=150)
    first_fork, second_fork = first + timedelta(milliseconds=80), second + timedelta(milliseconds=64)
    first_call, second_call = "toolu_01XE3dKFBK3hhcUGyGMczjrA", "toolu_01ADFsQqGP6jAWqgfxgX1WTw"
    background_result = _BACKGROUND_RESULT.replace(_BACKGROUND_FORK_ID, "a74f4c702a426345f")
    try:
        if spans:
            await _seed_turn(engine, session_id, [])
            await _seed_skill_span(engine, session_id, first, first_call)
            await _seed_skill_span(engine, session_id, second, second_call)
        else:
            await _seed_request(engine, session_id)
        await _seed_fork_request(engine, session_id, "a74f4c702a426345f", first + timedelta(seconds=2), 0.25)
        await _seed_fork_request(engine, session_id, _FOREGROUND_FORK_ID, first + timedelta(seconds=3), 0.5)
        await _ingest(
            engine,
            [
                *_skill_call(session_id, first, background_result, tool_use_id=first_call),
                _fork(session_id, first_fork, "a74f4c702a426345f", _SKILL_PROMPT_ID),
                *_skill_call(session_id, second, _FOREGROUND_RESULT, tool_use_id=second_call),
                _fork(session_id, second_fork, _FOREGROUND_FORK_ID, _SKILL_PROMPT_ID, skill_name=None),
                _stop(session_id, first_fork + timedelta(seconds=30), "a74f4c702a426345f"),
                _stop(session_id, second_fork + timedelta(seconds=20), _FOREGROUND_FORK_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(2)
        expected_rows = (
            [("skill · probe-fork", "skill", 30_000, 0.25), ("skill · probe-fork", "skill", 20_000, 0.5)]
            if spans
            else [("command · probe-fork", "command", 30_000, 0.25)]
        )
        assert _dispatch_rows(await _api_detail(engine, session_id)) == expected_rows
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_an_inline_slash_skill_and_an_ordinary_agent_of_one_turn_stay_apart(engine, spans):
    """An inline /probe-skill has no fork; an ordinary subagent of the same turn (no skill_name, named by its
    Agent call's result) is an agent, not the skill's fork: the `command` row keeps no cost or run of its own.
    With spans the Agent call is its own `agent` row with the subagent's cost, its run from SubagentStart to
    SubagentStop."""
    session_id = _session(f"inline-{spans}")
    prompt_id = "4c0974fa-b400-420d-9aa4-14a431cdd059"
    agent_id = "a9d8798d295da2cf0"
    ts, call_ts = _DAY + timedelta(minutes=5), _DAY + timedelta(minutes=6)
    fork_ts = call_ts + timedelta(milliseconds=300)
    agent_call = {"tool_name": "Agent", "tool_use_id": "toolu_01AgentOfTheTurn", "prompt_id": prompt_id}
    try:
        events = [
            _dispatch(session_id, ts, prompt_id, skill_name="probe-skill"),
            _fork(session_id, fork_ts, agent_id, prompt_id, skill_name=None),
            _stop(session_id, fork_ts + timedelta(seconds=20), agent_id),
            _hook("agent.tool.start", "PreToolUse", session_id, call_ts, _CLAUDE_CODE, **agent_call),
            _hook("agent.tool.end", "PostToolUse", session_id, fork_ts, _CLAUDE_CODE, **agent_call,
                  tool_response=_AGENT_RESULT[:300]),
        ]  # fmt: skip
        if spans:
            await _seed_turn(engine, session_id, [(6, "toolu_01AgentOfTheTurn", "general-purpose")])
        else:
            await _seed_request(engine, session_id)
        await _seed_fork_request(engine, session_id, agent_id, call_ts + timedelta(seconds=2), 0.75)
        await _ingest(engine, events)
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id, "probe-skill") == _expected(1, agents=1, agent_rows=1)
        command_row = ("command · probe-skill", "command", 0, None)
        agent_rows = [("agent · general-purpose", "agent", 20_000, 0.75)] if spans else []
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [command_row, *agent_rows]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_slash_command_just_before_midnight_and_its_fork_after_it_count_once(engine):
    """The dispatch at 23:59:59.900 and its fork at 00:00:00.100 the next day, ingested fork first and refreshed
    before the dispatch arrives: the dispatch marks the fork's day too, so the fork is recounted against it. The
    backfill marks the next day of such a dispatch the same way."""
    session_id = _session("midnight")
    day = _DAY.date()
    dispatch_ts = datetime.combine(day, time(23, 59, 59, 900_000), tzinfo=timezone.utc)
    fork_ts = dispatch_ts + timedelta(milliseconds=200)
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: day)
    try:
        await _seed_request(engine, session_id)
        await _ingest(engine, [_fork(session_id, fork_ts, _SLASH_FORK_ID, _SLASH_PROMPT_ID)])
        async with engine.acquire() as conn:
            await refresher.mark_dirty(day, day, conn, mask=1)  # LOG_FACTS only: the seeded request's cost
            await refresher.refresh(conn)
        await _ingest(engine, [_dispatch(session_id, dispatch_ts, _SLASH_PROMPT_ID)])
        async with engine.acquire() as conn:
            await refresher.refresh(conn)

        assert await _counts(engine, session_id) == _expected(1)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", None, None)
        ]

        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
            await refresher.mark_dirty(day, day, conn)
            next_day_kinds = await conn.fetchval(
                "SELECT kinds FROM codemie_analytics.rollup_dirty WHERE session_id = $1 AND day = $2",
                session_id,
                day + timedelta(days=1),
            )
        assert next_day_kinds == 4  # SPAN_FACTS only
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_an_agent_call_before_midnight_that_ends_the_next_day_counts_on_its_day(engine):
    """An Agent call at 23:30 refreshed before its agent.tool.end arrives at 01:30 the next day: the end re-queues
    the call's day, which then counts the call. The backfill of the end's day queues the call's day the same way."""
    session_id = _session("agent-next-day")
    day = _DAY.date()
    call_ts = datetime.combine(day, time(23, 30), tzinfo=timezone.utc)
    end_ts = call_ts + timedelta(hours=2)
    tool_use_id = "toolu_01AgentAcrossMidnight"
    common = {"tool_name": "Agent", "tool_use_id": tool_use_id, "tool_input": json.dumps({"subagent_type": "Explore"})}
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: day)
    try:
        await _seed_turn(engine, session_id, [(13 * 60 + 30, tool_use_id, "Explore")])
        await _ingest(engine, [_hook("agent.tool.start", "PreToolUse", session_id, call_ts, _CLAUDE_CODE, **common)])
        async with engine.acquire() as conn:
            await refresher.mark_dirty(day, day, conn, mask=1)  # LOG_FACTS only: the seeded requests' cost
            await refresher.refresh(conn)
        detail = await _api_detail(engine, session_id)
        assert detail["agent_count"] == 0  # no evidence yet: denied
        assert _dispatch_rows(detail) == []

        await _ingest(engine, [_hook("agent.tool.end", "PostToolUse", session_id, end_ts, _CLAUDE_CODE, **common)])
        async with engine.acquire() as conn:
            await refresher.refresh(conn)

        rollup_count, _ = await _rollup_agent_counts(engine, session_id, "Explore")
        detail = await _api_detail(engine, session_id)
        assert rollup_count == detail["agent_count"] == 1
        assert _dispatch_rows(detail) == [("agent · Explore", "agent", None, None)]

        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
            await refresher.mark_dirty(day + timedelta(days=1), day + timedelta(days=1), conn)
            call_day_kinds = await conn.fetchval(
                "SELECT kinds FROM codemie_analytics.rollup_dirty WHERE session_id = $1 AND day = $2",
                session_id,
                day,
            )
        assert call_day_kinds == 4  # SPAN_FACTS only
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_fork_before_midnight_whose_skill_call_ends_the_next_day_counts_once(engine):
    """A Skill call at 23:30 and its fork at 23:30:01, refreshed before the call's agent.tool.end naming the fork
    arrives at 01:30 the next day: the end re-queues the fork's day, where the fork is then deduped against it."""
    session_id = _session("fork-next-day")
    call_ts = datetime.combine(_DAY.date(), time(23, 30), tzinfo=timezone.utc)
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: _DAY.date())
    try:
        await _seed_turn(engine, session_id, [])
        await _seed_skill_span(engine, session_id, call_ts, _SKILL_TOOL_USE_ID)
        start, end = _skill_call(session_id, call_ts, _FOREGROUND_RESULT)
        fork = _fork(session_id, call_ts + timedelta(seconds=1), _FOREGROUND_FORK_ID, _SKILL_PROMPT_ID)
        await _ingest(engine, [start, fork])
        async with engine.acquire() as conn:
            await refresher.mark_dirty(_DAY.date(), _DAY.date(), conn, mask=1)  # LOG_FACTS only: the seeded cost
            await refresher.refresh(conn)

        await _ingest(engine, [{**end, "timestamp": _iso(call_ts + timedelta(hours=2))}])
        async with engine.acquire() as conn:
            await refresher.refresh(conn)

        assert await _counts(engine, session_id) == _expected(1)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [("skill · probe-fork", "skill", None, None)]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_fork_whose_skill_call_ends_two_days_later_counts_once(engine):
    """A foreground fork whose Skill call's agent.tool.end comes two days after it, past every neighbouring-day
    mark: the end re-queues the fork's own day by its agent_id, where the fork is then deduped against it."""
    session_id = _session("fork-two-days")
    call_ts = datetime.combine(_DAY.date(), time(12, 0), tzinfo=timezone.utc)
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: _DAY.date())
    try:
        await _seed_turn(engine, session_id, [])
        await _seed_skill_span(engine, session_id, call_ts, _SKILL_TOOL_USE_ID)
        start, end = _skill_call(session_id, call_ts, _FOREGROUND_RESULT)
        fork = _fork(session_id, call_ts + timedelta(seconds=1), _FOREGROUND_FORK_ID, _SKILL_PROMPT_ID)
        await _ingest(engine, [start, fork])
        async with engine.acquire() as conn:
            await refresher.mark_dirty(_DAY.date(), _DAY.date(), conn, mask=1)  # LOG_FACTS only: the seeded cost
            await refresher.refresh(conn)

        await _ingest(engine, [{**end, "timestamp": _iso(call_ts + timedelta(days=2))}])
        async with engine.acquire() as conn:
            await refresher.refresh(conn)

        assert await _counts(engine, session_id) == _expected(1)

        # the backfill of the end's day queues the fork's day the same way
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
            await refresher.mark_dirty(_DAY.date() + timedelta(days=2), _DAY.date() + timedelta(days=2), conn)
            fork_day_kinds = await conn.fetchval(
                "SELECT kinds FROM codemie_analytics.rollup_dirty WHERE session_id = $1 AND day = $2",
                session_id,
                _DAY.date(),
            )
        assert fork_day_kinds == 4  # SPAN_FACTS only
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_fork_named_by_a_cut_agent_id_counts_on_its_own(engine):
    """A tool_response cut by the CLI inside the agentId names no fork: the fork counts beside its initiator."""
    session_id = _session("cut-id")
    call_ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_request(engine, session_id)
        await _ingest(
            engine,
            [
                *_skill_call(session_id, call_ts, _CUT_RESULT),
                _fork(session_id, call_ts + timedelta(milliseconds=75), _BACKGROUND_FORK_ID, _SKILL_PROMPT_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(2)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", None, None)
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_fork_named_by_the_fork_agent_id_of_a_cut_skill_end_counts_once(engine):
    """A Skill end whose tool_response is cut inside the agentId but whose fork_agent_id names the fork: the fork
    joins its counted initiator and is not added. The typed agent_id (the caller's id inside a subagent) is no key."""
    session_id = _session("typed-id")
    call_ts = _DAY + timedelta(minutes=5)
    start, end = _skill_call(session_id, call_ts, _CUT_RESULT)
    try:
        await _seed_request(engine, session_id)
        await _ingest(
            engine,
            [
                start,
                {**end, "fork_agent_id": _BACKGROUND_FORK_ID, "agent_id": "a-caller-subagent"},
                _fork(session_id, call_ts + timedelta(milliseconds=75), _BACKGROUND_FORK_ID, _SKILL_PROMPT_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(1)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", None, None)
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_a_slash_command_and_its_foreground_fork_without_a_skill_name_count_once(engine, spans):
    """/probe-fork whose fork ran in the foreground: its SubagentStart has no skill_name, but it shares the
    dispatch's prompt_id and no Skill or Agent call names it, so it is the dispatch's fork, a skill and no agent;
    the `command` row takes its run and cost."""
    session_id = _session(f"slash-foreground-{spans}")
    ts = _DAY + timedelta(minutes=5)
    fork_ts = ts + timedelta(milliseconds=113)
    try:
        if spans:
            await _seed_turn(engine, session_id, [])
        else:
            await _seed_request(engine, session_id)
        await _seed_fork_request(engine, session_id, _SLASH_FORK_ID, ts + timedelta(seconds=2), 0.5)
        await _ingest(
            engine,
            [
                _dispatch(session_id, ts, _SLASH_PROMPT_ID),
                _fork(session_id, fork_ts, _SLASH_FORK_ID, _SLASH_PROMPT_ID, skill_name=None),
                _stop(session_id, fork_ts + timedelta(seconds=40), _SLASH_FORK_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(1)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", 40_000, 0.5)
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_an_agent_named_by_its_agent_call_is_never_the_fork_of_a_slash_command(engine):
    """The same /probe-fork turn also starts an ordinary Explore subagent before the fork; its Agent call's
    result names it, so it never pairs with the dispatch: it stays an agent, and the `command` row keeps the
    fork's run and cost, not the agent's."""
    session_id = _session("slash-foreground-agent")
    ts = _DAY + timedelta(minutes=5)
    agent_ts, fork_ts = ts + timedelta(milliseconds=50), ts + timedelta(milliseconds=113)
    agent_id = "a9d8798d295da2cf0"
    agent_call = {"tool_name": "Agent", "tool_use_id": "toolu_01AgentOfTheTurn", "prompt_id": _SLASH_PROMPT_ID}
    try:
        await _seed_request(engine, session_id)
        await _seed_fork_request(engine, session_id, agent_id, ts + timedelta(seconds=1), 0.75)
        await _seed_fork_request(engine, session_id, _SLASH_FORK_ID, ts + timedelta(seconds=2), 0.5)
        await _ingest(
            engine,
            [
                _dispatch(session_id, ts, _SLASH_PROMPT_ID),
                _hook("agent.tool.start", "PreToolUse", session_id, ts + timedelta(milliseconds=40), _CLAUDE_CODE,
                      **agent_call),
                {**_fork(session_id, agent_ts, agent_id, _SLASH_PROMPT_ID, skill_name=None), "agent_type": "Explore"},
                _fork(session_id, fork_ts, _SLASH_FORK_ID, _SLASH_PROMPT_ID, skill_name=None),
                _hook("agent.tool.end", "PostToolUse", session_id, ts + timedelta(seconds=3), _CLAUDE_CODE,
                      **agent_call, tool_response=_AGENT_RESULT[:300]),
                _stop(session_id, fork_ts + timedelta(seconds=40), _SLASH_FORK_ID),
                _stop(session_id, agent_ts + timedelta(seconds=20), agent_id),
            ],
        )  # fmt: skip
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(1, agents=1, agent_rows=1)
        assert await _rollup_agent_counts(engine, session_id, "Explore") == (1, 1)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", 40_000, 0.5)
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_fork_its_skill_call_names_is_never_the_fork_of_a_slash_command(engine):
    """In one prompt, /probe-fork and a model Skill call of the same skill (#642 alone: no span, no skill_name on
    its PreToolUse) whose result names a skill-tagged fork. That fork is the Skill call's, never the dispatch's;
    the Skill call is not counted, so the fork counts on its own: two invocations, in the counts and on the
    timeline alike."""
    session_id = _session("slash-and-skill-call")
    ts = _DAY + timedelta(minutes=5)
    call_ts = ts + timedelta(seconds=1)
    fork_ts = call_ts + timedelta(milliseconds=75)
    try:
        await _seed_request(engine, session_id)
        await _seed_fork_request(engine, session_id, _BACKGROUND_FORK_ID, call_ts + timedelta(seconds=2), 0.25)
        await _ingest(
            engine,
            [
                _dispatch(session_id, ts, _SKILL_PROMPT_ID),
                *_skill_call(session_id, call_ts, _BACKGROUND_RESULT, tagged=False),
                _fork(session_id, fork_ts, _BACKGROUND_FORK_ID, _SKILL_PROMPT_ID),
                _stop(session_id, fork_ts + timedelta(seconds=30), _BACKGROUND_FORK_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(2)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", 0, None),
            ("command · probe-fork", "command", 30_000, 0.25),
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_two_no_agent_id_dispatches_starting_together_split_their_window_equally(engine):
    """Two Agent calls under one parent with the same start and no agentId: each takes half of the window from
    their start on, never one an empty window and the other the whole sum."""
    session_id = _session("tied-siblings")
    try:
        await _seed_turn(engine, session_id, [(5, "explore-ok", "Explore")], costs=((20, 1.0),))
        async with engine.acquire() as conn:
            await conn.execute(
                _SPANS_SQL,
                _DAY + timedelta(minutes=5),
                1,
                b"t" * 16,
                _id(f"tool-{session_id}-5-review"),
                _id(f"int-{session_id}"),
                _TOOL,
                "claude_code.tool",
                session_id,
                "Agent",
                "review-ok",
                "Review",
            )
            await conn.execute(_AGENT_TOOL_END_SQL, _DAY + timedelta(minutes=5), session_id, "review-ok")
        await _refresh_rollups(engine)

        costs = {r["subagent_type"]: r["cost_usd"] for r in await _sql_agents(engine, session_id)}
        assert costs == {"Explore": pytest.approx(0.5), "Review": pytest.approx(0.5)}
        # Each takes half of the window's catalog price, 1.0 USD.
        assert _agent_costs(await _api_detail(engine, session_id)) == {
            "agent · Explore": pytest.approx(0.5),
            "agent · Review": pytest.approx(0.5),
        }
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_no_agent_id_dispatch_tied_with_a_known_agent_takes_its_whole_window(engine):
    """A no-agentId Agent call that starts with a sibling whose agentId is known: the sibling takes its own
    requests, not a share, so the no-agentId call takes its whole window, never half of it."""
    session_id = _session("tied-known")
    try:
        await _seed_turn(engine, session_id, [(5, "explore-ok", "Explore")], costs=((20, 1.0),))
        async with engine.acquire() as conn:
            await conn.execute(
                _SPANS_SQL,
                _DAY + timedelta(minutes=5),
                1,
                b"t" * 16,
                _id(f"tool-{session_id}-5-review"),
                _id(f"int-{session_id}"),
                _TOOL,
                "claude_code.tool",
                session_id,
                "Agent",
                "review-ok",
                "Review",
            )
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, tool_name, tool_use_id, attrs) "
                "VALUES ($1, $2, 'agent.tool.end', 'Agent', $3, $4::jsonb)",
                _DAY + timedelta(minutes=5),
                session_id,
                "review-ok",
                json.dumps({"tool_response": '{"status":"async_launched","agentId":"a5ed1ce0known1"}'}),
            )
        await _refresh_rollups(engine)

        costs = {r["subagent_type"]: r["cost_usd"] for r in await _sql_agents(engine, session_id)}
        assert costs == {"Explore": pytest.approx(1.0), "Review": None}
        assert _agent_costs(await _api_detail(engine, session_id)) == {
            "agent · Explore": pytest.approx(1.0),
            "agent · Review": None,
        }
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_an_agent_continued_by_send_message_runs_for_its_runs_not_the_gap(engine):
    """SendMessage resumes an agent with the same agent_id: a second SubagentStart / SubagentStop pair, no second
    Agent call. Its one row runs for the sum of its runs (2.4 s + 2.2 s), not from its first start to its last stop."""
    session_id = _session("resumed-agent")
    agent_id = "a9fbe350c5f076c31"
    start = _DAY + timedelta(minutes=5, seconds=1)
    try:
        await _seed_turn(engine, session_id, [(5, "explore-ok", "Explore")])
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, tool_name, tool_use_id, attrs) "
                "VALUES ($1, $2, 'agent.tool.end', 'Agent', $3, $4::jsonb)",
                start,
                session_id,
                "explore-ok",
                json.dumps({"tool_response": f'{{"status":"async_launched","agentId":"{agent_id}"}}'}),
            )
        await _ingest(
            engine,
            [
                _fork(session_id, start, agent_id, "prompt-1", None),
                _stop(session_id, start + timedelta(milliseconds=2_400), agent_id),
                _fork(session_id, start + timedelta(milliseconds=7_000), agent_id, "prompt-2", None),
                _stop(session_id, start + timedelta(milliseconds=9_200), agent_id),
            ],
        )
        await _refresh_rollups(engine)

        rows = [row for row in _dispatch_rows(await _api_detail(engine, session_id)) if row[1] == "agent"]
        assert [(label, duration) for label, _, duration, _ in rows] == [("agent · Explore", 4_600)]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("starts", "duration"),
    [
        # the agent is running again: its finished run only, the open gap is no idle time
        ((0, 7_000), 2_400),
        # one stop, a start sent twice before the next stop: the gap counts once
        ((0, 7_000, 7_100), 4_600),
    ],
    ids=["still-running", "repeated-start"],
)
async def test_live_an_agent_continued_by_send_message_subtracts_only_closed_gaps_once(engine, starts, duration):
    """Idle time is a stop-to-start gap that a later stop of the same agent_id closes, each stop's gap once."""
    session_id = _session(f"resumed-agent-{len(starts)}")
    agent_id = "a9fbe350c5f076c31"
    start = _DAY + timedelta(minutes=5, seconds=1)
    events = [_fork(session_id, start + timedelta(milliseconds=ms), agent_id, f"prompt-{ms}", None) for ms in starts]
    events.insert(1, _stop(session_id, start + timedelta(milliseconds=2_400), agent_id))
    if len(starts) == 3:
        events.append(_stop(session_id, start + timedelta(milliseconds=9_200), agent_id))
    try:
        await _seed_turn(engine, session_id, [(5, "explore-ok", "Explore")])
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, tool_name, tool_use_id, attrs) "
                "VALUES ($1, $2, 'agent.tool.end', 'Agent', $3, $4::jsonb)",
                start,
                session_id,
                "explore-ok",
                json.dumps({"tool_response": f'{{"status":"async_launched","agentId":"{agent_id}"}}'}),
            )
        await _ingest(engine, events)
        await _refresh_rollups(engine)

        rows = [row for row in _dispatch_rows(await _api_detail(engine, session_id)) if row[1] == "agent"]
        assert [(label, run) for label, _, run, _ in rows] == [("agent · Explore", duration)]
    finally:
        await _cleanup(engine, session_id)


# The prompt_id of the turn whose SendMessage continues a subagent (live capture, Claude Code 2.1.302).
_RESUME_PROMPT_ID = "a39fd8bd-cfdf-4244-88f9-bdd4a0439eea"


async def _seed_day(engine: AnalyticsPgEngine, session_id: str, spans: bool) -> None:
    if spans:
        await _seed_turn(engine, session_id, [])
    else:
        await _seed_request(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_a_slash_fork_continued_by_send_message_counts_once(engine, spans):
    """SendMessage continues the fork of /probe-fork under its agent_id: a second SubagentStart under the new turn's
    prompt_id, tagged with skill_name again, and a second SubagentStop. Still one skill and no agent; the `command`
    row runs for its two runs."""
    session_id = _session(f"slash-resumed-{spans}")
    ts = _DAY + timedelta(minutes=5)
    fork_ts = ts + timedelta(milliseconds=88)
    try:
        await _seed_day(engine, session_id, spans)
        await _ingest(
            engine,
            [
                _dispatch(session_id, ts, _SLASH_PROMPT_ID),
                _fork(session_id, fork_ts, _SLASH_FORK_ID, _SLASH_PROMPT_ID),
                _stop(session_id, fork_ts + timedelta(milliseconds=2_189), _SLASH_FORK_ID),
                _fork(session_id, fork_ts + timedelta(seconds=139), _SLASH_FORK_ID, _RESUME_PROMPT_ID),
                _stop(session_id, fork_ts + timedelta(seconds=141), _SLASH_FORK_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(1)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", 4_189, None)
        ]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_an_agent_continued_by_send_message_counts_once(engine, spans):
    """An ordinary subagent continued by SendMessage starts again under its agent_id, without skill_name: one agent
    of its subtype, before the rollup catches up and after."""
    session_id = _session(f"agent-resumed-{spans}")
    agent_id = "a9d8798d295da2cf0"
    call = {"tool_name": "Agent", "tool_use_id": "toolu_01AgentResumed-ok", "prompt_id": "prompt-agent"}
    ts = _DAY + timedelta(minutes=5)
    try:
        if spans:
            await _seed_turn(engine, session_id, [(5, call["tool_use_id"], "general-purpose")])
        else:
            await _seed_request(engine, session_id)
            await _refresh_rollups(engine)
        await _ingest(
            engine,
            [
                _hook("agent.tool.start", "PreToolUse", session_id, ts, _CLAUDE_CODE, **call),
                _fork(session_id, ts + timedelta(milliseconds=300), agent_id, "prompt-agent", None),
                _hook("agent.tool.end", "PostToolUse", session_id, ts + timedelta(milliseconds=302), _CLAUDE_CODE,
                      **call, tool_response=_AGENT_RESULT[:300]),
                _stop(session_id, ts + timedelta(seconds=3), agent_id),
                _fork(session_id, ts + timedelta(seconds=60), agent_id, _RESUME_PROMPT_ID, None),
                _stop(session_id, ts + timedelta(seconds=62), agent_id),
            ],
        )  # fmt: skip
        if not spans:
            assert (await _api_detail(engine, session_id))["agent_count"] == 1
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(0, agents=1, skill_calls=0, agent_rows=1)
        assert await _rollup_agent_counts(engine, session_id, "general-purpose") == (1, 1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_a_skill_call_fork_continued_by_send_message_counts_once(engine, spans):
    """The fork a Skill call names, continued by SendMessage with a second skill-tagged SubagentStart: one skill,
    no agent."""
    session_id = _session(f"skill-resumed-{spans}")
    call_ts = _DAY + timedelta(minutes=5)
    fork_ts = call_ts + timedelta(milliseconds=95)
    try:
        await _seed_day(engine, session_id, spans)
        if spans:
            await _seed_skill_span(engine, session_id, call_ts, _SKILL_TOOL_USE_ID)
        await _ingest(
            engine,
            [
                *_skill_call(session_id, call_ts, _BACKGROUND_RESULT),
                _fork(session_id, fork_ts, _BACKGROUND_FORK_ID, _SKILL_PROMPT_ID),
                _stop(session_id, fork_ts + timedelta(milliseconds=1_668), _BACKGROUND_FORK_ID),
                _fork(session_id, fork_ts + timedelta(seconds=76), _BACKGROUND_FORK_ID, _RESUME_PROMPT_ID),
                _stop(session_id, fork_ts + timedelta(seconds=78), _BACKGROUND_FORK_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("fork_skill", ["named", "foreground"])
async def test_live_a_slash_command_whose_skill_name_is_too_long_to_count_pairs_with_no_fork(engine, fork_skill):
    """The rollups never count a dispatch whose skill name is over the key bound, so it starts no fork: its fork
    counts on its own, as when unpaired — a skill-tagged fork of that name is no countable skill either and keeps
    its own timeline row, a foreground fork is an agent — and the dispatch has no timeline row."""
    session_id = _session(f"long-dispatch-{fork_skill}")
    long_name = "x" * 257
    ts = _DAY + timedelta(minutes=5)
    fork_ts = ts + timedelta(milliseconds=113)
    try:
        await _seed_request(engine, session_id)
        await _ingest(
            engine,
            [
                _dispatch(session_id, ts, _SLASH_PROMPT_ID, skill_name=long_name),
                _fork(session_id, fork_ts, _SLASH_FORK_ID, _SLASH_PROMPT_ID,
                      skill_name=long_name if fork_skill == "named" else None),
            ],
        )  # fmt: skip
        await _refresh_rollups(engine)

        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id)
        if fork_skill == "named":
            assert await _counts(engine, session_id, long_name) == _expected(0)
            # the fork's own row, at the fork's start (the reader's timestamps are naive UTC)
            assert [(r["skill_name"], r["dispatch_start"]) for r in rows] == [(long_name, fork_ts.replace(tzinfo=None))]
        else:
            assert await _counts(engine, session_id, long_name) == _expected(0, agents=1, agent_rows=1)
            assert rows == []
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_the_session_detail_counts_hook_events_the_rollup_has_not_caught_up_with(engine):
    """Between ingest and the next refresh, the session detail adds a dispatch and a hook-only subagent start that
    no rollup row reflects yet; once the rollup catches up, it adds nothing and the counts stay the same."""
    session_id = _session("before-refresh")
    ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_request(engine, session_id)
        await _refresh_rollups(engine)
        before = await _api_detail(engine, session_id)
        assert (before["skill_count"], before["agent_count"]) == (0, 0)

        await _ingest(
            engine,
            [
                _dispatch(session_id, ts, "prompt-inline", "probe-skill"),
                _fork(session_id, ts + timedelta(seconds=1), "a0agent0before0r1", "prompt-agent", None),
            ],
        )
        pending = await _api_detail(engine, session_id)
        assert (pending["skill_count"], pending["agent_count"]) == (1, 1)

        await _refresh_rollups(engine)
        assert await _counts(engine, session_id, "probe-skill") == _expected(1, agents=1, agent_rows=1)
    finally:
        await _cleanup(engine, session_id)


def _execution_span(session_id: str, ts: datetime, tool_use_id: str) -> bytes:
    """An OTLP/JSON claude_code.tool.execution span of a call that ran."""
    ns = str((ts - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(microseconds=1) * 1000)
    attributes = [
        {"key": key, "value": {"stringValue": value}}
        for key, value in (("session.id", session_id), ("tool_use_id", tool_use_id), ("success", "true"))
    ]
    span = {
        "traceId": hashlib.sha1(session_id.encode()).hexdigest()[:32],
        "spanId": hashlib.sha1(tool_use_id.encode()).hexdigest()[:16],
        "name": "claude_code.tool.execution",
        "kind": 1,
        "startTimeUnixNano": ns,
        "endTimeUnixNano": ns,
        "attributes": attributes,
    }
    resource = {"attributes": [{"key": "service.name", "value": {"stringValue": "claude-code"}}]}
    scope = {"scope": {"name": "com.anthropic.claude_code.tracing"}, "spans": [span]}
    return json.dumps({"resourceSpans": [{"resource": resource, "scopeSpans": [scope]}]}).encode()


@pytest.mark.asyncio
async def test_live_an_agent_call_before_midnight_whose_execution_span_comes_the_next_day_counts_on_its_day(engine):
    """An Agent call at 23:30 whose only run evidence is its TOOL_EXECUTION span at 01:30 the next day, ingested
    after the call's day was refreshed: the span re-queues the call's day, which then counts the call. The
    backfill of the span's day queues the call's day the same way."""
    session_id = _session("exec-next-day")
    day = _DAY.date()
    tool_use_id = "toolu_01ExecutionAcrossMidnight"
    span_ts = datetime.combine(day, time(23, 30), tzinfo=timezone.utc) + timedelta(hours=2)
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: day)
    try:
        await _seed_turn(engine, session_id, [(13 * 60 + 30, tool_use_id, "Explore")])
        async with engine.acquire() as conn:
            await refresher.mark_dirty(day, day, conn)
            await refresher.refresh(conn)
        assert (await _api_detail(engine, session_id))["agent_count"] == 0  # no evidence yet: denied

        ingestor = PostgresTelemetryIngestor(engine, clock=lambda: day)
        await ingestor.ingest_otlp("traces", _execution_span(session_id, span_ts, tool_use_id), "application/json")
        async with engine.acquire() as conn:
            await refresher.refresh(conn)

        rollup_count, _ = await _rollup_agent_counts(engine, session_id, "Explore")
        assert rollup_count == (await _api_detail(engine, session_id))["agent_count"] == 1

        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
            await refresher.mark_dirty(day + timedelta(days=1), day + timedelta(days=1), conn)
            call_day_kinds = await conn.fetchval(
                "SELECT kinds FROM codemie_analytics.rollup_dirty WHERE session_id = $1 AND day = $2",
                session_id,
                day,
            )
        assert call_day_kinds == 4  # SPAN_FACTS only
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_fork_sent_twice_counts_once(engine):
    """A re-sent SubagentStart is stored once."""
    session_id = _session("resend")
    fork = _fork(session_id, _DAY + timedelta(minutes=5), _SLASH_FORK_ID, _SLASH_PROMPT_ID)
    try:
        await _seed_request(engine, session_id)
        await _ingest(engine, [fork])
        await _ingest(engine, [fork])
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_slash_command_and_a_fork_without_a_prompt_id_are_not_paired(engine):
    """An empty prompt_id is no key."""
    session_id = _session("no-prompt-id")
    ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_request(engine, session_id)
        await _ingest(
            engine,
            [
                _dispatch(session_id, ts, ""),
                _fork(session_id, ts + timedelta(milliseconds=113), _SLASH_FORK_ID, ""),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(2)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_skill_call_whose_fork_made_no_request_has_no_cost_not_the_parent_window(engine):
    """The Skill call's result names its fork, which made no API request: its row has no cost, never the slice
    of the turn's requests a skill without a fork falls back to."""
    session_id = _session("fork-no-request")
    call_ts = _DAY + timedelta(minutes=5)
    fork_ts = call_ts + timedelta(milliseconds=75)
    try:
        await _seed_turn(engine, session_id, [])
        await _seed_skill_span(engine, session_id, call_ts, _SKILL_TOOL_USE_ID)
        await _ingest(
            engine,
            [
                *_skill_call(session_id, call_ts, _BACKGROUND_RESULT),
                _fork(session_id, fork_ts, _BACKGROUND_FORK_ID, _SKILL_PROMPT_ID),
                _stop(session_id, fork_ts + timedelta(seconds=30), _BACKGROUND_FORK_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert _dispatch_rows(await _api_detail(engine, session_id)) == [("skill · probe-fork", "skill", 30_000, None)]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_a_slash_command_whose_fork_is_still_running_has_its_cost_so_far_and_no_run(engine):
    """A fork with no SubagentStop yet has no run: the handler shows no duration. Its requests so far are the
    row's cost."""
    session_id = _session("fork-running")
    ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_request(engine, session_id)
        await _seed_fork_request(engine, session_id, _SLASH_FORK_ID, ts + timedelta(seconds=2), 0.5)
        await _ingest(
            engine,
            [
                _dispatch(session_id, ts, _SLASH_PROMPT_ID),
                _fork(session_id, ts + timedelta(milliseconds=113), _SLASH_FORK_ID, _SLASH_PROMPT_ID),
            ],
        )
        await _refresh_rollups(engine)

        assert _dispatch_rows(await _api_detail(engine, session_id)) == [("command · probe-fork", "command", None, 0.5)]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_a_repeated_slash_command_takes_its_own_fork_not_the_continuation_of_the_first(engine, spans):
    """/probe-fork and its fork F1; SendMessage continues F1 in the next turn (a second start under that turn's
    prompt_id, still with skill_name), and in that turn the user runs /probe-fork again: its fork F2 starts after
    F1's continuation. Only an agent_id's first start is a fork: two skills, no agent, and the second dispatch
    takes F2's run and cost."""
    session_id = _session(f"slash-repeat-{spans}")
    first = _DAY + timedelta(minutes=5)
    fork_ts = first + timedelta(milliseconds=113)
    second_fork_id = "a35680c9da5ddd926"
    try:
        await _seed_day(engine, session_id, spans)
        await _seed_fork_request(engine, session_id, _SLASH_FORK_ID, fork_ts + timedelta(seconds=1), 0.25)
        await _seed_fork_request(engine, session_id, second_fork_id, fork_ts + timedelta(seconds=71), 0.5)
        await _ingest(
            engine,
            [
                _dispatch(session_id, first, _SLASH_PROMPT_ID),
                _fork(session_id, fork_ts, _SLASH_FORK_ID, _SLASH_PROMPT_ID),
                _stop(session_id, fork_ts + timedelta(seconds=2), _SLASH_FORK_ID),
                _dispatch(session_id, fork_ts + timedelta(seconds=50), _RESUME_PROMPT_ID),
                _fork(session_id, fork_ts + timedelta(seconds=60), _SLASH_FORK_ID, _RESUME_PROMPT_ID),
                _stop(session_id, fork_ts + timedelta(seconds=62), _SLASH_FORK_ID),
                _fork(session_id, fork_ts + timedelta(seconds=70), second_fork_id, _RESUME_PROMPT_ID),
                _stop(session_id, fork_ts + timedelta(seconds=100), second_fork_id),
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(2)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [
            ("command · probe-fork", "command", 4_000, 0.25),
            ("command · probe-fork", "command", 30_000, 0.5),
        ]
    finally:
        await _cleanup(engine, session_id)


_OTHER_SKILL = "probe-other"
_NO_FORK_ROW = ("command · probe-fork", "command", 0, None)
# case -> (events: ("d", second, skill) a dispatch, ("f", second, skill or None) a fork; skills, calls of
# probe-fork, agents on a fallback pair, timeline rows). The n-th fork runs 5 s or 7 s and costs 0.25 or 0.5 USD.
_ONE_PROMPT_CASES = {
    "d1x-d2x-f1x-f2x": (
        [("d", 0, _FORK_SKILL), ("d", 10, _FORK_SKILL), ("f", 20, _FORK_SKILL), ("f", 30, _FORK_SKILL)],
        4,
        4,
        0,
        [
            _NO_FORK_ROW,
            _NO_FORK_ROW,
            ("command · probe-fork", "command", 5_000, 0.25),
            ("command · probe-fork", "command", 7_000, 0.5),
        ],
    ),
    "d1x-fU-d2x-fNx": (
        [("d", 0, _FORK_SKILL), ("f", 10, None), ("d", 20, _FORK_SKILL), ("f", 30, _FORK_SKILL)],
        3,
        3,
        1,
        [_NO_FORK_ROW, _NO_FORK_ROW, ("command · probe-fork", "command", 7_000, 0.5)],
    ),
    "d1x-d2y-fU": (
        [("d", 0, _FORK_SKILL), ("d", 10, _OTHER_SKILL), ("f", 20, None)],
        2,
        1,
        1,
        [_NO_FORK_ROW, ("command · probe-other", "command", 0, None)],
    ),
    "d1x-f1x": (
        [("d", 0, _FORK_SKILL), ("f", 10, _FORK_SKILL)],
        1,
        1,
        0,
        [("command · probe-fork", "command", 5_000, 0.25)],
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
@pytest.mark.parametrize("case", list(_ONE_PROMPT_CASES))
async def test_live_only_the_one_dispatch_of_a_prompt_pairs_with_a_fork(engine, case, spans):
    """A slash command pairs with a fork only when its prompt_id holds no other dispatch: with two, no dispatch of
    the prompt has a fork and every fork counts on its own, a skill-tagged one as a skill and one without a skill
    name as an agent (only where the day has no span: there agents come from spans). One dispatch with its fork is
    one invocation. The rollups and the timeline agree: one row per skill, each fork's run and cost on one row."""
    events, skills, calls, agents, rows = _ONE_PROMPT_CASES[case]
    agents = 0 if spans else agents
    session_id = _session(f"one-prompt-{case}-{spans}")
    ts = _DAY + timedelta(minutes=5)
    runs = iter(((5, 0.25), (7, 0.5)))
    hooks = []
    try:
        await _seed_day(engine, session_id, spans)
        for n, (kind, second, skill) in enumerate(events):
            at = ts + timedelta(seconds=second)
            if kind == "d":
                hooks.append(_dispatch(session_id, at, _SLASH_PROMPT_ID, skill_name=skill))
                continue
            fork_id, (run, cost) = f"a{n}f0one0prompt0fork", next(runs)
            await _seed_fork_request(engine, session_id, fork_id, at + timedelta(seconds=1), cost)
            hooks += [
                _fork(session_id, at, fork_id, _SLASH_PROMPT_ID, skill_name=skill),
                _stop(session_id, at + timedelta(seconds=run), fork_id),
            ]
        await _ingest(engine, hooks)
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(
            skills, agents=agents, skill_calls=calls, agent_rows=agents
        )
        timeline = _dispatch_rows(await _api_detail(engine, session_id))
        assert timeline == rows
        assert len(timeline) == skills
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_a_start_without_an_agent_id_under_a_slash_commands_prompt_is_an_agent_not_its_fork(engine, spans):
    """A Claude Code SubagentStart with no agent_id (ingest stores it NULL) under the prompt_id of a slash command
    has no key to be a fork by: it stands alone as an agent beside the dispatch's skill, where the day has no span
    (there agents come from spans)."""
    session_id = _session(f"keyless-{spans}")
    agents = 0 if spans else 1
    ts = _DAY + timedelta(minutes=5)
    keyless = _fork(session_id, ts + timedelta(milliseconds=113), "", _SLASH_PROMPT_ID, skill_name=None)
    del keyless["agent_id"]
    try:
        await _seed_day(engine, session_id, spans)
        await _ingest(engine, [_dispatch(session_id, ts, _SLASH_PROMPT_ID), keyless])
        await _refresh_rollups(engine)

        async with engine.acquire() as conn:
            stored = await conn.fetchval(
                "SELECT agent_id FROM codemie_analytics.hook_events WHERE session_id = $1 "
                "AND event_type = 'agent.subagent.start'",
                session_id,
            )
        assert stored is None
        assert await _counts(engine, session_id) == _expected(1, agents=agents, agent_rows=agents)
        assert _dispatch_rows(await _api_detail(engine, session_id)) == [_NO_FORK_ROW]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["execution-span", "agent-end"])
async def test_live_run_evidence_just_before_midnight_counts_a_call_just_after_it(engine, evidence):
    """An Agent call at 00:10 whose only run evidence is stamped 23:30 the day before, within the gate's hour before
    the call, and arrives after the call's day was refreshed: the evidence re-queues the next day, which then counts
    the call. The backfill of the evidence's day queues the call's day the same way."""
    session_id = _session(f"evidence-before-midnight-{evidence}")
    day = _DAY.date()
    call_day = day + timedelta(days=1)
    evidence_ts = datetime.combine(day, time(23, 30), tzinfo=timezone.utc)
    tool_use_id = "toolu_01EvidenceBeforeMidnight"
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: day)
    try:
        await _seed_turn(engine, session_id, [(14 * 60 + 10, tool_use_id, "Explore")])  # 00:10 the next day
        async with engine.acquire() as conn:
            await refresher.mark_dirty(day, call_day, conn)
            await refresher.refresh(conn)
        assert await _rollup_agent_counts(engine, session_id, "Explore") == (0, 0)  # no evidence yet: denied

        if evidence == "execution-span":
            ingestor = PostgresTelemetryIngestor(engine, clock=lambda: day)
            await ingestor.ingest_otlp(
                "traces", _execution_span(session_id, evidence_ts, tool_use_id), "application/json"
            )
        else:
            end = _hook("agent.tool.end", "PostToolUse", session_id, evidence_ts, _CLAUDE_CODE,
                        tool_name="Agent", tool_use_id=tool_use_id)  # fmt: skip
            await _ingest(engine, [end])
        async with engine.acquire() as conn:
            await refresher.refresh(conn)

        assert await _rollup_agent_counts(engine, session_id, "Explore") == (1, 1)
        assert (await _api_detail(engine, session_id))["agent_count"] == 1

        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
            await refresher.mark_dirty(day, day, conn)
            call_day_kinds = await conn.fetchval(
                "SELECT kinds FROM codemie_analytics.rollup_dirty WHERE session_id = $1 AND day = $2",
                session_id,
                call_day,
            )
        assert call_day_kinds == 4  # SPAN_FACTS only
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["ingest", "backfill"])
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_a_skill_error_just_before_midnight_counts_a_skill_call_just_after_it(engine, spans, path):
    """A Skill call at 00:10 whose only run evidence is its agent.tool.error at 23:30 the day before, arriving after
    the call's day was refreshed: the error re-queues the call's day at ingest, and the backfill of the error's day
    queues it the same way. The call then counts as one skill on its day."""
    session_id = _session(f"skill-error-before-midnight-{spans}-{path}")
    day = _DAY.date()
    call_day = day + timedelta(days=1)
    call_ts = datetime.combine(call_day, time(0, 10), tzinfo=timezone.utc)
    error_ts = datetime.combine(day, time(23, 30), tzinfo=timezone.utc)
    tool_use_id, skill_name = "toolu_01SkillErrorBeforeMidnight", "err-inline"
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: day)
    try:
        if spans:
            await _seed_skill_span(engine, session_id, call_ts, tool_use_id, skill_name, executed=False)
        await _ingest(engine, [_skill_start(session_id, call_ts, tool_use_id, skill_name)])
        async with engine.acquire() as conn:
            await refresher.mark_dirty(day, call_day, conn)
            await refresher.refresh(conn)
        assert await _rollup_skill_counts(engine, session_id, skill_name) == (0, 0)  # no evidence yet: denied

        error = _hook("agent.tool.error", "PostToolUseFailure", session_id, error_ts, _CLAUDE_CODE,
                      prompt_id=_SKILL_PROMPT_ID, tool_name="Skill", tool_input=json.dumps({"skill": skill_name}),
                      tool_use_id=tool_use_id, error="Interrupted")  # fmt: skip
        await _ingest(engine, [error])
        async with engine.acquire() as conn:
            if path == "backfill":
                await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
                await refresher.mark_dirty(day, day, conn)
            await refresher.refresh(conn)
            call_day_skills = await conn.fetchval(
                "SELECT sum(skill_count) FROM codemie_analytics.tool_facts_daily WHERE session_id = $1 AND day = $2",
                session_id,
                call_day,
            )
        assert call_day_skills == 1
        assert await _rollup_skill_counts(engine, session_id, skill_name) == (1, 1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_an_agent_end_on_the_last_calendar_day_queues_no_day_after_it(engine):
    """An Agent agent.tool.end at 9999-12-31 23:30 is within the hour before a midnight with no day after it: ingest
    and a backfill of that day queue its own day and the day before only, and the refresher reads the queue back."""
    session_id = _session("agent-end-last-calendar-day")
    last = date(9999, 12, 31)
    end_ts = datetime.combine(last, time(23, 30), tzinfo=timezone.utc)
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: _DAY.date())
    try:
        end = _hook("agent.tool.end", "PostToolUse", session_id, end_ts, _CLAUDE_CODE,
                    tool_name="Agent", tool_use_id="toolu_01LastCalendarDay")  # fmt: skip
        await _ingest(engine, [end])
        async with engine.acquire() as conn:
            await refresher.mark_dirty(last, last, conn)
            queued = await conn.fetch(
                "SELECT day, kinds FROM codemie_analytics.rollup_dirty WHERE session_id = $1 ORDER BY 1", session_id
            )
            await refresher.refresh(conn)
            left = await conn.fetchval(
                "SELECT count(*) FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id
            )
        assert [(r["day"], r["kinds"]) for r in queued] == [(date(9999, 12, 30), 4), (last, 38)]
        assert left == 0
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_an_agents_first_start_arriving_after_its_continuation_the_next_day_counts_once(engine):
    """An agent continued by SendMessage the next day: its continuation start is ingested and refreshed first, its
    first start arrives later. The first start re-queues the continuation's day, which then no longer counts the
    agent: one agent in total. The backfill of the first start's day queues the continuation's day the same way."""
    session_id = _session("first-start-late")
    agent_id = "a4b0first0start01"
    day = _DAY.date()
    first_ts = _DAY + timedelta(minutes=5)
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: day)
    try:
        await _seed_request(engine, session_id)
        await _ingest(engine, [_fork(session_id, first_ts + timedelta(days=1), agent_id, _RESUME_PROMPT_ID, None)])
        await _refresh_rollups(engine)
        assert (await _counts(engine, session_id))["agent_count"] == 1

        await _ingest(engine, [_fork(session_id, first_ts, agent_id, "prompt-agent", None)])
        async with engine.acquire() as conn:
            await refresher.refresh(conn)

        assert await _counts(engine, session_id) == _expected(0, agents=1, skill_calls=0, agent_rows=1)

        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
            await refresher.mark_dirty(day, day, conn)
            continuation_day_kinds = await conn.fetchval(
                "SELECT kinds FROM codemie_analytics.rollup_dirty WHERE session_id = $1 AND day = $2",
                session_id,
                day + timedelta(days=1),
            )
        assert continuation_day_kinds == 4  # SPAN_FACTS only
    finally:
        await _cleanup(engine, session_id)


class _HeldEngine:
    """The live engine for one ingest: records its backend pid and, while `hold` is set, keeps the batch's
    transaction open just before its rollup_dirty insert (after its rows and lookups) until `release` is set."""

    def __init__(self, engine: AnalyticsPgEngine, hold: bool = False) -> None:
        self._engine = engine
        self.settings = engine.settings
        self.hold = hold
        self.held = asyncio.Event()
        self.release = asyncio.Event()
        self.pid: int | None = None

    def lock_key(self, name: str) -> int:
        return self._engine.lock_key(name)

    @asynccontextmanager
    async def acquire(self, timeout: float | None = None):
        async with self._engine.acquire(timeout=timeout) as conn:
            self.pid = conn.get_server_pid()
            yield _HeldConnection(conn, self)


class _HeldConnection:
    def __init__(self, conn: asyncpg.Connection, owner: _HeldEngine) -> None:
        self._conn = conn
        self._owner = owner

    def transaction(self):
        return self._conn.transaction()

    async def fetch(self, sql: str, *args: Any):
        return await self._conn.fetch(sql, *args)

    async def execute(self, sql: str, *args: Any):
        if self._owner.hold and "INSERT INTO rollup_dirty" in sql:
            self._owner.held.set()
            await self._owner.release.wait()
        return await self._conn.execute(sql, *args)


async def _done_or_waiting_on_a_lock(engine: AnalyticsPgEngine, task: asyncio.Task, held: _HeldEngine) -> None:
    """Poll, with a deadline, until the ingest task finished or its backend waits on a lock."""
    deadline = asyncio.get_running_loop().time() + 30
    while not task.done():
        async with engine.acquire() as conn:
            if held.pid is not None and await conn.fetchval(
                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = $1", held.pid
            ):
                return
        assert asyncio.get_running_loop().time() < deadline, "the ingest neither finished nor waited on a lock"
        await asyncio.sleep(0.05)  # polling interval of the wait above, not a timing assumption


@pytest.mark.asyncio
async def test_live_a_first_start_and_its_continuation_ingested_at_once_count_one_agent(engine):
    """Two batches of one session at once: A carries an agent's first start and stays open after its lookups, B its
    continuation the next day. Whichever order they commit in, the continuation's day is recomputed with the first
    start in view: a refresh while A is open, then one after both, leave one agent."""
    session_id = _session("first-start-concurrent")
    agent_id = "a4b0first0start02"
    first_ts = _DAY + timedelta(minutes=5)
    first = _HeldEngine(engine, hold=True)
    continuation = _HeldEngine(engine)
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: _DAY.date())
    first_ingest = continuation_ingest = None
    try:
        await _seed_request(engine, session_id)
        first_ingest = asyncio.create_task(
            _ingest(first, [_fork(session_id, first_ts, agent_id, "prompt-agent", None)])  # type: ignore[arg-type]
        )
        await asyncio.wait_for(first.held.wait(), 30)
        continuation_ingest = asyncio.create_task(
            _ingest(continuation, [_fork(session_id, first_ts + timedelta(days=1), agent_id, _RESUME_PROMPT_ID, None)])  # type: ignore[arg-type]
        )
        await _done_or_waiting_on_a_lock(engine, continuation_ingest, continuation)
        await _refresh_rollups(engine)

        first.release.set()
        await asyncio.wait_for(asyncio.gather(first_ingest, continuation_ingest), 30)
        async with engine.acquire() as conn:  # only the queue: a backfill of the first start's day re-queues too
            await refresher.refresh(conn)

        assert await _counts(engine, session_id) == _expected(0, agents=1, skill_calls=0, agent_rows=1)
    finally:
        first.release.set()
        for task in (first_ingest, continuation_ingest):
            if task is not None and not task.done():
                task.cancel()
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_the_backfill_of_an_agent_end_queues_the_day_of_the_subagent_it_names(engine):
    """An Agent call's agent.tool.end two days after the SubagentStart of the agent it names: the backfill of the
    end's day queues the agent's own day."""
    session_id = _session("agent-end-two-days")
    agent_id = "a9d8798d295da2cf0"  # the agentId of _AGENT_RESULT
    start_ts = _DAY + timedelta(minutes=5)
    end_day = _DAY.date() + timedelta(days=2)
    call = {"tool_name": "Agent", "tool_use_id": "toolu_01AgentEndTwoDays", "prompt_id": "prompt-agent"}
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: _DAY.date())
    try:
        await _seed_request(engine, session_id)
        await _ingest(
            engine,
            [
                _hook("agent.tool.start", "PreToolUse", session_id, start_ts, _CLAUDE_CODE, **call),
                _fork(session_id, start_ts + timedelta(milliseconds=300), agent_id, "prompt-agent", None),
                _hook("agent.tool.end", "PostToolUse", session_id, start_ts + timedelta(days=2), _CLAUDE_CODE,
                      **call, tool_response=_AGENT_RESULT[:300]),
            ],
        )  # fmt: skip

        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
            await refresher.mark_dirty(end_day, end_day, conn)
            agent_day_kinds = await conn.fetchval(
                "SELECT kinds FROM codemie_analytics.rollup_dirty WHERE session_id = $1 AND day = $2",
                session_id,
                _DAY.date(),
            )
        assert agent_day_kinds == 4  # SPAN_FACTS only
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_the_backfill_of_a_cut_skill_end_queues_the_day_of_the_fork_its_fork_agent_id_names(engine):
    """A Skill call's agent.tool.end two days after its fork, its tool_response cut inside the agentId: only its
    fork_agent_id names the fork, and the backfill of the end's day queues the fork's own day by it."""
    session_id = _session("fork-agent-id-two-days")
    call_ts = datetime.combine(_DAY.date(), time(12, 0), tzinfo=timezone.utc)
    end_day = _DAY.date() + timedelta(days=2)
    refresher = rollups.RollupRefresher(engine, batch_size=100, clock=lambda: _DAY.date())
    start, end = _skill_call(session_id, call_ts, _CUT_RESULT)
    try:
        await _seed_request(engine, session_id)
        await _ingest(
            engine,
            [
                start,
                _fork(session_id, call_ts + timedelta(seconds=1), _BACKGROUND_FORK_ID, _SKILL_PROMPT_ID),
                {**end, "fork_agent_id": _BACKGROUND_FORK_ID, "timestamp": _iso(call_ts + timedelta(days=2))},
            ],
        )

        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
            await refresher.mark_dirty(end_day, end_day, conn)
            fork_day_kinds = await conn.fetchval(
                "SELECT kinds FROM codemie_analytics.rollup_dirty WHERE session_id = $1 AND day = $2",
                session_id,
                _DAY.date(),
            )
        assert fork_day_kinds == 4  # SPAN_FACTS only
    finally:
        await _cleanup(engine, session_id)


# Skill calls in the shape of a live Claude Code capture with a deny rule on two skills: a denied call, inline or
# forked, leaves its TOOL span and PreToolUse(Skill) and nothing else (no execution span, no PostToolUse, no fork).


def _skill_start(
    session_id: str, ts: datetime, tool_use_id: str, skill_name: str, platform: dict[str, str] = _CLAUDE_CODE
) -> dict[str, Any]:
    return _hook(
        "agent.tool.start",
        "PreToolUse",
        session_id,
        ts,
        platform,
        prompt_id=_SKILL_PROMPT_ID,
        tool_name="Skill",
        tool_input=json.dumps({"skill": skill_name}),
        tool_use_id=tool_use_id,
        skill_name=skill_name,
    )


def _inline_skill_end(session_id: str, ts: datetime, tool_use_id: str, skill_name: str) -> dict[str, Any]:
    return _hook(
        "agent.tool.end",
        "PostToolUse",
        session_id,
        ts,
        _CLAUDE_CODE,
        prompt_id=_SKILL_PROMPT_ID,
        tool_name="Skill",
        tool_input=json.dumps({"skill": skill_name}),
        tool_use_id=tool_use_id,
        tool_response=json.dumps({"success": True, "commandName": skill_name}),
        duration_ms=3,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
@pytest.mark.parametrize(
    ("skill_name", "ran", "skills"),
    [("den-inline", False, 0), ("den-fork", False, 0), ("ok-inline", True, 1)],
    ids=["denied-inline", "denied-fork", "allowed-inline"],
)
async def test_live_a_skill_call_counts_only_with_evidence_it_ran(engine, skill_name, ran, skills, spans):
    """A denied Skill call is no skill and no agent, and has no timeline row; an allowed inline call (its execution
    span and PostToolUse) counts once, and on a day with spans it is a `skill` row."""
    session_id = _session(f"skill-gate-{skill_name}-{spans}")
    tool_use_id = f"toolu_{skill_name}"
    call_ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_day(engine, session_id, spans)
        if spans:
            await _seed_skill_span(engine, session_id, call_ts, tool_use_id, skill_name, executed=ran)
        events = [_skill_start(session_id, call_ts, tool_use_id, skill_name)]
        if ran:
            events.append(_inline_skill_end(session_id, call_ts + timedelta(milliseconds=75), tool_use_id, skill_name))
        await _ingest(engine, events)
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id, skill_name) == _expected(skills)
        labels = [label for label, *_ in _dispatch_rows(await _api_detail(engine, session_id))]
        assert labels == (["skill · ok-inline"] if ran and spans else [])
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_a_running_foreground_fork_counts_once_before_and_after_its_skill_call_ends(engine, spans):
    """While a foreground fork runs, its Skill call has no evidence it ran (TOOL span, PreToolUse, the fork's
    skill-tagged SubagentStart, no PostToolUse yet): the fork is the skill. Once the PostToolUse names the fork, the
    call counts and the fork is dropped. One skill and no agent throughout."""
    session_id = _session(f"running-fork-{spans}")
    call_ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_day(engine, session_id, spans)
        if spans:
            await _seed_skill_span(engine, session_id, call_ts, _SKILL_TOOL_USE_ID, executed=False)
        start, end = _skill_call(session_id, call_ts, _FOREGROUND_RESULT)
        await _ingest(
            engine, [start, _fork(session_id, call_ts + timedelta(seconds=1), _FOREGROUND_FORK_ID, _SKILL_PROMPT_ID)]
        )
        await _refresh_rollups(engine)
        assert await _counts(engine, session_id) == _expected(1)

        end["timestamp"] = _iso(call_ts + timedelta(seconds=30))
        await _ingest(engine, [end])
        await _refresh_rollups(engine)
        assert await _counts(engine, session_id) == _expected(1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("spans", [False, True], ids=["fallback-pair", "spans"])
async def test_live_another_clients_skill_start_without_an_end_counts(engine, spans):
    """Only Claude Code's tool ends are verified evidence: a Copilot agent.tool.start with skill_name and no end
    counts as it is."""
    session_id = _session(f"copilot-skill-start-{spans}")
    try:
        await _seed_day(engine, session_id, spans)
        await _ingest(
            engine,
            [
                _skill_start(
                    session_id,
                    _DAY + timedelta(minutes=5),
                    "call_copilot_skill_1",
                    "copilot-skill",
                    platform={"platform": "copilot-cli"},
                )
            ],
        )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id, "copilot-skill") == _expected(1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_cursor_skill_and_agent_spans_count_without_execution_evidence(engine):
    """The denied-call gate reads Claude Code spans only: a cursor.tool span of a skill and one of an agent, each
    with a tool_use_id and no execution span or tool end, count once each."""
    session_id = _session("cursor-spans")
    ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_turn(engine, session_id, [])
        async with engine.acquire() as conn:
            await conn.execute(
                _SPANS_SQL, ts, 1, b"t" * 16, _id(f"cursor-agent-{session_id}"), _id(f"int-{session_id}"), _TOOL,
                "cursor.tool", session_id, "Task", "cursor-agent-call", "Explore",
            )  # fmt: skip
            await conn.execute(
                """INSERT INTO spans
                    (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
                     session_id, tool_name, tool_use_id, skill_name, status_code, resource_id)
                   VALUES ($1, 1, $2, $3, $4, 1, 'cursor.tool', $5, 'Skill', 'cursor-skill-call', 'cursor-skill', 0, 0)""",
                ts + timedelta(minutes=1),
                b"t" * 16,
                _id(f"cursor-skill-{session_id}"),
                _id(f"int-{session_id}"),
                session_id,
            )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id, "cursor-skill") == _expected(1, agents=1, agent_rows=1)
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_live_hook_agent_count_takes_only_an_agent_type_kind_3_counts(engine):
    """On a fallback pair, a SubagentStart with an empty agent_type and one over 256 bytes have no kind-3 row, and
    tool_facts_daily does not count them either: one agent, the one with a countable agent_type."""
    session_id = _session("agent-type-bound")
    ts = _DAY + timedelta(minutes=5)
    try:
        await _seed_request(engine, session_id)
        untyped = _fork(session_id, ts, "a0empty0agent0ty1", "prompt-agent", None)
        untyped["agent_type"] = ""
        await _ingest(
            engine, [untyped, _fork(session_id, ts + timedelta(seconds=1), "a0typed0agent0ty1", "prompt-agent", None)]
        )
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, agent_type, agent_id) "
                "VALUES ($1, $2, 'agent.subagent.start', $3, 'a0long0agent0typ1')",
                ts + timedelta(seconds=2),
                session_id,
                "x" * 257,
            )
        await _refresh_rollups(engine)

        assert await _counts(engine, session_id) == _expected(0, agents=1, skill_calls=0, agent_rows=1)
    finally:
        await _cleanup(engine, session_id)
