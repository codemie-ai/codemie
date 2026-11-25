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

"""Compare production-shaped project reads against synthetic data in a scratch PostgreSQL schema.

Run with PYTHONPATH=src .venv/bin/python scripts/usage_attribution_bench/project_reads.py.
Set BENCH_PG_URL to a local PostgreSQL SQLAlchemy asyncpg URL if the default socket is unavailable.
"""

import asyncio
import importlib
import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from statistics import median
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import case, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from codemie.core.models import Application
from codemie.enterprise.litellm.spend_ledger import SpendLogsLedger
from codemie.repository.application_repository import ApplicationRepository
from codemie.repository.spend_logs_model import LiteLLMSpendHourly, LiteLLMSpendLog
from codemie.repository.spend_logs_repository import SpendLogsRepository
from codemie.service.spend.coverage import LedgerCoverage, TimeRange

APPLICATIONS = 120_000
HOURLY_ROWS = 1_200_000
REPEATS = 2_048
BURST = 32
NAME = "Project-1"
WINDOW = TimeRange(datetime(2026, 1, 3, tzinfo=UTC), datetime(2026, 2, 2, tzinfo=UTC))
EDGE_WINDOW = TimeRange(WINDOW.start.replace(minute=15), WINDOW.end.replace(minute=45))
MIGRATION = importlib.import_module(
    "external.alembic.versions.b4e17194c0de_index_active_application_and_project_residue"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_migration(connection, action: Callable[[], None]) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        action()


async def seed(connection) -> None:
    for table in (Application.__table__, LiteLLMSpendHourly, LiteLLMSpendLog.__table__):
        await connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
        for index in table.indexes:
            if index.name in {
                "ix_applications_name",
                "ix_applications_active_name_lower",
                "ix_spend_hourly_project_residue",
            }:
                continue
            await connection.execute(CreateIndex(index))

    await connection.exec_driver_sql(
        """
        INSERT INTO applications (id, name, project_instance_id, project_type, description, deleted_at,
                                  chargeback_enabled, cli_analytics_enabled, chargeback_attribution)
        SELECT 'app-' || n, 'project-' || n, gen_random_uuid(), 'shared',
               repeat(md5(n::text), 8), CASE WHEN n % 10 = 0 THEN now() END, false, false, 'project'
        FROM generate_series(1, $1::integer) n
        """,
        (APPLICATIONS,),
    )
    await connection.exec_driver_sql(
        """
        INSERT INTO applications (id, name, project_instance_id, project_type, deleted_at,
                                  chargeback_enabled, cli_analytics_enabled, chargeback_attribution)
        VALUES ('exact', 'Project-1', gen_random_uuid(), 'shared', NULL, false, false, 'project'),
               ('deleted', 'PROJECT-1', gen_random_uuid(), 'shared', now(), false, false, 'project')
        """
    )
    await connection.exec_driver_sql(
        """
        INSERT INTO litellm_spend_hourly (hour, attr_source, attr_form, attr_project,
                                          attr_principal, spend, row_count)
        SELECT TIMESTAMPTZ '2026-01-01 00:00+00' + (n / 1000) * interval '1 hour',
               CASE WHEN n % 5 = 0 THEN 'parsed' ELSE 'explicit' END,
               CASE WHEN n % 100 = 0 THEN 'principal' ELSE 'project_scoped' END,
               CASE WHEN n % 100 <> 0 THEN 'project-' || (n % 300) END,
               n::text, (n % 21)::numeric / 100, n % 7 + 1
        FROM generate_series(0, $1::integer) n
        """,
        (HOURLY_ROWS - 1,),
    )
    await connection.exec_driver_sql(
        """
        INSERT INTO litellm_spend_logs (request_id, start_time, end_time, spend, status, attr_source, attr_form)
        SELECT hour::text || '-' || n, hour + n * interval '1 minute', hour + n * interval '1 minute',
               .05, 'success', 'parsed', 'principal'
        FROM (VALUES (TIMESTAMPTZ '2026-01-03 00:00+00'), (TIMESTAMPTZ '2026-02-02 00:00+00')) hours(hour)
        CROSS JOIN generate_series(0, 59) n
        """
    )


class QueryLog:
    def __init__(self):
        self.statements = []

    def capture(self, _conn, _cursor, statement, params, _context, _executemany):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            self.statements.append((statement, params))

    async def explain(self, engine, *, repeats=5) -> dict:
        statement, params = self.statements[-1]
        async with engine.connect() as connection:
            samples = [
                (
                    await connection.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, params)
                ).scalar_one()[0]
                for _ in range(repeats)
            ]
        plan = samples[-1]["Plan"]

        def indexes(node):
            return [node["Index Name"]] if "Index Name" in node else sum(map(indexes, node.get("Plans", ())), [])

        return {
            "median_ms": round(median(sample["Execution Time"] for sample in samples), 3),
            "shared_blocks": plan["Shared Hit Blocks"] + plan["Shared Read Blocks"],
            "indexes": indexes(plan),
        }

    async def burst(self, lookup, *, repeats=REPEATS) -> tuple[list, dict]:
        self.statements.clear()
        started = time.perf_counter()
        results = []
        for _ in range(0, repeats, BURST):
            results.extend(await asyncio.gather(*(lookup() for _ in range(BURST))))
        return results, {"sql_calls": len(self.statements), "elapsed_s": round(time.perf_counter() - started, 3)}


