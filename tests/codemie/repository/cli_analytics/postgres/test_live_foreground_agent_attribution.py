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

"""Live-Postgres checks for EPMCDME-16939: the naming rule that ties a Claude Code Agent call to the agent it
started, in its forward form (call -> agent id) and its reverse form (started agent -> call), run as SQL against
hook rows shaped like the real telemetry. Skipped (not failed) when no local analytics Postgres is reachable."""

from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from codemie.repository.cli_analytics.postgres import rollups
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.agent_call_attribution import (
    AGENT_CALL_DAYS_SQL,
    agent_call_agent_id,
    agent_call_claim,
    agent_call_ids_cte,
    agent_call_of,
)
from codemie.repository.cli_analytics.postgres.fork_attribution import dispatch_fork_candidate
from codemie.repository.cli_analytics.postgres.hook_row_sql import call_end_ts
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from tests.codemie.repository.cli_analytics.support.live_engine import cleanup_live_session as _cleanup
from tests.codemie.repository.cli_analytics.support.live_engine import get_live_engine as _get_engine
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE

_HOOK_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type, tool_use_id, tool_name, agent_id, attrs)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
"""
_CUT = '{"status":"completed","prompt":"a long echoed prompt cut before its agentId'


async def _hook(
    engine: AnalyticsPgEngine,
    ts: datetime,
    session_id: str,
    event_type: str,
    tool_use_id: str | None,
    *,
    tool_name: str | None = "Agent",
    agent_id: str | None = None,
    **attrs: Any,
) -> None:
    async with engine.acquire() as conn:
        await conn.execute(
            _HOOK_SQL,
            ts,
            session_id,
            event_type,
            tool_use_id,
            tool_name,
            agent_id,
            json.dumps(attrs) if attrs else None,
        )


async def _call(
    engine: AnalyticsPgEngine,
    ts: datetime,
    session_id: str,
    tool_use_id: str,
    *,
    stops: tuple[str, ...] = (),
    **end_attrs: Any,
) -> None:
    """One Agent call: its start, the stops of the agents it ran (each carrying the call's tool_use_id) and its
    end with `end_attrs` (tool_response, fork_agent_id, platform)."""
    await _hook(engine, ts, session_id, "agent.tool.start", tool_use_id)
    for offset, agent_id in enumerate(stops, start=1):
        await _hook(
            engine,
            ts + timedelta(seconds=offset),
            session_id,
            "agent.subagent.stop",
            tool_use_id,
            tool_name=None,
            agent_id=agent_id,
        )
    await _hook(engine, ts + timedelta(seconds=10), session_id, "agent.tool.end", tool_use_id, **end_attrs)


def _text(agent_id: str) -> str:
    return f'{{"status":"completed","agentId":"{agent_id}"}}'


async def _forward(engine: AnalyticsPgEngine, session_id: str, tool_use_id: str) -> str | None:
    async with engine.acquire() as conn:
        return await conn.fetchval(f"SELECT {agent_call_agent_id('$1::text', '$2::text')}", session_id, tool_use_id)


async def _reverse(engine: AnalyticsPgEngine, session_id: str, agent_id: str) -> str | None:
    async with engine.acquire() as conn:
        return await conn.fetchval(f"SELECT {agent_call_of('$1::text', '$2::text')}", session_id, agent_id)


@pytest.mark.asyncio
async def test_an_agent_call_names_its_agent_by_field_then_stop_then_text_and_the_reverse_form_agrees():
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-naming"
    other_session = "test-epmcdme-16939-live-naming-other"
    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        # a cut end: only its stop names the agent; another client's stop on the same call is no source
        await _call(engine, t0, session_id, "tu-stop", stops=("ag-stop",), tool_response=_CUT)
        await _hook(
            engine,
            t0 + timedelta(seconds=2),
            session_id,
            "agent.subagent.stop",
            "tu-stop",
            tool_name=None,
            agent_id="ag-noise",
            platform="codex",
        )
        # the field beats the stop and the text; the stop beats a conflicting text id; the text alone answers
        await _call(
            engine,
            t0 + timedelta(minutes=1),
            session_id,
            "tu-field",
            stops=("ag-stop-f",),
            fork_agent_id="ag-field",
            tool_response=_text("ag-text-f"),
        )
        await _call(
            engine,
            t0 + timedelta(minutes=2),
            session_id,
            "tu-stopwins",
            stops=("ag-stop-s",),
            tool_response=_text("ag-text-s"),
        )
        await _call(engine, t0 + timedelta(minutes=3), session_id, "tu-text", tool_response=_text("ag-textonly"))
        # two stops with different ids: ambiguous, and the text is blocked
        await _call(
            engine,
            t0 + timedelta(minutes=4),
            session_id,
            "tu-ambig",
            stops=("ag-amb1", "ag-amb2"),
            tool_response=_text("ag-text-a"),
        )
        # an empty tool_use_id and another client's call never match
        await _call(engine, t0 + timedelta(minutes=5), session_id, "", stops=("ag-empty",), fork_agent_id="ag-empty")
        await _call(
            engine, t0 + timedelta(minutes=6), session_id, "tu-other", fork_agent_id="ag-other", platform="codex"
        )
        # a tool_use_id reused in another session: that session's stop does not name this session's call
        await _call(engine, t0 + timedelta(minutes=7), session_id, "tu-cut", tool_response=_CUT)
        await _hook(
            engine,
            t0 + timedelta(minutes=7),
            other_session,
            "agent.subagent.stop",
            "tu-cut",
            tool_name=None,
            agent_id="ag-foreign",
        )

        assert await _forward(engine, session_id, "tu-stop") == "ag-stop"
        assert await _reverse(engine, session_id, "ag-stop") == "tu-stop"
        assert await _reverse(engine, session_id, "ag-noise") is None
        assert await _forward(engine, session_id, "tu-field") == "ag-field"
        assert await _reverse(engine, session_id, "ag-field") == "tu-field"
        assert await _reverse(engine, session_id, "ag-stop-f") is None
        assert await _reverse(engine, session_id, "ag-text-f") is None
        assert await _forward(engine, session_id, "tu-stopwins") == "ag-stop-s"
        assert await _reverse(engine, session_id, "ag-text-s") is None
        assert await _forward(engine, session_id, "tu-text") == "ag-textonly"
        assert await _reverse(engine, session_id, "ag-textonly") == "tu-text"
        assert await _forward(engine, session_id, "tu-ambig") is None
        for agent_id in ("ag-amb1", "ag-amb2", "ag-text-a"):
            assert await _reverse(engine, session_id, agent_id) is None
        assert await _forward(engine, session_id, "") is None
        assert await _reverse(engine, session_id, "ag-empty") is None
        assert await _forward(engine, session_id, "tu-other") is None
        assert await _reverse(engine, session_id, "ag-other") is None
        assert await _forward(engine, session_id, "tu-cut") is None
        assert await _forward(engine, other_session, "tu-cut") is None
        assert await _reverse(engine, other_session, "ag-foreign") is None
        # a stop with no call at all is no Agent call
        assert await _forward(engine, session_id, "tu-missing") is None
    finally:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        await engine.close()


@pytest.mark.asyncio
async def test_two_calls_naming_one_agent_leave_it_to_the_highest_source_then_the_earliest_call():
    """A resumed agent or a prompt quoting an earlier agentId: one agent, two calls. The higher-ranked source wins
    whatever the order; on a tie the earliest call wins; the other call is unresolved in both forms."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-naming-shared"
    t0 = datetime.now(timezone.utc) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _call(engine, t0, session_id, "tu-quote", tool_response=_text("ag-resumed"))
        await _call(engine, t0 + timedelta(minutes=1), session_id, "tu-resume", fork_agent_id="ag-resumed")
        await _call(engine, t0 + timedelta(minutes=2), session_id, "tu-tie-b", tool_response=_text("ag-tie"))
        await _call(engine, t0 + timedelta(minutes=3), session_id, "tu-tie-a", tool_response=_text("ag-tie"))
        await _call(engine, t0 + timedelta(minutes=4), session_id, "tu-stop-b", stops=("ag-twice",))
        await _call(engine, t0 + timedelta(minutes=5), session_id, "tu-stop-a", stops=("ag-twice",))

        assert await _reverse(engine, session_id, "ag-resumed") == "tu-resume"
        assert await _forward(engine, session_id, "tu-resume") == "ag-resumed"
        assert await _forward(engine, session_id, "tu-quote") is None
        assert await _reverse(engine, session_id, "ag-tie") == "tu-tie-b"
        assert await _forward(engine, session_id, "tu-tie-b") == "ag-tie"
        assert await _forward(engine, session_id, "tu-tie-a") is None
        assert await _reverse(engine, session_id, "ag-twice") == "tu-stop-b"
        assert await _forward(engine, session_id, "tu-stop-a") is None
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_call_ends_at_its_latest_end_or_error():
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-call-end"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _call(engine, t0, session_id, "tu-ended", tool_response=_CUT)
        await _hook(engine, t0 + timedelta(seconds=20), session_id, "agent.tool.error", "tu-ended")
        await _hook(engine, t0, session_id, "agent.tool.start", "tu-open")
        sql = (
            f"SELECT {call_end_ts('c', 'Agent')} FROM hook_events c "
            "WHERE c.session_id = $1 AND c.tool_use_id = $2 AND c.event_type = 'agent.tool.start'"
        )
        async with engine.acquire() as conn:
            assert await conn.fetchval(sql, session_id, "tu-ended") == t0 + timedelta(seconds=20)
            assert await conn.fetchval(sql, session_id, "tu-open") is None
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


_START_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type, agent_id, agent_type, prompt_id)
    VALUES ($1, $2, 'agent.subagent.start', $3, 'general-purpose', $4)
