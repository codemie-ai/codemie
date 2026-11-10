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

"""The route-pricing SQL of CLI Analytics on a real PostgreSQL: the `cost_daily` key of a request that ran on a
LiteLLM route, the stored and listed model names, session detail, dispatch money and the catalog money of Opus 4.6.

Raw rows are inserted and rolled up by the real refresher, then read through the real reader and handler at a
mocked catalog. Needs `TEST_LOCAL_PG_URL` (a disposable PostgreSQL; the tests migrate it): without it every test is
skipped, and with it a test is skipped (not failed) when that database is unreachable. Each test deletes only its
own sessions. Run in one process, as the tests refresh the same rollup days:

    TEST_LOCAL_PG_URL=postgresql://<user>:<password>@<host>:<port>/<db> poetry run pytest -n0 <this file>
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.cost_key_sql import normalized_model_name
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.vocabulary import EventKind
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.analytics.handlers.coding_agent_pricing import normalize_model
from codemie.service.llm_service.llm_service import llm_service
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE, EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

# Any constant shared by the xdist workers: start() on an empty schema must run in one of them at a time.
_SCHEMA_LOCK = 0x6D6F6E6579
# Two days back: the next UTC day's requests are already in the past, and the day never leaves a retention window.
_DAY = datetime.now(timezone.utc).date() - timedelta(days=2)
_START = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)

_SONNET = "claude-sonnet-4-6"
_ROUTE = "bedrock/eu.anthropic.claude-sonnet-4-6"
_VERTEX_ROUTE = "vertex_ai/claude-sonnet-4-6"
# input, output, cache read, cache write 5m, cache write 1h per token; the plain catalog price is 3.0e-06 per input
# token, the route's own 3.3e-06
_SONNET_RATES = (3.0e-6, 1.5e-5, 3e-7, 3.75e-6, 6e-6)
_ROUTE_RATES = (3.3e-6, 1.65e-5, 3.3e-7, 4.125e-6, 6.6e-6)
_ROUTED_REQUEST_COST = 0.003465  # 1 000 input at 3.3e-06 + 10 output at 1.65e-05
_PLAIN_REQUEST_COST = 0.00315  # 1 000 input at 3.0e-06 + 10 output at 1.5e-05

_KEY_USAGE = """INSERT INTO usage_requests
    (ts, session_id, request_id, model_raw, model, input_tokens, output_tokens, cache_read_tokens, stop_reason,
     user_email)
    VALUES ($1, $2, $3, $4, $5, 10, 1, 0, 'end_turn', 'route-key@example.com')"""
_ROUTED_USAGE = """INSERT INTO usage_requests
    (ts, session_id, request_id, model_raw, model, input_tokens, output_tokens, cache_read_tokens, stop_reason)
    VALUES ($1, $2, $3, $4, $5, 1000, 10, 0, 'end_turn')"""
_API_REQUEST = """INSERT INTO log_events
    (ts, session_id, event_kind, request_id, model, cost_usd, input_tokens, output_tokens, cache_read_tokens,
     cache_creation_tokens, severity, resource_id, attrs)
    VALUES ($1, $2, 1, $3, $4, 0.5, $5, $6, $7, $8, 9, 1, $9::jsonb)"""
_SESSION_START = "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.session.start')"


def _cost(rates: tuple) -> CostConfig:
    return CostConfig(
        input=rates[0],
        output=rates[1],
        cache_read_input_token_cost=rates[2],
        cache_creation_input_token_cost=rates[3],
        cache_creation_input_token_cost_above_1hr=rates[4],
    )


def _entry(name: str, rates: tuple) -> MagicMock:
    entry = MagicMock()
    entry.base_name, entry.deployment_name, entry.cost = name, None, _cost(rates)
    return entry


@contextmanager
def _catalog(models: list, routes: dict[str, CostConfig]):
    """The LiteLLM catalog: its chat models and its route -> price map."""
    with (
        patch.object(llm_service, "get_all_llm_model_info", return_value=models),
        patch.object(llm_service, "_litellm_route_costs", routes),
        patch.object(llm_service, "catalog_source", return_value="litellm"),
    ):
        yield


async def _engine() -> AnalyticsPgEngine:
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_URL=_LOCAL_PG_URL))
    engine = AnalyticsPgEngine(settings)
    try:
        lock = await asyncpg.connect(settings.dsn)
    except (OSError, asyncpg.PostgresError) as exc:  # no reachable database: skip, do not fail
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    try:
        await lock.execute("SELECT pg_advisory_lock($1)", _SCHEMA_LOCK)
        await PostgresAnalyticsRuntime(engine, settings).start()
    finally:
        await lock.close()  # closing the session releases its advisory lock
    return engine


async def _cleanup(engine: AnalyticsPgEngine, session_ids: list[str]) -> None:
    """Remove the sessions from every analytics table that has a session_id: raw rows, rollups and the queue."""
    async with engine.acquire() as conn:
        tables = await conn.fetch(
            """SELECT DISTINCT table_name FROM information_schema.columns
               WHERE table_schema = 'codemie_analytics' AND column_name = 'session_id'"""
        )
        for row in tables:
            await conn.execute(
                f"DELETE FROM codemie_analytics.{row['table_name']} WHERE session_id = ANY($1::text[])",  # noqa: S608
                session_ids,
            )


async def _refresh(engine: AnalyticsPgEngine, first_day: date = _DAY, last_day: date | None = None) -> None:
    async with live_rollup_lock(engine):
        refresher = RollupRefresher(engine, batch_size=100)
        async with engine.acquire() as conn:
            await refresher.mark_dirty(first_day, last_day or first_day, conn)
        await refresher.refresh()


def _at(seconds: int) -> datetime:
    return _START + timedelta(seconds=seconds)


async def _detail(engine: AnalyticsPgEngine, sid: str, models: list, routes: dict) -> dict:
    with _catalog(models, routes):
        handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))
        detail, _, _ = await handler.get_session_detail(sid, scope=DETAIL_SCOPE)
    assert detail is not None
    return detail


def _requests(detail: dict) -> list[dict]:
    return [e for e in detail["events"] if e["event_type"] == "api_request"]


def _growth_cost(detail: dict) -> float:
    """The final value of the UI growth line (SessionBody growthData): the cost of every event with a cost or tokens."""
    return sum(
        e["cost_usd"] or 0.0 for e in detail["events"] if e["cost_usd"] is not None or e["input_tokens"] is not None
    )


def _money(value: float) -> float:
    return pytest.approx(value, abs=1e-8)


async def _model_names(engine: AnalyticsPgEngine, sid: str) -> list[str]:
    async with engine.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT model_name FROM cost_daily WHERE session_id = $1", sid)
    return [r["model_name"] for r in rows]


# The cost_daily key, the stored and listed model names, the normaliser.


async def _seed(rows: dict[str, list[tuple[str | None, str, str]]]) -> tuple[dict, dict, list[str]]:
    """`rows`: per session name, (request id, model_raw, model) transcript lines one second apart. Returns the
    cost_daily calls per (session name, model_name), session_dims.models per session name, and the session ids."""
    engine = await _engine()
    ids = {name: f"t16633-{name}-{uuid.uuid4().hex[:8]}" for name in rows}
    try:
        async with engine.acquire() as conn:
            for name, lines in rows.items():
                await conn.execute(_SESSION_START, _at(0), ids[name])
                for n, (rid, model_raw, model) in enumerate(lines):
                    await conn.execute(_KEY_USAGE, _at(1 + n), ids[name], rid, model_raw, model)
        await _refresh(engine)
        async with engine.acquire() as conn:
            cost = await conn.fetch(
                "SELECT session_id, model_name, sum(api_call_count) AS calls FROM cost_daily"
                " WHERE session_id = ANY($1::text[]) GROUP BY 1, 2",
                list(ids.values()),
            )
            dims = await conn.fetch(
                "SELECT session_id, models FROM session_dims WHERE session_id = ANY($1::text[])", list(ids.values())
            )
    except BaseException:
        await _cleanup(engine, list(ids.values()))
        raise
    finally:
        await engine.close()
    names = {sid: name for name, sid in ids.items()}
    calls = {(names[r["session_id"]], r["model_name"]): r["calls"] for r in cost}
    models = {names[r["session_id"]]: json.loads(r["models"]) for r in dims}
    return calls, models, list(ids.values())


async def _drop(session_ids: list[str]) -> None:
    engine = await _engine()
    try:
        await _cleanup(engine, session_ids)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_cost_daily_keys_a_routed_transcript_request_by_its_route_and_every_other_by_its_own_name() -> None:
    route = _ROUTE
    calls, models, ids = await _seed(
        {
            # one routed and one plain request of the same model on one day: two cost_daily rows
            "mixed": [("r1", _SONNET, route), ("r2", _SONNET, _SONNET)],
            # Copilot CLI: model_raw = model
            "copilot": [("c1", "gpt-5.5-2026-04-24", "gpt-5.5-2026-04-24")],
            # Claude Code on a non-Claude model: the proxy's name does not reduce to the transcript's, so no route
            "gpt": [("g1", "gpt-5.5-2026-04-24", "azure/codemie-gpt-5.5-2026-04-24")],
            # a route of another model is not the request's route
            "other": [("o1", _SONNET, "bedrock/eu.anthropic.claude-opus-4-8")],
            # a dated route reduces to the dated transcript name: still a route
            "dated": [("t1", "claude-sonnet-4-5-20250929", "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")],
            # a model that differs from model_raw without a '/' is no route
            "noslash": [("n1", _SONNET, "claude-sonnet-4-6-dial")],
            # a request whose lines name more than one route keeps its plain name, with or without a plain line
            "disagree": [("d1", _SONNET, route), ("d1", _SONNET, _VERTEX_ROUTE)],
            "disagree-plain": [("dp1", _SONNET, route), ("dp1", _SONNET, _VERTEX_ROUTE), ("dp1", _SONNET, _SONNET)],
            # two lines of one request on the same route keep that route
            "same-route": [("sr1", _SONNET, route), ("sr1", _SONNET, route)],
            # one request with a routed line and a plain line: the route, whether the prefix sorts before
            # the plain name (bedrock/, anthropic/) or after it (vertex_ai/)
            "mixed-bedrock": [("m1", _SONNET, route), ("m1", _SONNET, _SONNET)],
            "mixed-anthropic": [("m2", _SONNET, "anthropic/anthropic.claude-sonnet-4-6"), ("m2", _SONNET, _SONNET)],
            "mixed-vertex": [("m3", _SONNET, _VERTEX_ROUTE), ("m3", _SONNET, _SONNET)],
            # real pairs: a Bedrock '-v1' id counts for the undated and for the dated transcript name, an undated
            # Vertex route for a dated one, a dated route for an undated name; another model's deployment never does
            "opus-v1": [("p1", "claude-opus-4-6", "bedrock/eu.anthropic.claude-opus-4-6-v1")],
            "opus-dated": [("q1", "claude-opus-4-6-20260205", "bedrock/eu.anthropic.claude-opus-4-6-v1")],
            "haiku-bedrock": [
                (
                    "h1",
                    "anthropic.claude-haiku-4-5-20251001-v1:0",
                    "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0",
                )
            ],
            "sonnet-vertex": [("s1", "claude-sonnet-4-5-20250929", "vertex_ai/claude-sonnet-4-5")],
            "other-dated": [("od1", "claude-sonnet-4-5-20250929", "bedrock/eu.anthropic.claude-opus-4-8")],
            "undated-raw": [("ur1", "claude-sonnet-4-5", "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")],
            "haiku-5-5": [("k1", "claude-haiku-5-5", "bedrock/eu.anthropic.claude-haiku-5-5")],
            "anthropic-direct": [("ad1", _SONNET, "anthropic/claude-sonnet-4-6")],
            "vertex-at": [("va1", "claude-haiku-4-5-20251001", "vertex_ai/claude-haiku-4-5@20251001")],
        }
    )
    try:
        assert calls == {
            ("mixed", route): 1,
            ("mixed", _SONNET): 1,
            ("copilot", "gpt-5.5-2026-04-24"): 1,
            ("gpt", "gpt-5.5-2026-04-24"): 1,
            ("other", _SONNET): 1,
            ("dated", "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"): 1,
            ("noslash", _SONNET): 1,
            ("disagree", "claude-sonnet-4-6"): 1,
            ("disagree-plain", "claude-sonnet-4-6"): 1,
            ("same-route", "bedrock/eu.anthropic.claude-sonnet-4-6"): 1,
            ("mixed-bedrock", route): 1,
            ("mixed-anthropic", "anthropic/anthropic.claude-sonnet-4-6"): 1,
            ("mixed-vertex", "vertex_ai/claude-sonnet-4-6"): 1,
            ("opus-v1", "bedrock/eu.anthropic.claude-opus-4-6-v1"): 1,
            ("opus-dated", "bedrock/eu.anthropic.claude-opus-4-6-v1"): 1,
            ("haiku-bedrock", "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0"): 1,
            ("sonnet-vertex", "vertex_ai/claude-sonnet-4-5"): 1,
            ("other-dated", "claude-sonnet-4-5-20250929"): 1,
            ("undated-raw", "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"): 1,
            ("haiku-5-5", "bedrock/eu.anthropic.claude-haiku-5-5"): 1,
            ("anthropic-direct", "anthropic/claude-sonnet-4-6"): 1,
            ("vertex-at", "vertex_ai/claude-haiku-4-5@20251001"): 1,
        }
        # without a summary event the session's models come from cost_daily, under the normalised name
        assert models["mixed"] == [_SONNET]
        assert models["copilot"] == ["gpt-5.5-2026-04-24"]
        assert models["gpt"] == ["gpt-5.5-2026-04-24"]
        assert models["haiku-5-5"] == ["claude-haiku-5-5"]
        assert models["sonnet-vertex"] == ["claude-sonnet-4-5"]
        assert models["opus-dated"] == ["claude-opus-4-6"]
        assert models["other-dated"] == ["claude-sonnet-4-5-20250929"]
        assert models["undated-raw"] == ["claude-sonnet-4-5-20250929"]
        assert models["anthropic-direct"] == [_SONNET]
        assert models["vertex-at"] == ["claude-haiku-4-5-20251001"]
    finally:
        await _drop(ids)


@pytest.mark.asyncio
async def test_a_summary_listing_routes_stores_the_normalised_models() -> None:
    # The Claude Code plugin fills the summary's models from each request's model, the route when the transcript
    # carries one: stored under the normalised name, deduplicated in first position, as a session without a summary.
    sid = f"t16633-summary-{uuid.uuid4().hex[:8]}"
    summary = {
        "models": ["bedrock/eu.anthropic.claude-sonnet-4-6", "claude-sonnet-4-6", "claude-opus-4-8"],
        "primary_model": "bedrock/eu.anthropic.claude-sonnet-4-6",
    }
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type, attrs)"
                " VALUES ($1, $2, 'agent.session.summary', $3)",
                _at(0),
                sid,
                json.dumps(summary),
            )
            await conn.execute(_KEY_USAGE, _at(1), sid, "r1", _SONNET, "bedrock/eu.anthropic.claude-sonnet-4-6")
        await _refresh(engine)
        async with engine.acquire() as conn:
            row = await conn.fetchrow("SELECT models, primary_model FROM session_dims WHERE session_id = $1", sid)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    assert json.loads(row["models"]) == ["claude-sonnet-4-6", "claude-opus-4-8"]
    assert row["primary_model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_the_users_top_model_and_the_session_model_list_are_the_normalised_name() -> None:
    lines = (
        [(f"b{n}", _SONNET, "bedrock/eu.anthropic.claude-sonnet-4-6") for n in range(3)]
        + [(f"a{n}", _SONNET, "anthropic/anthropic.claude-sonnet-4-6") for n in range(2)]
        + [(f"o{n}", "claude-opus-4-8", "claude-opus-4-8") for n in range(4)]
    )
    _, _, ids = await _seed({"top": lines})
    engine = await _engine()
    try:
        handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))
        window = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE, start_dt=_START - timedelta(days=1), end_dt=_START + timedelta(days=1)
        ).restricted_to(ids)
        with _catalog([], {}):
            users, _ = await handler.get_users(window, None, None)
            sessions, _, _ = await handler.get_sessions(window, 0, 10, "start_time", None)
    finally:
        await _cleanup(engine, ids)
        await engine.close()

    assert [r["top_model"] for r in users["rows"]] == ["claude-sonnet-4-6"]
    assert [r["model_name"] for r in sessions["sessions"]] == ["claude-opus-4-8, claude-sonnet-4-6"]


@pytest.mark.asyncio
async def test_the_sql_normaliser_equals_the_python_one_on_the_route_forms() -> None:
    names = [
        "bedrock/eu.anthropic.claude-sonnet-4-6",
        "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "bedrock/global.anthropic.claude-fable-5-1",
        "vertex_ai/claude-sonnet-4-6",
        "anthropic/anthropic.claude-sonnet-4-6",
        "bedrock/converse/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "bedrock/anthropic.claude-sonnet-4-6",
        "bedrock/eu.anthropic.claude-opus-4-6-v1",
        "bedrock/us.anthropic.claude-opus-4-6-v1",
        "anthropic.claude-opus-4-6-v1",
        "us.anthropic.claude-opus-4-6-v1",
        "claude-3-5-sonnet-v2",
        "claude-opus-router-v1",
        "qwen.qwen3-coder-30b-a3b-v1",
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "anthropic.claude-v2",
        "anthropic.claude-opus-4-6-v2",
        "bedrock/eu.anthropic.claude-opus-4-6-v2",
        "bedrock/eu.anthropic.claude-haiku-5-5",
        "anthropic/claude-sonnet-4-6",
        "vertex_ai/claude-haiku-4-5@20251001",
        "vertex_ai/claude-sonnet-4-5@20250929",
        "claude-sonnet-4-5@2025",
    ]
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            rows = await conn.fetch(f"SELECT n, {normalized_model_name('n')} AS m FROM unnest($1::text[]) AS n", names)
    finally:
        await engine.close()

    assert {r["n"]: r["m"] for r in rows} == {name: normalize_model(name) for name in names}
    assert [normalize_model(name) for name in names] == [
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-fable-5-1",
        "claude-sonnet-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-opus-4-6",
        "claude-opus-4-6",
        "claude-opus-4-6",
        "claude-3-5-sonnet-v2",
        "claude-opus-router-v1",
        "qwen.qwen3-coder-30b-a3b-v1",
        "claude-3-5-sonnet-20241022",
        "claude-v2",
        "claude-opus-4-6-v2",
        "claude-opus-4-6-v2",
        "claude-haiku-5-5",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-5@2025",
    ]


# Session detail of a routed session.


async def _routed_pair(conn: asyncpg.Connection, sid: str, transcript_ts: datetime, otel_ts: datetime, rid: str | None):
    """One request run on `_ROUTE`: its transcript line (`rid` or no id) and its OTel record under the plain name."""
    await conn.execute(_ROUTED_USAGE, transcript_ts, sid, rid, _SONNET, _ROUTE)
    await conn.execute(_API_REQUEST, otel_ts, sid, rid or "otel-" + otel_ts.isoformat(), _SONNET, 1000, 10, 0, 0, "{}")


async def _routed_detail(sid: str, pairs: list[tuple[datetime, datetime, str | None]], last_day: date) -> tuple:
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(_SESSION_START, _at(0), sid)
            for transcript_ts, otel_ts, rid in pairs:
                await _routed_pair(conn, sid, transcript_ts, otel_ts, rid)
        await _refresh(engine, last_day=last_day)
        detail = await _detail(engine, sid, [_entry(_SONNET, _SONNET_RATES)], {_ROUTE: _cost(_ROUTE_RATES)})
        names = await _model_names(engine, sid)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()
    return detail, names


async def _two_line_request(otel: bool, second_model: str, routes: dict[str, CostConfig]) -> tuple:
    """One request `x1`: a line on `_ROUTE` (with its OTel record when `otel`) and a second transcript line on
    `second_model` a second later. Returns its session detail and the session's cost_daily model names."""
    sid = f"t16633-mixed-{uuid.uuid4().hex[:8]}"
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(_SESSION_START, _at(0), sid)
            if otel:
                await _routed_pair(conn, sid, _at(10), _at(10), "x1")
            else:
                await conn.execute(_ROUTED_USAGE, _at(10), sid, "x1", _SONNET, _ROUTE)
            await conn.execute(_ROUTED_USAGE, _at(11), sid, "x1", _SONNET, second_model)
        await _refresh(engine)
        detail = await _detail(engine, sid, [_entry(_SONNET, _SONNET_RATES)], routes)
        names = await _model_names(engine, sid)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()
    return detail, names


