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

"""Live-Postgres checks for EPMCDME-16457: Session Detail of a hook-only (GitHub Copilot CLI) session.

Hook rows are seeded the way the CLI sends them, the real rollups run, and the real reader is called.
Skipped (not failed) when the local analytics Postgres is unreachable.
"""

import json
import os
from datetime import date, datetime, time, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.otlp import MAX_KEY_BYTES
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.vocabulary import EventKind
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")

_COPILOT = "test-epmcdme-16457-copilot"
_CLAUDE = "test-epmcdme-16457-claude"
_MIXED = "test-epmcdme-16457-mixed"
_STUB = "test-epmcdme-16457-stub"
_SUB = "test-epmcdme-16457-sub"
_PARENT = "test-epmcdme-16457-parent"
_REAL = "test-epmcdme-16457-real"
_CLAUDE_CHILD = "test-epmcdme-16457-claude-child"
_COSTED_SUB = "test-epmcdme-16457-costed-sub"
_TWO_DAY = "test-epmcdme-16457-two-day"
_MIDNIGHT = "test-epmcdme-16457-midnight"
_ALL = (
    _COPILOT, _CLAUDE, _MIXED, _STUB, _SUB, _PARENT, _REAL, _CLAUDE_CHILD, _COSTED_SUB, _TWO_DAY, _MIDNIGHT,
)  # fmt: skip
_EMAIL = "copilot@example.com"

_HOOK_SQL = """
    INSERT INTO hook_events (ts, session_id, event_type, user_email, tool_name, tool_use_id, attrs)
    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
"""
_USAGE_SQL = """
    INSERT INTO usage_requests
        (ts, session_id, user_email, request_id, scope_kind, model_raw, model, input_tokens, output_tokens,
         cache_read_tokens, stop_reason, is_api_error)
    VALUES ($1, $2, $3, $4, $5, $6, $6, $7, $8, $9, $10, $11)
"""
_SPAN_SQL = """
    INSERT INTO spans
        (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
         session_id, subagent_type, skill_name, tool_name, tool_use_id, attrs, success, status_code, resource_id)
    VALUES ($1, 1000000, ''::bytea, $2::bytea, ''::bytea, $3, $4, $5, '', '', 'Read', $6, NULL, 'true', 0, $7)
"""
_RESOURCE_SQL = "INSERT INTO otel_resources (resource_id, service_name, attrs) VALUES ($1, $2, '{}'::jsonb)"
_SUBAGENT_START_SQL = (
    "INSERT INTO hook_events (ts, session_id, event_type, user_email, agent_id, attrs) "
    "VALUES ($1, $2, 'agent.subagent.start', $3, $4, $5::jsonb)"
)


async def _get_engine() -> AnalyticsPgEngine:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        await PostgresAnalyticsRuntime(engine, settings).start()
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


async def _cleanup(engine: AnalyticsPgEngine) -> None:
    async with engine.acquire() as conn:
        for table in (
            "hook_events",
            "usage_requests",
            "spans",
            "log_events",
            "cost_daily",
            "session_dims",
            "invocations_hourly",
            "tool_facts_daily",
            "session_usage",
            "session_usage_hourly",
            "turns_daily",
            "lines_daily",
            "rollup_dirty",
        ):
            await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", list(_ALL))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)


async def _hook(conn, t: datetime, session: str, event: str, tool: str | None, tool_use_id: str | None, platform: str):
    attrs = json.dumps({"platform": platform})
    await conn.execute(_HOOK_SQL, t, session, event, _EMAIL, tool, tool_use_id, attrs)


async def _seed_copilot(engine: AnalyticsPgEngine, session: str, t: datetime) -> None:
    """3 tool starts (view, bash, bash); view and one bash succeed, the other bash fails; 3 API requests,
    one of them re-sent with the same request_id and a higher input; one is a sub-agent request."""
    async with engine.acquire() as conn:
        for i, (tool, tu) in enumerate([("view", "tc-1"), ("bash", "tc-2"), ("bash", "tc-3")]):
            await _hook(conn, t + timedelta(seconds=i), session, "agent.tool.start", tool, tu, "copilot-cli")
        await _hook(conn, t + timedelta(seconds=10), session, "agent.tool.end", "view", "tc-1", "copilot-cli")
        await _hook(conn, t + timedelta(seconds=11), session, "agent.tool.end", "bash", "tc-2", "copilot-cli")
        await _hook(conn, t + timedelta(seconds=12), session, "agent.tool.error", "bash", "tc-3", "copilot-cli")
        rows = [
            (t + timedelta(seconds=20), "req-1", "main", 100, 10, 50),
            (t + timedelta(seconds=21), "req-1", "main", 150, 10, 50),  # re-sent with a higher input
            (t + timedelta(seconds=30), "req-2", "main", 200, 20, 0),
            (t + timedelta(seconds=40), "req-3", "agent", 300, 30, 0),
        ]
        for ts, rid, scope, inp, out, cache in rows:
            await conn.execute(
                _USAGE_SQL, ts, session, _EMAIL, rid, scope, "claude-sonnet-4.6", inp, out, cache, "end_turn", False
            )