"""
_DISPATCH_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type, skill_name, prompt_id)
    VALUES ($1, $2, 'agent.skill.dispatch', 'my-skill', $3)
"""


async def _fork_candidates(engine: AnalyticsPgEngine, session_id: str) -> dict[str, bool]:
    sql = (
        f"SELECT f.agent_id, {dispatch_fork_candidate('f')} AS candidate FROM hook_events f "
        "WHERE f.session_id = $1 AND f.event_type = 'agent.subagent.start'"
    )
    async with engine.acquire() as conn:
        return {row["agent_id"]: row["candidate"] for row in await conn.fetch(sql, session_id)}


@pytest.mark.asyncio
async def test_a_started_agent_named_only_by_its_stop_is_no_slash_command_fork_and_is_counted_once():
    """D1: the end's tool_response is cut, a day with no tool spans, and the stop (which arrives after the start
    and after the end) is the only name of the agent. It is no fork candidate of the slash command, the Timeline
    and the count agree, and agent_count counts it once."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-stop-named"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        async with engine.acquire() as conn:
            await conn.execute(_DISPATCH_SQL, t0, session_id, "p1")
            await conn.execute(_START_SQL, t0 + timedelta(seconds=1), session_id, "ag-stop", "p1")
        await _hook(engine, t0 + timedelta(seconds=1), session_id, "agent.tool.start", "tu-1")
        await _hook(engine, t0 + timedelta(seconds=11), session_id, "agent.tool.end", "tu-1", tool_response=_CUT)
        await _hook(  # the stop arrives last
            engine,
            t0 + timedelta(seconds=12),
            session_id,
            "agent.subagent.stop",
            "tu-1",
            tool_name=None,
            agent_id="ag-stop",
        )

        assert await _fork_candidates(engine, session_id) == {"ag-stop": False}
        scalars = await PostgresCliAnalyticsReader(engine).get_session_detail_scalars(session_id, scope=DETAIL_SCOPE)
        assert scalars[0]["agent_count"] == 1
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_fork_candidate_follows_the_one_call_that_names_the_agent_conflicting_stops_included():
    """D1: two stops of one call that disagree name no agent (both starts stay candidates), a stop beats a text id
    (the text id's agent stays a candidate), and a resumed agent is named by one call, so it is no candidate."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-stop-conflicts"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        async with engine.acquire() as conn:
            for offset, agent_id in enumerate(("ag-amb1", "ag-amb2", "ag-resumed", "ag-stop", "ag-text", "ag-plain")):
                await conn.execute(_START_SQL, t0 + timedelta(seconds=offset), session_id, agent_id, "p1")
        await _call(engine, t0 + timedelta(minutes=1), session_id, "tu-amb", stops=("ag-amb1", "ag-amb2"))
        await _call(engine, t0 + timedelta(minutes=2), session_id, "tu-r1", stops=("ag-resumed",))
        await _call(engine, t0 + timedelta(minutes=3), session_id, "tu-r2", stops=("ag-resumed",))
        await _call(
            engine,
            t0 + timedelta(minutes=4),
            session_id,
            "tu-conflict",
            stops=("ag-stop",),
            tool_response=_text("ag-text"),
        )

        assert await _fork_candidates(engine, session_id) == {
            "ag-amb1": True,
            "ag-amb2": True,
            "ag-resumed": False,
            "ag-stop": False,
            "ag-text": True,
            "ag-plain": True,
        }
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


# ── The Timeline row of a foreground Agent call (B1, B3) ──

_SPAN_SQL = """
    INSERT INTO spans
        (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
         session_id, subagent_type, skill_name, tool_name, tool_use_id, attrs, success, status_code, resource_id)
    VALUES ($1, $2, 'trace'::bytea, $3, $4, $5, $6, $7, $8, '', $9, $10, $11, 'true', 0, 1)
"""
_REQUEST_SQL = """
    INSERT INTO log_events
        (ts, session_id, event_kind, trace_id, span_id, model, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, severity, resource_id)
    VALUES ($1, $2, 1, 'trace'::bytea, $3, $4, $5, $6, $7, 0, 0, 0, 1)
"""
_PARENT_SPAN = b"main-span"


async def _request(
    engine: AnalyticsPgEngine, ts: datetime, session_id: str, span_id: bytes, model: str, cost: float, tokens: int
) -> None:
    async with engine.acquire() as conn:
        await conn.execute(_REQUEST_SQL, ts, session_id, span_id, model, cost, tokens, tokens // 2)


async def _tool_span(
    engine: AnalyticsPgEngine,
    ts: datetime,
    session_id: str,
    tool_use_id: str,
    subagent_type: str,
    *,
    tool_name: str = "Agent",
    span_name: str = "claude_code.tool",
    span_id: bytes | None = None,
) -> None:
    async with engine.acquire() as conn:
        await conn.execute(
            _SPAN_SQL,
            ts,
            6_000_000,
            span_id or b"dispatch-" + tool_use_id.encode(),
            _PARENT_SPAN,
            1,
            span_name,
            session_id,
            subagent_type,
            tool_name,
            tool_use_id,
            None,
        )


async def _foreground_agent(
    engine: AnalyticsPgEngine,
    session_id: str,
    ts: datetime,
    tool_use_id: str,
    agent_id: str,
    *,
    source: str,
    model: str,
    cost: float,
    tokens: int,
    subagent_type: str = "general-purpose",
    closing: str | None = "agent.tool.end",
    stop_duration_ms: int | None = 5000,
    **end_attrs: Any,
) -> None:
    """One foreground Agent call shaped like the real telemetry. Its result is cut, so its text does not name the
    agent unless `source` is "text" (`end_attrs` may add a conflicting text id); the other sources are the end's
    `fork_agent_id` ("field") and the stop's `agent_id` with the call's tool_use_id ("stop"). The subagent starts
    1 s in, makes one request 3 s in, stops 6 s in (a 5 s run by the timestamps) and the call returns at 10 s with
    `closing` (agent.tool.end, or agent.tool.error for a call that errored or was cancelled: no result text, no
    field). The stop carries `stop_duration_ms` as its duration_ms (none when None, as an older CLI sends it)."""
    sub_span = b"sub-" + agent_id.encode()
    await _tool_span(engine, ts, session_id, tool_use_id, subagent_type)
    async with engine.acquire() as conn:
        await conn.execute(
            _SPAN_SQL,
            ts + timedelta(seconds=1),
            1_000_000,
            b"llm-" + agent_id.encode(),
            sub_span,
            4,
            "claude_code.llm_request",
            session_id,
            "",
            "",
            "",
            json.dumps({"agent_id": agent_id}),
        )
    await _request(engine, ts + timedelta(seconds=3), session_id, sub_span, model, cost, tokens)
    await _hook(engine, ts, session_id, "agent.tool.start", tool_use_id)
    await _hook(
        engine, ts + timedelta(seconds=1), session_id, "agent.subagent.start", None, tool_name=None, agent_id=agent_id
    )
    await _hook(
        engine,
        ts + timedelta(seconds=6),
        session_id,
        "agent.subagent.stop",
        tool_use_id if source == "stop" else None,
        tool_name=None,
        agent_id=agent_id,
        **({} if stop_duration_ms is None else {"duration_ms": stop_duration_ms}),
    )
    if closing is None:  # never ended: its execution span stands in for the evidence it ran
        async with engine.acquire() as conn:
            await conn.execute(
                _SPAN_SQL, ts, 1_000_000, b"exec-" + tool_use_id.encode(), _PARENT_SPAN, 2, "claude_code.tool.execution",
                session_id, "", "Agent", tool_use_id, None,
            )  # fmt: skip
        return
    attrs: dict[str, Any] = {}
    if closing == "agent.tool.end":
        attrs["tool_response"] = _text(agent_id) if source == "text" else _CUT
        if source == "field":
            attrs["fork_agent_id"] = agent_id
    await _hook(engine, ts + timedelta(seconds=10), session_id, closing, tool_use_id, **{**attrs, **end_attrs})


async def _dispatch_rows(engine: AnalyticsPgEngine, session_id: str) -> list[dict[str, Any]]:
    return await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id, scope=DETAIL_SCOPE)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["field", "stop", "text", "stop_over_text"])