@pytest.mark.asyncio
async def test_a_routed_session_prices_its_events_by_the_route_cost_daily_keys_them_by() -> None:
    sid = f"t16633-routed-{uuid.uuid4().hex[:8]}"
    # a pair by request id and a pair by fingerprint (no transcript id, 2 s apart)
    detail, names = await _routed_detail(sid, [(_at(10), _at(10), "q1"), (_at(20), _at(22), None)], _DAY)

    assert names == [_ROUTE]
    events = _requests(detail)
    assert [e["model_name"] for e in events] == [_SONNET, _SONNET]
    assert [e["cost_usd"] for e in events] == pytest.approx([_ROUTED_REQUEST_COST] * 2, abs=1e-12)
    assert detail["cost_usd"] == pytest.approx(0.00693, abs=1e-12)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-12)


@pytest.mark.asyncio
async def test_routed_pairs_across_midnight_add_up_to_the_session_cost() -> None:
    sid = f"t16633-midnight-{uuid.uuid4().hex[:8]}"
    midnight = _START + timedelta(hours=12)
    pairs = [
        # one request id, 118 min apart on both sides of midnight: each within 1 h of the other's day
        (midnight - timedelta(minutes=59), midnight + timedelta(minutes=59), "m1"),
        # a fingerprint pair 4 s apart across midnight, inside the day widened by 5 s
        (midnight - timedelta(seconds=2), midnight + timedelta(seconds=2), None),
    ]
    detail, names = await _routed_detail(sid, pairs, _DAY + timedelta(days=1))

    assert names == [_ROUTE]
    assert [e["cost_usd"] for e in _requests(detail)] == pytest.approx([_ROUTED_REQUEST_COST] * 2, abs=1e-12)
    assert detail["cost_usd"] == pytest.approx(0.00693, abs=1e-12)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-12)


