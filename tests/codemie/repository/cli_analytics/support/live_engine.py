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

"""The `AnalyticsPgEngine` the live Postgres checks share: a connection to `TEST_LOCAL_PG_URL`, skipped
(not failed) when no local analytics Postgres is reachable, and the cleanup of one session's rows
across the tables those checks write to."""

from __future__ import annotations

import os

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")

_SESSION_TABLES = (
    "hook_events",
    "log_events",
    "spans",
    "cost_daily",
    "session_dims",
    "rollup_dirty",
    "session_usage",
    "turns_daily",
)


async def get_live_engine() -> AnalyticsPgEngine:
    try:
        engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL)))
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:  # no reachable database (refused, wrong server, bad auth): skip
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


async def cleanup_live_session(engine: AnalyticsPgEngine, session_id: str) -> None:
    async with engine.acquire() as conn:
        for table in _SESSION_TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE session_id = $1", session_id)  # noqa: S608
