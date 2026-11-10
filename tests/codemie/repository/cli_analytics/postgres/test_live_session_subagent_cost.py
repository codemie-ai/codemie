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
import os
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import rollups
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

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


def test_totals_are_a_source_is_unchanged():
    """The writer-side guard is the only double-count protection: the reader adds none (EPMCDME-16441)."""
    src = inspect.getsource(rollups._totals_are_a_source)
    assert "NOT EXISTS (SELECT 1 FROM usage_requests u" in src
    assert "su.attrs->>'token_source', '') <> 'subagent_usage'" in src
    assert "_otel_usage_exists(session_id)" in src
