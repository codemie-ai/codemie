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

"""Live-Postgres checks for EPMCDME-16441: a subagent's totals-only usage (session_usage rows with
attrs.token_source = 'subagent_usage') is part of the session money rows, priced at query time.

Skipped when the local analytics Postgres named by `TEST_LOCAL_PG_URL` is unreachable.
"""

import inspect
import json
import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import rollups
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.analytics.handlers.session_money import price_session_models
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE, EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_DAY = date(2026, 8, 2)
_NOW = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)
_START = _NOW - timedelta(minutes=10)
_KNOWN_MODEL = "claude-sonnet-4-5"
_COST_MODEL = "claude-haiku-4-5"
_COST_MODEL_2 = "claude-opus-4-5"

_PRICES = {
    _KNOWN_MODEL: CostConfig(input=0.000003, output=0.000015, cache_read_input_token_cost=0.0000003),
    _COST_MODEL: CostConfig(input=0.000003, output=0.000015, cache_read_input_token_cost=0.0000003),
    _COST_MODEL_2: CostConfig(input=0.000003, output=0.000015, cache_read_input_token_cost=0.0000003),
}


def _lookup(model: str) -> CostConfig | None:
    return _PRICES.get(model)


_API_REQUEST_SQL = """
    INSERT INTO log_events
        (ts, session_id, event_kind, cost_usd, input_tokens, output_tokens, cache_read_tokens,
         cache_creation_tokens, severity, resource_id)
    VALUES ($1, $2, 1, $3, $4, $5, $6, $7, 9, 1)
"""


async def _get_engine() -> AnalyticsPgEngine:
    try:
        engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL)))
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


async def _cleanup(engine: AnalyticsPgEngine, session_id: str) -> None:
    async with engine.acquire() as conn:
        for table in (
            "hook_events",
            "log_events",
            "cost_daily",
            "session_dims",
            "rollup_dirty",
            "session_usage",
            "turns_daily",  # rolled up from a prompt event
        ):
            await conn.execute(f"DELETE FROM {table} WHERE session_id = $1", session_id)  # noqa: S608