@pytest.mark.asyncio
@pytest.mark.parametrize("otel", [True, False], ids=["paired", "transcript-only"])
async def test_a_request_with_a_routed_and_a_plain_transcript_line_is_priced_at_the_route(otel: bool) -> None:
    # the second line of the request is under the plain name, which sorts after the bedrock route
    detail, names = await _two_line_request(otel, _SONNET, {_ROUTE: _cost(_ROUTE_RATES)})

    assert names == [_ROUTE]
    assert [e["cost_usd"] for e in _requests(detail)] == pytest.approx([_ROUTED_REQUEST_COST], abs=1e-12)
    assert detail["cost_usd"] == pytest.approx(_ROUTED_REQUEST_COST, abs=1e-12)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-12)


@pytest.mark.asyncio
@pytest.mark.parametrize("otel", [True, False], ids=["paired", "transcript-only"])
async def test_a_request_with_lines_on_two_routes_of_the_model_is_priced_at_the_plain_name(otel: bool) -> None:
    # both routes are priced at the route rates, yet a request naming two routes ran on neither for certain
    routes = {_ROUTE: _cost(_ROUTE_RATES), _VERTEX_ROUTE: _cost(_ROUTE_RATES)}
    detail, names = await _two_line_request(otel, _VERTEX_ROUTE, routes)

    assert names == [_SONNET]
    assert [e["cost_usd"] for e in _requests(detail)] == pytest.approx([_PLAIN_REQUEST_COST], abs=1e-12)
    assert detail["cost_usd"] == pytest.approx(_PLAIN_REQUEST_COST, abs=1e-12)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-12)


