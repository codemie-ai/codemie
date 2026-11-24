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

"""Live-Postgres check that the user start-day rollup serves what the live statements give (EPMCDME-17051). Skipped when
`TEST_LOCAL_PG_URL` is not set or unreachable, or when the database has not run the revision that adds the tables."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import G_USER
from codemie.repository.cli_analytics.postgres import user_rollup as ur
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres.live_helpers import _MODULE, _engine
from tests.codemie.repository.cli_analytics.postgres.test_live_users_page import _MODEL, _Seed
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import _HAIKU

VERSION = "test-rates-version"


async def _has_tables(engine) -> bool:
    async with engine.acquire() as conn:
        return bool(await conn.fetchval("SELECT to_regclass('user_rollup_day_state') IS NOT NULL"))


async def _forget_days(engine, days) -> None:
    async with engine.acquire() as conn:
        for table in ("user_day_rollup", "user_day_money_rollup", "user_rollup_day_state"):
            await conn.execute(f"DELETE FROM {table} WHERE start_day = ANY($1::date[])", days)  # noqa: S608


@pytest.mark.asyncio
async def test_the_rollup_serves_what_the_live_statements_give_for_the_seeded_users():
    engine = await _engine()
    if not await _has_tables(engine):
        await engine.close()
        pytest.skip("the user rollup tables are not in this database")
    seed = _Seed(real_work=True)
    now = datetime.now(timezone.utc)
    first = seed.t0.date()
    days = sorted({first, now.date()})
    try:
        a, b, c = seed.name("ra"), seed.name("rb"), seed.name("rc")
        await seed.session(engine, "1", a, input_tokens=3000, turns=2, calls=4, ok=3)
        await seed.session(engine, "2", a, input_tokens=1000)
        await seed.session(engine, "3", b, input_tokens=5000, turns=1, calls=2, ok=2)
        await seed.session(engine, "4", c, input_tokens=None, turns=1)  # a developer with nothing priced
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            async with engine.acquire() as conn:
                signatures = await ur.day_signatures(conn, days[0], days[-1])
                for day in days:
                    await ur.build_day(
                        reader, conn, day, model_rates_for, VERSION, signatures.get(day, ur._NO_SESSIONS)
                    )
            start = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
            f = LocalAnalyticsFilter(scope=ur.ROLLUP_SCOPE, start_dt=start, end_dt=datetime.now(timezone.utc))
            served = await reader.rollup_users(f, model_rates_for, 3600)
            assert served is not None

            only = LocalAnalyticsFilter(
                scope=ur.ROLLUP_SCOPE, start_dt=start, end_dt=f.end_dt, developers=[a.lower(), b.lower(), c.lower()]
            )
            async with reader.bounded() as repo:
                await repo.materialize_sel(only)
                live_users = {r["developer_name"]: r for r in await repo.get_users(only)}
                live_facts = {r["developer"]: r for r in await repo.get_developer_session_facts(only)}
                page = await repo.get_users_money_page(only, [model_rates_for(_MODEL)], None, 0)

        users = {r["developer_name"]: r for r in served.user_rows}
        facts = {r["developer"]: r for r in served.developer_facts}
        for dev in (a.lower(), b.lower(), c.lower()):
            for key in ("session_count", "input_tokens", "output_tokens", "top_model"):
                assert users[dev][key] == live_users[dev][key], (dev, key)
            for key in ("turns", "tool_calls", "tool_successes"):
                assert facts[dev][key] == live_facts[dev][key], (dev, key)
        live_cost = {g.keys["user"]: g.cost_usd for g in page.money.groups[G_USER]}
        served_cost = {g.keys["user"]: g.cost_usd for g in served.money_page.money.groups[G_USER]}
        for dev in (a.lower(), b.lower()):
            assert served_cost[dev] == pytest.approx(live_cost[dev])
        assert c.lower() not in served_cost  # nothing priced: in the ranking, not in the money
        ranking = served.money_page.developers
        assert ranking.index(b.lower()) < ranking.index(a.lower()) < ranking.index(c.lower())  # by spend
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, days)
        await engine.close()


@pytest.mark.asyncio
async def test_a_window_with_a_day_the_rollup_lacks_takes_the_live_path():
    engine = await _engine()
    if not await _has_tables(engine):
        await engine.close()
        pytest.skip("the user rollup tables are not in this database")
    try:
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        now = datetime.now(timezone.utc)
        long_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=400)
        f = LocalAnalyticsFilter(scope=ur.ROLLUP_SCOPE, start_dt=long_ago, end_dt=now)

        assert await reader.rollup_users(f, model_rates_for, 3600) is None
    finally:
        await engine.close()