async def _seed_start(engine: AnalyticsPgEngine, session_id: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute(
            "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.session.start')",
            _START,
            session_id,
        )


async def _seed_prompt(engine: AnalyticsPgEngine, session_id: str) -> None:
    """A genuine activity signal without any cost: the window admits a session that did something, and leaves out
    a lifecycle-only start (EPMCDME-16496)."""
    async with engine.acquire() as conn:
        await conn.execute(
            "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.prompt.submit')",
            _START + timedelta(seconds=5),
            session_id,
        )


async def _seed_totals(engine: AnalyticsPgEngine, session_id: str, usage: list[dict], agent_id: str = "ag1") -> None:
    async with engine.acquire() as conn:
        await conn.execute(
            "INSERT INTO hook_events (ts, session_id, event_type, agent_id, attrs)"
            " VALUES ($1, $2, 'agent.subagent.usage', $3, $4)",
            _START + timedelta(seconds=20),
            session_id,
            agent_id,
            json.dumps({"usage": usage}),
        )


async def _seed_requests(engine: AnalyticsPgEngine, session_id: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute(_API_REQUEST_SQL, _START + timedelta(seconds=10), session_id, 0.10, 1000, 200, 300, 50)


async def _refresh(engine: AnalyticsPgEngine) -> None:
    async with live_rollup_lock(engine):
        refresher = RollupRefresher(engine, batch_size=100)
        async with engine.acquire() as conn:
            await refresher.mark_dirty(_DAY, _DAY, conn)
        await refresher.refresh()


def _window() -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(
        scope=EXCLUDE_SCOPE, start_dt=_START - timedelta(days=1), end_dt=_NOW + timedelta(days=1)
    )


_USAGE = {
    "model": _KNOWN_MODEL,
    "input_tokens": 1000,
    "output_tokens": 200,
    "cache_read_tokens": 300,
    "cache_creation_5m_tokens": 40,
    "cache_creation_1h_tokens": 10,
}


@pytest.mark.asyncio
async def test_totals_only_agent_adds_priced_cost_to_session_list_and_detail():
    engine = await _get_engine()
    sid = "test-epmcdme-16441-totals-only"
    try:
        await _cleanup(engine, sid)
        await _seed_start(engine, sid)
        await _seed_totals(engine, sid, [_USAGE])
        await _refresh(engine)

        reader = PostgresCliAnalyticsReader(engine)
        rows = [r for r in await reader.get_session_model_costs(_window()) if r["session_id"] == sid]
        assert [r["model_name"] for r in rows] == [_KNOWN_MODEL]
        assert rows[0]["input_tokens"] == 1000
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            assert price_session_models(rows).by_session[sid] > 0

        detail = await reader.get_session_detail_model_costs(sid, scope=DETAIL_SCOPE)
        assert [r["model_name"] for r in detail] == [_KNOWN_MODEL]
        assert detail[0]["output_tokens"] == 200

        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            handler_detail, _, _ = await LocalAnalyticsHandler(reader).get_session_detail(sid, scope=DETAIL_SCOPE)
        assert handler_detail["cost_available"] is True
    finally:
        await _cleanup(engine, sid)
        await engine.close()


@pytest.mark.asyncio
async def test_subagent_totals_of_a_personal_paid_work_session_stay_out_of_spend_but_in_its_own_cost():
    """EPMCDME-16440 with EPMCDME-16441: the totals-only subagent rows follow their session. A Personal-paid session
    stored under a work project is activity, its subagent money is no aggregate spend, and the session keeps it."""
    engine = await _get_engine()
    sid = "test-epmcdme-16440-personal-paid-subagent"
    try:
        await _cleanup(engine, sid)
        await _seed_start(engine, sid)
        await _seed_totals(engine, sid, [_USAGE])
        await _refresh(engine)
        async with engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET owner_email = 'ext@x.com', subscription_type = 'personal',"
                " project_origin = 'default_project', project_name = 'work' WHERE session_id = $1",
                sid,
            )

        reader = PostgresCliAnalyticsReader(engine)
        listed = [r for r in await reader.get_session_model_costs(_window()) if r["session_id"] == sid]
        spend = [r for r in await reader.get_session_model_costs(_window(), spend_only=True) if r["session_id"] == sid]
        assert [r["input_tokens"] for r in listed] == [1000]  # the session itself keeps its subagent tokens
        assert spend == []  # but no aggregate money is priced from them
    finally:
        await _cleanup(engine, sid)
        await engine.close()


@pytest.mark.asyncio
async def test_agent_with_request_rows_is_not_double_counted_after_late_requests():
    engine = await _get_engine()
    sid = "test-epmcdme-16441-late-requests"
    try:
        await _cleanup(engine, sid)
        await _seed_start(engine, sid)
        await _seed_totals(engine, sid, [_USAGE])
        await _refresh(engine)
        await _seed_requests(engine, sid)
        await _refresh(engine)

        reader = PostgresCliAnalyticsReader(engine)
        rows = [r for r in await reader.get_session_model_costs(_window()) if r["session_id"] == sid]
        assert sum(r["input_tokens"] for r in rows) == 1000  # the request rows only
        assert all(r["model_name"] != _KNOWN_MODEL for r in rows)
        detail = await reader.get_session_detail_model_costs(sid, scope=DETAIL_SCOPE)
        assert sum(r["input_tokens"] for r in detail) == 1000
    finally:
        await _cleanup(engine, sid)
        await engine.close()


@pytest.mark.asyncio
async def test_unknown_totals_model_prices_zero_and_logs_unpriced():
    engine = await _get_engine()
    sid = "test-epmcdme-16441-unknown-model"
    try:
        await _cleanup(engine, sid)
        await _seed_start(engine, sid)
        await _seed_totals(engine, sid, [{**_USAGE, "model": "not-a-real-model-xyz"}])
        await _refresh(engine)

        rows = [
            r
            for r in await PostgresCliAnalyticsReader(engine).get_session_model_costs(_window())
            if r["session_id"] == sid
        ]
        with (
            patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup),
            patch("codemie.service.analytics.handlers.session_money.logger") as log,
        ):
            money = price_session_models(rows)
        assert money.by_session[sid] == 0.0
        assert log.warning.call_args.args[1] == ["not-a-real-model-xyz"]
    finally:
        await _cleanup(engine, sid)
        await engine.close()


