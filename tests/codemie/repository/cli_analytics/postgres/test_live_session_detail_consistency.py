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

"""Live-Postgres checks for EPMCDME-16609 and EPMCDME-16741: the Session Detail of a hooks-only session reconciles
with the same session's cost and tool aggregates.

Raw rows (usage_requests, hook_events, log_events, spans) are inserted and rolled up by the real refresher; the
expected money and counts are computed here from the inserted tokens and catalog rates, not from the reader.
Skipped when `TEST_LOCAL_PG_URL` is unset or unreachable.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.vocabulary import SpanKind
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE, EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_SCHEMA_LOCK = 0x6D6F6E6579
_DAY = date(2026, 11, 2)
_START = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
_MODEL = "claude-sonnet-5-5"
_ALIAS = "bedrock/global.anthropic.claude-sonnet-5-5"
# input, output, cache read, cache write 5m, cache write 1h per token
_RATES = (3e-6, 1.5e-5, 3e-7, 3.75e-6, 6e-6)
# a second priced model with different rates, so a wrongly merged or mis-keyed aggregate changes the sum
_MODEL2 = "claude-haiku-5-5"
_RATES2 = (1e-6, 5e-6, 1e-7, 1.25e-6, 2e-6)
_PRICING = "codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config"
_TABLES = (
    "hook_events",
    "log_events",
    "spans",
    "usage_requests",
    "cost_daily",
    "session_dims",
    "rollup_dirty",
    "session_usage",
    "invocations_hourly",
    "tool_facts_daily",
)

_USAGE = """INSERT INTO usage_requests
    (ts, session_id, request_id, model_raw, model, input_tokens, output_tokens, cache_read_tokens, stop_reason,
     cache_creation_5m_tokens, cache_creation_1h_tokens)
    VALUES ($1, $2, $3, $4, $4, $5, $6, $7, $8, $9, $10)"""
_API_REQUEST = """INSERT INTO log_events
    (ts, session_id, event_kind, request_id, model, cost_usd, input_tokens, output_tokens, cache_read_tokens,
     cache_creation_tokens, severity, resource_id, attrs)
    VALUES ($1, $2, 1, $3, $4, 0.5, $5, $6, $7, $8, 9, 1, $9::jsonb)"""
_HOOK = "INSERT INTO hook_events (ts, session_id, event_type, tool_use_id, tool_name) VALUES ($1, $2, $3, $4, $5)"
_SPAN = """INSERT INTO spans
    (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name, session_id, subagent_type, tool_name,
     tool_use_id, attrs, success, status_code, resource_id)
    VALUES ($1, 1000000, 'trace'::bytea, $2, $3, $4, $5, $6, '', $7, $8, '{}'::jsonb, $9, 0, 1)"""


def _price(inp: int, out: int, read: int, w5m: int = 0, w1h: int = 0, unsplit: int = 0, rates: tuple = _RATES) -> float:
    return inp * rates[0] + out * rates[1] + read * rates[2] + (w5m + unsplit) * rates[3] + w1h * rates[4]


def _lookup(model: str) -> CostConfig | None:
    if model == _MODEL2:
        rates = _RATES2
    elif model == _MODEL:
        rates = _RATES
    else:
        return None
    return CostConfig(
        input=rates[0],
        output=rates[1],
        cache_read_input_token_cost=rates[2],
        cache_creation_input_token_cost=rates[3],
        cache_creation_input_token_cost_above_1hr=rates[4],
    )


async def _engine() -> AnalyticsPgEngine:
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_URL=_LOCAL_PG_URL))
    engine = AnalyticsPgEngine(settings)
    try:
        lock = await asyncpg.connect(settings.dsn)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    try:
        await lock.execute("SELECT pg_advisory_lock($1)", _SCHEMA_LOCK)
        await PostgresAnalyticsRuntime(engine, settings).start()
    finally:
        await lock.close()
    return engine


async def _cleanup(engine: AnalyticsPgEngine, session_ids: list[str]) -> None:
    async with engine.acquire() as conn:
        for table in _TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", session_ids)  # noqa: S608


async def _refresh(engine: AnalyticsPgEngine) -> None:
    async with live_rollup_lock(engine):
        refresher = RollupRefresher(engine, batch_size=100)
        async with engine.acquire() as conn:
            await refresher.mark_dirty(_DAY, _DAY, conn)
        await refresher.refresh()


def _at(seconds: int) -> datetime:
    return _START + timedelta(seconds=seconds)


async def _detail(engine: AnalyticsPgEngine, sid: str) -> dict:
    with patch(_PRICING, _lookup):
        handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))
        detail, _, _ = await handler.get_session_detail(sid, scope=DETAIL_SCOPE)
    assert detail is not None
    return detail


def _requests(detail: dict) -> list[dict]:
    return [e for e in detail["events"] if e["event_type"] == "api_request"]


# (model, input, output, cache read, cache write 5m, cache write 1h) of a subagent known only by its totals
_TOTALS_A = [(_MODEL, 1_000, 200, 300, 40, 10), (_MODEL2, 400, 80, 60, 0, 25)]
_TOTALS_B = [(_MODEL2, 7, 3, 2, 6, 0)]
_TOTALS_A_COST = _price(1_000, 200, 300, w5m=40, w1h=10) + _price(400, 80, 60, w1h=25, rates=_RATES2)
_TOTALS_B_COST = _price(7, 3, 2, w5m=6, rates=_RATES2)


async def _totals_hook(conn: asyncpg.Connection, sid: str, agent: str, seconds: int, usage: list[tuple]) -> None:
    items = [
        {
            "model": m,
            "input_tokens": i,
            "output_tokens": o,
            "cache_read_tokens": r,
            "cache_creation_5m_tokens": w5,
            "cache_creation_1h_tokens": w1,
        }
        for m, i, o, r, w5, w1 in usage
    ]
    await conn.execute(
        "INSERT INTO hook_events (ts, session_id, event_type, agent_id, attrs)"
        " VALUES ($1, $2, 'agent.subagent.usage', $3, $4::jsonb)",
        _at(seconds),
        sid,
        agent,
        json.dumps({"usage": items}),
    )


def _assert_totals_events(detail: dict, ts: datetime, usage: list[tuple]) -> None:
    """Every model of the subagent is one aggregate event: its own tokens, its own price, no request fields."""
    found = [e for e in detail["events"] if e["event_type"] == "subagent_totals" and e["timestamp"] == ts.isoformat()]
    got = sorted((e["model_name"], e["input_tokens"], e["output_tokens"], e["cache_read_tokens"]) for e in found)
    assert got == sorted((m, i, o, r) for m, i, o, r, _, _ in usage)
    by_model = {e["model_name"]: e for e in found}
    for m, i, o, r, w5, w1 in usage:
        rates = _RATES2 if m == _MODEL2 else _RATES
        assert by_model[m]["cache_creation_tokens"] == w5 + w1
        assert by_model[m]["cost_usd"] == pytest.approx(_price(i, o, r, w5m=w5, w1h=w1, rates=rates), abs=1e-12)
        assert by_model[m]["tool_name"] is None


async def _assert_denied_scope_reads_nothing(engine: AnalyticsPgEngine, sid: str, *, event_types: set[str]) -> None:
    """The lower reader, not only the handler's meta gate: a denied scope (None is always false) gets no row from
    any branch of the events query nor from the tools query, while the allowed scope gets them from this very data."""
    reader = PostgresCliAnalyticsReader(engine)
    allowed = await reader.get_session_detail_events(sid, scope=DETAIL_SCOPE)
    assert {r["event_type"] for r in allowed} >= event_types
    assert await reader.get_session_detail_events(sid, scope=None) == []
    assert await reader.get_session_detail_tools(sid, scope=None) == []


def _growth_cost(detail: dict) -> float:
    """The final value of the UI growth line (SessionBody growthData): the cost of every event with a cost or tokens."""
    return sum(
        e["cost_usd"] or 0.0 for e in detail["events"] if e["cost_usd"] is not None or e["input_tokens"] is not None
    )


@pytest.mark.asyncio
async def test_hooks_only_requests_are_priced_with_their_cache_writes_and_add_up_to_the_session_cost() -> None:
    sid = f"t16609-hooks-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.session.start')",
                _at(0),
                sid,
            )
            # (request id, seconds, model, input, output, read, stop, 5m, 1h): a 1h-only write, one request
            # as a partial snapshot then its final row, an unsplit one (no cache write known), a 5m + 1h one
            # and a request under a Bedrock-style name.
            rows = [
                ("r1", 1, _MODEL, 2, 316, 24_739, "end_turn", None, 24_216),
                ("r2", 2, _MODEL, 2, 10, 48_955, None, None, 415),
                ("r2", 3, _MODEL, 2, 92, 48_955, "end_turn", None, 415),
                ("r3", 4, _MODEL, 5, 7, 100, "end_turn", None, None),
                ("r4", 5, _MODEL, 1, 1, 1, "end_turn", 1_000, 500),
                ("r5", 6, _ALIAS, 3, 4, 5, "end_turn", 6, 7),
                (None, 7, _MODEL, 1, 1, 0, "end_turn", None, None),
                (None, 7, _MODEL, 1, 1, 0, "end_turn", None, None),
            ]
            for rid, sec, model, i, o, r, stop, w5, w1 in rows:
                await conn.execute(_USAGE, _at(sec), sid, rid, model, i, o, r, stop, w5, w1)
            # a subagent known only by its totals (two models): part of the session cost, no request of its own
            await _totals_hook(conn, sid, "agent-t", 20, _TOTALS_A)
        await _refresh(engine)
        detail = await _detail(engine, sid)
        await _assert_denied_scope_reads_nothing(engine, sid, event_types={"api_request", "subagent_totals"})
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    events = _requests(detail)
    assert [(e["model_name"], e["cache_creation_tokens"]) for e in events] == [
        (_MODEL, 24_216),
        (_MODEL, 415),
        (_MODEL, 0),
        (_MODEL, 1_500),
        (_ALIAS, 13),
        (_MODEL, 0),
        (_MODEL, 0),
    ]
    expected = [
        _price(2, 316, 24_739, w1h=24_216),
        _price(2, 92, 48_955, w1h=415),
        _price(5, 7, 100),
        _price(1, 1, 1, w5m=1_000, w1h=500),
        0.0,  # the alias has no catalog price (EPMCDME-16291), in the request as in the session
        _price(1, 1, 0),
        _price(1, 1, 0),
    ]
    assert [e["cost_usd"] for e in events] == pytest.approx(expected, abs=1e-12)
    assert all(e["tool_name"] is None for e in events)
    totals_only = _TOTALS_A_COST
    assert detail["cost_usd"] == pytest.approx(sum(expected) + totals_only, abs=1e-9)
    # the totals-only subagent is one aggregate event per model, not a request
    _assert_totals_events(detail, _at(20), _TOTALS_A)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-9)


@pytest.mark.asyncio
async def test_a_session_known_only_by_subagent_totals_ends_its_growth_line_at_the_cost() -> None:
    sid = f"t16609-totals-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.session.start')",
                _at(0),
                sid,
            )
            await _totals_hook(conn, sid, "agent-1", 5, _TOTALS_A)
            await _totals_hook(conn, sid, "agent-2", 9, _TOTALS_B)
        await _refresh(engine)
        detail = await _detail(engine, sid)
        await _assert_denied_scope_reads_nothing(engine, sid, event_types={"subagent_totals"})
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    assert _requests(detail) == []
    aggregates = [e for e in detail["events"] if e["event_type"] == "subagent_totals"]
    assert len(aggregates) == 3
    assert detail["cost_usd"] == pytest.approx(_TOTALS_A_COST + _TOTALS_B_COST, abs=1e-9)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-9)


@pytest.mark.asyncio
async def test_a_subagent_with_requests_of_its_own_adds_no_aggregate_event() -> None:
    sid = f"t16609-agent-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.session.start')",
                _at(0),
                sid,
            )
            await conn.execute(_USAGE, _at(1), sid, "m1", _MODEL, 10, 20, 30, "end_turn", 5, None)
            # the subagent has requests of its own AND reports totals: the totals must not be counted again
            await conn.execute(
                "INSERT INTO usage_requests (ts, session_id, request_id, model_raw, model, input_tokens,"
                " output_tokens, cache_read_tokens, stop_reason, agent_id)"
                " VALUES ($1, $2, 's1', $3, $3, 100, 50, 25, 'end_turn', 'agent-r')",
                _at(2),
                sid,
                _MODEL2,
            )
            await _totals_hook(conn, sid, "agent-r", 8, _TOTALS_A)
            # a different subagent known only by totals still gets its aggregate
            await _totals_hook(conn, sid, "agent-t", 9, _TOTALS_B)
        await _refresh(engine)
        detail = await _detail(engine, sid)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    assert [e["model_name"] for e in _requests(detail)] == [_MODEL, _MODEL2]
    _assert_totals_events(detail, _at(9), _TOTALS_B)
    expected = _price(10, 20, 30, w5m=5) + _price(100, 50, 25, rates=_RATES2) + _TOTALS_B_COST
    assert detail["cost_usd"] == pytest.approx(expected, abs=1e-9)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-9)


@pytest.mark.asyncio
async def test_a_session_with_otel_requests_keeps_them_and_its_usage_requests_add_none() -> None:
    sid = f"t16609-mixed-{uuid.uuid4().hex[:8]}"
    otel_only = f"t16609-otel-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            for session in (sid, otel_only):
                for n, rid in enumerate(("q1", "q2")):
                    await conn.execute(
                        _API_REQUEST,
                        _at(10 + n),
                        session,
                        rid,
                        _MODEL,
                        10,
                        20,
                        30,
                        400 + n,
                        json.dumps({"cache_creation_5m_tokens": 100, "cache_creation_1h_tokens": 300 + n}),
                    )
            for n, rid in enumerate(("q1", "q2")):  # the transcript side of the same two requests
                await conn.execute(_USAGE, _at(10 + n), sid, rid, _MODEL, 10, 20, 30, "end_turn", 100, 300 + n)
        await _refresh(engine)
        mixed, plain = await _detail(engine, sid), await _detail(engine, otel_only)
    finally:
        await _cleanup(engine, [sid, otel_only])
        await engine.close()

    for detail in (mixed, plain):
        events = _requests(detail)
        assert [e["cache_creation_tokens"] for e in events] == [400, 401]
        assert [e["cost_usd"] for e in events] == pytest.approx(
            [_price(10, 20, 30, w5m=100, w1h=300), _price(10, 20, 30, w5m=100, w1h=301)], abs=1e-12
        )
    assert mixed["cost_usd"] == pytest.approx(sum(e["cost_usd"] for e in _requests(mixed)), abs=1e-9)


@pytest.mark.asyncio
async def test_detail_tool_calls_and_successes_are_the_aggregate_ones_beyond_the_top_20() -> None:
    hooks, spans = (f"t16741-{kind}-{uuid.uuid4().hex[:8]}" for kind in ("hooks", "spans"))
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            for session in (hooks, spans):
                await conn.execute(
                    "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.session.start')",
                    _at(0),
                    session,
                )
            # 25 hooks-only tools, one hook start per call and an end for the successful ones: T00 3 calls / 2 ok,
            # T01-T22 1 / 1 each, T23 1 / 0 (a real zero), T24 2 / 0. 28 calls, 24 ok; only T00, T24 and T01-T18
            # are listed, so the listed successes (20) are fewer than the session's.
            calls = {"T00": (3, 2), "T23": (1, 0), "T24": (2, 0), **{f"T{n:02d}": (1, 1) for n in range(1, 23)}}
            for name, (starts, ends) in calls.items():
                for n in range(starts):
                    use_id = f"{name}-{n}"
                    await conn.execute(_HOOK, _at(5), hooks, "agent.tool.start", use_id, name)
                    if n < ends:
                        await conn.execute(_HOOK, _at(6), hooks, "agent.tool.end", use_id, name)
            # an OTel session: Read succeeded, Bash failed
            for n, (name, success) in enumerate((("Read", "true"), ("Bash", "false"))):
                use_id = f"tu-{n}"
                tool, execution = b"tool%d" % n, b"exec%d" % n
                await conn.execute(
                    _SPAN, _at(5), tool, b"main", int(SpanKind.TOOL), "claude_code.tool", spans, name, use_id, None
                )
                await conn.execute(
                    _SPAN,
                    _at(6),
                    execution,
                    tool,
                    int(SpanKind.TOOL_EXECUTION),
                    "tool.execution",
                    spans,
                    "",
                    use_id,
                    success,
                )
        await _refresh(engine)
        reader = PostgresCliAnalyticsReader(engine)
        details = {sid: await _detail(engine, sid) for sid in (hooks, spans)}
        # the populated hooks-only tools are served to the allowed scope and to no other
        assert len(await reader.get_session_detail_tools(hooks, scope=DETAIL_SCOPE)) == 20
        assert await reader.get_session_detail_tools(hooks, scope=None) == []
        window = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE, start_dt=_START - timedelta(days=1), end_dt=_START + timedelta(days=1)
        )
        aggregate = {
            r["session_id"]: r for r in await reader.get_tool_success_by_session(window.restricted_to([hooks, spans]))
        }
    finally:
        await _cleanup(engine, [hooks, spans])
        await engine.close()

    detail = details[hooks]
    assert (detail["tool_call_count"], detail["tool_calls_success"], detail["tool_success_rate"]) == (28, 24, 85.7)
    assert [t["tool_name"] for t in detail["tools"]][:2] == ["T00", "T24"]
    assert len(detail["tools"]) == 20
    assert sum(t["success_count"] for t in detail["tools"]) == 20 < detail["tool_calls_success"]
    assert {t["tool_name"]: (t["call_count"], t["success_count"]) for t in detail["tools"]}["T24"] == (2, 0)
    assert {t["tool_name"]: t["success_rate"] for t in detail["tools"]}["T00"] == 66.7
    detail = details[spans]
    assert (detail["tool_call_count"], detail["tool_calls_success"], detail["tool_success_rate"]) == (2, 1, 50.0)
    for sid, detail in details.items():  # the aggregates of the same session say the same
        assert (detail["tool_call_count"], detail["tool_calls_success"]) == (
            aggregate[sid]["tool_calls"],
            aggregate[sid]["tool_calls_success"],
        )
