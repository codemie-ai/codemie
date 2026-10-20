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

"""Live-Postgres behavioral checks for EPMCDME-15515's agent cost attribution.

The SQL-text tests in test_reader.py prove the query shape only. These write rows shaped like the
real telemetry (an Agent tool call's hook event naming its agentId, the subagent's llm_request spans
carrying that agentId, and api_request events grouped under those spans' parent) into the real local
analytics Postgres, call the real, unmocked `get_session_detail_dispatches`, and clean up after
themselves. Skipped (not failed) when the local analytics Postgres is unreachable.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

# pytest.ini forces PG_URL to a dead port for the whole suite; TEST_LOCAL_PG_URL points this file at
# a real local analytics Postgres (default: the docker-compose `postgres` service's credentials).
_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")

_SPANS_SQL = """
    INSERT INTO spans
        (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
         session_id, subagent_type, skill_name, tool_name, tool_use_id, attrs, success, status_code, resource_id)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
"""
_LOG_EVENTS_SQL = """
    INSERT INTO log_events
        (ts, session_id, event_kind, trace_id, span_id,
         cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
         severity, resource_id)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
"""
_AGENT_TOOL_HOOK_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type, tool_use_id, tool_name, attrs)
    VALUES ($1, $2, $3, $4, 'Agent', $5)
"""
_STOP_EVENT_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type, agent_id, attrs)
    VALUES ($1, $2, 'agent.subagent.stop', $3, $4)