@pytest.mark.asyncio
async def test_a_proxy_name_of_another_model_is_no_route_and_its_event_is_priced_by_the_plain_name() -> None:
    # Claude Code on a non-Claude model: LiteLLM names the deployment azure/codemie-gpt-5.5-2026-04-24, which does
    # not reduce to the transcript's gpt-5.5-2026-04-24, so cost_daily and the event keep the plain name and price.
    sid = f"t16633-gpt-{uuid.uuid4().hex[:8]}"
    gpt = "gpt-5.5-2026-04-24"
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(_SESSION_START, _at(0), sid)
            await conn.execute(_ROUTED_USAGE, _at(10), sid, "g1", gpt, "azure/codemie-gpt-5.5-2026-04-24")
            await conn.execute(_API_REQUEST, _at(10), sid, "g1", gpt, 1000, 10, 0, 0, "{}")
        await _refresh(engine)
        detail = await _detail(engine, sid, [_entry(gpt, _SONNET_RATES)], {})
        names = await _model_names(engine, sid)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()

    assert names == [gpt]
    assert [e["cost_usd"] for e in _requests(detail)] == pytest.approx([_PLAIN_REQUEST_COST], abs=1e-12)
    assert detail["cost_usd"] == pytest.approx(_PLAIN_REQUEST_COST, abs=1e-12)
    assert _growth_cost(detail) == pytest.approx(detail["cost_usd"], abs=1e-12)