async def _refresh(engine: AnalyticsPgEngine, day: date) -> None:
    await _refresh_days(engine, day, day)


async def _refresh_days(engine: AnalyticsPgEngine, first: date, last: date) -> None:
    refresher = RollupRefresher(engine, batch_size=50, max_run_seconds=60.0, raw_retention_days=30)
    await refresher.mark_dirty(first, last)
    await refresher.refresh()


@pytest.mark.asyncio
async def test_hook_only_session_detail_matches_the_session_list():
    engine = await _get_engine()
    t = _now()
    try:
        await _cleanup(engine)
        await _seed_copilot(engine, _COPILOT, t)
        await _refresh(engine, t.date())
        reader = PostgresCliAnalyticsReader(engine)

        tools = {
            r["tool_name"]: (r["call_count"], r["success_count"])
            for r in await reader.get_session_detail_tools(_COPILOT, scope=DETAIL_SCOPE)
        }
        assert tools == {"view": (1, 1), "bash": (2, 1)}

        flt = LocalAnalyticsFilter(start_dt=t - timedelta(days=1), end_dt=t + timedelta(days=1), users=[_EMAIL])
        listed_tools = {r["tool_name"]: (r["call_count"], r["success_count"]) for r in await reader.get_tool_usage(flt)}
        assert {k: listed_tools[k] for k in tools} == tools

        events = await reader.get_session_detail_events(_COPILOT, scope=DETAIL_SCOPE)
        by_type = {}
        for e in events:
            by_type.setdefault(e["event_type"], []).append(e)
        assert len(by_type["tool_call"]) == 3
        assert len(by_type["api_request"]) == 3  # req-1 appears once
        assert max(e["input_tokens"] for e in by_type["api_request"] if e["input_tokens"] < 200) == 150

        async with engine.acquire() as conn:
            api_calls = await conn.fetchval(
                "SELECT sum(api_call_count) FROM cost_daily WHERE session_id = $1", _COPILOT
            )
        assert len(by_type["api_request"]) == api_calls

        meta = await reader.get_session_detail_meta(_COPILOT, scope=DETAIL_SCOPE)
        assert meta[0]["harness_service_name"] == "copilot-cli"
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_api_error_rows_count_like_cost_daily():
    engine = await _get_engine()
    t = _now()
    try:
        await _cleanup(engine)
        await _seed_copilot(engine, _COPILOT, t)
        async with engine.acquire() as conn:
            await conn.execute(
                _USAGE_SQL, t + timedelta(seconds=50), _COPILOT, _EMAIL, "req-err", "main",
                "claude-sonnet-4.6", 0, 0, 0, None, True,
            )  # fmt: skip
        await _refresh(engine, t.date())
        reader = PostgresCliAnalyticsReader(engine)

        events = await reader.get_session_detail_events(_COPILOT, scope=DETAIL_SCOPE)
        async with engine.acquire() as conn:
            api_calls = await conn.fetchval(
                "SELECT sum(api_call_count) FROM cost_daily WHERE session_id = $1", _COPILOT
            )
        assert len([e for e in events if e["event_type"] == "api_request"]) == api_calls
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_timeline_model_name_follows_model_raw_like_cost_daily():
    """A request whose client-normalized `model` ('claude-sonnet-4-6', dashed) differs from the literal
    `model_raw` ('claude-sonnet-4.6', dotted): the timeline row and cost_daily (grouped, and priced, by
    model_raw) must report the same model name for it, or the handler prices the same request twice."""
    engine = await _get_engine()
    t = _now()
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO usage_requests
                    (ts, session_id, user_email, request_id, scope_kind, model_raw, model, input_tokens,
                     output_tokens, cache_read_tokens, stop_reason, is_api_error)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                t, _COPILOT, _EMAIL, "req-dotted", "main", "claude-sonnet-4.6", "claude-sonnet-4-6", 100, 10, 0,
                "end_turn", False,
            )  # fmt: skip
        await _refresh(engine, t.date())
        reader = PostgresCliAnalyticsReader(engine)

        events = await reader.get_session_detail_events(_COPILOT, scope=DETAIL_SCOPE)
        api_requests = [e for e in events if e["event_type"] == "api_request"]
        assert len(api_requests) == 1

        model_costs = await reader.get_session_detail_model_costs(_COPILOT, scope=DETAIL_SCOPE)
        assert {r["model_name"] for r in model_costs} == {"claude-sonnet-4.6"}
        assert api_requests[0]["model_name"] == "claude-sonnet-4.6"
    finally:
        await _cleanup(engine)
        await engine.close()


