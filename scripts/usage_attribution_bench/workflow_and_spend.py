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

"""Compare workflow warmup and spend aggregation before and after on synthetic PostgreSQL data.

Run with PYTHONPATH=src .venv/bin/python scripts/usage_attribution_bench/workflow_and_spend.py.
Set BENCH_PG_URL to a local PostgreSQL SQLAlchemy asyncpg URL to override the default socket.
"""

import asyncio
import importlib
import json
import os
import time
from statistics import median
from uuid import uuid4

from project_reads import EDGE_WINDOW, WINDOW, QueryLog, require, run_migration
from sqlalchemy import Enum, event, func, insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from codemie.core.workflow_models import WorkflowConfig
from codemie.core.workflow_models.workflow_pool_storage import POOL_ENABLED_PREDICATE
from codemie.repository.spend_logs_model import PROJECTED_DIMENSIONS, LiteLLMSpendHourly, LiteLLMSpendLog
from codemie.repository.spend_logs_repository import SpendLogsRepository
from codemie.service.spend.coverage import SpendDimension
from codemie.service.spend.ledger import AccessScope, PersonTotalHolder, ReportFilter

WORKFLOWS = 12_000
SPEND_ROWS = 480_000
VALUES = 240
MIGRATION = importlib.import_module("external.alembic.versions.40ad17194c0d_index_pool_enabled_workflows")

DIMENSIONS = {
    f"assistant-{value}": tuple(f"call-{value * 60 + i}" for i in range(32)) + ("call-55", "call-55")
    for value in range(VALUES)
} | {"unmatched": (), "missing-request": ("absent",)}
PEOPLE = [
    PersonTotalHolder(f"person-{i}", frozenset({f"alias-{i}", f"alias-{(i + 1) % VALUES}"})) for i in range(VALUES)
] + [PersonTotalHolder("person-1", None), PersonTotalHolder("missing", frozenset())]


async def seed(connection) -> None:
    workflow_columns = WorkflowConfig.__table__.columns
    dialect = connection.dialect
    types = ", ".join(
        f'"{col.name}" {col.type.compile(dialect=dialect) if not isinstance(col.type, Enum) else "text"}'
        for col in workflow_columns
    )
    await connection.exec_driver_sql(
        f"CREATE TABLE workflows ({types}, PRIMARY KEY (id))"  # noqa: S608 - columns come from ORM metadata
    )
    await connection.exec_driver_sql(
        """
        INSERT INTO workflows (id, name, description, yaml_config, pool_config)
        SELECT 'workflow-' || n, 'workflow-' || n, repeat(md5(n::text), 8), repeat(md5((n*5)::text), 75),
               CASE WHEN n % 800 = 0 THEN '{"enabled":true}'::jsonb
                    WHEN n % 2111 = 0 THEN '{"enabled":"YES"}'::jsonb
                    WHEN n % 2333 = 0 THEN '{"enabled":1.0}'::jsonb
                    WHEN n % 40 = 0 THEN '{"enabled":false}'::jsonb
                    WHEN n % 37 = 0 THEN '{}'::jsonb
                    WHEN n % 29 = 0 THEN NULL ELSE 'null'::jsonb END
        FROM generate_series(1, $1::integer) n
        """,
        (WORKFLOWS,),
    )

    for table in (LiteLLMSpendLog.__table__, LiteLLMSpendHourly):
        await connection.execute(CreateTable(table))
        for index in table.indexes:
            await connection.execute(CreateIndex(index))

    await connection.exec_driver_sql(
        """
        INSERT INTO litellm_spend_logs
            (request_id, start_time, end_time, spend, status, codemie_request_id, codemie_workflow_id,
             model_group, attr_source, attr_form, attr_project, attr_user_id, attr_principal)
        SELECT 'raw-' || n, ts, ts, (n % 201 + 1)::numeric / 10000, 'success', 'call-' || (n / 2),
               CASE WHEN n % 3 = 0 THEN 'wf-' || (n % 13) END,
               CASE WHEN n % 7 <> 0 THEN 'model-' || (n % 13) END,
               CASE WHEN n % 11 = 0 THEN 'parsed' ELSE 'explicit' END,
               CASE WHEN n % 11 = 0 THEN 'principal' ELSE 'project_scoped' END,
               CASE WHEN n % 11 <> 0 THEN 'proj-' || (n % 5) END,
               CASE WHEN n % 13 <> 0 THEN 'person-' || (n % 300) END,
               CASE WHEN n % 11 = 0 THEN 'alias-' || (n % 300) END
        FROM generate_series(0, $1::integer) n
        CROSS JOIN LATERAL (
            VALUES (TIMESTAMPTZ '2026-01-03 00:00+00' + (n % 43200) * interval '1 minute')
        ) times(ts)
        """,
        (SPEND_ROWS - 1,),
    )
    raw = LiteLLMSpendLog.__table__.c
    hour = func.date_trunc("hour", raw.start_time, "UTC")
    dimensions = tuple(raw[name] for name in PROJECTED_DIMENSIONS)
    projected = select(hour, *dimensions, func.sum(raw.spend), func.count()).group_by(hour, *dimensions)
    await connection.execute(
        insert(LiteLLMSpendHourly).from_select(("hour", *PROJECTED_DIMENSIONS, "spend", "row_count"), projected)
    )