# Catalog money of Opus 4.6.

_OPUS_46 = "claude-opus-4-6"
_OPUS_46_ROUTE = "bedrock/eu.anthropic.claude-opus-4-6-v1"
_OPUS_46_USAGE = """INSERT INTO usage_requests
    (ts, session_id, request_id, model_raw, model, input_tokens, output_tokens, cache_read_tokens,
     cache_creation_5m_tokens, cache_creation_1h_tokens, stop_reason, user_email)
    VALUES ($1, $2, 'opus46-r1', $3, $4, 4, 137, 0, 68800, 0, 'end_turn', 'opus46@example.com')"""


def _opus_46_catalog(dated_input: float) -> list[MagicMock]:
    """The two Opus 4.6 deployments (the dated one at `dated_input`, -vertex at 5 per MTok), Opus 4.7 and a pair of
    one undated name with conflicting tariffs."""
    return [
        _entry("claude-opus-4-6-20260205", (dated_input, 25e-6, 0.5e-6, 6.25e-6, 10e-6)),
        _entry("claude-opus-4-6-vertex", (5e-6, 25e-6, 0.5e-6, 6.25e-6, 10e-6)),
        _entry("claude-opus-4-7", (5e-6, 25e-6, 0.5e-6, 6.25e-6, 10e-6)),
        _entry("claude-zeta-1-20260101", (1e-6, 5e-6, 0.1e-6, 1.25e-6, 2e-6)),
        _entry("claude-zeta-1-vertex", (2e-6, 10e-6, 0.2e-6, 2.5e-6, 4e-6)),
    ]