async def _seed_claude_with_spans(engine: AnalyticsPgEngine, session: str, t: datetime, platform_hook: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute(_RESOURCE_SQL, 16457, "claude-code")
        await conn.execute(_SPAN_SQL, t, b"s1", 1, "claude_code.tool", session, "tu-1", 16457)
        await conn.execute(_SPAN_SQL, t, b"s2", 2, "claude_code.tool.execution", session, "tu-1", 16457)
        await _hook(conn, t, session, "agent.tool.start", "Read", "tu-1", platform_hook)
        await _hook(conn, t + timedelta(seconds=1), session, "agent.tool.end", "Read", "tu-1", platform_hook)
        # A usage row keeps the session in cost_daily, hence in the detail.
        await conn.execute(
            _USAGE_SQL, t + timedelta(seconds=2), session, _EMAIL, "req-1", "main", "claude-sonnet-4-6", 10, 1, 0,
            "end_turn", False,
        )  # fmt: skip


@pytest.mark.asyncio
@pytest.mark.parametrize("session,platform", [(_CLAUDE, "claude-code"), (_MIXED, "copilot-cli")])
async def test_spans_win_over_hooks(session, platform):
    """Claude regression and mixed case: tools and timeline come from spans only, harness from OTel."""
    engine = await _get_engine()
    t = _now()
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM otel_resources WHERE resource_id = 16457")
        await _seed_claude_with_spans(engine, session, t, platform)
        await _refresh(engine, t.date())
        reader = PostgresCliAnalyticsReader(engine)

        tools = await reader.get_session_detail_tools(session, scope=DETAIL_SCOPE)
        assert [(r["tool_name"], r["call_count"]) for r in tools] == [("Read", 1)]
        events = await reader.get_session_detail_events(session, scope=DETAIL_SCOPE)
        assert len([e for e in events if e["event_type"] == "tool_call"]) == 1
        meta = await reader.get_session_detail_meta(session, scope=DETAIL_SCOPE)
        assert meta[0]["harness_service_name"] == "claude-code"
    finally:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM otel_resources WHERE resource_id = 16457")
        await engine.close()


async def _seed_span(conn, ts: datetime, session: str, span_id: bytes, kind: int, name: str, tool_use_id: str) -> None:
    await conn.execute(_SPAN_SQL, ts, span_id, kind, name, session, tool_use_id, 16457)


async def _prepare_span_session(engine: AnalyticsPgEngine) -> None:
    await _cleanup(engine)
    async with engine.acquire() as conn:
        await conn.execute("DELETE FROM otel_resources WHERE resource_id = 16457")
        await conn.execute(_RESOURCE_SQL, 16457, "copilot-cli")


async def _drop_span_resource(engine: AnalyticsPgEngine) -> None:
    await _cleanup(engine)
    async with engine.acquire() as conn:
        await conn.execute("DELETE FROM otel_resources WHERE resource_id = 16457")
    await engine.close()


@pytest.mark.asyncio
async def test_hook_fallback_is_decided_per_utc_day():
    """CR-001: spans on day 1, hooks only on day 2 -> day 2's hook calls still show in tools and timeline."""
    engine = await _get_engine()
    t = _now()
    day1 = t - timedelta(days=1)
    try:
        await _prepare_span_session(engine)
        async with engine.acquire() as conn:
            await _seed_span(conn, day1, _MIXED, b"s1", 1, "copilot.tool", "tu-1")
            await _hook(conn, t, _MIXED, "agent.tool.start", "view", "tc-1", "copilot-cli")
            await _hook(conn, t + timedelta(seconds=1), _MIXED, "agent.tool.start", "view", "tc-2", "copilot-cli")
        refresher = RollupRefresher(engine, batch_size=50, max_run_seconds=60.0, raw_retention_days=30)
        await refresher.mark_dirty(day1.date(), t.date())
        await refresher.refresh()
        reader = PostgresCliAnalyticsReader(engine)

        tools = {
            r["tool_name"]: r["call_count"] for r in await reader.get_session_detail_tools(_MIXED, scope=DETAIL_SCOPE)
        }
        assert tools == {"Read": 1, "view": 2}
        events = await reader.get_session_detail_events(_MIXED, scope=DETAIL_SCOPE)
        assert len([e for e in events if e["event_type"] == "tool_call"]) == 3
    finally:
        await _drop_span_resource(engine)


@pytest.mark.asyncio
async def test_timeline_usage_branch_is_decided_per_utc_day():
    """An OTel api_request on day 1 does not hide the plugin usage row of day 2 from the timeline."""
    engine = await _get_engine()
    day2 = datetime.combine(_now().date() - timedelta(days=1), time(12), tzinfo=timezone.utc)
    day1 = day2 - timedelta(days=1)
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO log_events (ts, session_id, event_kind, severity, resource_id) VALUES ($1, $2, $3, 9, 1)",
                day1, _COPILOT, int(EventKind.API_REQUEST),
            )  # fmt: skip
            await conn.execute(
                _USAGE_SQL, day2, _COPILOT, _EMAIL, "req-1", "main", "claude-sonnet-4.6", 100, 10, 0, "end_turn", False
            )

        events = await PostgresCliAnalyticsReader(engine).get_session_detail_events(_COPILOT, scope=DETAIL_SCOPE)
        requests = [e for e in events if e["event_type"] == "api_request"]
        assert [e["timestamp"].replace(tzinfo=None) for e in requests] == [
            day1.replace(tzinfo=None),
            day2.replace(tzinfo=None),
        ]
    finally:
        await _cleanup(engine)
        await engine.close()


