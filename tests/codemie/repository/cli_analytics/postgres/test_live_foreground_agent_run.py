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

"""Live-Postgres check for EPMCDME-16939 on a real capture: the Session Detail row of a foreground Agent call.

`support/foreground-agent-run.json` is a sanitized capture of one Claude Code session (hook events, spans and the
transcript's usage requests as the CLI sent them): the model calls the Agent tool once in the foreground, the
subagent makes one request and stops, the Agent call returns, and the main thread makes one more request. The rows
are inserted as they are, the API-side `api_request` log rows are derived from the capture's LLM request spans (the
capture holds none), the rollups are refreshed by the real refresher, and `LocalAnalyticsHandler.get_session_detail`
answers. The expected figures are computed here from the capture and the rates below. Skipped when
`TEST_LOCAL_PG_URL` is unset or unreachable.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.support.live_engine import get_live_engine as _engine
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_LOCAL_PG_URL"), reason="TEST_LOCAL_PG_URL is not set: no database to run against"
)

_FIXTURE = Path(__file__).resolve().parents[1] / "support" / "foreground-agent-run.json"
_SESSION = "test-epmcdme-16939-live-foreground-run"
_PRICING = "codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config"
# input, output, cache read, cache write 5m, cache write 1h per token
_RATES = (1e-6, 5e-6, 1e-7, 1.25e-6, 2e-6)
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
_API_REQUEST = """INSERT INTO log_events
    (ts, session_id, event_kind, model, cost_usd, input_tokens, output_tokens, cache_read_tokens,
     cache_creation_tokens, trace_id, span_id, severity, resource_id, attrs)
    VALUES ($1, $2, 1, $3, 0, $4, $5, $6, $7, $8, $9, 9, 1, $10::jsonb)"""


# The token sums the session totals read: cost_daily, the one token rollup of the capture (it holds no subagent
# totals-only row of session_usage)
_STORED_TOKENS = """SELECT input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
    cache_creation_5m_tokens, cache_creation_1h_tokens FROM cost_daily WHERE session_id = $1"""


def _lookup(model: str) -> CostConfig | None:
    if "haiku-5-5" not in model:
        return None
    return CostConfig(
        input=_RATES[0],
        output=_RATES[1],
        cache_read_input_token_cost=_RATES[2],
        cache_creation_input_token_cost=_RATES[3],
        cache_creation_input_token_cost_above_1hr=_RATES[4],
    )


def _price(input_tokens: int, output_tokens: int, read: int, written_5m: int, written_1h: int) -> float:
    return (
        input_tokens * _RATES[0]
        + output_tokens * _RATES[1]
        + read * _RATES[2]
        + written_5m * _RATES[3]
        + written_1h * _RATES[4]
    )


def _capture() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _value(column: str, value: Any) -> Any:
    """A capture field as the column takes it: timestamps parsed, `attrs` as JSON text, span ids as bytes."""
    if value is None:
        return None
    if column == "ts":
        return datetime.fromisoformat(value)
    if column == "attrs":
        return json.dumps(value)
    if column in ("trace_id", "span_id", "parent_span_id"):
        return bytes.fromhex(value)
    return value


async def _insert(engine: AnalyticsPgEngine, table: str, rows: list[dict[str, Any]], **extra: Any) -> None:
    """Insert capture rows into `table` by their own column names (a trusted file, so the names are not quoted)."""
    async with engine.acquire() as conn:
        for row in rows:
            row = {**row, "session_id": _SESSION, **extra}
            columns = list(row)
            marks = ", ".join(f"${n}" for n in range(1, len(columns) + 1))
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})"  # noqa: S608
            await conn.execute(sql, *(_value(column, row[column]) for column in columns))


async def _cleanup(engine: AnalyticsPgEngine) -> None:
    async with engine.acquire() as conn:
        for table in _TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE session_id = $1", _SESSION)  # noqa: S608


async def _seed(engine: AnalyticsPgEngine, capture: dict[str, Any]) -> date:
    await _insert(engine, "hook_events", capture["hook_events"])
    await _insert(engine, "spans", capture["spans"], resource_id=1)
    await _insert(engine, "usage_requests", capture["usage_requests"])
    # The API-side api_request row of each LLM request span: grouped under the span's parent, as the subagent's are
    # under the tool execution span, and carrying the tokens of the transcript request made at that time
    # (a subagent's by its agentId, the main thread's in order).
    main_requests = iter(sorted((r for r in capture["usage_requests"] if not r["agent_id"]), key=lambda r: r["ts"]))
    async with engine.acquire() as conn:
        for span in sorted((s for s in capture["spans"] if s["span_name"] == "claude_code.llm_request"), key=_ts_of):
            agent_id = span["attrs"].get("agent_id")
            request = (
                next(r for r in capture["usage_requests"] if r["agent_id"] == agent_id)
                if agent_id
                else next(main_requests)
            )
            written = {k: request[k] for k in ("cache_creation_5m_tokens", "cache_creation_1h_tokens")}
            await conn.execute(
                _API_REQUEST,
                datetime.fromisoformat(request["ts"]),
                _SESSION,
                request["model"],
                request["input_tokens"],
                request["output_tokens"],
                request["cache_read_tokens"],
                sum(written.values()),
                bytes.fromhex(span["trace_id"]),
                bytes.fromhex(span["parent_span_id"]),
                json.dumps(written),
            )
    return datetime.fromisoformat(capture["hook_events"][0]["ts"]).date()


def _ts_of(item: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(item["ts"])


async def _refresh(engine: AnalyticsPgEngine, day: date) -> None:
    async with live_rollup_lock(engine):
        refresher = RollupRefresher(engine, batch_size=100, clock=lambda: day)
        async with engine.acquire() as conn:
            await refresher.mark_dirty(day, day, conn)
        await refresher.refresh()


@pytest.mark.asyncio
async def test_live_a_foreground_agent_row_shows_the_subagents_own_run_and_requests_never_the_parents():
    """The row of the one Agent call: a duration within a second of the SubagentStop's own `duration_ms`, and the
    tokens, cost and model of the subagent's one request only; the main thread's requests, the later one (logged
    after the Agent call returned) included, are not on it."""
    capture = _capture()
    (stop,) = [h for h in capture["hook_events"] if h["event_type"] == "agent.subagent.stop"]
    stop_duration_ms = stop["attrs"]["duration_ms"]
    (own,) = [r for r in capture["usage_requests"] if r["agent_id"]]
    parents = [r for r in capture["usage_requests"] if not r["agent_id"]]
    assert len(parents) == 2 and _ts_of(parents[-1]) > _ts_of(
        next(h for h in capture["hook_events"] if h["event_type"] == "agent.tool.end")
    )  # the capture holds the parent request logged after the Agent call returned
    engine = await _engine()
    try:
        await _cleanup(engine)
        day = await _seed(engine, capture)
        await _refresh(engine, day)

        reader = PostgresCliAnalyticsReader(engine)
        (row,) = await reader.get_session_detail_dispatches(_SESSION, scope=DETAIL_SCOPE)
        with patch(_PRICING, _lookup):
            detail, _, _ = await LocalAnalyticsHandler(reader).get_session_detail(_SESSION, scope=DETAIL_SCOPE)
        async with engine.acquire() as conn:
            stored = [
                {key: value or 0 for key, value in dict(record).items()}
                for record in await conn.fetch(_STORED_TOKENS, _SESSION)
            ]
            api_requests = await conn.fetchval(
                "SELECT count(*) FROM log_events WHERE session_id = $1 AND event_kind = 1", _SESSION
            )
    finally:
        await _cleanup(engine)
        await engine.close()

    assert detail is not None
    expected_models = [
        {
            "model_name": own["model"],
            "input_tokens": own["input_tokens"],
            "output_tokens": own["output_tokens"],
            "cache_read_tokens": own["cache_read_tokens"],
            "cache_creation_tokens": own["cache_creation_5m_tokens"] + own["cache_creation_1h_tokens"],
            "cache_creation_5m_tokens": own["cache_creation_5m_tokens"],
            "cache_creation_1h_tokens": own["cache_creation_1h_tokens"],
        }
    ]
    assert row["models"] == expected_models
    # the row's run time is the stop's own duration_ms, not the stop row's lagging timestamp minus the start
    assert row["subagent_duration_ms"] == stop_duration_ms
    launch = next(h for h in capture["hook_events"] if h["event_type"] == "agent.subagent.start")
    by_timestamps = (_ts_of(stop) - _ts_of(launch)).total_seconds() * 1000
    assert abs(by_timestamps - stop_duration_ms) <= 1000  # the capture's own two readings agree within a second
    # the bar's start is anchored to the stop minus duration_ms, not to SubagentStart: compared against the
    # capture's own stop timestamp and duration_ms directly, not against row["subagent_duration_ms"] (whose
    # equality to dispatch_start + duration would hold by construction from the same SQL CASE, whatever
    # stop_duration_ms turned out to be, and so could not catch dispatch_start reading a different one)
    expected_start = _ts_of(stop).astimezone(timezone.utc).replace(tzinfo=None) - timedelta(
        milliseconds=stop_duration_ms
    )
    assert row["dispatch_start"] == expected_start

    session_row, agent_row = detail["dispatches"]
    assert session_row["kind"] == "session" and agent_row["kind"] == "agent"
    assert abs(agent_row["duration_ms"] - stop_duration_ms) <= 1000
    assert agent_row["input_tokens"] == own["input_tokens"]
    assert agent_row["output_tokens"] == own["output_tokens"]
    assert agent_row["cache_read_tokens"] == (own["cache_read_tokens"] or None)  # the API shows a zero as none
    assert agent_row["cache_creation_tokens"] == own["cache_creation_5m_tokens"] + own["cache_creation_1h_tokens"]
    assert agent_row["cost_usd"] == pytest.approx(
        _price(
            own["input_tokens"],
            own["output_tokens"],
            own["cache_read_tokens"],
            own["cache_creation_5m_tokens"],
            own["cache_creation_1h_tokens"],
        )
    )
    # none of the parent's requests is on the row (the figures above are the subagent's alone)
    assert all(agent_row["output_tokens"] != parent["output_tokens"] for parent in parents)

    # B5: the session totals are the sum of every stored request (the subagent's and the parent's), read from the
    # rollup rows themselves, whatever the dispatch rows attribute to the Agent call
    assert detail["input_tokens"] == sum(r["input_tokens"] for r in stored)
    assert detail["output_tokens"] == sum(r["output_tokens"] for r in stored)
    assert detail["cache_read_tokens"] == sum(r["cache_read_tokens"] for r in stored)
    assert detail["cache_creation_tokens"] == sum(r["cache_creation_tokens"] for r in stored)
    assert detail["cost_usd"] == pytest.approx(
        sum(
            _price(
                r["input_tokens"],
                r["output_tokens"],
                r["cache_read_tokens"],
                r["cache_creation_5m_tokens"],
                r["cache_creation_1h_tokens"],
            )
            for r in stored
        )
    )
    assert sum(1 for event in detail["events"] if event["event_type"] == "api_request") == api_requests