async def _opus_46_session(model: str, models: list[MagicMock], routes: dict[str, CostConfig]) -> tuple:
    """One Claude Code transcript request recorded as claude-opus-4-6 (input 4, output 137, cache write 68 800 at
    5m) on `model`, through the real rollup refresher; returns the session detail, its unpriced models and the
    Overview and Cost totals of its window, priced at `models` / `routes`."""
    engine = await _engine()
    sid = f"opus46-{uuid.uuid4().hex[:8]}"
    try:
        async with engine.acquire() as conn:
            await conn.execute(_SESSION_START, _at(0), sid)
            await conn.execute(_OPUS_46_USAGE, _at(1), sid, _OPUS_46, model)
        await _refresh(engine)
        start = _START.replace(tzinfo=None)
        window = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE, start_dt=start - timedelta(hours=1), end_dt=start + timedelta(hours=1)
        ).restricted_to([sid])
        with _catalog(models, routes):
            handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))
            detail, unpriced, _ = await handler.get_session_detail(sid, scope=DETAIL_SCOPE)
            overview, _, _ = await handler.get_overview(window)
            cost, _ = await handler.get_cost(window)
    finally:
        await _cleanup(engine, [sid])
        await engine.close()
    return detail, unpriced, overview["kpis"]["total_cost_usd"], cost["kpis"]["total_cost_usd"]


