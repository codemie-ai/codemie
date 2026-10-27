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

"""Money on the aggregate surfaces, read from a disposable analytics Postgres and priced at a mocked catalog.

`cost_daily` rows are inserted directly with a producer `cost_usd` that differs from the catalog price; the real
reader and handler must answer the catalog money. Without TEST_LOCAL_PG_URL every test is skipped; with it a test
is skipped (not failed) when that Postgres is unreachable. Only the test's own sessions are deleted afterwards.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import os

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")
# Any constant shared by the xdist workers: start() on an empty schema must run in one of them at a time.
_SCHEMA_LOCK = 0x6D6F6E6579

_PRICING = "codemie.service.analytics.handlers.coding_agent_pricing.llm_service"
_DIMS = """INSERT INTO session_dims (session_id, started_at, last_event_at, jwt_email, repository)
    VALUES ($1, $2, $2, $3, $4)"""
_COST = """INSERT INTO cost_daily
    (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
     cache_read_tokens, cache_creation_tokens, api_call_count, cache_creation_5m_tokens, cache_creation_1h_tokens)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 1, $11, $12)"""
_EVENT = """INSERT INTO log_events
    (ts, session_id, event_kind, span_id, model, cost_usd, input_tokens, output_tokens, cache_read_tokens,
     cache_creation_tokens, severity, resource_id, attrs)
    VALUES ($1, $2, 1, $3, 'claude-opus-5-5', $4, $5, $6, $7, $8, 0, 1, $9::jsonb)"""
_SPAN = """INSERT INTO spans
    (ts, duration_ns, trace_id, span_id, parent_span_id, span_kind, span_name, session_id, subagent_type, tool_name,
     tool_use_id, attrs, success, status_code, resource_id)
    VALUES ($1, 1000000, 'trace'::bytea, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, 'true', 0, 1)"""
_AGENT_HOOK = """INSERT INTO hook_events (ts, session_id, event_type, tool_use_id, tool_name, attrs)
    VALUES ($1, $2, $3, 'tu-explore', 'Agent', $4::jsonb)"""
# The ticket's Opus 5.5 session as three api_request events (input, output, cache read, cache write at 1h); their
# sums are its cost_daily row. The last one is the Explore subagent's.
_DETAIL_EVENTS = (
    (124, 22_161, 13_452_179, 629_636),
    (125, 22_161, 13_452_179, 629_636),
    (125, 22_161, 13_452_180, 629_636),
)
_LINES = """INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, lines_removed)
    VALUES ($1, $2, $3, 'claude-sonnet-4-6', $4, $5)"""


def _catalog():
    """Opus 5.5 at 4.4 / 22 / 0.22 / 5.5 / 8.8 per MTok and Sonnet 4.6 at 3.3 / 16.5 / 0.33 / 4.125 / 6.6."""
    models = []
    for name, prices in (
        ("claude-opus-5-5", (4.4e-6, 22e-6, 0.22e-6, 5.5e-6, 8.8e-6)),
        ("claude-sonnet-4-6", (3.3e-6, 16.5e-6, 0.33e-6, 4.125e-6, 6.6e-6)),
    ):
        model = MagicMock()
        model.base_name = model.deployment_name = name
        model.cost = CostConfig(
            input=prices[0],
            output=prices[1],
            cache_read_input_token_cost=prices[2],
            cache_creation_input_token_cost=prices[3],
            cache_creation_input_token_cost_above_1hr=prices[4],
        )
        models.append(model)
    return models


class _Seed:
    def __init__(self) -> None:
        run = uuid.uuid4().hex[:8]
        self.opus, self.sonnet, self.extra, self.split, self.lines_only, self.detail = (
            f"money-{kind}-{run}" for kind in ("opus", "sonnet", "extra", "split", "lines-only", "detail")
        )
        self.alice, self.bob, self.carol, self.dave, self.erin, self.frank, self.gina = (
            f"{name}-{run}@example.com" for name in ("alice", "bob", "carol", "dave", "erin", "frank", "gina")
        )
        self.split_repository = f"repo-split-{run}"
        self.lines_only_repository = f"repo-lines-only-{run}"
        self.started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)

    def window(self, *users: str, repositories: list[str] | None = None) -> LocalAnalyticsFilter:
        start = self.started.replace(tzinfo=None)
        return LocalAnalyticsFilter(
            start_dt=start - timedelta(hours=1),
            end_dt=start + timedelta(hours=1),
            users=list(users),
            repositories=repositories or [],
        )

    async def insert(self, conn: asyncpg.Connection) -> None:
        day = self.started.date()
        for session, email, repository in (
            (self.opus, self.alice, "repo-money"),
            (self.sonnet, self.bob, "repo-money"),
            (self.extra, self.carol, ""),
            (self.split, None, self.split_repository),
            (self.lines_only, None, self.lines_only_repository),
            (self.detail, None, ""),
        ):
            await conn.execute(_DIMS, session, self.started, email, repository)
        rows = [
            # The ticket's Opus 5.5 session, every cache write at 1h; the producer charged 24.5.
            (
                self.opus,
                self.alice,
                "claude-opus-5-5",
                "repl_main_thread",
                24.5,
                374,
                66_483,
                40_356_538,
                1_888_908,
                None,
                1_888_908,
            ),
            # The Sonnet 4.6 session, every cache write at 1h; the producer charged 0.3.
            (
                self.sonnet,
                self.bob,
                "us.anthropic.claude-sonnet-4-6",
                "repl_main_thread",
                0.3,
                6,
                708,
                107_215,
                41_644,
                0,
                41_644,
            ),
            # A request without a model.
            (self.extra, self.carol, "", "repl_main_thread", 0.1, 0, 5, 0, 0, None, None),
            # A cache write without a 5m / 1h split: priced at the 5m price.
            (self.extra, self.carol, "claude-sonnet-4-6", "repl_main_thread", 0.1, 0, 0, 0, 1_000, None, None),
            # A session without an email of its own whose rows carry two emails.
            (self.split, self.erin, "claude-sonnet-4-6", "repl_main_thread", 0.1, 0, 2_000, 0, 0, None, None),
            (self.split, self.dave, "claude-sonnet-4-6", "repl_main_thread", 0.1, 0, 1_000, 0, 0, None, None),
            # The Session Detail session: the ticket's Opus 5.5 tokens, every cache write at 1h.
            (
                self.detail,
                self.gina,
                "claude-opus-5-5",
                "repl_main_thread",
                24.5,
                374,
                66_483,
                40_356_538,
                1_888_908,
                0,
                1_888_908,
            ),
        ]
        for session, email, model, source, *values in rows:
            await conn.execute(_COST, day, session, email, model, source, *values)
        # The split session's lines carry only erin, its cost rows dave and erin; the lines-only session has no
        # cost rows at all.
        for session, email, added, removed in ((self.split, self.erin, 40, 7), (self.lines_only, self.frank, 7, 1)):
            await conn.execute(_LINES, day, session, email, added, removed)
        await self._insert_detail_events(conn)

    async def _insert_detail_events(self, conn: asyncpg.Connection) -> None:
        """The detail session's requests: two of the main thread, one of an Explore subagent named by its agentId
        (its Agent call's span and hook events, its llm_request span grouping its api_request event). Each event
        carries the producer cost 8.0 and its 5m / 1h split in attrs."""
        at = self.started + timedelta(minutes=1)
        await conn.execute(
            _SPAN, at, b"dispatch", b"main", 1, "claude_code.tool", self.detail, "Explore", "Agent", "tu-explore", None
        )
        await conn.execute(_AGENT_HOOK, at, self.detail, "agent.tool.start", None)
        response = json.dumps({"isAsync": False, "agentId": "agentexplore", "status": "completed"})
        await conn.execute(
            _AGENT_HOOK,
            at + timedelta(seconds=10),
            self.detail,
            "agent.tool.end",
            json.dumps({"tool_response": response}),
        )
        await conn.execute(
            _SPAN,
            at + timedelta(seconds=1),
            b"llm-explore",
            b"sub-explore",
            4,
            "claude_code.llm_request",
            self.detail,
            "",
            "",
            "",
            json.dumps({"agent_id": "agentexplore"}),
        )
        for i, (inp, out, read, write) in enumerate(_DETAIL_EVENTS):
            span = b"sub-explore" if i == len(_DETAIL_EVENTS) - 1 else b"main"
            split = json.dumps({"cache_creation_5m_tokens": 0, "cache_creation_1h_tokens": write})
            await conn.execute(
                _EVENT, at + timedelta(seconds=2 + i), self.detail, span, 8.0, inp, out, read, write, split
            )

    async def delete(self, conn: asyncpg.Connection) -> None:
        for table in ("session_dims", "cost_daily", "lines_daily", "log_events", "spans", "hook_events"):
            await conn.execute(
                f"DELETE FROM {table} WHERE session_id = ANY($1::text[])",
                [self.opus, self.sonnet, self.extra, self.split, self.lines_only, self.detail],
            )


@asynccontextmanager
async def _seeded() -> AsyncIterator[tuple[LocalAnalyticsHandler, _Seed]]:
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_URL=_LOCAL_PG_URL))
    engine = AnalyticsPgEngine(settings)
    seed = _Seed()
    try:
        try:
            lock = await asyncpg.connect(settings.dsn)
        except (OSError, asyncpg.PostgresError) as exc:  # no reachable database: skip, do not fail
            pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
        try:
            await lock.execute("SELECT pg_advisory_lock($1)", _SCHEMA_LOCK)
            await PostgresAnalyticsRuntime(engine, settings).start()
        finally:
            await lock.close()  # closing the session releases its advisory lock
        async with engine.acquire() as conn:
            await seed.insert(conn)
        try:
            with (
                patch(f"{_PRICING}.get_all_llm_model_info", return_value=_catalog()),
                patch(f"{_PRICING}.catalog_source", return_value="litellm"),
            ):
                yield LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)), seed
        finally:
            async with engine.acquire() as conn:
                await seed.delete(conn)
    finally:
        await engine.close()


def _money(value: float) -> float:
    return pytest.approx(value, abs=1e-8)


@pytest.mark.asyncio
async def test_overview_and_efficiency_answer_catalog_money_not_the_producer_cost() -> None:
    async with _seeded() as (handler, seed):
        overview, pricer, _ = await handler.get_overview(seed.window(seed.alice))
        efficiency, _ = await handler.get_efficiency(seed.window(seed.alice))

    for kpis in (overview["kpis"], efficiency["kpis"]):
        assert kpis["total_cost_usd"] == _money(26.96510036)
        assert kpis["cache_read_cost_usd"] == _money(8.87843836)
        assert kpis["bloat_pct"] == 32.9
    assert efficiency["dead_sessions"]["wasted_cost_usd"] == _money(26.96510036)
    assert (pricer.unpriced, pricer.catalog_source) == ([], "litellm")


@pytest.mark.asyncio
async def test_cost_users_repositories_and_sessions_share_one_priced_basis() -> None:
    async with _seeded() as (handler, seed):
        window = seed.window(seed.alice, seed.bob)
        cost, _ = await handler.get_cost(window)
        users, _ = await handler.get_users(window, page=None, per_page=None)
        repositories, _ = await handler.get_repositories(window, 0, 20, False)
        sessions, _, _ = await handler.get_sessions(window, 0, 20, "cost_usd", None)
        overview, _, _ = await handler.get_overview(window)

    assert cost["kpis"]["total_cost_usd"] == _money(27.28703351)
    assert [(r["developer_name"], r["cost_usd"]) for r in cost["cost_by_user"]] == [
        (seed.alice, _money(26.96510036)),
        (seed.bob, _money(0.32193315)),
    ]
    assert [(r["model_name"], r["cost_usd"]) for r in cost["cost_by_model"]] == [
        ("claude-opus-5-5", _money(26.96510036)),
        ("claude-sonnet-4-6", _money(0.32193315)),
    ]
    assert [(r["developer_name"], r["cost_usd"]) for r in users["rows"]] == [
        (seed.alice, _money(26.96510036)),
        (seed.bob, _money(0.32193315)),
    ]
    assert [(r["repository"], r["cost_usd"]) for r in repositories["rows"]] == [("repo-money", _money(27.28703351))]
    assert [(s["trace_id"], s["cost_usd"]) for s in sessions["sessions"]] == [
        (seed.opus, _money(26.96510036)),
        (seed.sonnet, _money(0.32193315)),
    ]
    # Opus 8.87843836 + Sonnet 0.03538095
    assert overview["kpis"]["cache_read_cost_usd"] == _money(8.91381931)


@pytest.mark.asyncio
async def test_an_unsplit_cache_write_prices_at_5m_and_an_empty_model_is_unpriced() -> None:
    async with _seeded() as (handler, seed):
        cost, pricer = await handler.get_cost(seed.window(seed.carol))

    # 1000 cache-write tokens at the Sonnet 5m price 4.125 per MTok; the 5 output tokens without a model count 0.
    assert cost["kpis"]["total_cost_usd"] == _money(0.004125)
    assert pricer.unpriced == [""]


@pytest.mark.asyncio
async def test_a_session_whose_rows_carry_two_emails_belongs_to_one_developer() -> None:
    async with _seeded() as (handler, seed):
        window = seed.window(repositories=[seed.split_repository])
        cost, _ = await handler.get_cost(window)
        users, _ = await handler.get_users(window, page=None, per_page=None)
        cost_facts = await handler._repo.get_session_cost_facts(window)
        repositories, _ = await handler.get_repositories(window, 0, 20, False)
        sessions, _, _ = await handler.get_sessions(window, 0, 20, "cost_usd", None)
        daily = await handler._repo.get_users_daily_activity(window)
        lines = await handler._repo.get_lines_by_user(window)
        lines_only_window = seed.window(repositories=[seed.lines_only_repository])
        lines_only = await handler._repo.get_lines_by_user(lines_only_window)
        last_active = await handler._repo.get_users_last_active(window)
        lines_only_last_active = await handler._repo.get_users_last_active(lines_only_window)

    # 3000 Sonnet output tokens at 16.5 per MTok; the smallest row email owns the session.
    assert cost["kpis"]["total_cost_usd"] == _money(0.0495)
    # One session row, so Repositories and Sessions count the session and its money once.
    assert [(r["session_id"], r["developer_name"]) for r in cost_facts] == [(seed.split, seed.dave)]
    assert [(r["repository"], r["session_count"], r["cost_usd"]) for r in repositories["rows"]] == [
        (seed.split_repository, 1, _money(0.0495))
    ]
    assert [(s["trace_id"], s["cost_usd"]) for s in sessions["sessions"]] == [(seed.split, _money(0.0495))]
    assert [(r["developer_name"], r["cost_usd"]) for r in cost["cost_by_user"]] == [(seed.dave, _money(0.0495))]
    assert [(r["developer_name"], r["cost_usd"], r["session_count"]) for r in users["rows"]] == [
        (seed.dave, _money(0.0495), 1)
    ]
    assert sum(r["cost_usd"] for r in users["rows"]) == _money(cost["kpis"]["total_cost_usd"])
    assert [r["developer_name"] for r in daily] == [seed.dave]
    # The lines (40 - 7), carried by erin alone, and the session's last event sit under its cost developer.
    assert [(r["developer_name"], r["net_lines"]) for r in lines] == [(seed.dave, 33)]
    # A session without cost rows keeps its lines (7 - 1) and its last event under its lines email.
    assert [(r["developer_name"], r["net_lines"]) for r in lines_only] == [(seed.frank, 6)]
    assert [(r["developer_name"], r["last_active"]) for r in lines_only_last_active] == [
        (seed.frank, seed.started.replace(tzinfo=None))
    ]
    assert [(r["developer_name"], r["last_active"]) for r in last_active] == [
        (seed.dave, seed.started.replace(tzinfo=None))
    ]
    assert [(r["net_lines"], r["last_active"]) for r in users["rows"]] == [(33, seed.started.isoformat())]


@pytest.mark.asyncio
async def test_session_detail_prices_the_session_its_events_and_its_subagent_at_the_catalog() -> None:
    async with _seeded() as (handler, seed):
        detail, unpriced, catalog_source = await handler.get_session_detail(seed.detail)

    assert detail["cost_usd"] == _money(26.96510036)
    assert detail["cache_read_cost_usd"] == _money(8.87843836)
    assert detail["bloat_pct"] == 32.9
    events = [e["cost_usd"] for e in detail["events"] if e["event_type"] == "api_request"]
    assert sum(events) == _money(detail["cost_usd"])
    assert events == [_money(8.98836378), _money(8.98836818), _money(8.98836840)]
    # The subagent's own request: 125 input, 22 161 output, 13 452 180 cache read, 629 636 cache write at 1h.
    assert [(d["label"], d["cost_usd"]) for d in detail["dispatches"]] == [
        ("session", _money(26.96510036)),
        ("agent · Explore", _money(8.98836840)),
    ]
    assert (unpriced, catalog_source) == ([], "litellm")
