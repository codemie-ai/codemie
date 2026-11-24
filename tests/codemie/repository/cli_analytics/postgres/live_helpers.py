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

"""Shared helpers of the live-Postgres CLI analytics tests of EPMCDME-17051.

Restored locally: the modules they used to live in (`test_live_session_cost_key`, the request seeding of
`test_live_session_subagent_cost`) were removed upstream.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")

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
_API_REQUEST_SQL = """
    INSERT INTO log_events
        (ts, session_id, event_kind, cost_usd, input_tokens, output_tokens, cache_read_tokens,
         cache_creation_tokens, severity, resource_id)
    VALUES ($1, $2, 1, $3, $4, $5, $6, $7, 9, 1)
"""
# A record of real work (EPMCDME-16496 AC4): without one a session is admitted only to a window wider than its own day,
# so the rollup windows (midnight UTC to now) do not see it.
_REAL_WORK_SQL = "INSERT INTO log_events (ts, session_id, event_kind, severity, resource_id) VALUES ($1, $2, 1, 9, 1)"
_REQUEST_START = datetime(2026, 8, 2, 12, tzinfo=timezone.utc) - timedelta(minutes=10)


async def _engine() -> AnalyticsPgEngine:
    try:
        engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL)))
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


async def _seed_requests(engine: AnalyticsPgEngine, session_id: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute(
            _API_REQUEST_SQL, _REQUEST_START + timedelta(seconds=10), session_id, 0.10, 1000, 200, 300, 50
        )


async def _seed_start(engine: AnalyticsPgEngine, session_id: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute(
            "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.session.start')",
            _REQUEST_START,
            session_id,
        )


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