async def test_a_foreground_agent_with_a_cut_result_gets_its_own_data_from_each_source(source: str):
    """B1: the end's result is cut, so the id comes from the field, the stop, or the complete text; a stop wins over
    a conflicting text id. The row shows the subagent's model, tokens, cost and a duration, never the parent's
    later request."""
    engine = await _get_engine()
    session_id = f"test-epmcdme-16939-live-source-{source}"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        conflict = source == "stop_over_text"
        await _foreground_agent(
            engine,
            session_id,
            t0,
            "tu-fg",
            "ag-own",
            source="stop" if conflict else source,
            model="model-sub",
            cost=0.25,
            tokens=400,
            **({"tool_response": _text("ag-conflict")} if conflict else {}),
        )
        await _request(engine, t0 + timedelta(seconds=12), session_id, _PARENT_SPAN, "model-parent", 9.0, 9000)

        (row,) = await _dispatch_rows(engine, session_id)

        assert row["cost_usd"] == pytest.approx(0.25)
        assert (row["input_tokens"], row["output_tokens"]) == (400, 200)
        assert [m["model_name"] for m in row["models"]] == ["model-sub"]
        assert row["subagent_duration_ms"] == 5000
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_duration_ms", "expected_ms"),
    [
        (3000, 3000),  # the stop's own duration_ms, though its timestamps say 5 s
        (7438, 7438),  # and when it is longer than the timestamps
        (0, 0),  # a real zero is a value, not an absence
        (None, 5000),  # an older CLI sends none: the stop's timestamp minus the start
    ],
)
async def test_a_resolved_agents_duration_is_the_stops_duration_ms_else_stop_ts_minus_start(
    stop_duration_ms: int | None, expected_ms: int
):
    """C1: on a real capture the stop row lags the subagent's end, so its timestamps overstate the run; the row's
    duration is the stop's duration_ms when it has one, else the stop's timestamp minus the start (1 s in, 6 s in)."""
    engine = await _get_engine()
    session_id = f"test-epmcdme-16939-live-duration-{stop_duration_ms}"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _foreground_agent(
            engine,
            session_id,
            t0,
            "tu-fg",
            "ag-own",
            source="stop",
            model="model-sub",
            cost=0.25,
            tokens=400,
            stop_duration_ms=stop_duration_ms,
        )

        (row,) = await _dispatch_rows(engine, session_id)

        assert row["subagent_duration_ms"] == expected_ms
        assert row["cost_usd"] == pytest.approx(0.25)
        # the bar's start keeps step with whichever duration source fed subagent_duration_ms above: anchored to
        # the stop minus duration_ms when the stop has one (so start + duration always lands exactly on the stop,
        # whatever the real gap to SubagentStart turns out to be), else unchanged at launch_ts (1 s in), where the
        # timestamp-only fallback already agrees with itself
        stop_ts = t0 + timedelta(seconds=6)
        launch_ts = t0 + timedelta(seconds=1)
        expected_start = launch_ts if stop_duration_ms is None else stop_ts - timedelta(milliseconds=expected_ms)
        assert row["dispatch_start"].replace(tzinfo=timezone.utc) == expected_start
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_duration_ms", "second_duration_ms", "expected_ms"),
    [
        # the CLI rereads the whole transcript on every stop, so the second stop's duration_ms is cumulative from
        # the agent's first line: here 1 s (its own start) to 68 s, the 5 s first run + the 60 s pause + the 2 s
        # continuation. Only this latest value is read; subtracting the pause leaves the two runs (5 s + 2 s)
        (5000, 67000, 7000),
        (None, 67000, 7000),  # an earlier stop missing it (an older CLI) does not matter: only the latest is read
        (5000, None, 7000),  # the latest stop has none: the timestamps answer instead, same result here
    ],
)
async def test_a_continued_agents_duration_reads_its_latest_stops_duration_ms_less_the_idle_gap(
    first_duration_ms: int | None, second_duration_ms: int | None, expected_ms: int
):
    """C1: an agent continued by SendMessage stops twice; its duration reads the latest stop's own duration_ms
    (already the whole transcript span, idle included) less the idle time between its runs (60 s), not a sum of
    every stop's duration_ms, which a real CLI never sends as independent per-run deltas. With a latest stop that
    has none, the whole agent takes the timestamps: its last stop minus its first start, less the same idle time."""
    engine = await _get_engine()
    session_id = f"test-epmcdme-16939-live-duration-continued-{first_duration_ms}-{second_duration_ms}"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _foreground_agent(
            engine,
            session_id,
            t0,
            "tu-fg",
            "ag-own",
            source="stop",
            model="m-sub",
            cost=0.25,
            tokens=400,
            stop_duration_ms=first_duration_ms,
        )
        # continued 60 s after the first stop: it starts again, runs 2 s by the timestamps and stops again
        await _start(engine, t0 + timedelta(seconds=66), session_id, "ag-own")
        await _hook(
            engine,
            t0 + timedelta(seconds=68),
            session_id,
            "agent.subagent.stop",
            None,
            tool_name=None,
            agent_id="ag-own",
            **({} if second_duration_ms is None else {"duration_ms": second_duration_ms}),
        )

        (row,) = await _dispatch_rows(engine, session_id)

        assert row["subagent_duration_ms"] == expected_ms
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_parallel_and_sequential_agents_are_not_cross_attributed_or_counted_twice():
    """B3: two parallel agents under one parent and a later sequential one each get their own request. Noise that
    shares a tool_use_id (another client's stop, another session's stop) names nothing, and the parent's own request
    lands on no row."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-parallel-sequential"
    other_session = "test-epmcdme-16939-live-parallel-sequential-other"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    agents = {  # tool_use_id -> (agent id, source, model, cost, tokens, start offset in seconds)
        "tu-a": ("ag-a", "stop", "model-a", 0.10, 100, 0),
        "tu-b": ("ag-b", "stop", "model-b", 0.20, 200, 0),
        "tu-c": ("ag-c", "field", "model-c", 0.40, 400, 60),
    }
    try:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        for tool_use_id, (agent_id, source, model, cost, tokens, offset) in agents.items():
            await _foreground_agent(
                engine,
                session_id,
                t0 + timedelta(seconds=offset),
                tool_use_id,
                agent_id,
                source=source,
                model=model,
                cost=cost,
                tokens=tokens,
            )
        await _hook(  # another client's stop and another session's stop on the key of a call
            engine, t0, session_id, "agent.subagent.stop", "tu-a", tool_name=None, agent_id="ag-codex", platform="codex"
        )
        await _hook(engine, t0, other_session, "agent.subagent.stop", "tu-b", tool_name=None, agent_id="ag-foreign")
        await _request(engine, t0 + timedelta(seconds=30), session_id, _PARENT_SPAN, "model-parent", 9.0, 9000)

        rows = await _dispatch_rows(engine, session_id)

        by_model = {row["models"][0]["model_name"]: row for row in rows}
        assert sorted(by_model) == ["model-a", "model-b", "model-c"]
        for model, (cost, tokens) in {"model-a": (0.10, 100), "model-b": (0.20, 200), "model-c": (0.40, 400)}.items():
            assert [m["model_name"] for m in by_model[model]["models"]] == [model]
            assert by_model[model]["cost_usd"] == pytest.approx(cost)
            assert by_model[model]["input_tokens"] == tokens
            assert by_model[model]["subagent_duration_ms"] == 5000
        assert sum(row["cost_usd"] for row in rows) == pytest.approx(0.70)
    finally:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        await engine.close()


@pytest.mark.asyncio
async def test_two_sessions_with_identically_keyed_dispatches_never_mix_rows_or_totals():
    """Every CTE of get_session_detail_dispatches filters on $sid, but no test proved that end to end for two
    parent sessions each with their own dispatches: here both reuse the same tool_use_id, agent_id, parent span
    bytes (_PARENT_SPAN, which every test in this file already reuses) and timestamps, so a missing session
    filter anywhere in the newer CTEs (idle, stopped, sibling_window, request_shares) would merge or double their
    rows. Querying one session must show only its own row and cost, never the other's identically-keyed one."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-two-sessions-a"
    other_session = "test-epmcdme-16939-live-two-sessions-b"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        for sid, model, cost, tokens in ((session_id, "model-a", 0.10, 100), (other_session, "model-b", 0.20, 200)):
            await _foreground_agent(
                engine, sid, t0, "tu-shared", "ag-shared", source="stop", model=model, cost=cost, tokens=tokens
            )

        (row_a,) = await _dispatch_rows(engine, session_id)
        (row_b,) = await _dispatch_rows(engine, other_session)

        assert [m["model_name"] for m in row_a["models"]] == ["model-a"]
        assert row_a["cost_usd"] == pytest.approx(0.10)
        assert row_a["input_tokens"] == 100
        assert [m["model_name"] for m in row_b["models"]] == ["model-b"]
        assert row_b["cost_usd"] == pytest.approx(0.20)
        assert row_b["input_tokens"] == 200
    finally:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "first_source", "second_source", "winner"),
    [
        # a prompt quotes an earlier agentId: the stop of the real call outranks the quoted text
        ("quote", "stop", "text", "first-call"),
        # a resumed agent: the second call's own field outranks the first call's stop
        ("resume", "stop", "field", "second-call"),
    ],
)
async def test_a_resumed_agent_and_a_quoted_agent_id_put_the_usage_on_one_row(
    case: str, first_source: str, second_source: str, winner: str
):
    """B3: two Agent calls name one agent id. The higher-ranked source keeps the usage; the other row shows no
    subagent data (the parent has no request here, so its fallback is empty) and the usage is counted once."""
    engine = await _get_engine()
    session_id = f"test-epmcdme-16939-live-shared-{case}"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _foreground_agent(
            engine,
            session_id,
            t0,
            "tu-first",
            "ag-shared",
            source=first_source,
            model="model-one",
            cost=0.30,
            tokens=300,
            subagent_type="first-call",
        )
        second_attrs = {"tool_response": _text("ag-shared") if second_source == "text" else _CUT}
        if second_source == "field":
            second_attrs["fork_agent_id"] = "ag-shared"
        # the second call carries the same agent id and runs nothing of its own
        await _call(engine, t0 + timedelta(minutes=5), session_id, "tu-second", **second_attrs)
        await _tool_span(engine, t0 + timedelta(minutes=5), session_id, "tu-second", "second-call")

        rows = {row["subagent_type"]: row for row in await _dispatch_rows(engine, session_id)}

        other = "second-call" if winner == "first-call" else "first-call"
        assert rows[winner]["cost_usd"] == pytest.approx(0.30)
        assert rows[other]["cost_usd"] is None and rows[other]["models"] == []
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


