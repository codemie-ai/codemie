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

"""Live-Postgres checks of the request's `sel` temp table (EPMCDME-17051): every statement of the three dashboard
requests returns the same rows reading the table as evaluating the `sel` CTE, and the table never outlives the bounded
block (normal end, an error, a cancel during a statement). Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable.
"""

from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from tests.codemie.repository.cli_analytics.postgres.test_live_users_page import _rates, _Seed
from tests.codemie.repository.cli_analytics.postgres.live_helpers import _engine

_LEFT = "SELECT count(*) FROM pg_class WHERE relname LIKE 'request_sel_%'"


async def _leftover(engine) -> int:
    async with engine.acquire() as conn:
        return await conn.fetchval(_LEFT)


async def _all_reads(reader, f, scoped):
    return {
        "models": await reader.get_cost_models(f),
        "money": (await reader.get_cost_money(f, _rates())).groups,
        "page": await reader.get_users_money_page(f, _rates(), 50, 0),
        "kpis": await reader.get_cost_kpis(f),
        "classes_all": await reader.get_class_lists(f, "all"),
        "classes_model": await reader.get_class_lists(f, "model"),
        "by_user": await reader.get_cost_by_user(f),
        "avg": await reader.get_avg_session_duration_ms(f),
        "users": await reader.get_users(scoped),
        "facts": await reader.get_developer_session_facts(scoped),
        "lines": await reader.get_lines_by_user(scoped),
        "last_active": await reader.get_users_last_active(scoped),
        "daily": await reader.get_users_daily_activity(scoped),
    }


def _unordered(reads: dict) -> dict:
    """Every list of rows sorted by its text, so the order the planner happened to return them in does not count."""
    return {k: sorted(map(repr, v)) if isinstance(v, list) else v for k, v in reads.items()}


async def _seed(engine, seed):
    a, b = seed.name("a"), seed.name("b")
    await seed.session(engine, "1", a, turns=3, calls=4, ok=3)
    await seed.session(engine, "2", a, input_tokens=None)
    await seed.session(engine, "3", b, turns=1, calls=2, ok=2)
    return a, b


@pytest.mark.asyncio
@pytest.mark.parametrize("narrow", [True, False], ids=["filtered", "window"])
async def test_every_statement_reads_the_same_rows_from_the_temp_table(narrow):
    engine = await _engine()
    seed = _Seed()
    reader = PostgresCliAnalyticsReader(engine)
    try:
        a, b = await _seed(engine, seed)
        f = seed.filter(a, b) if narrow else seed.filter()
        scoped = copy.copy(f)
        scoped.developers = [a.lower()] if narrow else None
        plain = await _all_reads(reader, f, scoped)
        async with reader.bounded() as repo:
            await repo.materialize_sel(f)
            assert repo._sel_temp is not None  # noqa: SLF001
            via_table = await _all_reads(repo, f, scoped)
            assert await _leftover(engine) == 1
        # Most of the lists have no ORDER BY (the page orders them by the ranking), so the rows are compared as sets.
        assert _unordered(via_table) == _unordered(plain)
        assert await _leftover(engine) == 0
    finally:
        await seed.cleanup(engine)


@pytest.mark.asyncio
async def test_a_statement_with_another_selection_builds_its_own_cte():
    engine = await _engine()
    seed = _Seed()
    reader = PostgresCliAnalyticsReader(engine)
    try:
        a, b = await _seed(engine, seed)
        f = seed.filter(a, b)
        other = seed.filter(a)
        expected = await reader.get_cost_kpis(other)
        async with reader.bounded() as repo:
            await repo.materialize_sel(f)
            assert "request_sel_" not in repo._sel_cte(other)  # noqa: SLF001
            assert "request_sel_" in repo._sel_cte(f)  # noqa: SLF001
            assert await repo.get_cost_kpis(other) == expected
    finally:
        await seed.cleanup(engine)


@pytest.mark.asyncio
async def test_the_table_is_dropped_after_an_error_and_after_a_cancel_during_a_statement():
    engine = await _engine()
    seed = _Seed()
    reader = PostgresCliAnalyticsReader(engine)
    try:
        a, b = await _seed(engine, seed)
        f = seed.filter(a, b)
        with pytest.raises(RuntimeError):
            async with reader.bounded() as repo:
                await repo.materialize_sel(f)
                raise RuntimeError("boom")
        assert await _leftover(engine) == 0
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(1.0), reader.bounded() as repo:
                await repo.materialize_sel(f)
                async with repo._engine.acquire() as conn:  # noqa: SLF001
                    await conn.fetch("SELECT pg_sleep(30)")
        assert await _leftover(engine) == 0
        async with reader.bounded() as repo:  # the pool still serves requests
            assert await repo.get_cost_kpis(f)
    finally:
        await seed.cleanup(engine)


@pytest.mark.asyncio
async def test_both_request_tables_are_dropped_after_an_error_and_after_a_cancel():
    engine = await _engine()
    seed = _Seed()
    reader = PostgresCliAnalyticsReader(engine)
    both = "SELECT count(*) FROM pg_class WHERE relname LIKE 'request_sel_%' OR relname LIKE 'request_files_%'"

    async def leftover() -> int:
        async with engine.acquire() as conn:
            return await conn.fetchval(both)

    try:
        a, b = await _seed(engine, seed)
        f = seed.filter(a, b)
        with pytest.raises(RuntimeError):
            async with reader.bounded() as repo:
                await repo.materialize_sel(f)
                await repo.materialize_files(f)
                assert await leftover() == 2
                raise RuntimeError("boom")
        assert await leftover() == 0
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(1.0), reader.bounded() as repo:
                await repo.materialize_sel(f)
                await repo.materialize_files(f)
                async with repo._engine.acquire() as conn:  # noqa: SLF001
                    await conn.fetch("SELECT pg_sleep(30)")
        assert await leftover() == 0
    finally:
        await seed.cleanup(engine)


@pytest.mark.asyncio
async def test_a_failed_drop_closes_the_connection_and_keeps_the_requests_own_error():
    reader = PostgresCliAnalyticsReader(MagicMock())
    reader._sel_temp, reader._files_temp = ("request_sel_x", (), None), "request_files_x"  # noqa: SLF001
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=RuntimeError("drop failed"))

    await reader._drop_sel(conn)  # noqa: SLF001 - returns, so the request's own error is the one that propagates

    conn.terminate.assert_called_once()
    assert "request_files_x" in conn.execute.await_args.args[0] and "request_sel_x" in conn.execute.await_args.args[0]
    assert (reader._sel_temp, reader._files_temp) == (None, None)  # noqa: SLF001