_OTEL_REQUEST_SQL = """
    INSERT INTO log_events (ts, session_id, event_kind, severity, resource_id, request_id, model, input_tokens,
                            output_tokens)
    VALUES ($1, $2, $3, 9, 1, $4, 'claude-sonnet-4-6', 100, 10)
"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage_offsets",
    [
        pytest.param([-1], id="transcript-row-just-before-midnight"),
        pytest.param([-1, 1], id="re-sent-transcript-rows-straddle-midnight"),
    ],
)
async def test_timeline_lists_a_request_once_when_its_otel_record_is_across_midnight(usage_offsets):
    """A transcript request and its OTel record (same request id) either side of midnight are one request: one
    timeline api_request, as many as cost_daily counts, whatever the request's rows' own days."""
    engine = await _get_engine()
    midnight = datetime.combine(_now().date() - timedelta(days=1), time(0), tzinfo=timezone.utc)
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            for offset in usage_offsets:
                await conn.execute(
                    _USAGE_SQL, midnight + timedelta(seconds=offset), _COPILOT, _EMAIL, "req-1", "main",
                    "claude-sonnet-4.6", 100, 10, 0, "end_turn", False,
                )  # fmt: skip
            await conn.execute(
                _OTEL_REQUEST_SQL, midnight + timedelta(seconds=2), _COPILOT, int(EventKind.API_REQUEST), "req-1"
            )
        await _refresh_days(engine, midnight.date() - timedelta(days=1), midnight.date())

        events = await PostgresCliAnalyticsReader(engine).get_session_detail_events(_COPILOT, scope=DETAIL_SCOPE)
        async with engine.acquire() as conn:
            api_calls = await conn.fetchval(
                "SELECT sum(api_call_count) FROM cost_daily WHERE session_id = $1", _COPILOT
            )
        assert len([e for e in events if e["event_type"] == "api_request"]) == api_calls == 1
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_many_distinct_request_ids_count_as_many_api_calls():
    """AC3: 193 usage rows with distinct request ids are 193 calls in cost_daily and in the timeline."""
    engine = await _get_engine()
    t = _now()
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await conn.executemany(
                _USAGE_SQL,
                [
                    (t + timedelta(seconds=i), _COPILOT, _EMAIL, f"req-{i}", "main", "claude-sonnet-4.6", 10, 1, 0,
                     "end_turn", False)
                    for i in range(193)
                ],
            )  # fmt: skip
        await _refresh(engine, t.date())

        events = await PostgresCliAnalyticsReader(engine).get_session_detail_events(_COPILOT, scope=DETAIL_SCOPE)
        async with engine.acquire() as conn:
            api_calls = await conn.fetchval(
                "SELECT sum(api_call_count) FROM cost_daily WHERE session_id = $1", _COPILOT
            )
        assert api_calls == 193
        assert len([e for e in events if e["event_type"] == "api_request"]) == 193
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_timeline_hook_branch_is_off_for_a_day_with_any_tool_kind_span():
    """CR-002: an INTERACTION span alone switches the hook fallback off, like the rollup."""
    engine = await _get_engine()
    t = _now()
    try:
        await _prepare_span_session(engine)
        async with engine.acquire() as conn:
            await _seed_span(conn, t, _MIXED, b"s1", 3, "copilot.interaction", "")
            await _hook(conn, t, _MIXED, "agent.tool.start", "view", "tc-1", "copilot-cli")
        await _refresh(engine, t.date())
        reader = PostgresCliAnalyticsReader(engine)

        assert [
            r for r in await reader.get_session_detail_tools(_MIXED, scope=DETAIL_SCOPE) if r["tool_name"] == "view"
        ] == []
        events = await reader.get_session_detail_events(_MIXED, scope=DETAIL_SCOPE)
        assert [e for e in events if e["event_type"] == "tool_call" and e["tool_name"] == "view"] == []
    finally:
        await _drop_span_resource(engine)


@pytest.mark.asyncio
async def test_timeline_skips_an_over_long_hook_tool_name():
    """CR-003: a hook tool name over MAX_KEY_BYTES is not in the rollup, so not in the timeline either."""
    engine = await _get_engine()
    t = _now()
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await _hook(conn, t, _COPILOT, "agent.tool.start", "x" * (MAX_KEY_BYTES + 1), "tc-1", "copilot-cli")
            await _hook(conn, t + timedelta(seconds=1), _COPILOT, "agent.tool.start", "view", "tc-2", "copilot-cli")
        await _refresh(engine, t.date())

        events = await PostgresCliAnalyticsReader(engine).get_session_detail_events(_COPILOT, scope=DETAIL_SCOPE)
        assert [e["tool_name"] for e in events if e["event_type"] == "tool_call"] == ["view"]
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_hook_only_request_carries_cache_write_and_reasoning_and_matches_cost_daily():
    """AC4: the timeline row has the request's cache-write (5m + 1h) and thinking tokens; the cache write equals
    what cost_daily holds, so the row is priced like the session total."""
    engine = await _get_engine()
    t = _now()
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await _hook(conn, t, _COPILOT, "agent.tool.start", "view", "tc-1", "copilot-cli")
            await conn.execute(
                _USAGE_SQL, t + timedelta(seconds=5), _COPILOT, _EMAIL, "req-1", "main", "claude-sonnet-4.6", 100, 40,
                30, "end_turn", False,
            )  # fmt: skip
            await conn.execute(
                "UPDATE usage_requests SET cache_creation_5m_tokens = 7, cache_creation_1h_tokens = 5, "
                "thinking_tokens = 12 WHERE session_id = $1",
                _COPILOT,
            )
        await _refresh(engine, t.date())

        (request,) = [
            e
            for e in await PostgresCliAnalyticsReader(engine).get_session_detail_events(_COPILOT, scope=DETAIL_SCOPE)
            if e["event_type"] == "api_request"
        ]
        assert (request["cache_creation_tokens"], request["cache_creation_5m_tokens"]) == (12, 7)
        assert (request["cache_creation_1h_tokens"], request["reasoning_tokens"]) == (5, 12)
        async with engine.acquire() as conn:
            written = await conn.fetchval(
                "SELECT sum(cache_creation_tokens) FROM cost_daily WHERE session_id = $1", _COPILOT
            )
        assert written == request["cache_creation_tokens"]
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_listing_gate_drops_start_only_and_sub_agent_sessions():
    """Only a session with real activity that is not a sub-agent's own id is listed and counted."""
    engine = await _get_engine()
    t = _now()
    flt = LocalAnalyticsFilter(start_dt=t - timedelta(days=1), end_dt=t + timedelta(days=1), users=[_EMAIL])
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await _hook(conn, t, _STUB, "agent.session.start", None, None, "copilot-cli")
            await _hook(conn, t, _SUB, "agent.tool.start", "view", "tc-1", "copilot-cli")
            await _hook(conn, t, _REAL, "agent.tool.start", "view", "tc-2", "copilot-cli")
            await conn.execute(_SUBAGENT_START_SQL, t, _PARENT, _EMAIL, _SUB, json.dumps({"platform": "copilot-cli"}))
        await _refresh(engine, t.date())
        reader = PostgresCliAnalyticsReader(engine)

        listed = {r["session_id"] for r in await reader.get_session_cost_facts(flt)}
        assert listed & {_STUB, _SUB, _PARENT, _REAL} == {_REAL}
        (kpi,) = await reader.get_cost_kpis(flt)
        assert kpi["total_sessions"] == len(listed)
        (user,) = await reader.get_users(flt)
        assert user["session_count"] == len(listed)
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_resent_tool_start_counts_like_the_rollup():
    """The rollup counts every agent.tool.start; a re-sent start of one tool_use_id is a call in Detail tools
    and a timeline row alike."""
    engine = await _get_engine()
    t = _now()
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            for i in range(2):
                await _hook(conn, t + timedelta(seconds=i), _COPILOT, "agent.tool.start", "view", "tc-1", "copilot-cli")
        await _refresh(engine, t.date())
        reader = PostgresCliAnalyticsReader(engine)

        tools = {
            r["tool_name"]: r["call_count"] for r in await reader.get_session_detail_tools(_COPILOT, scope=DETAIL_SCOPE)
        }
        events = await reader.get_session_detail_events(_COPILOT, scope=DETAIL_SCOPE)
        assert tools == {"view": 2}
        assert len([e for e in events if e["event_type"] == "tool_call"]) == 2
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_request_identity_falls_back_to_message_id_then_to_the_row():
    """Rows with no request_id share a message_id -> one request; rows with neither id are one request each."""
    engine = await _get_engine()
    t = _now()
    sql = (
        "INSERT INTO usage_requests (ts, session_id, user_email, request_id, message_id, scope_kind, model_raw, "
        "model, input_tokens, output_tokens, cache_read_tokens, stop_reason, is_api_error) "
        "VALUES ($1, $2, $3, $4, $5, 'main', 'claude-sonnet-4.6', 'claude-sonnet-4.6', 10, 1, 0, 'end_turn', false)"
    )
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await conn.execute(sql, t, _COPILOT, _EMAIL, "", "msg-1")
            await conn.execute(sql, t + timedelta(seconds=1), _COPILOT, _EMAIL, "", "msg-1")
            await conn.execute(sql, t + timedelta(seconds=2), _COPILOT, _EMAIL, "", "")
            await conn.execute(sql, t + timedelta(seconds=3), _COPILOT, _EMAIL, "", "")
        await _refresh(engine, t.date())

        events = await PostgresCliAnalyticsReader(engine).get_session_detail_events(_COPILOT, scope=DETAIL_SCOPE)
        async with engine.acquire() as conn:
            api_calls = await conn.fetchval(
                "SELECT sum(api_call_count) FROM cost_daily WHERE session_id = $1", _COPILOT
            )
        assert len([e for e in events if e["event_type"] == "api_request"]) == api_calls == 3
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_late_parent_start_flags_the_already_rolled_up_child_as_a_sub_agent():
    """The parent's agent.subagent.start arrives after the child was rolled up and listed: the next refresh
    sets is_subagent on the child and drops it from the listing."""
    engine = await _get_engine()
    t = _now()
    flt = LocalAnalyticsFilter(start_dt=t - timedelta(days=1), end_dt=t + timedelta(days=1), users=[_EMAIL])
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await _hook(conn, t, _SUB, "agent.tool.start", "view", "tc-1", "copilot-cli")
        await _refresh(engine, t.date())
        reader = PostgresCliAnalyticsReader(engine)
        assert _SUB in {r["session_id"] for r in await reader.get_session_cost_facts(flt)}

        async with engine.acquire() as conn:
            await conn.execute(_SUBAGENT_START_SQL, t, _PARENT, _EMAIL, _SUB, json.dumps({"platform": "copilot-cli"}))
        await _refresh(engine, t.date())

        assert _SUB not in {r["session_id"] for r in await reader.get_session_cost_facts(flt)}
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_aggregates_keep_claude_session_results_and_add_the_session_without_usage():
    """EPMCDME-16496 regression: a session with usage rows reports exactly its cost_daily sums; a session with
    dimensions but no usage row is counted and listed with empty cost facts."""
    engine = await _get_engine()
    t = _now()
    flt = LocalAnalyticsFilter(start_dt=t - timedelta(days=1), end_dt=t + timedelta(days=1), users=[_EMAIL])
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM otel_resources WHERE resource_id = 16457")
        await _seed_claude_with_spans(engine, _CLAUDE, t, "claude-code")
        async with engine.acquire() as conn:
            await _hook(conn, t, _MIXED, "agent.tool.start", "view", "tc-1", "copilot-cli")
        await _refresh(engine, t.date())
        reader = PostgresCliAnalyticsReader(engine)

        facts = {r["session_id"]: r for r in await reader.get_session_cost_facts(flt)}
        assert _CLAUDE in facts
        assert (facts[_CLAUDE]["input_tokens"], facts[_CLAUDE]["api_call_count"]) == (10, 1)
        assert _MIXED in facts
        assert facts[_MIXED]["api_call_count"] is None
        (kpi,) = await reader.get_cost_kpis(flt)
        assert kpi["total_input_tokens"] == 10 and kpi["total_api_calls"] == 1
        assert kpi["total_sessions"] == len(facts)
        (user,) = await reader.get_users(flt)
        assert user["top_model"] == "claude-sonnet-4-6" and user["session_count"] == len(facts)
        (by_user,) = await reader.get_cost_by_user(flt)
        assert {_CLAUDE, _MIXED} <= set(by_user["session_ids"])
        days = {r["day"] for r in await reader.get_users_daily_activity(flt)}
        assert days == {t.date()}  # the usage-less session's day is its start date, not NULL
        assert len(await reader.get_session_detail_meta(_MIXED, scope=DETAIL_SCOPE)) == 1
    finally:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM otel_resources WHERE resource_id = 16457")
        await engine.close()