# ── The parent fallback ends with the call (B2, D2) ──


async def _execution_span(
    engine: AnalyticsPgEngine, ts: datetime, session_id: str, tool_use_id: str, tool_name: str = "Agent"
) -> None:
    """The tool execution span of a call: the evidence it ran when it left no end."""
    async with engine.acquire() as conn:
        await conn.execute(
            _SPAN_SQL,
            ts,
            1_000_000,
            b"exec-" + tool_use_id.encode(),
            _PARENT_SPAN,
            2,
            "claude_code.tool.execution",
            session_id,
            "",
            tool_name,
            tool_use_id,
            None,
        )


async def _unnamed_call(
    engine: AnalyticsPgEngine,
    session_id: str,
    ts: datetime,
    tool_use_id: str,
    subagent_type: str,
    *,
    closing: str | None = "agent.tool.end",
    run_seconds: int = 10,
    tool_name: str = "Agent",
    start_hook: bool = True,
) -> None:
    """A call that names no agent (a cut result, no stop), so its row falls back to the parent's requests. It ends
    (`closing`: agent.tool.end or agent.tool.error, None for a call that never ended) `run_seconds` after its start;
    an execution span stands in for the evidence it ran when it left no end. `start_hook` False loses the call's
    agent.tool.start (a hook that never arrived)."""
    await _tool_span(engine, ts, session_id, tool_use_id, subagent_type, tool_name=tool_name)
    await _execution_span(engine, ts, session_id, tool_use_id, tool_name)
    if start_hook:
        await _hook(engine, ts, session_id, "agent.tool.start", tool_use_id, tool_name=tool_name)
    if closing:
        attrs = {"tool_response": _CUT} if closing == "agent.tool.end" else {}
        await _hook(
            engine,
            ts + timedelta(seconds=run_seconds),
            session_id,
            closing,
            tool_use_id,
            tool_name=tool_name,
            **attrs,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("closing", ["agent.tool.end", "agent.tool.error"])
async def test_a_parent_request_logged_after_the_call_returned_is_not_on_its_row(closing: str):
    """B2 and B4: a call with no resolved id shows the parent's requests from its start to its end or error, never a
    later one."""
    engine = await _get_engine()
    session_id = f"test-epmcdme-16939-live-bound-{closing.rsplit('.', 1)[1]}"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _unnamed_call(engine, session_id, t0, "tu-1", "general-purpose", closing=closing)
        await _request(engine, t0 + timedelta(seconds=3), session_id, _PARENT_SPAN, "model-during", 1.0, 100)
        await _request(engine, t0 + timedelta(seconds=12), session_id, _PARENT_SPAN, "model-after", 9.0, 9000)

        (row,) = await _dispatch_rows(engine, session_id)

        assert row["cost_usd"] == pytest.approx(1.0)
        assert row["input_tokens"] == 100
        assert [m["model_name"] for m in row["models"]] == ["model-during"]
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_call_with_no_start_hook_takes_the_parent_requests_from_its_span_to_its_end():
    """B2: the call's agent.tool.start never arrived and no SubagentStart names an agent, so nothing but its span says
    when it began. Its window runs from the span to the call's end (it is not empty, as a window that starts at the
    end would be), and a request logged after the end is still the parent's."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-bound-no-start-hook"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _unnamed_call(engine, session_id, t0, "tu-1", "general-purpose", start_hook=False)
        await _request(engine, t0 + timedelta(seconds=3), session_id, _PARENT_SPAN, "model-during", 1.0, 100)
        await _request(engine, t0 + timedelta(seconds=12), session_id, _PARENT_SPAN, "model-after", 9.0, 9000)

        (row,) = await _dispatch_rows(engine, session_id)

        assert row["cost_usd"] == pytest.approx(1.0)
        assert row["input_tokens"] == 100
        assert [m["model_name"] for m in row["models"]] == ["model-during"]
        # the bar keeps the end as its last resort (the reader serves naive UTC timestamps)
        assert row["dispatch_start"].replace(tzinfo=timezone.utc) == t0 + timedelta(seconds=10)
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_stop_alone_is_no_agent_call_so_its_span_shows_an_empty_row():
    """An Agent call is known by its own Agent hook rows (start, end, error); a stop whose call left none names no
    call, in the Timeline as in the counts (`agent_call_ids_cte` and `agent_call_of` read the same rule). The span
    that call left shows no agent's data and, never ended, takes no parent requests."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-stop-without-call"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _tool_span(engine, t0, session_id, "tu-1", "general-purpose")
        await _execution_span(engine, t0, session_id, "tu-1")
        await _start(engine, t0 + timedelta(seconds=1), session_id, "ag-lonely")
        await _hook(
            engine,
            t0 + timedelta(seconds=6),
            session_id,
            "agent.subagent.stop",
            "tu-1",
            tool_name=None,
            agent_id="ag-lonely",
        )
        await _request(engine, t0 + timedelta(seconds=3), session_id, _PARENT_SPAN, "model-parent", 9.0, 9000)

        (row,) = await _dispatch_rows(engine, session_id)

        assert await _resolved(engine, session_id) == {}
        assert await _reverse(engine, session_id, "ag-lonely") is None
        assert row["cost_usd"] is None and row["models"] == []
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_siblings_tied_on_start_but_not_on_end_each_take_their_own_window():
    """Two calls start together and end at different times: a request inside only the shorter one's window splits
    three ways while all three are running, one past the shorter window's end splits only between the two still
    running, and the three never together claim more than the requests they are eligible for sum to (no cross-agent
    attribution or double counting, EPMCDME-16939 B3/round-2 CR: a sibling tied on start but not on end must not
    also take a full, separate share of a request an overlapping sibling already claimed)."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-bound-tied-ends"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _unnamed_call(engine, session_id, t0, "tu-short", "type-short", run_seconds=10)
        await _unnamed_call(engine, session_id, t0, "tu-long", "type-long", run_seconds=20)
        await _unnamed_call(engine, session_id, t0, "tu-long-twin", "type-twin", run_seconds=20)
        await _request(engine, t0 + timedelta(seconds=5), session_id, _PARENT_SPAN, "model-a", 1.0, 100)
        await _request(engine, t0 + timedelta(seconds=15), session_id, _PARENT_SPAN, "model-b", 2.0, 200)
        await _request(engine, t0 + timedelta(seconds=25), session_id, _PARENT_SPAN, "model-c", 4.0, 400)

        rows = {row["subagent_type"]: row for row in await _dispatch_rows(engine, session_id)}
        siblings = ("type-short", "type-long", "type-twin")

        # model-a (+5s) falls inside all three windows and splits three ways; model-b (+15s) falls inside only
        # the two still running and splits two ways; model-c (+25s), past every window, goes to none of them.
        assert rows["type-short"]["cost_usd"] == pytest.approx(1.0 / 3)
        for twin in ("type-long", "type-twin"):
            assert rows[twin]["cost_usd"] == pytest.approx(1.0 / 3 + 2.0 / 2)

        # conservation: the three siblings' shares, and their token shares, never sum to more than the two
        # eligible requests ($3.00, 300 tokens) - not the $4.00 three dispatches would show if a window tied on
        # start but not on end took a full, undivided share of a request an overlapping sibling already claimed
        assert sum(rows[s]["cost_usd"] for s in siblings) == pytest.approx(3.0)
        assert sum(rows[s]["input_tokens"] for s in siblings) == pytest.approx(300, abs=1)

        # the per-model split (models, carried through _model_sums' own request_share weight) must divide the
        # same way as the row's own cost_usd/input_tokens sums above: the aggregate columns are no proof of it,
        # since they come from parent_usage's own sums, not from _model_sums (EPMCDME-16939 round-3 CR-003).
        def model_tokens(row: dict[str, Any], model_name: str) -> float:
            return next(m["input_tokens"] for m in row["models"] if m["model_name"] == model_name)

        assert [m["model_name"] for m in rows["type-short"]["models"]] == ["model-a"]
        assert model_tokens(rows["type-short"], "model-a") == pytest.approx(100 / 3)
        for twin in ("type-long", "type-twin"):
            assert [m["model_name"] for m in rows[twin]["models"]] == ["model-a", "model-b"]
            assert model_tokens(rows[twin], "model-a") == pytest.approx(100 / 3)
            assert model_tokens(rows[twin], "model-b") == pytest.approx(200 / 2)
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_never_ended_call_with_no_id_is_bounded_to_nothing_not_open():
    """D2: with no end and no error, a Claude Code Agent call shows no parent requests, though it is the last
    sibling (a NULL bound must not read as an open window). The ended sibling before it keeps its own. This is
    also B4's interrupted call (the process killed mid-run leaves no agent.tool.end or .error at all), as
    distinct from the errored/cancelled shape the tests above this section cover."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-bound-never-ended"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _unnamed_call(engine, session_id, t0, "tu-ended", "type-ended", run_seconds=10)
        await _unnamed_call(engine, session_id, t0 + timedelta(seconds=30), "tu-open", "type-open", closing=None)
        await _request(engine, t0 + timedelta(seconds=3), session_id, _PARENT_SPAN, "model-a", 1.0, 100)
        await _request(engine, t0 + timedelta(seconds=40), session_id, _PARENT_SPAN, "model-b", 9.0, 9000)

        rows = {row["subagent_type"]: row for row in await _dispatch_rows(engine, session_id)}

        assert rows["type-ended"]["cost_usd"] == pytest.approx(1.0)
        assert rows["type-open"]["cost_usd"] is None and rows["type-open"]["models"] == []
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_task_call_keeps_the_open_parent_window():
    """D3: the older Task tool is outside the Claude Code Agent scope; its row keeps today's estimate, the parent's
    requests from its start on, whatever its end."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-bound-task"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _unnamed_call(engine, session_id, t0, "tu-task", "type-task", tool_name="Task")
        await _request(engine, t0 + timedelta(seconds=3), session_id, _PARENT_SPAN, "model-a", 1.0, 100)
        await _request(engine, t0 + timedelta(seconds=12), session_id, _PARENT_SPAN, "model-b", 2.0, 200)

        (row,) = await _dispatch_rows(engine, session_id)

        assert row["cost_usd"] == pytest.approx(3.0)
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


# ── takes_parent: which rows take the parent's requests (D2, D3, B3, B4, B5) ──


@pytest.mark.asyncio
async def test_a_call_that_lost_its_agent_id_to_another_call_shows_an_empty_row():
    """B3 (AR-12): a resumed agent is named by two calls; the higher-ranked source keeps it, and the other call, though
    it ended and the parent made a request while it ran, takes no parent requests (empty row, not an estimate)."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-takes-parent-loser"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _foreground_agent(
            engine,
            session_id,
            t0,
            "tu-first",
            "ag-shared",
            source="stop",
            model="model-one",
            cost=0.30,
            tokens=300,
            subagent_type="first-call",
        )
        # the resumed call names the same agent by the text only (a lower source) and runs nothing of its own
        second = t0 + timedelta(minutes=5)
        await _call(engine, second, session_id, "tu-second", tool_response=_text("ag-shared"))
        await _tool_span(engine, second, session_id, "tu-second", "second-call")
        await _request(engine, second + timedelta(seconds=3), session_id, _PARENT_SPAN, "model-parent", 9.0, 9000)

        rows = {row["subagent_type"]: row for row in await _dispatch_rows(engine, session_id)}

        assert rows["first-call"]["cost_usd"] == pytest.approx(0.30)
        assert rows["second-call"]["cost_usd"] is None
        assert rows["second-call"]["input_tokens"] is None
        assert rows["second-call"]["models"] == []
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["stop", "unnamed"])
async def test_an_errored_call_shows_its_own_data_when_a_stop_names_it_else_follows_the_fallback(source: str):
    """B4: a call that ended in agent.tool.error (no agent.tool.end) shows the data of the agent its stop names;
    with no stop it falls back to the parent's requests up to the error, never a later one. The CLI reports a
    cancelled call the same way, as agent.tool.error with no result text: one shape, one test. An interrupted
    call (the process killed mid-run) leaves no closing event at all; that is the never-ended shape the tests
    below this one cover."""
    engine = await _get_engine()
    session_id = f"test-epmcdme-16939-live-errored-{source}"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        if source == "stop":
            await _foreground_agent(
                engine,
                session_id,
                t0,
                "tu-err",
                "ag-own",
                source="stop",
                model="model-sub",
                cost=0.25,
                tokens=400,
                closing="agent.tool.error",
            )
        else:
            await _unnamed_call(engine, session_id, t0, "tu-err", "general-purpose", closing="agent.tool.error")
            await _request(engine, t0 + timedelta(seconds=3), session_id, _PARENT_SPAN, "model-sub", 0.25, 400)
        await _request(engine, t0 + timedelta(seconds=12), session_id, _PARENT_SPAN, "model-parent", 9.0, 9000)

        (row,) = await _dispatch_rows(engine, session_id)

        assert row["cost_usd"] == pytest.approx(0.25)
        assert (row["input_tokens"], row["output_tokens"]) == (400, 200)
        assert [m["model_name"] for m in row["models"]] == ["model-sub"]
        if source == "stop":
            assert row["subagent_duration_ms"] == 5000
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_never_ended_call_whose_stop_names_it_shows_its_own_data():
    """D2 narrows only a call with no end or error and no resolved id: when a stop names the never-ended call, its
    row shows that agent's data, not an empty row."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-takes-parent-open-stop"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _foreground_agent(
            engine,
            session_id,
            t0,
            "tu-open",
            "ag-own",
            source="stop",
            model="m-sub",
            cost=0.25,
            tokens=400,
            closing=None,
        )
        await _request(engine, t0 + timedelta(seconds=12), session_id, _PARENT_SPAN, "model-parent", 9.0, 9000)

        (row,) = await _dispatch_rows(engine, session_id)

        assert row["cost_usd"] == pytest.approx(0.25)
        assert [m["model_name"] for m in row["models"]] == ["m-sub"]
        assert row["subagent_duration_ms"] == 5000
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_never_ended_call_takes_no_part_in_splitting_a_tied_siblings_window():
    """D2: a never-ended Claude Code Agent call takes no parent requests, so it must not halve the share of a sibling
    that starts with it (here the older Task tool, which keeps the open window)."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-takes-parent-ties"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _unnamed_call(engine, session_id, t0, "tu-task", "type-task", tool_name="Task")
        await _unnamed_call(engine, session_id, t0, "tu-open-a", "type-open-a", closing=None)
        await _unnamed_call(engine, session_id, t0, "tu-open-b", "type-open-b", closing=None)
        await _request(engine, t0 + timedelta(seconds=3), session_id, _PARENT_SPAN, "model-a", 1.0, 100)

        rows = {row["subagent_type"]: row for row in await _dispatch_rows(engine, session_id)}

        assert rows["type-task"]["cost_usd"] == pytest.approx(1.0)
        for never_ended in ("type-open-a", "type-open-b"):
            assert rows[never_ended]["cost_usd"] is None and rows[never_ended]["models"] == []
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


