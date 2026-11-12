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

"""Live-Postgres proof that `session_model_groups` (no foreign key) leaves with its session (EPMCDME-17051, stored
model groups, task 4): the whole-session purge, the part-wise purge of a heavy session, the orphan sweep, and the
re-derive after a partition drop unstamped the session. Runs in a private schema so the purge touches nothing else.
Skipped when `TEST_LOCAL_PG_URL` is not set."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres import migrations, session_purge
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.repository.cli_analytics.postgres.session_sort_keys import update_session_sort_keys
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.postgres.test_live_session_model_groups import (
    _COST_SQL,
    _LIVE_SQL,
    _STORED_SQL,
    _TOTALS_SQL,
)

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = [
    pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against"),
    pytest.mark.asyncio,
]

_SCHEMA = "mg_purge_test"
_TODAY = datetime.now(timezone.utc).date()
_RECENT = _TODAY - timedelta(days=2)
_OLD = datetime.now(timezone.utc) - timedelta(days=400)
_RECENT_AT = datetime.now(timezone.utc) - timedelta(days=2)
_POLICY = RetentionPolicy(raw_days=90, rollup_days=365, dedup_days=14, premake_weeks=4, session_days=180)
_DIMS_SQL = (
    "INSERT INTO session_dims (session_id, started_at, last_event_at, jwt_email) VALUES ($1, $2, $2, 'p@example.com')"
)
_COUNT_SQL = "SELECT count(*) FROM session_model_groups WHERE session_id = $1"


@pytest_asyncio.fixture
async def env():
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_SCHEMA=_SCHEMA))
    try:
        admin = await asyncpg.connect(_LOCAL_PG_URL)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    await admin.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
    migrations.run_migrations(settings)
    engine = AnalyticsPgEngine(settings)
    maintainer = PartitionMaintainer(engine, _POLICY, clock=lambda: _TODAY)
    async with engine.acquire() as conn:
        await maintainer._ensure_partitions(conn, _TODAY, MaintenanceReport())
    try:
        yield engine, maintainer
    finally:
        await engine.close()
        await admin.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        await admin.close()


async def _session(conn, sid: str, started, models=("m1", "m2")) -> None:
    """A stamped session with one session_usage row per model (all the rows are recent; `started` decides idleness)."""
    await conn.execute(_DIMS_SQL, sid, started)
    for model in models:
        await conn.execute(_TOTALS_SQL, sid, _RECENT, model, 7)
    await update_session_sort_keys(conn, [sid], {}, {}, "purge-test-v1")


async def _purge(maintainer: PartitionMaintainer, conn) -> None:
    await maintainer._purge_expired_rows(conn, _TODAY, MaintenanceReport())


async def test_the_whole_session_purge_removes_the_groups_and_keeps_those_of_a_retained_session(env) -> None:
    engine, maintainer = env
    async with engine.acquire() as conn:
        await _session(conn, "idle-1", _OLD)
        await _session(conn, "kept-1", _RECENT_AT)
        assert await conn.fetchval(_COUNT_SQL, "idle-1") == 2  # the stamp wrote them
        kept_before = [tuple(r) for r in await conn.fetch(_STORED_SQL, "kept-1")]

        await _purge(maintainer, conn)

        assert await conn.fetchval("SELECT count(*) FROM session_dims WHERE session_id = 'idle-1'") == 0
        assert await conn.fetchval(_COUNT_SQL, "idle-1") == 0
        assert [tuple(r) for r in await conn.fetch(_STORED_SQL, "kept-1")] == kept_before != []


async def test_a_heavy_session_loses_the_groups_with_its_other_rows_in_parts(env, monkeypatch) -> None:
    engine, maintainer = env
    monkeypatch.setattr(session_purge, "ROW_CAP", 3)  # 6 child rows (3 usage + 3 groups) exceed it: deleted in parts
    async with engine.acquire() as conn:
        await _session(conn, "heavy-1", _OLD, models=("m1", "m2", "m3"))
        assert await conn.fetchval(_COUNT_SQL, "heavy-1") == 3

        await _purge(maintainer, conn)

        assert await conn.fetchval("SELECT count(*) FROM session_dims WHERE session_id = 'heavy-1'") == 0
        assert await conn.fetchval(_COUNT_SQL, "heavy-1") == 0


async def test_the_orphan_sweep_removes_groups_without_a_session_and_keeps_the_others(env) -> None:
    engine, maintainer = env
    async with engine.acquire() as conn:
        await _session(conn, "kept-2", _RECENT_AT)
        kept_before = [tuple(r) for r in await conn.fetch(_STORED_SQL, "kept-2")]
        await conn.execute(
            "INSERT INTO session_model_groups (session_id, model_name, normalized_model_name, input_tokens, "
            "output_tokens, cache_read_tokens, cache_creation_tokens, has_cost_daily) "
            "VALUES ('orphan-1', 'm1', 'm1', 1, 1, 1, 1, false), ('orphan-1', 'm2', 'm2', 1, 1, 1, 1, false)"
        )

        await _purge(maintainer, conn)

        assert await conn.fetchval(_COUNT_SQL, "orphan-1") == 0
        assert [tuple(r) for r in await conn.fetch(_STORED_SQL, "kept-2")] == kept_before != []


async def test_a_session_unstamped_by_a_partition_drop_is_rederived_to_the_live_groups(env) -> None:
    engine, maintainer = env
    async with engine.acquire() as conn:
        sid = "dropped-1"
        await conn.execute(_DIMS_SQL, sid, _RECENT_AT)
        for model, day in (("m1", _RECENT), ("m2", _RECENT - timedelta(days=1))):
            await conn.execute(_COST_SQL, day, sid, "p@example.com", model, 1, 2, 3, 4, None, None)
        await update_session_sort_keys(conn, [sid], {}, {}, "purge-test-v1")
        assert await conn.fetchval(_COUNT_SQL, sid) == 2

        await maintainer._unstamp_sessions_of(conn, "cost_daily")  # what the partition drop does first
        await conn.execute("DELETE FROM cost_daily WHERE model_name = 'm2'")  # the dropped partition's rows
        assert await conn.fetchval("SELECT rates_version FROM session_dims WHERE session_id = $1", sid) is None
        assert await conn.fetchval(_COUNT_SQL, sid) == 2  # stale rows stay: harmless, rates_version is NULL

        await update_session_sort_keys(conn, [sid], {}, {}, "purge-test-v1")

        stored = [tuple(r) for r in await conn.fetch(_STORED_SQL, sid)]
        live = [tuple(r) for r in await conn.fetch(_LIVE_SQL, sid)]
        assert stored == live
        assert [row[0] for row in stored] == ["m1"]