@pytest.mark.asyncio
async def test_model_breakdown_merges_dotted_and_dashed_claude_names():
    """AC5: Copilot sends `claude-sonnet-4.6`, other clients `claude-sonnet-4-6`: one row in the by-model view."""
    engine = await _get_engine()
    t = _now()
    flt = LocalAnalyticsFilter(start_dt=t - timedelta(days=1), end_dt=t + timedelta(days=1), users=[_EMAIL])
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            for i, model in enumerate(("claude-sonnet-4.6", "claude-sonnet-4-6")):
                await conn.execute(
                    _USAGE_SQL, t + timedelta(seconds=i), _COPILOT, _EMAIL, f"req-{i}", "main", model, 100, 10, 0,
                    "end_turn", False,
                )  # fmt: skip
        await _refresh(engine, t.date())

        rows = await PostgresCliAnalyticsReader(engine).get_model_breakdown(flt)
        assert [(r["model_name"], r["session_count"]) for r in rows] == [("claude-sonnet-4-6", 1)]
    finally:
        await _cleanup(engine)
        await engine.close()


def _window(t: datetime) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(start_dt=t - timedelta(days=1), end_dt=t + timedelta(days=1), users=[_EMAIL])


@pytest.mark.asyncio
async def test_a_claude_code_subagent_start_does_not_hide_the_session_it_names():
    """Claude Code sends the sub-agent's own short id under the parent's session: that id is no session, so a
    session that happens to carry the same id stays listed."""
    engine = await _get_engine()
    t = _now()
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await _hook(conn, t, _CLAUDE_CHILD, "agent.tool.start", "view", "tc-1", "claude-code")
            await conn.execute(
                _SUBAGENT_START_SQL, t, _PARENT, _EMAIL, _CLAUDE_CHILD, json.dumps({"platform": "claude-code"})
            )
        await _refresh(engine, t.date())

        listed = {r["session_id"] for r in await PostgresCliAnalyticsReader(engine).get_session_cost_facts(_window(t))}
        assert _CLAUDE_CHILD in listed
        async with engine.acquire() as conn:
            flag = await conn.fetchval("SELECT is_subagent FROM session_dims WHERE session_id = $1", _CLAUDE_CHILD)
        assert not flag
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_subagent_with_cost_rows_of_its_own_stays_listed():
    engine = await _get_engine()
    t = _now()
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await _hook(conn, t, _COSTED_SUB, "agent.tool.start", "view", "tc-1", "copilot-cli")
            await conn.execute(
                _USAGE_SQL, t + timedelta(seconds=2), _COSTED_SUB, _EMAIL, "req-1", "main", "claude-sonnet-4.6", 10, 1,
                0, "end_turn", False,
            )  # fmt: skip
            await conn.execute(
                _SUBAGENT_START_SQL, t, _PARENT, _EMAIL, _COSTED_SUB, json.dumps({"platform": "copilot-cli"})
            )
        await _refresh(engine, t.date())

        listed = {r["session_id"] for r in await PostgresCliAnalyticsReader(engine).get_session_cost_facts(_window(t))}
        assert _COSTED_SUB in listed
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_hidden_subagent_is_dropped_from_every_session_aggregate_and_still_opens_by_id():
    """What the flag does to a hidden session's activity: it leaves the session-level aggregates (it is not counted
    under its parent), and Session Detail by its id still opens."""
    engine = await _get_engine()
    t = _now()
    flt = _window(t)
    try:
        await _cleanup(engine)
        async with engine.acquire() as conn:
            await _hook(conn, t, _SUB, "agent.tool.start", "view", "tc-1", "copilot-cli")
            await _hook(conn, t, _REAL, "agent.tool.start", "view", "tc-2", "copilot-cli")
            await conn.execute(_SUBAGENT_START_SQL, t, _REAL, _EMAIL, _SUB, json.dumps({"platform": "copilot-cli"}))
        await _refresh(engine, t.date())
        reader = PostgresCliAnalyticsReader(engine)

        listed = {r["session_id"] for r in await reader.get_session_cost_facts(flt)}
        assert _SUB not in listed and _REAL in listed
        (kpi,) = await reader.get_cost_kpis(flt)
        (user,) = await reader.get_users(flt)
        (by_user,) = await reader.get_cost_by_user(flt)
        assert kpi["total_sessions"] == user["session_count"] == len(listed)
        assert _SUB not in set(by_user["session_ids"]) and _SUB not in set(user["session_ids"])
        assert len(await reader.get_session_detail_meta(_SUB, scope=DETAIL_SCOPE)) == 1
    finally:
        await _cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_mixed_two_day_session_lists_what_its_totals_count():
    """Spans and an OTel request on day 1, hooks and a plugin request on day 2: the timeline's tool rows are the
    Detail tools table, its requests are cost_daily's calls, and their tokens add up to cost_daily's."""
    engine = await _get_engine()
    t = _now()
    day1 = t - timedelta(days=1)
    try:
        await _prepare_span_session(engine)
        async with engine.acquire() as conn:
            await _seed_span(conn, day1, _TWO_DAY, b"s1", 1, "copilot.tool", "tu-1")
            await _hook(conn, day1 + timedelta(seconds=1), _TWO_DAY, "agent.tool.start", "Read", "tu-1", "copilot-cli")
            await conn.execute(_OTEL_REQUEST_SQL, day1, _TWO_DAY, int(EventKind.API_REQUEST), "req-otel")
            await _hook(conn, t, _TWO_DAY, "agent.tool.start", "view", "tc-1", "copilot-cli")
            await _hook(conn, t + timedelta(seconds=1), _TWO_DAY, "agent.tool.start", "view", "tc-2", "copilot-cli")
            await conn.execute(
                _USAGE_SQL, t + timedelta(seconds=5), _TWO_DAY, _EMAIL, "req-plugin", "main", "claude-sonnet-4.6", 200,
                20, 0, "end_turn", False,
            )  # fmt: skip
        await _refresh_days(engine, day1.date(), t.date())
        reader = PostgresCliAnalyticsReader(engine)

        detail_tools = await reader.get_session_detail_tools(_TWO_DAY, scope=DETAIL_SCOPE)
        tools = {r["tool_name"]: r["call_count"] for r in detail_tools}
        events = await reader.get_session_detail_events(_TWO_DAY, scope=DETAIL_SCOPE)
        timeline_tools: dict[str, int] = {}
        for e in events:
            if e["event_type"] == "tool_call":
                timeline_tools[e["tool_name"]] = timeline_tools.get(e["tool_name"], 0) + 1
        assert tools == timeline_tools == {"Read": 1, "view": 2}

        requests = [e for e in events if e["event_type"] == "api_request"]
        async with engine.acquire() as conn:
            calls, input_tokens = await conn.fetchrow(
                "SELECT sum(api_call_count), sum(input_tokens) FROM cost_daily WHERE session_id = $1", _TWO_DAY
            )
        assert len(requests) == calls == 2
        assert sum(r["input_tokens"] for r in requests) == input_tokens == 300
    finally:
        await _drop_span_resource(engine)


