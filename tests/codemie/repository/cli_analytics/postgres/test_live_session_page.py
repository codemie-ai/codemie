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

"""Live-Postgres parity of the session list: the SQL-paged path (start_time / ctx_per_call sorts) returns the
same pages, order and total as the Python-paged path (a framework filter that keeps every session).

Sessions are seeded straight into session_dims / cost_daily / session_usage under one unique email, and every
read is filtered to that email, so other tests sharing the database cannot change the result. Skipped when
`TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable PostgreSQL (the live suite migrates it).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.delivery_framework import classify_delivery_framework
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.postgres.test_live_session_subagent_cost import (
    _COST_MODEL,
    _COST_MODEL_2,
    _KNOWN_MODEL,
    _lookup,
)

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_UNPRICED_MODEL = "not-a-real-model-xyz"
_PRICING = "codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config"

_DIMS_SQL = """
    INSERT INTO session_dims (session_id, started_at, last_event_at, repository, branch, jwt_email, first_prompt)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
"""
_COST_SQL = """
    INSERT INTO cost_daily
        (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, api_call_count)
    VALUES ($1, $2, $3, $4, 'repl_main_thread', 0.5, $5, 10, $6, 0, $7)
"""
_TOTALS_SQL = """
    INSERT INTO session_usage
        (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id,
         input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, attrs)
    VALUES ($1, $2, $3, '', '', '', '', 'ag1', 100, 20, $4, 0, $5::jsonb)
