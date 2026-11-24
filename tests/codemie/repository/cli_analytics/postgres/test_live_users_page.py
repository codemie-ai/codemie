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

"""Live-Postgres checks of the Users reader methods (EPMCDME-17051): the ranked money page, the per-developer
session facts and `get_users` without the producer money. Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import G_PERSONAL, G_ROUTE, G_TOTAL, G_USER, G_USER_PERSONAL, G_USER_ROUTE
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres.live_helpers import (
    _COST_SQL,
    _DIMS_SQL,
    _MODULE,
    _REAL_WORK_SQL,
    _engine,
)
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import _HAIKU

_MODEL = "claude-haiku-4-5"
_TABLES = (
    "session_dims",
    "cost_daily",
    "session_usage",
    "turns_daily",
    "invocations_hourly",
    "lines_daily",
    "log_events",
)


class _Seed:
    def __init__(self, real_work: bool = False) -> None:
        self.real_work = real_work
        self.suffix = uuid.uuid4().hex[:12]
        self.t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
        self.sids: list[str] = []

    def name(self, prefix: str) -> str:
        return f"{prefix}-{self.suffix}@x.com"

    def filter(self, *emails: str, developers: list[str] | None = None) -> LocalAnalyticsFilter:
        naive = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
        return LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE,
            start_dt=naive(self.t0 - timedelta(days=1)),
            end_dt=naive(self.t0 + timedelta(days=1)),
            users=[e.lower() for e in emails] or None,
            developers=developers,
        )

    async def session(
        self,
        engine,
        key: str,
        email: str | None,
        *,
        input_tokens: int | None = 1000,
        cost_email: str | None = None,
        last_event: datetime | None | str = "default",
        turns: int | None = None,
        calls: int | None = None,
        ok: int | None = None,
    ) -> str:
        sid = f"test-users-{self.suffix}-{key}"
        self.sids.append(sid)
        last = self.t0 + timedelta(minutes=1) if last_event == "default" else last_event
        day = self.t0.date()
        async with engine.acquire() as conn:
            await conn.execute(_DIMS_SQL, sid, self.t0, last, email)
            if self.real_work:
                await conn.execute(_REAL_WORK_SQL, self.t0 + timedelta(seconds=10), sid)
            if input_tokens is not None:
                await conn.execute(_COST_SQL, day, sid, cost_email or email, _MODEL, input_tokens, 0, 0, 0, None, None)
            if turns is not None:
                await conn.execute(
                    "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, $3)", day, sid, turns
                )
            if calls is not None:
                await conn.execute(
                    "INSERT INTO invocations_hourly VALUES ($1, $2, 1, 'Edit', $3, $4)",
                    self.t0.replace(minute=0),
                    sid,
                    calls,
                    ok,
                )
        return sid

    async def cleanup(self, engine) -> None:
        async with engine.acquire() as conn:
            for table in _TABLES:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.sids)  # noqa: S608


def _rates():
    return [model_rates_for(_MODEL)]


def _page(reader, f, limit, offset=0):
    return reader.get_users_money_page(f, _rates(), limit, offset)


@pytest.mark.asyncio
async def test_users_page_ranks_ties_code_point_and_counts_total():
    engine = await _engine()
    seed = _Seed()
    try:
        upper, lower = seed.name("B"), seed.name("a")
        top, costless = seed.name("top"), seed.name("zero")
        await seed.session(engine, "u", upper, input_tokens=1000)
        await seed.session(engine, "l", lower, input_tokens=1000)
        await seed.session(engine, "t", top, input_tokens=5000)
        await seed.session(engine, "z", costless, input_tokens=None, turns=1)
        reader = PostgresCliAnalyticsReader(engine)
        f = seed.filter(upper, lower, top, costless)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            everyone = await _page(reader, f, None)
            second = await _page(reader, f, 2, 1)
            beyond = await _page(reader, f, 2, 9)

        # equal round(cost, 9): 'B' < 'a' in code-point order (a locale would put 'a' first); costless last
        assert everyone.developers == [top, upper, lower, costless]
        assert everyone.total_count == 4
        assert second.developers == [upper, lower]
        assert second.total_count == 4
        assert beyond.developers == []
        assert beyond.total_count == 4
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_users_page_buckets_only_page_developers():
    engine = await _engine()
    seed = _Seed()
    try:
        first, second = seed.name("first"), seed.name("second")
        await seed.session(engine, "1", first, input_tokens=3000)
        await seed.session(engine, "2", second, input_tokens=1000)
        reader = PostgresCliAnalyticsReader(engine)
        f = seed.filter(first, second)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            page = await _page(reader, f, 1)

        assert page.developers == [first]
        groups = page.money.groups
        assert [g.keys["user"] for g in groups[G_USER]] == [first]
        assert {g.keys["user"] for g in groups[G_USER_ROUTE]} == {first}
        assert {g.keys["user"] for g in groups[G_USER_PERSONAL]} == {first}
        # the window figures still cover every developer
        assert groups[G_TOTAL][0].session_count == 2
        assert groups[G_TOTAL][0].cost_usd == pytest.approx(4000 * 1.0e-6)
        assert sum(g.session_count for g in groups[G_ROUTE]) == 2
        assert sum(g.session_count for g in groups[G_PERSONAL]) == 2
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_developer_facts_include_fallback_email_sessions():
    engine = await _engine()
    seed = _Seed()
    try:
        fallback = seed.name("fallback")
        # no email of its own: the developer comes from the cost_daily row
        await seed.session(engine, "f", None, cost_email=fallback, turns=3, calls=7, ok=5)
        reader = PostgresCliAnalyticsReader(engine)
        f = seed.filter(developers=[fallback])
        # the session is found by the developers filter only, not by f.users
        assert await seed_users_filter_rows(reader, seed, fallback) == []

        (row,) = await reader.get_developer_session_facts(f)
        assert row["developer"] == fallback
        assert (row["turns"], row["tool_calls"], row["tool_successes"]) == (3, 7, 5)
    finally:
        await seed.cleanup(engine)
        await engine.close()


async def seed_users_filter_rows(reader, seed: _Seed, email: str):
    return await reader.get_developer_session_facts(seed.filter(email))


@pytest.mark.asyncio
async def test_avg_duration_skips_null_keeps_zero():
    engine = await _engine()
    seed = _Seed()
    try:
        dev = seed.name("dur")
        await seed.session(engine, "zero", dev, last_event=seed.t0)  # a real 0 ms session
        await seed.session(engine, "long", dev, last_event=seed.t0 + timedelta(seconds=60))
        await seed.session(engine, "null", dev, last_event=None, turns=2)  # unknown duration: skipped
        alone = seed.name("alone")
        await seed.session(engine, "unknown", alone, last_event=None)
        reader = PostgresCliAnalyticsReader(engine)
        rows = {r["developer"]: r for r in await reader.get_developer_session_facts(seed.filter(dev, alone))}

        assert rows[dev]["avg_session_duration_ms"] == 30_000  # (0 + 60000) / 2, the NULL one is not a 0
        assert rows[dev]["turns"] == 2  # the unknown-duration session still counts its turns
        assert rows[alone]["avg_session_duration_ms"] is None
        assert (rows[alone]["turns"], rows[alone]["tool_calls"], rows[alone]["tool_successes"]) == (0, 0, 0)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_window_avg_duration_truncates_skips_null_and_spans_developers():
    engine = await _engine()
    seed = _Seed()
    try:
        a, b, none = seed.name("avga"), seed.name("avgb"), seed.name("avgn")
        await seed.session(engine, "a1", a, last_event=seed.t0 + timedelta(milliseconds=1))
        await seed.session(engine, "a2", a, last_event=seed.t0 + timedelta(milliseconds=1))
        await seed.session(engine, "b1", b, last_event=seed.t0 + timedelta(milliseconds=2))
        await seed.session(engine, "n1", a, last_event=None)  # unknown: not a 0 in the mean
        await seed.session(engine, "x1", none, last_event=None)
        reader = PostgresCliAnalyticsReader(engine)

        # (1 + 1 + 2) / 3 = 1.33 -> 1 like int(sum / len); a developer filter is not part of the window figure
        assert await reader.get_avg_session_duration_ms(seed.filter(a, b)) == 1
        assert await reader.get_avg_session_duration_ms(seed.filter(none)) is None
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_users_no_session_ids_column():
    engine = await _engine()
    seed = _Seed()
    try:
        dev, other = seed.name("cols"), seed.name("other")
        await seed.session(engine, "a", dev, input_tokens=1000)
        await seed.session(engine, "b", other, input_tokens=2000)
        reader = PostgresCliAnalyticsReader(engine)
        rows = await reader.get_users(seed.filter(dev, other, developers=[dev]))

        (row,) = rows  # honours f.developers
        assert row["developer_name"] == dev
        assert row["session_count"] == 1
        assert row["input_tokens"] == 1000
        assert row["top_model"] == _MODEL
        assert "session_ids" not in row
        assert "cost_usd" not in row
    finally:
        await seed.cleanup(engine)
        await engine.close()