@pytest.mark.asyncio
async def test_a_tool_call_across_midnight_is_listed_as_often_as_the_tools_table_counts_it():
    """A hook on a day without spans and a span on the next day: the rollup counts both (its fallback is per UTC
    day, unwidened), and so does the timeline, so the two views agree."""
    engine = await _get_engine()
    midnight = datetime.combine(_now().date() - timedelta(days=1), time(0), tzinfo=timezone.utc)
    try:
        await _prepare_span_session(engine)
        async with engine.acquire() as conn:
            await _hook(
                conn, midnight - timedelta(seconds=2), _MIDNIGHT, "agent.tool.start", "view", "tc-1", "copilot-cli"
            )
            await _seed_span(conn, midnight + timedelta(seconds=2), _MIDNIGHT, b"s1", 1, "copilot.tool", "tu-1")
        await _refresh_days(engine, midnight.date() - timedelta(days=1), midnight.date())
        reader = PostgresCliAnalyticsReader(engine)

        detail_tools = await reader.get_session_detail_tools(_MIDNIGHT, scope=DETAIL_SCOPE)
        events = await reader.get_session_detail_events(_MIDNIGHT, scope=DETAIL_SCOPE)
        timeline = sorted(e["tool_name"] for e in events if e["event_type"] == "tool_call")
        assert {r["tool_name"]: r["call_count"] for r in detail_tools} == {"Read": 1, "view": 1}
        assert timeline == ["Read", "view"]
    finally:
        await _drop_span_resource(engine)