_SKILL_SPAN_SQL = """
    INSERT INTO spans
        (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
         session_id, subagent_type, skill_name, tool_name, tool_use_id, attrs, success, status_code, resource_id)
    VALUES ($1, 6000000, 'trace'::bytea, $2, $3, 1, 'claude_code.tool', $4, '', 'my-skill', 'Skill', $5, NULL,
            'true', 0, 1)
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty_tool_use_id", "other_client", "skill"])
async def test_rows_outside_the_claude_code_agent_scope_keep_the_open_parent_window(case: str):
    """D3: a call without a tool_use_id, another client's call and a Skill row never ended and name no agent, yet keep
    today's estimate: the parent's requests from their start on, with no end to bound them."""
    engine = await _get_engine()
    session_id = f"test-epmcdme-16939-live-takes-parent-{case.replace('_', '-')}"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        if case == "empty_tool_use_id":
            await _unnamed_call(engine, session_id, t0, "", "type-out", closing=None)
        elif case == "other_client":
            await _tool_span(engine, t0, session_id, "tu-out", "type-out", span_name="codex.tool")
        else:
            async with engine.acquire() as conn:
                await conn.execute(_SKILL_SPAN_SQL, t0, b"skill-span", _PARENT_SPAN, session_id, "tu-out")
            await _hook(engine, t0, session_id, "agent.tool.end", "tu-out", tool_name="Skill", tool_response=_CUT)
        await _request(engine, t0 + timedelta(seconds=3), session_id, _PARENT_SPAN, "model-a", 1.0, 100)
        await _request(engine, t0 + timedelta(seconds=100), session_id, _PARENT_SPAN, "model-b", 2.0, 200)

        (row,) = await _dispatch_rows(engine, session_id)

        assert row["cost_usd"] == pytest.approx(3.0)
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


# --- Ingest marking: the days an Agent call's rows name, keyed on (session, tool_use_id) -------------------------


async def _start(engine: AnalyticsPgEngine, ts: datetime, session_id: str, agent_id: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute(_START_SQL, ts, session_id, agent_id, "")


async def _marked_days(engine: AnalyticsPgEngine, keys: list[tuple[str, str]]) -> set[tuple[date, str]]:
    """The (day, session) pairs the ingest marking statement queues for `keys`."""
    async with engine.acquire() as conn:
        rows = await conn.fetch(AGENT_CALL_DAYS_SQL, [s for s, _ in keys], [t for _, t in keys])
    return {(row["day"], row["session_id"]) for row in rows}


def _day(ts: datetime) -> date:
    return ts.astimezone(timezone.utc).date()


@pytest.mark.asyncio
async def test_an_agent_calls_rows_queue_the_day_of_every_start_they_carry_and_no_other():
    """Any row of the call carries an id: the end's field, the end's complete text, the stop. Every id's start gives
    its day, the stop-over-text loser included (its call no longer names it, so its day is recomputed). A start of
    another agent, the same tool_use_id in another session, another client's rows, a Skill end, an empty
    tool_use_id and a cut text are not marked."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-marking"
    other_session = "test-epmcdme-16939-live-marking-other"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)

    def day(n: int) -> datetime:  # the start of each agent lies on its own day
        return t0 - timedelta(days=n)

    try:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        for n, agent_id in enumerate(("ag-field", "ag-stop", "ag-text", "ag-loser", "ag-bystander", "ag-foreign"), 1):
            await _start(engine, day(n), session_id if agent_id != "ag-foreign" else other_session, agent_id)
        await _start(engine, day(7), session_id, "ag-cut")
        await _start(engine, day(8), session_id, "ag-skill")
        await _start(engine, day(9), session_id, "ag-codex")
        await _call(engine, t0, session_id, "tu-field", fork_agent_id="ag-field", tool_response=_CUT)
        await _call(engine, t0, session_id, "tu-stop", stops=("ag-stop",), tool_response=_CUT)
        await _call(engine, t0, session_id, "tu-text", tool_response=_text("ag-text"))
        # the stop beats the text: the text's agent is carried by the call all the same
        await _call(engine, t0, session_id, "tu-mixed", stops=("ag-stop",), tool_response=_text("ag-loser"))
        # none of these carries an id of this session's Agent calls
        await _call(engine, t0, session_id, "tu-cut", tool_response=f'{_CUT} "agentId":"ag-cut')
        await _call(engine, t0, session_id, "tu-skill", tool_name="Skill", tool_response=_text("ag-skill"))
        await _call(engine, t0, session_id, "tu-codex", fork_agent_id="ag-codex", platform="codex")
        await _call(engine, t0, other_session, "tu-field", stops=("ag-foreign",), tool_response=_CUT)
        await _call(engine, t0, session_id, "", fork_agent_id="ag-bystander")

        everything = [(session_id, tu) for tu in ("tu-field", "tu-stop", "tu-text", "tu-mixed", "tu-cut")]
        assert await _marked_days(engine, everything) == {(_day(day(n)), session_id) for n in (1, 2, 3, 4)}
        # one call at a time: each marks only what its own rows carry
        assert await _marked_days(engine, [(session_id, "tu-field")]) == {(_day(day(1)), session_id)}
        assert await _marked_days(engine, [(session_id, "tu-stop")]) == {(_day(day(2)), session_id)}
        assert await _marked_days(engine, [(session_id, "tu-text")]) == {(_day(day(3)), session_id)}
        assert await _marked_days(engine, [(session_id, "tu-mixed")]) == {
            (_day(day(2)), session_id),
            (_day(day(4)), session_id),
        }
        for none in (
            [(session_id, "tu-cut")],
            [(session_id, "tu-skill")],
            [(session_id, "tu-codex")],
            [(session_id, "")],
            [(session_id, "tu-missing")],
            [],
        ):
            assert await _marked_days(engine, none) == set()
        # the same tool_use_id in another session marks that session's own start only
        assert await _marked_days(engine, [(other_session, "tu-field")]) == {(_day(day(6)), other_session)}
    finally:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        await engine.close()


