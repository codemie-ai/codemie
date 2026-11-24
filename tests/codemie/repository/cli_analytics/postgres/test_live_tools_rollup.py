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

"""Live-Postgres check that the Tools & Models rows of the start-day rollup serve what the live /tools statements give,
from the build through the two tables to the handler's response, after every kind of change to the data
(EPMCDME-17301). Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable, or when the database has not run the
revision that adds the tables."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from codemie.configs.config import config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import user_rollup as ur
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.dashboard_cache import reset_dashboard_cache
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres.live_helpers import _COST_SQL, _DIMS_SQL, _MODULE, _engine
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import _HAIKU

VERSION = "test-tools-rates-version"
_TABLES = ("session_dims", "cost_daily", "lines_daily", "turns_daily", "invocations_hourly")
_ROLLUP_TABLES = (*ur._DAY_TABLES, "user_rollup_day_state")
_INVOCATION_SQL = (
    "INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success) VALUES ($1, $2, $3, $4, $5, $6)"
)
_TOOL, _SKILL, _AGENT, _COMMAND = 1, 2, 3, 4


async def _has_tables(engine) -> bool:
    async with engine.acquire() as conn:
        return bool(await conn.fetchval("SELECT to_regclass('user_day_tools_rollup') IS NOT NULL"))


async def _forget_days(engine, days) -> None:
    async with engine.acquire() as conn:
        for table in _ROLLUP_TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE start_day = ANY($1::date[])", days)  # noqa: S608 - fixed names


def _hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


class _Seed:
    """Sessions on two start days in two subscription classes, with more than twenty tools that tie on calls, skills,
    agents, slash commands from OTel prompts and from a summary (one of them also run as a skill), models that tie on
    sessions, a model with no name, a session with no tool use, and a session that started before the window and was
    resumed in it."""

    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:10]
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.t0 = self.now - timedelta(hours=2)
        self.sids: list[str] = []

    def name(self, kind: str, n: int | str) -> str:
        return f"{kind}-{self.suffix}-{n}"

    @property
    def days(self):
        first = (self.t0 - timedelta(days=1)).date()
        return sorted({first, self.t0.date(), self.now.date()})

    async def session(self, conn, i: int, started: datetime, *, summary: bool = False) -> str:
        sid = self.name("s", i)
        self.sids.append(sid)
        email = f"dev{i % 3}-{self.suffix}@x.com"
        await conn.execute(_DIMS_SQL, sid, started, started + timedelta(minutes=5), email)
        await conn.execute(
            "UPDATE session_dims SET subscription_type = $2 WHERE session_id = $1", sid, ("codemie", "corporate")[i % 2]
        )
        if summary:
            await conn.execute(
                "UPDATE session_dims SET summary_ts = $2, commands = $3::jsonb WHERE session_id = $1",
                sid,
                started + timedelta(minutes=6),
                json.dumps([self.name("/cmd-sum", 0), self.name("x-skill", 0)]),
            )
        return sid

    async def tools(self, conn, sid: str, i: int, started: datetime) -> None:
        hour = _hour(started)
        for k in range(3):
            calls = (i + k) % 4 + 1  # many ties on calls
            await conn.execute(
                _INVOCATION_SQL, hour, sid, _TOOL, self.name("tool", f"{(i * 3 + k) % 26:02d}"), calls, 1
            )
        if i % 3 == 0:
            await conn.execute(_INVOCATION_SQL, hour, sid, _SKILL, self.name("x-skill", 0), 2, 0)
        if i % 4 == 0:
            await conn.execute(_INVOCATION_SQL, hour, sid, _SKILL, self.name("skill-b", 0), 1, 0)
        if i % 5 == 0:
            await conn.execute(_INVOCATION_SQL, hour, sid, _AGENT, self.name("agent-a", 0), 1, 0)
        if i % 2 == 0:  # OTel prompts: counted for a session with no summary
            await conn.execute(_INVOCATION_SQL, hour, sid, _COMMAND, self.name("/cmd-otel", 0), 1, 0)
        if i == 0:  # a command the session also ran as a skill: counted once, under skills
            await conn.execute(_INVOCATION_SQL, hour, sid, _COMMAND, self.name("x-skill", 0), 1, 0)
        if i in (1, 3):  # a session with a summary: its OTel prompts are not read
            await conn.execute(_INVOCATION_SQL, hour, sid, _COMMAND, self.name("/cmd-ignored", 0), 4, 0)

    async def models(self, conn, sid: str, i: int, started: datetime) -> None:
        email = f"dev{i % 3}-{self.suffix}@x.com"
        await conn.execute(_COST_SQL, started.date(), sid, email, self.name("m", i % 4), 1000 + i, 10, 0, 0, None, None)
        if i == 5:  # no model name: dropped by both paths
            await conn.execute(_COST_SQL, started.date(), sid, email, "", 5, 5, 0, 0, None, None)

    async def seed(self, engine) -> None:
        async with engine.acquire() as conn:
            for i in range(12):
                started = self.t0 if i % 2 == 0 else self.t0 - timedelta(days=1)
                sid = await self.session(conn, i, started, summary=i in (1, 3))
                await self.models(conn, sid, i, started)
                if i != 11:  # the last has no tool use
                    await self.tools(conn, sid, i, started)
            # two models with the same tokens but not the same number of sessions (b: two sessions, a: one), on sessions
            # of different class pairs, so the live statement puts b first by sessions and the rollup reads them from
            # different rows: both paths must order the tie by name
            for i, name, tokens in ((60, "tie-b", 212121), (62, "tie-b", 212121), (61, "tie-a", 424252)):
                sid = await self.session(conn, i, self.t0)
                email = f"dev{i % 3}-{self.suffix}@x.com"
                await conn.execute(
                    _COST_SQL, self.t0.date(), sid, email, self.name("m", name), tokens, 10, 0, 0, None, None
                )
            # a session that started before the window and was resumed in it: its tools are read live
            first = datetime.combine(self.days[0], datetime.min.time(), tzinfo=timezone.utc)
            sid = await self.session(conn, 99, first - timedelta(days=3))
            # its activity is on a whole day of the window (the first), which is what admits an older session
            await conn.execute(
                "UPDATE session_dims SET resumed = true, last_activity_day = $2, has_activity = true"
                " WHERE session_id = $1",
                sid,
                first.date(),
            )
            await conn.execute(
                _INVOCATION_SQL, first + timedelta(hours=12), sid, _TOOL, self.name("tool", "resumed"), 7, 7
            )
            await conn.execute(
                _COST_SQL,
                first.date(),
                sid,
                f"dev0-{self.suffix}@x.com",
                self.name("m", "resumed"),
                50,
                5,
                0,
                0,
                None,
                None,
            )

    async def more(self, engine) -> None:
        """A change to a built day: one more session with a new tool and model, and a tool call on an old session."""
        async with engine.acquire() as conn:
            sid = await self.session(conn, 50, self.t0)
            await conn.execute(_INVOCATION_SQL, _hour(self.t0), sid, _TOOL, self.name("tool", "late"), 3, 3)
            later = _hour(self.t0) + timedelta(hours=1)  # another hour of the same session: a new row of its own
            await conn.execute(_INVOCATION_SQL, later, self.sids[0], _TOOL, self.name("tool", "00"), 9, 8)
            await conn.execute(
                "UPDATE session_dims SET last_event_at = $2 WHERE session_id = $1", self.sids[0], self.now
            )
            await conn.execute(
                _COST_SQL,
                self.t0.date(),
                sid,
                f"dev2-{self.suffix}@x.com",
                self.name("m", "late"),
                77,
                7,
                0,
                0,
                None,
                None,
            )

    async def cleanup(self, engine) -> None:
        async with engine.acquire() as conn:
            for table in _TABLES:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.sids)  # noqa: S608


def _filter(seed: _Seed, **kwargs) -> LocalAnalyticsFilter:
    day = seed.days[0]
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return LocalAnalyticsFilter(scope=ur.ROLLUP_SCOPE, start_dt=start, end_dt=datetime.now(timezone.utc), **kwargs)


async def _build(engine, reader, days) -> None:
    async with engine.acquire() as conn:
        signatures = await ur.day_signatures(conn, days[0], days[-1])
        for day in days:
            await ur.build_day(reader, conn, day, model_rates_for, VERSION, signatures.get(day, ur._NO_SESSIONS))


async def _tools(handler: LocalAnalyticsHandler, f, *, rollup: bool, class_lists: bool = False) -> dict:
    reset_dashboard_cache()
    with patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", rollup):
        return await handler.get_tools(f, include_class_lists=class_lists)


async def _live_rows(reader, f):
    async with reader.bounded() as repo:
        await repo.materialize_sel(f)
        return await repo.get_tool_usage(f), await repo.get_model_breakdown(f), await repo.get_invocations(f)


async def _assert_the_rollup_gives_the_live_answer(engine, reader, seed: _Seed) -> None:
    f = _filter(seed)
    spy_merged = []
    original = ur._merge_live_sessions

    async def spy(*args, **kwargs):
        spy_merged.append(args[4])  # the live sessions merged into the rollup rows
        return await original(*args, **kwargs)

    with patch.object(ur, "_merge_live_sessions", spy):
        served = await reader.rollup_tools(f, model_rates_for, 3600)
    assert served is not None
    assert any(seed.name("s", 99) in ids for ids in spy_merged), "the resumed session was not merged live"

    # the rows: the tools exactly as the live statement gives them (the same top 20, the same order)
    live_tools, live_models, live_invocations = await _live_rows(reader, f)
    assert served.tool_rows == [
        {"tool_name": r["tool_name"], "call_count": r["call_count"], "success_count": r["success_count"]}
        for r in live_tools
    ]
    assert len(served.tool_rows) == 20  # more than twenty tools were seeded
    # the scenario is real: the live statements count the resumed session, so the rollup had to merge it
    assert seed.name("tool", "resumed") in [r["tool_name"] for r in live_tools]
    as_models = lambda rows: sorted((r["model_name"], r["session_count"], r["total_tokens"]) for r in rows)  # noqa: E731
    assert as_models(served.model_rows) == as_models(live_models)
    as_invocations = lambda rows: sorted((r["kind"], r["name"], r["count"]) for r in rows)  # noqa: E731
    assert as_invocations(served.invocation_rows) == as_invocations(live_invocations)

    # the response: built from the rollup rows or from the live rows, it is the same
    handler = LocalAnalyticsHandler(reader)
    from_rollup = await _tools(handler, f, rollup=True)
    from_live = await _tools(handler, f, rollup=False)
    assert from_rollup == from_live
    tied = [m["model_name"] for m in from_live["tokens_by_model"] if "-tie-" in m["model_name"]]
    assert tied == [seed.name("m", "tie-a"), seed.name("m", "tie-b")]  # equal tokens: by name, on both paths
    # a command a session also ran as a skill is counted once, under skills: sessions 0, 3, 6 and 9 ran it (2 calls
    # each), sessions 0 (OTel prompt) and 3 (summary) also named it as a command and are not counted twice; session 1
    # only named it, so it is one slash command
    skills = {s["name"]: s["count"] for s in from_live["skills_invoked"]}
    commands = {s["name"]: s["count"] for s in from_live["slash_commands"]}
    assert skills[seed.name("x-skill", 0)] == 8
    assert commands[seed.name("x-skill", 0)] == 1
    # the OTel prompts of the two sessions with a summary are not read
    assert seed.name("/cmd-ignored", 0) not in commands
    assert commands[seed.name("/cmd-sum", 0)] == 2


@pytest.mark.asyncio
async def test_the_rollup_serves_the_tools_tab_the_live_statements_give_after_every_kind_of_change():
    engine = await _engine()
    if not await _has_tables(engine):
        await engine.close()
        pytest.skip("the Tools & Models rollup tables are not in this database")
    seed = _Seed()
    days = seed.days
    try:
        await seed.seed(engine)
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            await _build(engine, reader, days)
            await _assert_the_rollup_gives_the_live_answer(engine, reader, seed)

            await seed.more(engine)  # a session added to a built day and a call on an old session
            await _build(engine, reader, days)
            await _assert_the_rollup_gives_the_live_answer(engine, reader, seed)
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, days)
        await engine.close()


@pytest.mark.asyncio
async def test_a_filtered_window_and_a_window_with_class_lists_give_the_live_result():
    engine = await _engine()
    if not await _has_tables(engine):
        await engine.close()
        pytest.skip("the Tools & Models rollup tables are not in this database")
    seed = _Seed()
    days = seed.days
    try:
        await seed.seed(engine)
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            await _build(engine, reader, days)
            handler = LocalAnalyticsHandler(reader)

            filtered = _filter(seed, users=[f"dev0-{seed.suffix}@x.com"])
            assert await reader.rollup_tools(filtered, model_rates_for, 3600) is None  # a filter: not the rollup's
            assert await _tools(handler, filtered, rollup=True) == await _tools(handler, filtered, rollup=False)

            f = _filter(seed)
            with_lists = await _tools(handler, f, rollup=True, class_lists=True)
            assert with_lists == await _tools(handler, f, rollup=False, class_lists=True)
            assert any(row["subscription_types"] for row in with_lists["tool_usage"])  # the lists are filled
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, days)
        await engine.close()


@pytest.mark.asyncio
async def test_a_day_below_shape_eight_gives_the_live_result_until_it_is_rebuilt():
    engine = await _engine()
    if not await _has_tables(engine):
        await engine.close()
        pytest.skip("the Tools & Models rollup tables are not in this database")
    seed = _Seed()
    days = seed.days
    try:
        await seed.seed(engine)
        reader = PostgresCliAnalyticsReader(engine)
        reader.set_rates(lambda: VERSION)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            await _build(engine, reader, days)
            f = _filter(seed)
            assert await reader.rollup_tools(f, model_rates_for, 3600) is not None
            async with engine.acquire() as conn:
                await conn.execute("UPDATE user_rollup_day_state SET shape = 7 WHERE start_day = $1", days[-1])

            assert await reader.rollup_tools(f, model_rates_for, 3600) is None  # one old day: no mixed answer
            handler = LocalAnalyticsHandler(reader)
            assert await _tools(handler, f, rollup=True) == await _tools(handler, f, rollup=False)

            await _build(engine, reader, days)  # the job rebuilds it
            assert await reader.rollup_tools(f, model_rates_for, 3600) is not None
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, days)
        await engine.close()