@pytest.mark.asyncio
async def test_totals_without_cache_split_keep_5m_1h_null():
    engine = await _get_engine()
    sid = "test-epmcdme-16441-no-split"
    usage = {"model": _KNOWN_MODEL, "input_tokens": 500, "output_tokens": 50, "cache_read_tokens": 5}
    try:
        await _cleanup(engine, sid)
        await _seed_start(engine, sid)
        await _seed_totals(engine, sid, [usage])
        await _refresh(engine)

        reader = PostgresCliAnalyticsReader(engine)
        rows = [r for r in await reader.get_session_model_costs(_window()) if r["session_id"] == sid]
        assert rows[0]["input_tokens"] == 500
        assert rows[0]["cache_creation_5m_tokens"] is None
        assert rows[0]["cache_creation_1h_tokens"] is None
        detail = await reader.get_session_detail_model_costs(sid, scope=DETAIL_SCOPE)
        assert detail[0]["cache_creation_5m_tokens"] is None
        assert detail[0]["cache_creation_1h_tokens"] is None
    finally:
        await _cleanup(engine, sid)
        await engine.close()


@pytest.mark.asyncio
async def test_subagent_tokens_do_not_leak_when_a_filter_excludes_the_session():
    engine = await _get_engine()
    sid = "test-epmcdme-16441-filtered-out"
    try:
        await _cleanup(engine, sid)
        await _seed_start(engine, sid)
        await _seed_totals(engine, sid, [_USAGE])
        await _refresh(engine)

        reader = PostgresCliAnalyticsReader(engine)
        assert [r for r in await reader.get_session_model_costs(_window()) if r["session_id"] == sid]
        outside_window = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE, start_dt=_NOW + timedelta(days=2), end_dt=_NOW + timedelta(days=3)
        )
        other_repo = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE, start_dt=_window().start_dt, end_dt=_window().end_dt, repositories=["no-such-repo"]
        )
        for excluded in (outside_window, other_repo):
            rows = await reader.get_session_model_costs(excluded)
            assert [r for r in rows if r["session_id"] == sid] == []
    finally:
        await _cleanup(engine, sid)
        await engine.close()


@pytest.mark.asyncio
async def test_totals_only_session_lists_tokens_and_cost_available_end_to_end():
    engine = await _get_engine()
    with_totals, without_totals = "test-epmcdme-16441-e2e-totals", "test-epmcdme-16441-e2e-none"
    lifecycle_only = "test-epmcdme-16441-e2e-lifecycle"
    try:
        for sid in (with_totals, without_totals, lifecycle_only):
            await _cleanup(engine, sid)
            await _seed_start(engine, sid)
        await _seed_prompt(engine, without_totals)  # active, but no cost and no totals
        await _seed_totals(engine, with_totals, [_USAGE])
        await _refresh(engine)

        reader = PostgresCliAnalyticsReader(engine)
        facts = {r["session_id"]: r for r in await reader.get_session_cost_facts(_window(), include_costless=True)}
        assert lifecycle_only not in facts  # a start with no activity is not a session, whatever its cost
        assert facts[with_totals]["input_tokens"] == 1000
        assert facts[with_totals]["output_tokens"] == 200
        assert facts[with_totals]["cache_read_tokens"] == 300
        assert facts[without_totals]["input_tokens"] in (None, 0)

        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            result, _, _ = await LocalAnalyticsHandler(reader).get_sessions(_window(), 0, 1000, "start_time", None)
        by_id = {s["trace_id"]: s for s in result["sessions"]}
        assert by_id[with_totals]["cost_available"] is True
        assert by_id[with_totals]["cost_usd"] > 0
        assert by_id[with_totals]["input_tokens"] == 1000
        assert by_id[without_totals]["cost_available"] is False
        assert lifecycle_only not in by_id
    finally:
        for sid in (with_totals, without_totals, lifecycle_only):
            await _cleanup(engine, sid)
        await engine.close()


@pytest.mark.asyncio
async def test_totals_only_session_detail_tokens_match_the_list_tokens():
    engine = await _get_engine()
    sid = "test-epmcdme-16441-detail-tokens"
    try:
        await _cleanup(engine, sid)
        await _seed_start(engine, sid)
        await _seed_totals(engine, sid, [_USAGE])
        await _refresh(engine)

        reader = PostgresCliAnalyticsReader(engine)
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            listed, _, _ = await LocalAnalyticsHandler(reader).get_sessions(_window(), 0, 1000, "start_time", None)
            detail, _, _ = await LocalAnalyticsHandler(reader).get_session_detail(sid, scope=DETAIL_SCOPE)
        row = next(s for s in listed["sessions"] if s["trace_id"] == sid)
        columns = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
        assert [detail[c] for c in columns] == [row[c] for c in columns]
        assert [detail[c] for c in columns[:3]] == [1000, 200, 300]
    finally:
        await _cleanup(engine, sid)
        await engine.close()