"""


async def _engine() -> AnalyticsPgEngine:
    try:
        engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL)))
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


async def _cleanup(engine: AnalyticsPgEngine, session_ids: list[str]) -> None:
    async with engine.acquire() as conn:
        for table in ("session_dims", "cost_daily", "session_usage"):
            await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", session_ids)  # noqa: S608


def _naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class _Seed:
    """Twelve sessions of one user: ties on start_time and on ctx_per_call, cost-less, hooks-only, totals-only,
    several models x days, an unpriced model, an unattributed repository and search terms with `%`, `_` and `\\`."""

    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex
        self.email = f"page-{self.suffix}@example.com"
        self.t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=5)

    def sid(self, number: int) -> str:
        return f"test-page-{self.suffix}-{number:02d}"

    def ids(self) -> list[str]:
        return [self.sid(n) for n in range(1, 13)]

    def filter(self, **kwargs) -> LocalAnalyticsFilter:
        return LocalAnalyticsFilter(
            start_dt=_naive(self.t0 - timedelta(days=1)),
            end_dt=_naive(self.t0 + timedelta(days=1)),
            users=[self.email],
            **kwargs,
        )

    async def insert(self, engine: AnalyticsPgEngine) -> None:
        day, before = self.t0.date(), self.t0.date() - timedelta(days=1)
        # (number, start offset in minutes, repository, branch, prompt)
        dims = [
            (1, 0, "repo-a", "main", "fix 100% bug"),
            (2, 0, "repo_b", "main", "snake_case thing"),
            (3, 0, "", "", "plain costless"),
            (4, 1, "repo-a", "feature/x", "totals only"),
            (5, 2, "", "", "100 percent"),
            (6, 2, "repo-a", "main", "no calls"),
            (7, 3, "repo-c", "dev", "totals and cost"),
            (8, 4, "repo-c", "main", "path C:\\dir"),
            (9, 5, "repo-c", "dev", "unpriced model"),
            (10, 6, "", "", "UPPER Case_ term"),
            (11, 6, "", "", "snakeXcase"),
            (12, 7, "repo-a", "dev", "last one"),
        ]
        # (number, day, model, input tokens, cache-read tokens, api calls)
        costs = [
            (1, day, _COST_MODEL, 100, 600, 6),
            (1, before, _COST_MODEL_2, 50, 400, 4),  # 2 models x 2 days: ctx 1000 / 10 calls = 100.0
            (2, day, _KNOWN_MODEL, 100, 400, 4),  # ties with session 1 at ctx 100.0
            (6, day, _KNOWN_MODEL, 10, 77, 0),  # no calls: ctx 0.0
            (7, day, _KNOWN_MODEL, 10, 90, 3),  # 90 + 300 from the totals / 3 calls = 130.0
            (8, day, _KNOWN_MODEL, 10, 100, 3),  # 33.333333333
            (9, day, _UNPRICED_MODEL, 10, 100, 7),  # 14.285714286
            (11, day, _KNOWN_MODEL, 10, 0, 0),
            (12, day, _KNOWN_MODEL, 10, 1, 2),  # 0.5
        ]
        totals = [(4, 500), (7, 300)]
        async with engine.acquire() as conn:
            for number, minutes, repository, branch, prompt in dims:
                started = self.t0 + timedelta(minutes=minutes)
                await conn.execute(
                    _DIMS_SQL, self.sid(number), started, started + timedelta(minutes=1), repository, branch,
                    self.email, prompt,
                )  # fmt: skip
            for number, cost_day, model, tokens, cache_read, calls in costs:
                await conn.execute(_COST_SQL, cost_day, self.sid(number), self.email, model, tokens, cache_read, calls)
            for number, cache_read in totals:
                await conn.execute(
                    _TOTALS_SQL, self.sid(number), day, _KNOWN_MODEL, cache_read,
                    json.dumps({"token_source": "subagent_usage"}),
                )  # fmt: skip


async def _page(
    handler: LocalAnalyticsHandler, seed: _Seed, python: bool, page: int, per_page: int, sort_by: str, **kw
):
    # A framework that every session has (nobody activated a skill) keeps all sessions and forces the Python path.
    framework = classify_delivery_framework([]) if python else None
    data, _, data_as_of = await handler.get_sessions(
        seed.filter(), page, per_page, sort_by, kw.pop("search", None), framework=framework, **kw
    )
    return data, data_as_of


@pytest.mark.asyncio
async def test_the_sql_paged_session_list_matches_the_python_paged_one_on_every_page():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))
        searches = [None, "100%", "snake_", "REPO-A", "feature", "\\", "  ", "case_", "no-such-term"]
        pages = [(0, 1000), (0, 1), (1, 1), (0, 5), (1, 5), (2, 5), (2, 3), (50, 5), (3, 4)]
        compared = 0
        with patch(_PRICING, _lookup):
            for sort_by in ("start_time", "ctx_per_call"):
                for search in searches:
                    for unattributed in (False, True):
                        for page, per_page in pages:
                            args = (page, per_page, sort_by)
                            kw = {"search": search, "is_unattributed": unattributed}
                            sql = await _page(handler, seed, False, *args, **dict(kw))
                            python = await _page(handler, seed, True, *args, **dict(kw))
                            assert sql == python, (sort_by, search, unattributed, page, per_page)
                            compared += 1
                # the branch filter is applied before the page, on both paths
                for page, per_page in pages:
                    sql = await _page(handler, seed, False, page, per_page, sort_by, branch="main")
                    python = await _page(handler, seed, True, page, per_page, sort_by, branch="main")
                    assert sql == python, (sort_by, "branch", page, per_page)
                    compared += 1
        assert compared == 2 * (len(searches) * 2 * len(pages) + len(pages))
    finally:
        await _cleanup(engine, seed.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_the_sql_paged_list_orders_by_key_then_session_id_and_counts_every_session():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))
        n = seed.sid
        with patch(_PRICING, _lookup):
            by_start, _ = await _page(handler, seed, False, 0, 1000, "start_time")
            by_ctx, _ = await _page(handler, seed, False, 0, 1000, "ctx_per_call")
            middle, as_of = await _page(handler, seed, False, 1, 3, "start_time")
            past_end, past_as_of = await _page(handler, seed, False, 9, 3, "start_time")
            searched, _ = await _page(handler, seed, False, 0, 1000, "start_time", search="100%")
            unattributed, _ = await _page(handler, seed, False, 0, 1000, "start_time", is_unattributed=True)

        # start_time DESC, ties (sessions 10 / 11, 5 / 6, 1 / 2 / 3) by session id ASC
        assert [s["trace_id"] for s in by_start["sessions"]] == [n(k) for k in (12, 10, 11, 9, 8, 7, 5, 6, 4, 1, 2, 3)]
        # ctx_per_call DESC, ties by id; sessions without calls (3, 4, 5, 6, 10, 11) report 0.0
        assert [s["trace_id"] for s in by_ctx["sessions"]] == [n(k) for k in (7, 1, 2, 8, 9, 12, 3, 4, 5, 6, 10, 11)]
        assert [s["ctx_per_call"] for s in by_ctx["sessions"]][:3] == [130.0, 100.0, 100.0]
        assert by_start["total"] == by_ctx["total"] == 12
        assert [s["trace_id"] for s in middle["sessions"]] == [n(k) for k in (9, 8, 7)] and middle["total"] == 12
        assert as_of == max(s["start_time"] for s in middle["sessions"])
        assert (past_end["sessions"], past_end["total"], past_as_of) == ([], 12, None)
        assert [s["trace_id"] for s in searched["sessions"]] == [n(1)] and searched["total"] == 1
        assert [s["trace_id"] for s in unattributed["sessions"]] == [n(k) for k in (10, 11, 5, 3)]
        # the cost-less and unpriced sessions are listed, flagged and free
        rows = {s["trace_id"]: s for s in by_start["sessions"]}
        assert [rows[n(k)]["cost_available"] for k in (1, 3, 4, 5, 9)] == [True, False, True, False, False]
        assert rows[n(1)]["model_name"] == f"{_COST_MODEL}, {_COST_MODEL_2}"
        assert rows[n(1)]["api_call_count"] == 10 and rows[n(7)]["cache_read_tokens"] == 390
    finally:
        await _cleanup(engine, seed.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_a_session_page_never_leaves_the_selected_users_sessions():
    engine = await _engine()
    seed, other = _Seed(), _Seed()
    try:
        await seed.insert(engine)
        await other.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)

        total, ids = await reader.get_session_page(seed.filter(), "start_time", 0, 1000)
        assert (total, sorted(ids)) == (12, sorted(seed.ids()))

        # ids of another user's sessions narrow the selection, they never widen it past the user scope
        foreign = seed.filter().restricted_to(other.ids())
        assert await reader.get_session_cost_facts(foreign, include_costless=True) == []
        assert await reader.get_session_model_costs(foreign) == []

        # the per-session queries of a page read that page's sessions only
        page = seed.filter().restricted_to([seed.sid(1), seed.sid(7)])
        facts = await reader.get_session_cost_facts(page, include_costless=True)
        assert sorted(r["session_id"] for r in facts) == [seed.sid(1), seed.sid(7)]
        assert {r["session_id"] for r in await reader.get_session_model_costs(page)} == {seed.sid(1), seed.sid(7)}

        denied = LocalAnalyticsFilter(
            start_dt=seed.filter().start_dt, end_dt=seed.filter().end_dt, users=[seed.email], deny_all=True
        )
        assert await reader.get_session_page(denied, "start_time", 0, 20) == (0, [])
    finally:
        await _cleanup(engine, seed.ids() + other.ids())
        await engine.close()
