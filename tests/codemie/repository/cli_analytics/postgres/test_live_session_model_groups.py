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

"""Live-Postgres proof of the write path of `session_model_groups` (EPMCDME-17051, stored model groups, task 3).

The stamp statement (`SORT_KEYS_SQL`) fills the table in the same statement as the Sessions stamp: the stored rows
must equal today's token grouping (`model_token_groups(normalized=True)`), an unchanged re-derive must write nothing,
a changed model must update its row, a removed one must lose it, and a session with a model name too long for a btree
entry must write no rows and keep `model_groups_rev` NULL. Skipped when `TEST_LOCAL_PG_URL` is not set.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres import migrations
from codemie.repository.cli_analytics.postgres.cost_key_sql import model_token_groups
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.repository.cli_analytics.postgres.session_sort_keys import (
    MAX_MODEL_NAME_BYTES,
    MODEL_GROUPS_NEVER,
    update_session_sort_keys,
)
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = [
    pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against"),
    pytest.mark.asyncio,
]

_NOW = datetime.now(timezone.utc).replace(microsecond=0)
_DAY = (_NOW - timedelta(days=2)).date()
_STARTED = _NOW - timedelta(days=2)
_VERSION = "mg-test-v1"
_DIMS_SQL = "INSERT INTO session_dims (session_id, started_at, last_event_at, jwt_email) VALUES ($1, $2, $2, $3)"
_COST_SQL = """
    INSERT INTO cost_daily
        (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, cache_creation_5m_tokens, cache_creation_1h_tokens, api_call_count)
    VALUES ($1, $2, $3, $4, 'repl_main_thread', 0, $5, $6, $7, $8, $9, $10, 1)"""
_TOTALS_SQL = """
    INSERT INTO session_usage
        (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id,
         input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, attrs)
    VALUES ($1, $2, $3, '', '', '', '', 'ag1', $4, 0, 0, 0, '{"token_source": "subagent_usage"}'::jsonb)"""
_STORED_SQL = """
    SELECT model_name, normalized_model_name, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
           cache_creation_5m_tokens, cache_creation_1h_tokens, has_cost_daily
    FROM session_model_groups WHERE session_id = $1 ORDER BY model_name"""
_LIVE_SQL = f"""
    WITH sel AS (SELECT $1::text AS session_id)
    SELECT g.model_name, g.normalized_model_name, g.input_tokens, g.output_tokens, g.cache_read_tokens,
           g.cache_creation_tokens, g.cache_creation_5m_tokens, g.cache_creation_1h_tokens,
           EXISTS (SELECT 1 FROM cost_daily cd WHERE cd.session_id = g.session_id) AS has_cost_daily
    FROM {model_token_groups(normalized=True)} ORDER BY g.model_name"""
_REV_SQL = "SELECT model_groups_rev, rates_version FROM session_dims WHERE session_id = $1"
_XMIN_SQL = "SELECT model_name, xmin::text AS x FROM session_model_groups WHERE session_id = $1 ORDER BY model_name"


async def _engine() -> AnalyticsPgEngine:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    migrations.run_migrations(settings)
    today = datetime.now(timezone.utc).date()
    maintainer = PartitionMaintainer(engine, RetentionPolicy.from_settings(settings), clock=lambda: today)
    async with engine.acquire() as conn:
        await maintainer._ensure_partitions(conn, today, MaintenanceReport())
    return engine


class _Seed:
    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:12]
        self.email = f"modelgroups-{self.suffix}@example.com"
        self.ids: list[str] = []

    async def session(self, conn, name: str, rows=(), totals=()) -> str:
        """`rows`: (model, in, out, cache_read, cache_creation, 5m, 1h) per cost_daily row; `totals`: (model, in)."""
        sid = f"test-modelgroups-{self.suffix}-{name}"
        self.ids.append(sid)
        await conn.execute(_DIMS_SQL, sid, _STARTED, self.email)
        seen: list[str] = []
        for row in rows:  # a row of a model already seen goes to the day before: the key has no room for a twin
            day = _DAY - timedelta(days=seen.count(row[0]))
            seen.append(row[0])
            await conn.execute(_COST_SQL, day, sid, self.email, *row)
        for model, tokens in totals:
            await conn.execute(_TOTALS_SQL, sid, _DAY, model, tokens)
        return sid

    async def cleanup(self, engine: AnalyticsPgEngine) -> None:
        async with engine.acquire() as conn:
            for table in ("session_dims", "cost_daily", "session_usage", "session_model_groups"):
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.ids)  # noqa: S608


async def _stamp(conn, *sids: str) -> int:
    return await update_session_sort_keys(conn, list(sids), {}, {}, _VERSION)


async def _stored(conn, sid: str) -> list[tuple]:
    return [tuple(r) for r in await conn.fetch(_STORED_SQL, sid)]


async def _live(conn, sid: str) -> list[tuple]:
    return [tuple(r) for r in await conn.fetch(_LIVE_SQL, sid)]


@pytest_asyncio.fixture
async def env():
    engine = await _engine()
    seed = _Seed()
    try:
        yield engine, seed
    finally:
        await seed.cleanup(engine)
        await engine.close()


class TestStoredRowsEqualTheLiveGroups:
    async def test_cost_daily_and_subagent_only_sessions(self, env) -> None:
        engine, seed = env
        async with engine.acquire() as conn:
            cd = await seed.session(
                conn,
                "cd",
                rows=[
                    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", 10, 20, 30, 40, 25, 15),
                    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", 1, 2, 3, 4, 2, 2),
                    ("Claude-X", 5, 6, 7, 8, None, None),
                ],
                totals=[("agent-model", 99)],
            )
            sub = await seed.session(conn, "sub", totals=[("agent-only", 7)])
            await _stamp(conn, cd, sub)
            for sid, has in ((cd, True), (sub, False)):
                stored = await _stored(conn, sid)
                assert stored == await _live(conn, sid)
                assert stored
                if sid == cd:
                    assert {r[0] for r in stored} == {
                        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                        "Claude-X",
                        "agent-model",
                    }
                assert {r[8] for r in stored} == {has}
            assert [r[0] for r in await conn.fetch(_REV_SQL, cd)] == [1]
            assert [r[0] for r in await conn.fetch(_REV_SQL, sub)] == [1]

    async def test_clamped_cache_split_keeps_null_and_non_null_parts(self, env) -> None:
        engine, seed = env
        async with engine.acquire() as conn:
            sid = await seed.session(
                conn,
                "split",
                rows=[("m-null", 0, 0, 0, 100, None, None), ("m-split", 0, 0, 0, 100, 60, 90)],
            )
            await _stamp(conn, sid)
            by_model = {r[0]: r for r in await _stored(conn, sid)}
            assert by_model["m-null"][6:8] == (None, None)
            assert by_model["m-split"][6:8] == (60, 90)  # stored as summed; the clamp happens at read time
            assert list(by_model.values()) == await _live(conn, sid)


class TestUpkeep:
    async def test_unchanged_rederive_writes_no_row(self, env) -> None:
        engine, seed = env
        async with engine.acquire() as conn:
            sid = await seed.session(conn, "idem", rows=[("m1", 1, 2, 3, 4, None, None), ("m2", 5, 6, 7, 8, 4, 4)])
            await _stamp(conn, sid)
            before = [tuple(r) for r in await conn.fetch(_XMIN_SQL, sid)]
            assert len(before) == 2
            assert await _stamp(conn, sid) == 0  # session_dims untouched
            assert [tuple(r) for r in await conn.fetch(_XMIN_SQL, sid)] == before  # model rows untouched

    async def test_late_row_updates_the_existing_model_row_only(self, env) -> None:
        engine, seed = env
        async with engine.acquire() as conn:
            sid = await seed.session(
                conn, "late", rows=[("m1", 1, 2, 3, 4, None, None), ("m2", 5, 6, 7, 8, None, None)]
            )
            await _stamp(conn, sid)
            before = {r[0]: r[1] for r in await conn.fetch(_XMIN_SQL, sid)}
            await conn.execute(_COST_SQL, _DAY - timedelta(days=1), sid, seed.email, "m1", 100, 0, 0, 0, None, None)
            await _stamp(conn, sid)
            stored = await _stored(conn, sid)
            assert stored == await _live(conn, sid)
            assert stored[0][2] == 101
            after = {r[0]: r[1] for r in await conn.fetch(_XMIN_SQL, sid)}
            assert after["m1"] != before["m1"]
            assert after["m2"] == before["m2"]

    async def test_a_model_that_left_the_groups_loses_its_row(self, env) -> None:
        engine, seed = env
        async with engine.acquire() as conn:
            sid = await seed.session(
                conn, "gone", rows=[("keep", 1, 2, 3, 4, None, None), ("drop", 5, 6, 7, 8, None, None)]
            )
            await _stamp(conn, sid)
            await conn.execute("DELETE FROM cost_daily WHERE session_id = $1 AND model_name = 'drop'", sid)
            await _stamp(conn, sid)
            assert [r[0] for r in await _stored(conn, sid)] == ["keep"]

    async def test_session_without_a_session_dims_row_gets_no_rows(self, env) -> None:
        engine, seed = env
        async with engine.acquire() as conn:
            sid = await seed.session(conn, "nodims", rows=[("m1", 1, 2, 3, 4, None, None)])
            await conn.execute("DELETE FROM session_dims WHERE session_id = $1", sid)
            await _stamp(conn, sid)
            assert await _stored(conn, sid) == []


class TestOversized:
    async def test_long_raw_model_name_writes_no_rows_and_leaves_rev_null(self, env) -> None:
        engine, seed = env
        long_name = "x" * (MAX_MODEL_NAME_BYTES + 1)
        async with engine.acquire() as conn:
            sid = await seed.session(
                conn, "long", rows=[("short", 1, 2, 3, 4, None, None), (long_name, 5, 6, 7, 8, None, None)]
            )
            await _stamp(conn, sid)
            assert await _stored(conn, sid) == []
            rev, _ = tuple(await conn.fetchrow(_REV_SQL, sid))
            assert rev == MODEL_GROUPS_NEVER  # marked never valid at this revision: the backfill skips it

    async def test_a_session_that_becomes_oversized_loses_its_rows(self, env) -> None:
        engine, seed = env
        async with engine.acquire() as conn:
            sid = await seed.session(conn, "grows", rows=[("short", 1, 2, 3, 4, None, None)])
            await _stamp(conn, sid)
            assert len(await _stored(conn, sid)) == 1
            await conn.execute(
                _COST_SQL, _DAY, sid, seed.email, "y" * (MAX_MODEL_NAME_BYTES + 1), 1, 1, 1, 1, None, None
            )
            await _stamp(conn, sid)
            assert await _stored(conn, sid) == []
            assert (await conn.fetchrow(_REV_SQL, sid))["model_groups_rev"] == MODEL_GROUPS_NEVER

    async def test_session_over_the_arrays_limit_is_not_stored_either(self, env) -> None:
        engine, seed = env
        names = [f"model-{i:03d}-" + "z" * 80 for i in range(40)]  # arrays far above MAX_ARRAYS_BYTES, names short
        async with engine.acquire() as conn:
            sid = await seed.session(conn, "arrays", rows=[(n, 1, 1, 1, 1, None, None) for n in names])
            await _stamp(conn, sid)
            row = await conn.fetchrow(_REV_SQL, sid)
            assert row["rates_version"] is None
            assert row["model_groups_rev"] is None
            assert await _stored(conn, sid) == []


class TestRevisionMark:
    async def test_rev_is_set_only_when_stamped(self, env) -> None:
        engine, seed = env
        async with engine.acquire() as conn:
            sid = await seed.session(conn, "mark", rows=[("m1", 1, 2, 3, 4, None, None)])
            assert (await conn.fetchrow(_REV_SQL, sid))["model_groups_rev"] is None
            await _stamp(conn, sid)
            row = await conn.fetchrow(_REV_SQL, sid)
            assert (row["model_groups_rev"], row["rates_version"]) == (1, _VERSION)
