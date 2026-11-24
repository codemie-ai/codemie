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

"""Live-Postgres check of the pricing the start-day rollup gives its amounts (EPMCDME-17152, option A).

The stored rows (shape 4, no priced evidence) are served as they are. On the rollup an amount that is incomplete and
exactly $0 is unknown (`cost_unknown` true), a positive incomplete subtotal is partial and keeps its value and model
names, a complete amount is unmarked. The live statements keep their own rule: a used kind with a zero rate is priced
evidence, so the same $0 is partial there (the documented exception, `zero`). The requests go through the real
`LocalAnalyticsHandler` on a real Postgres, once with the rollup (the test asserts that the rollup answered) and once
with `CLI_ANALYTICS_USER_ROLLUP_ENABLED` off. Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable, or when the
database has not run the revisions that add the tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio

from codemie.configs import config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import user_rollup as ur
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres.live_helpers import (
    _COST_SQL,
    _DIMS_SQL,
    _MODULE,
    _REAL_WORK_SQL,
    _engine,
)
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import _HAIKU, _priced_model

VERSION = "test-evidence-rates-version"
_TABLES = ("session_dims", "cost_daily", "log_events")
# A used kind (cache re-read) with a zero rate next to a used kind (cache write) with none: $0 and incomplete. Live
# counts the zero rate as priced evidence (partial); the rollup stores no evidence (unknown).
_ZERO_GAP = _priced_model("zero-gap-model", 1.0e-6, 5.0e-6, 0.0, None, None)
_CATALOG = [_HAIKU, _ZERO_GAP]
_SCENARIOS = ("full", "unpriced", "partial", "zero")


class _Scenarios:
    """One developer per scenario, each in a class pair of its own, so a filter on the pair isolates the scenario
    (the rollup does not serve a developer filter) and the sessions of the other scenarios cannot reach it."""

    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:10]
        self.router = f"claude-router-premium-{self.suffix}"  # in no catalog
        self.sids: list[str] = []
        self.t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
        self.pairs = {  # scenario -> (subscription class, project-origin class)
            "full": ("codemie", "profile"),
            "unpriced": ("corporate", "profile"),
            "partial": ("codemie", "registered_repository"),
            "zero": ("corporate", "registered_repository"),
        }

    def email(self, scenario: str) -> str:
        return f"{scenario}-{self.suffix}@x.com"

    async def add(self, engine, scenario: str, rows: list[tuple], days_ago: int = 0) -> None:
        """One session `days_ago` days before `t0`; `rows` are (model, input, output, cache_read, cache_creation) per
        `cost_daily` row."""
        sid = f"test-evidence-{self.suffix}-{scenario}-{days_ago}"
        self.sids.append(sid)
        sub, origin = self.pairs[scenario]
        started = self.t0 - timedelta(days=days_ago)
        async with engine.acquire() as conn:
            await conn.execute(_DIMS_SQL, sid, started, started + timedelta(minutes=1), self.email(scenario))
            await conn.execute(_REAL_WORK_SQL, started + timedelta(seconds=10), sid)
            await conn.execute(
                "UPDATE session_dims SET subscription_type = $2, project_origin = $3, has_activity = true, "
                "resumed = false, last_activity_day = $4 WHERE session_id = $1",
                sid,
                sub,
                origin,
                started.date(),
            )
            for model, inp, out, read, write in rows:
                await conn.execute(
                    _COST_SQL, started.date(), sid, self.email(scenario), model, inp, out, read, write, None, None
                )

    async def seed(self, engine) -> None:
        await self.add(engine, "full", [("claude-haiku-4-5", 3000, 100, 0, 0)])
        await self.add(engine, "unpriced", [(self.router, 3000, 100, 0, 0)])
        await self.add(engine, "partial", [("claude-haiku-4-5", 3000, 100, 0, 0), (self.router, 1000, 50, 0, 0)])
        await self.add(engine, "zero", [("zero-gap-model", 0, 0, 1000, 500)])

    async def cleanup(self, engine) -> None:
        async with engine.acquire() as conn:
            for table in _TABLES:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.sids)  # noqa: S608


def _midnight(day) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _flags(row: dict) -> tuple:
    return (row["cost_incomplete"], row["cost_unknown"], tuple(row["unpriced_models"]))


async def _surfaces(handler, f: LocalAnalyticsFilter) -> dict:
    """What the Users, Cost and Overview tabs show for `f`: the flags and the money of every amount."""
    users, _ = await handler.get_users(
        f, 0, 500, include_chart=True, include_daily_activity=False, include_avg_duration=False
    )
    cost, _ = await handler.get_cost(f, include_class_lists=False, include_buckets=False)
    overview, _, _ = await handler.get_overview(f, include_class_lists=False, include_buckets=False)
    return {
        "users": {r["developer_name"]: (_flags(r), r["cost_usd"]) for r in users["rows"]},
        "chart": {r["developer_name"]: (_flags(r), r["cost_usd"]) for r in users["chart"]},
        "funding": {r["developer_name"]: r["funding"] for r in users["rows"]},
        "window_funding": (users["funding"], users["personal_billed"]),
        "cost_kpi": (_flags(cost["kpis"]), cost["kpis"]["total_cost_usd"]),
        "cost_by_user": {r["developer_name"]: (_flags(r), r["cost_usd"]) for r in cost["cost_by_user"]},
        "cost_by_model": {r["model_name"]: (_flags(r), r["cost_usd"]) for r in cost["cost_by_model"]},
        "overview_kpi": (_flags(overview["kpis"]), overview["kpis"]["total_cost_usd"]),
    }


class _Served:
    """Wraps the reader's three rollup reads and records whether each answered (None: the live statements ran)."""

    def __init__(self, reader: PostgresCliAnalyticsReader) -> None:
        self.answers: list[bool] = []
        for name in ("rollup_users", "rollup_cost", "rollup_overview"):
            setattr(reader, name, self._spy(getattr(reader, name)))

    def _spy(self, read):
        async def spied(*args, **kwargs):
            served = await read(*args, **kwargs)
            self.answers.append(served is not None)
            return served

        return spied