async def _ingest(engine: AnalyticsPgEngine, session_id: str, *events: dict[str, Any]) -> dict[date, int]:
    """Ingest one batch of hook events through the real ingestor; the rollup_dirty keys it queued for the session
    (day to kinds). The keys the session already had are dropped first."""
    async with engine.acquire() as conn:
        await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
    ingestor = PostgresTelemetryIngestor(engine, clock=lambda: datetime.now(timezone.utc).date())
    await ingestor.ingest_hook_events(list(events), "test@example.com", 0)
    async with engine.acquire() as conn:
        rows = await conn.fetch(
            "SELECT day, kinds FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id
        )
    return {row["day"]: row["kinds"] for row in rows}


def _event(event_type: str, session_id: str, ts: datetime, **fields: Any) -> dict[str, Any]:
    return {
        "type": event_type,
        "timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "session_id": session_id,
        "platform": "claude-code",
        **fields,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["stop_later", "end_later"])
async def test_a_stop_in_a_later_batch_or_the_reverse_queues_the_day_of_the_start_it_names(order: str):
    """D1 at ingest: the end of a call has a cut result and the stop carries the call's tool_use_id; whichever of the
    two arrives last queues the day of the agent's start (days before both) with SPAN_FACTS."""
    engine = await _get_engine()
    session_id = f"test-epmcdme-16939-live-ingest-{order.replace('_', '-')}-{uuid.uuid4().hex[:8]}"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    start_ts = t0 - timedelta(days=3)
    call = {"tool_name": "Agent", "tool_use_id": "tu-1"}
    start = _event("agent.subagent.start", session_id, start_ts, agent_id="ag-1", agent_type="general-purpose")
    end = _event("agent.tool.end", session_id, t0 + timedelta(seconds=10), tool_response=_CUT, **call)
    stop = _event("agent.subagent.stop", session_id, t0 + timedelta(seconds=6), agent_id="ag-1", tool_use_id="tu-1")
    first, last = (end, stop) if order == "stop_later" else (stop, end)
    try:
        await _cleanup(engine, session_id)
        await _ingest(engine, session_id, start)
        before_last = await _ingest(engine, session_id, first)

        queued = await _ingest(engine, session_id, last)

        if order == "stop_later":  # a cut end carries no id of its own: it names nothing until the stop comes
            assert not before_last.get(_day(start_ts), 0) & 4
        # the later one queues the start's day: the end through the stop stored before it, the stop by its own id
        assert queued[_day(start_ts)] & 4
    finally:
        await _cleanup(engine, session_id)
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_a_call_start_in_a_later_batch_than_its_stop_still_queues_the_day_the_stop_already_named():
    """The reverse of the case above, at ingest (EPMCDME-16939 round-3 CR-001): the call's own agent.tool.start
    carries no id and is no call by itself (see _call_sources), so it cannot be the thing a day's recompute reads
    to resolve the call - the stop or the end is. But a start arriving after its stop, in a later batch, out of
    order, is what turns that already-ingested stop from unknown (no Agent-tool row at all, so no call, so no
    agent) into resolved: without re-queuing on the start's own arrival, the agent's day would keep showing the
    unresolved state until some unrelated row dirtied it again. The start here lies on a different day than the
    subagent's own start (today, not three days before), so only the shared Agent-call rule, not some incidental
    same-day marking, can explain the subagent's day being queued by it."""
    engine = await _get_engine()
    session_id = f"test-epmcdme-16939-live-ingest-late-call-start-{uuid.uuid4().hex[:8]}"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    start_ts = t0 - timedelta(days=3)
    agent_start = _event("agent.subagent.start", session_id, start_ts, agent_id="ag-1", agent_type="general-purpose")
    stop = _event("agent.subagent.stop", session_id, t0 + timedelta(seconds=6), agent_id="ag-1", tool_use_id="tu-1")
    call_start = _event("agent.tool.start", session_id, t0, tool_name="Agent", tool_use_id="tu-1")
    try:
        await _cleanup(engine, session_id)
        await _ingest(engine, session_id, agent_start)
        await _ingest(engine, session_id, stop)

        queued = await _ingest(engine, session_id, call_start)

        assert queued[_day(start_ts)] & 4
    finally:
        await _cleanup(engine, session_id)
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM codemie_analytics.rollup_dirty WHERE session_id = $1", session_id)
        await engine.close()


_MANY_CALLS = 1500
_MANY_DAYS = 30