@pytest.mark.asyncio
async def test_totals_are_counted_once_for_a_session_with_cost_rows_for_two_models_on_two_days():
    engine = await _get_engine()
    sid, no_totals, costless = (
        "test-epmcdme-16441-many-rows",
        "test-epmcdme-16441-no-totals",
        "test-epmcdme-16441-costless",
    )
    insert_cost = """
        INSERT INTO cost_daily
            (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, api_call_count)
        VALUES ($1, $2, 'many-rows@example.com', $3, 'repl_main_thread', $4, $5, $6, $7, 0, 1)
    """
    day_before = _DAY - timedelta(days=1)
    try:
        for session in (sid, no_totals, costless):
            await _cleanup(engine, session)
            await _seed_start(engine, session)
        await _seed_prompt(engine, costless)  # active (a prompt), with no cost rows and no totals
        await _seed_totals(engine, sid, [_USAGE])
        await _refresh(engine)
        async with engine.acquire() as conn:
            # 2 models x 2 days = 4 cost rows: summing the joined totals over them would count the totals 4 times
            await conn.execute(insert_cost, day_before, sid, _COST_MODEL, 0.01, 100, 10, 5)
            await conn.execute(insert_cost, _DAY, sid, _COST_MODEL, 0.02, 200, 20, 7)
            await conn.execute(insert_cost, day_before, sid, _COST_MODEL_2, 0.03, 40, 4, 3)
            await conn.execute(insert_cost, _DAY, sid, _COST_MODEL_2, 0.04, 60, 6, 2)
            await conn.execute(insert_cost, _DAY, no_totals, _COST_MODEL, 0.05, 11, 2, 3)

        reader = PostgresCliAnalyticsReader(engine)
        facts = {r["session_id"]: r for r in await reader.get_session_cost_facts(_window(), include_costless=True)}
        assert facts[sid]["input_tokens"] == 1400  # 100 + 200 + 40 + 60 over the four rows, plus the totals' 1000 once
        assert facts[sid]["output_tokens"] == 240  # 10 + 20 + 4 + 6, plus 200 once
        assert facts[sid]["cache_read_tokens"] == 317  # 5 + 7 + 3 + 2, plus 300 once
        assert facts[sid]["cost_usd"] == pytest.approx(0.10)  # 0.01 + 0.02 + 0.03 + 0.04 over all four rows
        assert facts[no_totals]["input_tokens"] == 11  # no totals: the cost row only
        assert facts[no_totals]["cache_read_tokens"] == 3
        assert facts[no_totals]["cost_usd"] == pytest.approx(0.05)
        assert facts[costless]["input_tokens"] == 0  # no cost rows and no totals: listed with 0 tokens
        assert facts[costless]["cache_read_tokens"] == 0
        assert facts[costless]["cost_usd"] is None

        rows = {r["model_name"]: r for r in await reader.get_session_model_costs(_window()) if r["session_id"] == sid}
        assert rows[_COST_MODEL]["input_tokens"] == 300
        assert rows[_COST_MODEL]["output_tokens"] == 30
        assert rows[_COST_MODEL_2]["input_tokens"] == 100
        assert rows[_KNOWN_MODEL]["input_tokens"] == 1000
        assert rows[_KNOWN_MODEL]["output_tokens"] == 200
        assert rows[_KNOWN_MODEL]["cache_read_tokens"] == 300

        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            result, _, _ = await LocalAnalyticsHandler(reader).get_sessions(_window(), 0, 1000, "start_time", None)
        listed = {s["trace_id"]: s for s in result["sessions"]}
        assert listed[sid]["input_tokens"] == 1400
        assert listed[sid]["cost_usd"] == pytest.approx(
            0.0078951
        )  # 0.00609 for the totals once + 0.0018051 for the rows
        assert listed[no_totals]["input_tokens"] == 11
        assert listed[costless]["cost_available"] is False
    finally:
        for session in (sid, no_totals, costless):
            await _cleanup(engine, session)
        await engine.close()


def test_totals_are_a_source_is_unchanged():
    """The writer-side guard is the only double-count protection: the reader adds none (EPMCDME-16441)."""
    src = inspect.getsource(rollups._totals_are_a_source)
    assert "NOT EXISTS (SELECT 1 FROM usage_requests u" in src
    assert "su.attrs->>'token_source', '') <> 'subagent_usage'" in src
    assert "_otel_usage_exists(session_id)" in src