@pytest.mark.asyncio
async def test_the_sql_model_normalization_equals_the_python_pricer_on_the_same_inputs():
    """The two twins run on the same inputs, the SQL one in Postgres: a divergence would key cost rows by one name
    and price them by another."""
    from codemie.repository.cli_analytics.postgres.cost_key_sql import normalized_model_name
    from codemie.service.analytics.handlers.coding_agent_pricing import normalize_model

    engine = await _get_engine()
    inputs = [
        "claude-sonnet-4.6",
        "Claude-Sonnet-4.6",
        "claude-3.5-sonnet",
        "claude-opus-4.6.1",
        "claude-router.v1",
        "anthropic.claude-sonnet-4.6",
        "converse/us.anthropic.claude-sonnet-4.6-20251001-v1:0",
        "bedrock/eu.anthropic.claude-opus-4-6-v1",
        "vertex_ai/claude-haiku-4-5@20251001",
        "gpt-5.3-codex",
        "gpt-4.1",
        "claude-sonnet-4-6",
        "",
    ]
    sql = f"SELECT {normalized_model_name('$1::text')}"
    try:
        async with engine.acquire() as conn:
            for raw in inputs:
                assert await conn.fetchval(sql, raw) == normalize_model(raw), raw
    finally:
        await engine.close()
