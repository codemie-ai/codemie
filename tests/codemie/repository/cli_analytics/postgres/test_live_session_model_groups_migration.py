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

"""Live-Postgres proof of revision a1c1a0000013 (EPMCDME-17051): the stored token groups.

The table, the stamp column, the two control columns and the partial index appear on upgrade, a second upgrade changes
nothing, the downgrade removes them (twice over, idempotently), pre-existing `session_dims` rows keep a NULL stamp, and a
`session_dims` above the size threshold gets the index built CONCURRENTLY. Runs in a schema of its own, dropped
afterwards.

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

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres import migrations
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_HEAD = "a1c1a0000022"  # the head the upgrade reaches; the objects under test are those of a1c1a0000013
_BEFORE = "a1c1a0000012"
_INDEX = "session_dims_model_groups_pending"
_CONTROL = ("model_groups_enabled", "model_groups_operator_disabled")
_TABLE_COLUMNS = {
    "session_id": ("text", "NO"),
    "model_name": ("text", "NO"),
    "normalized_model_name": ("text", "NO"),
    "input_tokens": ("bigint", "NO"),
    "output_tokens": ("bigint", "NO"),
    "cache_read_tokens": ("bigint", "NO"),
    "cache_creation_tokens": ("bigint", "NO"),
    "cache_creation_5m_tokens": ("bigint", "YES"),
    "cache_creation_1h_tokens": ("bigint", "YES"),
    "has_cost_daily": ("boolean", "NO"),
}


def _downgrade_to_before(settings: AnalyticsPgSettings) -> None:
    schema = settings.schema
    engine = create_engine(settings.sqlalchemy_url)
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(migrations.ALEMBIC_DIR))
    cfg.attributes["schema"] = settings.schema
    try:
        with engine.begin() as connection:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            cfg.attributes["connection"] = connection
            command.downgrade(cfg, _BEFORE)
    finally:
        engine.dispose()


async def _snapshot(conn: asyncpg.Connection, schema: str) -> dict:
    columns = await conn.fetch(
        "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = $1 AND (table_name = 'session_model_groups' "
        "OR (table_name = 'session_dims' AND column_name = 'model_groups_rev') "
        "OR (table_name = 'session_sort_keys_control' AND column_name LIKE 'model_groups_%'))",
        schema,
    )
    index = await conn.fetchrow(
        "SELECT pg_get_indexdef(i.indexrelid) AS definition, i.indisvalid FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = $1 AND c.relname = $2",
        schema,
        _INDEX,
    )
    return {
        "columns": {
            (r["table_name"], r["column_name"]): (r["data_type"], r["is_nullable"], r["column_default"])
            for r in columns
        },
        "index": None if index is None else (index["definition"], index["indisvalid"]),
        "version": await conn.fetchval(f"SELECT version_num FROM {schema}.cli_analytics_alembic_version"),
    }


async def _connect(schema: str) -> asyncpg.Connection:
    try:
        conn = await asyncpg.connect(_LOCAL_PG_URL)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    return conn


def _settings(schema: str) -> AnalyticsPgSettings:
    return AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_SCHEMA=schema))


@pytest.mark.asyncio
async def test_the_upgrade_creates_the_objects_a_second_run_changes_nothing_and_the_downgrade_removes_them() -> None:
    schema = "mig_mg_upgrade"  # a schema per test: the tests may run in parallel workers against one database
    settings = _settings(schema)
    conn = await _connect(schema)
    try:
        await asyncio.to_thread(migrations.run_migrations, settings)
        first = await _snapshot(conn, schema)

        assert first["version"] == _HEAD
        for column, (sql_type, nullable) in _TABLE_COLUMNS.items():
            assert first["columns"][("session_model_groups", column)] == (sql_type, nullable, None)
        assert first["columns"][("session_dims", "model_groups_rev")] == ("smallint", "YES", None)
        for column in _CONTROL:
            assert first["columns"][("session_sort_keys_control", column)] == ("boolean", "NO", "false")
        definition, valid = first["index"]
        assert valid
        assert definition.endswith(
            "USING btree (session_id) WHERE ((rates_version IS NULL) OR (model_groups_rev IS DISTINCT FROM 1))"
        )

        key = await conn.fetch(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            f"WHERE i.indrelid = '{schema}.session_model_groups'::regclass AND i.indisprimary ORDER BY a.attnum"
        )
        assert [r["attname"] for r in key] == ["session_id", "model_name"]
        options = await conn.fetchval(
            f"SELECT reloptions FROM pg_class WHERE oid = '{schema}.session_model_groups'::regclass"
        )
        assert options == ["fillfactor=90"]
        assert await conn.fetchval(f"SELECT count(*) FROM {schema}.session_sort_keys_control") == 1
        assert (
            await conn.fetchval(
                f"SELECT model_groups_enabled OR model_groups_operator_disabled FROM {schema}.session_sort_keys_control"
            )
            is False
        )

        await asyncio.to_thread(migrations.run_migrations, settings)  # nothing to do
        assert await _snapshot(conn, schema) == first

        await asyncio.to_thread(_downgrade_to_before, settings)
        after = await _snapshot(conn, schema)
        assert after["version"] == _BEFORE
        assert after["columns"] == {} and after["index"] is None
        assert await conn.fetchval(f"SELECT to_regclass('{schema}.session_model_groups')") is None
        assert (
            await conn.fetchval(f"SELECT to_regclass('{schema}.session_sort_keys_control')") is not None
        )  # 0012's stays

        await asyncio.to_thread(migrations.run_migrations, settings)  # and the climb back is identical
        assert await _snapshot(conn, schema) == first
    finally:
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await conn.close()


@pytest.mark.asyncio
async def test_existing_sessions_keep_a_null_stamp_and_a_small_table_gets_the_index() -> None:
    schema = "mig_mg_existing"  # a schema per test: the tests may run in parallel workers against one database
    settings = _settings(schema)
    conn = await _connect(schema)
    try:
        await asyncio.to_thread(migrations.run_migrations, settings)
        await asyncio.to_thread(_downgrade_to_before, settings)
        await conn.execute(
            f"INSERT INTO {schema}.session_dims (session_id) SELECT 's' || g FROM generate_series(1, 50) g"
        )
        await conn.execute(f"UPDATE {schema}.session_dims SET rates_version = 'v1' WHERE session_id = 's1'")

        await asyncio.to_thread(migrations.run_migrations, settings)

        stamps = await conn.fetch(f"SELECT DISTINCT model_groups_rev FROM {schema}.session_dims")
        assert [r["model_groups_rev"] for r in stamps] == [None]  # nothing is trusted until the groups are derived
        assert (await _snapshot(conn, schema))["index"][1] is True
        # every session is pending: a stamped one has no trusted groups yet either
        pending = await conn.fetchval(
            f"SELECT count(*) FROM {schema}.session_dims WHERE rates_version IS NULL OR model_groups_rev IS DISTINCT FROM 1"
        )
        assert pending == 50
    finally:
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await conn.close()


@pytest.mark.asyncio
async def test_a_table_above_the_threshold_gets_the_index_built_concurrently_while_an_older_snapshot_is_open() -> None:
    schema = "mig_mg_concurrent"  # a schema per test: the tests may run in parallel workers against one database
    settings = _settings(schema)
    conn = await _connect(schema)
    writer = await asyncpg.connect(_LOCAL_PG_URL)
    try:
        await asyncio.to_thread(migrations.run_migrations, settings)
        await asyncio.to_thread(_downgrade_to_before, settings)
        await conn.execute(
            f"INSERT INTO {schema}.session_dims (session_id) SELECT 's' || g FROM generate_series(1, 150001) g"
        )
        # An open snapshot on another connection: it blocks no ALTER, a plain build ignores it, and CONCURRENTLY has to
        # wait for it after it has started.
        transaction = writer.transaction(isolation="repeatable_read")
        await transaction.start()
        await writer.fetchval("SELECT 1")

        migration = asyncio.create_task(asyncio.to_thread(migrations.run_migrations, settings))
        statement = None
        for _ in range(100):
            statement = await conn.fetchval(
                "SELECT query FROM pg_stat_activity WHERE query LIKE 'CREATE INDEX CONCURRENTLY%' "
                "AND pid <> pg_backend_pid()"
            )
            if statement:
                break
            await asyncio.sleep(0.1)
        await transaction.commit()
        await migration

        assert statement is not None and f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX}" in statement
        snapshot = await _snapshot(conn, schema)
        assert snapshot["version"] == _HEAD and snapshot["index"][1] is True
    finally:
        await writer.close()
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await conn.close()