@pytest.mark.asyncio
async def test_an_undated_opus_46_session_is_priced_at_the_one_tariff_of_its_deployments() -> None:
    detail, unpriced, overview_total, cost_total = await _opus_46_session(_OPUS_46, _opus_46_catalog(5e-6), {})

    # 4 x 5 + 137 x 25 + 68 800 x 6.25 per MTok
    assert detail["cost_usd"] == _money(0.433445)
    assert detail["cost_available"] is True
    assert _OPUS_46 not in unpriced
    assert (overview_total, cost_total) == (_money(0.433445), _money(0.433445))


@pytest.mark.asyncio
async def test_an_opus_46_session_on_a_bedrock_route_is_priced_at_the_route_when_the_deployments_disagree() -> None:
    route = CostConfig(
        input=5.5e-6,
        output=27.5e-6,
        cache_read_input_token_cost=0.55e-6,
        cache_creation_input_token_cost=6.875e-6,
        cache_creation_input_token_cost_above_1hr=11e-6,
    )
    detail, unpriced, overview_total, cost_total = await _opus_46_session(
        _OPUS_46_ROUTE, _opus_46_catalog(5.5e-6), {_OPUS_46_ROUTE: route}
    )

    # 4 x 5.5 + 137 x 27.5 + 68 800 x 6.875 per MTok
    assert detail["cost_usd"] == _money(0.4767895)
    assert detail["cost_available"] is True
    assert unpriced == []
    assert (overview_total, cost_total) == (_money(0.4767895), _money(0.4767895))


# Dispatch money of a routed session: rows ingested the way the plugin sends them, on today's partitions.

_TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
_CLAUDE_CODE = {"platform": "claude-code"}
_INTERACTION_SPAN_SQL = """
    INSERT INTO spans
        (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name,
         session_id, tool_name, tool_use_id, subagent_type, status_code, resource_id)
    VALUES ($1, 3600000000000, $2, $3, NULL, 3, 'interaction', $4, '', '', '', 0, 0)
"""
_INLINE_REQUEST_SQL = """
    INSERT INTO log_events
        (ts, session_id, event_kind, prompt_id, request_id, span_id, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, severity, resource_id, model, user_email)
    VALUES ($1, $2, $3, $4, nullif($5, ''), $6, $7::float8, $8, $9, $10, $11, 0, 0, $12, 'test@example.com')
"""


def _id(name: str) -> bytes:
    # A real span_id is a unique per-span OTel id; hash (not truncate) so two different names never collapse to
    # the same 8 bytes.
    return hashlib.sha1(name.encode()).digest()[:8]


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _hook(event_type: str, hook_event_name: str, session_id: str, ts: datetime, **fields) -> dict[str, Any]:
    return {
        "type": event_type,
        "timestamp": _iso(ts),
        "session_id": session_id,
        "hook_event_name": hook_event_name,
        **_CLAUDE_CODE,
        **fields,
    }


def _fork(session_id: str, ts: datetime, agent_id: str, prompt_id: str) -> dict[str, Any]:
    """SubagentStart of a foreground fork: no skill_name."""
    return _hook(
        "agent.subagent.start",
        "SubagentStart",
        session_id,
        ts,
        prompt_id=prompt_id,
        agent_id=agent_id,
        agent_type="general-purpose",
    )


def _dispatch(session_id: str, ts: datetime, prompt_id: str, skill_name: str) -> dict[str, Any]:
    """UserPromptExpansion of a slash command."""
    return _hook(
        "agent.skill.dispatch",
        "UserPromptExpansion",
        session_id,
        ts,
        expansion_type="slash_command",
        command_name=skill_name,
        command_source="projectSettings",
        skill_name=skill_name,
        prompt_id=prompt_id,
    )


def _stop(session_id: str, ts: datetime, agent_id: str) -> dict[str, Any]:
    """SubagentStop of a fork."""
    return _hook("agent.subagent.stop", "SubagentStop", session_id, ts, agent_id=agent_id, agent_type="general-purpose")


async def _ingest(engine: AnalyticsPgEngine, events: list[dict[str, Any]]) -> None:
    ingestor = PostgresTelemetryIngestor(engine, clock=lambda: _TODAY.date())
    await ingestor.ingest_hook_events(events, "test@example.com", 0)


async def _seed_inline_request(
    engine: AnalyticsPgEngine,
    session_id: str,
    ts: datetime,
    prompt_id: str,
    request_id: str,
    scope: tuple[str, str],
    input_tokens: int,
    *,
    model: str,
    route: str,
    agent_id: str = "",
    span: bytes | None = None,
) -> None:
    """An API request of `input_tokens`: its OTel record at `ts` under `model` and the plugin's agent.usage.request
    with its attribution scope (kind, name) and the same request id, `model` its model_raw and `route` its model;
    the record sits under `span`, else the session's interaction span."""
    async with engine.acquire() as conn:
        await conn.execute(
            _INLINE_REQUEST_SQL,
            ts,
            session_id,
            int(EventKind.API_REQUEST),
            prompt_id,
            request_id,
            span or _id(f"int-{session_id}"),
            0.0,
            input_tokens,
            0,
            0,
            0,
            model,
        )
    await _ingest(
        engine,
        [
            {
                "type": "agent.usage.request",
                "timestamp": _iso(ts),
                "session_id": session_id,
                "request_id": request_id,
                "message_id": "",
                "agent_id": agent_id,
                "scope_kind": scope[0],
                "scope_name": scope[1],
                "model_raw": model,
                "model": route,
                "input_tokens": input_tokens,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_1h_tokens": 0,
                "stop_reason": "end_turn",
                **_CLAUDE_CODE,
            }
        ],
    )


