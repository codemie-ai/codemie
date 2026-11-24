# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Live-Postgres check that the Overview rows of the start-day rollup serve what the live /overview statements give,
from the build through the three tables to the read, with the ties of the model order (EPMCDME-17051). Skipped when
`TEST_LOCAL_PG_URL` is not set or unreachable, or when the database has not run the revision that adds the tables."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import user_rollup as ur
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres.live_helpers import (
    _COST_SQL,
    _DIMS_SQL,
    _MODULE,
    _REAL_WORK_SQL,
    _engine,
)
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import _HAIKU

VERSION = "test-overview-rates-version"
PERSONAL_MODEL = "claude-haiku-4-5"  # priced by the patched catalog (`_HAIKU`), so its cache re-reads cost money
_TABLES = ("session_dims", "cost_daily", "lines_daily", "turns_daily", "invocations_hourly", "log_events")
_ROLLUP_TABLES = (*ur._DAY_TABLES, "user_rollup_day_state")


async def _has_tables(engine) -> bool:
    async with engine.acquire() as conn:
        return bool(await conn.fetchval("SELECT to_regclass('user_day_overview_rollup') IS NOT NULL"))


async def _forget_days(engine, days) -> None:
    async with engine.acquire() as conn:
        for table in _ROLLUP_TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE start_day = ANY($1::date[])", days)  # noqa: S608 - fixed names


class _Models:
    """Sessions of a few developers over models that tie on sessions and spend, so the order falls to the name, in
    the API and Subscription classes, and one Personal-paid session on a model of its own."""

    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:10]
        self.t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
        self.sids: list[str] = []

    def model(self, name: str) -> str:
        return f"{name}-{self.suffix}"

    async def seed(self, engine) -> None:
        plan = [  # (model, spend per session, sessions)
            ("e-most-sessions", 1, 3),
            ("d-more-spend", 7, 2),
            ("C-ties-upper", 5, 2),  # three models tie on two sessions and a spend of 10: code-point order of the name
            ("a-ties", 5, 2),
            ("b-ties", 5, 2),
            ("f-one-session", 9, 1),
        ]
        day = self.t0.date()
        n = 0
        async with engine.acquire() as conn:
            for name, spend, sessions in plan:
                for _ in range(sessions):
                    n += 1
                    sid = f"test-overview-{self.suffix}-{n}"
                    email = f"dev{n % 3}-{self.suffix}@x.com"
                    self.sids.append(sid)
                    await conn.execute(_DIMS_SQL, sid, self.t0, self.t0 + timedelta(minutes=1), email)
                    await conn.execute(_REAL_WORK_SQL, self.t0 + timedelta(seconds=10), sid)
                    await conn.execute(  # API and Subscription sessions: the `class` rows
                        "UPDATE session_dims SET subscription_type = $2 WHERE session_id = $1",
                        sid,
                        ("codemie", "corporate")[n % 2],
                    )
                    await conn.execute(_COST_SQL, day, sid, email, self.model(name), 1000, 10, 0, 0, None, None)
                    await conn.execute("UPDATE cost_daily SET cost_usd = $2 WHERE session_id = $1", sid, spend)
                    await conn.execute("INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, 2)", day, sid)
                    await conn.execute(
                        "INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, lines_removed)"
                        " VALUES ($1, $2, $3, $4, $5, $6)",
                        day - timedelta(days=n % 2),  # lines written on the day before the session started, too
                        sid,
                        email,
                        self.model(name),
                        10 * n,
                        n,
                    )

            # a Personal-paid work-project session (a shared origin, so not private and in the rollup's scope): in
            # the spend and its `personal` class row since EPMCDME-17260. On a priced model of its own (not ending in
            # the suffix, so the ties above stay) with cache re-reads, so the cache re-read cost is not zero
            sid, email = f"test-overview-{self.suffix}-personal", f"dev0-{self.suffix}@x.com"
            self.sids.append(sid)
            await conn.execute(_DIMS_SQL, sid, self.t0, self.t0 + timedelta(minutes=1), email)
            await conn.execute(_REAL_WORK_SQL, self.t0 + timedelta(seconds=10), sid)
            await conn.execute(
                "UPDATE session_dims SET subscription_type = 'personal', project_origin = 'profile'"
                " WHERE session_id = $1",
                sid,
            )
            await conn.execute(_COST_SQL, day, sid, email, PERSONAL_MODEL, 1000, 10, 1_000_000, 0, None, None)
            await conn.execute("UPDATE cost_daily SET cost_usd = 4 WHERE session_id = $1", sid)
            await conn.execute("INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, 2)", day, sid)

    async def cleanup(self, engine) -> None:
        async with engine.acquire() as conn:
            for table in _TABLES:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.sids)  # noqa: S608


