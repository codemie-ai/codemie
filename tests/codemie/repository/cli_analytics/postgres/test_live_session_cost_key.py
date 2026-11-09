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

"""Live-Postgres parity of the SQL cost key and the kind flags with the Python pricer (EPMCDME-16427, T2).

Every case of the shared pricing table is seeded as its own session (plus a subagent totals-only row), and the SQL
key must equal `price_session_models` per session while the SQL flags must make `CatalogPricer` record exactly the
models `price()` records. Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable.
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
from codemie.repository.cli_analytics.ports import ModelRates
from codemie.repository.cli_analytics.postgres.cost_key_sql import cost_groups_cte, cost_key_from_groups_sql
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import (
    PostgresCliAnalyticsReader,
    session_cost_key_sql,
    rates_params,
    window_params,
)
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.coding_agent_pricing import (
    CatalogPricer,
    TokenCounts,
    model_rates_for,
)
from codemie.service.analytics.handlers.session_money import price_session_models
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import (
    _HAIKU,
    _NO_1H,
    _PARTIAL,
    PRICING_CASES,
)

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_MODULE = "codemie.service.analytics.handlers.coding_agent_pricing"
_DIMS_SQL = """
    INSERT INTO session_dims (session_id, started_at, last_event_at, jwt_email) VALUES ($1, $2, $3, $4)
"""
_COST_SQL = """
    INSERT INTO cost_daily
        (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, cache_creation_5m_tokens, cache_creation_1h_tokens, api_call_count)
    VALUES ($1, $2, $3, $4, 'repl_main_thread', 0, $5, $6, $7, $8, $9, $10, 1)
"""
_TOTALS_SQL = """
    INSERT INTO session_usage
        (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id,
         input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, attrs)
    VALUES ($1, $2, $3, '', '', '', '', 'ag1', $4, 0, 0, 0, $5::jsonb)
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


class _Seed:
    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex
        self.email = f"cost-key-{self.suffix}@example.com"
        self.t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)

    def sid(self, name: str) -> str:
        return f"test-costkey-{self.suffix}-{name}"

    def filter(self) -> LocalAnalyticsFilter:
        naive = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
        return LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE,
            start_dt=naive(self.t0 - timedelta(days=1)),
            end_dt=naive(self.t0 + timedelta(days=1)),
            users=[self.email],
        )

    async def session(self, engine: AnalyticsPgEngine, name: str, rows: list[tuple]) -> str:
        """`rows`: (model, input, output, cache_read, cache_creation, cache_5m, cache_1h) per cost_daily row."""
        sid = self.sid(name)
        async with engine.acquire() as conn:
            await conn.execute(_DIMS_SQL, sid, self.t0, self.t0 + timedelta(minutes=1), self.email)
            for model, *tokens in rows:
                await conn.execute(_COST_SQL, self.t0.date(), sid, self.email, model, *tokens)
        return sid


def _row(model: str, counts: dict) -> tuple:
    return (
        model,
        counts.get("input", 0),
        counts.get("output", 0),
        counts.get("cache_read", 0),
        counts.get("cache_creation", 0),
        counts.get("cache_5m"),
        counts.get("cache_1h"),
    )


async def _keys(reader: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, rates: list[ModelRates]) -> dict:
    rows = await reader._q(
        f"WITH {reader._sessions_cte(f)} SELECT k.session_id, k.cost_key FROM {session_cost_key_sql()}",
        {**window_params(f), **rates_params(rates)},
    )
    return {r["session_id"]: float(r["cost_key"]) for r in rows}


async def _served_keys(reader: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, rates: list[ModelRates]) -> dict:
    """The keys by the spelling the cost sort serves: the cost key over the window's token groups (`cost_groups_cte`)."""
    rows = await reader._q(
        f"WITH {reader._sessions_cte(f)}, {cost_groups_cte()} SELECT k.session_id, k.cost_key FROM "
        f"{cost_key_from_groups_sql()}",
        {**window_params(f), **rates_params(rates)},
    )
    return {r["session_id"]: float(r["cost_key"]) for r in rows}


@pytest.mark.asyncio
async def test_sql_cost_key_and_flags_equal_the_python_pricer_on_every_pricing_case():
    engine = await _engine()
    seed = _Seed()
    sids = []
    try:
        cases = {}
        for number, param in enumerate(PRICING_CASES):
            model, counts, _, _ = param.values
            counts = dict(counts)  # the shared case table stays untouched
            sid = await seed.session(engine, f"c{number:02d}", [_row(model, counts)])
            sids.append(sid)
            cases[sid] = (model, counts)
        # a subagent totals-only row of the same model joins the same (session, model) group
        async with engine.acquire() as conn:
            await conn.execute(
                _TOTALS_SQL,
                sids[0],
                seed.t0.date(),
                "claude-haiku-4-5",
                7,
                json.dumps({"token_source": "subagent_usage"}),
            )
        cases[sids[0]][1]["input"] = cases[sids[0]][1].get("input", 0) + 7  # mirrored below for the Python side

        reader = PostgresCliAnalyticsReader(engine)
        f = seed.filter()
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU, _NO_1H, _PARTIAL]):
            rows = await reader.get_session_model_costs(f)
            expected = price_session_models(rows).by_session
            models = sorted({m for m, _ in cases.values()})
            rates = [model_rates_for(m) for m in models]
            keys = await _keys(reader, f, rates)
            served = await _served_keys(reader, f, rates)  # the spelling the cost sort and the framework page serve
            for sid in sids:
                assert keys[sid] == pytest.approx(round(expected[sid], 9), abs=1e-9), cases[sid]
                assert served[sid] == pytest.approx(round(expected[sid], 9), abs=1e-9), cases[sid]

            flags = await reader.get_window_model_flags(f)
            assert sorted(m for m, _ in flags) == models
            from_flags = CatalogPricer()
            from_flags.record_unpriced_from_flags(flags)
            from_rows = CatalogPricer()
            for sid in sids:
                model, counts = cases[sid]
                from_rows.price(model, TokenCounts(**counts))
            assert from_flags.unpriced == from_rows.unpriced
    finally:
        await _cleanup(engine, sids)
        await engine.close()


@pytest.mark.asyncio
async def test_costs_closer_than_the_key_precision_order_by_session_id():
    engine = await _engine()
    seed = _Seed()
    sids = []
    try:
        for name, tokens in (("b", 1_000_000), ("a", 1_000_001), ("c", 2_000_000)):
            sids.append(await seed.session(engine, name, [("m", tokens, 0, 0, 0, None, None)]))
        reader = PostgresCliAnalyticsReader(engine)
        f = seed.filter()
        rates = rates_params([ModelRates(model="m", input=1e-12)])
        rows = await reader._q(
            f"WITH {reader._sessions_cte(f)} SELECT k.session_id FROM {session_cost_key_sql()} "
            'ORDER BY k.cost_key DESC, k.session_id COLLATE "C"',
            {**window_params(f), **rates},
        )
        assert [r["session_id"] for r in rows] == [seed.sid("c"), seed.sid("a"), seed.sid("b")]
    finally:
        await _cleanup(engine, sids)
        await engine.close()