async def measure(engine, log: QueryLog, *, fixed: bool):
    sessions = async_sessionmaker(engine)
    apps = ApplicationRepository()
    spend = SpendLogsRepository(projection=True)
    ledger = SpendLogsLedger(LedgerCoverage(), session_factory=sessions, repository=spend)

    async def app(name=NAME):
        async with sessions() as session:
            if fixed:
                row = await apps.aget_active_project_for_read(session, name)
            else:
                statement = (
                    select(Application)
                    .where(func.lower(Application.name) == name.lower(), Application.deleted_at.is_(None))
                    .order_by(case((Application.name == name, 0), else_=1), Application.name)
                )
                row = (await session.execute(statement)).scalars().first()
            return row and row.id

    async def old_residue(window=WINDOW):
        async with sessions() as session:
            return await spend.project_dimension_residue(session, window)

    # Reproduce the previous cache-aside path, including its overlapping cold reads.
    residue_cache = {}

    async def residue():
        if fixed:
            portion = await ledger._project_portion(WINDOW)
            return portion.spend, portion.rows
        if WINDOW not in residue_cache:
            residue_cache[WINDOW] = await old_residue()
        return residue_cache[WINDOW]

    require(await app() == "exact", "the exact spelling should win")
    app_plan = await log.explain(engine)
    aligned = await old_residue()
    residue_plan = await log.explain(engine)
    unaligned = await old_residue(EDGE_WINDOW)
    edges_plan = await log.explain(engine)
    require(unaligned != aligned, "the partial-hour fixture must change the total")

    results, app_load = await log.burst(app)
    require(set(results) == {"exact"}, "concurrent lookups should all select the exact spelling")
    results, residue_load = await log.burst(residue)
    require(set(results) == {aligned}, "concurrent residue reads should agree with the direct query")
    variants = [await app(name) for name in ("project-1", "PROJECT-1", "project-10", "absent")]
    require(
        variants[0] == "app-1" and variants[1] in {"app-1", "exact"} and variants[2:] == [None, None],
        "case variants, deleted rows and missing names must retain their lookup behavior",
    )
    if fixed:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("UPDATE applications SET deleted_at = now() WHERE id = 'exact'")
        require(await app() == "app-1", "a completed lookup must reflect a subsequent soft deletion")
    return (
        {
            "application": {**app_plan, **app_load},
            "residue": {**residue_plan, **residue_load},
            "residue_edges": edges_plan,
        },
        (aligned, unaligned, variants),
    )


async def main() -> None:
    schema = f"project_reads_bench_{uuid4().hex}"
    engine = create_async_engine(
        os.getenv("BENCH_PG_URL", "postgresql+asyncpg:///postgres"),
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
        pool_size=BURST,
        max_overflow=0,
    )
    log = QueryLog()
    event.listen(engine.sync_engine, "before_cursor_execute", log.capture)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
            await seed(connection)
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            for table in ("applications", "litellm_spend_hourly", "litellm_spend_logs"):
                await connection.exec_driver_sql(f"VACUUM ANALYZE {table}")

        before, before_totals = await measure(engine, log, fixed=False)
        async with engine.begin() as connection:
            await connection.run_sync(run_migration, MIGRATION.upgrade)
            await connection.exec_driver_sql("ANALYZE applications")
            await connection.exec_driver_sql("ANALYZE litellm_spend_hourly")
        after, after_totals = await measure(engine, log, fixed=True)
        require(before_totals == after_totals, "index and coalescing changes must preserve results")
        require(after["application"]["sql_calls"] < before["application"]["sql_calls"], "lookup SQL calls must fall")
        require(after["residue"]["sql_calls"] == 1, "identical cold residue reads should share a single statement")
        require("ix_applications_active_name_lower" in after["application"]["indexes"], "lookup index must be used")
        require("ix_spend_hourly_project_residue" in after["residue"]["indexes"], "residue index must be used")
        async with engine.begin() as connection:
            await connection.run_sync(run_migration, MIGRATION.downgrade)
        print(json.dumps({"before": before, "after": after, "totals_match": True}, indent=2))  # noqa: T201
    finally:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
