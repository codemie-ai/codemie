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

"""Live-Postgres proof of revision a1c1a0000012 (EPMCDME-16427 part 2): the stored sort-key columns of `session_dims`
are nullable without default, the kill-switch control table holds one disabled row, the upgrade is idempotent and the
downgrade removes both.

Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable, migrated PostgreSQL.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres import migrations
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_COLUMNS = {
    "cache_read_total",
    "api_call_total",
    "classifier_names",
    "models_used",
    "kind_masks",
    "cost_key",
    "rates_version",
}


def _revision():
    path = Path(migrations.ALEMBIC_DIR) / "versions" / "a1c1a0000012_cli_analytics_session_sort_keys.py"
    spec = importlib.util.spec_from_file_location("sort_columns_live", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _groups_revision():
    path = Path(migrations.ALEMBIC_DIR) / "versions" / "a1c1a0000013_cli_analytics_session_model_groups.py"
    spec = importlib.util.spec_from_file_location("model_groups_live", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade_statements(revision) -> list[str]:
    """Both steps of the revision, the columns and the index, as one run of statements, then those of a1c1a0000013
    (its control columns and its index live on the objects this test drops)."""
    groups = _groups_revision()
    return [
        *revision.column_statements(),
        *revision.index_statements(),
        *groups.column_statements(),
        *groups.index_statements(),
    ]


async def _state(conn: asyncpg.Connection) -> tuple[dict, list]:
    columns = await conn.fetch(
        "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'session_dims' AND column_name = ANY($1)",  # other live tests keep schemas of their own
        list(_COLUMNS),
    )
    exists = await conn.fetchval("SELECT to_regclass('session_sort_keys_control')")
    rows = await conn.fetch("SELECT enabled FROM session_sort_keys_control") if exists else []
    return {r["column_name"]: (r["is_nullable"], r["column_default"]) for r in columns}, [r["enabled"] for r in rows]


async def _control_rows(conn: asyncpg.Connection) -> list[tuple[bool, bool]]:
    """The kill switch as the shared database holds it: (enabled, operator_disabled), [] when there is no table."""
    if not await conn.fetchval("SELECT to_regclass('session_sort_keys_control')"):
        return []
    rows = await conn.fetch("SELECT enabled, operator_disabled FROM session_sort_keys_control")
    return [(r["enabled"], r["operator_disabled"]) for r in rows]


async def _run() -> None:
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
    engine = AnalyticsPgEngine(settings)
    try:
        try:
            async with engine.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except (OSError, asyncpg.PostgresError) as exc:
            pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
        revision = _revision()
        async with engine.acquire() as conn:
            await _check(conn, settings, revision)
    finally:
        await engine.close()


async def _check(conn: asyncpg.Connection, settings: AnalyticsPgSettings, revision) -> None:
    # The database is shared with the other live tests: whatever the kill switch holds now is what an idempotent upgrade
    # must keep, and what this test puts back after the downgrade has dropped and re-seeded the table.
    kept = await _control_rows(conn)
    try:
        await asyncio.to_thread(migrations.run_migrations, settings)
        columns, control = await _state(conn)
        assert set(columns) == _COLUMNS
        assert all(nullable == "YES" and default is None for nullable, default in columns.values())
        assert await _control_rows(conn) == (kept or [(False, False)])  # one row; a fresh one is seeded disabled

        await asyncio.to_thread(migrations.run_migrations, settings)  # idempotent: the row is as it was
        assert await _control_rows(conn) == (kept or [(False, False)])
        for statement in _upgrade_statements(revision):  # and re-applying the statements changes nothing
            await conn.execute(statement.replace("SET LOCAL", "SET"))
        assert await _control_rows(conn) == (kept or [(False, False)])

        for statement in [*_groups_revision().DOWNGRADE, *revision.DOWNGRADE]:  # newest revision first
            await conn.execute(statement)
        assert await _state(conn) == ({}, [])
        for statement in _upgrade_statements(revision):
            await conn.execute(statement.replace("SET LOCAL", "SET"))
        assert set((await _state(conn))[0]) == _COLUMNS
        assert await _control_rows(conn) == [(False, False)]  # the re-seeded row is disabled
        assert await conn.fetchval("SHOW lock_timeout") == migrations.LOCK_TIMEOUT
    finally:
        for statement in _upgrade_statements(revision):  # leave the database at the head, as alembic's stamp says
            await conn.execute(statement.replace("SET LOCAL", "SET"))
        if kept:
            await conn.execute("UPDATE session_sort_keys_control SET enabled = $1, operator_disabled = $2", *kept[0])
        await conn.execute("RESET lock_timeout")  # the session-level SETs above must not outlive the pooled connection


def test_sort_columns_revision_on_a_live_database() -> None:
    asyncio.run(_run())