def _by_part(rows, part):
    return [r for r in rows if r["part"] == part]


@pytest.mark.asyncio
async def test_the_rollup_serves_the_overview_the_live_statements_give_with_the_model_ties():
    engine = await _engine()
    if not await _has_tables(engine):
        await engine.close()
        pytest.skip("the overview rollup tables are not in this database")
    seed = _Models()
    now = datetime.now(timezone.utc)
    days = sorted({seed.t0.date(), now.date()})
    try:
        await seed.seed(engine)
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        start = datetime(days[0].year, days[0].month, days[0].day, tzinfo=timezone.utc)
        f = LocalAnalyticsFilter(scope=ur.ROLLUP_SCOPE, start_dt=start, end_dt=now)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            async with engine.acquire() as conn:
                signatures = await ur.day_signatures(conn, days[0], days[-1])
                for day in days:
                    await ur.build_day(
                        reader, conn, day, model_rates_for, VERSION, signatures.get(day, ur._NO_SESSIONS)
                    )
            served = await reader.rollup_overview(f, model_rates_for, 3600)
            assert served is not None

            live_filter = LocalAnalyticsFilter(scope=ur.ROLLUP_SCOPE, start_dt=start, end_dt=datetime.now(timezone.utc))
            async with reader.bounded() as repo:
                await repo.materialize_sel(live_filter)
                await repo.materialize_files(live_filter)
                rates = [model_rates_for(m) for m in await repo.get_cost_models(live_filter)]
                live_money = await repo.get_overview_money(live_filter, rates)
                live_facts = await repo.get_overview_facts(live_filter)
                live_models = await repo.get_overview_models(live_filter)
                live_kpis = await repo.get_dashboard_kpis(live_filter)
                live_lines = await repo.get_overview_lines(live_filter)

        # the cards: money, facts, tokens
        money = lambda rows, key: [r for r in rows if r["part"] == "money" and r["key"] == key]  # noqa: E731
        live_spend, served_spend = money(live_money, "spend"), money(served.money_rows, "spend")
        assert len(served_spend) == len(live_spend)
        # Est. Cost and the cache re-read cost (Bloat %): the spend row, over every session of the window
        for served_row, live_row in zip(served_spend, live_spend):
            assert served_row["cost_usd"] == pytest.approx(float(live_row["cost_usd"]))
            assert served_row["cache_read_cost_usd"] == pytest.approx(float(live_row["cache_read_cost_usd"]))
            assert served_row["cache_read_cost_usd"] > 0  # the seeded cache re-reads: a dropped value would read 0
            assert sorted(served_row["unpriced_models"]) == sorted(live_row["unpriced_models"] or [])
        # API Spend and Subscription Usage: the `class` rows, the cost by subscription class
        by_class = lambda rows: {r["subscription_type"]: float(r["cost_usd"]) for r in money(rows, "class")}  # noqa: E731
        live_classes, served_classes = by_class(live_money), by_class(served.money_rows)
        assert served_classes.keys() == live_classes.keys()
        assert {"codemie", "corporate", "personal"} <= live_classes.keys()  # the seeded Personal-paid session is in
        for name, cost in live_classes.items():
            assert served_classes[name] == pytest.approx(cost), name
        assert _by_part(served.money_rows, "dead")[0]["n"] == _by_part(live_money, "dead")[0]["n"]
        for key in ("turns", "tool_calls", "tool_calls_success", "duration_ms", "files_changed"):
            assert served.fact_rows[0][key] == int(live_facts[0][key] or 0), key
        for key, value in served.kpi_rows[0].items():  # the columns /overview reads; the statement has more
            assert value == int(live_kpis[0][key] or 0), key

        # the models: the same order, with the ties of the seeded models resolved by the name in code-point order
        assert [(r["key"], r["n"]) for r in served.model_rows] == [(r["key"], r["n"]) for r in live_models]
        mine = [r["key"] for r in served.model_rows if r["key"].endswith(seed.suffix)]
        assert mine == [
            seed.model(name)
            for name in ("e-most-sessions", "d-more-spend", "C-ties-upper", "a-ties", "b-ties", "f-one-session")
        ]

        # the lines: the total and, by the day the lines were written, the net and whether a row reports lines
        live_total, served_total = _by_part(live_lines, "total")[0], _by_part(served.line_rows, "total")[0]
        assert served_total["lines_added"] == live_total["lines_added"]
        assert served_total["lines_removed"] == live_total["lines_removed"]
        day_rows = lambda rows: {r["key"]: (r["net_lines"], bool(r["nonzero"])) for r in _by_part(rows, "day")}  # noqa: E731
        assert day_rows(served.line_rows) == day_rows(live_lines)
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, days)
        await engine.close()