async def measure(log: QueryLog, engine, read, *, repeats=3):
    elapsed_ms = []
    for _ in range(repeats):
        log.statements.clear()
        start = time.perf_counter()
        result = await read()
        elapsed_ms.append(1000 * (time.perf_counter() - start))

    statements = tuple(log.statements)
    costs = []
    for statement in statements:
        log.statements[:] = [statement]
        costs.append(await log.explain(engine, repeats=repeats))
    return result, {
        "queries": len(statements),
        "wall_ms": round(median(elapsed_ms), 3),
        "db_ms": round(sum(cost["median_ms"] for cost in costs), 3),
        "shared_blocks": sum(cost["shared_blocks"] for cost in costs),
        "indexes": sorted({index for cost in costs for index in cost["indexes"]}),
    }


async def workflow_read(session_factory, *, fixed):
    async with session_factory() as session:
        predicate = text(POOL_ENABLED_PREDICATE) if fixed else WorkflowConfig.pool_config.is_not(None)
        rows = (await session.execute(select(WorkflowConfig).where(predicate))).scalars().all()
        return {row.id for row in rows if row.pool_config and row.pool_config.enabled}, len(rows)


async def suite(engine, log: QueryLog, *, fixed: bool):
    sessions = async_sessionmaker(engine)
    repository = SpendLogsRepository(projection=True, postgres_grouped_reads=fixed)

    async def dimension():
        async with sessions() as session:
            return await repository.sum_for_dimension_values(session, WINDOW, SpendDimension.ASSISTANT, DIMENSIONS)

    async def people():
        async with sessions() as session:
            return await repository.sum_for_holders(session, PEOPLE, WINDOW)

    async def workflow():
        return await workflow_read(sessions, fixed=fixed)

    projects, workflow_stats = await measure(log, engine, workflow)
    dimensions, dimension_stats = await measure(log, engine, dimension)
    totals, people_stats = await measure(log, engine, people)
    enabled_ids, fetched = projects
    workflow_stats["rows_fetched"] = fetched
    return (enabled_ids, dimensions, totals), {
        "workflow": workflow_stats,
        "dimension": dimension_stats,
        "holders": people_stats,
    }


async def check_parity(engine) -> None:
    sessions = async_sessionmaker(engine)
    access = AccessScope(
        unrestricted=False, user_id="person-11", plain_projects=("proj-0",), admin_projects=("proj-1",)
    )
    report = ReportFilter(projects=("proj-0", "proj-1"))
    cases = {
        SpendDimension.WORKFLOW: {"wf-8": ("call-10", "call-11", "call-11"), "wf-11": ("call-10", "call-12")},
        SpendDimension.MODEL: {"model-7": ("call-10", "call-11"), "model-9": ("call-10", "call-12")},
        SpendDimension.EXECUTION: {"call-10": ("call-11",), "call-11": ("call-10",), "absent": ()},
        SpendDimension.CONVERSATION: {"c1": ("call-10", "call-11"), "c2": ("call-11", "call-12")},
        SpendDimension.DATASOURCE: {"d1": ("call-10", "call-11"), "d2": ("call-11",)},
    }
    people = [
        PersonTotalHolder("person-11", frozenset({"alias-11", "alias-0"})),
        PersonTotalHolder("person-12", frozenset({"alias-11", "alias-0"})),
        PersonTotalHolder("person-11", None),
    ]
    for projection in (False, True):
        baseline = SpendLogsRepository(projection=projection)
        repository = SpendLogsRepository(projection=projection, postgres_grouped_reads=True)
        for scope, report_filter in ((None, None), (access, report)):
            async with sessions() as session:
                for dimension, ids in cases.items():
                    expected = await baseline.sum_for_dimension_values(
                        session, EDGE_WINDOW, dimension, ids, scope, report_filter
                    )
                    actual = await repository.sum_for_dimension_values(
                        session, EDGE_WINDOW, dimension, ids, scope, report_filter
                    )
                    require(expected == actual, f"dimension mismatch: {projection=}, {dimension=}, {scope=}")
                    require(any(actual.values()), f"dimension fixture matched no spend: {dimension=}, {scope=}")
                expected = await baseline.sum_for_holders(session, people, EDGE_WINDOW, scope, report_filter)
                actual = await repository.sum_for_holders(session, people, EDGE_WINDOW, scope, report_filter)
                require(expected == actual, f"holder mismatch: {projection=}, {scope=}")


async def main():
    schema = f"workflow_spend_bench_{uuid4().hex}"
    engine = create_async_engine(
        os.getenv("BENCH_PG_URL", "postgresql+asyncpg:///postgres"),
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    log = QueryLog()
    event.listen(engine.sync_engine, "before_cursor_execute", log.capture)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
            await seed(connection)
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            for table in ("workflows", "litellm_spend_logs", "litellm_spend_hourly"):
                await connection.exec_driver_sql(f"VACUUM ANALYZE {table}")

        old, old_stats = await suite(engine, log, fixed=False)
        async with engine.begin() as connection:
            await connection.run_sync(run_migration, MIGRATION.upgrade)
            await connection.exec_driver_sql("ANALYZE workflows")
        new, new_stats = await suite(engine, log, fixed=True)
        require(old == new, "optimized reads must preserve the benchmark results")
        require(new_stats["workflow"]["rows_fetched"] < old_stats["workflow"]["rows_fetched"], "fewer rows expected")
        require("ix_workflows_pool_enabled" in new_stats["workflow"]["indexes"], "workflow index must be used")
        require(
            new_stats["dimension"]["queries"] == new_stats["holders"]["queries"] == 1,
            "each grouped spend read should use one statement",
        )
        await check_parity(engine)
        async with engine.begin() as connection:
            await connection.run_sync(run_migration, MIGRATION.downgrade)
        print(json.dumps({"before": old_stats, "after": new_stats, "results_match": True}, indent=2))  # noqa: T201
    finally:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