@pytest.mark.asyncio
async def test_live_dispatches_on_a_routed_session_are_priced_by_the_route_their_requests_ran_on() -> None:
    """Every request of the session ran on the LiteLLM route `bedrock/eu.anthropic.claude-sonnet-4-6`, priced
    3.3e-06 per input token against the plain claude-sonnet-4-6's 3.0e-06: an inline slash skill (its attributed
    main-thread requests), a foreground fork (its paired requests) and a fork named by its LLM requests (its span
    group) each price their requests by the route, as cost_daily does, so the rows add up to the session cost."""
    # The ingest ledger keeps every record's identity: a fresh session per run keeps a re-run from being a re-send.
    session_id = f"test-forked-skill-routed-dispatches-{uuid.uuid4().hex[:8]}"
    sonnet, route = _SONNET, _ROUTE
    foreground, grouped = "a1f0re9r0und00000", "a19r0uped0000000"
    at = lambda minute, second=0: _TODAY + timedelta(minutes=minute, seconds=second)  # noqa: E731
    routed = {"model": sonnet, "route": route}
    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(_INTERACTION_SPAN_SQL, _TODAY, b"t" * 16, _id(f"int-{session_id}"), session_id)
        await _seed_inline_request(engine, session_id, at(5, 10), "prompt-inline", "req-inline",
                                   ("skill", "inlineme"), 1000, **routed)  # fmt: skip
        await _seed_inline_request(engine, session_id, at(7, 10), "prompt-fg", "req-fg", ("skill", "forkme"), 2000,
                                   agent_id=foreground, **routed)  # fmt: skip
        group = _id(f"group-{session_id}-{grouped}")
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO spans (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name, "
                "session_id, attrs, status_code, resource_id) VALUES ($1, 1, $2, $3, $4, 4, 'llm_request', $5, "
                "jsonb_build_object('agent_id', $6::text), 0, 0)",
                at(9, 10),
                b"t" * 16,
                _id(f"llm-{session_id}-{grouped}"),
                group,
                session_id,
                grouped,
            )
        await _seed_inline_request(engine, session_id, at(9, 10), "prompt-grouped", "req-grouped",
                                   ("skill", "groupme"), 4000, agent_id=grouped, span=group, **routed)  # fmt: skip
        await _ingest(
            engine,
            [
                _dispatch(session_id, at(5), "prompt-inline", "inlineme"),
                _dispatch(session_id, at(7), "prompt-fg", "forkme"),
                _fork(session_id, at(7, 2), foreground, "prompt-fg"),
                _stop(session_id, at(7, 20), foreground),
                _dispatch(session_id, at(9), "prompt-grouped", "groupme"),
                _fork(session_id, at(9, 2), grouped, "prompt-grouped"),
                _stop(session_id, at(9, 20), grouped),
            ],
        )
        await _refresh(engine, _TODAY.date())

        detail = await _detail(engine, session_id, [_entry(sonnet, _SONNET_RATES)], {route: _cost(_ROUTE_RATES)})
        rows = await PostgresCliAnalyticsReader(engine).get_session_detail_dispatches(session_id, scope=DETAIL_SCOPE)
    finally:
        await _cleanup(engine, [session_id])
        await engine.close()

    assert sorted((r["skill_name"], [m["model_name"] for m in r["models"]]) for r in rows if r["skill_name"]) == [
        ("forkme", [route]),
        ("groupme", [route]),
        ("inlineme", [route]),
    ]
    assert sorted((d["label"], d["cost_usd"]) for d in detail["dispatches"]) == [
        ("command · forkme", pytest.approx(0.0066, abs=1e-12)),  # 2 000 input at 3.3e-06
        ("command · groupme", pytest.approx(0.0132, abs=1e-12)),  # 4 000 input at 3.3e-06
        ("command · inlineme", pytest.approx(0.0033, abs=1e-12)),  # 1 000 input at 3.3e-06
        ("session", pytest.approx(0.0231, abs=1e-12)),
    ]
    assert detail["cost_usd"] == pytest.approx(0.0231, abs=1e-12)
    commands = [d["cost_usd"] for d in detail["dispatches"] if d["kind"] == "command"]
    assert sum(commands) == pytest.approx(detail["cost_usd"], abs=1e-12)