async def _build(engine, reader, days) -> None:
    async with engine.acquire() as conn:
        signatures = await ur.day_signatures(conn, days[0], days[-1])
        for day in days:
            await ur.build_day(reader, conn, day, model_rates_for, VERSION, signatures.get(day, ur._NO_SESSIONS))


@pytest_asyncio.fixture
async def live_world():
    engine = await _engine()
    async with engine.acquire() as conn:
        if not await conn.fetchval("SELECT to_regclass('user_rollup_day_state') IS NOT NULL"):
            await engine.close()
            pytest.skip("the user rollup tables are not in this database")
    s = _Scenarios()
    days = sorted({s.t0.date() - timedelta(days=1), s.t0.date(), datetime.now(timezone.utc).date()})
    try:
        async with live_rollup_lock(engine):  # the tests share one database and its day rows
            try:
                await s.seed(engine)
                reader = PostgresCliAnalyticsReader(engine)
                reader.set_rates(lambda: VERSION)
                with (
                    patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=_CATALOG),
                    patch.object(config, "CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S", 0),
                ):
                    await _build(engine, reader, days)
                    yield engine, reader, s, days
            finally:
                await s.cleanup(engine)
                async with engine.acquire() as conn:
                    await conn.execute("DELETE FROM user_rollup_day_state WHERE start_day = ANY($1::date[])", days)
                    for table in ur._DAY_TABLES:
                        await conn.execute(f"DELETE FROM {table} WHERE start_day = ANY($1::date[])", days)  # noqa: S608
    finally:
        await engine.close()


def _filter(days, pair=None) -> LocalAnalyticsFilter:
    sub, origin = pair or (None, None)
    return LocalAnalyticsFilter(
        scope=ur.ROLLUP_SCOPE,
        start_dt=_midnight(days[0]),
        end_dt=datetime.now(timezone.utc),
        subscription_types=[sub] if sub else None,
        project_origins=[origin] if origin else None,
    )