"""


async def _get_engine() -> AnalyticsPgEngine | None:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        await PostgresAnalyticsRuntime(engine, settings).start()
        return engine
    except (OSError, asyncpg.PostgresError) as exc:  # no reachable database (refused, wrong server, bad auth): skip
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
        return None


async def _cleanup(engine: AnalyticsPgEngine, session_id: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute("DELETE FROM log_events WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM hook_events WHERE session_id = $1", session_id)
        await conn.execute("DELETE FROM spans WHERE session_id = $1", session_id)


async def _seed_agent(
    engine: AnalyticsPgEngine,
    *,
    session_id: str,
    ts: datetime,
    tool_use_id: str,
    agent_id: str,
    subagent_span_id: bytes,
    api_requests: list[tuple[float, int, int]],
    foreground: bool = False,
    parent_span_id: bytes = b"",
) -> None:
    """One subagent, shaped like the real telemetry: its Agent dispatch span and hook event (which
    names the agentId in tool_response), its llm_request span (same agentId, parent_span_id = the
    subagent's own span id), and its api_request events tagged with that same span id."""
    async with engine.acquire() as conn:
        await conn.execute(
            _SPANS_SQL,
            ts,
            6_000_000,
            b"trace",
            f"dispatch-{tool_use_id}".encode(),
            parent_span_id,
            1,
            "claude_code.tool",
            session_id,
            "Explore",
            "",
            "Agent",
            tool_use_id,
            None,
            "true",
            0,
            1,
        )
        await conn.execute(_AGENT_TOOL_HOOK_SQL, ts, session_id, "agent.tool.start", tool_use_id, None)
        if foreground:
            # A synchronous agent: its call starts, the subagent runs and stops, then the call returns its result
            tool_response = f'{{"isAsync":false,"agentId":"{agent_id}","status":"completed"}}'
            end_ts = ts + timedelta(seconds=10)
        else:
            # A background agent: the launch ack arrives seconds after the call starts (2-8 s in real sessions)
            tool_response = f'{{"isAsync":true,"agentId":"{agent_id}","status":"async_launched"}}'
            end_ts = ts + timedelta(seconds=3)
        await conn.execute(
            _AGENT_TOOL_HOOK_SQL,
            end_ts,
            session_id,
            "agent.tool.end",
            tool_use_id,
            json.dumps({"tool_response": tool_response}),
        )
        await conn.execute(
            _SPANS_SQL,
            ts + timedelta(seconds=1),
            1_000_000,
            b"trace",
            f"llm-{agent_id}".encode(),
            subagent_span_id,
            4,
            "claude_code.llm_request",
            session_id,
            "",
            "",
            "",
            "",
            json.dumps({"agent_id": agent_id}),
            "true",
            0,
            1,
        )
        for offset_s, (cost, input_tokens, output_tokens) in enumerate(api_requests):
            await conn.execute(
                _LOG_EVENTS_SQL,
                ts + timedelta(seconds=5 + offset_s),
                session_id,
                1,
                b"trace",
                subagent_span_id,
                cost,
                input_tokens,
                output_tokens,
                0,
                0,
                0,
                1,
            )


async def _seed_main_thread_request(
    engine: AnalyticsPgEngine, *, session_id: str, ts: datetime, span_id: bytes, cost: float, input_tokens: int
) -> None:
    """One api_request of the main conversation, grouped under the span the dispatches are children of."""
    async with engine.acquire() as conn:
        await conn.execute(_LOG_EVENTS_SQL, ts, session_id, 1, b"trace", span_id, cost, input_tokens, 10, 0, 0, 0, 1)


@pytest.mark.asyncio
async def test_two_parallel_agents_get_their_own_exact_totals_not_an_even_split():
    """AC #1 and #2: each subagent shows only its own api_requests, and two agents doing different
    amounts of work show different values - exact, from the agentId chain, not a split."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15515-live-parallel"
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=10)
    try:
        await _cleanup(engine, session_id)
        await _seed_agent(
            engine,
            session_id=session_id,
            ts=start,
            tool_use_id="tu-docs",
            agent_id="agentdocs",
            subagent_span_id=b"sub-docs",
            api_requests=[(0.11, 217, 794)],
        )
        await _seed_agent(
            engine,
            session_id=session_id,
            ts=start,
            tool_use_id="tu-tests",
            agent_id="agenttests",
            subagent_span_id=b"sub-tests",
            api_requests=[(0.05, 300, 400), (0.12, 243, 2308)],
        )

        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id)
        assert len(rows) == 2
        by_cost = sorted(rows, key=lambda r: r["cost_usd"])
        assert by_cost[0]["cost_usd"] == pytest.approx(0.11)
        assert by_cost[0]["input_tokens"] == 217
        assert by_cost[0]["output_tokens"] == 794
        assert by_cost[1]["cost_usd"] == pytest.approx(0.17)
        assert by_cost[1]["input_tokens"] == 543
        assert by_cost[1]["output_tokens"] == 2708
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_dispatch_without_an_llm_group_has_no_cost_rather_than_a_guess():
    """A dispatch whose agentId is known but whose subagent has no api_request group has no attributable
    cost: NULL, not 0, not a share of another dispatch's total, and not the main thread's cost from the
    parent span the dispatch was launched from (CR-001)."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15515-live-no-handback"
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=10)
    try:
        await _cleanup(engine, session_id)
        await _seed_agent(
            engine,
            session_id=session_id,
            ts=start,
            tool_use_id="tu-done",
            agent_id="agentdone",
            subagent_span_id=b"sub-done",
            api_requests=[(0.04, 100, 50)],
            parent_span_id=b"main-span",
        )
        # A second dispatch from the same main-thread span that never handed back: Agent span and hook event only.
        async with engine.acquire() as conn:
            await conn.execute(
                _SPANS_SQL,
                start,
                6_000_000,
                b"trace",
                b"dispatch-orphan",
                b"main-span",
                1,
                "claude_code.tool",
                session_id,
                "Explore",
                "",
                "Agent",
                "tu-orphan",
                None,
                "true",
                0,
                1,
            )
            await conn.execute(
                _AGENT_TOOL_HOOK_SQL,
                start,
                session_id,
                "agent.tool.end",
                "tu-orphan",
                json.dumps({"tool_response": '{"agentId":"agentorphan","status":"async_launched"}'}),
            )
        # The main thread keeps working after the launch: its cost must not land on the orphan.
        await _seed_main_thread_request(
            engine,
            session_id=session_id,
            ts=start + timedelta(seconds=2),
            span_id=b"main-span",
            cost=0.5,
            input_tokens=900,
        )

        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id)
        assert len(rows) == 2
        done = next(r for r in rows if r["cost_usd"] is not None)
        orphan = next(r for r in rows if r["cost_usd"] is None)
        assert done["cost_usd"] == pytest.approx(0.04)
        assert orphan["input_tokens"] is None
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_dispatch_with_no_agent_id_falls_back_to_its_parent_span_from_its_start_on():
    """A dispatch with no agentId at all (no chain) keeps the pre-existing fallback: its parent span's
    api_requests, counted from the first dispatch under that parent on - not the parent's earlier work (CR-002)."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15515-live-no-chain"
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=10)
    try:
        await _cleanup(engine, session_id)
        async with engine.acquire() as conn:
            await conn.execute(
                _SPANS_SQL,
                start,
                6_000_000,
                b"trace",
                b"dispatch-legacy",
                b"main-span",
                1,
                "claude_code.tool",
                session_id,
                "Explore",
                "",
                "Agent",
                "tu-legacy",
                None,
                "true",
                0,
                1,
            )
            await conn.execute(
                _AGENT_TOOL_HOOK_SQL,
                start,
                session_id,
                "agent.tool.end",
                "tu-legacy",
                json.dumps({"tool_response": '{"status":"completed"}'}),
            )
        await _seed_main_thread_request(
            engine,
            session_id=session_id,
            ts=start - timedelta(seconds=5),
            span_id=b"main-span",
            cost=0.3,
            input_tokens=700,
        )
        await _seed_main_thread_request(
            engine,
            session_id=session_id,
            ts=start + timedelta(seconds=2),
            span_id=b"main-span",
            cost=0.07,
            input_tokens=120,
        )

        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id)
        (row,) = rows
        assert row["cost_usd"] == pytest.approx(0.07)
        assert row["input_tokens"] == 120
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_subagent_run_time_runs_from_its_call_start_to_its_stop_event_and_is_null_without_one():
    """A background subagent's run time is its SubagentStop hook event minus its Agent call's start
    (agent.tool.start), not the launch ack that arrives seconds later, matched by agentId. Its bar starts at
    that call start too. A subagent with no stop event has no run time (null)."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15512-live-stop"
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=10)
    try:
        await _cleanup(engine, session_id)
        await _seed_agent(
            engine,
            session_id=session_id,
            ts=start,
            tool_use_id="tu-stopped",
            agent_id="agentstopped",
            subagent_span_id=b"sub-stopped",
            api_requests=[(0.11, 217, 794)],
        )
        await _seed_agent(
            engine,
            session_id=session_id,
            ts=start,
            tool_use_id="tu-running",
            agent_id="agentrunning",
            subagent_span_id=b"sub-running",
            api_requests=[(0.05, 300, 400)],
        )
        async with engine.acquire() as conn:
            await conn.execute(_STOP_EVENT_SQL, start + timedelta(seconds=30), session_id, "agentstopped", "{}")

        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id)
        by_cost = {round(r["cost_usd"], 2): r for r in rows}
        assert by_cost[0.11]["subagent_duration_ms"] == 30_000
        assert by_cost[0.11]["dispatch_start"] == start.replace(tzinfo=None)  # the reader returns naive UTC
        assert by_cost[0.05]["subagent_duration_ms"] is None
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_foreground_agent_runs_from_its_call_start_to_its_stop_even_though_its_stop_precedes_the_result():
    """A synchronous agent's stop event arrives before its Agent tool call returns, so its run time is measured
    from the call start (agent.tool.start), not from the result (agent.tool.end), the same anchor as a
    background agent."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15512-live-foreground"
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=10)
    try:
        await _cleanup(engine, session_id)
        await _seed_agent(
            engine,
            session_id=session_id,
            ts=start,
            tool_use_id="tu-foreground",
            agent_id="agentforeground",
            subagent_span_id=b"sub-foreground",
            api_requests=[(0.11, 217, 794)],
            foreground=True,
        )
        async with engine.acquire() as conn:
            await conn.execute(_STOP_EVENT_SQL, start + timedelta(seconds=8), session_id, "agentforeground", "{}")

        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id)
        (row,) = rows
        assert row["subagent_duration_ms"] == 8_000
        assert row["dispatch_start"] == start.replace(tzinfo=None)  # the reader returns naive UTC, as elsewhere
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


async def _agent_bars(engine: AnalyticsPgEngine, session_id: str, session_start: datetime) -> list[tuple[int, int]]:
    """Each agent bar as the API returns it, [start, end) in ms from the session start, in start order: the
    real reader rows passed through the handler, so the check covers the SQL and the offset/clamp logic."""
    rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id)
    dispatches = LocalAnalyticsHandler._build_dispatches(rows, session_start.replace(tzinfo=None), 600_000, 1.0)
    agents = [d for d in dispatches if d["kind"] == "agent"]
    return sorted((d["start_offset_ms"], d["start_offset_ms"] + d["duration_ms"]) for d in agents)


@pytest.mark.asyncio
async def test_parallel_subagents_have_overlapping_windows():
    """EPMCDME-15512 AC3: two background subagents called one second apart run at the same time, so their bars
    overlap. Each bar runs from its own Agent call start to its own SubagentStop."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15512-live-parallel-windows"
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=10)
    try:
        await _cleanup(engine, session_id)
        for tool_use_id, agent_id, offset_s, run_s in (
            ("tu-par-a", "agentpara", 0, 64.5),
            ("tu-par-b", "agentparb", 1, 34.2),
        ):
            await _seed_agent(
                engine,
                session_id=session_id,
                ts=start + timedelta(seconds=offset_s),
                tool_use_id=tool_use_id,
                agent_id=agent_id,
                subagent_span_id=f"sub-{agent_id}".encode(),
                api_requests=[(0.01, 10, 10)],
            )
            async with engine.acquire() as conn:
                stop_ts = start + timedelta(seconds=offset_s + run_s)
                await conn.execute(_STOP_EVENT_SQL, stop_ts, session_id, agent_id, "{}")

        (first_start, first_end), (second_start, second_end) = await _agent_bars(
            engine, session_id, start - timedelta(seconds=10)
        )
        assert (first_start, first_end) == (10_000, 74_500)
        assert (second_start, second_end) == (11_000, 45_200)
        assert second_start < first_end
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_sequential_subagents_do_not_overlap():
    """EPMCDME-15512 AC3: a subagent called after the previous one stopped starts after that bar ends."""
    engine = await _get_engine()
    session_id = "test-epmcdme-15512-live-sequential-windows"
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=10)
    try:
        await _cleanup(engine, session_id)
        for tool_use_id, agent_id, offset_s, run_s in (
            ("tu-seq-a", "agentseqa", 0, 20.6),
            ("tu-seq-b", "agentseqb", 25, 20),
        ):
            await _seed_agent(
                engine,
                session_id=session_id,
                ts=start + timedelta(seconds=offset_s),
                tool_use_id=tool_use_id,
                agent_id=agent_id,
                subagent_span_id=f"sub-{agent_id}".encode(),
                api_requests=[(0.01, 10, 10)],
            )
            async with engine.acquire() as conn:
                stop_ts = start + timedelta(seconds=offset_s + run_s)
                await conn.execute(_STOP_EVENT_SQL, stop_ts, session_id, agent_id, "{}")

        (first_start, first_end), (second_start, second_end) = await _agent_bars(
            engine, session_id, start - timedelta(seconds=10)
        )
        assert (first_start, first_end) == (10_000, 30_600)
        assert (second_start, second_end) == (35_000, 55_000)
        assert second_start >= first_end
    finally:
        await _cleanup(engine, session_id)
        await engine.close()
