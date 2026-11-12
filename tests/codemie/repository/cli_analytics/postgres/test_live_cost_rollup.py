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

"""Live-Postgres checks of the start-day rollup for /cost and for a window that starts inside a day (EPMCDME-17051):
`rollup_cost` gives what `get_cost_money` / `get_cost_kpis` / `get_cost_by_user` give, a non-midnight start serves the
partial first day from the sessions live, and a window the rollup is not made for answers None. Skipped when
`TEST_LOCAL_PG_URL` is not set or unreachable, or when the database has not run the revision that adds the tables."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import G_MODEL, G_USER
from codemie.repository.cli_analytics.postgres import user_rollup as ur
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres.live_helpers import _COST_SQL, _DIMS_SQL, _MODULE, _engine
from tests.codemie.repository.cli_analytics.postgres.test_live_users_page import _MODEL
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import _HAIKU

VERSION = "test-cost-rates-version"
_TABLES = ("session_dims", "cost_daily", "lines_daily", "turns_daily", "invocations_hourly")
_ROLLUP_TABLES = (*ur._DAY_TABLES, "user_rollup_day_state")


async def _has_tables(engine) -> bool:
    async with engine.acquire() as conn:
        return bool(await conn.fetchval("SELECT to_regclass('user_rollup_day_state') IS NOT NULL"))


async def _forget_days(engine, days) -> None:
    async with engine.acquire() as conn:
        for table in _ROLLUP_TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE start_day = ANY($1::date[])", days)  # noqa: S608 - fixed names


class _Sessions:
    """Sessions of a few developers at chosen start times, each with one priced token row."""

    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:10]
        self.sids: list[str] = []

    def email(self, prefix: str) -> str:
        return f"{prefix}-{self.suffix}@x.com"

    async def add(self, engine, key: str, email: str, started: datetime, input_tokens: int) -> str:
        sid = f"test-cost-{self.suffix}-{key}"
        self.sids.append(sid)
        async with engine.acquire() as conn:
            await conn.execute(_DIMS_SQL, sid, started, started + timedelta(minutes=1), email)
            await conn.execute(_COST_SQL, started.date(), sid, email, _MODEL, input_tokens, 0, 0, 0, None, None)
            # known activity and not resumed: the sessions the partial first day reads as `_GAP_NONRESUMED_SQL`; the
            # last activity day is what a window that starts inside a day admits a session by (ingest stores it)
            await conn.execute(
                "UPDATE session_dims SET has_activity = true, resumed = false, last_activity_day = $2 WHERE session_id = $1",
                sid,
                started.date(),
            )
        return sid

    async def cleanup(self, engine) -> None:
        async with engine.acquire() as conn:
            for table in _TABLES:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.sids)  # noqa: S608


async def _build(engine, reader, days) -> None:
    async with engine.acquire() as conn:
        signatures = await ur.day_signatures(conn, days[0], days[-1])
        for day in days:
            await ur.build_day(reader, conn, day, model_rates_for, VERSION, signatures.get(day, ur._NO_SESSIONS))


def _midnight(day) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_rollup_cost_gives_what_the_live_cost_statements_give():
    engine = await _engine()
    if not await _has_tables(engine):
        await engine.close()
        pytest.skip("the user rollup tables are not in this database")
    seeds = _Sessions()
    started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
    days = sorted({started.date(), datetime.now(timezone.utc).date()})
    try:
        a, b, c = seeds.email("ca"), seeds.email("cb"), seeds.email("cc")
        await seeds.add(engine, "1", a, started, 3000)
        await seeds.add(engine, "2", a, started + timedelta(minutes=5), 1000)
        await seeds.add(engine, "3", b, started + timedelta(minutes=9), 5000)
        async with engine.acquire() as conn:  # a developer with a session and no priced row
            sid = f"test-cost-{seeds.suffix}-4"
            seeds.sids.append(sid)
            await conn.execute(_DIMS_SQL, sid, started, started + timedelta(minutes=1), c)
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            await _build(engine, reader, days)
            f = LocalAnalyticsFilter(
                scope=ur.ROLLUP_SCOPE, start_dt=_midnight(days[0]), end_dt=datetime.now(timezone.utc)
            )
            served = await reader.rollup_cost(f, model_rates_for, 3600)
            assert served is not None
            async with reader.bounded() as repo:
                await repo.materialize_sel(f)
                rates = [model_rates_for(m) for m in await repo.get_cost_models(f)]
                live_money = await repo.get_cost_money(f, rates, buckets=False)
                live_kpis = (await repo.get_cost_kpis(f))[0]
                live_by_user = {r["developer_name"] for r in await repo.get_cost_by_user(f, with_classes=False)}

        live_user = {g.keys["user"]: g.cost_usd for g in live_money.groups[G_USER]}
        served_user = {g.keys["user"]: g.cost_usd for g in served.money.groups[G_USER]}
        assert served_user.keys() == live_user.keys()
        for user, cost in live_user.items():
            assert served_user[user] == pytest.approx(cost), user
        live_model = {g.keys["model"]: g.cost_usd for g in live_money.groups[G_MODEL]}
        served_model = {g.keys["model"]: g.cost_usd for g in served.money.groups[G_MODEL]}
        assert served_model.keys() == live_model.keys()
        for model, cost in live_model.items():
            assert served_model[model] == pytest.approx(cost), model
        assert set(served.developers) == live_by_user  # the developers with a priced row; c has none
        assert c.lower() not in served.developers
        assert served.total_sessions == int(live_kpis["total_sessions"])
        assert served.spend_sessions == int(live_kpis["spend_sessions"])
        live_tokens = sum(
            int(live_kpis[k] or 0)
            for k in (
                "total_input_tokens",
                "total_output_tokens",
                "total_cache_read_tokens",
                "total_cache_creation_tokens",
            )
        )
        assert served.total_tokens == live_tokens
    finally:
        await seeds.cleanup(engine)
        await _forget_days(engine, days)
        await engine.close()


@pytest.mark.asyncio
async def test_a_window_that_starts_inside_a_day_reads_that_day_from_the_sessions_live():
    engine = await _engine()
    if not await _has_tables(engine):
        await engine.close()
        pytest.skip("the user rollup tables are not in this database")
    seeds = _Sessions()
    now = datetime.now(timezone.utc)
    day = now.date() - timedelta(days=1)
    start = _midnight(day) + timedelta(hours=12)  # not a midnight: the first day is partial
    days = sorted({day, now.date()})
    try:
        a, b = seeds.email("pa"), seeds.email("pb")
        await seeds.add(engine, "before", a, start - timedelta(hours=2), 7000)  # started before the window: not in it
        await seeds.add(engine, "in1", a, start + timedelta(hours=1), 3000)
        await seeds.add(engine, "in2", b, start + timedelta(hours=2), 1000)
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            await _build(engine, reader, days)
            f = LocalAnalyticsFilter(scope=ur.ROLLUP_SCOPE, start_dt=start, end_dt=datetime.now(timezone.utc))
            served = await reader.rollup_users(f, model_rates_for, 3600)
            assert served is not None
            only = LocalAnalyticsFilter(
                scope=ur.ROLLUP_SCOPE, start_dt=start, end_dt=f.end_dt, developers=[a.lower(), b.lower()]
            )
            async with reader.bounded() as repo:
                await repo.materialize_sel(only)
                live = {r["developer_name"]: r for r in await repo.get_users(only)}

        rows = {r["developer_name"]: r for r in served.user_rows}
        for dev in (a.lower(), b.lower()):
            for key in ("session_count", "input_tokens"):
                assert rows[dev][key] == live[dev][key], (dev, key)
        assert rows[a.lower()]["session_count"] == 1  # the session before the window is neither counted twice nor lost
    finally:
        await seeds.cleanup(engine)
        await _forget_days(engine, days)
        await engine.close()


@pytest.mark.asyncio
async def test_a_window_inside_one_day_and_a_gap_over_the_cap_take_the_live_path():
    engine = await _engine()
    if not await _has_tables(engine):
        await engine.close()
        pytest.skip("the user rollup tables are not in this database")
    seeds = _Sessions()
    now = datetime.now(timezone.utc)
    day = now.date() - timedelta(days=1)
    start = _midnight(day) + timedelta(hours=12)
    days = sorted({day, now.date()})
    try:
        await seeds.add(engine, "gap", seeds.email("ga"), start - timedelta(hours=1), 1000)
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            await _build(engine, reader, days)
            last_hour = LocalAnalyticsFilter(scope=ur.ROLLUP_SCOPE, start_dt=now - timedelta(minutes=1), end_dt=now)
            assert await reader.rollup_users(last_hour, model_rates_for, 3600) is None
            assert await reader.rollup_cost(last_hour, model_rates_for, 3600) is None
            f = LocalAnalyticsFilter(scope=ur.ROLLUP_SCOPE, start_dt=_midnight(day) + timedelta(hours=11), end_dt=now)
            with patch.object(ur, "MAX_RESUMED", 0):  # one session of the partial first day is over a cap of none
                assert await reader.rollup_users(f, model_rates_for, 3600) is None
    finally:
        await seeds.cleanup(engine)
        await _forget_days(engine, days)
        await engine.close()