@pytest.mark.asyncio
async def test_the_marking_of_many_calls_runs_well_inside_the_ingest_statement_timeout():
    """A batch that brings the end of every call of a session with many Agent calls: the statement stays under
    1 s (a marking over the 10 s ingest statement timeout rolls the batch back)."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-marking-many"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    keys = [(session_id, f"tu-{n}") for n in range(_MANY_CALLS)]
    try:
        await _cleanup(engine, session_id)
        async with engine.acquire() as conn:
            await conn.executemany(
                _START_SQL,
                [(t0 - timedelta(days=n % _MANY_DAYS), session_id, f"ag-{n}", "") for n in range(_MANY_CALLS)],
            )
            for event, rows in (
                ("agent.tool.start", [(f"tu-{n}", None, None) for n in range(_MANY_CALLS)]),
                ("agent.subagent.stop", [(f"tu-{n}", f"ag-{n}", None) for n in range(0, _MANY_CALLS, 2)]),
                (
                    "agent.tool.end",
                    [(f"tu-{n}", None, _CUT if n % 2 == 0 else _text(f"ag-{n}")) for n in range(_MANY_CALLS)],
                ),
            ):
                await conn.executemany(
                    _HOOK_SQL,
                    [
                        (
                            t0 + timedelta(seconds=n),
                            session_id,
                            event,
                            tuid,
                            None if event == "agent.subagent.stop" else "Agent",
                            agent_id,
                            json.dumps({"tool_response": text}) if text else None,
                        )
                        for n, (tuid, agent_id, text) in enumerate(rows)
                    ],
                )
            await conn.execute("ANALYZE hook_events")
            async with conn.transaction():
                await conn.execute("SET LOCAL statement_timeout = 1000")
                started = time.perf_counter()
                rows = await conn.fetch(AGENT_CALL_DAYS_SQL, [s for s, _ in keys], [t for _, t in keys])
                elapsed = time.perf_counter() - started
        assert elapsed < 1.0
        # every call names its agent, half by a stop and half by a complete text: all the days come back
        assert len({row["day"] for row in rows}) == _MANY_DAYS
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


# --- Range marking (`rollups._MARK_RANGE`, the backfill and mark_dirty twin of the ingest marking) --------------


class _RolledBack(Exception):
    """Raised inside the transaction of `_range_marked` so what the range marking queued is not kept."""


async def _range_marked(engine: AnalyticsPgEngine, session_id: str, first: date, last: date) -> dict[date, int]:
    """The rollup_dirty keys (day to kinds) `_MARK_RANGE` queues for the session over the days first..last, in a
    transaction that is rolled back: the statement marks every session of those days."""
    queued: dict[date, int] = {}
    try:
        async with engine.acquire() as conn:
            async with conn.transaction():
                await conn.execute(rollups._MARK_RANGE, first, last, rollups._ALL_BITS, None)
                for row in await conn.fetch("SELECT day, kinds FROM rollup_dirty WHERE session_id = $1", session_id):
                    queued[row["day"]] = row["kinds"]
                raise _RolledBack
    except _RolledBack:
        pass
    return queued


@pytest.mark.asyncio
async def test_the_range_marking_queues_the_days_of_the_starts_the_ingest_marking_queues():
    """D1 for the backfill range: the calls whose rows lie on the range's days queue the day of every start they
    carry, by field, stop or text, a start on the day after the range included, as AGENT_CALL_DAYS_SQL does at
    ingest. A call with no row on the range, a Skill end's agent, and what the ingest marking leaves alone stay
    out."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-range"
    other_session = "test-epmcdme-16939-live-range-other"
    first = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=10)
    outside = first - timedelta(days=12)  # the rows of a call that is not on the range

    def start_day(n: int) -> datetime:  # the start of each agent lies on a day of its own, off the range's days
        return first - timedelta(days=n) if n > 0 else first + timedelta(days=-n)

    starts = {
        "ag-after": -1,  # named by a stop only, on the day after the range
        "ag-field": 3,
        "ag-text": 4,
        "ag-win": 5,
        "ag-loser": 6,
        "ag-lonely": 7,  # named by a stop with no end in sight
        "ag-split": 8,  # end on the range, its stop two days after it
        "ag-bystander": 9,
        "ag-cut": 10,
        "ag-codex": 11,
        "ag-foreign": 13,
        "ag-outside": 14,
        "ag-skill": 15,
    }
    try:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        for agent_id, n in starts.items():
            await _start(engine, start_day(n), other_session if agent_id == "ag-foreign" else session_id, agent_id)
        await _call(engine, first, session_id, "tu-after", stops=("ag-after",), tool_response=_CUT)
        await _call(engine, first, session_id, "tu-field", fork_agent_id="ag-field", tool_response=_CUT)
        await _call(engine, first, session_id, "tu-text", tool_response=_text("ag-text"))
        await _call(engine, first, session_id, "tu-mixed", stops=("ag-win",), tool_response=_text("ag-loser"))
        await _hook(engine, first, session_id, "agent.subagent.stop", "tu-lonely", tool_name=None, agent_id="ag-lonely")
        await _call(engine, first, session_id, "tu-split", tool_response=_CUT)
        await _hook(
            engine,
            first + timedelta(days=2),
            session_id,
            "agent.subagent.stop",
            "tu-split",
            tool_name=None,
            agent_id="ag-split",
        )
        # none of these carries an id of this session's Agent calls
        await _call(engine, first, session_id, "tu-cut", tool_response=f'{_CUT} "agentId":"ag-cut')
        await _call(engine, first, session_id, "tu-codex", fork_agent_id="ag-codex", platform="codex")
        await _call(engine, first, other_session, "tu-after", stops=("ag-foreign",), tool_response=_CUT)
        await _call(engine, first, session_id, "", fork_agent_id="ag-bystander")
        # a call off the range: marked at its own ingest, not by the range
        await _call(engine, outside, session_id, "tu-outside", fork_agent_id="ag-outside")
        # a Skill end keeps its own rule (FORK_DAYS_SQL at ingest)
        await _call(engine, first, session_id, "tu-skill", tool_name="Skill", tool_response=_text("ag-skill"))

        expected = {
            _day(start_day(starts[a])) for a in ("ag-after", "ag-field", "ag-text", "ag-win", "ag-loser", "ag-lonely")
        } | {_day(start_day(starts["ag-split"]))}
        on_range = [
            (session_id, tu)
            for tu in ("tu-after", "tu-field", "tu-text", "tu-mixed", "tu-lonely", "tu-split", "tu-cut", "tu-codex")
        ]
        at_ingest = {day for day, _ in await _marked_days(engine, on_range)}
        assert at_ingest == expected

        queued = await _range_marked(engine, session_id, _day(first), _day(first))

        # the days of the starts, with the span facts the ingest marking queues
        start_days = {_day(start_day(n)) for n in starts.values()}
        range_days = {day for day, kinds in queued.items() if kinds & 4 and day in start_days}
        assert range_days == expected | {_day(start_day(starts["ag-skill"]))}
        assert range_days - {_day(start_day(starts["ag-skill"]))} == at_ingest
    finally:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        await engine.close()


# --- The session-wide resolution (`agent_call_ids_cte`), the Timeline and the slash-command pairing read it -------


async def _resolved(engine: AnalyticsPgEngine, session_id: str) -> dict[str, tuple[str | None, str | None]]:
    sql = f"WITH {agent_call_ids_cte('$1::text')} SELECT tool_use_id, claimed_id, agent_id FROM call_ids"
    async with engine.acquire() as conn:
        return {row["tool_use_id"]: (row["claimed_id"], row["agent_id"]) for row in await conn.fetch(sql, session_id)}


async def _claim(engine: AnalyticsPgEngine, session_id: str, tool_use_id: str) -> str | None:
    async with engine.acquire() as conn:
        return await conn.fetchval(f"SELECT {agent_call_claim('$1::text', '$2::text')}", session_id, tool_use_id)


async def _candidates_from_resolution(engine: AnalyticsPgEngine, session_id: str) -> dict[str, bool]:
    sql = (
        f"WITH {agent_call_ids_cte('$1::text')} "
        f"SELECT f.agent_id, {dispatch_fork_candidate('f', 'call_ids')} AS candidate FROM hook_events f "
        "WHERE f.session_id = $1 AND f.event_type = 'agent.subagent.start'"
    )
    async with engine.acquire() as conn:
        return {row["agent_id"]: row["candidate"] for row in await conn.fetch(sql, session_id)}