async def _rollup_and_live(reader, f) -> tuple[dict, dict]:
    served = _Served(reader)
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        from_rollup = await _surfaces(LocalAnalyticsHandler(reader), f)
    assert served.answers == [True, True, True], "the rollup must have answered Users, Cost and Overview"
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", False):
        live = await _surfaces(LocalAnalyticsHandler(reader), f)
    return from_rollup, live


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "rollup", "live"),  # (cost_incomplete, cost_unknown) on the rollup and on the live statements
    [
        ("full", (False, False), (False, False)),  # complete: unmarked
        ("unpriced", (True, True), (True, True)),  # entirely unpriced: unknown, never a $0
        ("partial", (True, False), (True, False)),  # positive subtotal: partial, keeps value and names
        ("zero", (True, True), (True, False)),  # EXCEPTION: zero-rate evidence is live-only; the rollup reads unknown
    ],
)
async def test_stored_rows_serve_every_amount_with_the_option_a_pricing(live_world, scenario, rollup, live):
    _, reader, s, days = live_world
    email = s.email(scenario).lower()
    from_rollup, from_live = await _rollup_and_live(reader, _filter(days, s.pairs[scenario]))

    for name, got in (("rollup", from_rollup), ("live", from_live)):
        want = rollup if name == "rollup" else live
        assert got["users"][email][0][:2] == want, name
        assert got["chart"][email][0][:2] == want, name
        assert got["cost_by_user"][email][0][:2] == want, name
        # the window holds this scenario alone, so the KPIs are the developer's amount
        assert got["cost_kpi"][0][:2] == want and got["overview_kpi"][0][:2] == want, name
    if scenario != "zero":
        assert from_rollup == from_live  # every flag, name and amount of every tab
    else:  # the same money and names; only `cost_unknown` differs
        assert from_rollup["users"][email][1] == from_live["users"][email][1] == 0.0
        assert from_rollup["users"][email][0][2] == from_live["users"][email][0][2] == ("zero-gap-model",)
    user_flags, user_cost = from_rollup["users"][email]
    assert from_rollup["cost_kpi"][1] == pytest.approx(user_cost)
    if scenario == "unpriced":
        assert user_cost == 0.0 and user_flags[2] == (s.router,)
        assert from_rollup["cost_by_model"][s.router][0][:2] == (True, True)  # Cost by Model: unknown
    if scenario == "partial":
        assert user_cost > 0 and user_flags[2] == (s.router,)  # the priced part stays
        assert from_rollup["cost_by_model"]["claude-haiku-4-5"][0][:2] == (False, False)
        assert from_rollup["cost_by_model"][s.router][0][:2] == (True, True)  # its own $0 amount: unknown


@pytest.mark.asyncio
async def test_the_whole_window_is_partial_and_only_the_zero_rate_row_differs_from_live(live_world):
    _, reader, s, days = live_world
    from_rollup, live = await _rollup_and_live(reader, _filter(days))
    for kpi in (from_rollup["cost_kpi"], from_rollup["overview_kpi"]):
        assert kpi[0][0] is True and kpi[0][1] is False  # a positive subtotal: an estimate, not unknown
    zero = s.email("zero").lower()
    for scenario in _SCENARIOS:
        assert s.email(scenario).lower() in from_rollup["users"]
    assert {k: v for k, v in from_rollup["users"].items() if k != zero} == {
        k: v for k, v in live["users"].items() if k != zero
    }
    assert from_rollup["cost_kpi"] == live["cost_kpi"] and from_rollup["overview_kpi"] == live["overview_kpi"]


@pytest.mark.asyncio
async def test_existing_shape_4_days_are_served_without_a_rebuild_and_a_developer_filter_stays_live(live_world):
    engine, reader, s, days = live_world
    async with engine.acquire() as conn:
        # The current builder also writes newer aggregates. Exercise compatibility with the legacy
        # shape-4 state that Users, Cost and Overview still support.
        result = await conn.execute(
            "UPDATE user_rollup_day_state SET shape = 4 WHERE start_day = ANY($1::date[])", days
        )
    assert result == f"UPDATE {len(days)}"

    f = _filter(days, s.pairs["unpriced"])
    served = _Served(reader)
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        await _surfaces(LocalAnalyticsHandler(reader), f)
    assert served.answers == [True, True, True]  # served from the stored rows as they are

    only = _filter(days, s.pairs["zero"])
    only.users = [s.email("zero")]  # a filtered request is not the rollup's: the live statements answer, unchanged
    served = _Served(reader)
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
        filtered = await _surfaces(LocalAnalyticsHandler(reader), only)
    assert served.answers == [False, False, False]
    assert filtered["users"][s.email("zero").lower()][0][:2] == (True, False)  # live: zero-rate evidence, partial
