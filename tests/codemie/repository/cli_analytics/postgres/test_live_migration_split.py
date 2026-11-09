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

"""Live-Postgres proof that the activity columns survive a failure of the index step (EPMCDME-16427).

Revision a1c1a0000011 adds the three columns and commits them, then builds the six indexes. The columns are what every
Sessions reader needs; the indexes only make the lists fast. A failure of the index step (a lock timeout of the builds,
simulated here at the statement) rolls back that step alone: the columns stay, the revision is not recorded, and the
next run finishes the indexes. Runs in a schema of its own, dropped afterwards.

Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable PostgreSQL.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres import migrations
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_SCHEMA = "mig_split_test"
_COLUMNS = {"has_activity", "last_activity_day", "resumed"}
_HEAD = "a1c1a0000011"
_BEFORE = "a1c1a0000010"  # main's publication contract: the revision before ours


async def _scalar(conn, sql: str):
    return await conn.fetchval(sql)


@pytest.mark.asyncio
async def test_the_columns_stay_applied_when_the_index_step_fails_and_a_retry_finishes_the_indexes(monkeypatch) -> None:
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_SCHEMA=_SCHEMA))
    try:
        conn = await asyncpg.connect(_LOCAL_PG_URL)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    version = f"SELECT version_num FROM {_SCHEMA}.cli_analytics_alembic_version"
    columns_sql = (
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_schema = '{_SCHEMA}' AND table_name = 'session_dims'"
    )
    index_count = (
        f"SELECT count(*) FROM pg_indexes WHERE schemaname = '{_SCHEMA}' AND indexname LIKE 'session_dims_active%'"
    )
    try:
        await conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")

        original = Connection.execute

        def failing(self, statement, *args, **kwargs):
            if "CREATE INDEX IF NOT EXISTS" in str(statement):
                raise RuntimeError("canceling statement due to lock timeout (simulated)")
            return original(self, statement, *args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(Connection, "execute", failing)
            with pytest.raises(RuntimeError, match="lock timeout"):
                await asyncio.to_thread(migrations.run_migrations, settings)

        assert {r["column_name"] for r in await conn.fetch(columns_sql)} >= _COLUMNS  # committed before the step failed
        assert await _scalar(conn, version) == _BEFORE  # the revision is not recorded
        assert await _scalar(conn, index_count) == 0  # and the indexes were rolled back

        await asyncio.to_thread(migrations.run_migrations, settings)  # the next job runs the revision again
        assert await _scalar(conn, version) == _HEAD
        assert await _scalar(conn, index_count) == 2  # active_started, active_shared_cov
        await asyncio.to_thread(migrations.run_migrations, settings)  # and a migrated database is left as it is
        assert await _scalar(conn, version) == _HEAD
    finally:
        await conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [_BEFORE, "a1c1a0000007", "a1c1a0000001"])
async def test_a_downgraded_database_climbs_back_to_the_head_and_a_database_at_the_head_is_left_alone(target) -> None:
    """The runner starts every pass at the oldest revision. For a database already at a later revision that must be a
    no-op (Alembic does not reject an ancestor target), and a database downgraded to any revision must be brought to
    the head: columns and indexes back, the version at the head, a second pass changing nothing."""
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_SCHEMA=_SCHEMA))
    try:
        conn = await asyncpg.connect(_LOCAL_PG_URL)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    version = f"SELECT version_num FROM {_SCHEMA}.cli_analytics_alembic_version"
    try:
        await conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        await asyncio.to_thread(migrations.run_migrations, settings)
        head = await _scalar(conn, version)
        assert head == _HEAD

        engine = create_engine(settings.sqlalchemy_url)
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(migrations.ALEMBIC_DIR))
        cfg.attributes["schema"] = settings.schema
        try:
            with engine.begin() as connection:
                connection.execute(text(f'SET search_path TO "{_SCHEMA}"'))
                cfg.attributes["connection"] = connection
                command.downgrade(cfg, target)
        finally:
            engine.dispose()
        assert await _scalar(conn, version) == target

        for _ in range(2):  # the first pass climbs, the second changes nothing
            await asyncio.to_thread(migrations.run_migrations, settings)
            assert await _scalar(conn, version) == head
        columns = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = 'session_dims'",
                _SCHEMA,
            )
        }
        assert columns >= _COLUMNS
        assert (
            await _scalar(
                conn,
                f"SELECT count(*) FROM pg_indexes WHERE schemaname = '{_SCHEMA}' AND indexname = 'session_dims_active_shared_cov'",
            )
            == 1
        )
    finally:
        await conn.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        await conn.close()