@pytest.mark.asyncio
async def test_the_session_wide_resolution_gives_every_call_what_the_forward_reverse_and_claim_forms_give():
    """One pass over the session names, for each Agent call, the agent it resolved to (`claimed_id`) and the agent it
    won (`agent_id`), and the started agents the calls named: the per-call forms stay the oracle, case by case
    (field, stop, text, ambiguous stops, a cut result, a resumed agent, a tie, another client, another session)."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-resolution"
    other_session = "test-epmcdme-16939-live-resolution-other"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    agents = (
        "ag-stop", "ag-noise", "ag-field", "ag-stop-f", "ag-text-f", "ag-stop-s", "ag-text-s", "ag-textonly",
        "ag-amb1", "ag-amb2", "ag-text-a", "ag-empty", "ag-other", "ag-foreign", "ag-resumed", "ag-tie", "ag-twice",
        "ag-lonely", "ag-plain",
    )  # fmt: skip

    def at(minutes: int) -> datetime:
        return t0 + timedelta(minutes=minutes)

    try:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        await _call(engine, at(0), session_id, "tu-stop", stops=("ag-stop",), tool_response=_CUT)
        await _hook(
            engine, at(0), session_id, "agent.subagent.stop", "tu-stop", tool_name=None, agent_id="ag-noise",
            platform="codex",
        )  # fmt: skip
        await _call(
            engine, at(1), session_id, "tu-field", stops=("ag-stop-f",), fork_agent_id="ag-field",
            tool_response=_text("ag-text-f"),
        )  # fmt: skip
        await _call(engine, at(2), session_id, "tu-stopwins", stops=("ag-stop-s",), tool_response=_text("ag-text-s"))
        await _call(engine, at(3), session_id, "tu-text", tool_response=_text("ag-textonly"))
        await _call(
            engine, at(4), session_id, "tu-ambig", stops=("ag-amb1", "ag-amb2"), tool_response=_text("ag-text-a")
        )
        await _call(engine, at(5), session_id, "", stops=("ag-empty",), fork_agent_id="ag-empty")
        await _call(engine, at(6), session_id, "tu-other", fork_agent_id="ag-other", platform="codex")
        await _call(engine, at(7), session_id, "tu-cut", tool_response=_CUT)
        await _hook(
            engine, at(7), other_session, "agent.subagent.stop", "tu-cut", tool_name=None, agent_id="ag-foreign"
        )
        await _call(engine, at(8), session_id, "tu-quote", tool_response=_text("ag-resumed"))
        await _call(engine, at(9), session_id, "tu-resume", fork_agent_id="ag-resumed")
        await _call(engine, at(10), session_id, "tu-tie-b", tool_response=_text("ag-tie"))
        await _call(engine, at(11), session_id, "tu-tie-a", tool_response=_text("ag-tie"))
        await _call(engine, at(12), session_id, "tu-stop-b", stops=("ag-twice",))
        await _call(engine, at(13), session_id, "tu-stop-a", stops=("ag-twice",))
        # a stop with no Agent row is no call; a Skill call is no Agent call
        await _hook(
            engine, at(14), session_id, "agent.subagent.stop", "tu-lonely", tool_name=None, agent_id="ag-lonely"
        )
        await _hook(
            engine, at(15), session_id, "agent.tool.end", "tu-skill", tool_name="Skill", fork_agent_id="ag-plain"
        )
        async with engine.acquire() as conn:
            for offset, agent_id in enumerate(agents):
                await conn.execute(_START_SQL, t0 + timedelta(seconds=offset), session_id, agent_id, "p1")

        resolved = await _resolved(engine, session_id)
        assert resolved == {
            "tu-stop": ("ag-stop", "ag-stop"),
            "tu-field": ("ag-field", "ag-field"),
            "tu-stopwins": ("ag-stop-s", "ag-stop-s"),
            "tu-text": ("ag-textonly", "ag-textonly"),
            "tu-ambig": (None, None),
            "tu-cut": (None, None),
            "tu-other": (None, None),  # its start is Claude Code's, its end another client's: a call with no answer
            "tu-quote": ("ag-resumed", None),
            "tu-resume": ("ag-resumed", "ag-resumed"),
            "tu-tie-b": ("ag-tie", "ag-tie"),
            "tu-tie-a": ("ag-tie", None),
            "tu-stop-b": ("ag-twice", "ag-twice"),
            "tu-stop-a": ("ag-twice", None),
        }
        for tool_use_id in (*resolved, "", "tu-other", "tu-lonely", "tu-skill", "tu-missing"):
            claimed, won = resolved.get(tool_use_id, (None, None))
            assert await _forward(engine, session_id, tool_use_id) == won, tool_use_id
            assert await _claim(engine, session_id, tool_use_id) == claimed, tool_use_id
        for agent_id in agents:
            owners = [tool_use_id for tool_use_id, (_, won) in resolved.items() if won == agent_id]
            assert await _reverse(engine, session_id, agent_id) == (owners[0] if owners else None), agent_id
        # the fork candidates: an agent an Agent call names is no candidate, as with the reverse form
        by_resolution = await _candidates_from_resolution(engine, session_id)
        assert by_resolution == await _fork_candidates(engine, session_id)
        assert by_resolution["ag-field"] is False and by_resolution["ag-amb1"] is True
        assert by_resolution["ag-plain"] is False  # named by a Skill call, which keeps its own rule
    finally:
        await _cleanup(engine, session_id)
        await _cleanup(engine, other_session)
        await engine.close()


_BIG_CALLS = 150
_BIG_NOISE_ROWS = 6000


async def _big_session(engine: AnalyticsPgEngine, session_id: str, t0: datetime) -> None:
    """A long session: many foreground Agent calls (a span, a start, a stop carrying the call's tool_use_id and an
    end with a cut result each) among thousands of other hook rows, ANALYZEd."""
    end_attrs = json.dumps({"tool_response": _CUT})
    async with engine.acquire() as conn:
        await conn.executemany(
            _SPAN_SQL,
            [
                (
                    t0 + timedelta(seconds=n), 1_000_000, f"dispatch-{n}".encode(), _PARENT_SPAN, 1,
                    "claude_code.tool", session_id, "general-purpose", "Agent", f"tu-{n}", None,
                )
                for n in range(_BIG_CALLS)
            ],
        )  # fmt: skip
        rows: list[tuple[Any, ...]] = []
        for n in range(_BIG_CALLS):
            at = t0 + timedelta(seconds=n)
            ms = timedelta(milliseconds=1)
            rows += [
                (at, session_id, "agent.tool.start", f"tu-{n}", "Agent", None, None),
                (at + ms, session_id, "agent.subagent.start", None, None, f"ag-{n}", None),
                (at + 2 * ms, session_id, "agent.subagent.stop", f"tu-{n}", None, f"ag-{n}", None),
                (at + 3 * ms, session_id, "agent.tool.end", f"tu-{n}", "Agent", None, end_attrs),
            ]
        rows += [
            (t0 + timedelta(milliseconds=n), session_id, "agent.tool.start", f"noise-{n}", "Bash", None, None)
            for n in range(_BIG_NOISE_ROWS)
        ]
        await conn.executemany(_HOOK_SQL, rows)
        await conn.execute("ANALYZE hook_events")
        await conn.execute("ANALYZE spans")


@pytest.mark.asyncio
async def test_the_timeline_of_a_long_session_with_many_agent_calls_resolves_each_call_once():
    """The Timeline's cost is linear in the session: 150 foreground Agent calls among 6 000 other hook rows come back
    well inside the dashboard statement timeout (a per-call correlated lookup scanned the whole session for each
    call: seconds here, minutes on a session ten times larger)."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-timeline-many"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _big_session(engine, session_id, t0)
        started = time.perf_counter()
        rows = await _dispatch_rows(engine, session_id)
        elapsed = time.perf_counter() - started
        assert len(rows) == _BIG_CALLS
        assert elapsed < 2.0, f"{elapsed:.2f} s"
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


@pytest.mark.asyncio
async def test_the_range_marking_of_a_long_session_with_many_agent_calls_is_linear_in_the_session():
    """_MARK_RANGE reads the call rows and the starts of a long session once, not once per call: 150 calls among
    6 000 other rows queue their start days in well under the refresher's statement timeout. A guard against a
    blow-up of the plan, which depends on the data: the probe-per-call shape it replaced took 4 s on a session of
    11 000 rows and 200 calls, yet the planner picks a better plan on this test database, so the test does not tell
    the two shapes apart."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-range-many"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _big_session(engine, session_id, t0)
        started = time.perf_counter()
        queued = await _range_marked(engine, session_id, _day(t0), _day(t0))
        elapsed = time.perf_counter() - started
        assert queued and all(kinds & 4 for kinds in queued.values())
        assert elapsed < 2.0, f"{elapsed:.2f} s"
    finally:
        await _cleanup(engine, session_id)
        await engine.close()


_FALLBACK_SIBLINGS = 150
_FALLBACK_REQUESTS = 300


async def _fallback_heavy_session(engine: AnalyticsPgEngine, session_id: str, t0: datetime) -> None:
    """A long session of unnamed Agent calls (no resolvable agent id each, so every one falls back to its slice of
    the parent's requests) tied on the same start under one parent and ending at staggered times, so most of them
    overlap at once: the shape request_share (reader.py) must divide every API request among, to stress its
    per-row correlated sibling count (EPMCDME-16939 round-3 CR-002)."""
    cut = json.dumps({"tool_response": _CUT})
    async with engine.acquire() as conn:
        await conn.executemany(
            _SPAN_SQL,
            [
                (
                    t0, 6_000_000, f"dispatch-{n}".encode(), _PARENT_SPAN, 1, "claude_code.tool",
                    session_id, f"type-{n}", "Agent", f"tu-{n}", None,
                )
                for n in range(_FALLBACK_SIBLINGS)
            ]
            + [
                (
                    t0, 1_000_000, f"exec-{n}".encode(), _PARENT_SPAN, 2, "claude_code.tool.execution",
                    session_id, "", "Agent", f"tu-{n}", None,
                )
                for n in range(_FALLBACK_SIBLINGS)
            ],
        )  # fmt: skip
        await conn.executemany(
            _HOOK_SQL,
            [(t0, session_id, "agent.tool.start", f"tu-{n}", "Agent", None, None) for n in range(_FALLBACK_SIBLINGS)]
            + [
                (t0 + timedelta(seconds=10 * (n + 1)), session_id, "agent.tool.end", f"tu-{n}", "Agent", None, cut)
                for n in range(_FALLBACK_SIBLINGS)
            ],
        )
        await conn.executemany(
            _REQUEST_SQL,
            [
                (t0 + timedelta(milliseconds=10 * m + 1), session_id, _PARENT_SPAN, f"model-{m % 5}", 0.01, 100, 50)
                for m in range(_FALLBACK_REQUESTS)
            ],
        )
        await conn.execute("ANALYZE hook_events")
        await conn.execute("ANALYZE spans")
        await conn.execute("ANALYZE log_events")


@pytest.mark.asyncio
async def test_the_parent_fallback_split_of_many_overlapping_siblings_stays_inside_the_statement_timeout():
    """request_share is spliced into parent_usage and _model_sums eleven times per API request it might split. A
    fallback-heavy session (no agent ids resolved at all, so every call splits the parent's requests with its
    siblings) with 150 tied-start siblings splitting 300 requests at once - the shape this change makes more
    common - comes back well inside the dashboard statement timeout (EPMCDME-16939 round-3 CR-002)."""
    engine = await _get_engine()
    session_id = "test-epmcdme-16939-live-fallback-heavy"
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
    try:
        await _cleanup(engine, session_id)
        await _fallback_heavy_session(engine, session_id, t0)
        started = time.perf_counter()
        rows = await _dispatch_rows(engine, session_id)
        elapsed = time.perf_counter() - started
        assert len(rows) == _FALLBACK_SIBLINGS
        assert all(row["cost_usd"] is not None for row in rows)
        assert elapsed < 2.0, f"{elapsed:.2f} s"
    finally:
        await _cleanup(engine, session_id)
        await engine.close()
